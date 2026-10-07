"""LR-R1-P5 measurement harness (evidence only; moved to handover/evidence/lr-r1-p5/ after use).
Runs the P5 reproducer's end-to-end scenario once; writes measured facts to $P5_MEASURE_OUT."""
import json
import logging
import os
import re
import sqlite3

import test_enforce_verified_no_change as harness
import test_lr_r1_p5_integration_obligation_reproducer as repro

from kriya.core.attempt_evidence import reader
from kriya.core.attempt_evidence.explain import explain_run
from kriya.core.state_paths import ENV_STATE_DIR, trace_db_path


def test_measure(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="kriya.workflow.workflow_controller")
    monkeypatch.setattr(harness, "_plan", repro._plan_with_integration)
    workspace, _events, result = harness._enforce(tmp_path, harness.TOOL_CRITERION)
    state = os.environ[ENV_STATE_DIR]
    [run_id] = reader.list_runs(state)
    run = reader.open_run(state, run_id)
    records = list(run.records())
    explained = explain_run(state, run_id)
    integration_events = []
    for record in records:
        if record["kind"] == "mirror.event" and (record.get("payload") or {}).get("kind") == "integration.obligation":
            ref = (record.get("blobs") or {}).get("event")
            integration_events.append(json.loads(run.blob(ref)) if ref else record["payload"])
    files_by_unit = {}
    from kriya.config import AppConfig
    with sqlite3.connect(trace_db_path(AppConfig())) as db:
        cols = [c[1] for c in db.execute("PRAGMA table_info(runs)")]
        if "files_modified" in cols:
            for row in db.execute("SELECT * FROM runs"):
                data = dict(zip(cols, row, strict=True))
                files_by_unit[str(data.get(cols[0]))] = data.get("files_modified")
    out = {
        "subtask_status": {r.subtask_id: r.status.value for r in result.subtask_results},
        "legacy_status": result.legacy_result.get("status"),
        "integration_log": re.findall(r"INTEGRATION_OBLIGATION_\w+ id=\S+ consumer=\S+ missing=\[[^\]]*\]", caplog.text),
        "terminal_obligations_log": [line.split(":", 3)[-1].strip() for line in caplog.text.splitlines()
                                     if "TERMINAL OBLIGATIONS UNSATISFIED" in line],
        "files_by_trace_row": files_by_unit,
        "workspace_service_applied": (workspace / harness.SERVICE).read_text() != harness.UNCACHED_SERVICE,
        "model_calls": sum(1 for r in records if r["kind"] == "model.result"),
        "model_calls_by_role": {role: sum(1 for r in records if r["kind"] == "model.result" and r.get("role") == role)
                                for role in sorted({r.get("role") for r in records if r["kind"] == "model.result"})},
        "integration_events": integration_events,
        "evidence_verify": explained["verification"],
        "Q9": {k: explained["Q9"].get(k) for k in ("terminal_cause", "failed_terminal_gates", "integration_obligations")},
    }
    with open(os.environ["P5_MEASURE_OUT"], "w") as handle:
        json.dump(out, handle, indent=1, default=str)
