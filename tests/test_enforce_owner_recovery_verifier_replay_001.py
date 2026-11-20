"""ENFORCE-OWNER-RECOVERY-DEPENDENT-VERIFIER-REGENERATED-001 (owner rulings 2026-11-12; Scenario-A run 7, finding R7-B): the enforce loop.

Run 20261112T150652-e9c5502b, MEASURED: s3 owns ProtocolParser.java (declares tool: compile). s6 depends on s3, owns ProtocolParserTest.java
(declares tool: test). s6's tests failed inside the parser; the loop reopened s3; s3's repair passed compile and a tests gate that ran ZERO tests;
the loop then invoked s6 again as an ordinary unit, so the model wrote a NEW test file each time (three of them) to find out whether the repair
worked, and told s6 the owner had been "repaired and re-verified". The owner's result stayed ``completed`` after three disproved repairs.

``tests/test_enforce_owner_recovery_verifier_replay_001.py::OLD_BEHAVIOUR`` records that shape as it was measured model-free on 7f1a44c / caf278f
(evidence: ~/kriya-evidence/product-72h-confidence/run7-gaps-2026-11-12/r7b/). The tests below pin the corrected contract at the unit boundary
(``run_generation_workflow`` scripted): the consumer's refused candidate becomes a run-local replay handle; a valid handle is replayed with no
Developer generation; a failed replay - the same failure or a different one - goes back to the owner and never regenerates the verifier; a
passing replay settles the recovery; the owner's current result follows the dependent verification; the three-cycle cap is unchanged.
The unit side (no Developer call, exact bytes through the candidate writer, review/approval/gates rerun) is tests/test_verifier_replay_unit_001.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest
from test_workflow_controller_enforce import _patched, _workflow_engine

from kriya.repository.snapshot import raw_digest
from kriya.workflow import verifier_replay
from kriya.workflow.plan_schema import (
    ChangeKind,
    EngineeringPlan,
    ExecutionMethod,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
)
from kriya.workflow.verifier_replay import CarriedFile, RefusedCandidate, ReplayHandle
from kriya.workflow.workflow_controller import _MAX_PLAN_SCOPE_REVISION_ATTEMPTS, WorkflowController

PARSER = "src/main/java/com/example/ProtocolParser.java"
TEST = "src/test/java/com/example/ProtocolParserTest.java"
COMPILE = VerificationMethod(type=VerificationMethodType.TOOL, description="ProtocolParser compiles", tool_name="compile")
RUN_TESTS = VerificationMethod(type=VerificationMethodType.TOOL, description="JUnit test compiles and runs", tool_name="test")
COMPILE_EVIDENCE = {"type": "tool", "tool_name": "compile", "description": "ProtocolParser compiles", "passed": True,
                    "evidence": "authoritative_gate_outcome"}
TESTS_EVIDENCE = {"type": "tool", "tool_name": "test", "description": "JUnit test compiles and runs", "passed": True,
                  "evidence": "authoritative_gate_outcome"}
ZERO_TESTS = {"type": "tests", "success": True, "attempt": 1, "output": "No tests to run.",
              "test_execution": {"cases": 0, "completeness": "INDETERMINATE", "reason": "STRUCTURED_REPORT_MISSING", "status_counts": {}}}
OVERFLOW = ("[ERROR] Tests run: 4, Failures: 0, Errors: 4\njava.nio.BufferOverflowException\n"
            "\tat com.example.ProtocolParser.encode(ProtocolParser.java:37)\n")
COMPILE_BREAK = "[ERROR] ProtocolParserTest.java:[12,31] cannot find symbol: method encode(com.example.Protocol)\n"

# What the unfixed loop did with the run-7 shape (MEASURED model-free; the characterization kept in the evidence directory asserts each line).
OLD_BEHAVIOUR = {
    "unit_invocations": ["s3", "s6", "s3", "s6", "s3", "s6", "s3", "s6"],
    "consumer_artifacts_generated": 4,
    "consumer_retry_context": "Upstream owner(s) s3 were repaired and re-verified.",
    "owner_recovery_passed": [False, False, False],
    "results": {"s3": "completed", "s6": "needs_review"},
}


def _plan() -> EngineeringPlan:
    return EngineeringPlan(plan_id="run7shape", kind=ChangeKind.TASK, subtasks=[
        Subtask(id="s3", description="create the parser", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=PARSER, action=FileAction.CREATE)], provides=["protocol-parser"], verification=[COMPILE]),
        Subtask(id="s6", description="create the JUnit test", execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=TEST, action=FileAction.CREATE)], requires=["protocol-parser"], verification=[RUN_TESTS]),
    ])


def _refused(text: str, failure: str, *, path: str = TEST, digest: Optional[str] = None) -> RefusedCandidate:
    return RefusedCandidate(
        files=(CarriedFile(path, text, digest or raw_digest(text.encode("utf-8"))),), binding_digest="binding",
        failure_type="targeted_test", failure_digest=verifier_replay.failure_identity("targeted_test", failure))


class Run7Shape:
    """The unit boundary. ``parser`` scripts each s3 invocation's file text; ``verdict(parser_text, replayed)`` scripts what s6's gates find:
    None (pass), ("owner", output) - a failure located in the owner's file - or ("other", output) - a failure that names no owner."""

    def __init__(self, parser: List[str], verdict: Callable[[str, bool], Any], *, refuse: Callable[[str, str], Any] = _refused,
                 replay_answer: Optional[Dict[str, Any]] = None) -> None:
        self.parser, self.verdict, self.refuse, self.replay_answer = list(parser), verdict, refuse, replay_answer
        self.calls: List[Dict[str, Any]] = []
        self.generated: List[str] = []          # every test file text a Developer generation produced
        self.replayed: List[ReplayHandle] = []
        self.verifier_in_tree_at_replay: List[bool] = []   # was the verifier file already in the candidate tree when a replay unit started?

    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        workspace = Path(kwargs["workspace_path"])
        if kwargs["current_subtask_id"] == "s3":
            path = workspace / PARSER
            path.parent.mkdir(parents=True, exist_ok=True)
            text = self.parser.pop(0)
            changed = not path.exists() or path.read_text() != text
            path.write_text(text)
            return {"status": "success", "quality_gates_passed": True, "files": [PARSER] if changed else [],
                    "verification_results": [COMPILE_EVIDENCE],
                    "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(ZERO_TESTS)]}
        handle = kwargs.get("carried_candidate")
        if handle is not None:
            self.replayed.append(handle)
            self.verifier_in_tree_at_replay.append((workspace / TEST).exists())
            if self.replay_answer is not None:
                answer, self.replay_answer = self.replay_answer, None
                return answer
            test_text = handle.candidate.files[0].content
        else:
            test_text = f"class ProtocolParserTest {{ /* generated #{len(self.generated) + 1} */ }}\n"
            self.generated.append(test_text)
        found = self.verdict((workspace / PARSER).read_text(), handle is not None)
        if found is None:
            (workspace / TEST).parent.mkdir(parents=True, exist_ok=True)
            (workspace / TEST).write_text(test_text)
            return {"status": "success", "quality_gates_passed": True, "files": [TEST], "verification_results": [TESTS_EVIDENCE]}
        where, output = found
        result: Dict[str, Any] = {
            "status": "failed", "quality_gates_passed": False, "files": [TEST],
            "last_failure": {"type": "targeted_test" if where == "owner" else "compile", "message": output, "raw_output": output},
        }
        if where == "owner":
            result["plan_scope_conflict"] = {
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "classification": "PLAN_SCOPE_DEFECT", "failure_type": "targeted_test",
                "required_files": [PARSER], "allowed_files": [TEST], "raw_evidence": output, "attribution_tier": "locator",
                "reason": "the failing frames are in ProtocolParser.encode"}
        else:
            result["no_progress_reason"] = verifier_replay.VERIFIER_REPLAY_FAILED
        if kwargs.get("capture_refused_candidate"):
            refused = self.refuse(test_text, output)
            if refused is not None:
                result["refused_candidate"] = refused
        return result

    # ---- what a run did
    def units(self) -> List[str]:
        return [call["current_subtask_id"] for call in self.calls]

    def roles(self) -> List[str]:
        return [call["execution_scope"].split("role=")[1] for call in self.calls]

    def contexts(self, unit: str) -> List[str]:
        return [str(call.get("recovery_contract_block") or "") for call in self.calls if call["current_subtask_id"] == unit]


async def _run(tmp_path: Path, shape: Run7Shape, plan: Optional[EngineeringPlan] = None) -> Any:
    engine = _workflow_engine()
    engine.run_generation_workflow = shape
    p1, p2, p3 = _patched(plan or _plan())
    with p1, p2, p3:
        return await WorkflowController(engine).execute("goal", str(tmp_path), migration_mode="enforce")


def _statuses(result: Any) -> Dict[str, str]:
    return {item.subtask_id: item.status.value for item in result.subtask_results}


NEVER_FIXED = ["putInt+putLong", "3 bytes+putLong", "3 bytes+putLong, body guard", "3 bytes+putLong, comment"]


def _overflow_until_fixed(parser_text: str, replayed: bool) -> Any:
    return None if "putInt(time)" in parser_text else ("owner", OVERFLOW)


# ----------------------------------------------------------------------------- the run-7 shape, corrected

@pytest.mark.asyncio
async def test_run7_shape_the_refused_verifier_is_replayed_and_never_generated_again(tmp_path):
    """Items 43, 57: same eight unit invocations as before (the owner is still wrong three times), but ONE consumer artifact instead of four:
    every check of an owner repair replays the consumer's refused candidate, byte for byte, with no Developer generation."""
    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed)
    result = await _run(tmp_path, shape)

    assert shape.units() == OLD_BEHAVIOUR["unit_invocations"]
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", "owner_recovery", "verifier_replay",
                             "owner_recovery", "verifier_replay"]
    assert len(shape.generated) == 1 < OLD_BEHAVIOUR["consumer_artifacts_generated"]
    assert len(shape.replayed) == 3
    for handle in shape.replayed:           # the SAME verifier each time: the bytes of the candidate that first disproved the owner
        assert [(f.path, f.content) for f in handle.candidate.files] == [(TEST, shape.generated[0])]
        assert handle.expected_digests() == {TEST: raw_digest(shape.generated[0].encode("utf-8"))}
        assert (handle.unit_id, handle.goal_digest) == ("s6", verifier_replay.goal_digest("goal"))
    assert len({handle.plan_hash for handle in shape.replayed}) == 1 and shape.replayed[0].plan_hash
    assert all(call.get("carried_candidate") is None for call in shape.calls if call["current_subtask_id"] == "s3")
    # the cap is unchanged: three owner recoveries, not one more because a check became cheaper
    assert shape.units().count("s3") == 1 + _MAX_PLAN_SCOPE_REVISION_ATTEMPTS == 4
    events = result.legacy_result["plan_recovery_events"]
    assert [event["owner_recovery_passed"] for event in events] == OLD_BEHAVIOUR["owner_recovery_passed"]
    assert result.legacy_result["status"] != "success"
    assert not (tmp_path / TEST).exists()                                  # a refused verifier never reaches the workspace
    # items 9-10: the loop hands the handle to the unit and writes nothing itself - the candidate tree has no verifier when a replay starts
    assert shape.verifier_in_tree_at_replay == [False, False, False]


