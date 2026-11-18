"""OWNER-RECOVERY-NESTED-SCOPE-CONFLICT-DISCARDED-001 (owner ruling 2026-11-18 after Stage B run 9, 20261118T190202-e4da9437).

MEASURED in run 9: s7 (verification only) failed at runtime on a missing Spring bean; the failure was located in App.java, so s5 was reopened
with App.java as its only authorized file. s5's governed recovery could not repair it there and ended on a typed PLAN_SCOPE_DEFECT naming
ignite-config.xml (attempt-evidence record 561). The enforce loop never read that conflict: it broke out of the recovery and the run ended on
s7's older App.java conflict (record 567). s4, the owner of the XML, was never asked.

The contract: when the ONE owner a recovery cycle reopened returns a typed PLAN_SCOPE_DEFECT naming a file outside its authorized files,
kept every byte of its own files and wrote nothing undeclared, the episode continues on that conflict. Routing comes from the owner's
conflict; the diagnosis stays the originating failure's gate evidence, with the owner's reason appended. The owner stays needs_review and
gets no scope over the file; only a dependent verification that later passes against its current bytes settles it.

The unit boundary is scripted (``run_generation_workflow``), as in tests/test_enforce_owner_recovery_verifier_replay_001.py: the controller,
the enforce loop, the recovery planner and the owner resolver are the real ones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest
import test_enforce_owner_recovery_verifier_replay_001 as r7

from kriya.workflow.enforce_loop import promoted_owner_scope_conflict
from kriya.workflow.plan_revision import _plan_scope_conflict_files
from kriya.workflow.plan_schema import (
    ChangeKind,
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
)
from kriya.workflow.workflow_controller import _MAX_PLAN_SCOPE_REVISION_ATTEMPTS

CONFIG = "src/main/resources/app-config.xml"
APP = "src/main/java/com/example/App.java"
UNOWNED = "src/main/resources/nobody-owns-this.xml"
PLAIN_CONFIG = "<beans><bean id='cfg'/></beans>\n"
NODE_CONFIG = "<beans><bean id='cfg'/><bean id='node'/></beans>\n"
APP_SOURCE = "class App { Object node = context.getBean(\"node\"); }\n"
RUNTIME_FAILURE = "Exception in thread \"main\" NoSuchBeanDefinitionException: No bean named 'node' available\n\tat com.example.App.main(App.java:9)\n"
NESTED_REASON = "the search block for App.java matched 0 times against it, but was found inside app-config.xml"
OWNER_EVIDENCE = {"verification_results": [r7.COMPILE_EVIDENCE],
                  "gate_outcomes": [{"type": "compile", "success": True, "attempt": 1}, dict(r7.ZERO_TESTS)]}


def _plan() -> EngineeringPlan:
    return EngineeringPlan(plan_id="nestedconflict", kind=ChangeKind.TASK, subtasks=[
        Subtask(id="s4", description="create the configuration", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path=CONFIG, action=FileAction.CREATE)], provides=["configuration"], verification=[r7.COMPILE]),
        Subtask(id="s5", description="create the application", execution_method=ExecutionMethod.MODEL, depends_on=["s4"],
                planned_files=[PlannedFile(path=APP, action=FileAction.CREATE)], requires=["configuration"], provides=["application"],
                verification=[r7.COMPILE]),
        Subtask(id="s7", description="run the application", execution_method=ExecutionMethod.MODEL, execution_role=ExecutionRole.VERIFICATION,
                depends_on=["s5"], planned_files=[], requires=["application"], verification=[r7.RUN_TESTS]),
    ])


def nested_conflict(files: List[str], *, classification: str = "PLAN_SCOPE_DEFECT") -> Dict[str, Any]:
    """What retry_strategy sets for a misdirected edit of a reopened owner (the run-9 record-561 shape)."""
    return {"classification": classification, "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "misdirected_edit",
            "reason": NESTED_REASON, "required_files": list(files), "allowed_files": [APP], "attribution_tier": "locator",
            "grounded_owner_files": [], "raw_evidence": "MISDIRECTED EDIT: " + NESTED_REASON}


def refused(conflict: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"status": "failed", "quality_gates_passed": False, "files": [],
                              "failure_category": "plan_scope_revision_required" if conflict else "quality_gates_exhausted", **extra}
    if conflict is not None:
        result["plan_scope_conflict"] = conflict
    return result


def _fails_until_the_node_bean_exists(config_text: str, app_text: str) -> Optional[str]:
    return None if "id='node'" in config_text else RUNTIME_FAILURE


def _always_fails(config_text: str, app_text: str) -> Optional[str]:
    return RUNTIME_FAILURE


class Shape:
    """``recoveries[unit]``: per owner-recovery invocation of that unit, a new text for its own file (an accepted, changed repair), a ready
    unit result, or a callable(workspace) returning one. ``verdict(config_text, app_text)``: None when s7's run passes, else the gate output
    (always located in App.java, as the stack frame says)."""

    def __init__(self, recoveries: Dict[str, List[Any]], verdict: Callable[[str, str], Optional[str]] = _fails_until_the_node_bean_exists) -> None:
        self.recoveries = {unit: list(answers) for unit, answers in recoveries.items()}
        self.verdict = verdict
        self.calls: List[Dict[str, Any]] = []
        self.trees: List[Dict[str, str]] = []      # the two files as each owner-recovery invocation found them

    async def __call__(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        workspace = Path(kwargs["workspace_path"])
        unit = kwargs["current_subtask_id"]
        role = kwargs["execution_scope"].split("role=")[1]
        if unit == "s7":
            failure = self.verdict((workspace / CONFIG).read_text(), (workspace / APP).read_text())
            if failure is None:
                return {"status": "success", "quality_gates_passed": True, "files": [], "verification_results": [r7.TESTS_EVIDENCE]}
            return {"status": "failed", "quality_gates_passed": False, "files": [],
                    "last_failure": {"type": "run_verification", "message": failure, "raw_output": failure},
                    "plan_scope_conflict": {
                        "classification": "PLAN_SCOPE_DEFECT", "reason_code": "PLAN_SCOPE_REVISION_REQUIRED", "failure_type": "run_verification",
                        "reason": "the failing frame is in App.main", "required_files": [APP], "allowed_files": [],
                        "attribution_tier": "locator", "raw_evidence": failure}}
        path = workspace / (CONFIG if unit == "s4" else APP)
        if role == "planned":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(PLAIN_CONFIG if unit == "s4" else APP_SOURCE)
            return {"status": "success", "quality_gates_passed": True, "files": [CONFIG if unit == "s4" else APP], **OWNER_EVIDENCE}
        self.trees.append({CONFIG: (workspace / CONFIG).read_text(), APP: (workspace / APP).read_text()})
        answers = self.recoveries.get(unit) or []
        answer = answers.pop(0) if answers else refused()
        if callable(answer):
            answer = answer(workspace)
        if isinstance(answer, dict):
            return dict(answer)
        path.write_text(answer)
        return {"status": "success", "quality_gates_passed": True, "files": [CONFIG if unit == "s4" else APP], **OWNER_EVIDENCE}

    def roles(self) -> List[str]:
        return [f"{call['current_subtask_id']}:{call['execution_scope'].split('role=')[1]}" for call in self.calls]

    def call(self, role: str, nth: int = 0) -> Dict[str, Any]:
        return [call for call, name in zip(self.calls, self.roles(), strict=True) if name == role][nth]

    def units_after_planning(self) -> List[str]:
        return [call["current_subtask_id"] for call in self.calls][3:]


async def _run(tmp_path: Path, shape: Shape) -> Any:
    return await r7._run(tmp_path, shape, _plan())


# ------------------------------------------------------------------------------------------------------------ the run-9 shape

@pytest.mark.asyncio
async def test_run9_shape_the_owners_typed_conflict_reaches_the_owner_of_the_file_it_names(tmp_path):
    shape = Shape({"s5": [refused(nested_conflict([CONFIG]))], "s4": [NODE_CONFIG]})
    result = await _run(tmp_path, shape)

    assert shape.roles()[:5] == ["s4:planned", "s5:planned", "s7:planned", "s5:owner_recovery", "s4:owner_recovery"]
    assert shape.units_after_planning() == ["s5", "s4", "s7"]                       # s4 is attempted; the stale App.java conflict ended nothing
    assert shape.call("s5:owner_recovery")["allowed_write_relpaths"] == [APP]       # s5 never had the XML in its scope
    assert shape.call("s4:owner_recovery")["allowed_write_relpaths"] == [CONFIG]    # s4 runs under its own scope
    context = str(shape.call("s4:owner_recovery").get("recovery_contract_block") or "")
    assert "No bean named 'node' available" in context                              # the originating runtime evidence
    assert NESTED_REASON in context and "outside its authorized files" in context   # and the rerouting fact, appended
    assert (tmp_path / APP).read_text() == APP_SOURCE                               # s5's bytes never changed
    assert (tmp_path / CONFIG).read_text() == NODE_CONFIG
    assert r7._statuses(result) == {"s4": "completed", "s5": "completed", "s7": "completed"}   # s5 settled by the passing dependent run
    # The run ends exactly as the same plan ends when nothing ever fails (the scripted unit boundary closes no goal requirement, so
    # that run's own terminal word is the reference, not a literal): the recovery left nothing behind.
    (tmp_path / "reference").mkdir()
    reference = (await _run(tmp_path / "reference", Shape({}, verdict=lambda config_text, app_text: None))).legacy_result
    recovered = result.legacy_result
    assert recovered["quality_gates_passed"] is reference["quality_gates_passed"], (recovered.get("requirement_truth"), reference.get("requirement_truth"))
    assert recovered["status"] == reference["status"], (recovered.get("requirement_truth"), reference.get("requirement_truth"))
    assert recovered.get("requirement_truth") == reference.get("requirement_truth")
    assert [(event["reopened_owner"], event["owner_recovery_passed"]) for event in result.legacy_result["plan_recovery_events"]] == [
        ("s5", False), ("s4", True)]


@pytest.mark.asyncio
async def test_a_dependent_run_that_still_fails_after_the_second_owner_leaves_the_first_owner_needs_review(tmp_path):
    shape = Shape({"s5": [refused(nested_conflict([CONFIG]))], "s4": [NODE_CONFIG]}, verdict=_always_fails)
    result = await _run(tmp_path, shape)

    assert shape.units_after_planning()[:3] == ["s5", "s4", "s7"]
    statuses = r7._statuses(result)
    assert statuses["s5"] == "needs_review" and statuses["s7"] != "completed"
    assert result.legacy_result["status"] != "success"


# ------------------------------------------------------------------------------------------------------------ negative controls

@pytest.mark.asyncio
async def test_an_owner_failure_without_a_typed_conflict_ends_the_recovery_as_before(tmp_path):
    """Controls 1 and 2: an ordinary gate failure, and one whose only mention of the other file is text (a rejected target, model prose)."""
    for case, answer in enumerate((
        refused(),
        refused(rejected_generation_targets=[CONFIG],
                last_failure={"type": "compile", "message": f"the fix belongs in {CONFIG}", "raw_output": f"move this to {CONFIG}"}),
    )):
        shape = Shape({"s5": [answer], "s4": [NODE_CONFIG]})
        (tmp_path / f"case{case}").mkdir()
        result = await _run(tmp_path / f"case{case}", shape)

        assert shape.units_after_planning() == ["s5"], case           # s4 is never reopened, s7 is never retried
        assert r7._statuses(result)["s5"] == "needs_review" and result.legacy_result["status"] != "success", case
        assert result.legacy_result["reason_codes"] == ["PLAN_SCOPE_REVISION_REQUIRED"], case      # the terminal it always was


@pytest.mark.asyncio
async def test_a_conflict_that_is_not_a_typed_plan_scope_defect_is_not_promoted(tmp_path):
    shape = Shape({"s5": [refused(nested_conflict([CONFIG], classification="PLAN_SCOPE_INSUFFICIENT"))], "s4": [NODE_CONFIG]})
    await _run(tmp_path, shape)

    assert shape.units_after_planning() == ["s5"]


@pytest.mark.asyncio
async def test_an_owner_that_wrote_the_other_owners_file_is_not_promoted(tmp_path):
    """Control 3. The write scope of the real unit refuses this write (s5's scope is App.java, asserted above); if a result nevertheless
    reports it, the loop treats it as the undeclared write it is: no promotion, no second owner."""
    def writes_the_config(workspace: Path) -> Dict[str, Any]:
        (workspace / CONFIG).write_text(NODE_CONFIG)
        return refused(nested_conflict([CONFIG]), files=[CONFIG])

    shape = Shape({"s5": [writes_the_config], "s4": [NODE_CONFIG]})
    result = await _run(tmp_path, shape)

    assert shape.units_after_planning() == ["s5"]
    assert result.legacy_result["status"] != "success"


@pytest.mark.asyncio
async def test_an_owner_that_kept_an_unaccepted_change_of_its_own_file_is_not_promoted(tmp_path):
    """Control 7: a typed conflict AND a retained, unaccepted change of the owner's own authorized file stays the stop it was."""
    def changes_app_and_names_the_config(workspace: Path) -> Dict[str, Any]:
        (workspace / APP).write_text(APP_SOURCE + "// half a repair\n")
        return refused(nested_conflict([CONFIG]), files=[APP])

    shape = Shape({"s5": [changes_app_and_names_the_config], "s4": [NODE_CONFIG]})
    result = await _run(tmp_path, shape)

    assert shape.units_after_planning() == ["s5"]
    assert r7._statuses(result)["s5"] == "needs_review" and result.legacy_result["status"] != "success"


@pytest.mark.asyncio
async def test_a_conflict_naming_a_file_no_stage_owns_fails_closed(tmp_path):
    """Control 4: the existing resolver finds no owner; nobody is guessed and nothing else is reopened."""
    shape = Shape({"s5": [refused(nested_conflict([UNOWNED]))], "s4": [NODE_CONFIG]})
    result = await _run(tmp_path, shape)

    assert "s4" not in shape.units_after_planning()
    assert shape.roles().count("s5:owner_recovery") == 1
    assert r7._statuses(result)["s5"] == "needs_review" and result.legacy_result["status"] != "success"


@pytest.mark.asyncio
async def test_owners_that_keep_naming_each_other_stop_at_the_existing_cycle_bound(tmp_path):
    """Control 5: s5 names the XML, s4 names App.java, and so on. The episode's own cycle cap ends it; no new bound exists."""
    back = dict(nested_conflict([APP]), allowed_files=[CONFIG])
    shape = Shape({"s5": [refused(nested_conflict([CONFIG]))] * 5, "s4": [refused(back)] * 5})
    result = await _run(tmp_path, shape)

    reopened = [role for role in shape.roles() if role.endswith(":owner_recovery")]
    assert reopened[:2] == ["s5:owner_recovery", "s4:owner_recovery"]
    assert len(reopened) <= _MAX_PLAN_SCOPE_REVISION_ATTEMPTS
    assert "s7" not in shape.units_after_planning()                    # nothing changed, so the dependent run was never repeated
    assert result.legacy_result["status"] != "success"
    assert all(tree == {CONFIG: PLAIN_CONFIG, APP: APP_SOURCE} for tree in shape.trees) and len(shape.trees) == len(reopened)


# ------------------------------------------------------------------------------------------------------------ the pure decision

ORIGINATING = {"classification": "PLAN_SCOPE_DEFECT", "failure_type": "run_verification", "reason": "the failing frame is in App.main",
               "required_files": [APP], "allowed_files": [], "attribution_tier": "locator", "raw_evidence": RUNTIME_FAILURE}


def _promoted(owner_result: Dict[str, Any], authorized: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return promoted_owner_scope_conflict(owner_result, "s5", authorized or [APP], ORIGINATING, _plan_scope_conflict_files)


def test_routing_is_the_owners_conflict_and_diagnosis_is_the_originating_failure():
    promoted = _promoted(refused(nested_conflict([CONFIG])))

    assert promoted is not None
    assert _plan_scope_conflict_files(promoted) == [CONFIG]
    assert promoted["classification"] == "PLAN_SCOPE_DEFECT" and promoted["attribution_tier"] == "locator"
    assert promoted["failure_type"] == "run_verification" and promoted["raw_evidence"] == RUNTIME_FAILURE
    assert promoted["reason"].startswith("the failing frame is in App.main; ")
    assert NESTED_REASON in promoted["reason"] and "misdirected_edit" in promoted["reason"]


@pytest.mark.parametrize("owner_result", [
    {},
    refused(),
    refused(nested_conflict([CONFIG], classification="PLAN_SCOPE_INSUFFICIENT")),
    refused(nested_conflict([APP])),                                              # names only the owner's own authorized file
    refused(nested_conflict([])),
    {"rejected_generation_targets": [CONFIG], "last_failure": {"message": f"fix {CONFIG}"}},
])
def test_nothing_else_is_promoted(owner_result):
    assert _promoted(owner_result) is None
