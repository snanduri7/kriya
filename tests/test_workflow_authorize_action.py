"""MA4.13: WorkflowEngine._authorize_action - the general-purpose policy
consultation helper, and its one real (audit-only, enforce=False) wiring
into the Stage 2A knowledge-gap seam. Direct unit coverage of every
enforce/decision/callback branch, plus one integration test proving the
real wiring never affects the actual Stage 2A approval decision."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.config import AppConfig
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.workflow.workflow import WorkflowEngine


def _make_engine():
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    return WorkflowEngine(kernel, llm)


def _result(decision, reason_code="X"):
    return PolicyResult(decision=decision, reason_code=reason_code, explanation="test explanation")


def _request():
    return ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target="some-package")


@pytest.mark.asyncio
async def test_default_enforce_false_never_raises_regardless_of_decision():
    engine = _make_engine()
    for decision in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_SANDBOXED, PolicyDecision.REQUIRE_APPROVAL, PolicyDecision.DENY):
        engine.execution_policy.evaluate = MagicMock(return_value=_result(decision))
        result = await engine._authorize_action(_request())
        assert result.decision == decision


@pytest.mark.asyncio
async def test_enforce_false_never_invokes_approval_callback():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.REQUIRE_APPROVAL))
    callback = MagicMock(return_value=True)
    await engine._authorize_action(_request(), approval_callback=callback)
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_true_deny_raises_without_invoking_callback():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.DENY, "SOME_DENY_REASON"))
    callback = MagicMock(return_value=True)
    with pytest.raises(PolicyDeniedError) as exc_info:
        await engine._authorize_action(_request(), approval_callback=callback, enforce=True)
    callback.assert_not_called()
    assert exc_info.value.result.reason_code == "SOME_DENY_REASON"


@pytest.mark.asyncio
async def test_enforce_true_allow_and_allow_sandboxed_never_raise_or_call_back():
    engine = _make_engine()
    callback = MagicMock(return_value=False)  # would deny if ever (wrongly) consulted
    for decision in (PolicyDecision.ALLOW, PolicyDecision.ALLOW_SANDBOXED):
        engine.execution_policy.evaluate = MagicMock(return_value=_result(decision))
        result = await engine._authorize_action(_request(), approval_callback=callback, enforce=True)
        assert result.decision == decision
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_enforce_true_require_approval_with_no_callback_raises():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.REQUIRE_APPROVAL))
    with pytest.raises(PolicyDeniedError):
        await engine._authorize_action(_request(), approval_callback=None, enforce=True)


@pytest.mark.asyncio
async def test_enforce_true_require_approval_sync_callback_true_returns_result():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.REQUIRE_APPROVAL))
    callback = MagicMock(return_value=True)
    result = await engine._authorize_action(_request(), approval_callback=callback, enforce=True)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    callback.assert_called_once()


@pytest.mark.asyncio
async def test_enforce_true_require_approval_sync_callback_false_raises():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.REQUIRE_APPROVAL))
    callback = MagicMock(return_value=False)
    with pytest.raises(PolicyDeniedError):
        await engine._authorize_action(_request(), approval_callback=callback, enforce=True)


@pytest.mark.asyncio
async def test_enforce_true_require_approval_async_callback_is_awaited():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(return_value=_result(PolicyDecision.REQUIRE_APPROVAL))
    callback = AsyncMock(return_value=True)
    result = await engine._authorize_action(_request(), approval_callback=callback, enforce=True)
    assert result.decision == PolicyDecision.REQUIRE_APPROVAL
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_is_invoked_with_the_real_diffs_shape_and_explanation():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(
        return_value=_result(PolicyDecision.REQUIRE_APPROVAL, "SOME_REASON")
    )
    callback = MagicMock(return_value=True)
    diffs = [{"filepath": "a.py", "diff": "..."}]
    await engine._authorize_action(_request(), diffs_to_show=diffs, approval_callback=callback, enforce=True)
    callback.assert_called_once_with(diffs, "test explanation")


@pytest.mark.asyncio
async def test_broken_policy_engine_never_blocks_regardless_of_enforce():
    engine = _make_engine()
    engine.execution_policy.evaluate = MagicMock(side_effect=RuntimeError("policy engine broke"))
    for enforce in (False, True):
        result = await engine._authorize_action(_request(), enforce=enforce)  # must not raise
        assert result is None


# --- Real wiring: Stage 2A knowledge-gap seam (audit-only) ---

@pytest.mark.asyncio
async def test_stage_2a_wiring_never_affects_the_real_gap_approval_decision(tmp_path):
    """A non-empty new_gaps list must still go through the existing
    approval_callback exactly as before MA4.13 - the new _authorize_action
    call must never raise/block that path (enforce stays False)."""
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    engine = WorkflowEngine(kernel, llm)

    initial_report = GapReport()  # Stage 0: no gaps, run proceeds
    post_report = GapReport()
    post_report.add_gap("newlib", "2.0.0", None, "medium", "introduced by the architect design")

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Use newlib for the new feature",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",
    ])

    real_evaluate = engine.execution_policy.evaluate
    captured = []

    def spy(request):
        captured.append(request)
        return real_evaluate(request)

    engine.execution_policy.evaluate = spy

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", side_effect=[initial_report, post_report]):
        result = await engine.run_generation_workflow(goal="Build a feature", workspace_path=str(tmp_path))

    # Never raised, never blocked - the run completed past Stage 2A.
    assert result is not None
    install_package_requests = [r for r in captured if r.action_type == ActionType.INSTALL_PACKAGE]
    assert len(install_package_requests) == 1
    assert install_package_requests[0].target == "newlib"


# --- POL-001: enforce=True Stage 2A wiring - DENY hard-stops, REQUIRE_APPROVAL
# defers to the existing single aggregate approval_callback prompt instead of
# also prompting once per new_gaps entry (N+1 prompts) or raising before that
# prompt is ever reached. ---

@pytest.mark.asyncio
async def test_stage_2a_enforce_true_deny_raises_before_the_aggregate_prompt(tmp_path):
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.execution_policy.enabled = True
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    engine = WorkflowEngine(kernel, llm)
    kernel.config.execution_policy.mode = "enforce"  # bypasses the config-load validator directly

    initial_report = GapReport()
    post_report = GapReport()
    post_report.add_gap("badlib", "1.0.0", None, "high", "introduced by the architect design")

    engine.execution_policy.evaluate = MagicMock(
        return_value=_result(PolicyDecision.DENY, "PACKAGE_SOURCE_UNTRUSTED")
    )
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Use badlib for the new feature",
    ])
    approval_callback = MagicMock(return_value=True)

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", side_effect=[initial_report, post_report]):
        with pytest.raises(PolicyDeniedError) as exc_info:
            await engine.run_generation_workflow(
                goal="Build a feature", workspace_path=str(tmp_path), approval_callback=approval_callback,
            )
    assert exc_info.value.result.decision == PolicyDecision.DENY
    approval_callback.assert_not_called()


@pytest.mark.asyncio
async def test_stage_2a_enforce_true_require_approval_asks_once_not_per_library(tmp_path):
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.execution_policy.enabled = True
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    engine = WorkflowEngine(kernel, llm)
    kernel.config.execution_policy.mode = "enforce"

    initial_report = GapReport()
    post_report = GapReport()
    post_report.add_gap("libone", "1.0.0", None, "medium", "reason one")
    post_report.add_gap("libtwo", "2.0.0", None, "medium", "reason two")

    def evaluate_side_effect(request):
        if request.action_type == ActionType.INSTALL_PACKAGE:
            return _result(PolicyDecision.REQUIRE_APPROVAL, "PACKAGE_REQUIRES_APPROVAL")
        return _result(PolicyDecision.ALLOW)

    engine.execution_policy.evaluate = MagicMock(side_effect=evaluate_side_effect)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Use libone and libtwo for the new feature",
        '[{"filepath": "app.py", "content": "print(1)"}]',
        "Review: Approved",  # pre-approval reviewer pass
        "Review: Approved",  # final reviewer pass
    ])
    approval_callback = MagicMock(return_value=True)

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", side_effect=[initial_report, post_report]):
        result = await engine.run_generation_workflow(
            goal="Build a feature", workspace_path=str(tmp_path), approval_callback=approval_callback,
        )

    assert result is not None
    # Exactly the pre-existing single aggregate prompt - not one per library,
    # and not raised-away before ever reaching it.
    stage_2a_calls = [
        c for c in approval_callback.call_args_list
        if "proceed with these dependencies" in c.args[1]
    ]
    assert len(stage_2a_calls) == 1


@pytest.mark.asyncio
async def test_stage_2a_enforce_true_require_approval_rejected_aborts_via_existing_path(tmp_path):
    from kriya.tools.knowledge import GapReport

    cfg = AppConfig()
    cfg.execution_policy.enabled = True
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    engine = WorkflowEngine(kernel, llm)
    kernel.config.execution_policy.mode = "enforce"

    initial_report = GapReport()
    post_report = GapReport()
    post_report.add_gap("libone", "1.0.0", None, "medium", "reason one")

    def evaluate_side_effect(request):
        if request.action_type == ActionType.INSTALL_PACKAGE:
            return _result(PolicyDecision.REQUIRE_APPROVAL, "PACKAGE_REQUIRES_APPROVAL")
        return _result(PolicyDecision.ALLOW)

    engine.execution_policy.evaluate = MagicMock(side_effect=evaluate_side_effect)

    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Use libone for the new feature",
    ])
    approval_callback = MagicMock(return_value=False)  # human declines the aggregate prompt

    with patch("kriya.tools.knowledge.KnowledgeGuard.check_goal", side_effect=[initial_report, post_report]):
        with pytest.raises(ValueError, match="Workflow aborted"):
            await engine.run_generation_workflow(
                goal="Build a feature", workspace_path=str(tmp_path), approval_callback=approval_callback,
            )
