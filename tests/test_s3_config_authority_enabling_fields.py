"""S3 (OI-99, registry OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001): a repository-controlled kriya.yaml cannot enable
authority-sensitive behaviour without the human authority path (SEC-009 P1 classification, P2 durable approval).

Measured defect: ``autonomy.self_correction_loop_enabled`` was REPOSITORY_SAFE, so a cloned repository's kriya.yaml
that only says ``self_correction_loop_enabled: true`` was honoured with no approval (``kriya authority inspect``: "No
pending security-authority fields"), and in human-in-the-loop mode the controller's owner-recovery self-correction then
ran a model edit loop whose validator compiles the model-written owner files in the plan worktree BEFORE any source
review (the Gap B guard that turns the three attempt-level micro-loops off in that mode is not consulted there).

Characterization FIRST (S1 method): pins of today's governed behaviour (green before and after) and EXPECTED-TO-FLIP pins
written as the corrected behaviour, strict-xfail (AssertionError only) at the characterization commit 9415d53 (all 10
failing on the unfixed bytes with --runxfail); the correction removed the marks.

There is no trusted user-level configuration source in SEC-009 (only the packaged default and the platform floor are
trusted; an explicit --config outside the workspace is repository-equivalent): the positive control for "the operator
can still enable it" is the durable approval path (``kriya authority approve`` / a trust file), exactly as for every
other SECURITY_AUTHORITY field.
"""
import asyncio
import subprocess
from unittest.mock import patch

import pytest
import yaml
from _strict_doubles import strict_config, strict_kernel

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config, resolve_config_state

HITL = "human-in-the-loop"


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _workspace(tmp_path, monkeypatch, data, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.chdir(ws)
    return ws


def _pending():
    return sorted(v.field_path for v in resolve_config_state().violations)


def _denied(config_path=None):
    """The typed SEC-009 refusal's field paths, or None when the configuration loads."""
    try:
        load_config(config_path)
    except ConfigAuthorityError as error:
        return sorted(v.field_path for v in error.violations)
    return None


def _approve():
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    aa.save_approval_artifact(aa.default_local_approval_path(state.workspace_root), artifact)
    return artifact


# =========================================================================== governed behaviour that must NOT change

def test_the_restricting_value_from_the_repository_needs_no_approval(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": False, "web_lookup_auto_approve": False,
                                                    "self_correction_loop_max_turns": 2}})
    assert _pending() == [] and _denied() is None
    cfg = load_config()
    assert cfg.autonomy.self_correction_loop_enabled is False and cfg.autonomy.self_correction_loop_max_turns == 2


def test_a_project_setup_profile_asks_for_exactly_the_same_approvals(tmp_path, monkeypatch):
    """`kriya init` writes self_correction_loop_enabled: false and web_lookup_auto_approve: false; the fields it asks
    the person to approve are unchanged (an existing approval of such a project stays current)."""
    from kriya.application.project_init import recommended_profile

    _workspace(tmp_path, monkeypatch, recommended_profile())
    pending = _pending()
    assert "autonomy.self_correction_loop_enabled" not in pending and "autonomy.web_lookup_auto_approve" not in pending
    assert "autonomy.mode" in pending and "llm.base_url" in pending


@pytest.mark.parametrize("field, value", [("developer_investigation_enabled", True), ("web_lookup_enabled", True),
                                          ("best_of_n_first_attempt", 3), ("run_verification_enabled", False),
                                          ("spec_compliance_enabled", True), ("self_correction_loop_max_turns", 9)])
def test_the_audited_siblings_that_stay_repository_safe_still_load(tmp_path, monkeypatch, field, value):
    _workspace(tmp_path, monkeypatch, {"autonomy": {field: value}})
    assert _pending() == [] and getattr(load_config().autonomy, field) == value


def test_the_operator_approval_path_enables_it(tmp_path, monkeypatch):
    """Positive control: the durable approval (`kriya authority approve`) makes the same repository value effective."""
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    _approve()
    assert _denied() is None and load_config().autonomy.self_correction_loop_enabled is True


def test_a_trust_file_outside_the_workspace_enables_it(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    state = resolve_config_state()
    trust = tmp_path / "ci-trust.json"
    aa.save_approval_artifact(str(trust), aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root))
    assert load_config(trust_file=str(trust)).autonomy.self_correction_loop_enabled is True


