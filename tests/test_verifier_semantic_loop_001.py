"""VERIFIER-SEMANTIC-AUTHORITY-001 (WS3-C slice 2, owner rulings 2026-11-14): the loop seams that obey ``kriya/workflow/verifier_semantic.py``.

The REAL controller and the REAL unit (tests/_enforce_unit_harness.py: only the model transport and the toolchain gates are scripted). One unit ``s1``
writes production ``shop.py`` and a test ``tests/test_shop.py``; the tests gate answers from the bytes of the tree it is given and returns the structured
report dict in the shape ``kriya/tools/test_execution.py`` produces. Generic synthetic names only.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from _enforce_unit_harness import run_enforce

from kriya.policy.approval import TypedApprovalCallback, VerifierSemanticDecision, VerifierSemanticSubject
from kriya.workflow import verifier_semantic as vs
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow import WorkflowEngine

LIB, TEST = "shop.py", "tests/test_shop.py"
GOAL = "page_size() is the number of items on one page: 20."
LIB_20, LIB_10 = "def page_size():\n    return 20\n", "def page_size():\n    return 10\n"


def _test(expected: int, marker: str = "V0") -> str:
    return f"from shop import page_size\n\n\ndef test_size():  # {marker}\n    assert page_size() == {expected}\n"


def _plan(extra_units: Optional[List[Dict[str, Any]]] = None) -> EngineeringPlan:
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": f"write {LIB} and {TEST}", "execution_method": "model",
                      "planned_files": [{"path": LIB, "action": "create"}, {"path": TEST, "action": "create"}],
                      "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}]},
                     *(extra_units or [])]})


def _report(status: str, identity: str = "tests.test_shop.test_size") -> Dict[str, Any]:
    return {"completeness": "COMPLETE", "runner": "pytest",
            "test_cases": [{"identity": identity, "classname": identity.rsplit(".", 1)[0], "name": identity.rsplit(".", 1)[1], "status": status}]}


def _gate_result(tree: Path) -> Dict[str, Any]:
    """What the scripted tests gate answers for a tree: the test passes exactly when its expectation equals what the production file returns."""
    if not (tree / TEST).exists():
        return {"success": True, "output": "no tests ran"}
    if not (tree / LIB).exists():
        return {"success": False, "output": f"ImportError while importing test module '{TEST}'.\nE   ModuleNotFoundError: No module named 'shop'\n"}
    expected = (tree / TEST).read_text().split("== ")[1].strip()
    actual = (tree / LIB).read_text().split("return ")[1].strip()
    if expected == actual:
        return {"success": True, "output": f"{TEST} .\n1 passed in 0.01s", "test_execution": _report("passed")}
    return {"success": False, "test_execution": _report("failed"),
            "output": f"{TEST} F\n    def test_size():\n>       assert page_size() == {expected}\nE       assert {actual} == {expected}\n\n{TEST}:5: AssertionError\n1 failed in 0.01s"}


# ------------------------------------------------------------------ A. why V1 is regenerated INSIDE the unit (the pre-fix reproducer)

def test_reinvoking_an_unfinished_unit_over_only_its_verifier_path_loses_the_production_candidate(tmp_path):
    """The disproved shape (owner ruling 2026-11-14, RULING 1): a unit whose own verifier failed has applied NOTHING, so a second invocation of the unit
    with the owner-recovery scope (generation targets and write scope = the verifier path alone) runs its gates on a tree without the production file
    the verifier tested. Owner recovery can use that scope only because its owner already completed and its other files are in the base tree."""
    seen: List[Dict[str, Any]] = []
    invocation = {"n": 0}
    real_unit = WorkflowEngine.run_generation_workflow

    async def unit_then_one_path_reinvocation(self, *args, **kwargs):
        invocation["n"] = 1
        first = await real_unit(self, *args, **kwargs)
        assert first.get("quality_gates_passed") is not True
        assert not (Path(kwargs["workspace_path"]) / LIB).exists(), "an unfinished unit applied nothing to the base tree"
        invocation["n"] = 2
        await real_unit(self, *args, **{**kwargs, "predetermined_architect_files": [TEST], "allowed_write_relpaths": [TEST]})
        return first

    def tests_gate(validator: Any) -> Dict[str, Any]:
        tree = Path(validator.workspace_path)
        seen.append({"invocation": invocation["n"], "production_present": (tree / LIB).exists(), "verifier_present": (tree / TEST).exists()})
        return _gate_result(tree)

    with patch.object(WorkflowEngine, "run_generation_workflow", new=unit_then_one_path_reinvocation):
        run_enforce(tmp_path, plan=_plan(), goal=GOAL, units={"s1": {LIB: LIB_20, TEST: _test(10)}}, on_tests=tests_gate, mode="guardrails")
    first = [gate for gate in seen if gate["invocation"] == 1 and gate["verifier_present"]]
    second = [gate for gate in seen if gate["invocation"] == 2 and gate["verifier_present"]]
    assert first and all(gate["production_present"] for gate in first), "the unit's own gates ran its verifier beside its production candidate"
    assert second and not any(gate["production_present"] for gate in second), "the one-path re-invocation ran the verifier with no production code"


# ------------------------------------------------------------------ the scripted scene

class Scene:
    """One scripted enforce run of ``s1``. ``lib`` / ``test``: the Developer's successive answers for each file (the last one repeats; a
    ``(path, text)`` pair answers for another file). ``semantic``: the person's successive answers to the semantic review (``None`` = no decision;
    the last one repeats). ``gate``: ``(tree, call_number) -> result`` (default: ``_gate_result``)."""

    def __init__(self, *, lib: List[Any], test: List[Any], semantic: Optional[List[Optional[str]]] = None, gate: Any = None, typed: bool = True,
                 plan: Optional[EngineeringPlan] = None, base_files: Optional[Dict[str, str]] = None, units: Optional[Dict[str, Any]] = None) -> None:
        self.lib, self.test, self.semantic, self.gate, self.typed = list(lib), list(test), list(semantic or []), gate, typed
        self.plan, self.base_files, self.units = plan, base_files, units
        self.names = (LIB, TEST)
        self.subjects: List[VerifierSemanticSubject] = []
        self.gate_trees: List[Dict[str, Optional[str]]] = []
        self.unit_results: List[Dict[str, Any]] = []
        self.scopes: List[Dict[str, Any]] = []

    def _next(self, answers: List[Any]) -> Any:
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def _semantic(self, subject: VerifierSemanticSubject) -> Any:
        self.subjects.append(subject)
        word = self.semantic.pop(0) if len(self.semantic) > 1 else (self.semantic[0] if self.semantic else None)
        return VerifierSemanticDecision.decided_as(word, subject.fingerprint) if word else VerifierSemanticDecision.none("declined")

    def _tests(self, validator: Any) -> Dict[str, Any]:
        tree = Path(validator.workspace_path)
        self.gate_trees.append({name: ((tree / name).read_text() if (tree / name).exists() else None) for name in self.names})
        return self.gate(tree, len(self.gate_trees)) if self.gate is not None else _gate_result(tree)

    def run(self, tmp_path: Path, mode: str = "human-in-the-loop") -> Any:
        real_unit = WorkflowEngine.run_generation_workflow
        real_scope = vs.UnitSemantics.attempt_scope
        scene = self

        async def unit(engine, *args, **kwargs):
            result = await real_unit(engine, *args, **kwargs)
            scene.unit_results.append(result)
            return result

        def scope(unit_semantics, ctx, allowlist_mode):
            real = real_scope(unit_semantics, ctx, allowlist_mode)
            record: Dict[str, Any] = {"ctx": ctx, "before": (list(ctx.allowed_write_relpaths), ctx.write_scope_mode)}
            scene.scopes.append(record)
            enter, leave = real.__enter__, real.__exit__

            class Recorded:
                def __enter__(self):
                    enter()
                    record["during"] = (list(ctx.allowed_write_relpaths), ctx.write_scope_mode)
                    return self

                def __exit__(self, *exc):
                    leave(*exc)
                    record["after"] = (list(ctx.allowed_write_relpaths), ctx.write_scope_mode)

            return Recorded()

        def change(files, reason):
            return True

        callback = TypedApprovalCallback(change, lambda subject: None, None, self._semantic) if self.typed else change
        units = self.units or {"s1": {LIB: lambda: self._next(self.lib), TEST: lambda: self._next(self.test)}}
        with patch.object(WorkflowEngine, "run_generation_workflow", new=unit), patch.object(vs.UnitSemantics, "attempt_scope", new=scope):
            self.result = run_enforce(tmp_path, plan=self.plan or _plan(), goal=GOAL, units=units, on_tests=self._tests, mode=mode,
                                      callback=callback, base_files=self.base_files)
        return self.result

    # -- what happened
    def rulings(self) -> List[Dict[str, Any]]:
        return [event.details for event in self.result.events if event.kind == "verifier_semantic.ruling"]

    def requested(self) -> List[str]:
        """The file of every Developer request, in order."""
        return [path for _unit, path, _first in self.result.developer_requests]

    def applied(self) -> Dict[str, Optional[str]]:
        workspace = Path(self.result.workspace)
        return {name: ((workspace / name).read_text() if (workspace / name).exists() else None) for name in self.names}

    def stop(self) -> Dict[str, Any]:
        return self.unit_results[-1]


def _stopped_for_review(scene: Scene, reason_code: str) -> None:
    result = scene.stop()
    assert result.get("failure_category") == "verifier_semantic_authority_required", result.get("failure_category")
    assert str(result.get("environment_failure")).startswith(f"VERIFIER_SEMANTIC_AUTHORITY_REQUIRED: {reason_code}:"), result.get("environment_failure")
    assert not result.get("plan_scope_conflict") and result.get("quality_gates_passed") is not True
    assert scene.applied() == {LIB: None, TEST: None}, "nothing is applied when a generated verifier has no semantic authority"


# ------------------------------------------------------------------ C. the ordinary retry is intercepted

def test_unattended_a_failing_run_authored_verifier_stops_for_review_before_any_retry(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(10)])
    scene.run(tmp_path, mode="guardrails")
    _stopped_for_review(scene, vs.REASON_UNATTENDED)
    assert sorted(scene.requested()) == [LIB, TEST], "one generation: the failure never reached an ordinary Developer retry"
    assert scene.subjects == [] and [r["action"] for r in scene.rulings()] == ["needs_review"]


def test_a_failure_with_no_run_authored_executed_verifier_keeps_the_historical_retry(tmp_path):
    """Zero subjects: the same failing gate with no structured report (nothing proves what executed) is retried exactly as before WS3-C, and the
    policy owner rules on nothing."""
    def gate(tree: Path, _call: int) -> Dict[str, Any]:
        result = dict(_gate_result(tree))
        result.pop("test_execution", None)
        return result

    scene = Scene(lib=[LIB_20], test=[_test(10)], gate=gate)
    scene.run(tmp_path, mode="guardrails")
    assert scene.rulings() == [] and scene.subjects == []
    assert len(scene.requested()) > 2, "the ordinary retry loop ran"
    assert scene.stop().get("failure_category") != "verifier_semantic_authority_required"


# ------------------------------------------------------------------ D. expectation_invalid: exactly one in-unit regeneration of exactly V

def test_expectation_invalid_regenerates_exactly_the_verifier_once_beside_the_kept_production_candidate(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(10), _test(20, "V1")], semantic=["expectation_invalid", "expectation_valid"])
    scene.run(tmp_path)
    assert scene.requested() == [LIB, TEST, TEST] or scene.requested() == [TEST, LIB, TEST], scene.requested()
    v1_gates = [tree for tree in scene.gate_trees if tree[TEST] == _test(20, "V1")]
    assert v1_gates and all(tree[LIB] == LIB_20 for tree in v1_gates), "V1 ran beside the production candidate the unit already had"
    assert [(s.review_phase, s.verifier_files[0][0]) for s in scene.subjects] == [("failing_verifier", TEST), ("passing_verifier", TEST)]
    assert scene.subjects[0].verifier_files != scene.subjects[1].verifier_files, "the regenerated bytes needed fresh semantic authority"
    assert [r["action"] for r in scene.rulings()] == ["regenerate_verifier"]
    assert scene.applied() == {LIB: LIB_20, TEST: _test(20, "V1")}
    narrowed = [record for record in scene.scopes if record["during"][0] == [TEST]]
    assert len(narrowed) == 1, "exactly one attempt had the one-path write scope"
    assert all(record["after"] == record["before"] and sorted(record["before"][0]) == [LIB, TEST] for record in scene.scopes)


def test_a_passing_run_authored_verifier_needs_a_decision_before_terminal_regression_and_is_not_asked_twice(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(20)], semantic=["expectation_valid"])
    scene.run(tmp_path)
    assert [s.review_phase for s in scene.subjects] == ["passing_verifier"], "one card: the terminal regression's same subject is already settled"
    assert len(scene.gate_trees) >= 2, "the candidate gate and the terminal regression both ran the verifier"
    assert scene.applied() == {LIB: LIB_20, TEST: _test(20)} and scene.rulings() == []


def test_unattended_a_passing_run_authored_verifier_is_not_applied(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(20)])
    scene.run(tmp_path, mode="guardrails")
    _stopped_for_review(scene, vs.REASON_UNATTENDED)
    assert scene.rulings()[0]["seam"] == "pass_before_terminal_regression"


@pytest.mark.parametrize("answer_for", [LIB, "tests/test_other.py", "tests/test_shop_renamed.py"])
def test_the_regeneration_answer_cannot_write_production_a_sibling_or_a_renamed_verifier(tmp_path, answer_for):
    """The one-path write scope is the write gate's: an answer for any other path changes nothing, is not a regeneration of V, and there is no
    further Developer request."""
    scene = Scene(lib=[LIB_20], test=[_test(10), (answer_for, LIB_10 if answer_for == LIB else _test(20, "ELSEWHERE"))],
                  semantic=["expectation_invalid"])
    scene.run(tmp_path)
    result = scene.stop()
    assert result.get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert result.get("failure_category") in ("verifier_semantic_authority_required", "unauthorized_generation_target"), result.get("failure_category")
    assert all(tree[LIB] == LIB_20 and tree[TEST] == _test(10) for tree in scene.gate_trees), "no gate ever saw a changed production file or verifier"
    assert len(scene.requested()) == 3, "the two planned files, then the ONE regeneration request"
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 1


def test_there_is_no_second_regeneration(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(10), _test(11, "V1"), _test(20, "V2")], semantic=["expectation_invalid"])
    scene.run(tmp_path)
    _stopped_for_review(scene, vs.REASON_REGENERATION_SPENT)
    assert scene.requested().count(TEST) == 2 and _test(20, "V2") not in [tree[TEST] for tree in scene.gate_trees]
    assert [s.verifier_files[0][1] for s in scene.subjects][0] != [s.verifier_files[0][1] for s in scene.subjects][1]
    assert [r["action"] for r in scene.rulings()] == ["regenerate_verifier", "needs_review"]


def test_a_regeneration_that_throws_before_writing_leaves_the_write_scope_as_it_was(tmp_path):
    scene = Scene(lib=[LIB_20], test=[_test(10), RuntimeError("the model transport failed")], semantic=["expectation_invalid"])
    scene.run(tmp_path)
    assert scene.stop().get("quality_gates_passed") is not True and scene.applied() == {LIB: None, TEST: None}
    assert [record["during"][0] for record in scene.scopes].count([TEST]) == 1
    assert all(record["after"] == record["before"] and sorted(record["before"][0]) == [LIB, TEST] for record in scene.scopes), scene.scopes
    assert scene.requested().count(TEST) == 2, "no second regeneration request after the failed one"


def test_the_attempt_scope_is_restored_on_an_exception_and_the_next_attempt_sees_the_original_scope():
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True)
    ctx = SimpleNamespace(allowed_write_relpaths=[LIB, TEST], write_scope_mode="ALLOWLIST")
    original = ctx.allowed_write_relpaths
    unit.pending = (TEST, "0" * 64)
    with pytest.raises(TimeoutError):
        with unit.attempt_scope(ctx, "ALLOWLIST"):
            assert ctx.allowed_write_relpaths == [TEST] and unit.regenerating == (TEST, "0" * 64) and unit.pending is None
            raise TimeoutError("deadline")
    assert ctx.allowed_write_relpaths is original and ctx.write_scope_mode == "ALLOWLIST"
    with unit.attempt_scope(ctx, "ALLOWLIST"):
        assert ctx.allowed_write_relpaths == [LIB, TEST], "an unrelated next attempt is not narrowed"
    unrestricted = SimpleNamespace(allowed_write_relpaths=[], write_scope_mode="UNRESTRICTED")
    unit.pending = (TEST, "0" * 64)
    with unit.attempt_scope(unrestricted, "ALLOWLIST"):
        assert (unrestricted.allowed_write_relpaths, unrestricted.write_scope_mode) == ([TEST], "ALLOWLIST")
    assert (unrestricted.allowed_write_relpaths, unrestricted.write_scope_mode) == ([], "UNRESTRICTED")
    unit.denied.add(TEST)                                    # an accepted or spent verifier: write-denied for every later attempt, per attempt
    with unit.attempt_scope(ctx, "ALLOWLIST"):
        assert ctx.allowed_write_relpaths == [LIB]
    assert ctx.allowed_write_relpaths is original


@pytest.mark.parametrize("budgets, implicated, admitted", [
    ({}, [TEST], True),
    ({"targeted_retry_count": 3}, [TEST], False),            # the targeted allowance is spent: never reset to make room
    ({}, [LIB, TEST], False),                                # the next attempt would not be for exactly the verifier
    ({}, None, False),
])
def test_a_regeneration_the_retry_policy_does_not_admit_as_a_targeted_attempt_is_needs_review_and_resets_no_budget(budgets, implicated, admitted):
    from kriya.workflow import retry_strategy
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    state.attempt_number = 1
    state.last_implicated_files = implicated
    for name, value in budgets.items():
        setattr(state.budgets, name, value)
    before = dataclasses.asdict(state.budgets)
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True)
    unit.pending = (TEST, "0" * 64)
    ctx = SimpleNamespace(verifier_semantic=unit, max_retries=4, targeted_max_retries=3)
    with patch.object(retry_strategy, "fallback_routing_for_context", return_value=SimpleNamespace(available=False)):
        retry_strategy._close_unadmitted_verifier_regeneration(state, ctx)
    assert dataclasses.asdict(state.budgets) == before, "no counter was reset or replenished"
    if admitted:
        assert state.environment_failure is None and unit.pending == (TEST, "0" * 64)
    else:
        assert state.environment_failure.startswith(f"VERIFIER_SEMANTIC_AUTHORITY_REQUIRED: {vs.REASON_REGENERATION_NOT_ADMITTED}:")
        assert unit.pending is None and unit.denied == {TEST}


# ------------------------------------------------------------------ E. the second pass seam (after terminal regression, before apply)

MORE = "tests/test_more.py"


def test_a_verifier_only_the_terminal_regression_executed_is_reviewed_before_apply(tmp_path):
    more = "from shop import page_size\n\n\ndef test_more():\n    assert page_size() > 0\n"
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "write the shop and its tests", "execution_method": "model",
                      "planned_files": [{"path": path, "action": "create"} for path in (LIB, TEST, MORE)],
                      "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}]}]})

    def gate(_tree: Path, call: int) -> Dict[str, Any]:
        cases = [("tests.test_shop.test_size", "passed")] + ([("tests.test_more.test_more", "passed")] if call > 1 else [])
        return {"success": True, "output": f"{len(cases)} passed in 0.01s", "test_execution": {
            "completeness": "COMPLETE", "runner": "pytest",
            "test_cases": [{"identity": i, "classname": i.rsplit(".", 1)[0], "name": i.rsplit(".", 1)[1], "status": s} for i, s in cases]}}

    scene = Scene(lib=[LIB_20], test=[_test(20)], semantic=["expectation_valid"], gate=gate, plan=plan,
                  units={"s1": {LIB: LIB_20, TEST: _test(20), MORE: more}})
    scene.run(tmp_path)
    assert [s.verifier_files[0][0] for s in scene.subjects] == [TEST, MORE], "the earlier subject is not asked again; the new one is"
    assert (Path(scene.result.workspace) / MORE).read_text() == more and scene.rulings() == []

    refused = Scene(lib=[LIB_20], test=[_test(20)], semantic=["expectation_valid", "cannot_decide"], gate=gate, plan=plan,
                    units={"s1": {LIB: LIB_20, TEST: _test(20), MORE: more}})
    refused.run(tmp_path / "second")
    _stopped_for_review(refused, vs.REASON_CANNOT_DECIDE)
    assert refused.rulings()[0]["seam"] == "pass_before_apply" and refused.rulings()[0]["path"] == MORE


# ------------------------------------------------------------------ unknown provenance, and what is NOT a subject

@pytest.mark.parametrize("mode", ["human-in-the-loop", "guardrails"])
def test_an_executed_report_whose_run_authored_source_cannot_be_mapped_is_needs_review(tmp_path, mode):
    """A structural-resolution failure is never a semantic-authority bypass: cases executed, a source this run wrote cannot be parsed, so whether
    the cases are its own is unknown. Nobody is asked about a subject that cannot be shown exactly, and nothing is applied."""
    broken = _test(20).replace("def test_size():", "def test_size(:")
    scene = Scene(lib=[LIB_20], test=[broken], semantic=["expectation_valid"],
                  gate=lambda _tree, _call: {"success": True, "output": "1 passed in 0.01s", "test_execution": _report("passed")})
    scene.run(tmp_path, mode=mode)
    _stopped_for_review(scene, vs.REASON_SUBJECT_UNREADABLE)
    assert scene.subjects == [] and len(scene.requested()) == 2


def _brownfield_plan() -> EngineeringPlan:
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": f"change {LIB}", "execution_method": "model", "planned_files": [{"path": LIB, "action": "modify"}],
                      "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}]}]})


@pytest.mark.parametrize("result", [
    # the owner's control: a production change breaks the compilation of an unchanged pre-existing test - nothing executed, no subject exists
    {"success": False, "output": "[ERROR] COMPILATION ERROR :\n[ERROR] tests/test_old.py:[3,1] cannot find symbol page_size\n",
     "test_phase": {"phase_class": "TEST_SOURCE_COMPILE_FAILURE"}},
    # an unchanged pre-existing test executed and failed: mapping proves it is not run-authored
    {"success": False, "output": "tests/test_old.py F\n>       assert page_size() == 10\nE       assert 20 == 10\n\ntests/test_old.py:5: AssertionError\n1 failed in 0.01s",
     "test_execution": _report("failed", "tests.test_old.test_old")},
])
def test_a_production_change_that_fails_an_unchanged_pre_existing_test_is_outside_semantic_interception(tmp_path, result):
    old = "from shop import page_size\n\n\ndef test_old():\n    assert page_size() == 10\n"
    scene = Scene(lib=[LIB_20, LIB_20 + "# again\n", LIB_20 + "# and again\n"], test=[_test(20)], semantic=["expectation_valid"],
                  gate=lambda _tree, _call: dict(result), plan=_brownfield_plan(), base_files={LIB: LIB_10, "tests/test_old.py": old},
                  units={"s1": (LIB, lambda: scene._next(scene.lib))})
    scene.run(tmp_path, mode="guardrails")
    assert scene.rulings() == [] and scene.subjects == [], "WS3-C manufactured no subject and asked for no semantic review"
    assert len(scene.requested()) > 1, "the pre-WS3 repair path (the ordinary Developer retry) remained available"
    assert scene.stop().get("failure_category") != "verifier_semantic_authority_required"


# ------------------------------------------------------------------ expectation_valid: the verifier is fixed, production only by strict owner authority

def test_expectation_valid_with_a_locator_that_names_only_the_verifier_selects_no_production_owner(tmp_path):
    scene = Scene(lib=[LIB_10], test=[_test(20)], semantic=["expectation_valid"])
    scene.run(tmp_path)
    _stopped_for_review(scene, vs.REASON_NO_PRODUCTION_OWNER)
    assert "PRODUCTION_OWNER_LOCATOR" in scene.stop()["environment_failure"]
    assert len(scene.requested()) == 2 and len(scene.subjects) == 1, "accepted, and still nobody may be repaired from a raw assertion"


JLIB, JTEST = "src/main/java/com/example/Shop.java", "src/test/java/com/example/ShopTest.java"
POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n  <groupId>com.example</groupId>\n'
       "  <artifactId>shop</artifactId>\n  <version>1.0</version>\n</project>\n")


def _jlib(size: int) -> str:
    return f"package com.example;\n\npublic class Shop {{\n    public int pageSize() {{ return {size}; }}\n}}\n"


def _jtest(expected: int, marker: str = "V0") -> str:
    return ("package com.example;\n\nclass ShopTest {\n    @org.junit.jupiter.api.Test\n    void size() {  // " + marker + "\n"
            f"        org.junit.jupiter.api.Assertions.assertEquals({expected}, new Shop().pageSize());\n    }}\n}}\n")


def _java_gate(tree: Path, _call: int) -> Dict[str, Any]:
    """A JVM-shaped gate: on a mismatch the runtime's own stack trace carries a frame inside the production file (the locator the gate parses)."""
    if not (tree / JTEST).exists():
        return {"success": True, "output": "no tests ran"}
    expected = (tree / JTEST).read_text().split("assertEquals(")[1].split(",")[0]
    actual = (tree / JLIB).read_text().split("return ")[1].split(";")[0]
    report = lambda status: _report(status, "com.example.ShopTest.size")  # noqa: E731 - a two-use local shorthand
    if expected == actual:
        return {"success": True, "output": "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0", "test_execution": report("passed")}
    return {"success": False, "test_execution": report("failed"), "output": (
        "[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE! -- in com.example.ShopTest\n"
        f"org.opentest4j.AssertionFailedError: expected: <{expected}> but was: <{actual}>\n"
        "\tat com.example.Shop.pageSize(Shop.java:4)\n\tat com.example.ShopTest.size(ShopTest.java:6)\n")}


