import logging

from kriya.workflow.banners import log_gate_banner


def test_nested_attempt_banner_includes_execution_scope(caplog):
    with caplog.at_level(logging.INFO):
        log_gate_banner(
            "OVERALL ATTEMPT",
            "PASSED",
            1,
            scope="subtask=s1 role=owner_recovery",
        )

    assert any(
        "OVERALL ATTEMPT - Attempt 1: PASSED "
        "[subtask=s1 role=owner_recovery]" in record.message
        for record in caplog.records
    )
