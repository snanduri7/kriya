"""D2 M4 (owner decision 2026-10-23): the ownership stamp is mandatory and nothing adopts an unstamped record.

Every writer emits the stamp, every authority-bearing reader requires it (missing -> WORKSPACE_OWNERSHIP_MISSING, another workspace's ->
WORKSPACE_OWNERSHIP_MISMATCH), commit evidence is ownership-checked too, no overwrite re-stamps a document, and the resume-time migration is gone.
The stamp is identity hygiene (a hash of the workspace path), not authentication: these tests prove what arrives from elsewhere is refused, not that
a process that can write the stamp is stopped.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import pytest

from kriya.context import workspace_identity as wid
from kriya.context.workspace_identity import (
    OWNERSHIP_REFUSALS,
    WORKSPACE_OWNERSHIP_MISMATCH,
    WORKSPACE_OWNERSHIP_MISSING,
    WorkspaceOwnershipError,
    WorkspaceOwnershipMissing,
    legacy_workspace_identity,
    validate_ledger_line_ownership,
    validate_ownership,
    workspace_identity,
)
from kriya.control import persistence as P
from kriya.control.artifacts import ArtifactRegistry
from kriya.control.checkpoint import checkpoint_path, load_checkpoint, save_checkpoint
from kriya.control.contracts import ContractRegistry
from kriya.control.decisions import DecisionLedger, decision_ledger_path, load_decision_ledger
from kriya.control.ownership_audit import audit_control_ownership, refusal_fields
from kriya.control.run_record import RunRecord
from kriya.control.state import ControlState
from kriya.repository import commit as C
from kriya.workflow import milestones as M

FOREIGN = "f" * 64


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)


@pytest.fixture(name="ws")
def _ws(tmp_path):
    path = tmp_path / "W"
    path.mkdir()
    _git_init(path)
    return path


def _evidence(ws: Path) -> str:
    target = ws / "a.txt"
    writes = [C.StagedFileWrite(str(target), "x\n", str(target), C.content_revision(""), expected_base_exists=False)]
    C.commit_revision_grounded_batch(writes, workspace_path=str(ws), transaction_id="tx1")
    return os.path.join(C.commit_evidence_dir(str(ws)), "tx1.json")


def _write_everything(ws: Path) -> Dict[str, Tuple[str, str, Callable[[], Any]]]:
    """Every family, written by Kriya's REAL writers: name -> (path, kind, the reader that must accept/refuse it)."""
    w = str(ws)
    identity = workspace_identity(w)
    P.save_run_record(w, RunRecord.new("runA", identity, None, None), expected_revision=None)
    P.save_control_state(w, ControlState(schema_version=1, run_id="runA", subtask_states={"t1": "completed"}))
    P.save_contract_registry(w, ContractRegistry())
    P.save_artifact_registry(w, ArtifactRegistry())
    launch = "20261023T120000-abcdef12"
    P.save_launch_record(w, launch, {"workspace": {"identity": identity}, "phase": "RUNNING", "pending_approval": None})
    P.save_approved_plan(w, "plan1", {"plan": "x"})
    save_checkpoint(w, "runA", {"stage": "x"})
    M.save_milestone_run_state(w, M.MilestoneRunState(group_id="g1", original_goal="g", milestones=[]))
    DecisionLedger().record_and_persist(w, "probe_decision", note="x")
    evidence = _evidence(ws)
    return {
        "run_record": (P.run_record_path(w, "runA"), "json", lambda: P.load_run_record(w, "runA")),
        "control_state": (P.control_state_path(w), "json", lambda: P.load_control_state(w)),
        "contract_registry": (P.contract_registry_path(w), "json", lambda: P.load_contract_registry(w)),
        "artifact_registry": (P.artifact_registry_path(w), "json", lambda: P.load_artifact_registry(w)),
        "launch_record": (P.launch_record_path(w, launch), "json", lambda: P.load_launch_record(w, launch)),
        "approved_plan": (P.approved_plan_path(w, "plan1"), "json", lambda: P.load_approved_plan(w, "plan1")),
        "checkpoint": (checkpoint_path(w, "runA"), "json", lambda: load_checkpoint(w, "runA")),
        "milestone_sidecar": (M._sidecar_path(w, "g1"), "json", lambda: M.load_milestone_run_state(w, "g1")),
        "commit_evidence": (evidence, "json", lambda: C.load_commit_evidence(evidence, workspace_path=w)),
        "decision_ledger": (decision_ledger_path(w), "ledger", lambda: load_decision_ledger(w)),
    }


