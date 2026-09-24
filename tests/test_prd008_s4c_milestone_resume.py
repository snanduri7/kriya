"""PRD-008 S4c: milestone resume closure.

1. VERIFIED_NO_CHANGE - a milestone that committed nothing is reusable only
   when deterministic evidence covers every acceptance criterion (never a
   model's "no change", an unrelated passing test, or zero executed tests),
   bound to the verification policy, the completions it built on, the
   toolchain (UNVERIFIED while PRD-011 has no identity), and the workspace
   as the verified commit history explains it (generated output excluded).
2. A crash between a milestone's durable commit and its completion save -
   or inside the commit, finished by `kriya runs recover --complete-partial`
   - is closed by rebuilding the proof from the RunRecord's attributed cycle
   + commit evidence (+ recovery provenance), and refused (the milestone
   reruns) whenever that evidence does not prove the exact committed output.
3. `--resume` selects a milestone's OWN newest checkpoint by work-unit
   identity; the PRD-008 validator still decides whether it is reusable.
4. Shared-file lineage: an earlier milestone is never excused by a later
   overwrite that cannot itself be proven.

Harness (FakeEngine, _run, ...) is S4b's: real commits through the one
commit seam, milestone state reloaded from the sidecar like the CLI.
"""
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _milestone_proof_harness import (
    CHAIN,
    CHAIN_OUTPUTS,
    GROUP,
    MID_COMMIT_OUTPUTS,
    TESTS_DIR,
    FakeEngine,
    _completed_chain,
    _config,
    _crash_mid_commit,
    _decisions,
    _engine,
    _milestone,
    _run,
    _workspace,
    git_workspace,  # noqa: F401 - pytest fixture
)

from kriya.agents.contracts import AcceptanceCriterion, MilestoneV2
from kriya.config.config import AppConfig
from kriya.control.commit_state import UncertainWorkspaceStateError
from kriya.control.persistence import scan_run_records
from kriya.control.recovery import recover_workspace
from kriya.control.run_record import RunLifecycle
from kriya.workflow import resume_fingerprints
from kriya.workflow.checkpoint import save_checkpoint
from kriya.workflow.edit_safety import _persist_commit_evidence, commit_evidence_dir, load_commit_evidence
from kriya.workflow.milestone_completion import (
    ACCEPTANCE_COVERAGE_INCOMPLETE,
    ACCEPTANCE_COVERAGE_UNAVAILABLE,
    CHECKPOINT_IDENTITY_MISMATCH,
    COMMIT_EVIDENCE_MISSING,
    COMMIT_LINEAGE_UNVERIFIED,
    COMPLETION_RECONSTRUCTED,
    COMPLETION_RECONSTRUCTION_UNVERIFIED,
    CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE,
    MILESTONE_CHECKPOINT_SELECTED,
    NO_COMMITTED_OUTPUT,
    NO_COMPATIBLE_MILESTONE_CHECKPOINT,
    ORIGIN_RECOVERY,
    OUTPUT_CHANGED,
    RECOVERY_PROVENANCE_UNVERIFIED,
    TOOLCHAIN_IDENTITY_UNAVAILABLE,
    UPSTREAM_INVALIDATED,
    VERIFIED_NO_CHANGE,
    VERIFIED_NO_CHANGE_INVALIDATED,
    milestone_work_unit,
)
from kriya.workflow.milestones import load_milestone_run_state
from kriya.workflow.resume_fingerprints import Fingerprint
from kriya.workflow.workflow import deterministic_gate_evidence

TESTS_PASSED = [{"type": "test", "passed": True, "status": "PASS_WITH_TESTS", "attempt": 1}]
# What a deterministic verifier would report: criterion A of M1 is covered
# by one specific test that ran three tests and passed.
COVERS_A = [{
    "criterion_id": "A", "kind": "test", "selector": "tests/test_m1.py::test_a",
    "attempt": 1, "status": "PASS_WITH_TESTS", "tests_executed": 3,
}]


class GatedEngine(FakeEngine):
    """FakeEngine whose workflow result also reports deterministic gate
    evidence and acceptance coverage per milestone (as a deterministic
    verifier would), and which carries a real config for the
    verification-policy fingerprint."""

    def __init__(self, milestones, outputs, gates=None, integration=None, config=None, coverage=None):
        super().__init__(milestones, outputs, integration)
        self.gates = gates or {}
        self.coverage = coverage or {}
        self.kernel = MagicMock()
        self.kernel.config = config or AppConfig()
        self.kwargs = []

    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        name = self.order[milestone_index - 1] if milestone_index <= len(self.order) else "INTEGRATION"
        self.kwargs.append((name, {key: kwargs.get(key) for key in ("resume", "resume_id")}))
        result = await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)
        result["deterministic_gate_evidence"] = self.gates.get(name, [])
        result["acceptance_coverage"] = self.coverage.get(name, [])
        return result


def _events(result):
    return result["milestone_reuse"]["events"]


def _accepted(mid, depends_on=(), criteria=("A",)):
    """A milestone with structured acceptance criteria."""
    return MilestoneV2(
        id=mid, goal=f"build {mid}", depends_on=list(depends_on),
        acceptance=[AcceptanceCriterion(id=criterion, description=f"{mid}: {criterion}") for criterion in criteria],
    )


