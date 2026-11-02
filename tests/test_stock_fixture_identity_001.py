"""LIVE-STOCK-FIXTURE-IDENTITY-001: the stock live fixtures (batch 5 and batch 6) bind the qualified production identity, assert it before any product
result, and run on git-backed fixture repositories - deterministic guards, no model.

Before: batch 6 bound ``qwen3-coder:30b`` on /v1 (no qualification record, so the fixture failed closed as a config error), batch 5 had NO identity check
and overrode the window (16384) and output budget (4096), and two batch-6 tests wrote their fixture repository into a plain non-git directory (refused by
repository-membership authority before any model call). The live pass per module is the PRD-034 live proof; these tests keep the path from regressing.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import _live_identity as li
import pytest

TESTS = Path(__file__).resolve().parent
BATCH5 = (TESTS / "test_live_prd020_024_batch5.py").read_text(encoding="utf-8")
BATCH6 = (TESTS / "test_live_prd025_029_batch6.py").read_text(encoding="utf-8")


def _fingerprint(**overrides):
    values = {"effective_context_window": 32768, "exact": True, "adapter_version": "kriya-ollama-native/3", "digest": "d" * 64}
    return SimpleNamespace(**{**values, **overrides})


def _assessment(status=li.mq.QUALIFIED):
    return SimpleNamespace(status=status)


def test_the_qualified_native_identity_has_no_problems():
    assert li.identity_problems(_fingerprint(), _assessment()) == []


@pytest.mark.parametrize("fingerprint,assessment,expected", [
    (_fingerprint(effective_context_window=16384), _assessment(), "effective context window 16384 != 32768"),
    (_fingerprint(), _assessment("MISSING"), "developer qualification is MISSING"),
    (_fingerprint(exact=False), _assessment(), "runtime fingerprint is not exact"),
    (_fingerprint(adapter_version="kriya-openai-compat/3"), _assessment(), "runtime adapter 'kriya-openai-compat/3' != 'kriya-ollama-native/3'"),
])
def test_every_identity_deviation_is_a_named_problem(fingerprint, assessment, expected):
    assert any(expected in problem for problem in li.identity_problems(fingerprint, assessment))


def test_an_unidentifiable_endpoint_fails_closed_as_unavailable_never_a_skip(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ConnectionError("endpoint down")
    monkeypatch.setattr(li, "resolve_configured_model_runtime", refuse)
    with pytest.raises(pytest.fail.Exception, match="UNAVAILABLE: the live endpoint"):
        li.verify_identity(li.AppConfig(), "any:model")


def test_a_problem_identity_fails_closed_before_any_product_result(monkeypatch):
    monkeypatch.setattr(li, "resolve_configured_model_runtime", lambda *a, **k: _fingerprint(effective_context_window=8192))
    monkeypatch.setattr(li.mq, "assess", lambda *a, **k: _assessment())
    monkeypatch.setattr(li.mq, "required_capabilities", lambda *a, **k: [])
    monkeypatch.setattr(li, "role_inference_settings", lambda *a, **k: SimpleNamespace(digest="s"))
    with pytest.raises(pytest.fail.Exception, match=r"UNAVAILABLE \(live identity\).*8192 != 32768"):
        li.verify_identity(li.AppConfig(), "any:model")


@pytest.mark.parametrize("source", [BATCH5, BATCH6], ids=["batch5", "batch6"])
def test_the_stock_fixtures_bind_and_verify_the_shared_identity_and_override_no_window_or_budget(source):
    cfg_fixture = source.split("def cfg(", 1)[1].split("\n\n\n", 1)[0]
    assert "bind_primary(" in cfg_fixture and "verify_identity(" in cfg_fixture and "preflight_identity.json" in cfg_fixture
    assert not re.search(r"num_ctx|context_window\s*=|max_tokens\s*=|extra_body\s*=", cfg_fixture), cfg_fixture


def test_every_batch6_fixture_repository_that_the_measured_refusals_hit_is_a_git_baseline():
    """PRD-027 java-shop and PRD-028 ledger repo were written into plain directories (membership_authority.unavailable, model_calls 0)."""
    for marker in ('repo = tmp_path / "java-shop"', "(repo / \"ledger.py\").write_text(source"):
        after = BATCH6.split(marker, 1)[1]
        assert "_git_baseline(repo)" in after[:600], marker


def test_batch5_fixture_repositories_are_git_backed():
    assert '["init", "-q"]' in BATCH5.split("def _repo(", 1)[1].split("\n\n\n", 1)[0] and '"commit", "-qm"' in BATCH5


def test_the_prd022_evidence_records_the_ownership_findings_and_the_branch_that_actually_ran():
    case = BATCH5.split("def test_prd022_duplicate_owner_first_and_second_attempt", 1)[1].split("\ndef test_", 1)[0]
    assert '"ownership_findings": result.get("ownership_findings")' in case and '"branch_executed": branch' in case
    assert "for after in retried:" in case   # the conditional assertion is over a list the evidence names, not a silent no-op