def _mutate(path: str, kind: str, mode: str) -> None:
    if kind == "ledger":
        lines = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            payload.pop("_workspace_id", None)
            if mode == "FOREIGN":
                payload["_workspace_id"] = FOREIGN
            lines.append(json.dumps(payload, sort_keys=True))
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload.pop("_workspace", None)
    if mode == "FOREIGN":
        payload["_workspace"] = {"workspace_id": FOREIGN, "version": "2"}
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


FAMILIES = ["run_record", "control_state", "contract_registry", "artifact_registry", "launch_record", "approved_plan", "checkpoint",
            "milestone_sidecar", "commit_evidence", "decision_ledger"]


def _refusal_of(call: Callable[[], Any]) -> str:
    """The reason code of the typed refusal ``call`` raises (the run-record family wraps it in UnreadableRunRecordError: its text names the cause)."""
    with pytest.raises((WorkspaceOwnershipError, P.UnreadableRunRecordError)) as caught:
        call()
    error = caught.value
    if isinstance(error, WorkspaceOwnershipError):
        return error.reason_code
    return WORKSPACE_OWNERSHIP_MISSING if "no workspace ownership stamp" in str(error) else WORKSPACE_OWNERSHIP_MISMATCH


@pytest.mark.parametrize("family", FAMILIES)
def test_every_reader_accepts_what_the_real_writer_wrote_and_refuses_a_stripped_or_foreign_stamp(ws, family):
    path, kind, read = _write_everything(ws)[family]
    original = Path(path).read_bytes()
    assert read() is not None, "LEGIT: the document exactly as the writer produced it is read"
    _mutate(path, kind, "STRIPPED")
    assert _refusal_of(read) == WORKSPACE_OWNERSHIP_MISSING
    Path(path).write_bytes(original)
    _mutate(path, kind, "FOREIGN")
    assert _refusal_of(read) == WORKSPACE_OWNERSHIP_MISMATCH


def test_every_writer_stamps_every_document_it_writes_a_walk_over_the_whole_authority_tree(ws):
    """Not a hand-curated list: after the real writers ran, EVERY json document under .kriya (except an input plan) carries a stamp, per line for the
    ledger, and the audit finds nothing."""
    _write_everything(ws)
    seen = []
    for directory, _dirs, files in os.walk(ws / ".kriya"):
        for name in files:
            full = os.path.join(directory, name)
            if name.endswith(".plan.json") or name.startswith(".") or not (name.endswith(".json") or name.endswith(".jsonl")):
                continue
            seen.append(full)
            text = Path(full).read_text(encoding="utf-8")
            documents = [json.loads(line) for line in text.splitlines() if line.strip()] if name.endswith(".jsonl") else [json.loads(text)]
            for document in documents:
                assert "_workspace" in document or "_workspace_id" in document, f"{full} is not stamped"
    assert len(seen) >= len(FAMILIES) - 1, seen
    assert audit_control_ownership(str(ws)) == ()


def test_the_audit_reports_every_stripped_family_and_adopts_nothing(ws):
    families = _write_everything(ws)
    stripped = [name for name in FAMILIES if name not in ("launch_record",)]  # launches are not audited here (M1a moves them out of the workspace)
    for name in stripped:
        path, kind, _ = families[name]
        _mutate(path, kind, "STRIPPED")
    before = {name: Path(families[name][0]).read_bytes() for name in stripped}
    findings = audit_control_ownership(str(ws))
    assert {Path(f.path).name for f in findings} == {Path(families[name][0]).name for name in stripped}
    assert {f.reason_code for f in findings} == {WORKSPACE_OWNERSHIP_MISSING}
    assert {name: Path(families[name][0]).read_bytes() for name in stripped} == before
    fields = refusal_fields(findings)
    assert fields["reason_codes"] == [WORKSPACE_OWNERSHIP_MISSING] and len(fields["ownership_findings"]) == len(findings)


