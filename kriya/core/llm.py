import ipaddress
import json
import logging
import re
import socket
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from kriya.config import AppConfig

logger = logging.getLogger(__name__)

# Exception types that are clearly NOT about response_format/reasoning
# compatibility - a connection refused, an expired API key, or a rate limit
# has nothing to do with whether this backend supports JSON mode together
# with reasoning, so retrying identically for these just adds latency while
# reporting a misleading root cause (2026-08-12 SME review). Deliberately an
# exclusion list, not an allowlist restricted to e.g. just BadRequestError -
# the retry exists for real, previously-observed backend quirks whose exact
# error shape isn't guaranteed across every OpenAI-compatible server, so an
# unrecognized exception still gets the benefit of the doubt and retries.
_LLM_RETRY_EXCLUDED_EXCEPTIONS = (
    APIConnectionError, APITimeoutError, AuthenticationError,
    PermissionDeniedError, RateLimitError, InternalServerError,
)

class EgressViolationError(ValueError):
    """Raised when an LLM completion request violates local_only egress policy."""
    pass

def is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            # Fail CLOSED, not open - found live, 2026-08-12 (SME architecture
            # review): a malformed/typo'd base_url (e.g. missing "http://" in
            # a config file) parses with hostname=None via urlparse, and this
            # branch was previously treating that as local/allowed - the
            # opposite of the fail-closed behavior the except block below
            # already implements for every OTHER failure mode. This function
            # is a hard safety boundary (see kriya/core/llm.py's egress
            # enforcement, and CLAUDE.md's "Egress control" section) - an
            # unparseable hostname is exactly the kind of ambiguous input
            # that should never be treated as "safe."
            logger.debug(f"is_local_url check found no hostname for '{url}', treating as non-local (fail closed)")
            return False

        if hostname.lower() in {"localhost", "127.0.0.1", "[::1]"}:
            return True
        if hostname.lower().endswith(".local"):
            return True
            
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
                continue
            else:
                return False
        return True
    except Exception as e:
        logger.debug(f"is_local_url check failed for '{url}', treating as non-local (fail closed): {e}")
        return False

