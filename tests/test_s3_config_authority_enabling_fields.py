"""S3 (OI-99, registry OWNER-RECOVERY-SELF-CORRECTION-REVIEW-001): a repository-controlled kriya.yaml cannot enable
authority-sensitive behaviour without the human authority path (SEC-009 P1 classification, P2 durable approval).

Measured defect: ``autonomy.self_correction_loop_enabled`` was REPOSITORY_SAFE, so a cloned repository's kriya.yaml
that only says ``self_correction_loop_enabled: true`` was honoured with no approval (``kriya authority inspect``: "No
pending security-authority fields"), and in human-in-the-loop mode the controller's owner-recovery self-correction then
ran a model edit loop whose validator compiles the model-written owner files in the plan worktree BEFORE any source
review (the Gap B guard that turns the three attempt-level micro-loops off in that mode is not consulted there).

Characterization FIRST (S1 method): pins of today's governed behaviour (green before and after) and EXPECTED-TO-FLIP pins
written as the corrected behaviour, strict-xfail (AssertionError only) at the characterization commit.

There is no trusted user-level configuration source in SEC-009 (only the packaged default and the platform floor are
trusted; an explicit --config outside the workspace is repository-equivalent): the positive control for "the operator
can still enable it" is the durable approval path (``kriya authority approve`` / a trust file), exactly as for every
other SECURITY_AUTHORITY field.
"""
import asyncio
import os
import subprocess
from unittest.mock import patch

import pytest
import yaml
from _strict_doubles import strict_config, strict_kernel

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config, resolve_config_state

FLIP = pytest.mark.xfail(strict=True, raises=AssertionError,
                         reason="S3 expected-to-flip: the measured authority gap (removed by the correction commit)")
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


def _owner_recovery(mode, tmp_path):
    from test_e11_owner_modules import st

    from kriya.workflow import workflow_controller as wc

    (tmp_path / "pom.xml").write_text("<project/>")
    ran = []

    class Result:
        resolved, modified_files, turns_used = True, {"pom.xml": "x"}, 1

    async def loop(**kwargs):
        ran.append(kwargs["worktree_path"])
        return Result()

    kernel = strict_kernel(strict_config(autonomy={"self_correction_loop_enabled": True, "self_correction_loop_max_turns": 2,
                                                   "mode": mode}))
    with patch("kriya.workflow.self_correction.run_self_correction_loop", side_effect=loop):
        result = asyncio.run(wc._attempt_owner_recovery_self_correction(
            kernel=kernel, developer_llm=None, plan_workspace_path=str(tmp_path), workspace_path=str(tmp_path),
            owner=st("s1", ["pom.xml"]), required_owner_files=["pom.xml"], scope_conflict={"raw_evidence": "ERR"}))
    return result, ran


@pytest.mark.parametrize("mode", ["guardrails", "full-auto"])
def test_an_approved_loop_outside_human_in_the_loop_still_runs(tmp_path, mode):
    result, ran = _owner_recovery(mode, tmp_path)
    assert ran == [str(tmp_path)] and result == {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}


# =========================================================================== EXPECTED TO FLIP (the measured defect)

@FLIP
def test_a_repository_kriya_yaml_cannot_enable_the_self_correction_loop(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    assert _pending() == ["autonomy.self_correction_loop_enabled"]
    assert _denied() == ["autonomy.self_correction_loop_enabled"]


@FLIP
@pytest.mark.parametrize("value", [True, "true", 1, "yes"])
def test_every_enabling_spelling_is_refused(tmp_path, monkeypatch, value):
    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": value}})
    assert _denied() == ["autonomy.self_correction_loop_enabled"]


@FLIP
def test_authority_inspect_lists_it_as_pending(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": True}})
    out = CliRunner().invoke(main, ["authority", "inspect"], catch_exceptions=False).output
    assert "autonomy.self_correction_loop_enabled" in out and "security_authority" in out


@FLIP
def test_an_explicit_config_outside_the_workspace_is_not_trusted_either(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, {"llm": {"model": "m"}})
    outside = tmp_path / "operator.yaml"
    outside.write_text(yaml.safe_dump({"autonomy": {"self_correction_loop_enabled": True}}))
    assert ws.exists() and _denied(str(outside)) == ["autonomy.self_correction_loop_enabled"]


@FLIP
def test_an_approval_does_not_survive_a_change_to_the_enabling_value(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, {"autonomy": {"self_correction_loop_enabled": False, "mode": "guardrails"}})
    _approve()
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"self_correction_loop_enabled": True, "mode": "guardrails"}}))
    assert _denied() == ["autonomy.self_correction_loop_enabled"]


@FLIP
def test_a_repository_kriya_yaml_cannot_remove_the_live_lookup_confirmation(tmp_path, monkeypatch):
    """Sibling audit: web_lookup_auto_approve: true removes the per-query human confirmation of outbound egress."""
    _workspace(tmp_path, monkeypatch, {"autonomy": {"web_lookup_auto_approve": True}})
    assert _denied() == ["autonomy.web_lookup_auto_approve"]


@FLIP
def test_in_human_in_the_loop_owner_recovery_never_runs_the_unreviewed_loop(tmp_path, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="kriya.workflow.workflow_controller"):
        result, ran = _owner_recovery(HITL, tmp_path)
    assert ran == [] and result is None                          # falls through to the governed Developer path
    assert any("OWNER_RECOVERY_SELF_CORRECTION_HELD_FOR_SOURCE_REVIEW" in r.getMessage() for r in caplog.records)
