"""GR-R0 RETRY-NO-INFORMATION-GAIN: a retry refused before inference as
ANCHOR_CONTEXT_NOT_ESCALATED (same capability digest, same model, same
requested operation) could only resend the identical request, so it must not
consume another identical attempt.

Measured before the fix (live A5 run 20261006T175548-2b834bc0, and the offline
Graphify replay of CONTEXT-EDIT-PROTOCOL-001): one refused attempt was
followed by a second, byte-identical refusal before the fallback switch; with
no fallback, three identical refusals ran before the no-progress stop
(handover/evidence/gr0/retry_trajectory_prefix.json, synthetic harness, no
benchmark code). The fix changes strategy at the first refusal and stops at
the first identical repeat; a retry whose capability did change is untouched."""
from _edit_protocol_harness import (
    CORRECT_EDIT,
    FABRICATED_EDIT,
    NAMED_LINE,
    NAMED_LINE_FIX,
    TARGET,
    TARGET_SOURCE,
    run_edit_protocol,
)
from test_prd020_milestone_requirements import _probe

from kriya.workflow.edit_capability import ANCHOR_CONTEXT_NOT_ESCALATED
from kriya.workflow.retry_policy import STRATEGY_TRANSITION_AFTER_NO_PROGRESS, force_strategy_transition
from kriya.workflow.retry_strategy import record_workspace_progress
from kriya.workflow.state import GenerationState, RetryBudgets

FALLBACK = "fallback-model"


def _run(tmp_path, monkeypatch, fallback=None):
    return run_edit_protocol(tmp_path, monkeypatch, [FABRICATED_EDIT], probe=_probe,
                             fallback=fallback, fallback_answers=[CORRECT_EDIT])


def _trajectory(run):
    """Per attempt: (model, capability digest, refused before inference)."""
    refused = {e.attempt for e in run.kinds("failure.recorded") if ANCHOR_CONTEXT_NOT_ESCALATED in e.message}
    return [(e.attempt, e.details["model"], e.details["targets"][0]["digest"], e.attempt in refused)
            for e in run.kinds("context.edit_capability")]


def test_a_refusal_switches_to_the_fallback_at_once_and_the_fallback_repairs(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, FALLBACK)
    trajectory = _trajectory(run)
    refusals = [row for row in trajectory if row[3]]
    assert len(refusals) == 1, trajectory
    after = [row for row in trajectory if row[0] == refusals[0][0] + 1]
    assert after and after[0][1] == FALLBACK and not after[0][3], trajectory
    assert run.developer_models == ["dev-model", "dev-model", FALLBACK]
    transitions = run.kinds("retry.strategy_transition")
    assert [e.attempt for e in transitions] == [refusals[0][0]]
    assert transitions[0].details["refused_before_inference"] is True
    assert NAMED_LINE_FIX in (run.workspace / TARGET).read_text()
    assert NAMED_LINE not in (run.workspace / TARGET).read_text()


def test_no_two_attempts_are_refused_under_the_same_capability_model_and_operation(tmp_path, monkeypatch):
    for fallback in (FALLBACK, None):
        run = _run(tmp_path / str(fallback), monkeypatch, fallback)
        refused = [(model, digest) for _, model, digest, was_refused in _trajectory(run) if was_refused]
        if fallback is not None:
            assert len(set(refused)) == len(refused), refused
        else:
            # The one identical repeat is the attempt that proves no strategy
            # change altered the request; the run stops right there.
            assert len(refused) == 2 and len(set(refused)) == 1, refused


def test_without_a_fallback_an_identical_repeat_stops_typed_and_untouched(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch)
    trajectory = _trajectory(run)
    assert trajectory[-1][3] and trajectory[-2][3], trajectory  # stop at the first identical repeat
    assert trajectory[-1][1:3] == trajectory[-2][1:3]
    assert len(trajectory) == 4, trajectory
    assert run.result["failure_category"] == "no_progress"
    assert run.kinds("retry.no_progress_terminal")
    assert (run.workspace / TARGET).read_text() == TARGET_SOURCE


def test_a_retry_whose_capability_changed_still_goes_to_the_same_model(tmp_path, monkeypatch):
    """Negative control: attempt 2 widened the exact window after the anchor
    miss (information gain), so it is a real Developer request on the
    primary, not skipped and not switched."""
    run = _run(tmp_path, monkeypatch, FALLBACK)
    first, second = _trajectory(run)[:2]
    assert first[1] == second[1] == "dev-model"
    assert first[2] != second[2]
    assert not first[3] and not second[3]
    assert run.developer_models[:2] == ["dev-model", "dev-model"]


def test_the_transition_rule_fires_immediately_only_for_a_refusal():
    below = STRATEGY_TRANSITION_AFTER_NO_PROGRESS - 1
    budgets = RetryBudgets()
    assert not force_strategy_transition(budgets, consecutive_no_progress=below, targeted_max_retries=3,
                                         has_fallback_model=True)
    assert not budgets.fallback_targeted_requested and budgets.targeted_retry_count == 0
    assert force_strategy_transition(budgets, consecutive_no_progress=0, targeted_max_retries=3,
                                     has_fallback_model=True, immediate=True)
    assert budgets.fallback_targeted_requested and budgets.targeted_retry_count == 3


def test_a_repeated_refusal_reaches_the_no_progress_limit_and_a_first_one_does_not():
    state = GenerationState()
    assert record_workspace_progress(state, "h", 3, failure_signature="s", stage="no_progress_retry",
                                     capability_unchanged=True)
    assert state.consecutive_no_progress_attempts == 1 and not state.no_progress_terminated
    assert not record_workspace_progress(state, "h", 3, failure_signature="s", stage="no_progress_retry",
                                         capability_unchanged=True, refusal_repeated=True)
    assert state.no_progress_terminated and state.consecutive_no_progress_attempts == 3


def test_a_refusal_on_another_model_is_a_first_refusal_not_a_repeat(tmp_path, monkeypatch):
    """The fallback is refused under the same capability digest the primary
    was refused under (equal output budgets, as in the v5 production bindings
    and the measured Graphify preflight); that is the fallback's own first
    refusal (another model, so another request), so the strategy changes once
    more instead of the run stopping as an identical repeat."""
    run = run_edit_protocol(tmp_path, monkeypatch, [FABRICATED_EDIT], probe=_probe, max_tokens=16384,
                            fallback=FALLBACK, fallback_answers=[FABRICATED_EDIT])
    trajectory = _trajectory(run)
    refused = [row for row in trajectory if row[3]]
    first_fallback = next(row for row in refused if row[1] == FALLBACK)
    assert any(row[1] == "dev-model" and row[2] == first_fallback[2] for row in refused), trajectory
    assert first_fallback[0] in [e.attempt for e in run.kinds("retry.strategy_transition")]
    assert trajectory[-1][0] > first_fallback[0], trajectory  # the run went on after it
    assert run.result["failure_category"] == "no_progress"
