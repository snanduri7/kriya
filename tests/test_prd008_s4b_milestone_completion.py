"""PRD-008 S4b: a completed milestone is skipped only with durable proof.

Before S4b, ``milestone.id in completed_milestone_ids`` alone skipped a
milestone on a later run: editing, deleting or resetting its output left it
skipped and every later milestone built on output that no longer existed.

Every scenario here drives the real milestone driver with real commits
through the one commit seam (terminal_commit.py -> RunRecord cycle + commit
evidence), and reloads milestone state from the sidecar exactly as
`kriya generate --from-milestones` does. The fake engine stands in only for
model work; Test B uses the real WorkflowEngine.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import kriya.workflow.workflow_controller as workflow_controller_module
from kriya.agents.contracts import MilestoneV2
from kriya.config.config import AppConfig
from kriya.control.commit_state import UncertainWorkspaceStateError
from kriya.control.persistence import load_run_record, run_record_path, save_run_record, scan_run_records
from kriya.control.recovery import (
    STATUS_CLEAN,
    STATUS_COMPLETE_PARTIAL_REQUIRED,
    STATUS_RECOVERY_AVAILABLE,
    assess_recovery,
    recover_workspace,
)
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.run_record import RunLifecycle, RunRecord
from kriya.control.workspace_identity import ownership_metadata
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.edit_safety import commit_evidence_dir, read_file_revision
from kriya.workflow.milestone_completion import (
    COMMIT_EVIDENCE_MISSING,
    COMPLETION_PROOF_MISSING,
    COMPLETION_RECONSTRUCTION_UNVERIFIED,
    DELETED_PATH_RECREATED,
    LEGACY_STATE_UNVERIFIED,
    MILESTONE_DEFINITION_CHANGED,
    MODE_CHANGED,
    NO_COMMITTED_OUTPUT,
    OUTPUT_CHANGED,
    OUTPUT_MISSING,
    PROOF_EVIDENCE_MISMATCH,
    RUN_RECORD_MISSING,
    SHARED_PATH_WITH_RERUN,
    UPSTREAM_INVALIDATED,
    assess_completed_milestone_reuse,
)
from kriya.workflow.milestone_validation import topological_order
from kriya.workflow.milestones import (
    load_milestone_run_state,
    load_or_resume_milestone_run_state,
    run_milestones,
)
from kriya.workflow.terminal_commit import CandidateFile, commit_terminal_candidate, materialize_candidate
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

ROOT = Path(__file__).resolve().parents[1]
GROUP = "grp"


# ---------------------------------------------------------------- harness

def _milestone(mid, depends_on=()):
    return MilestoneV2(
        id=mid, goal=f"build {mid}", success_criterion=f"{mid} works", depends_on=list(depends_on),
    )


def _plan(milestones):
    return {"group_id": GROUP, "original_goal": "the goal", "milestones": [m.model_dump() for m in milestones]}


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Path(os.path.realpath(workspace))


def _apply(workspace_path, changes):
    """Commit ``changes`` ({relpath: bytes | (bytes, mode) | None=delete})
    through the real terminal commit seam, as a verified candidate would be."""
    candidate = Path(tempfile.mkdtemp())
    files = []
    for relpath, spec in sorted(changes.items()):
        target = os.path.join(workspace_path, relpath)
        if spec is None:
            files.append(CandidateFile(relpath, read_file_revision(target), delete=True))
            continue
        data, mode = spec if isinstance(spec, tuple) else (spec, 0o644)
        path = candidate / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, mode)
        files.append(CandidateFile(relpath, read_file_revision(target)))
    outcome = commit_terminal_candidate(
        materialize_candidate(str(candidate), str(workspace_path), files),
        workspace_path=str(workspace_path), transaction_id=uuid.uuid4().hex,
    )
    assert outcome.committed, outcome.failure_payload()


def _route():
    return EngineeringRoute(
        kind=ChangeKind.MILESTONE, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    )


class FakeEngine:
    """Model work replaced by fixed outputs; the commit is real."""

    def __init__(self, milestones, outputs, integration=None):
        self.order = [milestone.id for milestone in topological_order(milestones)]
        self.outputs = outputs
        self.integration = integration or {}
        self.calls = []
        self.run_verifier = MagicMock()
        self.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        self.engineering_triage = MagicMock()
        self.engineering_triage.classify = AsyncMock(return_value=_route())

    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **_):
        name = self.order[milestone_index - 1] if milestone_index <= len(self.order) else "INTEGRATION"
        self.calls.append(name)
        changes = self.integration if name == "INTEGRATION" else self.outputs.get(name, {})
        if changes:
            _apply(workspace_path, changes)
        return {
            "quality_gates_passed": True, "design": "d",
            "files": sorted(path for path, spec in changes.items() if spec is not None),
        }


def _run(workspace, milestones, engine, **kwargs):
    """One `generate --from-milestones` invocation: state reloaded from the
    plan + sidecar, a fresh run owning the workspace."""
    state = load_or_resume_milestone_run_state(str(workspace), _plan(milestones))
    result = asyncio.run(run_milestones(engine, state, str(workspace), **kwargs))
    return result, state


def _decisions(result):
    return {
        item["milestone_id"]: (item["status"], [reason["code"] for reason in item["reasons"]])
        for item in result["milestone_reuse"]["decisions"]
    }


CHAIN = [_milestone("M1"), _milestone("M2", ["M1"])]
CHAIN_OUTPUTS = {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2.py": b"M2 = 1\n"}}


def _completed_chain(tmp_path, outputs=CHAIN_OUTPUTS, milestones=CHAIN):
    workspace = _workspace(tmp_path)
    result, state = _run(workspace, milestones, FakeEngine(milestones, outputs))
    assert result["status"] == "success"
    assert state.completed_milestone_ids == [m.id for m in topological_order(milestones)]
    return workspace


# ---------------------------------------------------------------- A: the gate

def _unsettled_record(workspace):
    new = RunRecord.new("prior", "workspace", None, None)
    running = new.transition(RunLifecycle.RUNNING)
    eligible = running.begin_commit("tx-prior", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None)
    for index, record in enumerate([new, running, eligible]):
        save_run_record(str(workspace), record, expected_revision=index or None)


def test_a_execute_milestones_refuses_an_uncertain_workspace_exactly_like_direct_execution(tmp_path):
    workspace = _workspace(tmp_path)
    _unsettled_record(workspace)
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    state = load_or_resume_milestone_run_state(str(workspace), _plan(CHAIN))

    milestone_result = asyncio.run(WorkflowController(engine).execute_milestones(state, str(workspace)))
    direct_result = asyncio.run(WorkflowController(engine).execute("goal", str(workspace)))

    assert milestone_result.legacy_result["reason_codes"] == ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]
    assert milestone_result.legacy_result["reason_codes"] == direct_result.legacy_result["reason_codes"]
    assert milestone_result.legacy_result["status"] == direct_result.legacy_result["status"]
    # Refused before triage, before any milestone, before any new record.
    engine.engineering_triage.classify.assert_not_awaited()
    assert engine.calls == []
    assert [record.run_id for record in scan_run_records(str(workspace)).records] == ["prior"]
    assert not (workspace / "m1.py").exists()


# ---------------------------------------------------------------- B: resume inside a milestone

@pytest.fixture
def git_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    return workspace


def _config():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    return cfg


def _engine(cfg, responses):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=responses)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    return engine, llm


_M1_AFTER_PLAN = [
    "Design: Write math.py",
    '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
    "Review: Approved",
]
_INTEGRATION = [
    "Step 1: integrate",
    "Design: Write main.py",
    '[{"filepath": "main.py", "content": "print(1)"}]',
    "Review: Approved",
]


def _crash_after_plan_checkpoint(git_workspace, cfg, milestones):
    engine, _ = _engine(cfg, ["Step 1 (from the first run)", RuntimeError("architect crashed")])
    with pytest.raises(RuntimeError, match="architect crashed"):
        _run(git_workspace, milestones, engine)
    return {record.run_id for record in scan_run_records(str(git_workspace)).records}


@pytest.mark.parametrize("workspace_changed", [False, True])
def test_b_resume_inside_a_milestone_uses_the_prd008_validator(git_workspace, workspace_changed):
    cfg = _config()
    milestones = [_milestone("M1")]
    first_runs = _crash_after_plan_checkpoint(git_workspace, cfg, milestones)
    if workspace_changed:
        (git_workspace / "extra.py").write_text("X = 1\n")
    planner = ["Step 1 (from the second run)"] if workspace_changed else []
    engine, llm = _engine(cfg, planner + _M1_AFTER_PLAN + _INTEGRATION)

    result, state = _run(git_workspace, milestones, engine, resume=True)

    assert result["status"] == "success", result
    assert state.completed_milestone_ids == ["M1"]
    [record] = [r for r in scan_run_records(str(git_workspace)).records if r.run_id not in first_runs]
    decision = record.resume_decision
    assert decision is not None  # the PRD-008 resume plan was built for the milestone
    if workspace_changed:
        # Workspace fingerprint CHANGED: nothing from the checkpoint reused.
        assert "plan" not in decision["reused"]
        assert llm.complete.await_count == len(planner + _M1_AFTER_PLAN + _INTEGRATION)
    else:
        # Every fingerprint MATCH: the milestone's plan comes from its checkpoint.
        assert "plan" in decision["reused"]
        assert llm.complete.await_count == len(_M1_AFTER_PLAN + _INTEGRATION)


# ---------------------------------------------------------------- C: recovery across cycles

# M1 commits normally; M2's two-file commit is killed with os._exit between
# its staged-file replaces (crash_at "stage2": m2a.py applied, m2b.py not) or
# before the first one ("stage1").
_CRASH_SCRIPT = r'''
import asyncio, os, sys
sys.path.insert(0, sys.argv[3])
from tests.test_prd008_s4b_milestone_completion import CHAIN, FakeEngine, _plan, _apply
from kriya.workflow.milestones import load_or_resume_milestone_run_state, run_milestones

workspace, crash_at = sys.argv[1], sys.argv[2]
real_replace = os.replace
count = {"stage": 0}

def crashing_replace(src, dst):
    if os.path.basename(src).startswith(".kriya-stage-"):
        count["stage"] += 1
        if f"stage{count['stage']}" == crash_at:
            os._exit(9)
    return real_replace(src, dst)

class CrashingEngine(FakeEngine):
    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        if milestone_index == 2:
            os.replace = crashing_replace
        return await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)

engine = CrashingEngine(CHAIN, {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2a.py": b"A\n", "m2b.py": b"B\n"}})
state = load_or_resume_milestone_run_state(workspace, _plan(CHAIN))
asyncio.run(run_milestones(engine, state, workspace))
os._exit(0)
'''


@pytest.mark.parametrize("crash_at,expected_status,m2_decision", [
    # Rolled forward by --complete-partial: M2's commit is COMMITTED but was
    # settled by recovery, not by its run - never reconstructed (S4c).
    ("stage2", STATUS_COMPLETE_PARTIAL_REQUIRED, ("UNVERIFIED", [COMPLETION_RECONSTRUCTION_UNVERIFIED])),
    # Rolled back: nothing of M2 was committed, so there is nothing to decide.
    ("stage1", STATUS_RECOVERY_AVAILABLE, None),
])
def test_c_recovery_across_milestone_commit_cycles(tmp_path, crash_at, expected_status, m2_decision):
    workspace = _workspace(tmp_path)
    crashed = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, str(workspace), crash_at, str(ROOT)],
        env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=120,
    )
    assert crashed.returncode == 9, crashed.stderr
    m1_proof = load_milestone_run_state(str(workspace), GROUP).completion_proofs["M1"]
    assert len(m1_proof["transaction_ids"]) == 1

    # Normal execution is refused while M2's commit is unsettled.
    outputs = {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2a.py": b"A\n", "m2b.py": b"B\n"}}
    engine = FakeEngine(CHAIN, outputs)
    with pytest.raises(UncertainWorkspaceStateError):
        _run(workspace, CHAIN, engine)
    assert engine.calls == []

    assert assess_recovery(str(workspace)).status == expected_status
    report = recover_workspace(str(workspace), complete_partial=(crash_at == "stage2"))
    assert assess_recovery(str(workspace)).status == STATUS_CLEAN, report.to_dict()
    [crashed_record] = scan_run_records(str(workspace)).records
    assert crashed_record.lifecycle_state == RunLifecycle.RECOVERED

    # Legal again: M1 is skipped on its proof (still bound to M1's own
    # transaction); M2 was never completed, whatever part of it landed.
    result, state = _run(workspace, CHAIN, engine)
    assert result["status"] == "success"
    expected = {"M1": ("MATCH", [])}
    if m2_decision is not None:
        expected["M2"] = m2_decision
    assert _decisions(result) == expected
    assert engine.calls == ["M2", "INTEGRATION"]
    assert state.completion_proofs["M1"]["transaction_ids"] == m1_proof["transaction_ids"]
    assert (workspace / "m2a.py").read_bytes() == b"A\n" and (workspace / "m2b.py").read_bytes() == b"B\n"


# ---------------------------------------------------------------- D-H: workspace reality

def test_d_unchanged_completed_milestones_are_skipped_on_valid_proof(tmp_path):
    workspace = _completed_chain(tmp_path)
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, _ = _run(workspace, CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]  # no Developer/model work for M1 or M2
    newest = max(scan_run_records(str(workspace)).records, key=lambda record: record.created_at)
    assert [item["status"] for item in newest.milestone_reuse["decisions"]] == ["MATCH", "MATCH"]


def test_e_one_changed_byte_makes_the_milestone_and_its_dependents_rerun(tmp_path):
    workspace = _completed_chain(tmp_path)
    (workspace / "m1.py").write_bytes(b"M1 = 2\n")
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    assert _decisions(result) == {
        "M1": ("CHANGED", [OUTPUT_CHANGED]), "M2": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }
    assert engine.calls == ["M1", "M2", "INTEGRATION"]
    assert (workspace / "m1.py").read_bytes() == b"M1 = 1\n"
    assert state.completed_milestone_ids == ["M1", "M2"] and state.stale_milestone_ids == []


def test_f_deleted_output_is_output_missing(tmp_path):
    workspace = _completed_chain(tmp_path)
    (workspace / "m1.py").unlink()
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result)["M1"] == ("CHANGED", [OUTPUT_MISSING])


def test_g_recreating_a_path_the_milestone_deleted_is_detected(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "old.py").write_bytes(b"OLD = 1\n")
    outputs = {"M1": {"old.py": None, "m1.py": b"M1 = 1\n"}, "M2": {"m2.py": b"M2 = 1\n"}}
    _run(workspace, CHAIN, FakeEngine(CHAIN, outputs))
    assert not (workspace / "old.py").exists()
    (workspace / "old.py").write_bytes(b"OLD = 1\n")
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, outputs))
    assert _decisions(result)["M1"] == ("CHANGED", [DELETED_PATH_RECREATED])


def test_h_workspace_reset_to_before_the_milestone_is_not_skipped(git_workspace):
    (git_workspace / "app.py").write_bytes(b"APP = 0\n")
    subprocess.run(["git", "add", "app.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=git_workspace, check=True)
    outputs = {"M1": {"app.py": b"APP = 1\n", "m1.py": b"M1 = 1\n"}, "M2": {"m2.py": b"M2 = 1\n"}}
    _run(git_workspace, CHAIN, FakeEngine(CHAIN, outputs))
    # Reset to the pre-M1 content; the milestone sidecar (.kriya/) survives.
    subprocess.run(["git", "checkout", "-q", "--", "app.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "clean", "-fq", "m1.py", "m2.py"], cwd=git_workspace, check=True)
    engine = FakeEngine(CHAIN, outputs)
    result, _ = _run(git_workspace, CHAIN, engine)
    status, codes = _decisions(result)["M1"]
    assert status == "CHANGED" and set(codes) == {OUTPUT_CHANGED, OUTPUT_MISSING}
    assert engine.calls == ["M1", "M2", "INTEGRATION"]


# ---------------------------------------------------------------- I: authoritative evidence

def _m1_transaction(workspace):
    return load_milestone_run_state(str(workspace), GROUP).completion_proofs["M1"]["transaction_ids"][0]


def test_i_missing_commit_evidence_is_unverified_and_reruns(tmp_path):
    workspace = _completed_chain(tmp_path)
    os.unlink(os.path.join(commit_evidence_dir(str(workspace)), f"{_m1_transaction(workspace)}.json"))
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, _ = _run(workspace, CHAIN, engine)  # the gate lets a MISSING file through
    assert _decisions(result)["M1"] == ("UNVERIFIED", [COMMIT_EVIDENCE_MISSING])
    assert engine.calls == ["M1", "M2", "INTEGRATION"]


def test_i_corrupt_commit_evidence_is_refused_by_the_gate_before_any_reuse_decision(tmp_path):
    workspace = _completed_chain(tmp_path)
    Path(commit_evidence_dir(str(workspace)), f"{_m1_transaction(workspace)}.json").write_text("{not json")
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    with pytest.raises(UncertainWorkspaceStateError):
        _run(workspace, CHAIN, engine)
    assert engine.calls == []


def test_i_missing_run_record_is_unverified(tmp_path):
    workspace = _completed_chain(tmp_path)
    run_id = load_milestone_run_state(str(workspace), GROUP).completion_proofs["M1"]["run_id"]
    os.unlink(run_record_path(str(workspace), run_id))
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result)["M1"] == ("UNVERIFIED", [RUN_RECORD_MISSING])


def test_i_proof_that_disagrees_with_the_evidence_is_unverified(tmp_path):
    workspace = _completed_chain(tmp_path)
    sidecar = Path(workspace, ".kriya", "milestones", f"{GROUP}.json")
    payload = json.loads(sidecar.read_text())
    payload["commit_ledger"][0]["operations"][0]["post_state"]["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(payload))
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result)["M1"] == ("UNVERIFIED", [PROOF_EVIDENCE_MISMATCH])


# ---------------------------------------------------------------- J: legacy state

def test_j_legacy_milestone_state_loads_but_is_never_grandfathered(tmp_path):
    workspace = _completed_chain(tmp_path)
    sidecar = Path(workspace, ".kriya", "milestones", f"{GROUP}.json")
    payload = json.loads(sidecar.read_text())
    for key in ("completion_proofs", "commit_ledger", "last_reuse_assessment", "milestone_completion_schema"):
        payload.pop(key)
    payload["_workspace"] = ownership_metadata(str(workspace))
    sidecar.write_text(json.dumps(payload))
    legacy = load_milestone_run_state(str(workspace), GROUP)
    assert legacy.completed_milestone_ids == ["M1", "M2"] and legacy.completion_proofs == {}

    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    assert _decisions(result) == {
        "M1": ("UNVERIFIED", [LEGACY_STATE_UNVERIFIED]),
        "M2": ("UNVERIFIED", [LEGACY_STATE_UNVERIFIED]),
    }
    assert engine.calls == ["M1", "M2", "INTEGRATION"]
    # Re-completed with real proofs, so the next run can skip them.
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}


def test_a_proof_missing_from_a_current_sidecar_is_unverified(tmp_path):
    workspace = _completed_chain(tmp_path)
    state = load_milestone_run_state(str(workspace), GROUP)
    decision = assess_completed_milestone_reuse(
        str(workspace), CHAIN, ["M1"], {}, state.commit_ledger,
    ).get("M1")
    assert (decision.status.value, decision.reason_codes) == ("UNVERIFIED", [COMPLETION_PROOF_MISSING])


# ---------------------------------------------------------------- K: selectivity

def test_k_only_the_stale_milestone_and_its_dependents_rerun(tmp_path):
    milestones = [
        _milestone("M1"), _milestone("M2", ["M1"]), _milestone("M3", ["M1"]), _milestone("M4", ["M2"]),
    ]
    outputs = {mid: {f"{mid.lower()}.py": f"{mid} = 1\n".encode()} for mid in ("M1", "M2", "M3", "M4")}
    workspace = _completed_chain(tmp_path, outputs, milestones)
    (workspace / "m2.py").write_bytes(b"edited\n")
    engine = FakeEngine(milestones, outputs)
    result, _ = _run(workspace, milestones, engine)
    assert _decisions(result) == {
        "M1": ("MATCH", []), "M2": ("CHANGED", [OUTPUT_CHANGED]),
        "M3": ("MATCH", []), "M4": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }
    assert sorted(engine.calls) == ["INTEGRATION", "M2", "M4"]


def test_an_unrelated_milestone_sharing_a_path_with_a_rerun_also_reruns(tmp_path):
    milestones = [_milestone("M1"), _milestone("M2", ["M1"]), _milestone("M3", ["M1"])]
    outputs = {
        "M1": {"pom.xml": b"<m1/>\n"},
        "M2": {"m2.py": b"M2 = 1\n", "shared.cfg": b"m2\n"},
        "M3": {"shared.cfg": b"m3\n"},
    }
    workspace = _completed_chain(tmp_path, outputs, milestones)
    (workspace / "m2.py").write_bytes(b"edited\n")
    result, _ = _run(workspace, milestones, FakeEngine(milestones, outputs))
    decisions = _decisions(result)
    assert decisions["M1"] == ("MATCH", [])
    assert decisions["M2"] == ("CHANGED", [OUTPUT_CHANGED])
    assert decisions["M3"] == ("CHANGED", [SHARED_PATH_WITH_RERUN])


def test_milestones_modifying_each_others_files_stay_valid_when_nothing_changed(tmp_path):
    # pom.xml created by M1, modified by M2 and again by integration: the
    # latest verified writer owns each path, so a clean rerun skips both.
    outputs = {"M1": {"pom.xml": b"<m1/>\n"}, "M2": {"pom.xml": b"<m1/><m2/>\n"}}
    workspace = _workspace(tmp_path)
    integration = {"pom.xml": b"<m1/><m2/><int/>\n"}
    _run(workspace, CHAIN, FakeEngine(CHAIN, outputs, integration))
    engine = FakeEngine(CHAIN, outputs, integration)
    result, _ = _run(workspace, CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]
    # Editing the shared file is charged to its latest milestone writer only
    # when that writer is a milestone - here the integration pass owns it.
    (workspace / "pom.xml").write_bytes(b"edited\n")
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, outputs, integration))
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}


def test_a_milestone_that_committed_nothing_is_unverified(tmp_path):
    outputs = {"M1": {}, "M2": {"m2.py": b"M2 = 1\n"}}
    workspace = _completed_chain(tmp_path, outputs)
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, outputs))
    assert _decisions(result) == {
        "M1": ("UNVERIFIED", [NO_COMMITTED_OUTPUT]), "M2": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }


# ---------------------------------------------------------------- L: byte exactness

@pytest.mark.parametrize("committed,edit,expected", [
    (b"x\r\ny\r\n", lambda path: path.write_bytes(b"x\ny\n"), OUTPUT_CHANGED),  # CRLF -> LF
    (b"\xff\n", lambda path: path.write_bytes(b"\xfe\n"), OUTPUT_CHANGED),  # same text after errors="replace"
    (b"M1 = 1\n", lambda path: os.chmod(path, 0o755), MODE_CHANGED),
])
def test_l_byte_exact_detection(tmp_path, committed, edit, expected):
    outputs = {"M1": {"m1.py": committed}, "M2": {"m2.py": b"M2 = 1\n"}}
    workspace = _completed_chain(tmp_path, outputs)
    edit(workspace / "m1.py")
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, outputs))
    assert _decisions(result)["M1"] == ("CHANGED", [expected])


# ---------------------------------------------------------------- wiring

def test_retention_keeps_the_runs_a_completion_proof_references(tmp_path):
    workspace = _completed_chain(tmp_path)
    for _ in range(3):
        with begin_mutating_run(str(workspace)):
            pass
    prune_run_state(str(workspace), keep_terminal_runs=0)
    run_id = load_milestone_run_state(str(workspace), GROUP).completion_proofs["M1"]["run_id"]
    assert load_run_record(str(workspace), run_id) is not None
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}


def test_execute_milestones_never_marks_a_milestone_done_that_will_rerun(tmp_path):
    workspace = _completed_chain(tmp_path)
    (workspace / "m1.py").write_bytes(b"edited\n")
    saved = []
    real_save = workflow_controller_module.save_control_state

    def spy(workspace_path, state):
        saved.append(dict(state.milestone_states))
        return real_save(workspace_path, state)

    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    state = load_or_resume_milestone_run_state(str(workspace), _plan(CHAIN))
    with patch.object(workflow_controller_module, "save_control_state", side_effect=spy):
        result = asyncio.run(WorkflowController(engine).execute_milestones(state, str(workspace)))
    assert result.legacy_result["status"] == "success"
    assert saved[0] == {"M1": "stale", "M2": "stale"}
    assert engine.calls == ["M1", "M2", "INTEGRATION"]
    assert saved[-1] == {"M1": "done", "M2": "done"}


def test_invalidated_output_is_not_fed_back_as_established_context(tmp_path):
    workspace = _completed_chain(tmp_path)
    assert "m1.py" in load_milestone_run_state(str(workspace), GROUP).established_file_context
    (workspace / "m1.py").write_bytes(b"edited\n")
    seen = []

    class Recording(FakeEngine):
        async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
            seen.append(sorted(kwargs.get("established_files") or []))
            return await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)

    _run(workspace, CHAIN, Recording(CHAIN, CHAIN_OUTPUTS))
    assert seen[0] == []  # M1 reruns without its own stale output as "established"


def test_execute_milestones_records_every_decision_once_per_run(tmp_path):
    # execute_milestones and run_milestones both revalidate; the second call
    # must reuse the run's assessment, not re-decide over the already
    # pruned completed list and lose why M2 reran.
    milestones = [_milestone("M1"), _milestone("M2", ["M1"]), _milestone("M3", ["M1"])]
    outputs = {mid: {f"{mid.lower()}.py": f"{mid} = 1\n".encode()} for mid in ("M1", "M2", "M3")}
    workspace = _completed_chain(tmp_path, outputs, milestones)
    (workspace / "m2.py").write_bytes(b"edited\n")
    engine = FakeEngine(milestones, outputs)
    state = load_or_resume_milestone_run_state(str(workspace), _plan(milestones))
    result = asyncio.run(WorkflowController(engine).execute_milestones(state, str(workspace)))

    expected = {"M1": ("MATCH", []), "M2": ("CHANGED", [OUTPUT_CHANGED]), "M3": ("MATCH", [])}
    assert _decisions(result.legacy_result) == expected
    newest = max(scan_run_records(str(workspace)).records, key=lambda record: record.created_at)
    assert _decisions({"milestone_reuse": newest.milestone_reuse}) == expected
    sidecar = load_milestone_run_state(str(workspace), GROUP).last_reuse_assessment
    assert _decisions({"milestone_reuse": sidecar}) == expected
    assert engine.calls == ["M2", "INTEGRATION"]


def test_a_hand_edited_milestone_definition_reruns_with_its_dependents(tmp_path):
    # Direct resume's goal fingerprint catches a changed goal; a milestone
    # whose definition changed in the plan file must not be skipped either.
    workspace = _completed_chain(tmp_path)
    edited = [
        MilestoneV2(id="M1", goal="build M1 differently", success_criterion="M1 works"),
        _milestone("M2", ["M1"]),
    ]
    engine = FakeEngine(edited, CHAIN_OUTPUTS)
    result, _ = _run(workspace, edited, engine)
    assert _decisions(result) == {
        "M1": ("CHANGED", [MILESTONE_DEFINITION_CHANGED]), "M2": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }
    assert engine.calls == ["M1", "M2", "INTEGRATION"]