@pytest.fixture
def toolchain_bound(monkeypatch):
    """Stand-in for PRD-011: a canonical toolchain identity is available.
    Mutate ``identity["value"]`` to model a toolchain change."""
    identity = {"value": "toolchain-1"}
    monkeypatch.setattr(
        resume_fingerprints, "toolchain_fingerprint", lambda: Fingerprint(identity["value"], "test-toolchain"),
    )
    return identity


def _proof(workspace, milestone_id):
    return load_milestone_run_state(str(workspace), GROUP).completion_proofs[milestone_id]


# ---------------------------------------------------------------- 1. VERIFIED_NO_CHANGE

ACCEPTED_CHAIN = [_accepted("M1"), _accepted("M2", ["M1"])]
NO_OP_OUTPUTS = {"M1": {}, "M2": {"m2.py": b"M2 = 1\n"}}


def _no_op_engine(gates=None, coverage=None, milestones=ACCEPTED_CHAIN, outputs=NO_OP_OUTPUTS, **kwargs):
    return GatedEngine(
        milestones, outputs,
        gates={"M1": TESTS_PASSED if gates is None else gates},
        coverage={"M1": COVERS_A if coverage is None else coverage}, **kwargs,
    )


def test_c_a_covered_no_op_milestone_is_verified_no_change_and_skipped(git_workspace, toolchain_bound):  # noqa: F811
    _run(git_workspace, ACCEPTED_CHAIN, _no_op_engine())
    proof = _proof(git_workspace, "M1")
    assert proof["kind"] == VERIFIED_NO_CHANGE and proof["transaction_ids"] == []
    binding = proof["verification"]
    assert binding["acceptance_coverage"] == COVERS_A
    assert binding["verification_evidence_ids"] == [
        f"run:{proof['run_id']}:attempt:1:criterion:A:test:tests/test_m1.py::test_a",
    ]
    assert binding["toolchain"]["value"] == "toolchain-1"
    assert binding["model_runtime"] == "NOT_APPLICABLE"

    engine = _no_op_engine()
    result, _ = _run(git_workspace, ACCEPTED_CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]  # no Developer/model work, no mutation


def test_no_change_reuse_is_unverified_while_the_toolchain_identity_is_unavailable(git_workspace):  # noqa: F811
    # Production today: PRD-011 has not bound a toolchain identity, and the
    # evidence is test evidence - so the proof is issued but never reused.
    _run(git_workspace, ACCEPTED_CHAIN, _no_op_engine())
    proof = _proof(git_workspace, "M1")
    assert proof["kind"] == VERIFIED_NO_CHANGE and proof["verification"]["toolchain"]["value"] == "UNAVAILABLE"
    engine = _no_op_engine()
    result, _ = _run(git_workspace, ACCEPTED_CHAIN, engine)
    assert _decisions(result) == {
        "M1": ("UNVERIFIED", [TOOLCHAIN_IDENTITY_UNAVAILABLE]), "M2": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }
    assert engine.calls == ["M1", "M2", "INTEGRATION"]


def test_a_real_engine_no_change_milestone_converges(git_workspace):  # noqa: F811
    # In the real engine a "nothing to change" milestone commits its file
    # with identical bytes: a COMMITTED cycle that S4b proves byte-exactly.
    (git_workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "calc.py"], cwd=git_workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "calc"], cwd=git_workspace, check=True)
    milestones = [_milestone("M1")]
    same = '[{"filepath": "calc.py", "content": "def add(a, b):\\n    return a + b\\n"}]'
    integration = ["Step 1: integrate", "Design: calc.py", same, "Review: Approved"]
    engine, _ = _engine(_config(), ["Step 1", "Design: calc.py already has add", same, "Review: Approved"]
                        + integration)
    result, _ = _run(git_workspace, milestones, engine)
    assert result["status"] == "success", result
    engine, llm = _engine(_config(), list(integration))
    result, _ = _run(git_workspace, milestones, engine)
    assert _decisions(result) == {"M1": ("MATCH", [])}
    assert llm.complete.await_count == len(integration)  # only the integration pass ran
    # The real workflow reports its deterministic gates; this repository has
    # no tests, so the "no tests ran" regression pass is NO_TESTS_EXECUTED,
    # never positive evidence.
    assert result["integration_result"]["deterministic_gate_evidence"] == [
        {"type": "compile", "passed": True, "status": "PASSED", "attempt": 1},
        {"type": "regression_test", "passed": None, "status": "NO_TESTS_EXECUTED", "attempt": 1},
    ]


