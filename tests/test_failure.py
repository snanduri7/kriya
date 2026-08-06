from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure


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
    }


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
    assert failure.diagnostics is None
