"""PRD-033 live tier: a small real task set, then the metrics report.

User-run (``-m live_model``). Three real runs against the qualified local
model write their trace rows to this test's own state directory. The report
is then derived twice from those persisted rows and must be identical (same
content digest). Its counts must match the runs actually made, and its model
metrics must be keyed by the exact runtime and inference settings the runs
used. Asserts Kriya evidence, never model prose.

    KRIYA_METRICS_EVIDENCE_DIR=handover/evidence/PRD-033/live \\
    .venv/bin/pytest -m live_model -ra tests/test_live_prd033_metrics.py
"""
import asyncio
import os
from unittest.mock import patch

import pytest
from _chaos_harness import CALC, TEST_SUB, git_workspace
from _live_identity import qualified_live_config

from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.metrics.adjudication import load_adjudications
from kriya.metrics.evidence import load_trace_runs
from kriya.metrics.report import build_report, write_report
from kriya.workflow.workflow import WorkflowEngine

# PRD-034: needs the qualified target identity, never the CI wiring-smoke model.
pytestmark = [pytest.mark.live_model, pytest.mark.live_target]

TEST_MUL = "from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n"


def _run(config, workspace, goal):
    engine = WorkflowEngine(Kernel(config=config), LLMClient(config))
    return asyncio.run(engine.run_generation_workflow(goal=goal, workspace_path=str(workspace)))


def test_live_task_set_metrics_are_reproducible_and_identity_keyed(tmp_path, monkeypatch):
    config, identity = qualified_live_config(tmp_path, monkeypatch)
    results = [
        _run(config, git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="t1"),
             "add a function sub(a, b) returning a - b to calc.py"),
        _run(config, git_workspace(tmp_path, {"calc.py": CALC, "test_mul.py": TEST_MUL}, name="t2"),
             "add a function mul(a, b) returning a * b to calc.py"),
    ]
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": False, "output": "BUILD ERROR: the toolchain rejects every candidate."}):
        results.append(_run(config, git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB}, name="t3"),
                            "add a function sub(a, b) returning a - b to calc.py"))
    runs = load_trace_runs(trace_db_path(config))
    store = load_adjudications()
    first = build_report(runs, adjudications=store, generated={"at": "first"})
    second = build_report(load_trace_runs(trace_db_path(config)), adjudications=load_adjudications(),
                          generated={"at": "second"})
    evidence_dir = os.environ.get("KRIYA_METRICS_EVIDENCE_DIR")
    if evidence_dir:
        write_report(evidence_dir, first)
    assert first["content_digest"] == second["content_digest"]
    generation = first["content"]["outcomes"]["generation"]
    assert generation["runs"] == len(results) == 3
    succeeded = sum(result.get("quality_gates_passed") is True for result in results)
    assert generation["final_verified_success"]["numerator"] == succeeded
    assert results[2]["quality_gates_passed"] is False
    developer_rows = [row for row in first["content"]["model_protocol"] if row["role"] == "developer"]
    assert developer_rows, "no Developer calls were attributed"
    assert {row["runtime_digest"] for row in developer_rows} == {identity["runtime_fingerprint"]}
    assert {row["inference_settings_digest"] for row in developer_rows} == {identity["inference_settings_digest"]}
    expected_identity = f"{identity['runtime_fingerprint']}|{identity['inference_settings_digest']}"
    identities = first["content"]["by_developer_identity"]
    # A run that stopped before the Developer (a refused plan) is keyed "none".
    assert expected_identity in identities and set(identities) <= {expected_identity, "none"}
    assert sum(block["runs"] for block in identities.values()) == 3
    assert first["content"]["adjudication"]["false_success_rate"]["status"] == "UNAVAILABLE"
