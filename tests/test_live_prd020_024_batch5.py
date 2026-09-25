"""Batch 5 (PRD-020..024) live verification against a real local model.

Each case runs the real WorkflowEngine (Planner, Architect, Developer, gates,
Reviewer) on a small scratch repository and records its evidence. The bar is
the batch's mechanisms under real model output, not code quality:

- PRD-020: a goal with several exact requirements keeps its requirement ids
  and digest whatever the Planner/Architect wrote; every requirement ends
  with a recorded outcome and evidence.
- PRD-021: the Planner/Architect see the grounded existing owner; the run
  either extends it or leaves an auditable finding.
- PRD-022: a duplicate-owner tendency is recorded attempt by attempt; if the
  ownership gate fires, the next request targets the grounded owner only.
- PRD-024: a brownfield repository with one known pre-existing failing test:
  the `auto` baseline triggers and that failure is never classified new.
- PRD-023 has no live case: nothing a model sees changed (see its handover).

Run (both models must already be pulled; the primary is the one used):
    KRIYA_BATCH5_EVIDENCE_DIR=handover/evidence/BATCH5/user-live \\
    KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \\
    .venv/bin/pytest -m live_model -ra -s tests/test_live_prd020_024_batch5.py
"""
import asyncio
import json
import os
import sqlite3
import subprocess

import pytest

from kriya.config.config import load_config
from kriya.core import model_qualification as mq
from kriya.core.model_runtime import clear_model_runtime_cache
from kriya.core.state_paths import trace_db_path

pytestmark = pytest.mark.live_model

EVIDENCE_DIR = os.environ.get("KRIYA_BATCH5_EVIDENCE_DIR")
BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
PRIMARY = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")


def _evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