def test_expectation_valid_with_a_strict_production_owner_repairs_production_only_and_the_verifier_is_not_asked_about_again(tmp_path):
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "write the shop and its test", "execution_method": "model",
                      "planned_files": [{"path": JLIB, "action": "create"}, {"path": JTEST, "action": "create"}],
                      "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "test", "description": "test"}]}]})
    lib, test = [_jlib(10), [("    public int pageSize() { return 10; }", "    public int pageSize() { return 20; }")]], [_jtest(20), _jtest(10, "REWRITTEN")]
    scene = Scene(lib=lib, test=test, semantic=["expectation_valid"], gate=_java_gate, plan=plan,
                  base_files={"pom.xml": POM, ".gitignore": "target/\n"},
                  units={"s1": {JLIB: lambda: scene._next(lib), JTEST: lambda: scene._next(test)}})
    scene.names = (JLIB, JTEST)
    with patch("kriya.tools.lsp.find_jdtls", return_value=None):       # no language server: the gates are scripted, nothing here starts a JVM
        scene.run(tmp_path)
    assert scene.requested().count(JTEST) == 1, "the accepted verifier was never regenerated"
    assert scene.requested().count(JLIB) == 2 and scene.applied() == {JLIB: _jlib(20), JTEST: _jtest(20)}
    assert [s.review_phase for s in scene.subjects] == ["failing_verifier"], "fail VALID -> repair -> the same bytes pass: the authority stands"
    [ruling] = scene.rulings()
    assert (ruling["action"], ruling["owner_id"], ruling["owner_files"]) == ("repair_production_owner", "s1", [JLIB])
    repair = [record for record in scene.scopes if record["during"][0] != record["before"][0]]
    assert repair and all(record["during"][0] == [JLIB] for record in repair), "the accepted verifier was write-denied during the production repair"


