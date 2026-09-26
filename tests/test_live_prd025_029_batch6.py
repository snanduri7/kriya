"""Batch 6 (PRD-025..029) live verification against a real local model.

User-run only (``-m live_model``). Each test asserts the deterministic
invariants and writes the model-dependent evidence to
``KRIYA_BATCH6_EVIDENCE_DIR`` when set.

- PRD-025: a real verbose program prints ~3 MB with a decisive failure in
  the middle. The bounded evidence package reaches the real grader within
  the selected model's budget, and it keeps the middle marker. A second run
  with a small capture limit loses bytes at capture, and the grader can no
  longer PASS it.
- PRD-026: a real Developer faces a compile gate that rejects every
  candidate with the same error (an ineffective-repair loop). At
  temperature 0 the run must stop within the retry bound (NO_PROGRESS or
  budget exhaustion), with the retry-progress block recorded - never loop.
- PRD-027: (a) the certification suite against the REAL configured embedding
  model, persisted as the production record doctor reads; (b) a real
  generate run on the certification's Java fixture repository, indexed with
  the real embedder - the Developer's own prompt must carry the golden
  evidence (code quality is secondary).

Run:
    KRIYA_BATCH6_EVIDENCE_DIR=handover/evidence/BATCH6/user-live \\
    KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \\
    .venv/bin/pytest -m live_model -ra -s tests/test_live_prd025_029_batch6.py
"""
import json
import os
import sys

import pytest

from kriya.agents.agent import RunVerifierAgent
from kriya.config.config import load_config
from kriya.core import model_qualification as mq
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import clear_model_runtime_cache
from kriya.tools.process import ProcessController
from kriya.workflow.context_budget import allocation_window
from kriya.workflow.verifier_evidence import RetainedRuntimeEvidence

pytestmark = pytest.mark.live_model

EVIDENCE_DIR = os.environ.get("KRIYA_BATCH6_EVIDENCE_DIR")
BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
PRIMARY = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
WINDOW = 8192


def _evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(tmp_path / "qualifications"))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.base_url = BASE_URL
    config.llm.model = PRIMARY
    config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm.extra_body = {"options": {"num_ctx": WINDOW}}
    config.llm.context_window = WINDOW
    config.llm.max_tokens = 1024
    config.llm_chain = []
    clear_model_runtime_cache()
    return config


_VERBOSE_PROGRAM = """
import sys
for i in range(60000):
    print(f"INFO processing record {i} ok")
    if i == 30000:
        print("AssertionError: MIDDLE total=41 but expected 42")
print("finished all records")
sys.exit(0)
"""


def _run(tmp_path, max_output_chars=2_000_000):
    script = tmp_path / "verbose_app.py"
    script.write_text(_VERBOSE_PROGRAM, encoding="utf-8")
    result = ProcessController(max_output_chars=max_output_chars).run(
        [sys.executable, str(script)], cwd=str(tmp_path), timeout=120,
    ).to_dict()
    step = {
        "command": ["python", "verbose_app.py"], "exit_code": result["returncode"],
        "stdout": result["stdout"], "stderr": result["stderr"], "timed_out": result["timeout"],
        **{key: result[key] for key in ("stdout_lost_chars", "stderr_lost_chars") if key in result},
    }
    return {
        "success": result["returncode"] == 0, "timed_out": result["timeout"], "returncode": result["returncode"],
        "output": result["stdout"], "steps": [step],
    }


@pytest.mark.asyncio
async def test_live_prd025_bounded_package_keeps_middle_failure(cfg, tmp_path):
    run = _run(tmp_path)
    assert len(run["output"]) > 1_500_000
    llm = LLMClient(cfg)
    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Process every record and report the correct total of 42.",
        success_criteria="Output shows every record processed and total 42 with no assertion failure.",
        output=run["output"], returncode=run["returncode"],
        evidence=RetainedRuntimeEvidence.from_run_result(run),
    )
    [package] = [p for p in grade["evidence_packages"] if p["answered"]]
    budget = allocation_window(cfg) * 4
    _evidence("prd025_bounded_package.json", {"grade": grade, "allocation_bytes": budget})

    assert package["rendered_bytes"] <= budget
    assert package["package_truncated"] is True
    assert any(window["kind"] == "assertion" for window in package["included_windows"])
    assert package["decisive_windows_omitted"] is False
    # The middle failure reached the grader (asserted above), so a grader
    # that reads its evidence must not PASS this run. This is the one
    # model-behaviour assertion in this test.
    assert grade["verdict"] != "PASS"


@pytest.mark.asyncio
async def test_live_prd025_capture_loss_can_never_pass(cfg, tmp_path):
    run = _run(tmp_path, max_output_chars=20_000)
    assert run["steps"][0].get("stdout_lost_chars", 0) > 0
    llm = LLMClient(cfg)
    verifier = RunVerifierAgent("run_verifier", llm)
    grade = await verifier.grade(
        goal="Process every record and print 'finished all records'.",
        success_criteria="Output ends with 'finished all records'.",
        output=run["output"], returncode=run["returncode"],
        evidence=RetainedRuntimeEvidence.from_run_result(run),
    )
    _evidence("prd025_capture_loss.json", grade)
    assert grade["passed"] is False
    assert grade["verdict"] in ("UNKNOWN", "FAIL")
    [package] = [p for p in grade["evidence_packages"] if p["answered"]]
    assert package["truncation"][0] == "CAPTURE_TRUNCATION"


