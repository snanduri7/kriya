"""S18d P2b (OI-150 a, fix-forward of packet P2): the per-unit written-files record is CUMULATIVE over the run's candidate.

The OI-150 (a) read view (kriya.workflow.subtask_executor.reads_candidate_view) chooses the owner baseline or the
candidate from ControlState.subtask_written_files and reads an EMPTY entry as "this unit changed nothing". The record is
produced by the enforce loop (kriya/workflow/enforce_loop.py). Invariant (the owner decision R2): a unit's entry is the
union of every file that any invocation of that unit in this run COMMITTED to the run's candidate (its first run, an
owner recovery, a verification reopen, a consumer retry, a scope recovery). A later invocation's report never replaces
it, an uncommitted (failed, rolled-back) report never grows it, and only a candidate boundary starts it over (a new
run's ControlState, the discard reset).

Defect (found by the blind TEST-B agent, re-derived here on the unfixed bytes): owner-recovery finalization REPLACED the
owner's entry with the latest recovery call's files, so a VERIFY-only recovery (carried forward with files []) or a
MUST_CHANGE recovery of a subset erased the owner's first-run change, and a planned read depending on the owner read the
owner baseline, where the file does not exist.

Seams: the T6 enforce harness (tests/_t6_harness.enforce_run: the model runtime scripted, the Planner's structured
parse stubbed to the plan, the REAL validate_plan, candidate worktree, terminal gates and commit) with each unit's
run_generation_workflow scripted (tests/test_e12_enforce_characterization.py::_scripted - it writes into the workspace it
is given and returns a result dict); the writer tests use the e12 in-place harness (_enforce_scripted). Both use the
PRODUCTION recovery-plan builder (kriya.workflow.owner_recovery): no recovery plan is injected.
"""
import ast
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from _plugin_test_support import load_core_tools_module
from test_e12_enforce_characterization import (
    _COMPILE,
    _NO_CHANGE_POSSIBLE,
    _enforce_engine,
    _enforce_scripted,
    _insufficient,
    _owner_two_files,
    _plan_of,
    _scripted,
    _unit,
)

from kriya.workflow import subtask_executor
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, ExecutionRole, FileAction, PlannedFile, Subtask

C = "kriya.workflow.workflow_controller"
REPO = Path(__file__).resolve().parent.parent
_GATE = [{"type": "tool", "tool_name": "compile", "description": "compile"}]


def _read_after_the_owner():
    """s0 creates dep0.py and dep1.py; s1 (a.py) depends on s0; t2 is a planned filesystem read of dep0.py whose only
    dependency is s0 - it reasons about the candidate as s0 left it. A plan the real validate_plan accepts."""
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "subtasks": [
            {"id": "s0", "description": "write the deps", "execution_method": "model",
             "planned_files": [{"path": "dep0.py", "action": "create"}, {"path": "dep1.py", "action": "create"}],
             "provides": ["deps"], "relevant_global_invariant_ids": ["gi1"], "verification": _GATE},
            {"id": "s1", "description": "write a", "execution_method": "model",
             "planned_files": [{"path": "a.py", "action": "create"}], "requires": ["deps"], "provides": ["a_written"],
             "depends_on": ["s0"], "relevant_global_invariant_ids": ["gi1"], "verification": _GATE},
            {"id": "t2", "description": "read dep0", "execution_method": "tool", "tool_name": "filesystem",
             "tool_arguments": {"operation": "read", "path": "dep0.py"}, "depends_on": ["s0"],
             "provides": ["dep0_shown"], "relevant_global_invariant_ids": ["gi1"]},
        ]})


def _candidate_run(tmp_path, monkeypatch, conflict, script=None):
    """A real enforce run (T6 harness) over a git owner workspace: the run makes its real candidate worktree, every
    scripted unit writes into it, and t2 runs through the production executor with the real FilesystemTool. Returns
    what t2 observed at its own execution and the run's result."""
    import _t6_harness

    seen, calls = {}, []
    real_execute = subtask_executor.execute

    async def observe(*args, **kwargs):
        if kwargs["subtask"].id == "t2":
            roots = kwargs["tool_roots"]
            seen["candidate_writes"] = {k: list(v) for k, v in kwargs["candidate_writes"].items()}
            seen["roots"] = roots
            # The baseline divergence that proves which view the read used: dep0.py exists only in the candidate.
            seen["dep0_in_owner"] = os.path.exists(os.path.join(roots[0], "dep0.py"))
            seen["dep0_in_candidate"] = os.path.exists(os.path.join(roots[1], "dep0.py"))
            result = await real_execute(*args, **kwargs)
            seen["t2"] = result
            return result
        return await real_execute(*args, **kwargs)

    def prepare(engine):
        engine.run_generation_workflow = _scripted(tmp_path, calls, {("s1", "planned"): [conflict], **(script or {})})

    with patch.object(subtask_executor, "execute", side_effect=observe):
        observed = _t6_harness.enforce_run(
            tmp_path, monkeypatch, lambda role, request: "{}", {"README.md": "seed\n"}, "Create a.py",
            [_read_after_the_owner], tools={"filesystem": load_core_tools_module().FilesystemTool()},
            prepare_engine=prepare, read_evidence=False)
    seen["roles"] = [c["execution_scope"] for c in calls]
    seen["writes_in"] = sorted({c["workspace_path"] for c in calls})
    return seen, observed.result


