from kriya.workflow.failure import (
    Failure,
    FailureAttributionKind,
    FileLocation,
    QualityGateFailure,
    classify_failure_attribution,
)


def test_failure_to_gate_outcome_shape():
    failure = Failure(
        type="compile",
        message="COMPILATION FAILURE:\nsomething broke",
        raw_output="something broke",
        file_locations=[FileLocation(filepath="App.java", line=12)],
        likely_files=["App.java"],
        attempt=2,
        mode="targeted",
    )
    outcome = failure.to_gate_outcome()
    assert outcome == {
        "attempt": 2,
        "type": "compile",
        "success": False,
        "output": "something broke",
        "mode": "targeted",
        "likely_files": ["App.java"],
        "file_locations": [{"filepath": "App.java", "line": 12, "col": None}],
        "failed_content": {},
        "attempted_edits": [],
        "self_correction_attempt": None,
        "attribution_tier": None,
        "attribution_confidence": None,
        "attribution_reasoning": None,
        "attribution_kind": "SOURCE_DEFECT",
        "subtask_id": None,
        "plan_id": None,
        "milestone_id": None,
        "planned_files": [],
        "verification_target": None,
    }


def test_failure_to_gate_outcome_persists_failed_content_and_attempted_edits():
    """Regression test for a real forensics gap found live, 2026-08-07
    (kriya-protocol-parser-app): failed_content was captured on the Failure
    object at the moment of failure but to_gate_outcome() silently dropped
    it before persistence - a real anchor-match failure ("matched 0 times")
    was undiagnosable after the fact because neither the original file
    content nor the actual attempted search/replace text ever reached
    traces.db, only the generic error message. Both are now persisted
    together, since diagnosing an anchor mismatch needs both halves."""
    failure = Failure(
        type="anchored_edit",
        message="ANCHORED EDIT FAILURE in ProtocolParser.java: matched 0 times",
        raw_output="matched 0 times",
        likely_files=["ProtocolParser.java"],
        failed_content={"ProtocolParser.java": "class ProtocolParser {\n  void decode() {}\n}"},
        attempted_edits=[{"search": ">> 58: old line", "replace": "new line"}],
        attempt=3,
    )
    outcome = failure.to_gate_outcome()
    assert outcome["failed_content"] == {"ProtocolParser.java": "class ProtocolParser {\n  void decode() {}\n}"}
    assert outcome["attempted_edits"] == [{"search": ">> 58: old line", "replace": "new line"}]


def test_failure_to_gate_outcome_falls_back_to_message_when_raw_output_empty():
    # A failure source that only ever produces a message (no separate raw tool
    # output, e.g. the anchored-edit case) must still populate "output" -
    # gate_outcomes/traces.db has always had a non-empty output field.
    failure = Failure(type="anchored_edit", message="ANCHORED EDIT FAILURE in App.java: no match")
    outcome = failure.to_gate_outcome()
    assert outcome["output"] == "ANCHORED EDIT FAILURE in App.java: no match"


def test_quality_gate_failure_carries_the_failure_object():
    failure = Failure(type="test", message="TEST FAILURE:\nboom", raw_output="boom")
    exc = QualityGateFailure(failure)
    assert exc.failure is failure
    assert str(exc) == "TEST FAILURE:\nboom"


def test_failure_defaults_are_empty_not_none():
    # Every downstream consumer (workflow.py's except block, to_gate_outcome())
    # treats these as iterables/dicts unconditionally - a None default would be
    # a latent AttributeError/TypeError waiting for the first failure source
    # that doesn't explicitly pass them.
    failure = Failure(type="general_error", message="boom")
    assert failure.file_locations == []
    assert failure.likely_files == []
    assert failure.failed_content == {}
    assert failure.attempted_edits == []
    assert failure.diagnostics is None
    assert failure.attribution_tier is None
    assert failure.attribution_confidence is None
    assert failure.attribution_reasoning is None


def test_missing_runtime_verification_is_a_contract_defect_not_a_source_defect():
    assert classify_failure_attribution(
        "verification_infrastructure_failure",
        "REQUIRED_RUNTIME_VERIFICATION_MISSING",
    ) is FailureAttributionKind.VERIFICATION_CONTRACT_DEFECT


def test_test_process_failure_is_typed_as_test_evidence_until_source_is_grounded():
    assert classify_failure_attribution(
        "targeted_test", "one assertion failed",
    ) is FailureAttributionKind.TEST_DEFECT
