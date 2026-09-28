"""PRD-032 family E: attacks on the PRD-031A static-analysis gate.

Every case runs the real direct pipeline with the gate enabled and required.
The deterministic cases use the test fake provider (tests/_fake_static_analysis.py:
a line containing BAD_HIGH is a high-severity finding). The attack is
injected between the gate and the commit guard, or planted in the trusted
waiver store. E08 runs the real pinned Semgrep 1.178.0 (live_static_analysis).
A missing scanner is a FAILURE there, never a skip.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    ChaosRuntime,
    RuntimeRegistration,
    assert_no_false_pass,
    audit_run_records,
    benign_roles,
    chaos,
    chaos_config,
    chaos_engine,
    git_workspace,
    inject_after_static_analysis_gate,
    run_direct,
    static_analysis_config,
    typed_failure,
)
from _fake_static_analysis import FAKE, FakeKnobs, FakeRegistration

from kriya.static_analysis import model as sa_model
from kriya.static_analysis.model import ScanScope
from kriya.static_analysis.registry import _REGISTRY
from kriya.static_analysis.waivers import new_waiver, waiver_store_path, write_waiver
from kriya.workflow.verification_binding import VERIFIED_CANDIDATE_EVIDENCE_STALE

GOAL = "add sub to calc.py"
RISKY = CALC_WITH_SUB + "\nRISK = 'BAD_HIGH'\n"


@pytest.fixture(autouse=True)
def _trusted_waiver_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "trusted" / "waivers"))


def _run(chaos_case, tmp_path, developer_text, *, knobs=None, inject=None, monkeypatch=None,
         files=None, before_arm=None, **static):
    workspace = git_workspace(tmp_path, files or {"calc.py": CALC})
    if before_arm is not None:
        before_arm(workspace)
    runtime = ChaosRuntime(lambda role, request: developer_text if role == "developer" else benign_roles(role, request))
    with FakeRegistration(knobs or FakeKnobs()) as fake:
        if inject is not None:
            inject_after_static_analysis_gate(monkeypatch, lambda state: inject(state, workspace, fake))
        chaos_case.arm()
        with RuntimeRegistration(runtime):
            result = run_direct(chaos_engine(static_analysis_config(**static)), GOAL, workspace)
    return workspace, result, runtime


def _not_committed(chaos_case, workspace, result, *, allowed=()):
    chaos_case.assert_tree(allowed=allowed)
    assert_no_false_pass(result)
    assert result.get("accepted_risk") is not True
    audit = audit_run_records(workspace)
    return audit


def _commit_refused(chaos_case, workspace, result, runtime, reason_code, *, allowed=()):
    audit = _not_committed(chaos_case, workspace, result, allowed=allowed)
    assert typed_failure(result) == "workspace_commit_failed"
    assert result["workspace_commit_failure"]["reason_code"] == reason_code
    assert runtime.count("developer") == 1
    return audit


@chaos("E01")
def test_commit_evidence_dropped_after_the_gate_is_refused(chaos_case, tmp_path, monkeypatch):
    def drop(state, workspace, fake):
        del workspace, fake
        state.static_analysis_result = None

    workspace, result, runtime = _run(chaos_case, tmp_path, CALC_WITH_SUB, inject=drop, monkeypatch=monkeypatch)
    audit = _commit_refused(chaos_case, workspace, result, runtime, sa_model.STATIC_ANALYSIS_EVIDENCE_MISSING)
    assert Path(workspace, "calc.py").read_text() == CALC
    chaos_case.observe(sa_model.STATIC_ANALYSIS_EVIDENCE_MISSING, **audit.evidence())


def _worktree(workspace):
    return Path(workspace, ".kriya", "worktree")


@chaos("E02")
def test_candidate_bytes_changed_after_the_scan_are_refused(chaos_case, tmp_path, monkeypatch):
    def swap_candidate(state, workspace, fake):
        del state, fake
        _worktree(workspace).joinpath("calc.py").write_text(RISKY)

    workspace, result, runtime = _run(chaos_case, tmp_path, CALC_WITH_SUB, inject=swap_candidate, monkeypatch=monkeypatch)
    # The verification binding is checked before the static-analysis guard
    # (CANDIDATE-VERIFIED-DIGEST-BINDING-001); either refusal leaves the
    # workspace unchanged.
    audit = _commit_refused(chaos_case, workspace, result, runtime, VERIFIED_CANDIDATE_EVIDENCE_STALE)
    assert Path(workspace, "calc.py").read_text() == CALC
    chaos_case.observe(VERIFIED_CANDIDATE_EVIDENCE_STALE, stale="candidate batch", **audit.evidence())


@chaos("E03")
def test_an_in_scope_file_edited_after_the_scan_is_refused(chaos_case, tmp_path, monkeypatch):
    edit = "HELPER = 2  # edited by someone else after the scan\n"

    def edit_scope(state, workspace, fake):
        del state, fake
        Path(workspace, "helper.py").write_text(edit)

    workspace, result, runtime = _run(
        chaos_case, tmp_path, CALC_WITH_SUB, inject=edit_scope, monkeypatch=monkeypatch,
        files={"calc.py": CALC, "helper.py": "HELPER = 1\n"},
        knobs=FakeKnobs(minimum_scope=ScanScope.MODULE, cross_file=True),
    )
    audit = _commit_refused(chaos_case, workspace, result, runtime, sa_model.STATIC_ANALYSIS_EVIDENCE_STALE,
                            allowed={"ws/helper.py"})
    assert Path(workspace, "helper.py").read_text() == edit and Path(workspace, "calc.py").read_text() == CALC
    chaos_case.observe(sa_model.STATIC_ANALYSIS_EVIDENCE_STALE, stale="base scope", **audit.evidence())


@chaos("E04")
def test_a_scanner_runtime_or_rule_pack_change_after_the_scan_is_refused(chaos_case, tmp_path, monkeypatch):
    def upgrade(state, workspace, fake):
        del state, workspace
        fake.knobs.runtime_marker = "v2"  # the commit guard re-probes the provider

    workspace, result, runtime = _run(chaos_case, tmp_path, CALC_WITH_SUB, inject=upgrade, monkeypatch=monkeypatch)
    audit = _commit_refused(chaos_case, workspace, result, runtime, sa_model.STATIC_ANALYSIS_EVIDENCE_STALE)
    chaos_case.observe(sa_model.STATIC_ANALYSIS_EVIDENCE_STALE, stale="provider runtime", **audit.evidence())


def _waive(workspace, **overrides):
    fields = dict(workspace_root=str(workspace), waiver_id="SAW-CHAOS", provider=FAKE, rule_id="fake:bad-high",
                  paths=["calc.py"], reason="accepted legacy risk", owner="security", max_severity="high",
                  classifications=["introduced"],
                  expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat())
    fields.update(overrides)
    record = new_waiver(**fields)
    path = waiver_store_path(None, str(workspace))
    write_waiver(path, str(workspace), record)
    return Path(path)


def _blocked_not_accepted(chaos_case, workspace, result):
    audit = _not_committed(chaos_case, workspace, result)
    static = result["static_analysis"]
    assert typed_failure(result) == "static_analysis_blocked"
    assert static["outcome"] == "BLOCKED" and result["accepted_risks"] == []
    assert Path(workspace, "calc.py").read_text() == CALC
    return audit, static


def test_a_valid_waiver_is_accepted_risk_the_control_for_e05_to_e07(tmp_path, monkeypatch):
    """Control case: the same candidate and store without the attack commit
    as ACCEPTED_RISK (never PASS). Without it, E05-E07 could pass vacuously."""
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "trusted" / "waivers"))
    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    _waive(workspace)
    runtime = ChaosRuntime(lambda role, request: RISKY if role == "developer" else benign_roles(role, request))
    with FakeRegistration(), RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(static_analysis_config()), GOAL, workspace)
    assert result["quality_gates_passed"] is True
    assert result["static_analysis"]["outcome"] == "ACCEPTED_RISK" and result["accepted_risks"] == ["SAW-CHAOS"]


@chaos("E05")
def test_a_tampered_waiver_store_never_accepts_risk(chaos_case, tmp_path):
    def plant(workspace):
        store = _waive(workspace)
        payload = json.loads(store.read_text())
        payload["waivers"][0]["max_severity"] = "critical"
        store.write_text(json.dumps(payload))

    workspace, result, _ = _run(chaos_case, tmp_path, RISKY, before_arm=plant)
    audit, static = _blocked_not_accepted(chaos_case, workspace, result)
    assert sa_model.WAIVER_STORE_INVALID in static["reason_codes"]
    chaos_case.observe("static_analysis_blocked", reason=sa_model.WAIVER_STORE_INVALID, **audit.evidence())


@chaos("E06")
def test_an_expired_waiver_never_accepts_risk(chaos_case, tmp_path):
    workspace, result, _ = _run(chaos_case, tmp_path, RISKY,
                                before_arm=lambda ws: _waive(ws, expires_at="2000-01-01T00:00:00Z"))
    audit, _ = _blocked_not_accepted(chaos_case, workspace, result)
    chaos_case.observe("static_analysis_blocked", waiver="expired", **audit.evidence())


@chaos("E07")
def test_a_waiver_for_another_rule_never_accepts_risk(chaos_case, tmp_path):
    workspace, result, _ = _run(chaos_case, tmp_path, RISKY,
                                before_arm=lambda ws: _waive(ws, rule_id="fake:bad-critical"))
    audit, _ = _blocked_not_accepted(chaos_case, workspace, result)
    chaos_case.observe("static_analysis_blocked", waiver="other rule", **audit.evidence())


@chaos("E09")
def test_a_provider_that_disappears_after_the_scan_refuses_the_commit(chaos_case, tmp_path, monkeypatch):
    def vanish(state, workspace, fake):
        del state, workspace, fake
        _REGISTRY.pop(FAKE, None)

    workspace, result, runtime = _run(chaos_case, tmp_path, CALC_WITH_SUB, inject=vanish, monkeypatch=monkeypatch)
    audit = _not_committed(chaos_case, workspace, result)
    assert typed_failure(result) == "workspace_commit_failed" and runtime.count("developer") == 1
    reason = result["workspace_commit_failure"]["reason_code"]
    assert reason in sa_model.REASON_CODES
    assert Path(workspace, "calc.py").read_text() == CALC
    chaos_case.observe(reason, **audit.evidence())


@chaos("E10")
def test_an_oversized_required_target_is_unknown_and_blocks(chaos_case, tmp_path):
    workspace, result, _ = _run(chaos_case, tmp_path, CALC_WITH_SUB, max_target_bytes=16)
    audit = _not_committed(chaos_case, workspace, result)
    assert typed_failure(result) == "static_analysis_unknown"
    assert sa_model.TARGET_OVERSIZED in result["static_analysis"]["reason_codes"]
    chaos_case.observe("static_analysis_unknown", reason=sa_model.TARGET_OVERSIZED, **audit.evidence())


# --- E08: real pinned Semgrep --------------------------------------------------------

PINNED_SEMGREP = "1.178.0"
RULES = Path(__file__).parent / "fixtures" / "static_analysis" / "semgrep" / PINNED_SEMGREP / "rules"


def _require_pinned_semgrep():
    executable = shutil.which("semgrep")
    if executable is None:
        pytest.fail(f"UNAVAILABLE: semgrep {PINNED_SEMGREP} is not on PATH (required by this tier, never skipped)")
    out = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=120,
                         env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
                              "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"})
    version = (out.stdout.strip().splitlines() or ["<none>"])[-1].strip()
    if version != PINNED_SEMGREP:
        pytest.fail(f"UNAVAILABLE: semgrep is {version}; this tier is pinned to {PINNED_SEMGREP} exactly")


@chaos("E08")
def test_a_nosemgrep_suppression_in_the_candidate_never_hides_a_finding(chaos_case, tmp_path):
    _require_pinned_semgrep()
    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    suppressed = CALC_WITH_SUB + "\n\ndef run(code):\n    return eval(code)  # nosemgrep\n"
    runtime = ChaosRuntime(lambda role, request: suppressed if role == "developer" else benign_roles(role, request))
    cfg = chaos_config(sections={"static_analysis": {
        "enabled": True, "provider": "semgrep", "requirement": "required",
        "providers": {"semgrep": {"version": PINNED_SEMGREP, "rule_packs": [str(RULES)]}},
        # The fixture's python-eval rule is WARNING (medium); the operator's
        # policy blocks new medium findings, so a suppressed one must block.
        "policy": {"introduced": {"medium": "block"}},
    }})
    chaos_case.identity["static_analysis"] = f"semgrep {PINNED_SEMGREP} (host)"
    chaos_case.arm()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(cfg), GOAL, workspace)
    audit = _not_committed(chaos_case, workspace, result)
    static = result["static_analysis"]
    assert typed_failure(result) == "static_analysis_blocked", static
    assert sa_model.NEW_FINDING_BLOCKED in static["reason_codes"]
    # --disable-nosem: the suppressed finding is reported, not hidden.
    assert static["summary"]["introduced"] == 1 and static["summary"]["blocked"] == 1
    assert Path(workspace, "calc.py").read_text() == CALC
    chaos_case.observe("static_analysis_blocked", reason=sa_model.NEW_FINDING_BLOCKED,
                       introduced=static["summary"]["introduced"], **audit.evidence())
