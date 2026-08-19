from kriya.workflow.failure import Failure
from kriya.workflow.run_events import EventAuthority, FailureLedger, RunEvent
from kriya.workflow.state import GenerationState
from kriya.core.trace import TraceLogger


def test_auxiliary_failure_cannot_replace_authoritative_primary():
    ledger = FailureLedger()
    compile_failure = RunEvent(
        kind="failure.recorded", attempt=1, source="compiler",
        authority=EventAuthority.AUTHORITATIVE, failure_type="compile",
        message="javac failed",
    )
    optional_failure = RunEvent(
        kind="auxiliary.failed", attempt=1, source="self_correction",
        authority=EventAuthority.AUXILIARY, failure_type="http_500",
        message="local model backend rejected a tool call",
    )

    ledger.record(compile_failure)
    ledger.record(optional_failure)

    assert ledger.primary is compile_failure
    assert ledger.secondary == [optional_failure]


def test_generation_state_records_failure_authority_without_string_inference():
    state = GenerationState(attempt_number=2)
    state.record_failure(Failure(
        type="compile", message="maven failed", source="maven",
        authority="authoritative", likely_files=["Application.java"],
    ), operation="repair_with_patch")

    assert state.failure_ledger.primary is state.run_events[-1]
    assert state.run_events[-1].details["likely_files"] == ["Application.java"]


def test_trace_logger_persists_canonical_run_events(tmp_path):
    logger = TraceLogger(str(tmp_path / "traces.db"))
    event = RunEvent(
        kind="failure.recorded", attempt=1, source="maven",
        authority=EventAuthority.AUTHORITATIVE, failure_type="compile",
    )
    logger.log_run(
        run_id="run-1", goal="fix", duration_sec=1, attempts=1,
        status="failure", files_modified=[], run_events=[event.to_dict()],
    )
    row = logger.conn.execute(
        "SELECT run_events FROM runs WHERE run_id = ?", ("run-1",)
    ).fetchone()
    logger.close()

    assert '"authority": "authoritative"' in row[0]


def test_trace_logger_persists_content_free_generation_metrics(tmp_path):
    logger = TraceLogger(str(tmp_path / "traces.db"))
    metrics = {
        "calls": 2,
        "duration_seconds": 12.5,
        "files_requested": 4,
        "operation_fallbacks": 1,
    }
    logger.log_run(
        run_id="run-metrics", goal="generate", duration_sec=13, attempts=2,
        status="success", files_modified=["App.java"], generation_metrics=metrics,
    )
    row = logger.conn.execute(
        "SELECT generation_metrics FROM runs WHERE run_id = ?", ("run-metrics",)
    ).fetchone()
    logger.close()

    assert '"calls": 2' in row[0]
    assert '"files_requested": 4' in row[0]


def test_generation_state_metrics_aggregate_events_without_source_content():
    state = GenerationState(
        generation_timings=[
            {"duration_seconds": 2.5, "file_count": 1, "succeeded": True},
            {"duration_seconds": 4.0, "file_count": 2, "succeeded": False},
        ],
        validated_file_revisions={"App.java": "abc"},
    )
    state.record_event(RunEvent(
        kind="operation.fallback", attempt=1, source="developer",
        authority=EventAuthority.ADVISORY,
    ))
    state.record_event(RunEvent(
        kind="validation.invalidated", attempt=2, source="workflow",
        authority=EventAuthority.ADVISORY,
    ))

    assert state.generation_metrics() == {
        "calls": 2,
        "successful_calls": 1,
        "duration_seconds": 6.5,
        "files_requested": 3,
        "operation_fallbacks": 1,
        "validation_invalidations": 1,
        "validated_files": 1,
    }
