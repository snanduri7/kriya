"""PRD-011 reopen: the attested contained toolchain must be persisted WITH
the verification verdict, not just returned by the validator. The workflow
used to rebuild each gate outcome as {attempt, type, success, output}, which
dropped the image digest before it reached traces.db.

The producer here is real (PolymorphicValidator -> ProcessController result
-> _validation_result -> gate outcome -> TraceLogger); only the container
execution itself is substituted with the result the OCI backend returns
after attestation."""
import json
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.tools.process import ProcessResult
from kriya.tools.validate import PolymorphicValidator, toolchain_evidence
from kriya.workflow.workflow import WorkflowEngine

ATTESTED = {
    "language": "python", "runtime": "cpython", "runtime_version": "3.12",
    "build_tool": "pip", "build_tool_version": None, "containment_image": "python:3.12-slim",
    "requirement_source": "default:python-runtime-policy",
    "image_digest": "sha256:" + "ab" * 32, "observed_runtime_version": "3.12",
    "observed_build_tool_version": None,
}


def _contained_run(*_args, **_kwargs):
    return ProcessResult(returncode=0, stdout="", stderr="", timeout=False, toolchain_identity=ATTESTED)


def _latest_gate_outcomes(cfg):
    conn = sqlite3.connect(trace_db_path(cfg))
    try:
        row = conn.execute("SELECT gate_outcomes FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


def test_toolchain_evidence_is_empty_for_host_mode_results():
    assert toolchain_evidence({"success": True, "output": ""}) == {}
    assert toolchain_evidence(None) == {}
    assert toolchain_evidence({"success": True, "toolchain_identity": ATTESTED}) == {"toolchain_identity": ATTESTED}


def test_contained_compile_result_carries_the_attested_identity(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)
    with patch("kriya.tools.validate.ProcessController.run", side_effect=_contained_run):
        result = validator.run_compile_check(["app.py"])
    assert result["success"] is True
    assert result["toolchain_identity"]["image_digest"] == ATTESTED["image_digest"]


def test_app_sequence_result_carries_the_attested_identity(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    (tmp_path / "app.py").write_text("print(1)\n")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)
    with patch("kriya.tools.validate.ProcessController.run", side_effect=_contained_run):
        result = validator.run_app_sequence([["python3", "app.py"]])
    assert result["success"] is True
    assert result["toolchain_identity"] == ATTESTED
    assert result["steps"][0]["toolchain_identity"] == ATTESTED


@pytest.mark.asyncio
async def test_contained_compile_gate_persists_image_digest_in_the_trace(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
    ])
    we = WorkflowEngine(kernel, llm)
    we.reviewer.run = AsyncMock(return_value="Review: Approved")

    with patch("kriya.tools.validate.ProcessController.run", side_effect=_contained_run):
        res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    compile_outcomes = [o for o in _latest_gate_outcomes(cfg) if o.get("type") == "compile" and o.get("success")]
    assert compile_outcomes, "no successful compile gate was persisted"
    for outcome in compile_outcomes:
        assert outcome["toolchain_identity"]["image_digest"] == ATTESTED["image_digest"]
        assert outcome["toolchain_identity"]["observed_runtime_version"] == "3.12"
    assert os.path.exists(trace_db_path(cfg))


@pytest.mark.asyncio
async def test_host_mode_gate_outcomes_never_claim_a_toolchain_identity(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write app.py",
        '[{"filepath": "app.py", "content": "print(1)"}]',
    ])
    we = WorkflowEngine(kernel, llm)
    we.reviewer.run = AsyncMock(return_value="Review: Approved")

    res = await we.run_generation_workflow(goal="Create app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    outcomes = _latest_gate_outcomes(cfg)
    assert any(o.get("type") == "compile" for o in outcomes)
    assert all("toolchain_identity" not in o for o in outcomes)