# ------------------------------------------------------------------ F. side routes

def _self_correction_scene(tmp_path, gate) -> Any:
    from kriya.core.kernel import Kernel

    real_init = Kernel.__init__

    def init(kernel, *args, **kwargs):
        real_init(kernel, *args, **kwargs)
        kernel.config.autonomy.self_correction_loop_enabled = True

    scene = Scene(lib=[LIB_20], test=[_test(10)], gate=gate)
    with patch.object(Kernel, "__init__", new=init):
        scene.run(tmp_path, mode="guardrails")
    # the REAL repair loop leaves its record on the failed gate's outcome (Failure.self_correction_attempt): that is how its run is observed
    calls = [outcome["type"] for outcome in scene.result.gate_outcomes if outcome.get("self_correction_attempt")]
    return scene, calls


def test_the_targeted_test_self_correction_never_runs_on_a_failing_run_authored_verifier(tmp_path):
    scene, calls = _self_correction_scene(tmp_path, None)
    assert calls == [], "the autonomous rewrite loop was not offered the run-authored verifier's failure"
    _stopped_for_review(scene, vs.REASON_UNATTENDED)


def test_the_targeted_test_self_correction_is_unchanged_when_no_subject_is_identified(tmp_path):
    def gate(tree: Path, _call: int) -> Dict[str, Any]:
        result = dict(_gate_result(tree))
        result.pop("test_execution", None)
        return result

    scene, calls = _self_correction_scene(tmp_path, gate)
    assert calls and set(calls) == {"targeted_test"} and scene.rulings() == []


