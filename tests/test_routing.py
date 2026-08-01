from unittest.mock import AsyncMock

import pytest

from kriya.config import AppConfig
from kriya.routing import (
    _EXEMPLARS,
    CLARIFY,
    ROUTABLE_COMMANDS,
    UNROUTABLE,
    Router,
    RoutingModelUnavailable,
    build_dispatch_tokens,
)


def _router_with_mocks(gate_result: bool, ranked: list, reject_threshold: float = 0.3, ask_margin: float = 0.05) -> Router:
    """Bypasses real embedding/LLM calls by patching Router's internal
    _is_in_scope/_rank methods directly - the orchestration logic in route()
    is what's under test, not embedding math or a live gate call."""
    cfg = AppConfig()
    cfg.routing.reject_threshold = reject_threshold
    cfg.routing.ask_margin = ask_margin
    router = Router(cfg)
    router._is_in_scope = AsyncMock(return_value=gate_result)
    router._rank = AsyncMock(return_value=ranked)
    return router


def test_routable_commands_matches_exemplar_keys():
    assert set(ROUTABLE_COMMANDS) == set(_EXEMPLARS.keys())


@pytest.mark.asyncio
async def test_route_returns_unroutable_when_gate_says_out_of_scope():
    # Even a confident embeddings match must not override the gate - the gate
    # exists specifically because raw similarity can't tell in-scope from
    # topically-similar-but-out-of-scope (see kriya/routing.py module docstring).
    router = _router_with_mocks(gate_result=False, ranked=[("generate", 0.9), ("ask", 0.1)])
    result = await router.route("delete all my files")
    assert result.label == UNROUTABLE


@pytest.mark.asyncio
async def test_route_returns_unroutable_below_reject_threshold_even_if_in_scope():
    router = _router_with_mocks(gate_result=True, ranked=[("generate", 0.2), ("ask", 0.1)], reject_threshold=0.3)
    result = await router.route("hmm")
    assert result.label == UNROUTABLE


@pytest.mark.asyncio
async def test_route_returns_clarify_when_top_two_are_within_margin():
    router = _router_with_mocks(gate_result=True, ranked=[("generate", 0.70), ("ask", 0.68)], ask_margin=0.05)
    result = await router.route("something ambiguous")
    assert result.label == CLARIFY
    assert result.candidates == ["generate", "ask"]


@pytest.mark.asyncio
async def test_route_returns_top_label_when_confidently_separated():
    router = _router_with_mocks(gate_result=True, ranked=[("fix", 0.8), ("ask", 0.3)], ask_margin=0.05)
    result = await router.route("the build is broken, fix it")
    assert result.label == "fix"


@pytest.mark.asyncio
async def test_ensure_fitted_raises_loudly_on_unreachable_embed_model():
    # OllamaEmbeddingClient degrades to an all-zero vector (not an exception)
    # when the underlying model call fails - this must be caught and turned
    # into a loud, actionable error, not silently produce meaningless
    # centroids (or worse, silently fall back to embedding.model, which
    # measured 18 points worse on routing accuracy in the validating spike).
    cfg = AppConfig()
    router = Router(cfg)
    router._embed_client.get_embeddings = AsyncMock(return_value=[[0.0, 0.0, 0.0]])
    with pytest.raises(RoutingModelUnavailable):
        await router.route("anything")


def test_build_dispatch_tokens_generate_is_passthrough():
    assert build_dispatch_tokens("generate", "add a health check") == ["generate", "add a health check"]


def test_build_dispatch_tokens_ask_is_passthrough():
    assert build_dispatch_tokens("ask", "why is this slow") == ["ask", "why is this slow"]


def test_build_dispatch_tokens_fix_uses_error_flag_not_positional():
    assert build_dispatch_tokens("fix", "NullPointerException at Foo.bar") == [
        "fix", "--error", "NullPointerException at Foo.bar"
    ]


def test_build_dispatch_tokens_review_defaults_to_repo_root_not_raw_text():
    # review's CLI argument requires an EXISTING path (click.Path(exists=True)) -
    # passing raw natural language through would fail immediately.
    assert build_dispatch_tokens("review", "review my recent changes") == ["review", "."]


def test_build_dispatch_tokens_analyze_defaults_to_repo_root_not_raw_text():
    assert build_dispatch_tokens("analyze", "what does this repo look like") == ["analyze", "."]


def test_build_dispatch_tokens_skills_defaults_to_list():
    assert build_dispatch_tokens("skills", "what skills exist") == ["skills", "list"]


def test_build_dispatch_tokens_rejects_non_routable_labels():
    with pytest.raises(ValueError):
        build_dispatch_tokens(UNROUTABLE, "anything")
    with pytest.raises(ValueError):
        build_dispatch_tokens(CLARIFY, "anything")