@pytest.mark.parametrize("change", ["verification_policy", "workspace_edit", "upstream_recompleted", "toolchain"])
def test_b_a_changed_dependency_invalidates_verified_no_change(git_workspace, toolchain_bound, change):  # noqa: F811
    milestones = [_accepted("M0"), _accepted("M1", ["M0"]), _accepted("M2", ["M1"])]
    outputs = {"M0": {"m0.py": b"M0 = 1\n"}, "M1": {}, "M2": {"m2.py": b"M2 = 1\n"}}
    _run(git_workspace, milestones, _no_op_engine(milestones=milestones, outputs=outputs))
    assert _proof(git_workspace, "M1")["kind"] == VERIFIED_NO_CHANGE
    config = AppConfig()
    if change == "verification_policy":
        config.autonomy.run_verification_enabled = not config.autonomy.run_verification_enabled
    elif change == "workspace_edit":
        (git_workspace / "unrelated.txt").write_text("user edit\n")
    elif change == "toolchain":
        toolchain_bound["value"] = "toolchain-2"
    else:
        # M0's proof now names a different completion than the one M1's
        # no-change verification was bound to.
        sidecar = Path(git_workspace, ".kriya", "milestones", f"{GROUP}.json")
        payload = json.loads(sidecar.read_text())
        payload["completion_proofs"]["M0"]["transaction_ids"] = ["redone"]
        sidecar.write_text(json.dumps(payload))
    engine = _no_op_engine(milestones=milestones, outputs=outputs, config=config)
    result, _ = _run(git_workspace, milestones, engine)
    assert _decisions(result)["M1"] == ("CHANGED", [VERIFIED_NO_CHANGE_INVALIDATED])
    assert _decisions(result)["M2"] == ("CHANGED", [UPSTREAM_INVALIDATED])
    assert "M1" in engine.calls


def _assert_not_issued(workspace, engine_factory, code, criteria=None):
    """M1 committed nothing and got no VERIFIED_NO_CHANGE proof, for
    ``code``; the next run says why and M1 reruns."""
    _run(workspace, ACCEPTED_CHAIN, engine_factory())
    proof = _proof(workspace, "M1")
    assert proof["kind"] != VERIFIED_NO_CHANGE and proof["verification"] is None
    assert proof["no_change_refusal"]["code"] == code
    if criteria is not None:
        assert proof["no_change_refusal"]["criteria"] == criteria
    engine = engine_factory()
    result, _ = _run(workspace, ACCEPTED_CHAIN, engine)
    assert _decisions(result)["M1"] == ("UNVERIFIED", [NO_COMMITTED_OUTPUT])
    [decision] = [d for d in result["milestone_reuse"]["decisions"] if d["milestone_id"] == "M1"]
    assert decision["reasons"][0]["cause"]["code"] == code
    assert engine.calls[0] == "M1"


UNRELATED_TEST = [dict(COVERS_A[0], criterion_id="Z", selector="tests/test_other.py::test_unrelated")]


@pytest.mark.parametrize("gates,coverage,status", [
    # A test passed, but no criterion of this milestone maps to it.
    (TESTS_PASSED, UNRELATED_TEST, "UNCOVERED"),
    (TESTS_PASSED, [], "UNCOVERED"),
    # Coverage claimed by a test gate that never executed in the run.
    ([{"type": "compile", "passed": True, "status": "PASSED", "attempt": 1}], COVERS_A, "UNAVAILABLE"),
    # Coverage from an earlier attempt (a different candidate).
    ([dict(TESTS_PASSED[0], attempt=2)], COVERS_A, "UNAVAILABLE"),
    # A failed covering test.
    (TESTS_PASSED, [dict(COVERS_A[0], status="FAILED")], "FAILED"),
])
def test_a_an_unrelated_or_unproven_passing_test_never_issues_verified_no_change(
    git_workspace, gates, coverage, status,  # noqa: F811
):
    _assert_not_issued(
        git_workspace, lambda: _no_op_engine(gates=gates, coverage=coverage),
        ACCEPTANCE_COVERAGE_INCOMPLETE, {"A": status},
    )


@pytest.mark.parametrize("claimed", ["NO_TESTS_EXECUTED", "PASS_WITH_TESTS"])
def test_b_zero_executed_tests_are_never_positive_evidence(git_workspace, claimed):  # noqa: F811
    # The real gate evidence of a test command that exited 0 after running
    # nothing, and a coverage item on it - even one claiming a pass.
    gates = deterministic_gate_evidence([
        {"attempt": 1, "type": "regression_test", "success": True,
         "output": "collected 0 items\n\n============ no tests ran in 0.00s ============"},
    ], 1)
    assert gates == [{"type": "regression_test", "passed": None, "status": "NO_TESTS_EXECUTED", "attempt": 1}]
    coverage = [dict(COVERS_A[0], kind="regression_test", status=claimed, tests_executed=0)]
    _assert_not_issued(
        git_workspace, lambda: _no_op_engine(gates=gates, coverage=coverage),
        ACCEPTANCE_COVERAGE_INCOMPLETE, {"A": "NO_TESTS_EXECUTED"},
    )