def test_the_audit_does_not_call_a_corrupt_document_an_ownership_problem_nor_a_plan_an_unstamped_store(ws):
    families = _write_everything(ws)
    Path(families["control_state"][0]).write_text("{not json")
    (ws / ".kriya" / "milestones" / "g1.plan.json").write_text(json.dumps({"milestones": []}))
    assert audit_control_ownership(str(ws)) == ()


# ------------------------------------------------------------------------- overwrite never adopts

def test_a_save_never_overwrites_an_unstamped_or_foreign_document_so_it_never_stamps_it(ws):
    families = _write_everything(ws)
    saves = {
        "control_state": lambda: P.save_control_state(str(ws), ControlState(schema_version=1, run_id="new", subtask_states={})),
        "contract_registry": lambda: P.save_contract_registry(str(ws), ContractRegistry()),
        "artifact_registry": lambda: P.save_artifact_registry(str(ws), ArtifactRegistry()),
        "milestone_sidecar": lambda: M.save_milestone_run_state(str(ws), M.MilestoneRunState(group_id="g1", original_goal="g", milestones=[])),
        "checkpoint": lambda: save_checkpoint(str(ws), "runA", {"stage": "y"}),
    }
    for name, save in saves.items():
        path, kind, _ = families[name]
        for mode, code in (("STRIPPED", WORKSPACE_OWNERSHIP_MISSING), ("FOREIGN", WORKSPACE_OWNERSHIP_MISMATCH)):
            _mutate(path, kind, mode)
            before = Path(path).read_bytes()
            assert _refusal_of(save) == code, name
            assert Path(path).read_bytes() == before, f"{name}: a refused save must not touch the file"


def test_a_save_still_replaces_a_corrupt_document_as_before(ws):
    path = Path(P.control_state_path(str(ws)))
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    P.save_control_state(str(ws), ControlState(schema_version=1, run_id="r", subtask_states={}))
    assert json.loads(path.read_text())["_workspace"]["workspace_id"] == workspace_identity(str(ws))


def test_the_decision_ledger_refuses_to_append_to_or_load_an_unstamped_or_foreign_line(ws):
    families = _write_everything(ws)
    path, kind, _ = families["decision_ledger"]
    original = Path(path).read_bytes()
    for mode, code in (("STRIPPED", WORKSPACE_OWNERSHIP_MISSING), ("FOREIGN", WORKSPACE_OWNERSHIP_MISMATCH)):
        Path(path).write_bytes(original)
        _mutate(path, kind, mode)
        before = Path(path).read_bytes()
        assert _refusal_of(lambda: DecisionLedger().record_and_persist(str(ws), "more", note="y")) == code
        assert _refusal_of(lambda: load_decision_ledger(str(ws))) == code
        assert Path(path).read_bytes() == before


# ------------------------------------------------------------------------- commit evidence

def test_commit_evidence_is_ownership_checked_everywhere_it_is_read(ws, tmp_path):
    families = _write_everything(ws)
    path, kind, _ = families["commit_evidence"]
    _mutate(path, kind, "STRIPPED")
    [(listed_path, evidence, error)] = C.list_commit_evidence(str(ws))
    assert listed_path == path and evidence is None and "WorkspaceOwnershipMissing" in error
    with pytest.raises(C.UncertainCommitError):
        C.find_uncertain_commit_evidence(str(ws))
    from kriya.control.commit_state import assess_workspace_commit_state
    assessment = assess_workspace_commit_state(str(ws))
    assert assessment.safe is False and "tx1.json" in (assessment.commit_evidence_error or "") and assessment.reason_codes


