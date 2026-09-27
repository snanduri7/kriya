"""PRD-031: the recovery and verification coordinators, unit-tested without a
WorkflowEngine or a real attempt.

Existing attempt/retry/gate characterization stays in test_workflow.py,
test_prv17_preflight.py, test_deterministic_failure_diagnostic.py,
test_prd017_fallback_transition.py, test_best_of_n.py and
test_prd026_retry_progress.py (unchanged); they reach both coordinators
through the unchanged entry points (retry_strategy.handle_attempt_failure,
attempt._run_verification_only_attempt).
"""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_workflow import _make_two_participant_contract

import kriya.workflow.recovery_coordinator as rc
from kriya.core import token_budget as tb
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult
from kriya.tools.containment import ContainmentSetupError
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.retry_policy import RetryAction, RetryDecision
from kriya.workflow.state import GenerationState
from kriya.workflow.verification_coordinator import VerificationCoordinator, VerificationRequest

REPO = Path(__file__).resolve().parent.parent


def _no_grounding(exc, ctx):
    del exc, ctx


def _classify(exc, *, mode=None, grounding=_no_grounding):
    return rc.classify_attempt_exception(exc, object(), last_attempt_mode=mode, ground_scope_denial=grounding)


def _scope_denial(reason_code="FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE"):
    return PolicyDeniedError(
        request=ActionRequest(action_type=ActionType.WRITE_FILE, target="elsewhere.py"),
        result=PolicyResult(decision=PolicyDecision.DENY, reason_code=reason_code,
                            explanation="outside the subtask scope"),
    )


def _budget_refusal():
    return tb.ContextBudgetUnsatisfiableError(tb.plan_dispatch(
        messages=[{"role": "user", "content": "x"}], requested_max_tokens=1, context_window=None,
        window_source="unknown"))


# --- classify -----------------------------------------------------------------------

def test_an_attached_failure_is_used_as_is_and_grounding_is_not_consulted():
    attached = Failure(type="compile", message="boom", raw_output="boom")

    def grounding_must_not_run(exc, ctx):
        raise AssertionError("grounding consulted for an exception carrying its own Failure")

    classified = _classify(QualityGateFailure(attached), grounding=grounding_must_not_run)
    assert classified.failure is attached
    assert not (classified.unrecoverable_scope_denial or classified.internal_framework_bug
                or classified.containment_setup_failure)


@pytest.mark.parametrize(("mode", "logged"), [(None, "full-set"), ("full_set", "full-set"),
                                                ("targeted", "targeted"), ("missing_files", "missing_files")])
def test_the_attempt_mode_is_reported_in_its_log_wording(mode, logged):
    assert _classify(RuntimeError("x"), mode=mode).attempt_mode == logged


def test_an_ungrounded_scope_denial_is_unrecoverable():
    classified = _classify(_scope_denial())
    assert classified.unrecoverable_scope_denial
    assert classified.failure.type == "general_error"


def test_another_policy_denial_is_not_an_unrecoverable_scope_denial():
    assert not _classify(_scope_denial("SENSITIVE_PATH_REQUIRES_APPROVAL")).unrecoverable_scope_denial


def test_a_grounded_scope_denial_becomes_its_plan_scope_conflict():
    grounded = Failure(type="plan_scope_conflict", message="owner", raw_output="owner")
    classified = _classify(_scope_denial(), grounding=lambda exc, ctx: grounded)
    assert classified.failure is grounded and not classified.unrecoverable_scope_denial


@pytest.mark.parametrize("exc", [UnboundLocalError("x"), TypeError("x"), KeyError("x"), AssertionError("x")])
def test_a_control_plane_exception_is_an_internal_framework_error(exc):
    classified = _classify(exc)
    assert classified.internal_framework_bug and classified.failure.type == "internal_framework_error"
    assert classified.failure.message.startswith("INTERNAL KRIYA ERROR (not a generated-application defect): ")