def _repo(root, files):
    for path, content in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, path)) or root, exist_ok=True)
        with open(os.path.join(root, path), "w", encoding="utf-8") as fh:
            fh.write(content)
    for args in (["init", "-q"], ["config", "user.email", "live@kriya.test"], ["config", "user.name", "Kriya Live"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv(mq.QUALIFICATION_HOME_ENV, str(tmp_path / "qualifications"))
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm.base_url = BASE_URL
    config.llm.model = PRIMARY
    config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
    config.llm.extra_body = {"options": {"num_ctx": 16384}}
    config.llm.context_window = 16384
    config.llm.max_tokens = 4096
    config.llm_chain = []
    config.autonomy.mode = "guardrails"
    config.autonomy.run_verification_enabled = False
    config.autonomy.web_lookup_enabled = False
    config.autonomy.spec_compliance_enabled = True
    config.engineering_triage.enabled = True
    config.skills.load_global = False
    config.skills.load_cwd = False
    config.paths.skills = str(tmp_path / "skills")
    config.paths.memory = str(tmp_path / "memory")
    clear_model_runtime_cache()
    return config


def _run(cfg, goal, workspace, *, record_developer=False):
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    llm = LLMClient(cfg)
    prompts = []
    original = llm.complete

    async def recording(system_prompt, user_prompt, *args, **kwargs):
        prompts.append({"system": (system_prompt or "").splitlines()[0] if system_prompt else "",
                        "user": user_prompt})
        return await original(system_prompt, user_prompt, *args, **kwargs)

    llm.complete = recording
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    developer_calls = []
    if record_developer:
        real = engine.developer.run_generation

        async def developer(*args, **kwargs):
            developer_calls.append({k: kwargs.get(k) for k in (
                "known_target_files", "implicated_files", "prior_error_context", "operation_by_file")})
            return await real(*args, **kwargs)

        engine.developer.run_generation = developer
    result = asyncio.run(engine.run_generation_workflow(goal=goal, workspace_path=str(workspace)))
    return result, prompts, developer_calls


def _events(cfg, kind):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    return [e["details"] for (payload,) in rows for e in json.loads(payload or "[]") if e["kind"] == kind]


def test_prd020_lineage_survives_model_paraphrase(cfg, tmp_path):
    from kriya.workflow.requirements import derive_requirements

    ws = tmp_path / "ws"
    _repo(str(ws), {"README.md": "inventory scratch project\n"})
    goal = ("Create inventory.py for a small stock tracker.\n"
            "- Add a constant MAX_STOCK set to 500\n"
            "- Add a function restock(current, amount) that returns the new stock level\n"
            "- restock must never return more than MAX_STOCK\n")
    result, prompts, _ = _run(cfg, goal, ws)
    expected = derive_requirements(goal)
    lineage = _events(cfg, "requirement.lineage")
    verdicts = _events(cfg, "requirement.verdicts")
    _evidence("prd020-lineage.json", {"requirements": result.get("requirements"), "lineage": lineage,
                                      "verdicts": verdicts, "quality_gates_passed": result.get("quality_gates_passed"),
                                      "plan": result.get("plan"), "design": result.get("design")})

    assert result["requirements"]["digest"] == expected.digest  # ids fixed from the goal, not the plan
    assert set(result["requirements"]["outcomes"]) == set(expected.ids)
    assert {entry["stage"] for entry in lineage} >= {"plan", "design"}
    constraint = next(r for r in expected.requirements if r.text.startswith("restock must never"))
    assert any(f"{constraint.id}: {constraint.text}" in p["user"] for p in prompts if "Architect" in p["system"])
    if result["quality_gates_passed"]:
        assert verdicts, "a passing run carries the verifier's per-requirement outcomes"
        assert set(verdicts[-1]["outcomes"]) == set(expected.ids)


ORDER_REPO = {
    "src/__init__.py": "",
    "src/order_validator.py": ("class OrderValidator:\n    def validate(self, order):\n"
                               "        return order.quantity > 0\n"),
    "src/checkout.py": ("from src.order_validator import OrderValidator\n\n\ndef checkout(order):\n"
                        "    return OrderValidator().validate(order)\n"),
}


def test_prd021_planner_sees_the_grounded_owner(cfg, tmp_path):
    ws = tmp_path / "ws"
    _repo(str(ws), ORDER_REPO)
    goal = "Reject orders whose quantity exceeds 100 during checkout validation"
    result, prompts, _ = _run(cfg, goal, ws)
    planner = [p["user"] for p in prompts if "Planner" in p["system"]]
    findings = result.get("ownership_findings") or []
    _evidence("prd021-ownership.json", {"findings": findings, "files": result.get("files"),
                                        "plan": result.get("plan"), "design": result.get("design"),
                                        "planner_saw_owner": any("src/order_validator.py (responsibility" in p
                                                                 for p in planner)})

    assert any("src/order_validator.py (responsibility: valid" in p for p in planner)
    # Either the owner was extended, or every new overlapping file is an auditable finding.
    extended = "src/order_validator.py" in (result.get("files") or [])
    assert extended or findings or not [f for f in (result.get("files") or []) if f not in ORDER_REPO]


PRICING_REPO = {
    "src/__init__.py": "",
    "src/pricing.py": "def price(quantity, unit):\n    return quantity * unit\n",
    "tests/test_pricing.py": "from src.pricing import price\n\n\ndef test_price():\n    assert price(2, 3) == 6\n",
}


def test_prd022_duplicate_owner_first_and_second_attempt(cfg, tmp_path):
    ws = tmp_path / "ws"
    _repo(str(ws), PRICING_REPO)
    goal = "Add a bulk discount: price() gives 10% off for quantities over 10"
    result, _, developer_calls = _run(cfg, goal, ws, record_developer=True)
    redirects = [e for e in _events(cfg, "ownership.redirect_restored")]
    _evidence("prd022-attempts.json", {"developer_calls": developer_calls, "redirect_restored": redirects,
                                       "files": result.get("files"), "quality_gates_passed":
                                       result.get("quality_gates_passed")})

    assert developer_calls
    for before, after in zip(developer_calls, developer_calls[1:]):
        if "candidate=" in str(after.get("prior_error_context") or ""):  # an ownership violation was retried
            assert after["known_target_files"] == ["src/pricing.py"]


def test_prd024_pre_existing_failure_is_not_a_new_regression(cfg, tmp_path):
    ws = tmp_path / "ws"
    _repo(str(ws), {**PRICING_REPO,
                    "tests/test_legacy.py": "def test_known_broken():\n    assert 1 == 2, 'known pre-existing failure'\n"})
    cfg.autonomy.brownfield_full_regression_baseline_policy = "auto"
    goal = "Add a docstring to price() in src/pricing.py explaining its arguments"
    result, _, _ = _run(cfg, goal, ws)
    policy = _events(cfg, "validation_baseline.policy")
    deltas = _events(cfg, "validation_baseline.full_regression_delta")
    _evidence("prd024-baseline.json", {"policy": policy, "deltas": deltas,
                                       "quality_gates_passed": result.get("quality_gates_passed"),
                                       "failure_category": result.get("failure_category")})

    assert policy and policy[-1]["effective"] == "required", policy  # pricing.py is named by a test
    for delta in deltas:
        assert delta["level2"].get("tests/test_legacy.py::test_known_broken") in (None, "PRE_EXISTING_FAILURE")
    if deltas:
        assert deltas[-1]["level1_classification"] in ("PRE_EXISTING_FAILURE", "CHANGED_FAILURE", "NEW_FAILURE")
