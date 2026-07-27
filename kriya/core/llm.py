import logging
from typing import Optional
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

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Call the local LLM server and return the text completion."""
        logger.info(f"Sending completion request to local LLM [Model: {self.model}]")
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            content = response.choices[0].message.content
            if content is None:
                return ""
            return content.strip()
        except Exception as e:
            logger.error(f"Local LLM call failed: {e}", exc_info=True)
            raise e