def test_the_side_route_guard_is_the_policy_owners_decision():
    unit = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL,
                            read_bytes={TEST: _test(10).encode(), LIB: LIB_20.encode()}.get)
    failing = {"success": False, "test_execution": _report("failed")}
    assert unit.autonomous_rewrite_permitted([LIB, TEST], {"success": False, "output": "x"})
    assert not unit.autonomous_rewrite_permitted([LIB, TEST], failing)
    assert unit.autonomous_rewrite_permitted([LIB], failing), "the failing test is not run-authored: historical behaviour"
    assert unit.autonomous_rewrite_permitted([LIB, TEST], {"success": True, "test_execution": _report("passed")})
    unparsable = vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL,
                                  read_bytes={TEST: b"def test_size(:\n"}.get)
    assert not unparsable.autonomous_rewrite_permitted([TEST], failing), "unknown provenance of an executed case"
    unit.regenerating = (TEST, "0" * 64)
    assert not unit.autonomous_rewrite_permitted([LIB, TEST], {"success": False, "output": "x"}), "nothing is rewritten while the one answer is judged"


# ------------------------------------------------------------------ G. a direct run (no controller, no validated plan)

def _direct_unit(answer: str) -> Any:
    subjects: List[VerifierSemanticSubject] = []

    def review(subject: VerifierSemanticSubject) -> Any:
        subjects.append(subject)
        return VerifierSemanticDecision.decided_as(answer, subject.fingerprint)

    files = {TEST: _test(10).encode(), LIB: LIB_20.encode()}
    unit = vs.unit_semantics(None, run_id="run-1", unit_id=None, goal=GOAL, structured_plan=None, allowlist_scope=False, attended=True,
                             callback=TypedApprovalCallback(lambda files, reason: True, lambda subject: None, None, review), read_bytes=files.get)
    unit.observe({"success": False, "test_execution": _report("failed")})
    return unit, subjects


