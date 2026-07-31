from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

@pytest.mark.asyncio
async def test_local_egress_policy():
    from kriya.core.llm import EgressViolationError
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.llm.base_url = "https://api.deepseek.com/v1"

    llm = LLMClient(cfg)

    with pytest.raises(EgressViolationError):
        await llm.complete("system", "user")


def _mock_response(content):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.usage = None
    return mock_response


@pytest.mark.asyncio
async def test_json_mode_sets_response_format_for_reasoning_model():
    """A reasoning model must not be excluded from response_format - without it, it
    has nothing forcing it to ever commit to JSON at all (a real observed failure:
    the model just explains its reasoning in prose instead)."""
    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response('["a.txt"]'))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True)
        assert res == '["a.txt"]'
        assert mock_create.call_args[1].get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_json_mode_sets_response_format_for_non_reasoning_model():
    cfg = AppConfig()
    cfg.llm.reasoning = False
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response('["a.txt"]'))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user", json_mode=True)
        assert mock_create.call_args[1].get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_non_json_mode_never_sets_response_format():
    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response("plain text"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user", json_mode=False)
        assert mock_create.call_args[1].get("response_format") is None


@pytest.mark.asyncio
async def test_reasoning_model_retries_once_without_response_format_on_failure():
    """If a backend/model combination rejects json_object + reasoning together, retry
    once without response_format rather than failing the whole call outright."""
    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    mock_create = AsyncMock(side_effect=[
        Exception("this backend rejects response_format for reasoning models"),
        _mock_response('["a.txt"]'),
    ])
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True)
        assert res == '["a.txt"]'
        assert mock_create.call_count == 2
        assert mock_create.call_args_list[0][1].get("response_format") == {"type": "json_object"}
        assert mock_create.call_args_list[1][1].get("response_format") is None


@pytest.mark.asyncio
async def test_non_reasoning_model_does_not_retry_on_failure():
    """The retry-without-response_format path is scoped to reasoning models only - a
    non-reasoning model's json_mode call already worked unconditionally before this
    change, so a failure there should propagate normally, not silently retry."""
    cfg = AppConfig()
    cfg.llm.reasoning = False
    llm = LLMClient(cfg)

    mock_create = AsyncMock(side_effect=Exception("real failure"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        with pytest.raises(Exception, match="real failure"):
            await llm.complete("system", "user", json_mode=True)
        assert mock_create.call_count == 1


@pytest.mark.asyncio
async def test_reasoning_model_retry_failure_propagates_original_style_error():
    """If the retry without response_format ALSO fails, the call should still raise
    (not silently swallow both failures)."""
    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    mock_create = AsyncMock(side_effect=[
        Exception("first failure"),
        Exception("second failure"),
    ])
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        with pytest.raises(Exception, match="second failure"):
            await llm.complete("system", "user", json_mode=True)
        assert mock_create.call_count == 2