def _owner_recovery(mode, tmp_path, enabled=True):
    from test_e11_owner_modules import st

    from kriya.workflow import workflow_controller as wc

    (tmp_path / "pom.xml").write_text("<project/>")
    ran = []

    class Result:
        resolved, modified_files, turns_used = True, {"pom.xml": "x"}, 1

    async def loop(**kwargs):
        ran.append(kwargs["worktree_path"])
        return Result()

    kernel = strict_kernel(strict_config(autonomy={"self_correction_loop_enabled": enabled, "self_correction_loop_max_turns": 2,
                                                   "mode": mode}))
    with patch("kriya.workflow.self_correction.run_self_correction_loop", side_effect=loop):
        result = asyncio.run(wc._attempt_owner_recovery_self_correction(
            kernel=kernel, developer_llm=None, plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
            owner=st("s1", ["pom.xml"]), required_owner_files=["pom.xml"], scope_conflict={"raw_evidence": "ERR"}))
    return result, ran


@pytest.mark.parametrize("mode", ["guardrails", "full-auto", HITL])
def test_a_disabled_loop_never_runs_in_any_mode(tmp_path, mode):
    result, ran = _owner_recovery(mode, tmp_path, enabled=False)
    assert ran == [] and result is None


@pytest.mark.parametrize("mode", ["guardrails", "full-auto"])
def test_an_approved_loop_outside_human_in_the_loop_still_runs(tmp_path, mode):
    result, ran = _owner_recovery(mode, tmp_path)
    assert ran == [str(tmp_path)] and result == {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}


# =========================================================================== EXPECTED TO FLIP (the measured defect)

def test_a_repository_kriya_yaml_cannot_enable_the_self_correction_loop(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    assert _pending() == ["autonomy.self_correction_loop_enabled"]
    assert _denied() == ["autonomy.self_correction_loop_enabled"]


@pytest.mark.parametrize("value", [True, "true", 1, "yes"])
def test_every_enabling_spelling_is_refused(tmp_path, monkeypatch, value):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": value}})
    assert _denied() == ["autonomy.self_correction_loop_enabled"]


def test_authority_inspect_lists_it_as_pending(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    out = CliRunner().invoke(main, ["authority", "inspect"], catch_exceptions=False).output
    assert "autonomy.self_correction_loop_enabled" in out and "security_authority" in out


def test_an_explicit_config_outside_the_workspace_is_not_trusted_either(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, {"llm": {"model": "m"}})
    outside = tmp_path / "operator.yaml"
    outside.write_text(yaml.safe_dump({"autonomy": {"self_correction_loop_enabled": True}}))
    assert ws.exists() and _denied(str(outside)) == ["autonomy.self_correction_loop_enabled"]


def test_an_approval_does_not_survive_a_change_to_the_enabling_value(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": False, "mode": "guardrails"}})
    _approve()                                                   # approves autonomy.mode; the False needs nothing
    assert _denied() is None
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"self_correction_loop_enabled": True, "mode": "guardrails"}}))
    denied = _denied()
    assert denied is not None and "autonomy.self_correction_loop_enabled" in denied


def test_a_repository_kriya_yaml_cannot_remove_the_live_lookup_confirmation(tmp_path, monkeypatch):
    """Sibling audit: web_lookup_auto_approve: true removes the per-query human confirmation of outbound egress."""
    _workspace(tmp_path, monkeypatch, {"autonomy": {"web_lookup_auto_approve": True}})
    assert _denied() == ["autonomy.web_lookup_auto_approve"]


def test_in_human_in_the_loop_owner_recovery_never_runs_the_unreviewed_loop(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="kriya.workflow.workflow_controller"):
        result, ran = _owner_recovery(HITL, tmp_path)
    assert ran == [] and result is None                          # falls through to the governed Developer path
    assert any("OWNER_RECOVERY_SELF_CORRECTION_HELD_FOR_SOURCE_REVIEW" in r.getMessage() for r in caplog.records)


# =========================================================================== the S3 audit (correction)

from kriya.config import authority  # noqa: E402 - module-level after the characterization pins on purpose