@pytest.mark.parametrize("gates", [[], [{"type": "compile", "passed": True, "status": "PASSED", "attempt": 1}]])
def test_c_a_model_only_no_change_never_becomes_reusable(git_workspace, gates):  # noqa: F811
    # quality gates "passed", the milestone's criteria are free text only
    # (no structured acceptance): the model's no-change verdict is all
    # there is.
    _run(git_workspace, CHAIN, GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": gates}, coverage={"M1": COVERS_A}))
    proof = load_milestone_run_state(str(git_workspace), GROUP).completion_proofs["M1"]
    assert proof["kind"] != VERIFIED_NO_CHANGE and proof["verification"] is None
    assert proof["no_change_refusal"]["code"] == ACCEPTANCE_COVERAGE_UNAVAILABLE
    engine = GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": gates})
    result, _ = _run(git_workspace, CHAIN, engine)
    assert _decisions(result)["M1"] == ("UNVERIFIED", [NO_COMMITTED_OUTPUT])
    assert engine.calls[0] == "M1"


def test_d_generated_output_never_invalidates_verified_no_change(git_workspace, toolchain_bound):  # noqa: F811
    # No .gitignore: only the repository model's generated-output
    # directories keep these out of the workspace evidence.
    assert not (git_workspace / ".gitignore").exists()
    _run(git_workspace, ACCEPTED_CHAIN, _no_op_engine())
    for relpath in ("__pycache__/m2.cpython-312.pyc", "pkg/__pycache__/x.cpython-312.pyc", "target/classes/A.class"):
        (git_workspace / relpath).parent.mkdir(parents=True, exist_ok=True)
        (git_workspace / relpath).write_bytes(b"\x00generated")
    engine = _no_op_engine()
    result, _ = _run(git_workspace, ACCEPTED_CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]


@pytest.mark.parametrize("change", [
    "new_untracked_source", "new_untracked_config", "tracked_file_in_generated_dir", "committed_file_in_generated_dir",
])
def test_e_relevant_untracked_or_tracked_changes_invalidate_verified_no_change(
    git_workspace, toolchain_bound, change,  # noqa: F811
):
    outputs = dict(NO_OP_OUTPUTS)
    if change == "tracked_file_in_generated_dir":
        (git_workspace / "build").mkdir()
        (git_workspace / "build" / "settings.gradle").write_text("tracked\n")
        subprocess.run(["git", "add", "build/settings.gradle"], cwd=git_workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "tracked build file"], cwd=git_workspace, check=True)
    if change == "committed_file_in_generated_dir":
        outputs["M2"] = {"bin/tool.sh": b"#!/bin/sh\n"}
    _run(git_workspace, ACCEPTED_CHAIN, _no_op_engine(outputs=outputs))
    assert _proof(git_workspace, "M1")["kind"] == VERIFIED_NO_CHANGE
    target = {
        "new_untracked_source": "src/new_module.py", "new_untracked_config": "config.yaml",
        "tracked_file_in_generated_dir": "build/settings.gradle", "committed_file_in_generated_dir": "bin/tool.sh",
    }[change]
    (git_workspace / target).parent.mkdir(parents=True, exist_ok=True)
    (git_workspace / target).write_text("changed by the user\n")
    engine = _no_op_engine(outputs=outputs)
    result, _ = _run(git_workspace, ACCEPTED_CHAIN, engine)
    assert _decisions(result)["M1"] == ("CHANGED", [VERIFIED_NO_CHANGE_INVALIDATED])
    assert engine.calls[0] == "M1"


# ---------------------------------------------------------------- 2. reconstruction

# M1 completes normally. M2 commits for real, then the process dies:
#   "after_commit" - os._exit right after the commit, before anything else;
#   "in_judge"     - os._exit in the post-success judge call, i.e. after the
#                    ledger was saved but before the completion proof.
_CRASH_AFTER_COMMIT = r'''
import asyncio, os, sys
sys.path.insert(0, sys.argv[3])
from _milestone_proof_harness import CHAIN, FakeEngine, _plan
from kriya.workflow.milestones import load_or_resume_milestone_run_state, run_milestones

workspace, where = sys.argv[1], sys.argv[2]

class Crashing(FakeEngine):
    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        result = await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)
        if milestone_index == 2 and where == "after_commit":
            os._exit(9)
        return result

engine = Crashing(CHAIN, {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2.py": b"M2 = 1\n"}})
real_judge = engine.run_verifier.judge
async def judge(**kwargs):
    if where == "in_judge" and kwargs.get("files_written") == ["m2.py"]:
        os._exit(9)
    return await real_judge(**kwargs)
engine.run_verifier.judge = judge
asyncio.run(run_milestones(engine, load_or_resume_milestone_run_state(workspace, _plan(CHAIN)), workspace))
os._exit(0)
'''


def _crash_after_commit(workspace, where):
    crashed = subprocess.run(
        [sys.executable, "-c", _CRASH_AFTER_COMMIT, str(workspace), where, TESTS_DIR],
        env=dict(os.environ, PYTHONPATH=TESTS_DIR), capture_output=True, text=True, timeout=120,
    )
    assert crashed.returncode == 9, crashed.stderr
    state = load_milestone_run_state(str(workspace), GROUP)
    assert state.completed_milestone_ids == ["M1"] and "M2" not in state.completion_proofs
    [record] = scan_run_records(str(workspace)).records
    [m2_cycle] = [c for c in record.commits if (c.get("work_unit") or {}).get("milestone_id") == "M2"]
    assert m2_cycle["result"] == "COMMITTED"
    return m2_cycle["transaction_id"]


