import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from kriya.config import AppConfig
from kriya.core.llm import LLMClient

@pytest.mark.asyncio
async def test_llm_client_forwards_extra_body():
    cfg = AppConfig()
    cfg.llm.model = "qwen3-coder:30b"
    cfg.llm.extra_body = {
        "options": {
            "num_ctx": 32768,
            "top_p": 0.8
        }
    }
    
    llm = LLMClient(cfg)
    
    # Mock chat completions create
    mock_create = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Mock response"
    mock_response.usage = None
    mock_create.return_value = mock_response
    
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user")
        assert res == "Mock response"
        
        # Verify that extra_body was forwarded exactly as specified in the configuration
        mock_create.assert_called_once()
        kwargs = mock_create.call_args[1]
        assert kwargs.get("extra_body") == {
            "options": {
                "num_ctx": 32768,
                "top_p": 0.8
            }
        }
