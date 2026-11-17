"""S8 (C-07, ruling 9): accepted unit work of a stopped enforce plan is resumable only through its durable snapshot, exactly bound and revalidated.

One scripted plan through the REAL controller (tests/_enforce_unit_harness.py): three units, each creating one file. Run 1 accepts ``s1`` and ``s2`` in the plan
sandbox and stops on ``s3`` (its test gate never passes); the sandbox is removed. Run 2 resumes.

Characterization (pre-fix, MEASURED at b6ac020 with this same scenario, ``~/kriya-evidence/s8-b6ac020/prefix_accepted_work.txt``): the resume answered
``COMPLETIONS_NOT_IN_WORKSPACE`` and asked the Developer for s1 and s2 again. After S8 they are REPLAYED: the snapshot's bytes are staged by the candidate
writer and judged by each unit's own gates in the fresh sandbox, with no Developer call.

The negative controls each change ONE thing between the runs and assert that nothing accepted is reused (the Developer is asked again), never that a stale state
succeeds silently.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from _enforce_unit_harness import PASSING, Run, run_enforce

from kriya.control.persistence import accepted_candidate_path, control_state_path, load_control_state, work_ledger_path
from kriya.workflow.plan_schema import EngineeringPlan

A, B, C = "shop/a.py", "shop/b.py", "shop/c.py"
SOURCES = {A: "def a():\n    return 1\n", B: "def b():\n    return 2\n", C: "def c():\n    return 3\n"}
BROKEN_C = "def c():\n    return 'BROKEN'\n"
FAILING = {"success": False, "output": "shop/test_c.py F\nE   AssertionError: assert c() == 3\nshop/c.py:2: AssertionError\n1 failed in 0.01s"}


def _plan(description: str = "write") -> EngineeringPlan:
    def unit(sid: str, path: str, **more: Any) -> Dict[str, Any]:
        return {"id": sid, "description": f"{description} {path}", "execution_method": "model", "planned_files": [{"path": path, "action": "create"}],
                "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}], **more}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [unit("s1", A, provides=["a"]), unit("s2", B, depends_on=["s1"], requires=["a"], provides=["b"]),
                     unit("s3", C, depends_on=["s2"], requires=["b"])]})


def _tests_gate(validator: Any) -> Dict[str, Any]:
    tree = Path(validator.workspace_path)
    return FAILING if (tree / C).exists() and "BROKEN" in (tree / C).read_text() else PASSING


def _first_run(tmp_path: Path) -> Run:
    """s1 and s2 are accepted; s3 never passes its gate: the plan stops and its sandbox is removed."""
    run = run_enforce(tmp_path, plan=_plan(), goal="Write a, b and c", mode="guardrails",
                      units={"s1": (A, SOURCES[A]), "s2": (B, SOURCES[B]), "s3": (C, BROKEN_C)},
                      on_tests=_tests_gate, base_files={"shop/__init__.py": "", "README.md": "base\n"})
    assert run.result.legacy_result["status"] != "success"
    assert not (run.workspace / A).exists() and not (run.workspace / B).exists(), "a stopped plan commits nothing"
    return run


def _resume(tmp_path: Path, first: Run, *, plan: Optional[EngineeringPlan] = None, goal: str = "Write a, b and c") -> Run:
    return run_enforce(tmp_path, plan=plan or _plan(), goal=goal, mode="guardrails",
                       units={"s1": (A, SOURCES[A]), "s2": (B, SOURCES[B]), "s3": (C, SOURCES[C])},
                       on_tests=_tests_gate, existing_workspace=first.workspace, execute_kwargs={"resume": True})


def _decision(run: Run) -> Dict[str, Any]:
    return run.result.legacy_result.get("resume_decision") or {}


def _snapshot(workspace: Path) -> Dict[str, Any]:
    return json.loads(Path(accepted_candidate_path(str(workspace))).read_text())


def test_the_first_run_leaves_the_ordered_pair_snapshot_then_reference(tmp_path):
    first = _first_run(tmp_path)
    state = load_control_state(str(first.workspace))
    snapshot = _snapshot(first.workspace)
    # the durable half names exactly the two accepted units, with their exact bytes; the failed unit is not in it
    assert sorted(snapshot["units"]) == ["s1", "s2"]
    assert snapshot["units"]["s1"]["files"][0]["content"] == SOURCES[A]
    assert snapshot["plan_hash"] == state.current_plan_hash
    # the reference half names the same digests, and survived the discard that reset the completion states
    assert state.accepted_units == {unit: snapshot["units"][unit]["digest"] for unit in ("s1", "s2")}
    assert state.subtask_completion_scope == "candidate"
    assert all(status != "completed" for status in state.subtask_states.values())


def test_exact_binding_replays_the_accepted_units_without_the_developer_and_commits(tmp_path):
    first = _first_run(tmp_path)
    assert first.developer_calls.count("s1") == 1 and first.developer_calls.count("s2") == 1
    resumed = _resume(tmp_path, first)
    legacy = resumed.result.legacy_result
    # The whole candidate was committed. (This scripted plan declares no requirement truth, so the S6 result contract names the applied change
    # APPLIED_REQUIREMENTS_OPEN rather than success; what S8 proves here is the commit and how its bytes were produced.)
    assert legacy["status"] in ("success", "applied_requirements_open"), legacy.get("reason_codes")
    assert _decision(resumed)["reason"] == "ACCEPTED_CANDIDATE_REPLAY"
    assert _decision(resumed)["replayable_subtasks"] == ["s1", "s2"] and _decision(resumed)["work_ledger"] == "CONTINUED"
    # no Developer request for the replayed units; the unit that never passed is generated
    assert "s1" not in resumed.developer_calls and "s2" not in resumed.developer_calls
    assert resumed.developer_calls.count("s3") == 1
    # the replayed bytes went through each unit's own gates again (the tests gate ran for them in THIS run) and the commit is the whole candidate
    assert len([kind for kind, _ in resumed.builds if kind == "tests"]) >= 3
    for path, source in SOURCES.items():
        assert (resumed.workspace / path).read_text() == source
    # once committed the completions are the workspace's: nothing stays replayable
    state = load_control_state(str(resumed.workspace))
    assert state.accepted_units == {} and _snapshot(resumed.workspace)["units"] == {}


def _assert_nothing_reused(resumed: Run, *, reason: Optional[str] = None) -> None:
    assert resumed.developer_calls.count("s1") >= 1 and resumed.developer_calls.count("s2") >= 1, resumed.developer_calls
    assert not _decision(resumed).get("replayable_subtasks")
    if reason is not None:
        assert _decision(resumed)["reason"] == reason, _decision(resumed)


def test_one_changed_snapshot_byte_is_a_digest_mismatch_and_the_unit_is_generated_again(tmp_path):
    first = _first_run(tmp_path)
    path = Path(accepted_candidate_path(str(first.workspace)))
    document = json.loads(path.read_text())
    document["units"]["s1"]["files"][0]["content"] = SOURCES[A].replace("1", "7")
    path.write_text(json.dumps(document))
    resumed = _resume(tmp_path, first)
    decision = _decision(resumed)
    assert decision["replay_refused"] == {"s1": "ACCEPTED_SNAPSHOT_BYTES_CHANGED"} and decision["replayable_subtasks"] == ["s2"]
    assert resumed.developer_calls.count("s1") == 1 and "s2" not in resumed.developer_calls
    assert (resumed.workspace / A).read_text() == SOURCES[A], "the tampered bytes never reach the workspace"


def test_a_changed_plan_reuses_nothing(tmp_path):
    first = _first_run(tmp_path)
    resumed = _resume(tmp_path, first, plan=_plan(description="author"))
    _assert_nothing_reused(resumed, reason="PLAN_CHANGED")


def test_a_changed_workspace_base_reuses_nothing_of_the_changed_unit(tmp_path):
    first = _first_run(tmp_path)
    (first.workspace / A).write_text("# someone else created this file meanwhile\n")     # s1's base was "does not exist"
    resumed = _resume(tmp_path, first)
    assert _decision(resumed)["replay_refused"].get("s1") == "ACCEPTED_SNAPSHOT_BASE_CHANGED"
    assert "s1" not in (_decision(resumed).get("replayable_subtasks") or [])


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_missing_or_corrupt_snapshot_reuses_nothing_and_never_succeeds_silently(tmp_path, damage):
    first = _first_run(tmp_path)
    path = Path(accepted_candidate_path(str(first.workspace)))
    path.unlink() if damage == "missing" else path.write_text("{not json")
    resumed = _resume(tmp_path, first)
    _assert_nothing_reused(resumed, reason="COMPLETIONS_NOT_IN_WORKSPACE")
    assert set(_decision(resumed)["replay_refused"].values()) == {"ACCEPTED_SNAPSHOT_MISSING"}


def test_a_snapshot_without_its_reference_is_ignored(tmp_path):
    """The snapshot was written but the state that names it was not (the crash between the two writes): the unnamed unit is not reused."""
    first = _first_run(tmp_path)
    path = Path(control_state_path(str(first.workspace)))
    document = json.loads(path.read_text())
    document["accepted_units"].pop("s2")
    path.write_text(json.dumps(document))
    resumed = _resume(tmp_path, first)
    assert _decision(resumed)["replayable_subtasks"] == ["s1"]
    assert "s1" not in resumed.developer_calls and resumed.developer_calls.count("s2") == 1


def test_a_reference_without_its_snapshot_entry_is_never_trusted(tmp_path):
    """The state says accepted but the snapshot does not hold the unit: ControlState alone proves nothing."""
    first = _first_run(tmp_path)
    path = Path(accepted_candidate_path(str(first.workspace)))
    document = json.loads(path.read_text())
    document["units"].pop("s2")
    path.write_text(json.dumps(document))
    resumed = _resume(tmp_path, first)
    assert _decision(resumed)["replay_refused"] == {"s2": "ACCEPTED_SNAPSHOT_UNIT_MISSING"}
    assert resumed.developer_calls.count("s2") == 1


@pytest.mark.parametrize("damage, reason", [
    ("missing", "WORK_LEDGER_MISSING"), ("corrupt", "WORK_LEDGER_UNREADABLE"), ("not_json", "WORK_LEDGER_UNREADABLE"), ("other_work", "WORK_LEDGER_NOT_THIS_WORK"),
])
def test_a_ledger_that_cannot_be_continued_refuses_the_resume_before_planning(tmp_path, damage, reason):
    """Owner ruling 2026-11-17: a requested resume continues the authoritative ledger or fails closed with a typed refusal. Pre-fix (MEASURED on the uncommitted S8
    tree): with the ledger deleted the resume answered ``work_ledger: WORK_LEDGER_MISSING`` and ran the whole plan under a NEW ledger (a fresh hour, fresh
    attempts, fresh unit ceilings); a corrupt or foreign ledger logged a warning and did the same."""
    first = _first_run(tmp_path)
    workspace = str(first.workspace)
    path = Path(work_ledger_path(workspace))
    document = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    elif damage == "not_json":
        path.write_text("{")
    else:
        document.update({"corrupt": {"developer_attempts": "many"}, "other_work": {"runs": ["some-other-run"]}}[damage])
        path.write_text(json.dumps(document))
    kept = {name: (Path(file).read_bytes() if Path(file).exists() else None)
            for name, file in (("ledger", path), ("state", control_state_path(workspace)), ("snapshot", accepted_candidate_path(workspace)))}
    resumed = _resume(tmp_path, first)
    legacy = resumed.result.legacy_result
    assert legacy["status"] == "resume_refused" and legacy["reason_codes"] == [reason] and legacy["quality_gates_passed"] is False
    assert legacy["error"].startswith(f"{reason}: resume refused") and legacy["files"] == []
    assert resumed.planner_calls == 0 and resumed.developer_calls == [] and resumed.builds == [], "refused before planning: no model call, no unit, no gate"
    for name, file in (("ledger", path), ("state", control_state_path(workspace)), ("snapshot", accepted_candidate_path(workspace))):
        assert (Path(file).read_bytes() if Path(file).exists() else None) == kept[name], f"the refused resume changed the {name}"
    assert not (resumed.workspace / A).exists() and not (resumed.workspace / C).exists()


def test_a_resume_with_no_control_state_left_runs_the_plan_again_under_the_same_ledger(tmp_path):
    """Nothing identifies the earlier work (its ControlState is gone), the ledger is intact: the plan runs from its first unit and the ledger is continued."""
    first = _first_run(tmp_path)
    before = json.loads(Path(work_ledger_path(str(first.workspace))).read_text())
    Path(control_state_path(str(first.workspace))).unlink()
    resumed = _resume(tmp_path, first)
    legacy = resumed.result.legacy_result   # (the harness closes no requirement: the S6 contract reports the committed plan as ``applied_requirements_open``)
    assert legacy["status"] in ("success", "applied_requirements_open") and legacy.get("resume_decision") is None
    assert (resumed.workspace / C).read_text() == SOURCES[C], "the plan ran to its commit"
    assert {"s1", "s2", "s3"} <= set(resumed.developer_calls)
    after = json.loads(Path(work_ledger_path(str(resumed.workspace))).read_text())
    assert after["ledger_id"] == before["ledger_id"] and after["runs"][:-1] == before["runs"]
    assert after["unit_invocations"]["s1"] == before["unit_invocations"]["s1"] + 1
    assert after["developer_attempts"] == before["developer_attempts"] + len(resumed.developer_calls)


def test_a_deleted_snapshot_under_a_valid_ledger_recomputes_the_work_and_refunds_nothing(tmp_path):
    """Negative 5: accepted-work loss and budget authority are separate facts. The snapshot is gone, the ledger is intact: every unit is generated again, and the
    resumed run's invocations and attempts are added to the earlier run's on the same ledger."""
    first = _first_run(tmp_path)
    before = json.loads(Path(work_ledger_path(str(first.workspace))).read_text())
    Path(accepted_candidate_path(str(first.workspace))).unlink()
    resumed = _resume(tmp_path, first)
    _assert_nothing_reused(resumed, reason="COMPLETIONS_NOT_IN_WORKSPACE")
    assert _decision(resumed)["work_ledger"] == "CONTINUED" and resumed.planner_calls == 1
    after = json.loads(Path(work_ledger_path(str(resumed.workspace))).read_text())
    assert after["ledger_id"] == before["ledger_id"] and after["runs"][:-1] == before["runs"] and len(after["runs"]) == len(before["runs"]) + 1
    for unit in ("s1", "s2", "s3"):
        assert after["unit_invocations"][unit] == before["unit_invocations"][unit] + resumed.developer_calls.count(unit) >= before["unit_invocations"][unit] + 1
    assert after["developer_attempts"] == before["developer_attempts"] + len(resumed.developer_calls)
    assert after["consumed_seconds"] >= before["consumed_seconds"]
    for unit, state in before["retry"].items():
        for signature in state.get("seen_failure_signatures", []):
            assert signature in after["retry"][unit]["seen_failure_signatures"]


def test_a_replay_the_gates_refuse_now_is_not_accepted_the_unit_is_generated_again(tmp_path):
    """Revalidation is the unit's own gates, not the snapshot's word: bytes that are exactly the recorded ones but no longer pass are not reused."""
    first = _first_run(tmp_path)
    refuse: List[str] = ["s1"]

    def gate(validator: Any) -> Dict[str, Any]:
        tree = Path(validator.workspace_path)
        if refuse and (tree / A).exists() and not (tree / B).exists():
            refuse.pop()
            return {"success": False, "output": "shop/test_a.py F\nE   AssertionError: a() changed\nshop/a.py:2: AssertionError\n1 failed"}
        return _tests_gate(validator)

    resumed = run_enforce(tmp_path, plan=_plan(), goal="Write a, b and c", mode="guardrails",
                          units={"s1": (A, SOURCES[A]), "s2": (B, SOURCES[B]), "s3": (C, SOURCES[C])},
                          on_tests=gate, existing_workspace=first.workspace, execute_kwargs={"resume": True})
    assert resumed.developer_calls.count("s1") == 1, "the replay was refused by the gate, so the Developer was asked"
    assert "s2" not in resumed.developer_calls