def test_a_value_error_stays_an_ordinary_retryable_failure():
    """DeveloperAgent signals a malformed model response with ValueError."""
    classified = _classify(ValueError("malformed JSON"))
    assert not classified.internal_framework_bug and classified.failure.type == "general_error"
    assert classified.failure.message == "malformed JSON"


def test_a_containment_refusal_keeps_its_reason_code():
    exc = ContainmentSetupError("no backend")
    exc.reason_code = "TOOLCHAIN_REQUIREMENT_CONFLICT"
    classified = _classify(exc)
    assert classified.containment_setup_failure
    assert classified.failure.type == "containment_setup_failed"
    assert classified.failure.message == "CONTAINMENT_SETUP_FAILED: no backend"
    assert classified.failure.diagnostics == {"reason_code": "TOOLCHAIN_REQUIREMENT_CONFLICT"}


def test_a_budget_refusal_is_typed_with_its_reason_code():
    failure = _classify(_budget_refusal()).failure
    assert failure.type == "context_budget_unsatisfiable"
    assert failure.diagnostics["reason_code"] == tb.CONTEXT_BUDGET_UNSATISFIABLE and "budget" in failure.diagnostics


# --- decide -------------------------------------------------------------------------

def _ctx(tmp_path, *, sandboxed=True):
    workspace = tmp_path / "workspace"
    worktree = tmp_path / "worktree" if sandboxed else workspace
    for root in {workspace, worktree}:
        root.mkdir(exist_ok=True)
    (worktree / "app.py").write_text("candidate\n")
    return SimpleNamespace(worktree_path=str(worktree), workspace_path=str(workspace),
                           max_retries=3, targeted_max_retries=2, chain=[])


@pytest.fixture(name="removed")
def _removed(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "remove_git_worktree", lambda workspace, worktree: calls.append((workspace, worktree)))
    return calls


def _decision(monkeypatch, action):
    monkeypatch.setattr(rc, "decide_for_state", lambda state, **kwargs: RetryDecision(action, "test"))


def _state():
    state = GenerationState()
    state.all_files_written = {"app.py"}
    return state


def test_a_plan_scope_conflict_stops_before_the_retry_policy(tmp_path, monkeypatch, removed):
    def policy_must_not_run(state, **kwargs):
        raise AssertionError("the retry policy ran after a plan-scope conflict")

    monkeypatch.setattr(rc, "decide_for_state", policy_must_not_run)
    state = _state()
    state.plan_scope_conflict = {"required_files": ["owner.py"]}
    ctx = _ctx(tmp_path)
    decision = rc.conclude_attempt_failure(state, ctx)
    assert decision == rc.RecoveryDecision(stop_loop=True, action=None, budgets_exhausted=False)
    assert state.final_attempt_contents == {"app.py": "candidate\n"}
    assert removed == [(ctx.workspace_path, ctx.worktree_path)]


def test_no_progress_stops_and_abandons_an_active_repair_contract(tmp_path, removed):
    state = _state()
    state.no_progress_terminated = True
    state.repair_contract = _make_two_participant_contract("app.py", "test_app.py", created_attempt=1)
    assert state.repair_contract.status == RepairContractStatus.ACTIVE
    decision = rc.conclude_attempt_failure(state, _ctx(tmp_path))
    assert decision.stop_loop and decision.action is None
    assert state.repair_contract.status == RepairContractStatus.ABANDONED
    assert any(e.kind == "repair_contract_abandoned" and e.details["reason"] == "plan_scope_conflict_or_no_progress"
               for e in state.run_events)
    assert removed


def test_a_continuing_retry_keeps_the_sandbox(tmp_path, monkeypatch, removed):
    _decision(monkeypatch, RetryAction.TARGETED)
    state = _state()
    decision = rc.conclude_attempt_failure(state, _ctx(tmp_path))
    assert decision == rc.RecoveryDecision(stop_loop=False, action=RetryAction.TARGETED, budgets_exhausted=False)
    assert removed == [] and state.final_attempt_contents == {}


