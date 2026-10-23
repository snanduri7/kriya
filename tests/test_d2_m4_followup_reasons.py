"""D2 M4 follow-up (owner decision 2026-10-23): the ownership reason survives to the public commit-state refusal, and recovery is not offered for it.

An evidence file or run record that carries no workspace stamp (or another workspace's) is refused with WORKSPACE_OWNERSHIP_MISSING /
WORKSPACE_OWNERSHIP_MISMATCH, not UNCERTAIN_COMMIT_STATE / RUN_RECORD_UNREADABLE: ownership was never established, so ``kriya runs recover`` cannot settle
it and is not the advice. UNCERTAIN_COMMIT_STATE stays for owned evidence whose settlement really is uncertain. The same reason code reaches the
``generate --from-milestones`` failure result, with no traceback.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kriya.cli_output import GenerateOutput
from kriya.context.workspace_identity import (
    OWNERSHIP_REMEDIATION,
    WORKSPACE_OWNERSHIP_MISMATCH,
    WORKSPACE_OWNERSHIP_MISSING,
    WorkspaceOwnershipMissing,
    workspace_identity,
)
from kriya.control import persistence as P
from kriya.control.commit_state import RECOVERY_COMMAND, UncertainWorkspaceStateError, assess_workspace_commit_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.run_record import RunRecord
from kriya.repository import commit as C
from kriya.workflow import milestones as M
from tests.test_d2_m4_ownership import _evidence, _mutate

MODES = [("STRIPPED", WORKSPACE_OWNERSHIP_MISSING), ("FOREIGN", WORKSPACE_OWNERSHIP_MISMATCH)]
SAFE_WORDING = "outside the active Kriya control-state location"


@pytest.fixture(name="ws")
def _ws(tmp_path):
    path = tmp_path / "W"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def _owned_in_progress_evidence(ws: Path) -> str:
    """Real evidence (stamped by the real writer) whose settlement is genuinely uncertain: the state is rewritten, the stamp is kept."""
    path = _evidence(ws)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["state"] = "in_progress"
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_record(ws: Path) -> str:
    P.save_run_record(str(ws), RunRecord.new("runA", workspace_identity(str(ws)), None, None), expected_revision=None)
    return P.run_record_path(str(ws), "runA")


@pytest.mark.parametrize(("mode", "code"), MODES)
def test_ownership_refused_commit_evidence_keeps_its_reason_and_recovery_is_not_offered(ws, mode, code):
    _mutate(_evidence(ws), "json", mode)
    assessment = assess_workspace_commit_state(str(ws))
    assert not assessment.safe
    assert assessment.reason_codes == [code]  # never UNCERTAIN_COMMIT_STATE: the evidence is not "uncertain", it is not provably ours
    assert assessment.recoverable is False
    text = assessment.describe()
    assert RECOVERY_COMMAND not in text and SAFE_WORDING in text and "do not delete" in text
    payload = assessment.to_payload(run_id="r1")
    assert payload["status"] == "needs_review" and payload["reason_codes"] == [code]
    assert payload["recovery_command"] is None and payload["uncertain_commit_ids"] == []


def test_owned_evidence_that_is_genuinely_uncertain_stays_uncertain_and_recoverable(ws):
    _owned_in_progress_evidence(ws)
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.reason_codes == ["UNCERTAIN_COMMIT_STATE"] and assessment.uncertain_commit_ids == ("tx1",)
    assert assessment.recoverable is True and RECOVERY_COMMAND in assessment.describe()
    assert assessment.to_payload()["recovery_command"] == RECOVERY_COMMAND


def test_evidence_that_is_not_json_is_still_uncertain_not_an_ownership_question(ws):
    Path(_evidence(ws)).write_text("{not json", encoding="utf-8")
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.reason_codes == ["UNCERTAIN_COMMIT_STATE"] and assessment.recoverable is True
    assert assessment.to_payload()["recovery_command"] == RECOVERY_COMMAND


@pytest.mark.parametrize(("mode", "code"), MODES)
def test_ownership_refused_run_record_keeps_its_reason(ws, mode, code):
    path = _run_record(ws)
    _mutate(path, "json", mode)
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.reason_codes == [code] and "RUN_RECORD_UNREADABLE" not in assessment.reason_codes
    assert assessment.recoverable is False and RECOVERY_COMMAND not in assessment.describe()
    assert [item[0] for item in assessment.unreadable_run_records] == [path]  # still reported as unreadable: nothing is hidden
    payload = assessment.to_payload()
    assert payload["ownership_findings"] == [{"path": path, "reason_code": code}] and payload["recovery_command"] is None
    assert OWNERSHIP_REMEDIATION in assessment.describe()


def test_a_run_record_that_is_corrupt_stays_run_record_unreadable(ws):
    Path(_run_record(ws)).write_text("{not json", encoding="utf-8")
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.reason_codes == ["RUN_RECORD_UNREADABLE"] and assessment.recoverable is True
    assert "ownership_findings" not in assessment.to_payload()  # additive key: absent when there is nothing ownership-related


def test_a_mixed_refusal_names_both_and_offers_recovery_only_for_what_it_can_settle(ws):
    _owned_in_progress_evidence(ws)
    record = _run_record(ws)
    _mutate(record, "json", "STRIPPED")
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.reason_codes == ["UNCERTAIN_COMMIT_STATE", WORKSPACE_OWNERSHIP_MISSING]
    assert assessment.recoverable is True and assessment.to_payload()["recovery_command"] == RECOVERY_COMMAND
    text = assessment.describe()
    assert RECOVERY_COMMAND in text and "settles only the state that is provably this workspace's" in text and OWNERSHIP_REMEDIATION in text


@pytest.mark.parametrize(("mode", "code"), MODES)
def test_the_public_mutating_run_refusal_carries_the_ownership_reason(ws, mode, code):
    _mutate(_evidence(ws), "json", mode)
    with pytest.raises(UncertainWorkspaceStateError) as refusal, begin_mutating_run(str(ws)):
        pytest.fail("a mutating run must be refused before it starts")
    assert refusal.value.assessment.reason_codes == [code]
    assert RECOVERY_COMMAND not in str(refusal.value) and SAFE_WORDING in str(refusal.value)


def test_the_commit_mechanism_still_fails_closed_on_ownerless_evidence_with_the_reason_attached(ws):
    _mutate(_evidence(ws), "json", "STRIPPED")
    target = ws / "b.txt"
    writes = [C.StagedFileWrite(str(target), "y\n", str(target), C.content_revision(""), expected_base_exists=False)]
    with pytest.raises(C.UncertainCommitError) as refused:
        C.commit_revision_grounded_batch(writes, workspace_path=str(ws), transaction_id="tx2")
    assert refused.value.ownership_reason_code == WORKSPACE_OWNERSHIP_MISSING
    assert not target.exists()


def test_the_failure_result_of_an_ownership_refusal_carries_the_reason_code_and_no_other_failure_does():
    owned = GenerateOutput(True)
    owned.fail_with(WorkspaceOwnershipMissing("no stamp"))
    assert owned.result["reason_codes"] == [WORKSPACE_OWNERSHIP_MISSING] and owned.result["failure_type"] == "WorkspaceOwnershipMissing"
    other = GenerateOutput(True)
    other.fail_with(RuntimeError("boom"))
    assert "reason_codes" not in other.result


def _cli_plan_with_ownerless_sidecar(tmp_path: Path, ws: Path) -> tuple:
    plan = {"group_id": "grp-own", "original_goal": "Add mul to calc.py.", "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": []}]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    M.save_milestone_run_state(str(ws), M.MilestoneRunState.from_dict(plan))
    sidecar = Path(M._sidecar_path(str(ws), "grp-own"))
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload.pop("_workspace")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    return plan_path, sidecar


@pytest.mark.parametrize("json_output", [True, False])
def test_generate_from_milestones_with_an_ownerless_sidecar_is_a_typed_refusal_without_a_traceback(tmp_path, ws, json_output):
    """The real CLI in a real process: the sidecar load precedes the controller, so the refusal must be shaped at the CLI boundary."""
    plan_path, sidecar = _cli_plan_with_ownerless_sidecar(tmp_path, ws)
    before = sidecar.read_bytes()
    argv = [sys.executable, "-m", "kriya.cli", "generate", "--from-milestones", str(plan_path), "-y"] + (["--json"] if json_output else [])
    proc = subprocess.run(argv, cwd=ws, capture_output=True, text=True, timeout=300, env=dict(os.environ))
    assert proc.returncode == 1
    assert "Traceback (most recent call last)" not in proc.stdout + proc.stderr
    if json_output:
        result = json.loads(proc.stdout)
        assert result["status"] == "failed" and result["quality_gates_passed"] is False
        assert result["failure_type"] == "WorkspaceOwnershipMissing" and result["reason_codes"] == [WORKSPACE_OWNERSHIP_MISSING]
        assert SAFE_WORDING in result["error"] and RECOVERY_COMMAND not in result["error"]
    else:
        assert "WorkspaceOwnershipMissing" in proc.stdout and SAFE_WORDING in proc.stdout
    assert sidecar.read_bytes() == before  # refused, never adopted or stamped



def test_an_ownership_refused_launch_record_carries_its_reason_code(ws):
    """The third UnreadableRunRecordError raise site (load_launch_record): the code travels with the error like the run-record and evidence sites."""
    P.save_launch_record(str(ws), "20261023T120000-abcdef12", {"phase": "RUNNING", "pending_approval": None})
    path = P.launch_record_path(str(ws), "20261023T120000-abcdef12")
    _mutate(path, "json", "FOREIGN")
    with pytest.raises(P.UnreadableRunRecordError) as unreadable:
        P.load_launch_record(str(ws), "20261023T120000-abcdef12")
    assert unreadable.value.reason_code == WORKSPACE_OWNERSHIP_MISMATCH