def _advisory_conflict():
    """The one ungrounded scope-conflict shape (owner_recovery._participant_evidence_is_grounded): the production
    recovery builder gives its generation-0 participants VERIFY, and the enforce loop carries the group forward without
    calling the Developer (files [])."""
    conflict = _insufficient()
    conflict["plan_scope_conflict"]["failure_type"] = "attribution_rejected"
    return conflict


def _assert_the_read_saw_the_candidate(seen, result):
    assert len(seen["roots"]) == 2 and seen["roots"][0] != seen["roots"][1], "a real candidate worktree"
    assert seen["writes_in"] == [seen["roots"][1]], "every scripted unit wrote into the candidate"
    assert (seen["dep0_in_owner"], seen["dep0_in_candidate"]) == (False, True), "the baseline diverges"
    assert seen["t2"].status.value == "completed", seen["t2"].error
    assert seen["t2"].tool_output.startswith("# dep0.py by s0"), seen["t2"].tool_output
    assert result.legacy_result["status"] == "success", result.legacy_result


def test_a_verify_only_recovery_keeps_the_owners_change_and_the_read_sees_the_candidate(tmp_path, monkeypatch, caplog):
    """THE TEST-B SCENARIO (R6). Unfixed bytes, MEASURED: candidate_writes {s0: [], s1: [a.py]}, t2 read the owner
    baseline and FAILED 'dep0.py does not exist', the run stopped failed."""
    caplog.set_level(logging.INFO, logger=C)
    seen, result = _candidate_run(tmp_path, monkeypatch, _advisory_conflict())
    # The trigger really ran: the owner was carried forward VERIFY-only, never re-invoked.
    assert seen["roles"] == ["subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=s1 role=consumer_retry"]
    assert any("RECOVERY_VERIFY_CARRIED_FORWARD" in r.getMessage() and "owner=s0" in r.getMessage()
               for r in caplog.records)
    assert seen["candidate_writes"] == {"s0": ["dep0.py", "dep1.py"], "s1": ["a.py"]}
    _assert_the_read_saw_the_candidate(seen, result)
    assert result.control_state.subtask_written_files == {"s0": ["dep0.py", "dep1.py"], "s1": ["a.py"]}


def test_a_grounded_recovery_of_every_owner_file_reads_the_candidate_as_before(tmp_path, monkeypatch):
    """The grounded (MUST_CHANGE) variant TEST-B measured passing at the base and at the tip: unchanged."""
    seen, result = _candidate_run(tmp_path, monkeypatch, _insufficient())
    assert seen["roles"] == ["subtask=s0 role=planned", "subtask=s1 role=planned", "subtask=s0 role=owner_recovery",
                             "subtask=s1 role=consumer_retry"]
    assert seen["candidate_writes"] == {"s0": ["dep0.py", "dep1.py"], "s1": ["a.py"]}
    _assert_the_read_saw_the_candidate(seen, result)


def test_a_grounded_recovery_of_a_subset_keeps_the_first_run_change_visible(tmp_path, monkeypatch):
    """Case B end to end, with GROUNDED evidence (no advisory attribution needed): the conflict names only dep1.py, the
    owner's recovery rewrites dep1.py only. Unfixed bytes, MEASURED: the record became {s0: [dep1.py]} and t2's read
    of dep0.py went to the owner baseline and failed."""
    conflict = _insufficient()
    conflict["plan_scope_conflict"]["required_files"] = ["dep1.py"]
    seen, result = _candidate_run(tmp_path, monkeypatch, conflict, {
        ("s0", "owner_recovery"): [{"_write": ["dep1.py"], "files": ["dep1.py"]}]})
    assert "subtask=s0 role=owner_recovery" in seen["roles"]
    assert seen["candidate_writes"]["s0"] == ["dep0.py", "dep1.py"]
    _assert_the_read_saw_the_candidate(seen, result)


