"""MODEL-CANDIDATE-UNAVAILABLE-001 (Stage B run 4, F3): a refusal of the model
candidate made BEFORE any provider request is not a retry attempt.

MEASURED (run 20261118T111000-acc9061c, owner recovery s8 -> s5): attempts 1-2
were refused CONTEXT_BUDGET_UNSATISFIABLE on the primary, attempts 3-5
MODEL_BINDING_NOT_QUALIFIED on the fallback (three identical refusals in 170
ms). Each was charged to a retry family, a triage model call attributed the
first refusal to a source file, and the unit ended "all applicable retry
budgets are exhausted" with no Developer request sent.

Invariant: such a refusal marks that candidate unavailable for the unit
invocation with its typed reason, charges no retry family, asks no model to
attribute it, and the attempt advances ONCE to another eligible candidate or
the unit stops with the typed reason. Three counters stay separate: work grants
(S8: issued per attempt, never given back), provider requests, retry-family
attempts.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import developer_double
from test_prd017_fallback_transition import FALLBACK, PRIMARY, _cfg, _exact_ollama
from test_prv17_preflight import _minimal_ctx

import kriya.workflow.attempt as attempt_module
import kriya.workflow.recovery_coordinator as rc
import kriya.workflow.retry_strategy as retry_strategy_module
from kriya.core import model_admission as admission
from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core import token_budget as tb
from kriya.core.inference_settings import role_inference_settings
from kriya.core.kernel import Kernel
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.attempt import run_attempt
from kriya.workflow.attempt_lifecycle import plan_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.model_transition import MODEL_CANDIDATE_UNAVAILABLE, fallback_routing_for_state
from kriya.workflow.outcome import terminal_failure_category
from kriya.workflow.retry_policy import RetryAction, charge_failed_attempt, decide_for_state, decide_retry_action
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState, RetryBudgets

TARGET = "pkg/mod.py"
MAX_RETRIES, TARGETED_MAX_RETRIES = 4, 3


def _context_refusal(model=PRIMARY):
    """The real refusal, with run 4's arithmetic: 31601 prompt tokens + 1024 minimum output + the 256-token safety
    margin against a 32768 window (78922 ASCII bytes at the default 2.5 bytes/token = 31569, + 32 framing)."""
    with pytest.raises(tb.ContextBudgetUnsatisfiableError) as refused:
        tb.plan_dispatch(
            messages=[{"role": "system", "content": "s" * 22}, {"role": "user", "content": "u" * 78899}],
            requested_max_tokens=16384, context_window=32768, window_source="served_observed")
    refused.value.model = model   # LLMClient._dispatch_budget names the refused candidate
    return refused.value


def _admission_refusal(monkeypatch, cfg, model=FALLBACK):
    """The real refusal of the real admission owner for an unqualified identity."""
    _exact_ollama(monkeypatch)
    production = cfg.model_copy(deep=True)
    production.runtime_profile = "production"
    with pytest.raises(admission.ModelAdmissionError) as refused:
        mq.admit_dispatch(production, role="developer", model=model,
                          fingerprint=model_runtime.resolve_configured_model_runtime(production, model),
                          settings=role_inference_settings(production, "developer", model), admitted=set(),
                          runtime_name="ollama")
    return refused.value


def _unit(tmp_path, *, chain=True, developer=None):
    cfg = _cfg()
    if not chain:
        cfg.llm_chain = []
    ctx = _minimal_ctx(
        tmp_path, developer=developer or developer_double(), kernel=Kernel(config=cfg), chain=list(cfg.llm_chain),
        architect_files=[TARGET], expected_files_upfront=[TARGET], architect_basename_to_path={Path(TARGET).name: TARGET},
        allowed_write_relpaths=[TARGET], write_scope_mode=WriteScopeMode.ALLOWLIST,
        max_retries=MAX_RETRIES, targeted_max_retries=TARGETED_MAX_RETRIES)
    state = GenerationState()
    state.attempt_number, state.all_files_written = 1, set()
    state.last_attempt_mode = "full_set"
    return state, ctx, cfg


def _fail(state, ctx, exc):
    return asyncio.run(handle_attempt_failure(state, ctx, exc))


def _next(state, ctx):
    return decide_for_state(
        state, max_retries=MAX_RETRIES, targeted_max_retries=TARGETED_MAX_RETRIES,
        has_fallback_model=fallback_routing_for_state(state, ctx.kernel.config, ctx.chain, ctx.worktree_path).available)


def _family_charges(state):
    budgets = state.budgets
    return (budgets.retry_count, budgets.targeted_retry_count, budgets.api_contract_recovery_count,
            budgets.fallback_targeted_attempted, budgets.fallback_attempts_used)


# ---------------------------------------------------------------- classification

def test_a_context_refusal_and_an_admission_refusal_are_classified_as_candidate_refusals(monkeypatch, tmp_path):
    _state, ctx, cfg = _unit(tmp_path)
    classify = lambda exc: rc.classify_attempt_exception(  # noqa: E731 - a two-use local
        exc, ctx, last_attempt_mode=None, ground_scope_denial=lambda exc, ctx: None)
    budget = classify(_context_refusal())
    assert (budget.failure.type, budget.candidate_refusal_reason, budget.candidate_refusal_model) == (
        "context_budget_unsatisfiable", tb.CONTEXT_BUDGET_UNSATISFIABLE, PRIMARY)
    refused = classify(_admission_refusal(monkeypatch, cfg))
    assert (refused.failure.type, refused.candidate_refusal_reason, refused.candidate_refusal_model) == (
        "model_binding_not_qualified", admission.MODEL_BINDING_NOT_QUALIFIED, FALLBACK)
    assert refused.failure.diagnostics["inference_settings"]["temperature"] is not None
    ordinary = classify(ValueError("model produced garbage"))
    assert (ordinary.failure.type, ordinary.candidate_refusal_reason, ordinary.candidate_refusal_model) == (
        "general_error", None, None)


# ---------------------------------------------------------------- the recording owner and the next decision

def test_a_refused_primary_is_marked_unavailable_uncharged_and_the_attempt_advances_to_the_fallback(tmp_path):
    state, ctx, _cfg_ = _unit(tmp_path)
    stop = _fail(state, ctx, _context_refusal())
    assert stop is False and state.environment_failure is None
    assert state.unavailable_candidates == {PRIMARY: tb.CONTEXT_BUDGET_UNSATISFIABLE}
    assert state.primary_candidate_unavailable == tb.CONTEXT_BUDGET_UNSATISFIABLE
    assert _family_charges(state) == (0, 0, 0, False, 0)                 # no retry family paid for an unsent request
    assert not ctx.developer.llm.method_calls                            # and no model was asked to attribute it
    assert state.last_implicated_files is None
    decision = _next(state, ctx)
    assert decision.action is RetryAction.FULL_SET and decision.reason.startswith("primary candidate unavailable")
    event = next(e for e in state.run_events if e.kind == "model.candidate_unavailable")
    assert event.details["candidate"] == "primary" and event.details["terminal"] is False
    assert event.details["retry_family_charged"] is False


def test_with_grounded_targets_a_refused_primary_never_gets_the_targeted_retry(tmp_path):
    """Run 4 attempt 2: the targeted retry went back to the primary that had just refused the same request."""
    state, ctx, _cfg_ = _unit(tmp_path)
    state.last_implicated_files = [TARGET]
    _fail(state, ctx, _context_refusal())
    assert state.last_implicated_files == [TARGET]                       # the last real failure's grounding is kept
    decision = _next(state, ctx)
    assert decision.action is RetryAction.FALLBACK_TARGETED
    state.budgets.fallback_targeted_attempted = True
    assert _next(state, ctx).action is RetryAction.FULL_SET
    state.primary_candidate_unavailable = None                           # control: an available primary is offered it
    state.budgets.fallback_targeted_attempted = False
    assert _next(state, ctx).action is RetryAction.TARGETED


def test_when_the_fallback_is_refused_too_the_unit_stops_with_the_typed_reason(monkeypatch, tmp_path):
    state, ctx, cfg = _unit(tmp_path)
    assert _fail(state, ctx, _context_refusal()) is False
    state.attempt_number, state.last_attempt_mode = 2, "full_set"
    state.budgets.fallback_attempts_used = 1                             # what selecting the fallback reserved
    assert _fail(state, ctx, _admission_refusal(monkeypatch, cfg)) is True
    assert state.environment_failure.startswith(f"{MODEL_CANDIDATE_UNAVAILABLE}: no eligible model candidate remains")
    assert f"{PRIMARY}: {tb.CONTEXT_BUDGET_UNSATISFIABLE}" in state.environment_failure
    assert f"{FALLBACK}: {admission.MODEL_BINDING_NOT_QUALIFIED}" in state.environment_failure
    assert state.stop_reason_evidence.code == admission.MODEL_BINDING_NOT_QUALIFIED
    assert state.stop_reason_evidence.message == state.environment_failure
    assert FALLBACK in state.incompatible_fallbacks
    assert _family_charges(state) == (0, 0, 0, False, 0)
    assert _next(state, ctx).action is RetryAction.STOP_ENVIRONMENT
    assert terminal_failure_category(state) == "model_candidate_unavailable"


def test_a_refused_fallback_is_uncharged_and_the_existing_ladder_decides_what_follows(monkeypatch, tmp_path):
    state, ctx, cfg = _unit(tmp_path)
    state.budgets.retry_count, state.attempt_number = 1, 2               # one real, charged primary failure came first
    state.last_attempt_mode = "fallback_targeted"
    state.budgets.fallback_targeted_attempted, state.budgets.fallback_attempts_used = True, 1
    assert _fail(state, ctx, _admission_refusal(monkeypatch, cfg)) is False
    assert state.unavailable_candidates == {FALLBACK: admission.MODEL_BINDING_NOT_QUALIFIED}
    assert state.primary_candidate_unavailable is None and state.environment_failure is None
    # nothing charged, and what the unsent attempt reserved of the fallback's own allowances is given back
    assert _family_charges(state) == (1, 0, 0, False, 0)
    routing = fallback_routing_for_state(state, ctx.kernel.config, ctx.chain, ctx.worktree_path)
    assert routing.selected is None and state.incompatible_fallbacks[FALLBACK] == [
        f"{admission.MODEL_BINDING_NOT_QUALIFIED}: refused before any provider request"]
    # PRD-017's own terminal for a fallback that cannot serve the run: it is never selected or sent a request again
    with pytest.raises(QualityGateFailure) as terminal:
        attempt_module._select_developer_fallback(state, ctx, state.budgets.retry_count, targets=[TARGET],
                                                  allow_primary=True)
    assert terminal.value.failure.type == "fallback_incompatible"
    assert admission.MODEL_BINDING_NOT_QUALIFIED in terminal.value.failure.message


def test_a_second_refusal_of_an_unavailable_candidate_is_terminal(tmp_path):
    """The no-loop guarantee, whatever routed the request back to it."""
    state, ctx, _cfg_ = _unit(tmp_path)
    assert _fail(state, ctx, _context_refusal()) is False
    state.attempt_number = 2
    assert _fail(state, ctx, _context_refusal()) is True
    assert state.environment_failure.startswith(f"{MODEL_CANDIDATE_UNAVAILABLE}:")
    assert state.stop_reason_evidence.code == tb.CONTEXT_BUDGET_UNSATISFIABLE
    assert _family_charges(state) == (0, 0, 0, False, 0)


def test_without_a_fallback_a_refused_primary_stops_at_once(tmp_path):
    """It used to be re-sent until the full-set budget ran out."""
    state, ctx, _cfg_ = _unit(tmp_path, chain=False)
    assert _fail(state, ctx, _context_refusal()) is True
    assert state.environment_failure.startswith(f"{MODEL_CANDIDATE_UNAVAILABLE}:")
    assert tb.CONTEXT_BUDGET_UNSATISFIABLE in state.environment_failure
    assert _family_charges(state) == (0, 0, 0, False, 0)


def test_an_ordinary_model_failure_is_still_charged_and_marks_no_candidate(tmp_path):
    """The control: the rule is about pre-provider refusals of the candidate, not about every exception."""
    state, ctx, _cfg_ = _unit(tmp_path)
    assert _fail(state, ctx, ValueError("model produced garbage")) is False
    assert state.budgets.retry_count == 1 and state.unavailable_candidates == {}
    assert state.primary_candidate_unavailable is None
    assert not [e for e in state.run_events if e.kind == "model.candidate_unavailable"]


@pytest.mark.parametrize("mode", ["full_set", "targeted", "missing_files", "fallback_targeted", "api_contract_recovery"])
@pytest.mark.parametrize("candidate", ["primary", "fallback"])
def test_the_budget_owner_charges_no_family_for_an_unavailable_candidate(mode, candidate):
    budgets = RetryBudgets()
    budgets.fallback_attempts_used = 1
    budgets.fallback_targeted_attempted = mode == "fallback_targeted"
    charge_failed_attempt(budgets, attempt_mode=mode, plan_scope_conflict=False, failure_family_changed=False,
                          candidate_unavailable=candidate)
    assert (budgets.retry_count, budgets.targeted_retry_count, budgets.api_contract_recovery_count) == (0, 0, 0)
    assert budgets.fallback_attempts_used == (0 if candidate == "fallback" else 1)
    assert budgets.fallback_targeted_attempted == (mode == "fallback_targeted" and candidate == "primary")
    control = RetryBudgets()
    charge_failed_attempt(control, attempt_mode=mode, plan_scope_conflict=False, failure_family_changed=False)
    assert (control.retry_count, control.targeted_retry_count, control.api_contract_recovery_count) != (0, 0, 0) \
        or mode == "fallback_targeted"


def _policy(**overrides):
    inputs = dict(retry_count=0, max_retries=MAX_RETRIES, targeted_retry_count=0, targeted_max_retries=TARGETED_MAX_RETRIES,
                  has_implicated_files=False, has_missing_files=False, has_fallback_model=True,
                  fallback_targeted_attempted=False, environment_failure=None, attempt_number=1, max_total_attempts=11,
                  primary_unavailable=True)
    inputs.update(overrides)
    return decide_retry_action(**inputs)


def test_the_policy_itself_never_offers_an_unavailable_primary_a_request():
    """The pure policy, with no recorded stop to lean on (decide_attempt_mode reads it without one)."""
    stopped = _policy(has_fallback_model=False)
    assert stopped.action is RetryAction.STOP_EXHAUSTED and stopped.reason.startswith("no eligible model candidate remains")
    assert _policy(has_fallback_model=False, has_implicated_files=True).action is RetryAction.STOP_EXHAUSTED
    assert _policy(has_fallback_model=False, has_missing_files=True).action is RetryAction.STOP_EXHAUSTED
    assert _policy(has_missing_files=True).action is RetryAction.FULL_SET            # missing-files is primary-only
    assert _policy(has_implicated_files=True).action is RetryAction.FALLBACK_TARGETED
    assert _policy(retry_count=MAX_RETRIES).action is RetryAction.STOP_EXHAUSTED     # the fallback's own budget still binds
    # controls: an available primary keeps every one of its families
    assert _policy(primary_unavailable=False, has_fallback_model=False).action is RetryAction.FULL_SET
    assert _policy(primary_unavailable=False, has_missing_files=True).action is RetryAction.MISSING_FILES
    assert _policy(primary_unavailable=False, has_implicated_files=True).action is RetryAction.TARGETED


# ---------------------------------------------------------------- the run 4 recovery sequence, scripted and model-free

class _Script:
    """The Developer of the reopened owner. A candidate's entry is what a request to it does: an exception instance
    is a refusal raised BEFORE the provider (nothing sent); "answer" reaches the provider and returns an unusable
    answer (a real, charged model failure)."""

    def __init__(self, **by_model):
        self.by_model = {PRIMARY: by_model["primary"], FALLBACK: by_model["fallback"]}
        self.requested, self.provider_requests, self.state = [], [], None

    async def run_generation(self, *_args, **kwargs):
        model = kwargs.get("model_override") or PRIMARY
        attempt = self.state.attempt_number
        if (attempt, model) not in self.requested:
            self.requested.append((attempt, model))
        behaviour = self.by_model[model]
        if isinstance(behaviour, BaseException):
            raise behaviour
        if (attempt, model) not in self.provider_requests:
            self.provider_requests.append((attempt, model))
        raise ValueError("scripted model answer: not a usable file set")


def _drive(monkeypatch, tmp_path, script):
    """development_loop.run_development_loop's own loop head (plan_attempt with the routing view) around the real
    run_attempt and the real handle_attempt_failure; work grants are counted at the one place that issues them."""
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=script.run_generation)
    state, ctx, cfg = _unit(tmp_path, developer=developer)
    state.attempt_number, state.last_attempt_mode = 0, None
    script.state = state
    grants = []
    monkeypatch.setattr(attempt_module, "grant_developer_attempt", lambda required, unit: grants.append(unit))
    # Retry-family charges, read at their one owner (the no-progress transition also moves these counters).
    real_charge, script.charges = retry_strategy_module.charge_failed_attempt, []

    def charge(budgets, **kwargs):
        script.charges.append(kwargs.get("candidate_unavailable"))
        return real_charge(budgets, **kwargs)

    monkeypatch.setattr(retry_strategy_module, "charge_failed_attempt", charge)
    modes = []

    async def loop():
        for _ in range(40):   # far above every budget: the loop must stop by its own decision
            plan = plan_attempt(
                state, max_retries=MAX_RETRIES, targeted_max_retries=TARGETED_MAX_RETRIES,
                has_fallback_model=fallback_routing_for_state(state, cfg, ctx.chain, ctx.worktree_path).available,
                verification_only=False, best_of_n=1, sandboxed=False)
            if not plan.should_continue:
                return plan.decision
            try:
                await run_attempt(state, ctx)
            except Exception as exc:
                modes.append(state.last_attempt_mode)
                if await handle_attempt_failure(state, ctx, exc):
                    return None
        raise AssertionError("the retry loop did not stop")

    final = asyncio.run(loop())
    return state, script, grants, modes, final


def test_run4_sequence_as_measured_stops_after_one_refusal_per_candidate(monkeypatch, tmp_path):
    """primary context-unavailable -> fallback admission refused: 2 attempts, not 5; nothing sent; nothing charged."""
    cfg = _cfg()
    script = _Script(primary=_context_refusal(), fallback=_admission_refusal(monkeypatch, cfg))
    state, script, grants, modes, final = _drive(monkeypatch, tmp_path, script)
    assert script.requested == [(1, PRIMARY), (2, FALLBACK)]             # each candidate refused exactly once
    assert script.provider_requests == []                                # provider requests: 0
    assert len(grants) == 2 and state.attempt_number == 2                # work grants: one per attempt, kept
    assert _family_charges(state) == (0, 0, 0, False, 0)                 # retry-family attempts: 0
    assert script.charges == ["primary", "fallback"]                     # both ended uncharged at the budget owner
    assert modes == ["full_set", "full_set"] and final is None
    assert state.environment_failure.startswith(f"{MODEL_CANDIDATE_UNAVAILABLE}:")
    assert state.stop_reason_evidence.code == admission.MODEL_BINDING_NOT_QUALIFIED
    assert [e.details["model"] for e in state.run_events if e.kind == "model.candidate_unavailable"] == [PRIMARY, FALLBACK]
    assert terminal_failure_category(state) == "model_candidate_unavailable"


def test_run4_sequence_with_an_admitted_fallback_sends_every_later_attempt_and_charges_exactly_those(
        monkeypatch, tmp_path):
    """After DISPATCH-SETTINGS-IDENTITY-001 the fallback is admitted: the primary is refused once, uncharged; every
    later attempt reaches the provider on the fallback and is charged as the real attempt it is."""
    script = _Script(primary=_context_refusal(), fallback="answer")
    state, script, grants, _modes, final = _drive(monkeypatch, tmp_path, script)
    assert script.requested[0] == (1, PRIMARY)
    assert [model for _attempt, model in script.requested].count(PRIMARY) == 1   # the refused primary is never asked again
    assert script.provider_requests == script.requested[1:]              # every later attempt reached the provider
    sent = len(script.provider_requests)
    assert sent >= 2 and len(grants) == sent + 1 == state.attempt_number # work grants: every attempt, the refused one too
    assert script.charges[0] == "primary" and script.charges[1:] == [None] * sent   # charged: exactly the sent attempts
    assert state.budgets.retry_count + state.budgets.targeted_retry_count >= 1
    assert state.unavailable_candidates == {PRIMARY: tb.CONTEXT_BUDGET_UNSATISFIABLE}
    assert state.environment_failure is None                             # stopped by the ordinary policy, not by a refusal
    assert state.no_progress_terminated or (final is not None and not final.should_continue)