class LLMClient:
    """Wrapper around OpenAI-compatible API client for local LLM generation."""
    
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url
        )
        self.model = config.llm.model
        self.temperature = config.llm.temperature
        self.max_tokens = config.llm.max_tokens

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        json_mode: bool = False,
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        reasoning_override: Optional[bool] = None,
    ) -> str:
        """Call the local LLM server and return the text completion (supporting streaming and JSON mode).

        temperature_override/max_tokens_override/reasoning_override let a caller fully
        specify an alternate model's real config (not just model/base_url/api_key) -
        without them, is_reasoning falls back to scanning the top-level llm_chain for a
        matching model name (kept for backward compatibility with the existing
        Developer escalation call sites, which only ever pass the first three)."""
        # Validate egress policy
        if self.config.autonomy.egress_policy == "local_only":
            url_to_check = base_url_override or self.config.llm.base_url
            if not is_local_url(url_to_check):
                raise EgressViolationError(
                    f"Egress violation: Request to external API '{url_to_check}' blocked under 'local_only' policy."
                )

        model = model_override or self.model
        client = self.client

        if base_url_override or api_key_override:
            client = AsyncOpenAI(
                api_key=api_key_override or self.config.llm.api_key,
                base_url=base_url_override or self.config.llm.base_url
            )

        if reasoning_override is not None:
            is_reasoning = reasoning_override
        else:
            is_reasoning = self.config.llm.reasoning
            if model_override:
                for fb in self.config.llm_chain:
                    if fb.model == model_override:
                        is_reasoning = fb.reasoning
                        break

        temperature = temperature_override if temperature_override is not None else self.temperature
        base_max_tokens = max_tokens_override if max_tokens_override is not None else self.max_tokens
        max_tokens = max(base_max_tokens, 12288) if is_reasoning else base_max_tokens
        extra_body = self.config.llm.extra_body if self.config.llm.extra_body else None
        # Reasoning models are NOT excluded from response_format here - Ollama (at
        # least) keeps a reasoning model's <think>-equivalent output in a separate
        # "reasoning" field and json_object-constrains only the "content" field, so
        # forcing valid JSON and letting the model reason are not mutually exclusive.
        # Without this, a reasoning model has nothing forcing it to ever commit to
        # JSON at all - it can (and, observed live, sometimes does) just respond with
        # plain prose explaining its reasoning instead, which no amount of downstream
        # JSON-extraction fallback can recover since there's no JSON substring in it.
        response_format = {"type": "json_object"} if json_mode else None

        logger.info(f"Sending completion request to local LLM [Model: {model}, Stream: {stream_callback is not None}, JSON Mode: {json_mode}, Reasoning: {is_reasoning}]")
        import time

        import click

        start_time = time.time()

        try:
            try:
                content, prompt_tokens, completion_tokens = await self._request_once(
                    client, model, system_prompt, user_prompt, temperature, max_tokens,
                    extra_body, response_format, stream_callback
                )
            except Exception as e:
                # Only reasoning models risk this combination being unsupported by some
                # backend - a plain json_mode call already worked fine unconditionally
                # before this change, so there's no need to retry that case. Also
                # excludes exception types that are clearly unrelated to response_format
                # (connection/timeout/auth/rate-limit/server errors) - see
                # _LLM_RETRY_EXCLUDED_EXCEPTIONS.
                if response_format is not None and is_reasoning and not isinstance(e, _LLM_RETRY_EXCLUDED_EXCEPTIONS):
                    logger.warning(
                        f"Completion request with response_format={response_format} failed for "
                        f"reasoning model '{model}' ({e}) - retrying once without it (this backend/"
                        "model combination may not support JSON mode together with reasoning)."
                    )
                    content, prompt_tokens, completion_tokens = await self._request_once(
                        client, model, system_prompt, user_prompt, temperature, max_tokens,
                        extra_body, None, stream_callback
                    )
                else:
                    raise

            if is_reasoning:
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            elapsed_time = time.time() - start_time
            if prompt_tokens == 0:
                prompt_tokens = int((len(system_prompt) + len(user_prompt)) / 4)
            if completion_tokens == 0:
                completion_tokens = int(len(content) / 4)

            click.secho(f"\n[Usage: {prompt_tokens} input tokens, {completion_tokens} output tokens | Time: {elapsed_time:.2f}s]", fg="blue", dim=True)
            return content
        except Exception as e:
            logger.error(f"Local LLM call failed: {e}", exc_info=True)
            raise e

    async def _request_once(
        self, client, model, system_prompt, user_prompt, temperature, max_tokens,
        extra_body, response_format, stream_callback
    ):
        """Issues a single completion request (streaming or not) and returns
        (content, prompt_tokens, completion_tokens). Split out from complete() so a
        reasoning model's response_format can be retried once without it on failure."""
        prompt_tokens = 0
        completion_tokens = 0
        if stream_callback:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body=extra_body,
                    response_format=response_format
                )
            except Exception as e:
                logger.debug(f"Streaming request with stream_options failed, retrying without it (server may not support it): {e}")
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    extra_body=extra_body,
                    response_format=response_format
                )

            chunks = []
            async for chunk in response:
                if hasattr(chunk, "usage") and chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                if chunk.choices and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    chunks.append(delta)
                    stream_callback(delta)
            content = "".join(chunks).strip()
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                response_format=response_format
            )
            if hasattr(response, "usage") and response.usage:
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
            content = response.choices[0].message.content or ""
            content = content.strip()
        return content, prompt_tokens, completion_tokens

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Single-turn native tool-calling completion. Unlike complete()'s fixed
        system/user string pair, a tool-calling loop needs to append tool_calls and
        tool results as its own messages between turns - so this takes a full
        OpenAI-style message list and returns raw enough structure (content +
        decoded tool_calls) for the caller to drive that loop itself. One call is
        one model turn; this method does NOT loop turns - see
        kriya/workflow/self_correction.py for the only current caller's turn-budget
        loop.

        Reuses complete()'s own egress check and base_url_override/api_key_override
        client-construction logic unchanged - this is a second call site into the
        same safety boundary, not a parallel one. Deliberately does not support
        streaming, json_mode, or reasoning-tag stripping, all out of scope for a
        tool-calling turn (see this session's tool-use scoping decision - a tool
        loop's own turn content is a short plan/summary, not the kind of long-form
        output those features exist for)."""
        if self.config.autonomy.egress_policy == "local_only":
            url_to_check = base_url_override or self.config.llm.base_url
            if not is_local_url(url_to_check):
                raise EgressViolationError(
                    f"Egress violation: Request to external API '{url_to_check}' blocked under 'local_only' policy."
                )

        model = model_override or self.model
        from kriya.core.model_capabilities import (
            ModelCapabilityError, capabilities_for_model, validate_tool_call_sample,
        )
        capabilities = capabilities_for_model(self.config, model)
        if not capabilities.native_tool_calls:
            raise ModelCapabilityError(
                f"Model '{model}' is configured without reliable native tool calling. "
                "Use the ordinary operation-specific generation path instead."
            )
        client = self.client
        if base_url_override or api_key_override:
            client = AsyncOpenAI(
                api_key=api_key_override or self.config.llm.api_key,
                base_url=base_url_override or self.config.llm.base_url
            )

        temperature = temperature_override if temperature_override is not None else self.temperature
        max_tokens = max_tokens_override if max_tokens_override is not None else self.max_tokens
        extra_body = self.config.llm.extra_body if self.config.llm.extra_body else None

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        message = response.choices[0].message
        raw_tool_calls = message.tool_calls or []
        tool_calls = []
        for tc in raw_tool_calls:
            raw_arguments = tc.function.arguments
            try:
                arguments = json.loads(raw_arguments)
            except (json.JSONDecodeError, TypeError):
                # Preserve the established caller contract for malformed local-
                # model JSON: an empty argument object becomes an ordinary,
                # bounded missing-field tool error in the repair loop.
                logger.warning(
                    "Local model returned malformed tool arguments for '%s'; "
                    "using an empty argument object.",
                    tc.function.name,
                )
                tool_calls.append({
                    "id": tc.id, "name": tc.function.name, "arguments": {},
                })
                continue

            sample = validate_tool_call_sample(raw_arguments, capabilities)
            if not sample.compatible:
                logger.warning(
                    "Rejected incompatible local-model tool arguments for '%s': %s",
                    tc.function.name, "; ".join(sample.violations),
                )
                tool_calls.append({
                    "id": tc.id, "name": tc.function.name, "arguments": {},
                    "argument_error": "; ".join(sample.violations),
                })
                continue
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": arguments})
        return {"content": (message.content or "").strip(), "tool_calls": tool_calls}