@pytest.mark.asyncio
async def test_live_prd026_ineffective_repair_terminates(cfg, tmp_path):
    from unittest.mock import patch

    from kriya.core.kernel import Kernel
    from kriya.workflow.workflow import WorkflowEngine

    cfg.llm.temperature = 0.0
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    llm = LLMClient(cfg)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    calls = {"n": 0}
    real_generation = engine.developer.run_generation

    async def counted(*args, **kwargs):
        calls["n"] += 1
        return await real_generation(*args, **kwargs)

    engine.developer.run_generation = counted
    with patch(
        "kriya.tools.validate.PolymorphicValidator.run_compile_check",
        return_value={"success": False, "output": "BUILD ERROR: toolchain rejects every candidate (injected)."},
    ):
        result = await engine.run_generation_workflow(
            goal="Create calc.py with a function add(a, b) returning a + b.", workspace_path=str(workspace),
        )
    _evidence("prd026_ineffective_repair.json", {
        "failure_category": result.get("failure_category"),
        "retry_progress": result.get("retry_progress"),
        "developer_calls": calls["n"],
    })
    assert result["quality_gates_passed"] is False
    assert result["failure_category"] in ("no_progress", "quality_gates_exhausted")
    assert result["retry_progress"]["distinct_vectors"] >= 1
    # max_retries + targeted_max_retries + API recovery allowance: never an
    # unbounded loop.
    assert calls["n"] <= 12


EMBED_MODEL = os.environ.get("KRIYA_LIVE_EMBED_MODEL", "nomic-embed-text:latest")


@pytest.mark.asyncio
async def test_live_prd027_certification_with_the_real_embedder(cfg):
    from kriya.memory.vector import OllamaEmbeddingClient
    from kriya.workflow import context_certification as cc

    cfg.embedding.model = EMBED_MODEL
    cfg.embedding.base_url = BASE_URL
    runtime = cc.embedding_runtime_identity(cfg)
    assert runtime != "unavailable", "the live embedding runtime must be exactly identifiable"
    client = OllamaEmbeddingClient(base_url=BASE_URL, model=EMBED_MODEL, egress_policy=cfg.autonomy.egress_policy)
    report = await cc.run_certification(
        cfg, embedding_client=client, embedder=cc.EMBEDDER_CONFIGURED, embedding_runtime=runtime,
    )
    path = cc.save_certification(cfg, report)
    data = report.to_dict()
    _evidence("prd027_certification.json", {**data, "record_path": path})
    # Mechanics: every class measured, every miss typed. Whether the real
    # embedder certifies is the recorded result, not a test assumption.
    assert set(data["classes"]) == set(cc.CLASS_RECALL_TARGETS)
    for case in data["cases"]:
        for item in case["items"]:
            assert item["outcome"] in (cc.HIT, cc.NOT_RETRIEVED, cc.BUDGET_EXHAUSTED,
                                       cc.TIER_INSUFFICIENT, cc.SOURCE_UNAVAILABLE)


@pytest.mark.asyncio
async def test_live_prd027_developer_prompt_receives_golden_evidence(cfg, tmp_path):
    from unittest.mock import patch

    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.core.kernel import Kernel
    from kriya.core.role_metrics import current_model_role
    from kriya.workflow.context_recall_fixtures import JAVA_SHOP
    from kriya.workflow.workflow import WorkflowEngine

    cfg.embedding.model = EMBED_MODEL
    cfg.embedding.base_url = BASE_URL
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    repo = tmp_path / "java-shop"
    for path, content in JAVA_SHOP.files:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content, encoding="utf-8")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    os.makedirs(cfg.paths.memory)
    await RepositoryAnalyzer(str(repo)).index_repository(cfg, force=True, generate_conventions_skill=False)

    case = JAVA_SHOP.cases[0]
    developer_prompts = []
    llm = LLMClient(cfg)
    real_complete_result = llm.complete_result

    async def recording(system_prompt, prompt, *args, **kwargs):
        if current_model_role() == "developer":
            developer_prompts.append(system_prompt + "\n" + prompt)
        return await real_complete_result(system_prompt, prompt, *args, **kwargs)

    engine = WorkflowEngine(Kernel(config=cfg), llm)
    with patch.object(llm, "complete_result", side_effect=recording), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": "compile skipped in live context test"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               return_value={"success": True, "output": "tests skipped in live context test"}):
        await engine.run_generation_workflow(goal=case.goal, workspace_path=str(repo))

    assert developer_prompts, "the Developer must have been called"
    first = developer_prompts[0]
    present = {item.path: item.path in first for item in case.golden}
    _evidence("prd027_developer_context.json", {"goal": case.goal, "golden_present": present})
    for required in ("src/main/java/com/shop/order/OrderService.java",
                     "src/main/java/com/shop/order/DiscountPolicy.java",
                     "src/main/java/com/shop/order/OrderController.java"):
        assert present[required], required