@pytest.mark.parametrize("where", ["after_commit", "in_judge"])
def test_d_a_durable_commit_whose_completion_was_never_saved_is_reconstructed(tmp_path, where):
    workspace = _workspace(tmp_path)
    txid = _crash_after_commit(workspace, where)
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    assert result["status"] == "success"
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [COMPLETION_RECONSTRUCTED])}
    assert engine.calls == ["INTEGRATION"]  # M2 does not rerun
    [event] = [e for e in _events(result) if e["milestone_id"] == "M2"]
    assert event["code"] == COMPLETION_RECONSTRUCTED and event["transaction_ids"] == [txid]
    assert state.completion_proofs["M2"]["reconstructed_from"]["transaction_ids"] == [txid]
    # The rebuilt proof is an ordinary one from now on.
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result)["M2"] == ("MATCH", [COMPLETION_RECONSTRUCTED])


@pytest.mark.parametrize("damage,cause", [
    ("delete_evidence", COMMIT_EVIDENCE_MISSING),
    ("edit_output", OUTPUT_CHANGED),
])
def test_e_reconstruction_is_refused_when_the_evidence_does_not_prove_the_output(tmp_path, damage, cause):
    workspace = _workspace(tmp_path)
    txid = _crash_after_commit(workspace, "after_commit")
    if damage == "delete_evidence":
        os.unlink(os.path.join(commit_evidence_dir(str(workspace)), f"{txid}.json"))
    else:
        (workspace / "m2.py").write_bytes(b"edited after the crash\n")
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    status, codes = _decisions(result)["M2"]
    assert (status, codes) == ("UNVERIFIED", [COMPLETION_RECONSTRUCTION_UNVERIFIED])
    [decision] = [d for d in result["milestone_reuse"]["decisions"] if d["milestone_id"] == "M2"]
    assert decision["reasons"][0]["cause"]["code"] == cause
    assert engine.calls == ["M2", "INTEGRATION"]
    assert state.completion_proofs["M2"].get("reconstructed_from") is None


def test_e_corrupt_evidence_is_refused_by_the_gate_first(tmp_path):
    workspace = _workspace(tmp_path)
    txid = _crash_after_commit(workspace, "after_commit")
    Path(commit_evidence_dir(str(workspace)), f"{txid}.json").write_text("{not json")
    engine = FakeEngine(CHAIN, CHAIN_OUTPUTS)
    with pytest.raises(UncertainWorkspaceStateError):
        _run(workspace, CHAIN, engine)
    assert engine.calls == []


# ---------------------------------------------------------------- 2b. recovery-completed commits

def _recovered_mid_commit(tmp_path):
    """M2's commit killed between its two file replaces, then finished by
    `kriya runs recover --complete-partial`. Returns the workspace and the
    recovered run's record as recovery left it."""
    workspace = _workspace(tmp_path)
    _crash_mid_commit(workspace, "stage2")
    report = recover_workspace(str(workspace), complete_partial=True)
    assert report.rolled_forward and not report.errors, report.to_dict()
    [record] = scan_run_records(str(workspace)).records
    assert record.lifecycle_state == RunLifecycle.RECOVERED
    return workspace, record


def test_f_a_recovery_completed_milestone_is_reusable_and_its_run_stays_recovered(tmp_path):
    workspace, recovered = _recovered_mid_commit(tmp_path)
    engine = FakeEngine(CHAIN, MID_COMMIT_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    assert result["status"] == "success"
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [COMPLETION_RECONSTRUCTED])}
    assert engine.calls == ["INTEGRATION"]  # M2 is not regenerated
    [event] = [e for e in _events(result) if e["milestone_id"] == "M2"]
    assert event["code"] == COMPLETION_RECONSTRUCTED and event["completion_origin"] == ORIGIN_RECOVERY
    proof = state.completion_proofs["M2"]
    assert proof["completion_origin"] == ORIGIN_RECOVERY
    assert proof["reconstructed_from"]["run_id"] == recovered.run_id
    # The recovered run itself is untouched: still RECOVERED, never SUCCESS.
    after = next(r for r in scan_run_records(str(workspace)).records if r.run_id == recovered.run_id)
    assert after.lifecycle_state == RunLifecycle.RECOVERED
    assert (after.terminal_status, after.commit_result, after.revision) == (
        recovered.terminal_status, recovered.commit_result, recovered.revision,
    )
    assert after.terminal_status in ("NEEDS_REVIEW", "FAILURE")
    # And the rebuilt proof is an ordinary one from now on.
    engine = FakeEngine(CHAIN, MID_COMMIT_OUTPUTS)
    result, _ = _run(workspace, CHAIN, engine)
    assert _decisions(result)["M2"] == ("MATCH", [COMPLETION_RECONSTRUCTED])
    assert engine.calls == ["INTEGRATION"]


def _tamper_recovery(workspace, txid, **changes):
    path = os.path.join(commit_evidence_dir(str(workspace)), f"{txid}.json")
    evidence = load_commit_evidence(path)
    _persist_commit_evidence(str(workspace), dataclasses.replace(evidence, recovery=dict(evidence.recovery, **changes)))


