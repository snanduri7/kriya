"""Wave-5 A4 live-evidence wrapper (2026-11-01): the repo's own signed live tests, imported UNCHANGED, run at the Gate-E
qualified identity. Only the `cfg` fixture's model binding differs from the stock fixtures (the stock identity qwen3-coder:30b
on /v1 has NO qualification record: measured MISSING in evidence/prd027/stock_fixture_identity_measure.txt) plus a per-test
state/log isolation that the repo conftest would otherwise provide (the wrapper runs with -c /dev/null like the accepted PRD-027
wrapper)."""
import os
from pathlib import Path
import pytest, yaml

CFG = Path.home() / ".kriya/operator/graphify-qwen3coder-native-production.yaml"
PRIMARY = "qwen3-coder:30b-kriya-e52213655394"


def qualified_llm():
    from kriya.config import AppConfig
    parsed = yaml.safe_load(CFG.read_text()); parsed.pop("runtime_profile", None)
    return AppConfig.model_validate(parsed).llm


def identity_check(config, stock, evidence_name):
    from kriya.core import model_qualification as mq
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.model_runtime import resolve_configured_model_runtime
    fp = resolve_configured_model_runtime(config, PRIMARY, fresh=True)
    assessment = mq.assess(fp, mq.required_capabilities(config, "developer", PRIMARY),
                           settings=role_inference_settings(config, "developer", PRIMARY))
    problems = stock.live_identity_problems(fp, assessment, 32768) if hasattr(stock, "live_identity_problems") else (
        [] if assessment.status == mq.QUALIFIED else [f"qualification {assessment.status}"])
    payload = {"model": PRIMARY, "runtime_digest": fp.digest, "runtime_exact": fp.exact, "adapter": fp.adapter_version,
               "effective_context_window": fp.effective_context_window, "qualification": assessment.status, "problems": problems}
    writer = getattr(stock, "_write_evidence", None) or getattr(stock, "_evidence")
    writer(evidence_name, payload)
    if problems:
        pytest.fail("LIVE FIXTURE CONFIG ERROR (not a product result): " + "; ".join(problems), pytrace=False)


@pytest.fixture(autouse=True)
def _per_test_state(tmp_path, monkeypatch):
    for var, name in (("KRIYA_STATE_DIR", "state"), ("KRIYA_LOG_DIR", "logs")):
        d = tmp_path / f"_kriya_{name}"; d.mkdir(); monkeypatch.setenv(var, str(d))