def _direct_failure(unit: Any, located: List[str]) -> Any:
    return asyncio.run(unit.on_gate_failure(
        written=[LIB, TEST], failure_type="targeted_test", outcome="assert 20 == 10", located_paths=located,
        is_workspace_artifact=lambda path: True, owners_of=lambda path: []))


def test_a_direct_run_never_regenerates_expectation_invalid_is_needs_review_and_changes_nothing():
    unit, subjects = _direct_unit("expectation_invalid")
    assert not unit.regenerable and unit.plan_hash == vs.NO_STRUCTURED_PLAN
    ruling = _direct_failure(unit, [TEST])
    assert ruling.stop.reason_code == vs.REASON_REGENERATION_UNAVAILABLE and ruling.regenerate is None and unit.pending is None
    assert len(subjects) == 1 and unit.denied == {TEST}
    ctx = SimpleNamespace(allowed_write_relpaths=[], write_scope_mode="UNRESTRICTED")
    with unit.attempt_scope(ctx, "ALLOWLIST"):
        assert (ctx.allowed_write_relpaths, ctx.write_scope_mode) == ([], "UNRESTRICTED"), "no regeneration scope was ever opened"


def test_a_direct_run_with_expectation_valid_has_no_validated_plan_owner_and_is_needs_review():
    """TRACED: only the enforce loop passes a structured plan to the unit; a direct run has no plan ownership, so even a locator inside a
    production file selects nobody."""
    unit, _subjects = _direct_unit("expectation_valid")
    ruling = _direct_failure(unit, [LIB, TEST])
    assert ruling.stop.reason_code == vs.REASON_NO_PRODUCTION_OWNER and "PRODUCTION_OWNER_ARTIFACT_HAS_NO_PLAN_OWNER" in ruling.stop.detail
    assert ruling.owner is None and ruling.regenerate is None