@pytest.mark.parametrize("damage,cause", [
    ("delete_evidence", COMMIT_EVIDENCE_MISSING),
    # Evidence that still loads and still matches the candidate and bytes,
    # but whose recovery provenance does not prove an exact completion:
    # only the recovery-provenance check refuses these.
    ("foreign_operation", RECOVERY_PROVENANCE_UNVERIFIED),
    ("not_an_interrupted_commit", RECOVERY_PROVENANCE_UNVERIFIED),
    ("not_kriya_recovery", RECOVERY_PROVENANCE_UNVERIFIED),
])
def test_g_recovery_completion_with_unproven_evidence_is_refused_and_reruns(tmp_path, damage, cause):
    workspace, recovered = _recovered_mid_commit(tmp_path)
    [cycle] = [c for c in recovered.commits if (c.get("work_unit") or {}).get("milestone_id") == "M2"]
    txid = cycle["transaction_id"]
    if damage == "delete_evidence":
        os.unlink(os.path.join(commit_evidence_dir(str(workspace)), f"{txid}.json"))
    elif damage == "foreign_operation":
        _tamper_recovery(workspace, txid, operations=[
            {"target_path": "m2a.py", "classification": "APPLIED"},
            {"target_path": "m2b.py", "classification": "FOREIGN"},
        ], rolled_forward=[])
    elif damage == "not_an_interrupted_commit":
        _tamper_recovery(workspace, txid, prior_state="committed")
    else:
        _tamper_recovery(workspace, txid, tool="hand-edited")
    engine = FakeEngine(CHAIN, MID_COMMIT_OUTPUTS)
    result, state = _run(workspace, CHAIN, engine)
    assert _decisions(result)["M2"] == ("UNVERIFIED", [COMPLETION_RECONSTRUCTION_UNVERIFIED])
    [decision] = [d for d in result["milestone_reuse"]["decisions"] if d["milestone_id"] == "M2"]
    assert decision["reasons"][0]["cause"]["code"] == cause
    assert engine.calls == ["M2", "INTEGRATION"]
    assert state.completion_proofs["M2"]["completion_origin"] == "RUN"


# ---------------------------------------------------------------- 3. checkpoint selection

def _checkpoint(workspace, run_id, milestone, saved_offset=0.0):
    save_checkpoint(str(workspace), run_id, {
        "stage": "plan", "milestone_group_id": GROUP, "plan": f"plan for {milestone.id}",
        "work_unit": milestone_work_unit(GROUP, milestone),
    })
    if saved_offset:
        path = Path(workspace, ".kriya", "checkpoints", f"{run_id}.json")
        payload = json.loads(path.read_text())
        payload["saved_at"] += saved_offset
        path.write_text(json.dumps(payload))


def test_f_resume_offers_a_milestone_only_its_own_newest_checkpoint(tmp_path):
    workspace = _workspace(tmp_path)
    m1, m2 = CHAIN
    _checkpoint(workspace, "ckpt-m1-old", m1, saved_offset=-20)
    _checkpoint(workspace, "ckpt-m1", m1, saved_offset=-10)
    _checkpoint(workspace, "ckpt-m2-newest", m2)
    engine = GatedEngine(CHAIN, CHAIN_OUTPUTS)
    result, _ = _run(workspace, CHAIN, engine, resume=True)
    offered = dict(engine.kwargs)
    assert offered["M1"] == {"resume": False, "resume_id": "ckpt-m1"}
    assert offered["M2"] == {"resume": False, "resume_id": "ckpt-m2-newest"}
    selections = {e["milestone_id"]: e for e in _events(result) if e["code"] == MILESTONE_CHECKPOINT_SELECTED}
    assert selections["M1"]["checkpoint"] == "ckpt-m1"


def test_h_no_compatible_checkpoint_means_a_fresh_start_never_another_units(tmp_path):
    workspace = _workspace(tmp_path)
    m1, m2 = CHAIN
    _checkpoint(workspace, "ckpt-m2", m2)
    save_checkpoint(str(workspace), "legacy-no-identity", {"stage": "plan", "milestone_group_id": GROUP})
    engine = GatedEngine(CHAIN, {})
    result, _ = _run(workspace, CHAIN, engine, resume=True)
    offered = dict(engine.kwargs)
    assert offered["M1"] == {"resume": False, "resume_id": None}
    [m1_event] = [e for e in _events(result) if e["milestone_id"] == "M1"]
    assert m1_event["code"] == NO_COMPATIBLE_MILESTONE_CHECKPOINT


def test_h_an_explicit_resume_id_is_offered_only_to_its_own_milestone(tmp_path):
    workspace = _workspace(tmp_path)
    _checkpoint(workspace, "ckpt-m2", CHAIN[1])
    engine = GatedEngine(CHAIN, {})
    result, _ = _run(workspace, CHAIN, engine, resume_id="ckpt-m2")
    offered = dict(engine.kwargs)
    assert offered["M1"] == {"resume": False, "resume_id": None}
    assert offered["M2"] == {"resume": False, "resume_id": "ckpt-m2"}
    codes = {(e["milestone_id"], e["code"]) for e in _events(result)}
    assert ("M1", CHECKPOINT_IDENTITY_MISMATCH) in codes and ("M2", MILESTONE_CHECKPOINT_SELECTED) in codes


