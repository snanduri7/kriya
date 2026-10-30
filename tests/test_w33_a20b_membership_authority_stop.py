"""W3-3 (D-001): the repository layer's ``BoundTreeError`` never leaves a production entrypoint as a raw traceback.

MEASURED at abe4650 (tests/test_d001_workspace_authority.py, before the root correction): a run whose membership authority could not decide escaped
``run_generation_workflow`` as a raw ``BoundTreeError`` (the A1 re-raises kept it from becoming a retried candidate failure, and nothing above them handled it); the
registry row had assumed a typed ``AUTHORITY_EXECUTION_UNAVAILABLE`` stop. The contract (owner decision): ``BoundTreeError`` stays an internal repository-layer
exception; at the workflow entrypoint it becomes the EXISTING typed ``AUTHORITY_EXECUTION_UNAVAILABLE`` outcome (the existing authority-unavailable result shape and
failure category; no new code), with the repository layer's reason and detail preserved - never ``[]``, absence, a retry, a continued run or a traceback.

The development-stage end-to-end proofs live in tests/test_w33_bound_membership_guards.py (the A1 guards, best-of-N); this file adds the design stage, the result shape and the
structural guards (both stage runners are inside the handler; the entrypoint keeps its decorators).
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from test_s19e_honest_refusal import (  # noqa: E402  # pylint: disable=wrong-import-position
    _isolated_git,  # noqa: F401  # autouse fixture of that module, re-exported so it applies here
)

from kriya.config import AppConfig
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.repository.bound_tree import BOUND_TREE_IDENTITY_MISMATCH, BOUND_TREE_UNAVAILABLE, BoundTreeError
from kriya.workflow.authority_bundle import AUTHORITY_EXECUTION_UNAVAILABLE, membership_authority_unavailable_result
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine

REPO_ROOT = Path(__file__).resolve().parents[1]


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("placeholder\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def test_the_typed_result_carries_the_existing_code_and_the_repository_layers_evidence():
    error = BoundTreeError(BOUND_TREE_IDENTITY_MISMATCH, "the copy does not hold the bound content", ("a.py", "b.py"))
    result = membership_authority_unavailable_result(error)
    assert result["status"] == "failure" and result["quality_gates_passed"] is False and result["files"] == []
    assert result["reason_codes"] == [AUTHORITY_EXECUTION_UNAVAILABLE] and result["failure_category"] == "verification_authority_unavailable"
    assert result["membership_authority"] == {"reason_code": BOUND_TREE_IDENTITY_MISMATCH, "detail": "the copy does not hold the bound content", "paths": ["a.py", "b.py"]}
    assert "AUTHORITY_EXECUTION_UNAVAILABLE" in result["error"] and "the copy does not hold the bound content" in result["error"]
    assert result["error"] == result["environment_failure"]


@pytest.mark.asyncio
async def test_a_design_stage_membership_failure_is_the_typed_outcome_not_a_traceback(tmp_path):
    """The design stage reaches the Architect's owner resolution (the same TASK-route setup as test_workflow's response-owner test); an unavailable membership authority
    there ends the run typed, before any Developer call, with nothing applied."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.engineering_triage.enabled = True
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=AssertionError("no model call may follow a design-stage membership failure"))
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    error = BoundTreeError(BOUND_TREE_UNAVAILABLE, "git ls-files failed: injected at the design stage")
    before = sorted(p.name for p in tmp_path.iterdir() if p.name not in (".git", ".kriya"))
    with patch("kriya.workflow.stage_architecture.resolve_planned_artifact_owners", side_effect=error) as resolver:
        with begin_mutating_run(str(tmp_path)):
            result = await engine.run_generation_workflow(
                goal="Add a new audit_logger.py that changes how audit events are recorded.", workspace_path=str(tmp_path),
                predetermined_plan="Add audit_logger.py", predetermined_design="Add audit_logger.py", predetermined_architect_files=["audit_logger.py"])
    assert resolver.called, "the design-stage resolver was never reached (the run ended somewhere else)"
    assert result["status"] == "failure" and result["quality_gates_passed"] is False and result["files"] == []
    assert result["reason_codes"] == [AUTHORITY_EXECUTION_UNAVAILABLE] and result["failure_category"] == "verification_authority_unavailable"
    assert result["membership_authority"]["reason_code"] == BOUND_TREE_UNAVAILABLE and "injected at the design stage" in result["membership_authority"]["detail"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.name not in (".git", ".kriya")) == before                      # nothing applied


# ---- structural guards ------------------------------------------------------------------------------------------------------------------------

def _workflow_functions():
    tree = ast.parse((REPO_ROOT / "kriya/workflow/workflow.py").read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_entrypoint_keeps_its_run_lease_and_exception_decorators_and_the_helper_has_none():
    """A helper inserted above ``run_generation_workflow`` once took its ``@coordinated_mutation`` / ``@record_run_exceptions`` decorators - silently removing the run
    lease and the exception tracing from the main entrypoint. Pinned."""
    functions = _workflow_functions()
    decorators = lambda node: {ast.unparse(d) for d in node.decorator_list}  # noqa: E731 - a tiny local projection
    assert {"coordinated_mutation", "record_run_exceptions"} <= decorators(functions["run_generation_workflow"])
    assert decorators(functions["_membership_authority_stop"]) == set()


@pytest.mark.parametrize("stage_call", ["run_design_stages", "run_development"])
def test_both_stage_runners_are_inside_the_membership_authority_handler(stage_call):
    entrypoint = _workflow_functions()["run_generation_workflow"]
    handled = []
    for node in ast.walk(entrypoint):
        if isinstance(node, ast.Try) and any(isinstance(sub, ast.Call) and getattr(sub.func, "id", None) == stage_call for stmt in node.body for sub in ast.walk(stmt)):
            handled.append([ast.unparse(h.type) for h in node.handlers])
    assert handled == [["BoundTreeError"]], f"{stage_call} must sit in exactly one try whose only handler is BoundTreeError: {handled}"


def test_the_handler_returns_the_typed_result_and_never_swallows_the_error():
    handler = _workflow_functions()["_membership_authority_stop"]
    returns = [ast.unparse(node.value) for node in ast.walk(handler) if isinstance(node, ast.Return)]
    assert len(returns) == 1 and returns[0].startswith("{**result,")
    assert not any(isinstance(node, ast.ExceptHandler) for node in ast.walk(handler))
    assert "persist_early_exit_trace" in ast.unparse(handler)