def test_only_the_enforce_loop_gives_a_unit_a_structured_plan():
    """The direct-run trace, pinned: every ``run_generation_workflow`` call that passes a plan is the enforce loop's bounded-subtask invocation."""
    import re

    import kriya

    root = Path(kriya.__file__).parent
    passing = sorted(str(path.relative_to(root)) for path in root.rglob("*.py")
                     if re.search(r"structured_plan=(?!structured_plan\b|None\b|ctx\.|deps\.|self\.)\w", path.read_text(encoding="utf-8")))
    assert passing == ["workflow/enforce_loop.py"], passing


def test_the_typed_stop_is_one_category_and_relabels_nothing_else():
    from kriya.workflow.outcome import terminal_failure_category
    from kriya.workflow.state import GenerationState

    for reason in vs.REVIEW_REASON_CODES:
        state = GenerationState()
        state.environment_failure = vs.needs_review_message(vs.SubjectDecision(vs.Action.NEEDS_REVIEW, TEST, reason, "why"))
        assert terminal_failure_category(state) == "verifier_semantic_authority_required"
    other = GenerationState()
    other.environment_failure = "TEST FAILURE: VERIFIER_SEMANTIC_AUTHORITY_REQUIRED appears in the middle of a test's own output"
    assert terminal_failure_category(other) == "environment_failure"
    assert terminal_failure_category(GenerationState()) == "quality_gates_exhausted"