# -- every writer of the record, one by one (in place: the persisted record of the run; production recovery builder) --

def _owner_three_files():
    return _plan_of(Subtask(id="s0", description="write deps", execution_method=ExecutionMethod.MODEL,
                            planned_files=[PlannedFile(path=f"dep{i}.py", action=FileAction.CREATE) for i in range(3)],
                            verification=[_COMPILE]),
                    _unit("s1", "a.py", depends_on=["s0"]))


def _grounded(*required, skip_write=False):
    """A grounded (COMPILE_ERROR) scope conflict naming ``required``: the production builder gives MUST_CHANGE."""
    return {"quality_gates_passed": False, "status": "failed", **({"_skip_write": True} if skip_write else {}),
            "plan_scope_conflict": {"classification": "PLAN_SCOPE_INSUFFICIENT", "required_files": list(required),
                                    "failure_type": "COMPILE_ERROR", "reason": "deps must change"}}


def _first_run_dep0():
    """s0's first run commits dep0.py only (a fresh list per run: the scripted queue is consumed)."""
    return {("s0", "planned"): [{"_write": ["dep0.py"], "files": ["dep0.py"]}]}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_owner_recovery_verify_only_keeps_the_first_run_files(tmp_path):
    """Case A at the owner-recovery finalization writer (enforce_loop, the invoked_group_records loop)."""
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_two_files(),
                                            {("s1", "planned"): [_advisory_conflict()]})
    assert [c["execution_scope"] for c in calls] == ["subtask=s0 role=planned", "subtask=s1 role=planned",
                                                     "subtask=s1 role=consumer_retry"]
    assert result.control_state.subtask_written_files["s0"] == ["dep0.py", "dep1.py"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_owner_recovery_must_change_adds_its_files_to_the_first_run_files(tmp_path):
    """Case B at the same writer: the first run committed dep0.py, the MUST_CHANGE recovery commits dep1.py."""
    script = {**_first_run_dep0(), ("s1", "planned"): [_grounded("dep1.py")],
              ("s0", "owner_recovery"): [{"_write": ["dep1.py"], "files": ["dep1.py"]}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_three_files(), script)
    assert "subtask=s0 role=owner_recovery" in [c["execution_scope"] for c in calls]
    assert result.legacy_result["status"] == "success"
    assert result.control_state.subtask_written_files["s0"] == ["dep0.py", "dep1.py"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_owner_recovery_that_failed_adds_nothing_it_did_not_commit(tmp_path):
    """Case C at the same writer: the recovery's gates failed (its sub-run committed nothing; the scripted call writes
    nothing) yet it reports dep1.py and dep2.py - neither becomes authoritative."""
    script = {**_first_run_dep0(), ("s1", "planned"): [_grounded("dep1.py")],
              ("s0", "owner_recovery"): [{"quality_gates_passed": False, "status": "failed", "_skip_write": True,
                                          "files": ["dep1.py", "dep2.py"]}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_three_files(), script)
    assert "subtask=s0 role=owner_recovery" in [c["execution_scope"] for c in calls]
    assert result.control_state.subtask_states["s0"] == "needs_review"
    assert result.control_state.subtask_written_files["s0"] == ["dep0.py"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_owner_recovery_re_invoked_across_generations_accumulates(tmp_path):
    """Re-invocation: s1's consumer retry fails with a second conflict, so s0 is reopened twice (generation 0 commits
    dep1.py, generation 1 commits dep2.py); the first run committed dep0.py. All three stay recorded."""
    script = {**_first_run_dep0(), ("s1", "planned"): [_grounded("dep1.py")],
              ("s1", "consumer_retry"): [_grounded("dep2.py", skip_write=True), {}],
              ("s0", "owner_recovery"): [{"_write": ["dep1.py"], "files": ["dep1.py"]},
                                         {"_write": ["dep2.py"], "files": ["dep2.py"]}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_three_files(), script)
    assert [c["execution_scope"] for c in calls].count("subtask=s0 role=owner_recovery") == 2
    assert result.legacy_result["status"] == "success"
    assert result.control_state.subtask_written_files["s0"] == ["dep0.py", "dep1.py", "dep2.py"]


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_verification_reopen_adds_its_files_to_the_first_run_files(tmp_path):
    """The verification-rerun fold (it already unioned at the base; pinned): the first run committed a.py, the accepted
    reopen commits a2.py only."""
    owner = Subtask(id="s0", description="write a", execution_method=ExecutionMethod.MODEL,
                    planned_files=[PlannedFile(path="a.py", action=FileAction.CREATE),
                                   PlannedFile(path="a2.py", action=FileAction.CREATE)], verification=[_COMPILE])
    plan = _plan_of(owner, Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                                   execution_role=ExecutionRole.VERIFICATION, depends_on=["s0"],
                                   verification=[_COMPILE]))
    script = {("s0", "planned"): [{"_write": ["a.py"], "files": ["a.py"]}],
              ("sv", "planned"): [dict(_NO_CHANGE_POSSIBLE)],
              ("s0", "verification_owner_recovery"): [{"_write": ["a2.py"], "files": ["a2.py"]}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan, script)
    assert [c["execution_scope"] for c in calls][-1] == "subtask=sv role=verification_rerun"
    assert result.control_state.subtask_written_files == {"s0": ["a.py", "a2.py"], "sv": []}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_unit_result_records_what_the_consumer_retry_committed(tmp_path):
    """The per-unit writer after a consumer retry: s1's failed planned run committed nothing, its retry committed a.py.
    (No invocation of s1 is recorded before this writer: owners are strictly upstream of the consumer.)"""
    script = {("s1", "planned"): [_grounded("dep0.py", "dep1.py", skip_write=True)]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_two_files(), script)
    assert [c["execution_scope"] for c in calls][-1] == "subtask=s1 role=consumer_retry"
    assert result.control_state.subtask_written_files == {"s0": ["dep0.py", "dep1.py"], "s1": ["a.py"]}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_writer_unit_result_of_a_failed_unit_records_no_uncommitted_file(tmp_path):
    """Case C at the per-unit writer: a unit whose gates failed committed nothing (its scripted call writes nothing),
    whatever its result's files report; its entry exists and is empty."""
    script = {("s1", "planned"): [{"quality_gates_passed": False, "status": "failed", "_skip_write": True}]}
    result, calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _owner_two_files(), script)
    assert [c["execution_scope"] for c in calls] == ["subtask=s0 role=planned", "subtask=s1 role=planned"]
    assert result.control_state.subtask_states["s1"] != "completed"
    assert result.control_state.subtask_written_files == {"s0": ["dep0.py", "dep1.py"], "s1": []}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_unit_labelled_verification_that_committed_a_file_is_recorded(tmp_path):
    """Case F: the label is no authority - a unit with the verification role whose result committed v.py is recorded
    with it (case E, a VERIFY recovery that changes nothing, is the verify-only writer test above)."""
    plan = _plan_of(Subtask(id="sv", description="verify", execution_method=ExecutionMethod.MODEL,
                            execution_role=ExecutionRole.VERIFICATION, verification=[_COMPILE]))
    result, _calls = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, plan,
                                             {("sv", "planned"): [{"_write": ["v.py"], "files": ["v.py"]}]})
    assert result.control_state.subtask_written_files == {"sv": ["v.py"]}


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_new_run_starts_a_new_record(tmp_path):
    """Case D: a candidate boundary - the next run in the same workspace never inherits the previous run's record (its
    ControlState is new); the discard reset of a sandboxed run is pinned in test_e12_enforce_characterization.py
    (the WORKSPACE_COMMIT_FAILED test asserts subtask_written_files == {})."""
    first, _ = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s0", "dep0.py")), {})
    assert first.control_state.subtask_written_files == {"s0": ["dep0.py"]}
    second, _ = await _enforce_scripted(_enforce_engine(tmp_path), tmp_path, _plan_of(_unit("s9", "z.py")), {})
    assert second.control_state.subtask_written_files == {"s9": ["z.py"]}


# -- the canonical update operation ------------------------------------------------------------------------------------

def _control_state(**written):
    from kriya.control.state import ControlState
    return ControlState.new(run_id="r1").with_updates(subtask_written_files={k: list(v) for k, v in written.items()})


@pytest.mark.parametrize("result, committed", [
    ({"quality_gates_passed": True, "files": ["b.py", "a.py"]}, ["a.py", "b.py"]),
    ({"quality_gates_passed": False, "files": ["a.py"]}, []),
    ({"quality_gates_passed": "yes", "files": ["a.py"]}, []),
    ({"files": ["a.py"]}, []),
    ({"quality_gates_passed": True}, []),
    ({"quality_gates_passed": False, "files": ["a.py"],
      "final_review_refusal": {"candidate_applied": True}}, ["a.py"]),
    ({"quality_gates_passed": False, "files": ["a.py"],
      "final_review_refusal": {"candidate_applied": False}}, []),
], ids=["gates-passed", "gates-failed", "not-a-bool", "no-verdict", "no-files", "review-refused-applied",
        "review-refused-not-applied"])
def test_only_committed_files_count(result, committed):
    from kriya.workflow.enforce_loop import committed_files
    assert sorted(committed_files(result)) == committed


def test_the_record_is_the_union_and_never_loses_or_invents_a_file():
    from kriya.workflow.enforce_loop import record_committed_effects
    state = _control_state(s0=["dep0.py"], s1=["a.py"])
    after = record_committed_effects(state, "s0", {"quality_gates_passed": True, "files": ["dep1.py", "dep0.py"]})
    assert after.subtask_written_files == {"s0": ["dep0.py", "dep1.py"], "s1": ["a.py"]}
    assert state.subtask_written_files == {"s0": ["dep0.py"], "s1": ["a.py"]}, "never mutated in place"
    kept = record_committed_effects(after, "s0", {"status": "success", "quality_gates_passed": True, "files": []})
    assert kept.subtask_written_files["s0"] == ["dep0.py", "dep1.py"]
    refused = record_committed_effects(after, "s0", {"quality_gates_passed": False, "files": ["dep9.py"]})
    assert refused.subtask_written_files["s0"] == ["dep0.py", "dep1.py"]


def test_a_unit_that_committed_nothing_gets_an_empty_entry():
    """An entry exists once the unit ran: empty = it changed nothing; no entry = no evidence (the S18c fail-closed
    reading in reads_candidate_view)."""
    from kriya.workflow.enforce_loop import record_committed_effects
    after = record_committed_effects(_control_state(), "s4", {"quality_gates_passed": True, "files": []})
    assert after.subtask_written_files == {"s4": []}
    failed = record_committed_effects(_control_state(), "s5", {"quality_gates_passed": False, "files": ["x.py"]})
    assert failed.subtask_written_files == {"s5": []}


# -- structural pin (supplementary, R4): no writer replaces the record --------------------------------------------------

_RECORD = "subtask_written_files"
_MUTATORS = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _record_writes(tree):
    """Every place a module writes the record: a ``subtask_written_files=`` keyword, an assignment to the attribute or
    to an item of it, or a mutating method call on it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == _RECORD:
            yield node, node.value
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                base = target.value if isinstance(target, ast.Subscript) else target
                if isinstance(base, ast.Attribute) and base.attr == _RECORD:
                    yield node, None
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS
              and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == _RECORD):
            yield node, None


def _enclosing_function(tree, target):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and target in set(ast.walk(node)):
            if not any(isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not node
                       and target in set(ast.walk(inner)) for inner in ast.walk(node)):
                return node.name
    return None


def _allowed(path, tree, node, value):
    if path == "kriya/control/state.py":
        return _enclosing_function(tree, node) == "from_dict"  # deserialization of a persisted record
    if path != "kriya/workflow/enforce_loop.py":
        return False
    if _enclosing_function(tree, node) == "record_committed_effects":
        return True  # the one canonical update
    return isinstance(value, ast.Dict) and not value.keys  # the candidate-discard boundary reset ({})


def test_every_writer_of_the_record_goes_through_the_one_cumulative_update():
    offenders, seen = [], 0
    for path in sorted((REPO / "kriya").rglob("*.py")):
        relative = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, value in _record_writes(tree):
            seen += 1
            if not _allowed(relative, tree, node, value):
                offenders.append(f"{relative}:{node.lineno}")
    assert seen >= 3, "the scan sees the canonical update, the boundary reset and the deserializer"
    assert offenders == [], f"writer(s) of {_RECORD} bypassing record_committed_effects (replacement): {offenders}"


@pytest.mark.parametrize("source", [
    "cs.with_updates(subtask_written_files={**cs.subtask_written_files, 's0': files})",
    "cs.subtask_written_files['s0'] = files",
    "cs.subtask_written_files.update({'s0': files})",
    "replace(cs, subtask_written_files={'s0': files})",
])
def test_the_structural_pin_sees_a_planted_replacement(source):
    tree = ast.parse(f"def writer(cs, files):\n    {source}\n")
    planted = list(_record_writes(tree))
    assert planted and not any(_allowed("kriya/workflow/enforce_loop.py", tree, node, value)
                               for node, value in planted)