@pytest.mark.parametrize("workspace_changed", [False, True])
def test_g_the_selected_checkpoint_still_goes_through_the_prd008_validator(git_workspace, workspace_changed):  # noqa: F811
    cfg = _config()
    milestones = [_milestone("M1")]
    engine, _ = _engine(cfg, ["Step 1 (from the first run)", RuntimeError("architect crashed")])
    with pytest.raises(RuntimeError):
        _run(git_workspace, milestones, engine)
    first_runs = {record.run_id for record in scan_run_records(str(git_workspace)).records}
    [own] = [
        name[:-len(".json")] for name in os.listdir(git_workspace / ".kriya" / "checkpoints")
        if name.endswith(".json")
    ]
    # A newer checkpoint of a different unit must not be offered to M1.
    _checkpoint(git_workspace, "ckpt-other-unit", _milestone("M9"), saved_offset=60)
    if workspace_changed:
        (git_workspace / "extra.py").write_text("X = 1\n")
    planner = ["Step 1 (from the second run)"] if workspace_changed else []
    after_plan = [
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ]
    integration = ["Step 1: integrate", "Design: main.py", '[{"filepath": "main.py", "content": "print(1)"}]',
                   "Review: Approved"]
    engine, llm = _engine(cfg, planner + after_plan + integration)
    result, _ = _run(git_workspace, milestones, engine, resume=True)
    assert result["status"] == "success", result
    [selection] = [e for e in _events(result) if e["milestone_id"] == "M1"]
    assert (selection["code"], selection["checkpoint"]) == (MILESTONE_CHECKPOINT_SELECTED, own)
    [record] = [r for r in scan_run_records(str(git_workspace)).records if r.run_id not in first_runs]
    assert record.resume_decision is not None  # the validator judged the selected checkpoint
    if workspace_changed:
        assert "plan" not in record.resume_decision["reused"]
    else:
        assert "plan" in record.resume_decision["reused"]
    assert llm.complete.await_count == len(planner + after_plan + integration)


# ---------------------------------------------------------------- 4. shared-write lineage

SHARED = {"M1": {"pom.xml": b"<A/>\n", "m1.py": b"M1 = 1\n"}, "M2": {"pom.xml": b"<B/>\n"}}


def test_i_a_fully_proven_overwrite_keeps_both_milestones_valid(tmp_path):
    workspace = _completed_chain(tmp_path, SHARED)
    engine = FakeEngine(CHAIN, SHARED)
    result, _ = _run(workspace, CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]


def _m2_transaction(workspace):
    return load_milestone_run_state(str(workspace), GROUP).completion_proofs["M2"]["transaction_ids"][0]


def test_j_an_unproven_overwrite_never_excuses_the_earlier_milestone(tmp_path):
    workspace = _completed_chain(tmp_path, SHARED)
    txid = _m2_transaction(workspace)
    os.unlink(os.path.join(commit_evidence_dir(str(workspace)), f"{txid}.json"))
    engine = FakeEngine(CHAIN, SHARED)
    result, _ = _run(workspace, CHAIN, engine)
    decisions = result["milestone_reuse"]["decisions"]
    m1 = next(d for d in decisions if d["milestone_id"] == "M1")
    m2 = next(d for d in decisions if d["milestone_id"] == "M2")
    assert (m2["status"], [r["code"] for r in m2["reasons"]]) == ("UNVERIFIED", [COMMIT_EVIDENCE_MISSING])
    assert m1["status"] == "UNVERIFIED"
    assert [(r["code"], r["path"], r["transaction_id"]) for r in m1["reasons"]] == [
        (COMMIT_LINEAGE_UNVERIFIED, "pom.xml", txid),
    ]
    assert engine.calls == ["M1", "M2", "INTEGRATION"]


def test_j_corrupt_overwrite_evidence_is_refused_by_the_gate_first(tmp_path):
    workspace = _completed_chain(tmp_path, SHARED)
    Path(commit_evidence_dir(str(workspace)), f"{_m2_transaction(workspace)}.json").write_text("{bad")
    with pytest.raises(UncertainWorkspaceStateError):
        _run(workspace, CHAIN, FakeEngine(CHAIN, SHARED))


def test_j_a_reordered_ledger_cannot_rewrite_who_wrote_last(tmp_path):
    workspace = _completed_chain(tmp_path, SHARED)
    (workspace / "pom.xml").write_bytes(b"<A/>\n")  # user reverts to M1's bytes
    sidecar = Path(workspace, ".kriya", "milestones", f"{GROUP}.json")
    payload = json.loads(sidecar.read_text())
    payload["commit_ledger"][0], payload["commit_ledger"][1] = payload["commit_ledger"][1], payload["commit_ledger"][0]
    sidecar.write_text(json.dumps(payload))
    result, _ = _run(workspace, CHAIN, FakeEngine(CHAIN, SHARED))
    # Without the order check, M1 would "own" pom.xml and the revert of
    # M2's output would pass as MATCH for both.
    assert _decisions(result)["M2"] != ("MATCH", [])
    codes = {code for _, codes in _decisions(result).values() for code in codes}
    assert COMMIT_LINEAGE_UNVERIFIED in codes