# ------------------------------------------------------------------ policy-level controls of the unit state (each kills a named mutant)

def _unit(files: Dict[str, bytes], answer: Optional[str] = None, **over: Any) -> Any:
    def review(subject: VerifierSemanticSubject) -> Any:
        return VerifierSemanticDecision.decided_as(answer, subject.fingerprint)

    callback = TypedApprovalCallback(lambda files, reason: True, lambda subject: None, None, review) if answer else None
    return vs.UnitSemantics(ledger=vs.VerifierSemanticLedger(), run_id="r", plan_hash="p", unit_id="s1", goal=GOAL, regenerable=True,
                            attended=bool(answer), callback=callback, read_bytes=files.get, **over)


def _fail(unit: Any, located: List[str], owners: Any = lambda path: ["s1"]) -> Any:
    return asyncio.run(unit.on_gate_failure(written=[LIB, TEST], failure_type="targeted_test", outcome="x", located_paths=located,
                                            is_workspace_artifact=lambda path: True, owners_of=owners))


@pytest.mark.parametrize("after, executed, expected", [
    (_test(20, "V1"), True, None),                                        # new bytes, executed: judged like any subject
    (_test(10), True, vs.REASON_REGENERATION_NOT_EXECUTED),               # the same invalid bytes came back
    (_test(20, "V1"), False, vs.REASON_REGENERATION_NOT_EXECUTED),        # new bytes no test gate executed
])
def test_once_the_regeneration_answer_is_given_the_path_is_closed_and_only_a_new_executed_verifier_counts(after, executed, expected):
    old_sha = hashlib.sha256(_test(10).encode()).hexdigest()
    unit = _unit({TEST: after.encode(), LIB: LIB_20.encode()})
    unit.regenerating = (TEST, old_sha)
    if executed:
        unit.observe({"success": True, "test_execution": _report("passed")})
    ruling = asyncio.run(unit.on_gates_passed(written=[LIB, TEST], final=True))
    assert unit.denied == {TEST} and unit.regenerating is None, "whatever happened, the path may not be written again"
    if expected is None:
        assert ruling.stop.reason_code == vs.REASON_UNATTENDED, "the regenerated verifier needs fresh semantic authority"
    else:
        assert ruling.stop.reason_code == expected
    waiting = _unit({TEST: _test(20, "V1").encode()})
    waiting.regenerating = (TEST, old_sha)
    assert asyncio.run(waiting.on_gates_passed(written=[TEST], final=False)).unchanged and waiting.regenerating == (TEST, old_sha), \
        "before the terminal regression a not-yet-executed regeneration is still open (the later gate may execute it)"


def test_unknown_provenance_on_a_failing_gate_is_needs_review_and_nobody_is_asked():
    unit = _unit({TEST: b"def test_size(:\n", LIB: LIB_20.encode()}, "expectation_valid")
    unit.observe({"success": False, "test_execution": _report("failed")})
    ruling = _fail(unit, [LIB])
    assert ruling.stop.reason_code == vs.REASON_SUBJECT_UNREADABLE and ruling.owner is None and unit.denied == set()
    silent = _unit({TEST: b"def test_size(:\n"})
    silent.observe({"success": False, "output": "no structured report: nothing executed that can be mapped"})
    assert _fail(silent, [LIB]).unchanged, "with no executed report there is no provenance question: historical behaviour"


def test_a_locator_inside_the_verifier_itself_is_never_a_production_locator():
    unit = _unit({TEST: _test(10).encode(), LIB: LIB_20.encode()}, "expectation_valid")
    unit.observe({"success": False, "test_execution": _report("failed")})
    ruling = _fail(unit, [TEST])
    assert ruling.stop.reason_code == vs.REASON_NO_PRODUCTION_OWNER and "PRODUCTION_OWNER_LOCATOR_VERIFIER_ONLY" in ruling.stop.detail
    selected = _unit({TEST: _test(10).encode(), LIB: LIB_20.encode()}, "expectation_valid")
    selected.observe({"success": False, "test_execution": _report("failed")})
    chosen = _fail(selected, [LIB, TEST])
    assert chosen.owner.selected and chosen.owner.files == (LIB,) and selected.denied == {TEST}


def test_a_selected_owner_without_an_allow_list_scope_is_needs_review(tmp_path):
    """The failure seam itself: the accepted verifier can only be write-denied under an allow-list scope."""
    from kriya.policy.filesystem import WriteScopeMode
    from kriya.workflow import retry_strategy
    from kriya.workflow.failure import Failure, FileLocation
    from kriya.workflow.state import GenerationState

    (tmp_path / LIB).write_text(LIB_20)
    plan = _plan()
    for mode, stopped in ((WriteScopeMode.ALLOWLIST, False), (WriteScopeMode.UNRESTRICTED, True)):
        unit = _unit({TEST: _test(10).encode(), LIB: LIB_20.encode()}, "expectation_valid")
        unit.observe({"success": False, "test_execution": _report("failed")})
        state = GenerationState()
        state.all_files_written = {LIB, TEST} if isinstance(state.all_files_written, set) else [LIB, TEST]
        ctx = SimpleNamespace(verifier_semantic=unit, structured_plan=plan, worktree_path=str(tmp_path), write_scope_mode=mode)
        failure = Failure(type="targeted_test", message="x", raw_output="x", file_locations=[FileLocation(filepath=LIB, line=2)])
        ruling = asyncio.run(retry_strategy._generated_verifier_ruling(state, ctx, failure))
        if stopped:
            assert ruling.stop.reason_code == vs.REASON_NO_PRODUCTION_OWNER and state.environment_failure.startswith("VERIFIER_SEMANTIC_AUTHORITY_REQUIRED:")
        else:
            assert ruling.owner.selected and ruling.owner.owner_id == "s1" and state.environment_failure is None