def test_an_environment_stop_breaks_the_loop_and_cleans_up(tmp_path, monkeypatch, removed):
    _decision(monkeypatch, RetryAction.STOP_ENVIRONMENT)
    state = _state()
    state.environment_failure = "JAVA_HOME missing"
    decision = rc.conclude_attempt_failure(state, _ctx(tmp_path))
    assert decision == rc.RecoveryDecision(stop_loop=True, action=RetryAction.STOP_ENVIRONMENT, budgets_exhausted=True)
    assert removed and state.final_attempt_contents == {"app.py": "candidate\n"}


def test_exhausted_budgets_clean_up_but_leave_the_loop_condition_to_stop(tmp_path, monkeypatch, removed):
    _decision(monkeypatch, RetryAction.STOP_EXHAUSTED)
    decision = rc.conclude_attempt_failure(_state(), _ctx(tmp_path))
    assert decision == rc.RecoveryDecision(stop_loop=False, action=RetryAction.STOP_EXHAUSTED, budgets_exhausted=True)
    assert removed


def test_an_in_place_run_has_no_sandbox_to_remove(tmp_path, monkeypatch, removed):
    _decision(monkeypatch, RetryAction.STOP_EXHAUSTED)
    state = _state()
    rc.conclude_attempt_failure(state, _ctx(tmp_path, sandboxed=False))
    assert removed == [] and state.final_attempt_contents == {}


def test_the_coordinator_classifies_then_records_then_decides(tmp_path, removed):
    """The recording step sees the classified failure, and the decision
    reads what it recorded."""
    seen = []

    async def record(state, ctx, exc, classified):
        seen.append((exc, classified.failure.type))
        state.no_progress_terminated = True

    exc = TypeError("bug")
    decision = asyncio.run(rc.RecoveryCoordinator(ground_scope_denial=_no_grounding, record_failure=record)
                           .handle(_state(), _ctx(tmp_path), exc))
    assert seen == [(exc, "internal_framework_error")]
    assert decision.stop_loop and removed


# --- verification -------------------------------------------------------------------

