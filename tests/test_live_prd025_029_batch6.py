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
- PRD-028: a real repair on a Python module too large to be shown whole,
  with Developer investigation enabled. The escalation is triggered
  deterministically: the first compile of the changed module fails once at
  apply_fee's line, so the retry must request member authority for
  Ledger.apply_fee against the real model's candidate (GRANTED, in scope,
  origin CANDIDATE, present in pristine). Every recorded expansion is
  revision-bound and inside the write scope.
- PRD-029: an enforce-mode run makes a small, explicitly authorized API
  change (the goal names the owner, the symbol and the change - a DIRECT
  authorization). On success the ContractRegistry holds a public_api record
  bound to that commit, and the RunRecord cycle records the same registry
  identity; its consumer (the caller) is recorded, never claimed complete.

Evidence status (one JSON file per case): LIVE_EXERCISED when the path ran
and every assertion held; NOT_LIVE_EXERCISED when the case skipped because
the model never reached the path (the PRD-029 verified commit depends on the
model; a skip is never verification); FAILED otherwise.

Run:
    KRIYA_BATCH6_EVIDENCE_DIR=handover/evidence/BATCH6/user-live \\
    KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \\
    .venv/bin/pytest -m live_model -ra -s tests/test_live_prd025_029_batch6.py
"""
import contextlib
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
# The qualified production identity these cases must run under: the packaged
# llm settings (num_ctx 32768, temperature/top_k/top_p) of a model qualified
# with `kriya model qualify`. The cfg fixture never overrides any of them.
EXPECTED_CONTEXT_WINDOW = int(os.environ.get("KRIYA_LIVE_CONTEXT_WINDOW", "32768"))


def _write_evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


LIVE_EXERCISED = "LIVE_EXERCISED"
NOT_LIVE_EXERCISED = "NOT_LIVE_EXERCISED"
LIVE_FAILED = "FAILED"


@contextlib.contextmanager
def _verdict(name):
    """Evidence for one live case, written with its honest status: a SKIP is
    NOT_LIVE_EXERCISED (the path never ran, so nothing was verified), never
    counted as verification. The body fills the yielded dict."""
    evidence: dict = {}
    try:
        yield evidence
    except pytest.skip.Exception as skipped:
        _write_evidence(name, {**evidence, "status": NOT_LIVE_EXERCISED, "reason": str(skipped)})
        raise
    except BaseException as error:
        _write_evidence(name, {**evidence, "status": LIVE_FAILED, "error": f"{type(error).__name__}: {error}"})
        raise
    _write_evidence(name, {**evidence, "status": LIVE_EXERCISED})


def live_identity_problems(fingerprint, assessment, expected_window):
    """Why the resolved Developer identity is not the qualified one these live
    cases are meant to exercise ([] when it is). A fixture that silently ran
    at another window or without qualification measured the wrong thing: an
    8K window with the default byte bound refused prompts the qualified 32K
    identity serves (Batch 6 live, 2026-09-27)."""
    problems = []
    if fingerprint.effective_context_window != expected_window:
        problems.append(
            f"effective context window is {fingerprint.effective_context_window}, expected {expected_window}")
    if assessment.status != mq.QUALIFIED:
        problems.append(f"developer qualification is {assessment.status}: {'; '.join(assessment.reasons)}")
    return problems


@pytest.fixture
def cfg(tmp_path):
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.base_url = BASE_URL
    config.llm.model = PRIMARY
    config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm_chain = []
    clear_model_runtime_cache()
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.model_runtime import resolve_configured_model_runtime

    fingerprint = resolve_configured_model_runtime(config, PRIMARY, fresh=True)
    assessment = mq.assess(
        fingerprint, mq.required_capabilities(config, "developer", PRIMARY),
        settings=role_inference_settings(config, "developer", PRIMARY),
    )
    problems = live_identity_problems(fingerprint, assessment, EXPECTED_CONTEXT_WINDOW)
    _write_evidence("preflight_identity.json", {
        "model": PRIMARY, "runtime_digest": fingerprint.digest, "runtime_exact": fingerprint.exact,
        "effective_context_window": fingerprint.effective_context_window,
        "expected_context_window": EXPECTED_CONTEXT_WINDOW, "qualification": assessment.status,
        "problems": problems,
    })
    if problems:
        pytest.fail("LIVE FIXTURE CONFIG ERROR (not a product result): " + "; ".join(problems)
                    + ". Qualify the model first: kriya model qualify --model " + PRIMARY, pytrace=False)
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
    with _verdict("prd025_bounded_package.json") as evidence:
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
        evidence.update({"grade": grade, "allocation_bytes": budget})

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
    with _verdict("prd025_capture_loss.json") as evidence:
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
        evidence.update(grade)
        assert grade["passed"] is False
        assert grade["verdict"] in ("UNKNOWN", "FAIL")
        [package] = [p for p in grade["evidence_packages"] if p["answered"]]
        assert package["truncation"][0] == "CAPTURE_TRUNCATION"


@pytest.mark.asyncio
async def test_live_prd026_ineffective_repair_terminates(cfg, tmp_path):
    with _verdict("prd026_ineffective_repair.json") as evidence:
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
        evidence.update({
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
    with _verdict("prd027_certification.json") as evidence:
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
        evidence.update({**data, "record_path": path})
        # Mechanics: every class measured, every miss typed. Whether the real
        # embedder certifies is the recorded result, not a test assumption.
        assert set(data["classes"]) == set(cc.CLASS_RECALL_TARGETS)
        for case in data["cases"]:
            for item in case["items"]:
                assert item["outcome"] in (cc.HIT, cc.NOT_RETRIEVED, cc.BUDGET_EXHAUSTED,
                                           cc.TIER_INSUFFICIENT, cc.SOURCE_UNAVAILABLE)


@pytest.mark.asyncio
async def test_live_prd027_developer_prompt_receives_golden_evidence(cfg, tmp_path):
    with _verdict("prd027_developer_context.json") as evidence:
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
        evidence.update({"goal": case.goal, "golden_present": present})
        for required in ("src/main/java/com/shop/order/OrderService.java",
                         "src/main/java/com/shop/order/DiscountPolicy.java",
                         "src/main/java/com/shop/order/OrderController.java"):
            assert present[required], required


def _large_module(methods=160):
    body = ["class Ledger:"]
    for i in range(methods):
        body.append(f"    def entry_{i}(self, amount):\n        return amount + {i}\n")
    body.append("    def apply_fee(self, amount):\n        return amount - 5\n")
    return "\n".join(body) + "\n"


@pytest.mark.asyncio
async def test_live_prd028_member_authority_is_revision_bound_and_in_scope(cfg, tmp_path):
    with _verdict("prd028_expansions.json") as evidence:
        import sqlite3
        from unittest.mock import patch

        from kriya.core.kernel import Kernel
        from kriya.core.state_paths import trace_db_path
        from kriya.tools.validate import PolymorphicValidator
        from kriya.workflow.workflow import WorkflowEngine

        cfg.autonomy.mode = "guardrails"
        cfg.autonomy.run_verification_enabled = False
        cfg.autonomy.developer_investigation_enabled = True
        cfg.paths.skills = str(tmp_path / "skills")
        repo = tmp_path / "repo"
        repo.mkdir()
        source = _large_module()
        (repo / "ledger.py").write_text(source, encoding="utf-8")
        fee_line = source.splitlines().index("        return amount - 5") + 1
        # Deterministic trigger, independent of what the model writes: the
        # first compile of a CHANGED ledger.py fails once at apply_fee's line
        # (PolymorphicValidator's own Python error shape). The retry must then
        # request member authority for that member against the real candidate.
        real_compile = PolymorphicValidator.run_compile_check
        injected = {"done": False}

        def compile_failing_once(validator, files):
            written = os.path.join(validator.workspace_path, "ledger.py")
            if not injected["done"] and "ledger.py" in files and os.path.exists(written):
                with open(written, encoding="utf-8") as stream:
                    changed = stream.read() != source
                if changed:
                    injected["done"] = True
                    return {"success": False,
                            "output": f"Syntax error in ledger.py line {fee_line}: return amount (injected)"}
            return real_compile(validator, files)

        engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
        with patch.object(PolymorphicValidator, "run_compile_check", autospec=True,
                          side_effect=compile_failing_once):
            await engine.run_generation_workflow(
                goal="In ledger.py, change Ledger.apply_fee so the fee is 7 instead of 5.",
                workspace_path=str(repo),
            )
        with sqlite3.connect(trace_db_path(cfg)) as db:
            (events_json,) = db.execute("SELECT run_events FROM runs ORDER BY rowid DESC").fetchone()
        expansions = [e["details"] for e in json.loads(events_json) if e["kind"] == "authority.expansion"]
        evidence.update({"injected_compile_failure": injected["done"], "fee_line": fee_line,
                         "expansions": expansions})
        if not injected["done"]:
            pytest.skip("the model never wrote a changed ledger.py, so the retry path was never reached")
        assert expansions, "a located failure in an authorized target must produce an authority request"
        for record in expansions:
            if record["outcome"] == "GRANTED":
                assert record["in_write_scope"] is True
                assert record["source_revision"] and record["source_origin"] in ("PRISTINE", "CANDIDATE")
            assert record["mutation_boundary"] == "authorized_write_scope"
        fee = [r for r in expansions if r["path"] == "ledger.py" and "apply_fee" in r["member_id"]]
        # The located failure's own retry request, resolved against this
        # run's changed candidate - labelled CANDIDATE, never PRISTINE - and
        # apply_fee exists in the pristine file whatever the candidate did.
        retry = [r for r in fee if r["evidence"].get("source") == "retry_member_hints"]
        assert retry and retry[0]["outcome"] == "GRANTED"
        assert retry[0]["source_origin"] == "CANDIDATE"
        assert retry[0]["member_in_pristine"] is True


_PRICING = "def total(items):\n    return sum(items)\n"
_CHECKOUT = "from pricing import total\n\n\ndef checkout(items):\n    return total(items)\n"
_TEST = ("from checkout import checkout\n\n\ndef test_checkout():\n    assert checkout([1, 2]) == 3\n")


@pytest.mark.asyncio
async def test_live_prd029_authorized_api_change_is_bound_to_its_commit(cfg, tmp_path):
    with _verdict("prd029_registry.json") as evidence:
        import subprocess

        from kriya.control.contracts import KIND_PUBLIC_API
        from kriya.control.persistence import load_contract_registry, scan_run_records
        from kriya.core.kernel import Kernel
        from kriya.workflow.workflow import WorkflowEngine
        from kriya.workflow.workflow_controller import WorkflowController

        cfg.autonomy.mode = "guardrails"
        cfg.autonomy.run_verification_enabled = False
        cfg.paths.skills = str(tmp_path / "skills")
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / "pricing.py").write_text(_PRICING)
        (repo / "checkout.py").write_text(_CHECKOUT)
        (repo / "tests" / "test_checkout.py").write_text(_TEST)
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                     ["add", "-A"], ["commit", "-q", "-m", "base"]):
            subprocess.run(["git", *args], cwd=repo, check=True)
        goal = ("In pricing, change the method named total to take a second parameter tax_rate "
                "with default 0.0 and return the sum multiplied by (1 + tax_rate).")
        engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
        result = await WorkflowController(engine).execute(goal, str(repo), migration_mode="enforce")
        legacy = result.legacy_result or {}
        registry = load_contract_registry(str(repo))
        records = [r for r in registry.all_records() if r.kind == KIND_PUBLIC_API]
        evidence.update({
            "quality_gates_passed": legacy.get("quality_gates_passed"),
            "contract_registry": legacy.get("contract_registry"),
            "registry": registry.to_dict(),
        })
        if not legacy.get("quality_gates_passed"):
            pytest.skip("the enforce run did not reach a verified commit (model outcome, recorded as evidence)")
        [record] = records
        assert record.owner == "pricing.py" and record.source_revision
        assert "checkout.py" in record.consumers and record.consumers_complete is False
        committed = [cycle for run in scan_run_records(str(repo)).records for cycle in run.commits
                     if cycle.get("contract_registry")]
        assert committed and committed[-1]["contract_registry"]["after_digest"] == registry.digest()

