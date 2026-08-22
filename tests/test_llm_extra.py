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
async def test_complete_uses_fallback_extra_body_not_the_primarys():
    """Regression test for a real gap, 2026-08-22: every call site that
    escalates to a fallback model unconditionally used the PRIMARY model's
    own extra_body (config.llm.extra_body) regardless of which model was
    actually being called - a fallback needing different request shape
    (e.g. qwen3.8:27b's reasoning_effort) had no way to get it, and the
    primary's own tuning would silently leak onto the fallback call."""
    cfg = AppConfig()
    cfg.llm.extra_body = {"reasoning_effort": "xhigh"}
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response("ok"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user", model_override="qwen3.8:27b", extra_body_override={"reasoning_effort": "none"})
        assert mock_create.call_args[1].get("extra_body") == {"reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_complete_falls_back_to_primary_extra_body_when_no_override_given():
    """Unchanged behavior for every existing caller that doesn't pass
    extra_body_override at all (None, the default)."""
    cfg = AppConfig()
    cfg.llm.extra_body = {"reasoning_effort": "xhigh"}
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response("ok"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user")
        assert mock_create.call_args[1].get("extra_body") == {"reasoning_effort": "xhigh"}


@pytest.mark.asyncio
async def test_complete_empty_dict_extra_body_override_means_no_extra_body():
    """A fallback with no extra_body of its own (the common case - defaults to
    {}) must send NO extra_body, not silently inherit the primary's."""
    cfg = AppConfig()
    cfg.llm.extra_body = {"reasoning_effort": "xhigh"}
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response("ok"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user", model_override="some-other-model", extra_body_override={})
        assert mock_create.call_args[1].get("extra_body") is None


@pytest.mark.asyncio
async def test_complete_with_tools_uses_fallback_extra_body_not_the_primarys():
    cfg = AppConfig()
    cfg.llm.extra_body = {"reasoning_effort": "xhigh"}
    llm = LLMClient(cfg)

    mock_message = MagicMock()
    mock_message.tool_calls = []
    mock_message.content = "done"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = None
    mock_create = AsyncMock(return_value=mock_response)
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete_with_tools(
            [{"role": "user", "content": "hi"}], [],
            model_override="qwen3.8:27b", extra_body_override={"reasoning_effort": "none"},
        )
        assert mock_create.call_args[1].get("extra_body") == {"reasoning_effort": "none"}


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
async def test_json_mode_retries_once_with_a_token_floor_on_empty_response():
    """Regression test for a real bug caught live, 2026-08-22: some models emit
    hidden <think>...</think> reasoning before ever committing to JSON regardless
    of Kriya's own is_reasoning classification for them (a static per-model
    config guess, not a live observation) - a tight max_tokens_override then gets
    entirely consumed by hidden reasoning with nothing ever written to `content`,
    and json.loads("") raises "Expecting value: line 1 column 1". Confirmed live
    for two different models classified reasoning=False in this project's own
    llm_chain config (gpt-oss:20b, then qwen3.6:35b-a3b) - complete() must detect
    this directly and retry once with the same 12288-token floor reasoning
    models get, rather than requiring another hand-tuned max_tokens_override per
    affected model."""
    cfg = AppConfig()
    cfg.llm.reasoning = False
    llm = LLMClient(cfg)

    mock_create = AsyncMock(side_effect=[_mock_response(""), _mock_response('{"files": []}')])
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True, max_tokens_override=2000)
        assert res == '{"files": []}'
        assert mock_create.call_count == 2
        assert mock_create.call_args_list[0][1].get("max_tokens") == 2000
        assert mock_create.call_args_list[1][1].get("max_tokens") == 12288


@pytest.mark.asyncio
async def test_json_mode_does_not_retry_when_response_is_non_empty():
    cfg = AppConfig()
    cfg.llm.reasoning = False
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response('{"files": ["a.py"]}'))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True, max_tokens_override=2000)
        assert res == '{"files": ["a.py"]}'
        assert mock_create.call_count == 1


@pytest.mark.asyncio
async def test_json_mode_does_not_retry_when_already_at_or_above_the_floor():
    """An empty response at/above the 12288 floor is a genuine failure, not a
    truncated-reasoning symptom the floor can fix - retrying with the same
    budget again would just burn another call for the same empty result."""
    cfg = AppConfig()
    cfg.llm.reasoning = False
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_response(""))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete("system", "user", json_mode=True, max_tokens_override=12288)
        assert res == ""
        assert mock_create.call_count == 1


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


def _mock_tool_call_response(tool_calls=None, content=""):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].message.tool_calls = tool_calls
    return mock_response


@pytest.mark.asyncio
async def test_complete_with_tools_returns_tool_calls():
    cfg = AppConfig()
    llm = LLMClient(cfg)

    raw_call = MagicMock()
    raw_call.id = "call_1"
    raw_call.function.name = "recompile"
    raw_call.function.arguments = '{"foo": "bar"}'

    mock_create = AsyncMock(return_value=_mock_tool_call_response(tool_calls=[raw_call]))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete_with_tools(
            [{"role": "user", "content": "fix it"}],
            [{"type": "function", "function": {"name": "recompile", "parameters": {}}}],
        )
        assert res["content"] == ""
        assert res["tool_calls"] == [{"id": "call_1", "name": "recompile", "arguments": {"foo": "bar"}}]
        assert mock_create.call_args[1].get("tools") is not None
        assert mock_create.call_args[1].get("tool_choice") == "auto"


@pytest.mark.asyncio
async def test_complete_with_tools_handles_empty_tool_calls():
    cfg = AppConfig()
    llm = LLMClient(cfg)

    mock_create = AsyncMock(return_value=_mock_tool_call_response(tool_calls=None, content="all done"))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete_with_tools([{"role": "user", "content": "status?"}], [])
        assert res == {"content": "all done", "tool_calls": []}


@pytest.mark.asyncio
async def test_complete_with_tools_respects_egress_policy():
    from kriya.core.llm import EgressViolationError

    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    llm = LLMClient(cfg)

    mock_create = AsyncMock()
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        with pytest.raises(EgressViolationError):
            await llm.complete_with_tools(
                [{"role": "user", "content": "x"}], [], base_url_override="https://api.deepseek.com/v1",
            )
        mock_create.assert_not_called()


@pytest.mark.asyncio
async def test_complete_with_tools_malformed_arguments_does_not_crash():
    """Echoes this session's confirmed local-model failure mode - malformed/
    truncated JSON even at small tool-call-argument scale - falling back to
    {} rather than raising keeps one bad tool call from crashing the loop."""
    cfg = AppConfig()
    llm = LLMClient(cfg)

    raw_call = MagicMock()
    raw_call.id = "call_1"
    raw_call.function.name = "apply_patch"
    raw_call.function.arguments = '{"filepath": "a.py", "edits": [truncated...'

    mock_create = AsyncMock(return_value=_mock_tool_call_response(tool_calls=[raw_call]))
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        res = await llm.complete_with_tools([{"role": "user", "content": "fix it"}], [])
        assert res["tool_calls"] == [{"id": "call_1", "name": "apply_patch", "arguments": {}}]
