import json
from pathlib import Path
from types import SimpleNamespace

from kriya.workflow.attempt import _diagnosis_mismatch_bypass_reason
from kriya.workflow.context_projection import project_implementation_source
from kriya.workflow.retry_policy import decide_retry_action
from kriya.workflow.run_events import EventAuthority, FailureLedger, RunEvent
from kriya.workflow.state import GenerationState


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_replay_demo1_runtime_hang_keeps_implementation_evidence():
    incident = _fixture("demo1_runtime_hang.json")
    projection = project_implementation_source(
        incident["source"], "Application.java", incident["max_chars"],
        reason="runtime failure triage",
    )
    assert incident["required_evidence"] in projection.content


def test_replay_b10l_auxiliary_failure_preserves_compile_primary():
    incident = _fixture("b10l_auxiliary_failure.json")
    ledger = FailureLedger()
    ledger.record(RunEvent(
        kind="failure.recorded", attempt=1, source="maven",
        authority=EventAuthority.AUTHORITATIVE,
        failure_type=incident["primary_type"], message=incident["primary_message"],
    ))
    ledger.record(RunEvent(
        kind="auxiliary.failed", attempt=1, source="self_correction",
        authority=EventAuthority.AUXILIARY,
        failure_type=incident["auxiliary_type"], message=incident["auxiliary_message"],
    ))
    assert ledger.primary.failure_type == "compile"
    assert ledger.primary.message == incident["primary_message"]


def test_replay_b10o_fuzzy_diagnosis_check_has_bounded_veto():
    incident = _fixture("b10o_diagnosis_veto.json")
    state = GenerationState()
    state.budgets.diagnosis_mismatch_veto_counts[incident["filepath"]] = incident["prior_vetoes"]
    reason = _diagnosis_mismatch_bypass_reason(
        state, SimpleNamespace(), incident["filepath"], "<project/>",
    )
    assert incident["expected_bypass_fragment"] in reason


def test_replay_b10p_stops_when_every_budget_is_exhausted():
    incident = _fixture("b10p_budget_exhaustion.json")
    decision = decide_retry_action(
        retry_count=incident["retry_count"], max_retries=incident["max_retries"],
        targeted_retry_count=incident["targeted_retry_count"],
        targeted_max_retries=incident["targeted_max_retries"],
        has_implicated_files=True, has_missing_files=False,
        has_fallback_model=True,
        fallback_targeted_attempted=incident["fallback_targeted_attempted"],
        environment_failure=None,
    )
    assert decision.action.value == incident["expected_action"]
