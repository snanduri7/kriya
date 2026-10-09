"""SUITE-PRESERVATION-DIRECT-BASELINE-POLICY-001 (P3, third repair cycle review finding F3, owner decision 2026-10-10:
FIX NOW as a separate narrow slice): the deterministic reproducer written BEFORE the production change (rule 4).

Traced (review of main..6d97558, F3; workflow.py run start 4903-4955 and the direct call site 5392-5403): when the
run's effective full-regression baseline policy is ``disabled`` (configured so, or ``auto`` resolved to not required)
``state.validation_baseline_full_regression`` stays None and the direct terminal boundary passes it to the wrapper as
``suite_baseline=None`` - indistinguishable from the enforce terminal gate's call, which passes no baseline because
lazy capture IS its design (13.2). The wrapper then captures a baseline lazily through the run's own replay callable
and may close (or refuse by attribution) a REGRESSION_PRESERVATION requirement under a policy the operator switched
off: attribution authority the run never had, and a baseline suite run the policy said not to make.

Required invariant (owner, 2026-10-10): the direct suite-preservation path respects the run's baseline policy. With
the policy disabled / resolved disabled, a failing candidate suite never triggers lazy capture and never acquires
attribution authority: the requirement stays open with the typed ``REGRESSION_UNATTRIBUTED`` reason. A captured run
baseline is reused (no recapture); an indeterminate one fails closed (no recapture). The enforce terminal-closure path
keeps its lazy capture. A green suite never captures a baseline, whatever the caller. The distinction is an explicit
caller/policy input, never inferred from ``suite_baseline is None``.

Every case runs the real wrapper (the one entry point of both boundaries) on a real git workspace with the production
pytest gate, a candidate copy at the same HEAD and real PRE/POST attribution - the P1-2 reproducer's harness. The two
call-site helpers mirror the production callers argument for argument (workflow.py's direct terminal gate and
terminal_gate_service.py's enforce gate); the direct one gains the explicit policy input with the fix.
"""
import pytest
from test_repair_003_p1_2_suite_preservation_attribution import (
    CALC_MUL_BROKEN,
    CALC_TIDIED,
    GOAL,
    GREEN_SUITE,
    MUL,
    _candidate,
    _close,
    _run_baseline,
    _suite_entries,
    _workspace,
)

from kriya.workflow import workflow as wf
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.requirements import RequirementOutcome, requirement_outcomes
from kriya.workflow.validation_baseline import _capture_single_baseline

UNATTRIBUTED = "REGRESSION_UNATTRIBUTED"


def _direct_call(workspace, candidate, *, suite_baseline, replay, cache=None, policy="required"):
    """workflow.py's direct terminal-gate call site: the run's goal, its captured full-regression baseline
    (``state.validation_baseline_full_regression`` - None when the policy produced none), its stability cache and its
    own baseline replay callable (always defined at run start, whatever the policy)."""
    return _close(workspace, candidate, goal=GOAL, baseline_source=wf.SUITE_BASELINE_FROM_RUN,
                  suite_baseline=suite_baseline, stability_cache={} if cache is None else cache,
                  baseline_suite_run=replay, run_baseline_policy=policy)


def _enforce_call(workspace, candidate):
    """terminal_gate_service.py's enforce call site: no goal, no baseline, no cache, no replay (lazy capture)."""
    return _close(workspace, candidate)


def _captured(workspace, replay):
    captured = _capture_single_baseline(run_id="run-1", target_test=None, run_validator=replay,
                                        compute_revision=lambda: compute_workspace_content_hash(str(workspace)))
    assert captured.status == "captured" and replay.calls == [None]
    return captured


def _indeterminate():
    baseline = _capture_single_baseline(run_id="run-1", target_test=None, run_validator=lambda _t: {"success": True},
                                        compute_revision=lambda: None)
    assert baseline.status == "baseline_indeterminate"
    return baseline


def _assert_open_unattributed(entry, ledger, reqs):
    assert entry["closed"] is False, entry
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.UNVERIFIED
    assert entry["reason"].startswith(UNATTRIBUTED), entry["reason"]
    assert entry["regression_attribution"]["available"] is False
    assert entry["success"] is False  # the raw suite result is preserved, never reinterpreted


# ---------------------------------------------------------------- 1. direct path, policy disabled, failing suite
@pytest.mark.parametrize("calc, shape", [(CALC_TIDIED, "stable_pre_existing_failure"), (CALC_MUL_BROKEN, "candidate_introduced_failure")])
def test_f3_direct_path_with_the_baseline_policy_disabled_never_captures_and_never_attributes(tmp_path, calc, shape):
    """The run's policy produced no baseline. Whether the candidate's failing suite would have been excused
    (stable pre-existing) or blamed (new failure) by attribution, the closer has no such authority: no lazy capture
    through the run's replay, no wrapper-side baseline run, the requirement stays open, typed REGRESSION_UNATTRIBUTED."""
    workspace = _workspace(tmp_path)
    replay = _run_baseline(workspace)  # the run's replay callable exists; the policy decided not to use it
    candidate = _candidate(tmp_path, workspace, calc)
    entries, reqs, ledger, base, cand = _direct_call(workspace, candidate, suite_baseline=None, replay=replay,
                                                     policy="disabled")
    [entry] = _suite_entries(entries)
    assert cand == [None]  # the candidate suite ran once, whole
    assert replay.calls == [] and base == [], (replay.calls, base)  # no baseline suite run of any kind
    _assert_open_unattributed(entry, ledger, reqs)
    assert "policy (disabled)" in entry["reason"], entry["reason"]  # names the operator's policy, not a capture failure
    if shape == "candidate_introduced_failure":
        assert MUL not in entry["reason"]  # no attribution happened, so no regression was "confirmed" either


