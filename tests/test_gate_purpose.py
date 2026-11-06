"""WHY a gate ran (product/demo-72h; registry GATE-RESULT-PURPOSE-LABEL-001): the orchestrator that CALLS a gate declares its purpose, the recorded gate.result carries it, and no
reader has to infer it from the order of records or a label. A gate run outside any declared purpose records none - "not recorded", never a guess."""
import ast
import asyncio
import time
from pathlib import Path

import pytest

from kriya.core.attempt_evidence import scope
from kriya.tools import validate
from kriya.workflow import gate_orchestrator

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def recorded(monkeypatch):
    emitted = []
    monkeypatch.setattr(scope, "capture_mode", lambda: "full")
    monkeypatch.setattr(scope, "emit", lambda kind, payload, content=None, **_k: emitted.append((kind, dict(payload))))
    return emitted


def test_a_declared_purpose_is_recorded_on_the_gate_result_and_clears_afterwards(recorded):
    with scope.gate_purpose("candidate_verification"):
        validate._record_gate_result("tests", {"success": True}, time.monotonic())  # pylint: disable=protected-access
        with scope.gate_purpose("named_test_closure_base_run"):
            validate._record_gate_result("tests", {"success": True}, time.monotonic())  # pylint: disable=protected-access
        validate._record_gate_result("compile", {"success": True}, time.monotonic())  # pylint: disable=protected-access
    validate._record_gate_result("tests", {"success": True}, time.monotonic())  # pylint: disable=protected-access
    assert [p.get("purpose") for _kind, p in recorded] == ["candidate_verification", "named_test_closure_base_run", "candidate_verification", None]
    assert "purpose" not in recorded[-1][1], "no purpose declared: the key is absent (not recorded), never a guessed value"
    assert all(kind == "gate.result" for kind, _p in recorded) and scope.current_gate_purpose() is None


def test_an_unknown_purpose_is_a_programming_error_and_is_never_recorded():
    with pytest.raises(ValueError):
        with scope.gate_purpose("because"):
            pass
    assert scope.current_gate_purpose() is None


def test_the_decorator_declares_the_purpose_for_sync_and_async_orchestrators_and_restores_it():
    seen = []

    @scope.declares_gate_purpose("verification_only_unit")
    def sync():
        seen.append(scope.current_gate_purpose())

    @scope.declares_gate_purpose("candidate_verification")
    async def asynchronous():
        seen.append(scope.current_gate_purpose())
        await asyncio.sleep(0)
        seen.append(scope.current_gate_purpose())

    sync()
    asyncio.run(asynchronous())
    assert seen == ["verification_only_unit", "candidate_verification", "candidate_verification"] and scope.current_gate_purpose() is None
    assert sync.declared_gate_purpose == "verification_only_unit" and asynchronous.declared_gate_purpose == "candidate_verification"


def test_concurrent_orchestrators_do_not_see_each_others_purpose():
    seen = {}

    @scope.declares_gate_purpose("candidate_verification")
    async def one():
        await asyncio.sleep(0.01)
        seen["one"] = scope.current_gate_purpose()

    @scope.declares_gate_purpose("verification_only_unit")
    async def two():
        await asyncio.sleep(0)
        seen["two"] = scope.current_gate_purpose()

    async def both():
        await asyncio.gather(one(), two())

    asyncio.run(both())
    assert seen == {"one": "candidate_verification", "two": "verification_only_unit"}


@pytest.mark.parametrize("name,purpose", [("_run_executing_candidate_gates", "candidate_verification"), ("_run_verification_only_attempt", "verification_only_unit"),
                                          ("_run_deferred_executing_gates", "candidate_verification")])
def test_the_candidate_gate_orchestrators_declare_their_purpose(name, purpose):
    assert getattr(gate_orchestrator, name).declared_gate_purpose == purpose
    assert purpose in scope.GATE_PURPOSES


def test_both_named_test_closure_runs_are_inside_a_declared_purpose():
    tree = ast.parse((ROOT / "kriya" / "workflow" / "named_test_oracle.py").read_text())
    judge = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "judge_named_tests")
    found = {}
    for node in ast.walk(judge):
        if isinstance(node, ast.With) and any(isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "attr", "") == "gate_purpose" for i in node.items):
            purpose = next(i.context_expr.args[0].value for i in node.items if isinstance(i.context_expr, ast.Call) and getattr(i.context_expr.func, "attr", "") == "gate_purpose")
            runs = [c for c in ast.walk(node) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "run_tests"]
            found[purpose] = len(runs)
    assert found == {"named_test_closure_base_run": 1, "named_test_closure_candidate_run": 1}
    unguarded = [c for c in ast.walk(judge) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "run_tests"]
    assert len(unguarded) == 2, "every run_tests call of the oracle is one of the two declared runs"
