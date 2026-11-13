"""OWNER-RECOVERY-IMPLICATED-ARTIFACT-SCOPE-LOST-001 (owner rulings 2026-11-13 after Scenario-A run 9): an owner recovery is generated over the
recovery requirement's implicated artifacts, not over every file the owner plans.

Run 20261113T060030-40a14267, MEASURED: s3 owns Protocol.java + ProtocolParser.java; s5's test failed inside the parser; the requirement named
ProtocolParser.java only; the reopened s3 was generated over both files. Attempt 1 was refused for answering the parser while Protocol.java was
the target, attempts 2 and 7 were INCOMPLETE GENERATION for an unchanged Protocol.java, attempt 3 ran in missing_files mode; the accepted repair
changed only the parser. 14.6 minutes of a 2400 s budget.
Pre-fix characterization (passes on b141fdd): ~/kriya-evidence/product-72h-confidence/run9-fixes-2026-11-13/prefix-characterization/.

The contract: for ``execution_role="owner_recovery"`` the implicated artifacts are the invocation's generation targets, its completeness
requirement and its write scope; the owner's other files stay in the base tree, are context, and take part in every gate. An ordinary planned
generation still requires every planned file. First the unit boundary (``run_generation_workflow`` scripted), then the REAL controller and unit
(tests/_enforce_unit_harness.py: transport and toolchain gates scripted).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
import test_enforce_owner_recovery_verifier_replay_001 as r7
import test_verifier_replay_unit_001 as real
from _enforce_unit_harness import run_enforce

from kriya.workflow import verifier_replay
from kriya.workflow.enforce_loop import owner_recovery_mutation_scope
from kriya.workflow.plan_schema import ChangeKind, EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask

PROTOCOL = "src/main/java/com/example/Protocol.java"
PARSER, TEST = r7.PARSER, r7.TEST
DATALENGTH = "[ERROR] ProtocolParserTest.testRoundTrip:24 expected: <0> but was: <15>\n"


def _owner(files: List[str]) -> Subtask:
    return Subtask(id="s3", description="create the protocol classes", execution_method=ExecutionMethod.MODEL,
                   planned_files=[PlannedFile(path=path, action=FileAction.CREATE) for path in files],
                   provides=["protocol-classes"], verification=[r7.COMPILE])


def run9_plan(consumer: str = "s5") -> EngineeringPlan:
    return EngineeringPlan(plan_id="run9shape", kind=ChangeKind.TASK, subtasks=[
        _owner([PROTOCOL, PARSER]),
        Subtask(id=consumer, description="create the JUnit test", execution_method=ExecutionMethod.MODEL, depends_on=["s3"],
                planned_files=[PlannedFile(path=TEST, action=FileAction.CREATE)], requires=["protocol-classes"], verification=[r7.RUN_TESTS]),
    ])


class Run9Shape:
    """The unit boundary of the run-9 shape. ``owner_answers``: per s3 invocation, the parser text it writes (Protocol.java is written by the
    planned invocation only) or a ready unit result (a typed stop). ``verdict(parser_text, replayed)``: what the consumer's gates find - None,
    ("owner", output) with ``implicated`` as the files the failure is located in, or ("other", output)."""

    def __init__(self, owner_answers: List[Any], verdict, *, implicated: List[str] = (PARSER,), replay_category: str = "") -> None:
        self.owner_answers, self.verdict, self.implicated = list(owner_answers), verdict, list(implicated)
        self.replay_category = replay_category
        self.calls: List[Dict[str, Any]] = []
        self.generated: List[str] = []
        self.sibling_in_tree_at_recovery: List[bool] = []

    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        workspace = Path(kwargs["workspace_path"])
        role = kwargs["execution_scope"].split("role=")[1]
        if kwargs["current_subtask_id"] == "s3":
            answer = self.owner_answers.pop(0)
            if role != "planned":
                self.sibling_in_tree_at_recovery.append((workspace / PROTOCOL).exists())
            if isinstance(answer, dict):
                return dict(answer)
            written = []
            (workspace / PARSER).parent.mkdir(parents=True, exist_ok=True)
            if role == "planned":
                (workspace / PROTOCOL).write_text("class Protocol {}\n")
                written.append(PROTOCOL)
            if not (workspace / PARSER).exists() or (workspace / PARSER).read_text() != answer:
                (workspace / PARSER).write_text(answer)
                written.append(PARSER)
            return {"status": "success", "quality_gates_passed": True, "files": written, "verification_results": [r7.COMPILE_EVIDENCE],
                    "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(r7.ZERO_TESTS)]}
        handle = kwargs.get("carried_candidate")
        if handle is None:
            test_text = f"class ProtocolParserTest {{ /* generated #{len(self.generated) + 1} */ }}\n"
            self.generated.append(test_text)
        else:
            test_text = handle.candidate.files[0].content
        found = self.verdict((workspace / PARSER).read_text(), handle is not None)
        if found is None:
            (workspace / TEST).parent.mkdir(parents=True, exist_ok=True)
            (workspace / TEST).write_text(test_text)
            return {"status": "success", "quality_gates_passed": True, "files": [TEST], "verification_results": [r7.TESTS_EVIDENCE]}
        where, output = found
        result: Dict[str, Any] = {"status": "failed", "quality_gates_passed": False, "files": [TEST],
                                  "last_failure": {"type": "targeted_test", "message": output, "raw_output": output}}
        if where == "owner":
            result["plan_scope_conflict"] = {
                "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "classification": "PLAN_SCOPE_DEFECT", "failure_type": "targeted_test",
                "required_files": list(self.implicated), "allowed_files": [TEST], "raw_evidence": output, "attribution_tier": "locator",
                "reason": "the failing frames are in the owner's files"}
        else:
            result["no_progress_reason"] = verifier_replay.VERIFIER_REPLAY_FAILED
            if self.replay_category:
                result["failure_category"] = self.replay_category
        if kwargs.get("capture_refused_candidate"):
            result["refused_candidate"] = r7._refused(test_text, output)
        return result

    def roles(self) -> List[str]:
        return [f"{call['current_subtask_id']}:{call['execution_scope'].split('role=')[1]}" for call in self.calls]

    def call(self, role: str, nth: int = 0) -> Dict[str, Any]:
        return [call for call, name in zip(self.calls, self.roles(), strict=True) if name == role][nth]


def _fixed_after_one_repair(parser_text: str, replayed: bool) -> Any:
    return None if "four bytes" in parser_text else ("owner", r7.OVERFLOW)


# ------------------------------------------------------------------------------------------------------------ the unit boundary

@pytest.mark.asyncio
async def test_run9_shape_the_reopened_owner_is_generated_over_the_implicated_artifact_only(tmp_path):
    shape = Run9Shape(["putInt+putLong", "three bytes + four bytes"], _fixed_after_one_repair)
    result = await r7._run(tmp_path, shape, run9_plan())

    assert shape.roles() == ["s3:planned", "s5:planned", "s3:owner_recovery", "s5:verifier_replay"]
    recovery = shape.call("s3:owner_recovery")
    block = str(recovery["recovery_contract_block"])
    assert PARSER in block and PROTOCOL not in block                               # the requirement names the parser only
    assert recovery["predetermined_architect_files"] == [PARSER]                   # generation targets AND completeness requirement
    assert recovery["allowed_write_relpaths"] == [PARSER]                          # write scope
    assert shape.sibling_in_tree_at_recovery == [True]                             # the sibling is in the base tree ...
    assert PROTOCOL in recovery["established_files"] and PARSER in recovery["established_files"]   # ... and is context
    assert result.legacy_result["status"] == "success"
    assert (tmp_path / PROTOCOL).read_text() == "class Protocol {}\n" and "four bytes" in (tmp_path / PARSER).read_text()


@pytest.mark.asyncio
async def test_ordinary_planned_generation_still_requires_every_planned_file(tmp_path):
    shape = Run9Shape(["putInt+putLong", "three bytes + four bytes"], _fixed_after_one_repair)
    await r7._run(tmp_path, shape, run9_plan())

    planned = shape.call("s3:planned")
    assert planned["predetermined_architect_files"] == [PROTOCOL, PARSER] == planned["allowed_write_relpaths"]
    consumer = shape.call("s5:planned")
    assert consumer["predetermined_architect_files"] == [TEST] == consumer["allowed_write_relpaths"]
    replay = shape.call("s5:verifier_replay")                                      # no other role is narrowed
    assert replay["predetermined_architect_files"] == [TEST] == replay["allowed_write_relpaths"]


@pytest.mark.asyncio
async def test_a_requirement_that_implicates_both_files_requires_both(tmp_path):
    shape = Run9Shape(["putInt+putLong", "three bytes + four bytes"], _fixed_after_one_repair, implicated=[PARSER, PROTOCOL])
    await r7._run(tmp_path, shape, run9_plan())

    recovery = shape.call("s3:owner_recovery")
    assert recovery["predetermined_architect_files"] == [PROTOCOL, PARSER] == recovery["allowed_write_relpaths"]   # plan order
    block = str(recovery["recovery_contract_block"])
    assert PARSER in block and PROTOCOL in block


@pytest.mark.asyncio
async def test_every_reopen_of_the_same_requirement_keeps_the_implicated_scope(tmp_path):
    """The cap's three owner recoveries are each scoped: the second and third are not widened back to the owner."""
    shape = Run9Shape(list(r7.NEVER_FIXED), lambda text, replayed: ("owner", r7.OVERFLOW))
    await r7._run(tmp_path, shape, run9_plan())

    recoveries = [call for call, role in zip(shape.calls, shape.roles(), strict=True) if role == "s3:owner_recovery"]
    assert len(recoveries) == 3
    assert all(call["predetermined_architect_files"] == [PARSER] == call["allowed_write_relpaths"] for call in recoveries)