# ---------------------------------------------------------------- 2. direct path, captured baseline: reuse, no recapture
def test_f3_direct_path_reuses_the_runs_captured_baseline_without_recapture(tmp_path):
    workspace = _workspace(tmp_path)
    replay = _run_baseline(workspace)
    captured = _captured(workspace, replay)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    cache = {}
    entries, reqs, ledger, base, cand = _direct_call(workspace, candidate, suite_baseline=captured, replay=replay, cache=cache)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True and entry["regression_attribution"]["pre_existing_only"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert cand == [None] and replay.calls == [None] and base == []  # the run's one capture, nothing more


# ---------------------------------------------------------------- 3. direct path, indeterminate baseline: fail closed, no recapture
def test_f3_direct_path_with_an_indeterminate_run_baseline_fails_closed_without_recapture(tmp_path):
    workspace = _workspace(tmp_path)
    replay = _run_baseline(workspace)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _direct_call(workspace, candidate, suite_baseline=_indeterminate(), replay=replay)
    [entry] = _suite_entries(entries)
    _assert_open_unattributed(entry, ledger, reqs)
    assert "indeterminate" in entry["reason"], entry["reason"]
    assert cand == [None] and replay.calls == [] and base == []


# ---------------------------------------------------------------- 4. enforce path, no supplied baseline: lazy capture allowed
def test_f3_enforce_path_still_captures_lazily_and_closes_a_stable_pre_existing_failure(tmp_path):
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, base, cand = _enforce_call(workspace, candidate)
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True and entry["regression_attribution"]["pre_existing_only"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert cand == [None]
    assert base and all(t is None for t in base)  # the untouched workspace captured by the wrapper (replayed whole, bounded)


# ---------------------------------------------------------------- 5. a green suite never captures, whatever the caller
@pytest.mark.parametrize("caller", ["direct_policy_disabled", "direct_captured", "enforce"])
def test_f3_a_green_suite_closes_without_any_baseline_run_for_every_caller(tmp_path, caller):
    workspace = _workspace(tmp_path, suite=GREEN_SUITE)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    replay = _run_baseline(workspace)
    if caller == "enforce":
        entries, reqs, ledger, base, cand = _enforce_call(workspace, candidate)
        expected_replay_calls = []
    elif caller == "direct_captured":
        captured = _captured(workspace, replay)
        entries, reqs, ledger, base, cand = _direct_call(workspace, candidate, suite_baseline=captured, replay=replay)
        expected_replay_calls = [None]  # the run's own capture at run start, nothing from the closure
    else:
        entries, reqs, ledger, base, cand = _direct_call(workspace, candidate, suite_baseline=None, replay=replay,
                                                         policy="disabled")
        expected_replay_calls = []
    [entry] = _suite_entries(entries)
    assert entry["closed"] is True and entry["success"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-2"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert cand == [None] and base == [] and replay.calls == expected_replay_calls
    assert not entry.get("regression_attribution")


# ---------------------------------------------------------------- 6. enforce path, the lazy capture fails: fail closed
def test_f3_enforce_path_whose_lazy_capture_fails_stays_open_unattributed(tmp_path, monkeypatch):
    """The untouched workspace's pristine revision cannot be computed, so the capture is indeterminate: nothing is
    excused, nothing is blamed, the entry says REGRESSION_UNATTRIBUTED and names the capture failure."""
    monkeypatch.setattr(wf, "compute_workspace_content_hash", lambda _path: None)
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    entries, reqs, ledger, _base, cand = _enforce_call(workspace, candidate)
    [entry] = _suite_entries(entries)
    _assert_open_unattributed(entry, ledger, reqs)
    assert "could not be captured" in entry["reason"], entry["reason"]
    assert cand == [None]


# ---------------------------------------------------------------- 7. the caller distinction is explicit and closed
def test_f3_an_unknown_baseline_source_is_refused_before_any_suite_runs(tmp_path):
    """Neither call site may be guessed: a caller that does not say who owns the baseline gets no closure and no run."""
    workspace = _workspace(tmp_path)
    candidate = _candidate(tmp_path, workspace, CALC_TIDIED)
    with pytest.raises(ValueError, match="unknown baseline_source 'guess'"):
        _close(workspace, candidate, baseline_source="guess")
