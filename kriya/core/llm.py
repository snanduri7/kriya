import ipaddress
import logging
import re
import socket
from typing import Callable, Optional
from urllib.parse import urlparse

from openai import AsyncOpenAI

from kriya.config import AppConfig

logger = logging.getLogger(__name__)

class EgressViolationError(ValueError):
    """Raised when an LLM completion request violates local_only egress policy."""
    pass

def is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True
            
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
        api_key_override: Optional[str] = None
    ) -> str:
        """Call the local LLM server and return the text completion (supporting streaming and JSON mode)."""
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
            
        is_reasoning = self.config.llm.reasoning
        if model_override:
            for fb in self.config.llm_chain:
                if fb.model == model_override:
                    is_reasoning = fb.reasoning
                    break

        max_tokens = max(self.max_tokens, 12288) if is_reasoning else self.max_tokens
        extra_body = self.config.llm.extra_body if self.config.llm.extra_body else None
        response_format = None if is_reasoning else ({"type": "json_object"} if json_mode else None)
        
        logger.info(f"Sending completion request to local LLM [Model: {model}, Stream: {stream_callback is not None}, JSON Mode: {json_mode}, Reasoning: {is_reasoning}]")
        import time

        import click
        
        prompt_tokens = 0
        completion_tokens = 0
        start_time = time.time()
        
        try:
            if stream_callback:
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=self.temperature,
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
                        temperature=self.temperature,
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
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    response_format=response_format
                )
                if hasattr(response, "usage") and response.usage:
                    prompt_tokens = response.usage.prompt_tokens
                    completion_tokens = response.usage.completion_tokens
                content = response.choices[0].message.content or ""
                content = content.strip()
                
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