def test_the_scope_is_the_implicated_artifacts_the_owner_plans_in_plan_order():
    owner = _owner([PROTOCOL, PARSER, "pom.xml"])
    assert owner_recovery_mutation_scope(owner, [PARSER]) == [PARSER]
    assert owner_recovery_mutation_scope(owner, ["pom.xml", PROTOCOL]) == [PROTOCOL, "pom.xml"]
    assert owner_recovery_mutation_scope(owner, [PARSER, PARSER, "another/File.java"]) == [PARSER]     # only what the owner plans


def test_an_implicated_set_outside_the_owner_is_refused_never_widened_to_the_whole_owner():
    owner = _owner([PROTOCOL, PARSER])
    for implicated in ([], ["another/File.java"]):
        with pytest.raises(ValueError, match="none of the implicated artifacts"):
            owner_recovery_mutation_scope(owner, implicated)


# ------------------------------------------------------------------------------------------------------------ the real controller and unit

PROTO, LIB, LIBTEST = "src/main/java/com/example/Proto.java", real.LIB, real.TEST
PROTO_SOURCE = "package com.example;\n\npublic class Proto {\n    public int value;\n}\n"


def _real_plan() -> EngineeringPlan:
    def unit(sid: str, paths: List[str], tool: str, **more: Any) -> Dict[str, Any]:
        return {"id": sid, "description": f"write {' and '.join(paths)}", "execution_method": "model",
                "planned_files": [{"path": path, "action": "create"} for path in paths], "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": tool, "description": tool}], **more}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [unit("s3", [PROTO, LIB], "compile", provides=["encoder"]),
                     unit("s5", [LIBTEST], "test", depends_on=["s3"], requires=["encoder"], provides=["encoder-test"])]})