def test_i_an_edit_to_a_path_the_integration_pass_owns_is_charged_to_its_milestone(tmp_path):
    milestones = [_milestone("M1"), _milestone("M2", ["M1"]), _milestone("M3", ["M2"])]
    outputs = {"M1": {"m1.py": b"1\n"}, "M2": {"pom.xml": b"<m2/>\n"}, "M3": {"m3.py": b"3\n"}}
    integration = {"pom.xml": b"<m2/><int/>\n"}
    workspace = _workspace(tmp_path)
    _run(workspace, milestones, FakeEngine(milestones, outputs, integration))
    clean, _ = _run(workspace, milestones, FakeEngine(milestones, outputs, integration))
    assert {status for status, _ in _decisions(clean).values()} == {"MATCH"}

    (workspace / "pom.xml").write_bytes(b"<user-edit/>\n")
    engine = FakeEngine(milestones, outputs, integration)
    result, _ = _run(workspace, milestones, engine)
    assert _decisions(result) == {
        "M1": ("MATCH", []), "M2": ("CHANGED", [CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE]),
        "M3": ("CHANGED", [UPSTREAM_INVALIDATED]),
    }
    [reason] = next(d for d in result["milestone_reuse"]["decisions"] if d["milestone_id"] == "M2")["reasons"]
    assert reason["path"] == "pom.xml" and reason["superseded_by"]  # the integration commit
    assert reason["divergence"] == OUTPUT_CHANGED
    assert engine.calls == ["M2", "M3", "INTEGRATION"]


def test_i_an_edit_to_a_path_a_failed_milestone_wrote_last_is_charged_to_the_completed_writer(tmp_path):
    milestones = [_milestone("M1"), _milestone("M2", ["M1"])]
    outputs = {"M1": {"shared.cfg": b"m1\n"}, "M2": {"shared.cfg": b"m2\n"}}

    class M2CommitsThenFails(FakeEngine):
        async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
            result = await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)
            return dict(result, quality_gates_passed=milestone_index != 2)

    workspace = _workspace(tmp_path)
    first, _ = _run(workspace, milestones, M2CommitsThenFails(milestones, outputs))
    assert first["status"] == "milestone_failed"
    (workspace / "shared.cfg").write_bytes(b"user\n")
    result, _ = _run(workspace, milestones, FakeEngine(milestones, outputs))
    assert _decisions(result) == {"M1": ("CHANGED", [CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE])}


# ---------------------------------------------------------------- current workspace is the source of truth

def test_k_a_stale_milestone_reruns_on_the_users_bytes_never_restored(tmp_path):
    workspace = _completed_chain(tmp_path)
    (workspace / "m1.py").write_bytes(b"USER EDIT\n")
    seen = {}

    class Observing(FakeEngine):
        async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
            if milestone_index == 1:
                seen["m1.py"] = Path(workspace_path, "m1.py").read_bytes()
            return await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)

    result, _ = _run(workspace, CHAIN, Observing(CHAIN, CHAIN_OUTPUTS))
    assert _decisions(result)["M1"] == ("CHANGED", [OUTPUT_CHANGED])
    assert seen["m1.py"] == b"USER EDIT\n"  # Kriya did not roll the user's edit back


# ---------------------------------------------------------------- gate evidence

def test_gate_evidence_counts_only_the_final_attempt_and_never_unconfirmed_gates():
    outcomes = [
        # Attempt 1 ran targeted tests against a different candidate, then failed.
        {"attempt": 1, "type": "targeted_test", "success": True, "output": "2 passed"},
        {"attempt": 1, "type": "compile", "success": False, "output": "error"},
        # Attempt 2 (final): compile passed; tests were an unconfirmed skip.
        {"attempt": 2, "type": "compile", "success": True, "output": "ok"},
        {"attempt": 2, "type": "test", "success": True, "output": "No test runner available for this stack"},
        {"attempt": 2, "type": "regression_test", "success": True, "output": "5 passed"},
    ]
    assert deterministic_gate_evidence(outcomes, 2) == [
        {"type": "compile", "passed": True, "status": "PASSED", "attempt": 2},
        {"type": "test", "passed": None, "status": "UNAVAILABLE", "attempt": 2},
        {"type": "regression_test", "passed": True, "status": "PASS_WITH_TESTS", "attempt": 2},
    ]
    assert deterministic_gate_evidence(outcomes, None) == []


def test_gate_evidence_never_counts_a_test_run_that_executed_zero_tests():
    # Real pytest output from a repository with no tests: success, and
    # nothing verified. Seen live in the real engine's regression gate.
    vacuous = "collected 0 items\n\n============================ no tests ran in 0.00s ======"
    assert deterministic_gate_evidence([
        {"attempt": 1, "type": "regression_test", "success": True, "output": vacuous},
        {"attempt": 1, "type": "compile", "success": False, "output": "SyntaxError"},
    ], 1) == [
        {"type": "compile", "passed": False, "status": "FAILED", "attempt": 1},
        {"type": "regression_test", "passed": None, "status": "NO_TESTS_EXECUTED", "attempt": 1},
    ]
