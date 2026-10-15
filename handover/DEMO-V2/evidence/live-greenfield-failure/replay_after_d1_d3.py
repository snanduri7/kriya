"""Offline causal re-measurement of the live run (no model, no network): the RECORDED failures of run 20261015T152407-5f87716a are
fed through the production recording / attribution / pre-flight code as of the D1-D3 fixes. Run from the repository root:
    .venv/bin/python -I handover/DEMO-V2/evidence/live-greenfield-failure/replay_after_d1_d3.py
"""
import asyncio
import gzip
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from kriya.workflow.attempt import _retry_package_for_attempt  # noqa: E402
from kriya.workflow.failure import Failure, QualityGateFailure  # noqa: E402
from kriya.workflow.failure_grounding import build_static_rule_failure  # noqa: E402
from kriya.workflow.retry_strategy import handle_attempt_failure  # noqa: E402
from kriya.workflow.state import GenerationState  # noqa: E402

BASE = Path(__file__).resolve().parent / "attempt-evidence"
TEST_FILE = "src/test/java/hellokriya/AppTest.java"
KNOWN = ["pom.xml", "src/main/java/hellokriya/App.java", TEST_FILE]


def _records():
    with open(BASE / "records.jsonl", encoding="utf-8") as handle:
        return {record["seq"]: record for record in map(json.loads, handle)}


def _blob(record, name):
    digest = record["blobs"][name].split(":", 1)[1]
    return gzip.decompress((BASE / "blobs" / digest[:2] / f"{digest}.gz").read_bytes()).decode("utf-8")


async def main():
    import tempfile  # noqa: PLC0415

    from test_d1_open_findings import _ctx, _write_project  # noqa: PLC0415

    records = _records()
    static_text = _blob(records[56], "raw_output")
    protocol_text = _blob(records[81], "raw_output")
    with tempfile.TemporaryDirectory() as tmp, patch("kriya.workflow.retry_strategy.classify_environment_failure", return_value=None):
        work = Path(tmp)
        _write_project(work)
        ctx, state = _ctx(work), GenerationState()
        state.last_attempt_mode = "full_set"
        state.attempt_number = 1
        await handle_attempt_failure(state, ctx, QualityGateFailure(build_static_rule_failure(static_text, str(work), KNOWN, 1)))
        for attempt in (2, 3, 4):
            state.attempt_number = attempt
            await handle_attempt_failure(state, ctx, QualityGateFailure(Failure(
                type="operation_contract", message="OPERATION CONTRACT FAILURE", raw_output=protocol_text,
                likely_files=[TEST_FILE], diagnostics={"reason_code": "CONFLICTING_DEVELOPER_RESPONSE"})))
        request = _retry_package_for_attempt(state, ctx, target_files=[TEST_FILE], prompt_window=32768).render_error()
        print("D1  attempt-5-style request still names the open finding:", "vacuous_test_assertion" in request, "|", TEST_FILE in request)
        print("D1  open findings carried:", [(f.failure_type, f.files) for f in state.open_findings])
        state.attempt_number = 5
        state.last_self_diagnosis = (state.budgets.last_failure_signature, ["pom.xml"], 5)
        await handle_attempt_failure(state, ctx, QualityGateFailure(build_static_rule_failure(static_text, str(work), KNOWN, 5)))
        print("D2  repair target after the model's analysis named pom.xml:", state.last_implicated_files, "(live run: ['pom.xml'])")
    print("D3  see tests/test_d3_diagnosis_mismatch_scope.py (recorded attempt-6 response through run_attempt)")


asyncio.run(main())
