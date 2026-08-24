"""MA4.15: WorkflowEngine's own config wiring - AutonomyConfig.sensitive_paths
threaded into ExecutionPolicy's constructor, and execution_policy.enabled
actually gating both of WorkflowEngine's real MA4 call sites
(_audit_approval_rules, Stage 2A's _authorize_action caller)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.config import AppConfig
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine


def _make_engine(cfg=None):
    cfg = cfg or AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    return WorkflowEngine(kernel, llm)


def _control():
    route = EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.HEAVY,
    )
    return WorkflowControlContext.for_route(route)


def test_engine_threads_autonomy_sensitive_paths_into_execution_policy():
    cfg = AppConfig()
    cfg.autonomy.sensitive_paths = [r"totally-custom-pattern"]
    engine = _make_engine(cfg)

    # A path only the DEFAULT hardcoded list would have caught (e.g. .ssh)
    # must NOT be denied - proving the real AutonomyConfig list replaced it.
    result = engine.execution_policy.evaluate(ActionRequest(
        action_type=ActionType.READ_FILE, target="/home/user/.ssh/id_rsa",
    ))
    assert not (result.decision == PolicyDecision.DENY and result.reason_code == "SENSITIVE_PATH_DENIED")

    # The custom pattern itself does fire.
    result = engine.execution_policy.evaluate(ActionRequest(
        action_type=ActionType.READ_FILE, target="/repo/totally-custom-pattern/x",
    ))
    assert result.decision == PolicyDecision.DENY
    assert result.reason_code == "SENSITIVE_PATH_DENIED"


def test_audit_approval_rules_is_a_no_op_when_execution_policy_disabled(tmp_path):
    cfg = AppConfig()
    cfg.execution_policy.enabled = False
    engine = _make_engine(cfg)
    engine.execution_policy.evaluate = MagicMock()
    engine._audit_approval_rules(["app.py"], str(tmp_path), _control())
    engine.execution_policy.evaluate.assert_not_called()


def test_audit_approval_rules_still_fires_when_enabled_true_default(tmp_path):
    cfg = AppConfig()
    engine = _make_engine(cfg)
    engine.execution_policy.evaluate = MagicMock(wraps=engine.execution_policy.evaluate)
    engine._audit_approval_rules(["app.py"], str(tmp_path), _control())
    engine.execution_policy.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_stage_2a_never_calls_authorize_action_when_execution_policy_disabled(tmp_path):
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.execution_policy.enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    engine = WorkflowEngine(kernel, llm)

    initial_report = GapReport()
    post_report = GapReport()
    post_report.add_gap("newlib", "2.0.0", None, "medium", "introduced by the architect design")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Use newlib for the new feature",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    engine._authorize_action = AsyncMock(wraps=engine._authorize_action)

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", side_effect=[initial_report, post_report]):
        await engine.run_generation_workflow(goal="Build a feature", workspace_path=str(tmp_path))

    engine._authorize_action.assert_not_called()
