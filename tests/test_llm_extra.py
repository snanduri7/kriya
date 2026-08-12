from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.llm import LLMClient, is_local_url


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


@pytest.mark.asyncio
async def test_reasoning_model_does_not_retry_on_a_connection_or_auth_error():
    """Regression test for a finding from the 2026-08-12 SME review: the
    retry-without-response_format path previously triggered on ANY exception
    from the first request, not just one that plausibly indicates
    response_format/reasoning incompatibility - a connection-refused or
    auth error got silently "retried" too, adding latency and reporting a
    misleading root cause (the retry's own log message claims "may not
    support JSON mode with reasoning", which isn't what actually failed)."""
    import openai

    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    connection_error = openai.APIConnectionError(request=MagicMock())
    mock_create = AsyncMock(side_effect=connection_error)
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        with pytest.raises(openai.APIConnectionError):
            await llm.complete("system", "user", json_mode=True)
        assert mock_create.call_count == 1  # no retry attempted


@pytest.mark.asyncio
async def test_reasoning_model_still_retries_on_an_unrecognized_exception():
    """The exclusion list only rules out exception types clearly unrelated to
    response_format (connection/timeout/auth/rate-limit/server errors) - an
    unrecognized exception (e.g. a genuine backend rejection of the
    combination) still gets the benefit of the doubt and retries, matching
    test_reasoning_model_retries_once_without_response_format_on_failure
    above but confirming this explicitly survives the new exclusion logic."""
    cfg = AppConfig()
    cfg.llm.reasoning = True
    llm = LLMClient(cfg)

    mock_create = AsyncMock(side_effect=[
        ValueError("some other unexpected failure shape"),
        _mock_response('["a.txt"]'),
    ])
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True)
        assert res == '["a.txt"]'
        assert mock_create.call_count == 2


def test_is_local_url_fails_closed_on_hostname_less_url():
    """Regression test for a real bug found live, 2026-08-12 (SME architecture
    review): a URL with no parseable hostname (e.g. a malformed/typo'd
    base_url missing "http://") used to return True (treated as local/
    allowed) - the opposite of the fail-closed behavior this function's own
    except block already implements for every other failure mode, and a
    direct contradiction of its role as a hard egress safety boundary."""
    assert is_local_url("not-a-valid-url-at-all") is False
    assert is_local_url("") is False
    assert is_local_url("localhost:11434/v1") is False  # missing scheme -> no hostname parsed


def test_is_local_url_still_allows_real_local_hosts():
    assert is_local_url("http://localhost:11434/v1") is True
    assert is_local_url("http://127.0.0.1:11434/v1") is True
    assert is_local_url("http://my-machine.local:11434/v1") is True


def test_is_local_url_still_rejects_real_remote_hosts():
    assert is_local_url("http://api.openai.com/v1") is False
