"""MA4.9's own regression requirement, mirroring MA4.3/4.4/4.5/4.6/4.7/4.8:
ExecutionPolicy's audit-only integration into kriya/workflow/workflow.py's
approval-gate computation (_audit_approval_rules) must never affect the
real need_human_approval decision, under any condition including a
misconfigured or outright broken policy engine.
"""
from unittest.mock import MagicMock

from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.config import AppConfig
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine


def _make_engine():
    cfg = AppConfig()
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


def test_audit_call_is_a_no_op_when_control_is_none(tmp_path):
    engine = _make_engine()
    # Must not raise, must not call evaluate at all.
    engine.execution_policy.evaluate = MagicMock()
    engine._audit_approval_rules(["app.py"], str(tmp_path), None)
    engine.execution_policy.evaluate.assert_not_called()


def test_audit_call_never_raises_even_when_policy_engine_is_broken(tmp_path):
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(side_effect=RuntimeError("policy broke"))
    engine._audit_approval_rules(["app.py", "utils.py"], str(tmp_path), _control())  # must not raise


def test_audit_call_observes_engineering_route_and_process_profile(tmp_path):
    engine = _make_engine()
    captured = []
    real_evaluate = engine.execution_policy.evaluate

    def spy(request):
        captured.append(request)
        return real_evaluate(request)

    engine.execution_policy.evaluate = spy
    control = _control()
    engine._audit_approval_rules(["app.py"], str(tmp_path), control)

    assert len(captured) == 1
    assert captured[0].engineering_route is control.engineering_route
    assert captured[0].process_profile is control.process_profile
    assert captured[0].workspace_path is None  # deliberately omitted - see execution.py's own note


def test_audit_call_issues_one_request_per_file():
    engine = _make_engine()
    captured = []
    real_evaluate = engine.execution_policy.evaluate

    def spy(request):
        captured.append(request)
        return real_evaluate(request)

    engine.execution_policy.evaluate = spy
    engine._audit_approval_rules(["a.py", "b.py", "c.py"], "/repo", _control())
    assert len(captured) == 3


def test_real_stage_seven_actually_fires_for_this_call_shape():
    """Confirms the real, non-mocked policy engine actually reaches stage 7
    (approval-rules) for this call's shape, not stage 2's workspace-
    containment ALLOW - the whole point of omitting workspace_path."""
    engine = _make_engine()
    captured = []
    real_evaluate = engine.execution_policy.evaluate

    def spy(request):
        result = real_evaluate(request)
        captured.append(result)
        return result

    engine.execution_policy.evaluate = spy
    engine._audit_approval_rules(["app.py"], "/repo", _control())  # HEAVY -> human_review_required True
    assert len(captured) == 1
    assert captured[0].reason_code == "PROCESS_PROFILE_REQUIRES_APPROVAL"
