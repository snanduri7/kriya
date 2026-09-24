"""PRD-008 S4c: milestone resume closure.

1. VERIFIED_NO_CHANGE - a milestone that committed nothing is reusable only
   on deterministic gate evidence (never a model's "no change"), bound to the
   verification policy, the completions it built on, and the workspace as the
   verified commit history explains it.
2. A crash between a milestone's durable commit and its completion save is
   closed by rebuilding the proof from the RunRecord's attributed cycle +
   commit evidence - and refused (the milestone reruns) whenever that
   evidence does not prove the exact committed output.
3. `--resume` selects a milestone's OWN newest checkpoint by work-unit
   identity; the PRD-008 validator still decides whether it is reusable.
4. Shared-file lineage: an earlier milestone is never excused by a later
   overwrite that cannot itself be proven.

Harness (FakeEngine, _run, ...) is S4b's: real commits through the one
commit seam, milestone state reloaded from the sidecar like the CLI.
"""
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
    TESTS_DIR,
    FakeEngine,
    _completed_chain,
    _config,
    _decisions,
    _engine,
    _milestone,
    _run,
    _workspace,
    git_workspace,  # noqa: F401 - pytest fixture
)

from kriya.config.config import AppConfig
from kriya.control.commit_state import UncertainWorkspaceStateError
from kriya.control.persistence import scan_run_records
from kriya.workflow.checkpoint import save_checkpoint
from kriya.workflow.edit_safety import commit_evidence_dir
from kriya.workflow.milestone_completion import (
    CHECKPOINT_IDENTITY_MISMATCH,
    COMMIT_EVIDENCE_MISSING,
    COMMIT_LINEAGE_UNVERIFIED,
    COMPLETION_RECONSTRUCTED,
    COMPLETION_RECONSTRUCTION_UNVERIFIED,
    MILESTONE_CHECKPOINT_SELECTED,
    NO_COMMITTED_OUTPUT,
    NO_COMPATIBLE_MILESTONE_CHECKPOINT,
    OUTPUT_CHANGED,
    UPSTREAM_INVALIDATED,
    VERIFIED_NO_CHANGE,
    VERIFIED_NO_CHANGE_INVALIDATED,
    milestone_work_unit,
)
from kriya.workflow.milestones import load_milestone_run_state

TESTS_PASSED = [{"type": "test", "passed": True, "attempt": 1}]


class GatedEngine(FakeEngine):
    """FakeEngine whose workflow result also reports deterministic gate
    evidence per milestone (as run_generation_workflow does), and which
    carries a real config for the verification-policy fingerprint."""

    def __init__(self, milestones, outputs, gates=None, integration=None, config=None):
        super().__init__(milestones, outputs, integration)
        self.gates = gates or {}
        self.kernel = MagicMock()
        self.kernel.config = config or AppConfig()
        self.kwargs = []

    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        name = self.order[milestone_index - 1] if milestone_index <= len(self.order) else "INTEGRATION"
        self.kwargs.append((name, {key: kwargs.get(key) for key in ("resume", "resume_id")}))
        result = await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)
        result["deterministic_gate_evidence"] = self.gates.get(name, [])
        return result


def _events(result):
    return result["milestone_reuse"]["events"]


# ---------------------------------------------------------------- 1. VERIFIED_NO_CHANGE

NO_OP_OUTPUTS = {"M1": {}, "M2": {"m2.py": b"M2 = 1\n"}}


def test_a_stable_no_op_milestone_is_verified_no_change_and_skipped(git_workspace):  # noqa: F811
    _run(git_workspace, CHAIN, GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": TESTS_PASSED}))
    proof = load_milestone_run_state(str(git_workspace), GROUP).completion_proofs["M1"]
    assert proof["kind"] == VERIFIED_NO_CHANGE and proof["transaction_ids"] == []
    assert proof["verification"]["gate_evidence"] == TESTS_PASSED

    engine = GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": TESTS_PASSED})
    result, _ = _run(git_workspace, CHAIN, engine)
    assert _decisions(result) == {"M1": ("MATCH", []), "M2": ("MATCH", [])}
    assert engine.calls == ["INTEGRATION"]  # no Developer/model work, no mutation


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


@pytest.mark.parametrize("change", ["verification_policy", "workspace_edit", "upstream_recompleted"])
def test_b_a_changed_dependency_invalidates_verified_no_change(git_workspace, change):  # noqa: F811
    milestones = [_milestone("M0"), _milestone("M1", ["M0"]), _milestone("M2", ["M1"])]
    outputs = {"M0": {"m0.py": b"M0 = 1\n"}, "M1": {}, "M2": {"m2.py": b"M2 = 1\n"}}
    _run(git_workspace, milestones, GatedEngine(milestones, outputs, gates={"M1": TESTS_PASSED}))
    config = AppConfig()
    if change == "verification_policy":
        config.autonomy.run_verification_enabled = not config.autonomy.run_verification_enabled
    elif change == "workspace_edit":
        (git_workspace / "unrelated.txt").write_text("user edit\n")
    else:
        # M0's proof now names a different completion than the one M1's
        # no-change verification was bound to.
        sidecar = Path(git_workspace, ".kriya", "milestones", f"{GROUP}.json")
        payload = json.loads(sidecar.read_text())
        payload["completion_proofs"]["M0"]["transaction_ids"] = ["redone"]
        sidecar.write_text(json.dumps(payload))
    engine = GatedEngine(milestones, outputs, gates={"M1": TESTS_PASSED}, config=config)
    result, _ = _run(git_workspace, milestones, engine)
    assert _decisions(result)["M1"] == ("CHANGED", [VERIFIED_NO_CHANGE_INVALIDATED])
    assert _decisions(result)["M2"] == ("CHANGED", [UPSTREAM_INVALIDATED])
    assert "M1" in engine.calls


@pytest.mark.parametrize("gates", [[], [{"type": "compile", "passed": True, "attempt": 1}]])
def test_c_a_model_only_no_change_never_becomes_reusable(git_workspace, gates):  # noqa: F811
    # quality gates "passed" but no behavioural gate actually ran: the
    # model's no-change verdict is all there is.
    _run(git_workspace, CHAIN, GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": gates}))
    proof = load_milestone_run_state(str(git_workspace), GROUP).completion_proofs["M1"]
    assert proof["kind"] != VERIFIED_NO_CHANGE and proof["verification"] is None
    engine = GatedEngine(CHAIN, NO_OP_OUTPUTS, gates={"M1": gates})
    result, _ = _run(git_workspace, CHAIN, engine)
    assert _decisions(result)["M1"] == ("UNVERIFIED", [NO_COMMITTED_OUTPUT])
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