@pytest.mark.asyncio
async def test_run7_shape_the_recovery_truth_is_corrected(tmp_path):
    """Items 27-33, 39-43: the consumer is told the owner is only locally accepted, and a disproved owner is not ``completed``."""
    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed)
    result = await _run(tmp_path, shape)

    consumer_contexts = shape.contexts("s6")
    assert consumer_contexts[0] == ""
    for context in consumer_contexts[1:]:
        assert context.startswith("--- upstream owner repair locally accepted; dependent verification pending ---")
        assert "Nothing has verified that repair against this unit's verification yet" in context
        assert "re-verified" not in context and "were repaired" not in context
    assert OLD_BEHAVIOUR["results"]["s3"] == "completed"                      # what it used to report
    assert _statuses(result) == {"s3": "needs_review", "s6": "needs_review"}
    owner = next(item for item in result.subtask_results if item.subtask_id == "s3")
    assert owner.reason_codes == ("PLAN_RECOVERY_OWNER_FAILED",) and "dependent verification refused" in owner.error
    assert [item.subtask_id for item in result.subtask_results].count("s3") == 1   # the current result moved; none was added beside it


@pytest.mark.asyncio
async def test_a_passing_replay_settles_the_recovery_through_the_normal_path(tmp_path):
    """Items 29, 41, 45: the second repair is right; the replayed verifier passes; the recovery is settled by the consumer's gates as before."""
    shape = Run7Shape(["putInt+putLong", "3 bytes+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed)
    result = await _run(tmp_path, shape)

    assert shape.units() == ["s3", "s6", "s3", "s6", "s3", "s6"]
    assert shape.roles()[2:] == ["owner_recovery", "verifier_replay", "owner_recovery", "verifier_replay"]
    assert len(shape.generated) == 1
    assert result.legacy_result["status"] == "success"
    assert _statuses(result) == {"s3": "completed", "s6": "completed"}        # needs_review after the first disproved repair, then settled
    assert [event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"]] == [False, True]
    assert (tmp_path / TEST).read_text() == shape.generated[0]          # the carried verifier is the accepted, committed one


@pytest.mark.asyncio
async def test_a_first_repair_that_passes_the_replay_needs_no_second_cycle(tmp_path):
    shape = Run7Shape(["putInt+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed)
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay"]
    assert result.legacy_result["status"] == "success" and _statuses(result) == {"s3": "completed", "s6": "completed"}
    assert [event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"]] == [True]


# ----------------------------------------------------------------------------- a different failure is still evidence against the owner

@pytest.mark.asyncio
async def test_a_replay_that_fails_differently_goes_back_to_the_owner_and_never_regenerates_the_verifier(tmp_path):
    """Items 22-29, 65. The first repair changes the owner so that the carried test no longer COMPILES (no owner frame, no scope conflict from
    the unit). That is not permission to rewrite the test: no canonical contract changed, so the verifier stands and the owner is reopened
    with the new gate output."""
    def verdict(parser_text: str, replayed: bool) -> Any:
        if "renamed" in parser_text:
            return ("other", COMPILE_BREAK)
        return None if "putInt(time)" in parser_text else ("owner", OVERFLOW)

    shape = Run7Shape(["putInt+putLong", "renamed encode", "3 bytes+putInt(time)"], verdict)
    result = await _run(tmp_path, shape)

    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", "owner_recovery", "verifier_replay"]
    assert len(shape.generated) == 1                                   # never generated again, although the failure changed shape
    second_owner_context = shape.contexts("s3")[2]
    assert "cannot find symbol" in second_owner_context                 # the NEW deterministic evidence reached the owner
    assert "The failure CHANGED after your repair" in second_owner_context
    assert result.legacy_result["status"] == "success"
    assert [event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"]] == [False, True]


@pytest.mark.asyncio
async def test_a_differently_failing_replay_never_becomes_a_passed_recovery(tmp_path):
    def verdict(parser_text: str, replayed: bool) -> Any:
        return ("owner", OVERFLOW) if not replayed else ("other", COMPILE_BREAK)

    shape = Run7Shape(NEVER_FIXED, verdict)
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned"] + ["owner_recovery", "verifier_replay"] * 3
    assert len(shape.generated) == 1
    assert [event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"]] == [False, False, False]
    assert _statuses(result) == {"s3": "needs_review", "s6": "failed"} and result.legacy_result["status"] != "success"


@pytest.mark.asyncio
async def test_an_environment_stop_of_a_replay_is_not_evidence_against_the_owner(tmp_path):
    """A replay that could not run (infrastructure) says nothing about the repair: the owner is not reopened on it."""
    stopped = {"status": "failed", "quality_gates_passed": False, "files": [], "environment_failure": "docker daemon unreachable",
               "last_failure": {"type": "verification_infrastructure_failure", "message": "x", "raw_output": "x"}}
    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed, replay_answer=stopped)
    await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay"]


# ----------------------------------------------------------------------------- the facts the next owner invocation receives

@pytest.mark.asyncio
async def test_the_next_owner_invocation_receives_the_deterministic_facts_of_its_disproved_repair(tmp_path):
    """Items 45-49: candidate comparison and gate identities only, through the existing recovery context."""
    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed)
    await _run(tmp_path, shape)
    first, second, third = shape.contexts("s3")[1:]
    assert "dependent verification of your previous repair" not in first        # nothing to report before the first check
    for context in (second, third):
        assert "--- dependent verification of your previous repair (deterministic facts) ---" in context
        assert "Your previous repair changed the required file(s)." in context
        assert f"Repair candidate digest(s): {PARSER}=" in context
        assert "was the SAME artifact as before" in context and "replayed byte for byte" in context
        assert "The failure is the SAME as before your repair" in context
        assert "BufferOverflowException" in context                              # the evidence itself is still the gate's output
    identity = verifier_replay.failure_identity("targeted_test", OVERFLOW)
    assert f"Previous dependent failure identity: {identity}" in second and f"Current dependent failure identity:  {identity}" in second


# ----------------------------------------------------------------------------- when a handle is NOT replayed: regenerate, as before

@pytest.mark.asyncio
async def test_carried_bytes_that_do_not_match_their_digest_are_not_replayed(tmp_path):
    """Items 46, 62."""
    shape = Run7Shape(["putInt+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed,
                      refuse=lambda text, output: _refused(text, output, digest="sha256:" + "0" * 64))
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned", "owner_recovery", "consumer_retry"] and shape.replayed == []
    assert len(shape.generated) == 2 and result.legacy_result["status"] == "success"


@pytest.mark.asyncio
async def test_a_carried_file_outside_the_consumers_planned_scope_is_not_replayed(tmp_path):
    """Items 46, 61."""
    shape = Run7Shape(["putInt+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed,
                      refuse=lambda text, output: _refused(text, output, path="src/test/java/com/example/Other.java"))
    await _run(tmp_path, shape)
    assert shape.roles()[-1] == "consumer_retry" and shape.replayed == [] and len(shape.generated) == 2


@pytest.mark.asyncio
async def test_a_lost_handle_degrades_to_the_normal_regeneration(tmp_path):
    """Items 14-16, 66: nothing was captured (as after a restart, or an inexact capture): today's path, no error."""
    shape = Run7Shape(["putInt+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed, refuse=lambda text, output: None)
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned", "owner_recovery", "consumer_retry"] and shape.replayed == []
    assert len(shape.generated) == 2 and result.legacy_result["status"] == "success"
    assert shape.contexts("s6")[1].startswith("--- upstream owner repair locally accepted; dependent verification pending ---")


@pytest.mark.asyncio
async def test_a_replay_whose_staged_bytes_were_not_the_carried_ones_is_discarded_and_the_consumer_regenerated(tmp_path):
    """The unit's own refusal (VERIFIER_REPLAY_BYTES_MISMATCH, nothing executed) is not a verification result."""
    mismatch = {"status": "failed", "quality_gates_passed": False, "files": [], "no_progress_reason": verifier_replay.REPLAY_BYTES_MISMATCH,
                "last_failure": {"type": "verifier_replay_refused", "message": "x", "raw_output": "x"}}
    shape = Run7Shape(["putInt+putLong", "3 bytes+putInt(time)"], _overflow_until_fixed, replay_answer=mismatch)
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", "consumer_retry"]
    assert len(shape.generated) == 2 and result.legacy_result["status"] == "success"


def _handle(**changes: Any) -> ReplayHandle:
    base = dict(run_id="run", plan_hash="plan", unit_id="s6", goal_digest="goal", verification_contract_digest="contract",
                candidate=_refused("class T {}\n", OVERFLOW))
    return ReplayHandle(**{**base, **changes})


CURRENT = dict(run_id="run", plan_hash="plan", unit_id="s6", goal_digest="goal", verification_contract_digest="contract", planned_paths=[TEST])


@pytest.mark.parametrize("change,reason", [
    ({}, None),
    ({"plan_hash": "revised plan"}, verifier_replay.REPLAY_PLAN_CHANGED),                       # items 58, 63: any plan / scope / relationship revision
    ({"goal_digest": "another goal"}, verifier_replay.REPLAY_GOAL_CHANGED),                     # item 59
    ({"verification_contract_digest": "another contract"}, verifier_replay.REPLAY_CONTRACT_CHANGED),   # item 60
    ({"verification_contract_digest": None}, verifier_replay.REPLAY_CONTRACT_CHANGED),
    ({"run_id": "another run"}, verifier_replay.REPLAY_RUN_CHANGED),
    ({"unit_id": "s7"}, verifier_replay.REPLAY_UNIT_CHANGED),
    ({"planned_paths": []}, verifier_replay.REPLAY_OUT_OF_SCOPE),                               # item 61
    ({"planned_paths": ["src/test/java/com/example/Other.java"]}, verifier_replay.REPLAY_OUT_OF_SCOPE),
])
def test_replay_validity_is_decided_by_existing_canonical_identities(change, reason):
    """Items 17-21: the run, the plan hash, the goal, the sealed verification contract, the consumer unit, its planned scope, the bytes."""
    assert _handle().refusal(**{**CURRENT, **change}) == reason
    assert reason in (None, *verifier_replay.REPLAY_REFUSAL_CODES)


def test_replay_validity_refuses_altered_bytes_and_an_empty_candidate():
    altered = RefusedCandidate(files=(CarriedFile(TEST, "class T { int changed; }\n", raw_digest(b"class T {}\n")),),
                               binding_digest=None, failure_type="targeted_test", failure_digest="d")
    assert _handle(candidate=altered).refusal(**CURRENT) == verifier_replay.REPLAY_BYTES_MISMATCH
    empty = RefusedCandidate(files=(), binding_digest=None, failure_type=None, failure_digest=None)
    assert _handle(candidate=empty).refusal(**CURRENT) == verifier_replay.REPLAY_EMPTY


def test_an_upstream_implementation_change_is_not_an_input_of_replay_validity():
    """Items 19, 64: nothing about an upstream file's content is asked. The owner changing its implementation is what the verifier checks."""
    import inspect

    assert set(inspect.signature(ReplayHandle.refusal).parameters) == {
        "self", "run_id", "plan_hash", "unit_id", "goal_digest", "verification_contract_digest", "planned_paths"}


# ----------------------------------------------------------------------------- nothing persisted, nothing read from evidence, nothing leaked

@pytest.mark.asyncio
async def test_the_refused_candidate_never_reaches_a_result_a_trace_or_the_disk(tmp_path):
    """Items 4, 8, 10-13: the handle is transport inside the loop. The unit result the loop keeps has no ``refused_candidate`` key, no file
    under the workspace or the state directory holds the refused bytes, and the run result carries none."""
    import json
    import os

    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed)
    result = await _run(tmp_path, shape)
    carried = shape.generated[0].strip()
    assert "refused_candidate" not in json.dumps(result.legacy_result, default=str)
    assert carried not in json.dumps(result.legacy_result, default=str)
    roots = [str(tmp_path), os.environ["KRIYA_STATE_DIR"]]
    for root in roots:
        for directory, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if name != ".git"]    # git's own object store is not listed (the harness guards repository snapshots)
            for name in names:
                path = os.path.join(directory, name)
                try:
                    assert carried.encode() not in open(path, "rb").read(), path
                except OSError:
                    continue


def test_the_workflow_never_reads_the_attempt_evidence_store():
    """Items 1-3: attempt evidence stays control-flow inert; the replay bytes come from the unit's own staged candidate, in memory."""
    repo = Path(__file__).resolve().parents[1]
    for path in sorted((repo / "kriya" / "workflow").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "attempt_evidence.reader" not in source and "attempt_evidence import reader" not in source, path
        assert "candidate_export" not in source, path
    module = (repo / "kriya" / "workflow" / "verifier_replay.py").read_text(encoding="utf-8")
    for forbidden in ("write_control_file", "save_checkpoint", "json.dump", "open(os.path.join(worktree_path, relpath), \"w"):
        assert forbidden not in module, forbidden


# ----------------------------------------------------------------------------- units the change does not touch

@pytest.mark.asyncio
async def test_a_plan_whose_units_pass_is_invoked_exactly_as_before(tmp_path):
    """Items 44, 46 (direct-verification and no-dependent units): no recovery, no handle, no replay; every unit that owns files is merely
    asked to hand a refused candidate back if it ever stops."""
    shape = Run7Shape(["putInt(time)"], _overflow_until_fixed)
    result = await _run(tmp_path, shape)
    assert shape.roles() == ["planned", "planned"] and shape.replayed == []
    assert [call.get("capture_refused_candidate") for call in shape.calls] == [True, True]
    assert all(call.get("carried_candidate") is None for call in shape.calls)
    assert result.legacy_result["status"] == "success" and "plan_recovery_events" not in result.legacy_result


@pytest.mark.asyncio
async def test_zero_local_tests_of_the_owner_are_never_positive_dependent_evidence(tmp_path):
    """Items 40-41, 46: each reopened owner 'passes' on a compile and a zero-test gate; no recovery is recorded as passed on that."""
    shape = Run7Shape(NEVER_FIXED, _overflow_until_fixed)
    result = await _run(tmp_path, shape)
    assert not any(event["owner_recovery_passed"] for event in result.legacy_result["plan_recovery_events"])
    assert _statuses(result)["s3"] != "completed"


# ----------------------------------------------------------------------------- OWNER-REPAIR-REGRESSION-001 (Stage C run 20261119T200403-218cbbf6)
#
# MEASURED in the sealed run: the consumer's verifier (three tests) failed testNullBody against the owner as first written; a person accepted the
# expectation; the reopened owner made testNullBody pass by returning null for an empty body, which turned testEmptyBody - PASSING before the
# repair - red in the replay of the same verifier bytes. The loop kept the repaired bytes in the candidate tree and every later step ran on them.
# The per-test facts below are the sealed structured reports of that run (tests/fixtures/stage_c_run5), not a reconstruction.

RUN5 = Path(__file__).parent / "fixtures" / "stage_c_run5"
O0 = (RUN5 / "ProtocolParser.before_repair.java").read_text()
O1 = (RUN5 / "ProtocolParser.after_repair.java").read_text()
NULL_BODY, EMPTY_BODY, ROUND_TRIP = (f"com.example.ProtocolParserTest.{name}" for name in ("testNullBody", "testEmptyBody", "testRoundTrip"))


def _sealed_outcome(name: str) -> Any:
    cases = json.loads((RUN5 / name).read_text())["test_cases"]
    return (TEST, tuple(sorted(case["identity"] for case in cases)),
            tuple(sorted(case["identity"] for case in cases if case["status"] in ("failed", "error"))))


BEFORE_REPAIR = _sealed_outcome("s5_tests_before_owner_repair.test_execution.json")
AFTER_REPAIR = _sealed_outcome("s5_replay_after_owner_repair.test_execution.json")
NULL_FAILS = "[ERROR] ProtocolParserTest.testNullBody: expected: <null> but was: <[]>\n\tat com.example.ProtocolParser.decode(ProtocolParser.java:58)\n"
EMPTY_FAILS = "[ERROR] ProtocolParserTest.testEmptyBody: actual array was <null>\n"
NULL_FAILS_AGAIN = "[ERROR] ProtocolParserTest.testNullBody: expected: <null> but was: <[0]>\n"


def _refused_with(outcomes: Dict[str, Any]) -> Callable[[str, str], RefusedCandidate]:
    """The refused candidate as the real unit hands it over: its per-test outcome is the one the structured report gave for that gate output."""
    def refuse(text: str, failure: str) -> RefusedCandidate:
        return RefusedCandidate(
            files=(CarriedFile(TEST, text, raw_digest(text.encode("utf-8"))),), binding_digest="binding", failure_type="targeted_test",
            failure_digest=verifier_replay.failure_identity("targeted_test", failure), test_outcomes=tuple(o for o in (outcomes.get(failure),) if o))
    return refuse


class Run5Shape(Run7Shape):
    """Run7Shape plus the ONE thing the regression rule adds at the unit boundary: an owner invocation that CARRIES a candidate (the owner's
    exact pre-repair bytes) stages those bytes instead of asking the Developer. ``restore_accepts`` False = the owner unit's gates refuse them."""

    def __init__(self, *args: Any, restore_accepts: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.restore_accepts, self.restores, self.parser_after_restore = restore_accepts, [], None

    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        handle = kwargs.get("carried_candidate")
        if kwargs["current_subtask_id"] != "s3" or handle is None:
            return await super().__call__(**kwargs)
        self.calls.append(kwargs)
        self.restores.append(handle)
        if not self.restore_accepts:
            return {"status": "failed", "quality_gates_passed": False, "files": [],
                    "last_failure": {"type": "compile", "message": "the gate refused the carried bytes", "raw_output": "refused"}}
        path = Path(kwargs["workspace_path"]) / PARSER
        path.write_bytes(handle.candidate.files[0].content.encode("utf-8"))
        self.parser_after_restore = path.read_text()
        return {"status": "success", "quality_gates_passed": True, "files": [PARSER], "verification_results": [COMPILE_EVIDENCE],
                "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(ZERO_TESTS)]}


def _run5_verdict(parser_text: str, replayed: bool) -> Any:
    return ("owner", NULL_FAILS) if parser_text == O0 else ("other", EMPTY_FAILS)


def test_the_sealed_run5_reports_are_a_regression_of_testEmptyBody_and_only_an_exact_comparison_says_so():
    """Decision: WHEN a failed replay is a regression. Only the same verifier bytes with per-test outcomes on both sides."""
    before, after = _refused_with({"x": BEFORE_REPAIR})("V0", "x"), _refused_with({"x": AFTER_REPAIR})("V0", "x")
    assert BEFORE_REPAIR[2] == (NULL_BODY,) and AFTER_REPAIR[2] == (EMPTY_BODY,)                 # what the sealed run measured
    assert verifier_replay.regressed_tests(before, after) == (f"{TEST}::{EMPTY_BODY}",)
    assert verifier_replay.regressed_tests(before, before) == ()                                 # the same failure again: no regression
    assert verifier_replay.regressed_tests(after, before) == (f"{TEST}::{NULL_BODY}",)           # the comparison has a direction
    # fail closed: nothing is claimed without exact evidence on BOTH sides
    assert verifier_replay.regressed_tests(before, _refused("V0", "x")) == ()                    # no per-test outcome after
    assert verifier_replay.regressed_tests(_refused("V0", "x"), after) == ()                     # none before
    assert verifier_replay.regressed_tests(before, _refused_with({"x": AFTER_REPAIR})("V1 other bytes", "x")) == ()   # not the same verifier bytes
    assert verifier_replay.regressed_tests(None, after) == () and verifier_replay.regressed_tests(before, None) == ()
    # a test that did not execute before is not "previously passing"
    narrower = _refused_with({"x": (TEST, (NULL_BODY,), (NULL_BODY,))})("V0", "x")
    assert verifier_replay.regressed_tests(narrower, after) == ()


def test_a_refused_candidate_keeps_only_the_outcome_of_exactly_its_own_bytes(tmp_path):
    """Decision: WHICH outcome a refused candidate carries. One for other bytes or another path is not kept (so it can never be compared)."""
    (tmp_path / "src/test/java/com/example").mkdir(parents=True)
    (tmp_path / TEST).write_text("class T {}\n")
    digest = raw_digest(b"class T {}\n")
    state = SimpleNamespace(all_files_written={TEST}, candidate_digests={TEST: digest}, last_failure=None, verified_candidate_binding=None)
    exact = {"path": TEST, "sha256": digest, "identities": [EMPTY_BODY, NULL_BODY], "failed_identities": [NULL_BODY]}
    kept = verifier_replay.capture_refused_candidate(state, str(tmp_path), [exact, {**exact, "path": "src/test/java/Other.java"}])
    assert kept.test_outcomes == ((TEST, (EMPTY_BODY, NULL_BODY), (NULL_BODY,)),)
    assert kept.passed_tests() == frozenset({(TEST, EMPTY_BODY)}) and kept.failed_tests() == frozenset({(TEST, NULL_BODY)})
    assert verifier_replay.capture_refused_candidate(state, str(tmp_path), [{**exact, "sha256": "0" * 64}]).test_outcomes == ()
    assert verifier_replay.capture_refused_candidate(state, str(tmp_path)).test_outcomes == ()        # the historical call: unchanged


@pytest.mark.asyncio
async def test_run5_a_repair_that_turns_a_previously_passing_test_red_is_refused_restored_exactly_and_stops(tmp_path):
    """Decision: WHAT happens to a regressive repair. Refused; the owner's exact pre-repair bytes go back through the owner unit's own candidate
    path (no Developer call); the episode stops typed; the owner is never reopened again on the failure its own repair caused."""
    shape = Run5Shape([O0, O1], _run5_verdict, refuse=_refused_with({NULL_FAILS: BEFORE_REPAIR, EMPTY_FAILS: AFTER_REPAIR}))
    result = await _run(tmp_path, shape)

    assert shape.units() == ["s3", "s6", "s3", "s6", "s3"]
    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", verifier_replay.OWNER_REPAIR_RESTORE_ROLE]
    assert shape.parser == [] and len(shape.generated) == 1                  # no third Developer answer for the owner, no new verifier
    (restore,) = shape.restores
    assert [(file.path, file.content, file.raw_sha256) for file in restore.candidate.files] == [(PARSER, O0, raw_digest(O0.encode("utf-8")))]
    assert restore.unit_id == "s3" and shape.parser_after_restore == O0 != O1
    record = result.legacy_result["owner_repair_regression"]
    assert record["reason_code"] == verifier_replay.OWNER_REPAIR_REGRESSED and record["restored"] is True
    assert record["regressed_tests"] == [f"{TEST}::{EMPTY_BODY}"] and (record["subtask_id"], record["owner_subtask_id"]) == ("s6", "s3")
    assert record["owner_files_after"] == record["pre_repair_owner_files"] == {PARSER: raw_digest(O0.encode("utf-8"))}
    # a failed repair is never local or final success
    legacy = result.legacy_result
    assert legacy["status"] != "success" and not (tmp_path / TEST).exists()
    assert legacy["terminal_cause"]["failure_category"] == verifier_replay.OWNER_REPAIR_REGRESSION_FAILURE_CATEGORY
    assert legacy["terminal_cause"]["reason"].startswith(verifier_replay.OWNER_REPAIR_REGRESSED + ":")
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [False]
    statuses = _statuses(result)
    assert statuses["s3"] == "needs_review" and statuses["s6"] != "completed"


@pytest.mark.asyncio
async def test_a_replay_that_fails_differently_without_a_regression_still_goes_back_to_the_owner(tmp_path):
    """Negative control for the rule above, with per-test outcomes present on both sides: the SAME test fails after the repair with another
    message, nothing that passed before fails now. The existing bounded multi-step recovery is unchanged: no restore, the owner is reopened."""
    same_test_fails = (TEST, BEFORE_REPAIR[1], (NULL_BODY,))

    def verdict(parser_text: str, replayed: bool) -> Any:
        if parser_text == O0:
            return ("owner", NULL_FAILS)
        return ("other", NULL_FAILS_AGAIN) if parser_text == O1 else None

    shape = Run5Shape([O0, O1, "the second repair is right"], verdict,
                      refuse=_refused_with({NULL_FAILS: BEFORE_REPAIR, NULL_FAILS_AGAIN: same_test_fails}))
    result = await _run(tmp_path, shape)

    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", "owner_recovery", "verifier_replay"]
    assert shape.restores == [] and "owner_repair_regression" not in result.legacy_result
    assert "The failure CHANGED after your repair" in shape.contexts("s3")[2]            # the new evidence reached the owner, as before
    assert result.legacy_result["status"] == "success" and _statuses(result) == {"s3": "completed", "s6": "completed"}


@pytest.mark.asyncio
async def test_a_regressive_repair_whose_pre_repair_bytes_cannot_be_restored_stops_typed_and_is_never_reported_restored(tmp_path):
    """Decision: fail closed when restoration is not proven. The owner unit's gates refuse the carried bytes: the repair is still refused and the
    episode still stops, with the reason code that says the refused bytes are still in the candidate tree."""
    shape = Run5Shape([O0, O1], _run5_verdict, refuse=_refused_with({NULL_FAILS: BEFORE_REPAIR, EMPTY_FAILS: AFTER_REPAIR}), restore_accepts=False)
    result = await _run(tmp_path, shape)

    assert shape.roles() == ["planned", "planned", "owner_recovery", "verifier_replay", verifier_replay.OWNER_REPAIR_RESTORE_ROLE]
    record = result.legacy_result["owner_repair_regression"]
    assert record["restored"] is False and record["reason_code"] == verifier_replay.OWNER_REPAIR_REGRESSED_RESTORE_UNAVAILABLE
    assert record["owner_files_after"] == {PARSER: raw_digest(O1.encode("utf-8"))} != record["pre_repair_owner_files"]
    assert "could NOT be restored" in result.legacy_result["terminal_cause"]["reason"]
    assert result.legacy_result["status"] != "success" and _statuses(result)["s3"] == "needs_review"
    assert shape.parser == []                                                    # and the owner is not reopened again


@pytest.mark.asyncio
async def test_without_per_test_outcomes_the_existing_routing_is_unchanged(tmp_path):
    """Decision: no evidence, no rule. The run-5 shape WITHOUT structured outcomes (the historical refused candidate) is not called a regression."""
    shape = Run5Shape([O0, O1, O1 + "// second", O1 + "// third"], _run5_verdict)
    result = await _run(tmp_path, shape)
    assert shape.restores == [] and "owner_repair_regression" not in result.legacy_result
    assert shape.roles() == ["planned", "planned"] + ["owner_recovery", "verifier_replay"] * 3


OTHER = "src/main/java/com/example/ProtocolApplication.java"


def _plan_with_another_dependent() -> EngineeringPlan:
    """The run-7 plan plus s4: a second unit that depends on the owner and COMPLETED against its first bytes (the sealed run's s4)."""
    base = _plan()
    return EngineeringPlan(plan_id="run5shape", kind=ChangeKind.TASK, subtasks=[
        base.subtasks[0],
        Subtask(id="s4", description="create the application", execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=OTHER, action=FileAction.CREATE)], requires=["protocol-parser"], verification=[COMPILE]),
        base.subtasks[1],
    ])


class Run5ShapeWithDependent(Run5Shape):
    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        if kwargs["current_subtask_id"] != "s4":
            return await super().__call__(**kwargs)
        self.calls.append(kwargs)
        path = Path(kwargs["workspace_path"]) / OTHER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class ProtocolApplication {}\n")
        return {"status": "success", "quality_gates_passed": True, "files": [OTHER], "verification_results": [COMPILE_EVIDENCE],
                "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(ZERO_TESTS)]}


@pytest.mark.asyncio
@pytest.mark.parametrize("restore_accepts", [True, False])
async def test_run5_dependent_state_follows_the_bytes_restored_exactly_or_not_at_all(tmp_path, restore_accepts):
    """Decision: WHAT the other dependents of the owner are afterwards. s4 completed against the owner's first bytes and was invalidated when
    the owner was reopened. Exact restoration puts its completed state and result back WITHOUT running it again (its evidence describes these
    bytes); when the bytes are not restored it is NOT reported completed over the refused bytes."""
    shape = Run5ShapeWithDependent([O0, O1], _run5_verdict, refuse=_refused_with({NULL_FAILS: BEFORE_REPAIR, EMPTY_FAILS: AFTER_REPAIR}),
                                   restore_accepts=restore_accepts)
    result = await _run(tmp_path, shape, _plan_with_another_dependent())

    assert shape.units() == ["s3", "s4", "s6", "s3", "s6", "s3"], shape.roles()        # s4 ran once: never re-entered, restored or not
    record = result.legacy_result["owner_repair_regression"]
    assert record["restored"] is restore_accepts, record
    statuses = _statuses(result)
    if restore_accepts:
        assert statuses.get("s4") == "completed", statuses
        assert [item.subtask_id for item in result.subtask_results].count("s4") == 1
    else:
        assert statuses.get("s4") != "completed", statuses
    assert statuses["s3"] == "needs_review" and result.legacy_result["status"] != "success", statuses


def test_the_regression_reason_codes_are_a_closed_table():
    assert verifier_replay.OWNER_REPAIR_REGRESSION_CODES == ("OWNER_REPAIR_REGRESSED_ACCEPTED_VERIFIER", "OWNER_REPAIR_REGRESSED_RESTORE_UNAVAILABLE")
    assert verifier_replay.OWNER_REPAIR_REGRESSION_FAILURE_CATEGORY == "owner_repair_regressed"
    assert verifier_replay.OWNER_REPAIR_RESTORE_ROLE == "owner_repair_restore"