def test_commit_evidence_written_for_another_workspace_is_refused_here(ws, tmp_path):
    _write_everything(ws)
    other = tmp_path / "Other"
    other.mkdir()
    _git_init(other)
    foreign = tmp_path / "copied.json"
    foreign.write_bytes(Path(os.path.join(C.commit_evidence_dir(str(ws)), "tx1.json")).read_bytes())
    with pytest.raises(WorkspaceOwnershipError) as caught:
        C.load_commit_evidence(str(foreign), workspace_path=str(other))
    assert caught.value.reason_code == WORKSPACE_OWNERSHIP_MISMATCH


def test_settling_recovered_evidence_keeps_its_stamp(ws):
    _write_everything(ws)
    path = os.path.join(C.commit_evidence_dir(str(ws)), "tx1.json")
    evidence = C.load_commit_evidence(path, workspace_path=str(ws))
    in_progress = C.CommitEvidence(**{**evidence.__dict__, "state": C.CommitState.IN_PROGRESS})
    C._persist_commit_evidence(str(ws), in_progress)
    settled = C.settle_recovered_commit_evidence(str(ws), in_progress, state=C.CommitState.COMMITTED, recovery={"tool": "t"}, result_revisions={})
    assert settled.state is C.CommitState.COMMITTED
    assert json.loads(Path(path).read_text())["_workspace"]["workspace_id"] == workspace_identity(str(ws))


# ------------------------------------------------------------------------- the rule itself

def test_validate_ownership_distinguishes_missing_from_mismatch_and_keeps_the_legacy_spelling_of_this_workspace(tmp_path):
    w = str(tmp_path)
    with pytest.raises(WorkspaceOwnershipMissing) as missing:
        validate_ownership(w, {}, "x.json")
    assert missing.value.reason_code == WORKSPACE_OWNERSHIP_MISSING and "x.json" in str(missing.value) and "outside the active Kriya control-state location" in str(missing.value)
    for stamp in ({"workspace_id": FOREIGN}, "not a dict", {"workspace_id": None}, {}):
        with pytest.raises(WorkspaceOwnershipError) as mismatch:
            validate_ownership(w, {"_workspace": stamp}, "x.json")
        assert mismatch.value.reason_code == WORKSPACE_OWNERSHIP_MISMATCH and not isinstance(mismatch.value, WorkspaceOwnershipMissing)
    validate_ownership(w, {"_workspace": {"workspace_id": workspace_identity(w)}}, "x.json")
    validate_ownership(w, {"_workspace": {"workspace_id": legacy_workspace_identity(w)}}, "x.json")  # an alias of the SAME workspace, not an adoption
    with pytest.raises(WorkspaceOwnershipMissing):
        validate_ledger_line_ownership(w, {}, "ledger")
    with pytest.raises(WorkspaceOwnershipError):
        validate_ledger_line_ownership(w, {"_workspace_id": FOREIGN}, "ledger")
    validate_ledger_line_ownership(w, {"_workspace_id": workspace_identity(w)}, "ledger")


def test_the_reason_code_table_is_closed_and_every_message_renders():
    assert set(OWNERSHIP_REFUSALS) == {WORKSPACE_OWNERSHIP_MISSING, WORKSPACE_OWNERSHIP_MISMATCH}
    for code, template in OWNERSHIP_REFUSALS.items():
        rendered = template.format(source="s", expected="e", actual="a")
        assert "s" in rendered and "outside the active Kriya control-state location" in rendered, code
    assert issubclass(WorkspaceOwnershipMissing, WorkspaceOwnershipError)  # every existing handler of the base still catches the new refusal


def test_the_implicit_migration_is_retired_and_cannot_come_back_unnoticed():
    from kriya.control import decisions
    from kriya.workflow import resume_support, workflow_controller

    assert not hasattr(wid, "json_document_is_ownerless")
    assert not hasattr(decisions, "stamp_legacy_decision_ledger_ownership")
    for module in (workflow_controller, resume_support):
        assert not [name for name in dir(module) if "migrate_legacy" in name], module.__name__
    source = (Path(workflow_controller.__file__).read_text() + Path(resume_support.__file__).read_text())
    assert "LEGACY_STATE_MIGRATION_FAILED" not in source and "legacy_workspace_ownership_migrated" not in source