# Every REPOSITORY_SAFE field, deliberately, with the S3 audit's reason (the owner rule: a field that ENABLES an
# edit / execution / egress / authority behaviour is not repository-safe at its enabling value). A new safe field
# fails here until it is argued.
AUDITED_REPOSITORY_SAFE = {
    "autonomy.acquisition_cpu_seconds": "resource bound, enables nothing (asymmetry with mcp_lifecycle.*: ledger)",
    "autonomy.acquisition_memory_mb": "resource bound, enables nothing (ledger, as above)",
    "autonomy.auto_index_missing_dependency_graph": "index + embedding calls to the already-authorized endpoint; the conventions-Skill write is J1B/OI-120",
    "autonomy.best_of_n_first_attempt": "each candidate goes through run_attempt and its Gap B deferral (TRACED)",
    "autonomy.brownfield_full_regression_baseline_policy": "closed enum over EXISTING tests (VAL-001 G1-R3 argument)",
    "autonomy.developer_investigation_enabled": "read-only through AuthorizedFileReader",
    "autonomy.developer_investigation_max_turns": "bound of a read-only loop",
    "autonomy.generation_gate_reserve_seconds": "time budget",
    "autonomy.generation_seconds_per_file_estimate": "time budget",
    "autonomy.generation_time_budget_seconds": "time budget",
    "autonomy.max_consecutive_no_progress_attempts": "stop rule",
    "autonomy.run_verification_enabled": "default True: a repository can only restrict",
    "autonomy.run_verification_timeout_seconds": "time bound",
    "autonomy.sandbox_cpu_seconds": "resource bound (ledger, as above)",
    "autonomy.sandbox_memory_mb": "resource bound (ledger, as above)",
    "autonomy.self_correction_loop_max_turns": "bound; matters only once enabling the loop is authorized",
    "autonomy.shell_command_timeout_seconds": "time bound (ledger, as above)",
    "autonomy.spec_compliance_enabled": "a model review; edits/executes nothing",
    "autonomy.web_lookup_enabled": "search.base_url is SECURITY_AUTHORITY and each query is confirmed by a person",
    "embedding.model": "model choice", "engineering_triage.enabled": "classification only",
    "knowledge.check_enabled": "default True: a repository can only restrict", "knowledge.offline_mode": "restricts",
    "knowledge.release_cache_ttl_days": "cache age", "knowledge.training_cutoff": "a date",
    "llm.api_key": "sent only to the authorized endpoint", "llm.capabilities": "model capability declaration",
    "llm.context_window": "bounded by llm.context_policy (SECURITY_AUTHORITY)", "llm.extra_body": "request body to the authorized endpoint",
    "llm.knowledge_cutoff": "a date", "llm.knowledge_cutoff_confidence": "a label", "llm.max_tokens": "sampling",
    "llm.model": "model choice", "llm.planner_max_tokens": "sampling", "llm.provider": "documentation-only",
    "llm.reasoning": "sampling", "llm.retry_temperature": "sampling", "llm.reviewer_temperature": "sampling",
    "llm.temperature": "sampling", "logging.file_enabled": "turning logs off removes a capability",
    "logging.level": "verbosity", "logging.run_file_enabled": "as file_enabled",
    "process_profiles.enabled": "only adds approvals", "process_profiles.enforce_approval": "only adds approvals",
    "process_profiles.enforce_context_depth": "only adds checks", "process_profiles.enforce_verification_depth": "only adds checks",
    "routing.ask_margin": "REPL routing threshold", "routing.embed_model": "model choice", "routing.enabled": "REPL routing",
    "routing.reject_threshold": "REPL routing threshold", "search.public_terms": "query vocabulary (sanitized)",
    "search.top_k": "result count", "skills.load_cwd": "prompt text, fenced as untrusted", "skills.load_global": "prompt text",
}


def test_every_repository_safe_field_was_audited():
    assert sorted(f"{t}.{leaf}" for t, leaf in authority._REPOSITORY_SAFE_FIELDS) == sorted(AUDITED_REPOSITORY_SAFE)


@pytest.mark.parametrize("field", ["self_correction_loop_enabled", "web_lookup_auto_approve"])
def test_a_value_gated_field_is_safe_only_at_its_restricting_bool(field):
    gate = authority.value_gated_field_classification
    assert gate("autonomy", field, False) is authority.FieldClassification.REPOSITORY_SAFE
    for enabling in (True, "false", 0, None, "no", [], "true", 1):
        assert gate("autonomy", field, enabling) is authority.FieldClassification.SECURITY_AUTHORITY, enabling
    assert authority.classify_field("autonomy", field) is authority.FieldClassification.SECURITY_AUTHORITY  # static: closed
    assert authority.is_known_field("autonomy", field)


def test_a_field_that_is_not_value_gated_keeps_its_static_classification():
    assert authority.value_gated_field_classification("autonomy", "developer_investigation_enabled", True) is None
    assert authority.value_gated_field_classification("llm", "self_correction_loop_enabled", True) is None
    assert authority.value_gated_field_classification(None, "self_correction_loop_enabled", False) is None


def test_the_forced_sensitive_paths_baseline_is_untouched():
    assert authority.classify_field("autonomy", "sensitive_paths") is authority.FieldClassification.SECURITY_AUTHORITY


def test_the_hold_is_the_authority_decision_not_a_local_mode_comparison(tmp_path):
    """Patching AUTHORITY's decision changes the outcome: the owner-recovery code asks it, it does not compare strings."""
    with patch("kriya.policy.approval.defer_execution_until_source_review", return_value=False):
        result, ran = _owner_recovery(HITL, tmp_path)
    assert ran == [str(tmp_path)] and result is not None
