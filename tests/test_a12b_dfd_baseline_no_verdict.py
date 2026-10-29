"""A12b (Wave 2 Step 4 sibling): a baseline replay that returned no trustworthy verdict is no conclusion.

MEASURED at 9c7fd73: ``evaluate_candidate_independent_failure`` read a typed compile no-verdict (``success`` False + ``environment_reason_code``) as "the baseline did not reproduce the
failure" and recorded - and cached per (subtask, signature) - a CANDIDATE_CORRECTABLE record with ``baseline_reproduced_failure`` False, so the check was never repeated once the environment
answered and the record asserted a baseline judgment nobody made. A non-dict answer crashed with AttributeError. Now: no record, no cache, the caller's retry behaviour continues untouched.
"""
from unittest.mock import patch

import pytest
from test_deterministic_failure_diagnostic import _SIG_A, _call

from kriya.tools.dependency_execution import (
    COMPILE_NO_VERDICT_CODES,
    MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED,
    MAVEN_INVOCATION_FAILED,
)
from kriya.workflow.deterministic_failure_diagnostic import (
    DeterministicFailureCorrectability,
    DeterministicFailureDiagnosticStore,
)

REPLAY = "kriya.workflow.deterministic_failure_diagnostic.replay_deterministic_verification_against_baseline"
# The no-verdict's own output deliberately EQUALS the current error text: containment alone must never turn it into "the baseline reproduced the failure".
SAME = "same failure text"
TYPED = sorted(COMPILE_NO_VERDICT_CODES | {MAVEN_INVOCATION_FAILED, MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED})


@pytest.mark.parametrize("code", TYPED)
def test_a_typed_no_verdict_replay_records_and_caches_nothing(code):
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value={"success": False, "environment_reason_code": code, "output": SAME}) as replay:
        assert _call(store) is None
        assert store.get("s1", _SIG_A) is None
        assert _call(store) is None                                                     # not cached: the next recurrence replays again
    assert replay.call_count == 2


@pytest.mark.parametrize("answer", [None, "garbage", ["x"]], ids=["none", "str", "list"])
def test_a_replay_that_is_no_answer_is_no_conclusion_not_a_crash(answer):
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value=answer):
        assert _call(store) is None
    assert store.get("s1", _SIG_A) is None


def test_a_baseline_that_reproduced_the_failure_is_still_non_candidate_correctable():
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value={"success": False, "output": SAME}):
        record = _call(store)
    assert record.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE and record.evidence == {"baseline_reproduced_failure": True}
    assert store.get("s1", _SIG_A) is record


def test_a_baseline_that_compiles_is_still_candidate_correctable():
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value={"success": True, "output": "ok"}):
        record = _call(store)
    assert record.correctability == DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE and record.evidence == {"baseline_reproduced_failure": False}


def test_a_baseline_with_a_different_failure_is_still_candidate_correctable():
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value={"success": False, "output": "an unrelated compiler error"}):
        record = _call(store)
    assert record.correctability == DeterministicFailureCorrectability.CANDIDATE_CORRECTABLE


def test_a_no_verdict_then_a_real_answer_is_judged_on_the_real_answer():
    """The environment answers on the next recurrence: the earlier no-verdict left no cached conclusion to mask it."""
    store = DeterministicFailureDiagnosticStore()
    with patch(REPLAY, return_value={"success": False, "environment_reason_code": MAVEN_INVOCATION_FAILED, "output": SAME}):
        assert _call(store) is None
    with patch(REPLAY, return_value={"success": False, "output": SAME}):
        record = _call(store)
    assert record.correctability == DeterministicFailureCorrectability.NON_CANDIDATE_CORRECTABLE