def _real_run(tmp_path: Path, lib_answers: List[Any]) -> Any:
    """s3 owns Proto.java + Lib.java; s5's test fails inside Lib.java until it is FIXED. ``lib_answers``: the scripted Developer's answer to
    each request for Lib.java (a text, or (another path, text) to answer for a file it was not asked for)."""
    answers = iter(lib_answers)
    proto_requests: List[int] = []
    trees: List[Any] = []

    def proto() -> str:
        proto_requests.append(1)
        return PROTO_SOURCE

    def tests_gate(validator: Any) -> Dict[str, Any]:
        tree = Path(validator.workspace_path)
        trees.append(((tree / PROTO).exists(), (tree / LIB).exists(), (tree / LIBTEST).exists()))
        return real._tests_gate(validator)

    with patch("kriya.tools.lsp.find_jdtls", return_value=None):
        run = run_enforce(tmp_path, plan=_real_plan(), goal="Write an encoder and its test",
                          units={"s3": {PROTO: proto, LIB: lambda: next(answers)}, "s5": (LIBTEST, real.TEST_SOURCE)},
                          on_tests=tests_gate, base_files={"pom.xml": real.POM, ".gitignore": "target/\n"})
    run.proto_requests, run.trees = proto_requests, trees
    return run


def test_real_owner_recovery_generates_only_the_implicated_file_and_reports_no_missing_sibling(tmp_path):
    run = _real_run(tmp_path, [real.BROKEN, real.FIXED])
    legacy = run.result.legacy_result

    assert legacy["status"] == "success", legacy.get("reason_codes")
    # the planned generation asked for both files; the recovery asked for Lib.java alone, once
    assert [(unit, Path(path).name) for unit, path, _mode in run.developer_requests] == [
        ("s3", "Lib.java"), ("s3", "Proto.java"), ("s5", "LibTest.java"), ("s3", "Lib.java")]
    assert len(run.proto_requests) == 1
    assert not [event for event in run.events if event.kind == "failure.recorded" and event.failure_type == "incomplete_generation"]
    # the person was shown the two files once, then the repaired file alone
    assert [(unit, sorted(Path(p).name for p in paths)) for unit, paths, _ in run.approvals if unit == "s3"] == [
        ("s3", ["Lib.java", "Proto.java"]), ("s3", ["Lib.java"])]
    # every tests gate - the recovery's included - ran on a tree that holds the sibling: the gates see the complete project
    assert run.trees and all(proto_there and lib_there for proto_there, lib_there, _ in run.trees)
    assert (run.workspace / PROTO).read_text() == PROTO_SOURCE and (run.workspace / LIB).read_text() == real.FIXED
    assert [event["owner_recovery_passed"] for event in legacy["plan_recovery_events"]] == [True]


def test_real_owner_recovery_refuses_an_answer_for_the_sibling_it_was_not_asked_for(tmp_path):
    """A write outside the recovery's scope: the scripted model answers the Lib.java request with a new Proto.java. Nothing of it is staged or
    written; the next answer repairs Lib.java."""
    run = _real_run(tmp_path, [real.BROKEN, (PROTO, "package com.example;\n\npublic class Proto { /* TAMPERED */ }\n"), real.FIXED])
    legacy = run.result.legacy_result

    assert legacy["status"] == "success", legacy.get("reason_codes")
    assert (run.workspace / PROTO).read_text() == PROTO_SOURCE                    # the sibling's bytes never changed
    assert len(run.proto_requests) == 1                                           # and it was never requested again
    refused = [event for event in run.events if event.kind == "failure.recorded" and event.failure_type == "operation_contract"]
    assert len(refused) == 1 and "INVALID_EDIT_PROTOCOL" in str(refused[0].message) and "Proto.java" in str(refused[0].message)
    assert [(unit, sorted(Path(p).name for p in paths)) for unit, paths, _ in run.approvals if unit == "s3"][-1] == ("s3", ["Lib.java"])