class FakeValidator:
    """Records each gate run and returns the scripted results in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run_compile_check(self, known_files):
        self.calls.append(("compile", tuple(known_files)))
        return self.results.pop(0)

    def run_tests(self):
        self.calls.append(("test",))
        return self.results.pop(0)


OK = {"success": True, "output": "ok"}
FAIL = {"success": False, "output": "broken"}


def _tool(name):
    return {"type": "tool", "tool_name": name, "description": name}


RUNTIME = {"type": "judgment", "verifier_kind": "application_runtime", "requires_runtime_execution": True}


def _verify(validator, required, runtime=None):
    outcomes = []
    order = []

    async def runtime_verification():
        order.append("runtime")
        if runtime:
            raise runtime

    coordinator = VerificationCoordinator(validator, record_gate_outcome=outcomes.append,
                                          run_runtime_verification=runtime_verification)
    result = asyncio.run(coordinator.verify(VerificationRequest(
        required_verification=required, known_files=["b.py", "a.py"], attempt_number=4)))
    return result, outcomes, order


def test_declared_verifiers_run_in_order_and_record_their_outcomes():
    validator = FakeValidator(OK, OK)
    result, outcomes, order = _verify(validator, [_tool("compile"), _tool("test")])
    assert result.gates == ("compile", "test") and not result.runtime_verified and order == []
    assert validator.calls == [("compile", ("b.py", "a.py")), ("test",)]
    assert [(o["type"], o["success"], o["attempt"]) for o in outcomes] == [("compile", True, 4), ("test", True, 4)]


def test_quality_gates_stops_at_a_failing_compile_without_running_tests():
    validator = FakeValidator(FAIL)
    with pytest.raises(QualityGateFailure) as failure:
        _verify(validator, [_tool("quality_gates"), RUNTIME])
    assert failure.value.failure.type == "compile"
    assert failure.value.failure.message.startswith("COMPILATION FAILURE (verification-only subtask):")
    assert validator.calls == [("compile", ("b.py", "a.py"))]


def test_a_failing_test_raises_its_typed_failure_after_recording_it():
    outcomes_seen = []
    validator = FakeValidator(FAIL)
    coordinator = VerificationCoordinator(
        validator, record_gate_outcome=outcomes_seen.append,
        run_runtime_verification=lambda: (_ for _ in ()).throw(AssertionError("runtime after a failed gate")))
    with pytest.raises(QualityGateFailure) as failure:
        asyncio.run(coordinator.verify(VerificationRequest(
            required_verification=[_tool("test"), RUNTIME], known_files=[], attempt_number=1)))
    assert failure.value.failure.type == "test"
    assert [o["type"] for o in outcomes_seen] == ["test", "test"]
    assert outcomes_seen[0]["success"] is False


def test_runtime_verifiers_run_after_every_deterministic_verifier_passed():
    result, _outcomes, order = _verify(FakeValidator(OK), [RUNTIME, _tool("test")])
    assert result.gates == ("test",) and result.runtime_verified and order == ["runtime"]


def test_a_runtime_verification_failure_propagates_unchanged():
    runtime_failure = QualityGateFailure(Failure(type="run_verification", message="exit 1", raw_output="exit 1"))
    with pytest.raises(QualityGateFailure) as failure:
        _verify(FakeValidator(), [RUNTIME], runtime=runtime_failure)
    assert failure.value is runtime_failure


def test_an_outcome_is_kept_when_a_later_verifier_raises():
    class Exploding(FakeValidator):
        def run_tests(self):
            raise RuntimeError("runner crashed")

    outcomes = []
    coordinator = VerificationCoordinator(Exploding(OK), record_gate_outcome=outcomes.append,
                                          run_runtime_verification=None)
    with pytest.raises(RuntimeError):
        asyncio.run(coordinator.verify(VerificationRequest(
            required_verification=[_tool("compile"), _tool("test")], known_files=[], attempt_number=1)))
    assert [o["type"] for o in outcomes] == ["compile"]


def test_judgment_only_verification_runs_nothing():
    validator = FakeValidator()
    result, outcomes, order = _verify(validator, [{"type": "judgment", "description": "reads well"}])
    assert result.gates == () and validator.calls == [] and outcomes == [] and order == []


# --- architecture ---------------------------------------------------------------------

def _imported_modules(module_path):
    names = set()
    for node in ast.walk(ast.parse((REPO / module_path).read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


FORBIDDEN = {"kriya.workflow.attempt", "kriya.workflow.retry_strategy", "kriya.workflow.workflow",
             "kriya.workflow.workflow_controller"}


@pytest.mark.parametrize("module", ["kriya/workflow/recovery_coordinator.py",
                                    "kriya/workflow/verification_coordinator.py"])
def test_the_coordinators_depend_only_downward(module):
    """Neither coordinator imports the orchestration modules that call it
    (including lazy imports), so the dependency runs one way."""
    assert not _imported_modules(module) & FORBIDDEN


def test_the_callers_reach_the_coordinators():
    assert "kriya.workflow.recovery_coordinator" in _imported_modules("kriya/workflow/retry_strategy.py")
    assert "kriya.workflow.verification_coordinator" in _imported_modules("kriya/workflow/attempt.py")
    verification = (REPO / "kriya/workflow/verification_coordinator.py").read_text()
    recovery = (REPO / "kriya/workflow/recovery_coordinator.py").read_text()
    # The coordinators decide nothing themselves: recovery defers to the
    # retry policy, and verification owns no write path.
    assert "decide_for_state(" in recovery
    for write in ("AuthorizedFileWriter", "commit_revision_grounded", "open(", "write_text"):
        assert write not in verification, write
