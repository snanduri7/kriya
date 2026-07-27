import logging
from typing import Optional, Callable
from openai import AsyncOpenAI
from kriya.config import AppConfig

logger = logging.getLogger(__name__)

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
        stream_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """Call the local LLM server and return the text completion (supporting streaming)."""
        logger.info(f"Sending completion request to local LLM [Model: {self.model}, Stream: {stream_callback is not None}]")
        import time
        import click
        
        prompt_tokens = 0
        completion_tokens = 0
        start_time = time.time()
        
        extra_body = self.config.llm.extra_body if self.config.llm.extra_body else None
        
        try:
            if stream_callback:
                try:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        stream=True,
                        stream_options={"include_usage": True},
                        extra_body=extra_body
                    )
                except Exception:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        stream=True,
                        extra_body=extra_body
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
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    extra_body=extra_body
                )
                if hasattr(response, "usage") and response.usage:
                    prompt_tokens = response.usage.prompt_tokens
                    completion_tokens = response.usage.completion_tokens
                content = response.choices[0].message.content or ""
                content = content.strip()
                
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