@pytest.mark.parametrize("budgets, expect_stop", [({}, False), ({"targeted_retry_count": 3}, True)])
def test_a_full_set_attempt_never_follows_a_verifier_that_may_not_be_written(budgets, expect_stop):
    """After expectation_valid (or a used regeneration) the verifier is no later attempt's target, and when the retry policy's next attempt is
    the full file set the unit stops for review instead of regenerating it."""
    from kriya.workflow import retry_strategy
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    state.attempt_number = 1
    state.last_implicated_files = [LIB, TEST]
    for name, value in budgets.items():
        setattr(state.budgets, name, value)
    unit = _unit({})
    unit.denied.add(TEST)
    ctx = SimpleNamespace(verifier_semantic=unit, max_retries=4, targeted_max_retries=3)
    with patch.object(retry_strategy, "fallback_routing_for_context", return_value=SimpleNamespace(available=False)):
        retry_strategy._close_unadmitted_verifier_regeneration(state, ctx)
    assert state.last_implicated_files == [LIB], "the write-denied verifier is not a generation target"
    if expect_stop:
        assert state.environment_failure.startswith(f"VERIFIER_SEMANTIC_AUTHORITY_REQUIRED: {vs.REASON_FULL_REGENERATION_REFUSED}:")
    else:
        assert state.environment_failure is None


def test_every_unit_of_a_run_shares_the_one_ledger_and_a_direct_run_has_its_own(tmp_path):
    run = vs.RunSemantics(ledger=vs.VerifierSemanticLedger(), plan_hash="p", verification_contract_digest="c", goal=GOAL)
    make = lambda controller: vs.unit_semantics(controller, run_id="r", unit_id="s1", goal="unit text", structured_plan=_plan(),  # noqa: E731 - local shorthand
                                                allowlist_scope=True, attended=False, callback=None, read_bytes={}.get)
    first, second, direct = make(run), make(run), make(None)
    assert first.ledger is run.ledger and second.ledger is run.ledger and direct.ledger is not run.ledger
    assert (first.goal, first.plan_hash, first.verification_contract_digest, first.regenerable) == (GOAL, "p", "c", True)
    assert first.invariants == (("gi1", "x"),) and not direct.regenerable
    captured: List[Any] = []
    real_unit = WorkflowEngine.run_generation_workflow

    async def unit(engine, *args, **kwargs):
        captured.append(kwargs.get("verifier_semantic"))
        return await real_unit(engine, *args, **kwargs)

    other = {"id": "s2", "description": "write notes", "execution_method": "model", "planned_files": [{"path": "notes.txt", "action": "create"}],
             "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}],
             "provides": ["notes"]}
    plan = _plan([other])
    plan = EngineeringPlan.model_validate({**plan.model_dump(mode="json"), "subtasks": [
        {**subtask.model_dump(mode="json"), "provides": subtask.provides or ["shop"]} for subtask in plan.subtasks]})
    with patch.object(WorkflowEngine, "run_generation_workflow", new=unit):
        run_enforce(tmp_path, plan=plan, goal=GOAL, on_tests=lambda validator: _gate_result(Path(validator.workspace_path)),
                    units={"s2": ("notes.txt", "notes\n"), "s1": {LIB: LIB_20, TEST: _test(20)}},
                    callback=TypedApprovalCallback(lambda files, reason: True, lambda subject: None, None,
                                                   lambda subject: VerifierSemanticDecision.decided_as("expectation_valid", subject.fingerprint)))
    assert len(captured) >= 2 and all(isinstance(item, vs.RunSemantics) for item in captured)
    assert len({id(item.ledger) for item in captured}) == 1, "the controller created one ledger and every invocation received it"


def test_a_ruled_failure_is_never_handed_to_failure_attribution(tmp_path, monkeypatch):
    """The ruling REPLACES attribution: once the policy owner ruled on a failing generated verifier (regenerate it; stop), no locator, judge or
    triage tier is asked whose file the failure is. With no subject, attribution runs as it always did (the negative control)."""
    from kriya.workflow import retry_strategy

    asked: List[str] = []
    real = retry_strategy.attribute_failure

    async def spy(failure, *args, **kwargs):
        asked.append(failure.type)
        return await real(failure, *args, **kwargs)

    monkeypatch.setattr(retry_strategy, "attribute_failure", spy)
    scene = Scene(lib=[LIB_20], test=[_test(10), _test(20, "V1")], semantic=["expectation_invalid", "expectation_valid"])
    scene.run(tmp_path / "ruled")
    assert scene.applied() == {LIB: LIB_20, TEST: _test(20, "V1")} and asked == [], asked

    def unreported(tree: Path, _call: int) -> Dict[str, Any]:
        result = dict(_gate_result(tree))
        result.pop("test_execution", None)
        return result

    Scene(lib=[LIB_20], test=[_test(10)], gate=unreported).run(tmp_path / "unruled", mode="guardrails")
    assert asked and set(asked) <= {"targeted_test", "test"}, asked
