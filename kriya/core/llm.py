import ipaddress
import json
import logging
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
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

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
# A reasoning model's max_tokens covers hidden reasoning and visible output
# together, so it never runs below this.
REASONING_MIN_MAX_TOKENS = 12288

_LLM_RETRY_EXCLUDED_EXCEPTIONS = (
    APIConnectionError, APITimeoutError, AuthenticationError,
    PermissionDeniedError, RateLimitError, InternalServerError,
)

class EgressViolationError(ValueError):
    """Raised when an LLM completion request violates local_only egress policy.

    MA4.3 (control-plane implementation plan, kriya/policy/__init__.py): this
    is Kriya's hard local-only egress boundary and stays authoritative
    independently of kriya/policy/execution.py's ExecutionPolicy - see
    LLMClient._audit_llm_network_access below. Nothing in kriya/policy/ may
    ever replace, gate, or suppress this check."""
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
        # MA4.3 - audit-only. See _audit_llm_network_access below; this is
        # never consulted for enforcement, only logged.
        self.execution_policy = ExecutionPolicy()
        # R1 Deliverable 5 (performance telemetry, 2026-09-08) - observational
        # side-channel only: the metadata for the MOST RECENT complete() call,
        # for a caller that wants it (kriya/workflow/attempt.py's Developer-
        # timing wrapper, kriya/workflow/workflow.py's Planner/Architect/
        # Reviewer timing) to read AFTER the call returns. Never read by
        # complete() itself, never influences temperature/max_tokens/retry/
        # model selection or any return value - complete()'s own return
        # contract (a bare str) is completely unchanged. None until the first
        # successful call; overwritten (not appended) on every call, since a
        # caller must read it immediately after its own await completes,
        # before any other concurrent call on the same client could overwrite
        # it - single-flight per client instance is the existing calling
        # convention everywhere in this codebase already (no concurrent
        # complete() calls share one LLMClient), not a new constraint.
        self.last_call_metrics: Optional[Dict[str, Any]] = None
        # PRD-015: the normalized result (kriya.core.completion.CompletionResult)
        # of the most recent call, set before the call returns or raises;
        # None while a call is in flight. Same single-flight convention.
        self.last_completion = None
        # PRD-016: one entry per call whose context window or output budget
        # was automatically enlarged (adaptive budget policy), appended when
        # the call finishes. A workflow drains these into RunEvents so they
        # persist in the run trace (kriya/workflow/state.py
        # drain_budget_expansions); each is also logged.
        self.budget_expansions: List[Dict[str, Any]] = []

    def _audit_llm_network_access(self, url: str) -> None:
        """MA4.3 - audit-only ExecutionPolicy consultation, wired in front of
        (never in place of) this file's own is_local_url/EgressViolationError
        enforcement below. Per kriya/policy/__init__.py's own principle,
        policy DECIDES, existing mechanisms ENFORCE - and for the LLM egress
        boundary specifically, the existing mechanism remains the sole
        enforcer no matter what policy says. This call can never affect
        whether the real request proceeds: its result is only logged (audit
        mode - see the MA4 design doc's rollout plan), and any exception it
        raises is caught here and logged, never propagated - a bug in the
        still-young policy engine (which default-denies LLM_NETWORK_ACCESS
        today, since MA4.6's real network rules haven't landed yet) must
        never block, alter, or take credit for this file's own unconditional
        egress enforcement."""
        try:
            result = self.execution_policy.evaluate(
                ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target=url)
            )
            logger.debug(
                "MA4 policy audit (not enforced): LLM_NETWORK_ACCESS to '%s' -> %s (%s)",
                url, result.decision.value, result.reason_code,
            )
        except Exception as e:
            logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

    # ------------------------------------------------------------------
    # PRD-013/015/016: runtime identity, normalized result, dispatch budget
    # ------------------------------------------------------------------

    def _binding(self, model: str) -> Dict[str, Any]:
        """Config for ``model``: primary llm, an llm_chain entry or an
        agent_llms binding (the first exact, case-folded match)."""
        target = (model or "").casefold()
        cfg = self.config
        if cfg.llm.model.casefold() == target:
            return {"context_window": cfg.llm.context_window, "reasoning": cfg.llm.reasoning,
                    "context_policy": cfg.llm.context_policy}
        candidates = list(cfg.llm_chain)
        for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
            role_cfg = getattr(cfg.agent_llms, role, None)
            if role_cfg is None:
                continue
            if role_cfg.llm is not None:
                candidates.append(role_cfg.llm)
            candidates.extend(role_cfg.llm_chain)
        for candidate in candidates:
            if candidate.model.casefold() == target:
                return {"context_window": candidate.context_window, "reasoning": candidate.reasoning,
                        "context_policy": candidate.context_policy}
        return {"context_window": cfg.llm.context_window, "reasoning": cfg.llm.reasoning,
                "context_policy": cfg.llm.context_policy}

    async def _runtime_fingerprint(self, model: str, base_url: str, api_key: str,
                                   extra_body: Optional[Dict[str, Any]]):
        """PRD-013: the exact runtime this call goes to (cached per process),
        recorded on the active run. Never fails the call."""
        import asyncio

        from kriya.control.run_coordinator import record_model_runtime_use
        from kriya.core.model_runtime import (
            ModelRuntimeFingerprint,
            configured_context_window,
            endpoint_identity,
            kriya_protocol_identity,
            resolve_model_runtime,
        )

        try:
            protocol = kriya_protocol_identity(self.config, model)
            fingerprint = await asyncio.to_thread(
                resolve_model_runtime,
                base_url=base_url, model=model, api_key=api_key,
                egress_policy=self.config.autonomy.egress_policy,
                configured_context=configured_context_window(extra_body),
                kriya_protocol=protocol, config=self.config,
            )
        except Exception as error:
            logger.warning("Model runtime fingerprint unavailable for %s: %s", model, error)
            fingerprint = ModelRuntimeFingerprint(
                alias=model, endpoint=endpoint_identity(base_url), probe_errors=(str(error),),
            )
        if fingerprint.exact:
            record_model_runtime_use(fingerprint.digest)
        return fingerprint

    def _dispatch_budget(self, *, model: str, fingerprint, messages: List[Dict[str, Any]],
                         tools: Optional[List[Dict[str, Any]]], max_tokens: int, is_reasoning: bool,
                         base_url: str, api_key: str, expected_output=None):
        """PRD-016: choose this request's context window and output budget
        (kriya/core/token_budget.py plan_dispatch). Raises
        ContextBudgetUnsatisfiableError / OutputBudgetUnsatisfiableError
        before any inference."""
        from dataclasses import replace

        from kriya.core.model_qualification import measured_limits_for
        from kriya.core.token_budget import DEFAULT_REASONING_ALLOWANCE_TOKENS, plan_dispatch

        binding = self._binding(model)
        policy = binding["context_policy"]
        limits = measured_limits_for(fingerprint, self.config) if fingerprint.exact else {}
        if fingerprint.effective_context_window:
            window, source = fingerprint.effective_context_window, "served_num_ctx"
        else:
            window, source = binding.get("context_window"), "config_declared"
        reasoning = 0
        if is_reasoning:
            reasoning = int(limits.get("reasoning_tokens_max") or DEFAULT_REASONING_ALLOWANCE_TOKENS)
        tokenizer = fingerprint.tokenizer_digest if fingerprint.tokenizer_digest != "unavailable" else None
        from kriya.core.model_qualification import offered_context_tiers

        offer = offered_context_tiers(self.config, model, fingerprint, policy, base_url=base_url, api_key=api_key)
        tiers, ceiling, note = offer.tiers, offer.ceiling, offer.note
        try:
            decision = plan_dispatch(
                messages=messages, tools=tools, requested_max_tokens=max_tokens,
                context_window=window, window_source=source, tokenizer_digest=tokenizer,
                qualified_bytes_per_token=limits.get("bytes_per_token_floor"),
                qualified_non_ascii_bytes_per_token=limits.get("non_ascii_bytes_per_token_floor"),
                reasoning_allowance=reasoning, tiers=tiers, policy_mode=policy.mode,
                expected_output=expected_output, hard_context_ceiling=ceiling,
                hard_output_ceiling=policy.max_output_tokens,
            )
        except Exception as refusal:
            if note and getattr(refusal, "decision", None) is not None:
                refusal.decision = replace(refusal.decision, tier_note=note)
            raise
        return replace(decision, tier_note=note) if note else decision

    def _request_options(self, extra_body: Optional[Dict[str, Any]], budget) -> Optional[Dict[str, Any]]:
        """The request's extra_body with num_ctx set to the selected context
        tier (a copy; the configured extra_body is never changed)."""
        if not budget.context_expanded:
            return extra_body
        options = dict((extra_body or {}).get("options") or {})
        options["num_ctx"] = budget.context_window
        return {**(extra_body or {}), "options": options}

    def _note_budget_expansion(self, result, budget, *, reason: Optional[str] = None) -> None:
        """Evidence for an automatic enlargement (appended to
        budget_expansions and logged)."""
        event = {
            "model": result.model,
            "reason": reason or budget.selection_reason,
            "policy_mode": budget.policy_mode,
            "preferred_context_window": budget.preferred_context_window,
            "selected_context_window": budget.context_window,
            "preferred_output_tokens": budget.requested_max_tokens,
            "selected_output_tokens": result.max_tokens,
            "required_prompt_tokens": budget.prompt_tokens,
            "expected_output_tokens": budget.expected_output_tokens,
            "output_grounding": budget.output_grounding,
            "qualification_source": budget.qualification_source,
            "hard_context_ceiling": budget.hard_context_ceiling,
            "hard_output_ceiling": budget.hard_output_ceiling,
            "runtime_fingerprint": result.runtime_fingerprint,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "status": result.status.value,
        }
        self.budget_expansions.append(event)
        logger.warning(
            "Adaptive budget (%s): %s context %s -> %s, output %s -> %s (prompt ~%s tokens, expected output %s, "
            "tier source %s).",
            event["reason"], result.model, event["preferred_context_window"], event["selected_context_window"],
            event["preferred_output_tokens"], event["selected_output_tokens"], event["required_prompt_tokens"],
            event["expected_output_tokens"], event["qualification_source"],
        )

    def _finish(self, result, *, started: float, budget) -> None:
        """Common post-call bookkeeping: timing, budget comparison, the
        usage line and the observational metrics."""
        import time

        import click

        from kriya.core.token_budget import compare_with_usage

        result.elapsed_seconds = time.time() - started
        if budget is not None:
            result.budget = budget.to_dict()
            comparison = compare_with_usage(result.budget, result.prompt_tokens, model=result.model)
            if comparison:
                result.budget.update(comparison)
            if budget.expanded:
                self._note_budget_expansion(result, budget)
            if (result.protocol or {}).get("empty_content_floor_retry"):
                self._note_budget_expansion(result, budget, reason="empty_content_floor_retry")
        self.last_completion = result
        if result.error is None:
            click.secho(
                f"\n[Usage: {result.prompt_tokens} input tokens, {result.completion_tokens} output tokens | "
                f"Time: {result.elapsed_seconds:.2f}s | Finish: {result.finish_reason or 'unreported'}"
                f"{'' if result.status.value == 'OK' else ' | ' + result.status.value}]",
                fg="blue", dim=True,
            )
            # R1 Deliverable 5 - observational only. tokens_estimated=True means
            # the server's response carried no usage field for prompt and/or
            # completion tokens, so one or both counts are the char/4 heuristic,
            # never presented as exact. finish_reason (VAL-001 G1-R3) is None
            # when the provider/SDK never reported one - never a fabricated "stop".
            self.last_call_metrics = {
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "tokens_estimated": result.tokens_estimated,
                "duration_seconds": result.elapsed_seconds,
                "finish_reason": result.finish_reason,
                "runtime_fingerprint": result.runtime_fingerprint,
                "protocol_status": result.status.value,
                "completion": result.to_telemetry(),
            }

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
        extra_body_override: Optional[Dict[str, Any]] = None,
        expected_output=None,
    ) -> str:
        """Call the local LLM server and return the text completion (supporting streaming and JSON mode).

        PRD-015 compatibility API over ``complete_result``: returns the visible
        content and raises the original exception for a backend error or
        timeout, exactly as before. Capability-sensitive callers read the
        normalized ``self.last_completion`` (a CompletionResult) after the
        call, or call ``complete_result`` directly.

        temperature_override/max_tokens_override/reasoning_override/extra_body_override let
        a caller fully specify an alternate model's real config (not just model/base_url/
        api_key) - without them, is_reasoning falls back to scanning the top-level llm_chain
        for a matching model name (kept for backward compatibility with the existing
        Developer escalation call sites, which only ever pass the first three), and
        extra_body falls back to the PRIMARY model's own extra_body (kriya/config/config.py's
        LLMConfig) - which is only correct when no fallback model is actually in play. A
        caller escalating to a FallbackModelConfig entry should pass its own
        extra_body_override (even an empty dict, to mean "no extra_body for this model"),
        the same way it already passes that entry's model/base_url/api_key/temperature -
        otherwise the primary's own extra_body (e.g. a reasoning_effort tuned for a
        completely different model) silently applies to the fallback call instead."""
        result = await self.complete_result(
            system_prompt, user_prompt, stream_callback=stream_callback, json_mode=json_mode,
            model_override=model_override, base_url_override=base_url_override,
            api_key_override=api_key_override, temperature_override=temperature_override,
            max_tokens_override=max_tokens_override, reasoning_override=reasoning_override,
            extra_body_override=extra_body_override, expected_output=expected_output,
        )
        if result.error is not None:
            logger.error(f"Local LLM call failed: {result.error}", exc_info=result.error)
            raise result.error
        if result.status.value != "OK":
            logger.warning(
                "Completion from '%s' is %s (finish_reason=%s); returned to a compatibility caller as text.",
                result.model, result.status.value, result.finish_reason,
            )
        return result.content

    async def complete_result(
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
        extra_body_override: Optional[Dict[str, Any]] = None,
        expected_output=None,
    ):
        """PRD-015: one completion as a normalized ``CompletionResult``.

        Raises only for policy refusals (egress, CONTEXT_BUDGET_UNSATISFIABLE,
        OUTPUT_BUDGET_UNSATISFIABLE) and cancellation (recorded as CANCELLED
        first, never swallowed); a backend error or timeout is returned as
        BACKEND_ERROR/TIMEOUT.

        ``expected_output`` (token_budget.OutputExpectation) is a caller's
        GROUNDED estimate of the output this request needs (e.g. the size of
        a file being rewritten); under the adaptive budget policy it may
        enlarge the output budget and select a larger qualified context
        window (PRD-016). Without it the output never grows."""
        import asyncio
        import time

        from kriya.core.completion import CompletionResult, CompletionStatus, classify, split_reasoning

        # MA4.3 - audit-only, always runs regardless of egress_policy, and can
        # never affect the unconditional enforcement immediately below.
        url_to_check = base_url_override or self.config.llm.base_url
        self._audit_llm_network_access(url_to_check)

        # Validate egress policy (unchanged - the sole enforcement path)
        if self.config.autonomy.egress_policy == "local_only":
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
        max_tokens = max(base_max_tokens, REASONING_MIN_MAX_TOKENS) if is_reasoning else base_max_tokens
        if extra_body_override is not None:
            extra_body = extra_body_override or None
        else:
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

        self.last_call_metrics = None
        self.last_completion = None
        fingerprint = await self._runtime_fingerprint(
            model, url_to_check, api_key_override or self.config.llm.api_key, extra_body,
        )
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        api_key = api_key_override or self.config.llm.api_key
        budget = self._dispatch_budget(
            model=model, fingerprint=fingerprint, messages=messages, tools=None,
            max_tokens=max_tokens, is_reasoning=is_reasoning, base_url=url_to_check, api_key=api_key,
            expected_output=expected_output,
        )
        max_tokens = budget.max_tokens
        if budget.context_expanded:
            # The selected tier is a different runtime input (num_ctx), so a
            # different fingerprint: the call is attributed to it.
            extra_body = self._request_options(extra_body, budget)
            fingerprint = await self._runtime_fingerprint(model, url_to_check, api_key, extra_body)

        logger.info(f"Sending completion request to local LLM [Model: {model}, Stream: {stream_callback is not None}, JSON Mode: {json_mode}, Reasoning: {is_reasoning}]")
        start_time = time.time()
        result = CompletionResult(
            status=CompletionStatus.OK, model=model,
            runtime_fingerprint=fingerprint.digest, runtime_fingerprint_exact=fingerprint.exact,
            protocol={"json_mode": json_mode, "streaming": stream_callback is not None, "tools": False,
                      "reasoning_model": is_reasoning, "response_format_dropped": False,
                      "empty_content_floor_retry": False},
            max_tokens=max_tokens,
        )

        try:
            try:
                raw = await self._request_once(
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
                    result.protocol["response_format_dropped"] = True
                    raw = await self._request_once(
                        client, model, system_prompt, user_prompt, temperature, max_tokens,
                        extra_body, None, stream_callback
                    )
                else:
                    raise

            content, hidden = split_reasoning(raw["content"], anywhere=is_reasoning)
            if not is_reasoning and json_mode and not content and max_tokens < REASONING_MIN_MAX_TOKENS:
                # Some models emit hidden <think>...</think> reasoning before ever
                # committing to JSON regardless of Kriya's own is_reasoning
                # classification for them (a static per-model config guess, not a
                # live observation) - when that happens, the reasoning-only 12288-
                # token floor above never applies, so a tight max_tokens can get
                # entirely consumed by hidden reasoning with literally nothing ever
                # written to `content`. Retry once with the same floor reasoning
                # models get, rather than hand-tuning max_tokens_override per
                # affected model as each is found one at a time - confirmed live
                # for two different models this way already (gpt-oss:20b, then
                # qwen3.6:35b-a3b), neither ever classified reasoning=True in this
                # project's own llm_chain config.
                logger.warning(
                    f"JSON-mode completion from '{model}' returned empty content at "
                    f"max_tokens={max_tokens} (likely silent reasoning) - retrying once "
                    "with a 12288-token floor."
                )
                # An ungrounded enlargement: bounded by the selected window
                # and the hard output ceiling, and recorded as an expansion.
                floor = REASONING_MIN_MAX_TOKENS
                if budget.context_window:
                    floor = min(floor, max(max_tokens, budget.context_window - budget.prompt_tokens
                                           - budget.safety_margin))
                if budget.hard_output_ceiling is not None:
                    floor = min(floor, max(max_tokens, budget.hard_output_ceiling))
                result.protocol["empty_content_floor_retry"] = True
                result.max_tokens = floor
                raw = await self._request_once(
                    client, model, system_prompt, user_prompt, temperature, floor,
                    extra_body, response_format, stream_callback
                )
                content, hidden = split_reasoning(raw["content"], anywhere=True)
        except asyncio.CancelledError:
            result.status = CompletionStatus.CANCELLED
            result.backend_status = "cancelled"
            self._finish(result, started=start_time, budget=budget)
            raise
        except Exception as e:
            result.status = CompletionStatus.TIMEOUT if _is_timeout(e) else CompletionStatus.BACKEND_ERROR
            result.backend_status = "error"
            result.backend_error = f"{type(e).__name__}: {e}"[:500]
            result.error = e
            self._finish(result, started=start_time, budget=budget)
            return result

        reasoning_chars = hidden + raw["reasoning_chars"]
        result.content = content
        result.reasoning_present = reasoning_chars > 0
        result.reasoning_chars = reasoning_chars
        result.reasoning_source = (
            "reasoning_field" if raw["reasoning_chars"] else ("think_tags" if hidden else None)
        )
        result.finish_reason = raw["finish_reason"]
        result.provider_metadata = raw["provider_metadata"]
        prompt_tokens, completion_tokens = raw["prompt_tokens"], raw["completion_tokens"]
        result.tokens_estimated = prompt_tokens == 0 or completion_tokens == 0
        result.prompt_tokens = prompt_tokens or int((len(system_prompt) + len(user_prompt)) / 4)
        result.completion_tokens = completion_tokens or int(len(content) / 4)
        result.status, result.parser_status = classify(
            content=content, finish_reason=result.finish_reason, tool_calls=[], structured=json_mode,
        )
        self._finish(result, started=start_time, budget=budget)
        return result

    async def _request_once(
        self, client, model, system_prompt, user_prompt, temperature, max_tokens,
        extra_body, response_format, stream_callback
    ) -> Dict[str, Any]:
        """Issues a single completion request (streaming or not) and returns
        the raw fields the normalizer needs: content, reasoning_chars (from a
        separate provider reasoning field), prompt_tokens, completion_tokens,
        finish_reason and provider_metadata. Split out from complete_result()
        so a reasoning model's response_format can be retried once without it.

        Every provider field is read defensively (``getattr(..., None)`` and
        a type check, never a bare attribute access): a provider/SDK that
        omits ``usage`` degrades the token counts to 0 (estimated later), and
        one that omits ``finish_reason`` degrades it to None - never a
        fabricated "stop" (VAL-001 G1-R3)."""
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = None
        reasoning_chars = 0
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        if stream_callback:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
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
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    extra_body=extra_body,
                    response_format=response_format
                )

            chunks = []
            metadata: Dict[str, Any] = {}
            async for chunk in response:
                if not metadata:
                    metadata = _provider_metadata(chunk)
                usage = getattr(chunk, "usage", None)
                if usage:
                    prompt_tokens = _int_or_zero(getattr(usage, "prompt_tokens", 0))
                    completion_tokens = _int_or_zero(getattr(usage, "completion_tokens", 0))
                if chunk.choices:
                    # The finish_reason-carrying chunk is typically the LAST
                    # one and usually has empty/None delta content - checked
                    # unconditionally here, and only overwritten when a real
                    # value is present, so an earlier chunk's null
                    # finish_reason can never clobber a real one seen later.
                    chunk_finish_reason = _str_or_none(getattr(chunk.choices[0], "finish_reason", None))
                    if chunk_finish_reason:
                        finish_reason = chunk_finish_reason
                    delta = chunk.choices[0].delta
                    reasoning_delta = _str_or_none(getattr(delta, "reasoning", None))
                    if reasoning_delta:
                        reasoning_chars += len(reasoning_delta)
                    if delta.content:
                        chunks.append(delta.content)
                        stream_callback(delta.content)
            content = "".join(chunks).strip()
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                response_format=response_format
            )
            metadata = _provider_metadata(response)
            usage = getattr(response, "usage", None)
            if usage:
                prompt_tokens = _int_or_zero(getattr(usage, "prompt_tokens", 0))
                completion_tokens = _int_or_zero(getattr(usage, "completion_tokens", 0))
            if response.choices:
                finish_reason = _str_or_none(getattr(response.choices[0], "finish_reason", None))
            message = response.choices[0].message
            reasoning = _str_or_none(getattr(message, "reasoning", None)) or _str_or_none(
                getattr(message, "reasoning_content", None)
            )
            reasoning_chars = len(reasoning) if reasoning else 0
            content = (message.content or "").strip()
        return {
            "content": content,
            "reasoning_chars": reasoning_chars,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish_reason,
            "provider_metadata": metadata,
        }

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Single-turn native tool-calling completion. Unlike complete()'s fixed
        system/user string pair, a tool-calling loop needs to append tool_calls and
        tool results as its own messages between turns - so this takes a full
        OpenAI-style message list and returns raw enough structure (content +
        decoded tool_calls) for the caller to drive that loop itself. One call is
        one model turn; this method does NOT loop turns - see
        kriya/workflow/self_correction.py for the only current caller's turn-budget
        loop.

        PRD-015 compatibility API over ``complete_with_tools_result``: the
        returned dict and raised exceptions are unchanged."""
        result = await self.complete_with_tools_result(
            messages, tools, model_override=model_override, base_url_override=base_url_override,
            api_key_override=api_key_override, temperature_override=temperature_override,
            max_tokens_override=max_tokens_override, extra_body_override=extra_body_override,
        )
        if result.error is not None:
            raise result.error
        return {
            "content": result.content,
            "tool_calls": [
                {key: value for key, value in call.items() if key != "source"} for call in result.tool_calls
            ],
        }

    async def complete_with_tools_result(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
    ):
        """PRD-015: one native tool-calling turn as a CompletionResult.

        Reuses complete()'s own egress check and base_url_override/api_key_override
        client-construction logic unchanged - this is a second call site into the
        same safety boundary, not a parallel one. Native tool calls are
        normalized here; when a backend returned tool calls as text (Hermes
        JSON or Qwen XML ``<tool_call>`` blocks its own parser did not
        convert), they are recovered here too, and every call's arguments go
        through the same capability validation."""
        import asyncio
        import time

        from kriya.core.completion import (
            CompletionResult,
            CompletionStatus,
            classify,
            parse_textual_tool_calls,
            split_reasoning,
        )

        url_to_check = base_url_override or self.config.llm.base_url
        self._audit_llm_network_access(url_to_check)

        if self.config.autonomy.egress_policy == "local_only":
            if not is_local_url(url_to_check):
                raise EgressViolationError(
                    f"Egress violation: Request to external API '{url_to_check}' blocked under 'local_only' policy."
                )

        model = model_override or self.model
        from kriya.core.model_capabilities import (
            ModelCapabilityError,
            capabilities_for_model,
            validate_tool_call_sample,
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
        if extra_body_override is not None:
            extra_body = extra_body_override or None
        else:
            extra_body = self.config.llm.extra_body if self.config.llm.extra_body else None

        self.last_call_metrics = None
        self.last_completion = None
        fingerprint = await self._runtime_fingerprint(
            model, url_to_check, api_key_override or self.config.llm.api_key, extra_body,
        )
        api_key = api_key_override or self.config.llm.api_key
        budget = self._dispatch_budget(
            model=model, fingerprint=fingerprint, messages=messages, tools=tools,
            max_tokens=max_tokens, is_reasoning=False, base_url=url_to_check, api_key=api_key,
        )
        max_tokens = budget.max_tokens
        if budget.context_expanded:
            extra_body = self._request_options(extra_body, budget)
            fingerprint = await self._runtime_fingerprint(model, url_to_check, api_key, extra_body)
        start_time = time.time()
        result = CompletionResult(
            status=CompletionStatus.OK, model=model,
            runtime_fingerprint=fingerprint.digest, runtime_fingerprint_exact=fingerprint.exact,
            protocol={"json_mode": False, "streaming": False, "tools": True, "tool_count": len(tools)},
            max_tokens=max_tokens,
        )
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
        except asyncio.CancelledError:
            result.status = CompletionStatus.CANCELLED
            result.backend_status = "cancelled"
            self._finish(result, started=start_time, budget=budget)
            raise
        except Exception as e:
            result.status = CompletionStatus.TIMEOUT if _is_timeout(e) else CompletionStatus.BACKEND_ERROR
            result.backend_status = "error"
            result.backend_error = f"{type(e).__name__}: {e}"[:500]
            result.error = e
            self._finish(result, started=start_time, budget=budget)
            return result

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
                    "id": tc.id, "name": tc.function.name, "arguments": {}, "source": "native",
                })
                result.parser_status = "malformed_tool_arguments"
                continue

            sample = validate_tool_call_sample(raw_arguments, capabilities)
            if not sample.compatible:
                logger.warning(
                    "Rejected incompatible local-model tool arguments for '%s': %s",
                    tc.function.name, "; ".join(sample.violations),
                )
                tool_calls.append({
                    "id": tc.id, "name": tc.function.name, "arguments": {},
                    "argument_error": "; ".join(sample.violations), "source": "native",
                })
                continue
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": arguments, "source": "native"})

        content, hidden = split_reasoning(message.content or "")
        if not tool_calls and "<tool_call>" in content:
            recovered, content, errors = parse_textual_tool_calls(content)
            for call in recovered:
                sample = validate_tool_call_sample(json.dumps(call["arguments"]), capabilities)
                if not sample.compatible:
                    call["argument_error"] = "; ".join(sample.violations)
                    call["arguments"] = {}
            tool_calls.extend(recovered)
            if errors:
                result.parser_status = "malformed_textual_tool_call"
        result.content = content
        result.tool_calls = tool_calls
        reasoning = _str_or_none(getattr(message, "reasoning", None))
        result.reasoning_chars = hidden + (len(reasoning) if reasoning else 0)
        result.reasoning_present = result.reasoning_chars > 0
        result.reasoning_source = "reasoning_field" if reasoning else ("think_tags" if hidden else None)
        result.finish_reason = _str_or_none(getattr(response.choices[0], "finish_reason", None))
        result.provider_metadata = _provider_metadata(response)
        usage = getattr(response, "usage", None)
        prompt_tokens = _int_or_zero(getattr(usage, "prompt_tokens", 0)) if usage else 0
        completion_tokens = _int_or_zero(getattr(usage, "completion_tokens", 0)) if usage else 0
        result.tokens_estimated = prompt_tokens == 0 or completion_tokens == 0
        result.prompt_tokens = prompt_tokens or None
        result.completion_tokens = completion_tokens or None
        status, _ = classify(content=content, finish_reason=result.finish_reason, tool_calls=tool_calls,
                             structured=False)
        result.status = status
        if result.parser_status == "not_applicable" and tool_calls:
            result.parser_status = "ok"
        self._finish(result, started=start_time, budget=budget)
        return result


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _provider_metadata(response: Any) -> Dict[str, Any]:
    """Response identifiers only; anything that is not a plain string is dropped."""
    return {
        key: value
        for key in ("id", "model", "system_fingerprint")
        if isinstance(value := getattr(response, key, None), str) and value
    }


def _is_timeout(error: BaseException) -> bool:
    import asyncio

    return isinstance(error, (APITimeoutError, asyncio.TimeoutError, TimeoutError))
