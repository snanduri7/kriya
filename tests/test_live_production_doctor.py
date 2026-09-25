"""PRD-010/013/014 real local-model fingerprint and qualification evidence.

The doctor computes a stable exact runtime fingerprint from the real served
artifact (PASS), and reports the qualification as FAIL (MODEL_NOT_QUALIFIED)
unless a current qualification record exists for that exact runtime: a
model name is never a qualification. The qualification store is an empty
temporary directory here, so the FAIL is deterministic."""
import os
import subprocess

import pytest

from kriya.config.config import load_config
from kriya.core.model_qualification import QUALIFICATION_HOME_ENV
from kriya.production_doctor import (
    MODEL_NOT_QUALIFIED,
    CheckStatus,
    probe_llm_runtime,
    run_production_doctor,
)


@pytest.mark.live_model
def test_production_doctor_fingerprints_the_real_runtime_and_withholds_name_based_qualification(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(QUALIFICATION_HOME_ENV, str(tmp_path / "empty-qualifications"))
    # Built through load_config() like the CLI: a bare AppConfig() lacks the
    # packaged llm.capabilities and hid a real-path misclassification.
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    cfg = load_config(str(operator))
    cfg.llm.base_url = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
    cfg.llm.model = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
    cfg.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")

    first = probe_llm_runtime(cfg)
    second = probe_llm_runtime(cfg)
    assert first["selected_model"] is not None
    assert first["runtime"].exact, first["runtime"].to_dict()
    assert first["fingerprint"] and len(first["fingerprint"]) == 64
    assert first["fingerprint"] == second["fingerprint"]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    checks = {check.id: check for check in run_production_doctor(cfg, str(workspace)).checks}

    assert checks["model.connectivity"].status is CheckStatus.PASS
    fingerprint = checks["model.runtime_fingerprint"]
    assert fingerprint.status is CheckStatus.PASS
    assert fingerprint.evidence["fingerprint"] == first["fingerprint"]
    qualification = checks["model.qualification"]
    assert qualification.status is CheckStatus.FAIL
    assert qualification.evidence["reason_code"] == MODEL_NOT_QUALIFIED
    assert qualification.evidence["roles"]["developer"][0]["status"] == "MISSING"
