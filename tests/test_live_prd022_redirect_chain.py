"""PRD-022 live acceptance: a REAL ``brownfield_ownership_redirect`` followed by the corrective retry.

Why this file exists (Wave 5 / Gate E, 2026-11-01). The stock live case
``test_live_prd020_024_batch5.test_prd022_duplicate_owner_first_and_second_attempt`` never produced a redirect (the model
created a brand-new test file, so no existing test was redirected), and its assertion was conditional on one happening: it
executed nothing. This fixture removes the dependence on model variance for the TRIGGER, and keeps the part PRD-022 signs
as live: what Kriya does after the redirect.

Split of what is scripted and what is real (disclosed in the evidence as ``scripted_inputs``/``live_inputs``):

- SCRIPTED: the Planner answer, the Architect answer (its file list, so the completeness check cannot pre-empt the
  ownership gate), and Developer call #1 (the parallel ``src/discount_pricing.py`` plus the existing test redirected to it).
  These are the inputs that make the violating attempt certain.
- REAL (production path, unchanged): the candidate sandbox, the compile/test gates, ``find_brownfield_test_redirections``,
  the ``brownfield_ownership_redirect`` failure, ``state.ownership_redirect_recovery``, the deterministic restore in
  ``spec_compliance``, construction of the corrective retry request, Developer call #2 against the qualified model
  (``KRIYA_PRD022_RETRY=live``, the default), the terminal gates, and the commit decision. ``KRIYA_PRD022_RETRY=scripted``
  scripts call #2 with the correct owner fix and is the offline dry run of the trigger path (no model call at all).

Assertions are Kriya invariants, never model prose, and none is conditional. The terminal criterion is fixed up front:

- (a) the run passes its gates, ``src/pricing.py`` really changed, ``tests/test_pricing.py`` is byte-identical to its
  baseline and the parallel file is absent; or
- (b) the run fails closed and every workspace file is byte-identical to the seed commit.

Anything else (notably: success while the owner is untouched) FAILS the test.

Run (the Wave-5 wrapper pattern, ``-c /dev/null`` with an isolated state dir), after ``source <live-env>/env.sh``::

    KRIYA_PRD022_EVIDENCE_DIR=... KRIYA_LIVE_QUALIFIED_CONFIG=~/.kriya/operator/graphify-qwen3coder-native-production.yaml \\
    KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b-kriya-e52213655394 .venv/bin/pytest -m live_model -ra -s \\
    tests/test_live_prd022_redirect_chain.py
"""
import asyncio
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from kriya.config import AppConfig
from kriya.config.config import load_config
from kriya.core import model_qualification as mq
from kriya.core.model_runtime import clear_model_runtime_cache
from kriya.core.state_paths import trace_db_path

# PRD-034: needs the qualified target identity, never the CI wiring-smoke model.
pytestmark = [pytest.mark.live_model, pytest.mark.live_target]

EVIDENCE_DIR = os.environ.get("KRIYA_PRD022_EVIDENCE_DIR")
BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")
PRIMARY = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen3-coder:30b")
QUALIFIED_CONFIG = os.environ.get("KRIYA_LIVE_QUALIFIED_CONFIG")
EXPECTED_CONTEXT_WINDOW = int(os.environ.get("KRIYA_LIVE_CONTEXT_WINDOW", "32768"))
RETRY_MODE = os.environ.get("KRIYA_PRD022_RETRY", "live")

OWNER = "def price(quantity, unit):\n    return quantity * unit\n"
OWNER_TEST = "from src.pricing import price\n\n\ndef test_price():\n    assert price(2, 3) == 6\n"
UNRELATED = "def ship():\n    return 1\n"
SEED = {"src/__init__.py": "", "src/pricing.py": OWNER, "src/shipping.py": UNRELATED, "tests/test_pricing.py": OWNER_TEST}
PARALLEL_FILE = "src/discount_pricing.py"
PARALLEL = [
    {"filepath": PARALLEL_FILE, "content": (
        "def discounted_price(quantity, unit):\n    total = quantity * unit\n"
        "    return total * 0.9 if quantity > 10 else total\n")},
    {"filepath": "tests/test_pricing.py", "content": (
        "from src.discount_pricing import discounted_price\n\n\n"
        "def test_price():\n    assert discounted_price(2, 3) == 6\n")},
]
SCRIPTED_FIX = [{"filepath": "src/pricing.py", "content": (
    "def price(quantity, unit):\n    total = quantity * unit\n    return total * 0.9 if quantity > 10 else total\n")}]
GOAL = "Apply a 10% discount to price() for quantities over 10"


def _write_evidence(name, payload):
    if EVIDENCE_DIR:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, name), "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)


def _seed_repo(root):
    for path, content in SEED.items():
        os.makedirs(os.path.dirname(os.path.join(root, path)) or root, exist_ok=True)
        with open(os.path.join(root, path), "w", encoding="utf-8") as fh:
            fh.write(content)
    for args in (["init", "-q"], ["config", "user.email", "live@kriya.test"], ["config", "user.name", "Kriya Live"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)


def _workspace_files(root):
    found = {}
    for path in Path(root).rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(root).parts and "__pycache__" not in path.parts:
            found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return found


def _identity_problems(fingerprint, assessment, expected_window):
    problems = []
    if fingerprint.effective_context_window != expected_window:
        problems.append(
            f"effective context window is {fingerprint.effective_context_window}, expected {expected_window}")
    if assessment.status != mq.QUALIFIED:
        problems.append(f"developer qualification is {assessment.status}: {'; '.join(assessment.reasons)}")
    if not fingerprint.exact:
        problems.append("the resolved runtime fingerprint is not exact")
    return problems


@pytest.fixture(autouse=True)
def _per_test_state(tmp_path, monkeypatch):
    for var, name in (("KRIYA_STATE_DIR", "state"), ("KRIYA_LOG_DIR", "logs")):
        directory = tmp_path / f"_kriya_{name}"
        directory.mkdir()
        monkeypatch.setenv(var, str(directory))


@pytest.fixture
def cfg(tmp_path):
    operator = tmp_path / "operator.yaml"
    operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    if QUALIFIED_CONFIG:
        parsed = yaml.safe_load(Path(QUALIFIED_CONFIG).expanduser().read_text(encoding="utf-8"))
        parsed.pop("runtime_profile", None)
        config.llm = AppConfig.model_validate(parsed).llm
    else:
        config.llm.base_url = BASE_URL
        config.llm.model = PRIMARY
        config.llm.api_key = os.environ.get("KRIYA_LIVE_API_KEY", "local-key")
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
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.model_runtime import resolve_configured_model_runtime

    fingerprint = resolve_configured_model_runtime(config, PRIMARY, fresh=True)
    assessment = mq.assess(
        fingerprint, mq.required_capabilities(config, "developer", PRIMARY),
        settings=role_inference_settings(config, "developer", PRIMARY))
    problems = _identity_problems(fingerprint, assessment, EXPECTED_CONTEXT_WINDOW)
    _write_evidence("prd022_redirect_preflight_identity.json", {
        "model": PRIMARY, "runtime_digest": fingerprint.digest, "runtime_exact": fingerprint.exact,
        "adapter": fingerprint.adapter_version, "effective_context_window": fingerprint.effective_context_window,
        "qualification": assessment.status, "problems": problems, "retry_mode": RETRY_MODE})
    if problems:
        pytest.fail("LIVE FIXTURE CONFIG ERROR (not a product result): " + "; ".join(problems), pytrace=False)
    return config


def _trace_events(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    return [event for (payload,) in rows for event in json.loads(payload or "[]")]


def _run_chain(cfg, workspace):
    """Real WorkflowEngine; Planner/Architect/Developer call #1 scripted, everything after the redirect real."""
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    llm = LLMClient(cfg)
    real_complete = llm.complete
    scripted_retry = RETRY_MODE == "scripted"

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system = system_prompt or ""
        if "Kriya Planner Agent" in system:
            return "Step 1: implement the change"
        if "Kriya Architect Agent" in system:
            return "Design\n```json\n" + json.dumps({"files": [PARALLEL_FILE, "tests/test_pricing.py"]}) + "\n```"
        if scripted_retry and "Kriya Reviewer Agent" in system:
            return "Review: Approved"
        return await real_complete(system_prompt, user_prompt, *args, **kwargs)

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    real_generation = engine.developer.run_generation
    calls = []

    async def generation(*args, **kwargs):
        calls.append({key: (dict(value) if isinstance(value, dict) else value) for key, value in kwargs.items()})
        if len(calls) == 1:
            return PARALLEL
        if scripted_retry:
            return SCRIPTED_FIX
        return await real_generation(*args, **kwargs)

    engine.developer.run_generation = generation
    result = asyncio.run(engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace)))
    return result, calls


def test_prd022_a_real_redirect_is_followed_by_the_owner_scoped_corrective_retry(cfg, tmp_path):
    ws = tmp_path / "ws"
    _seed_repo(str(ws))
    evidence = {"retry_mode": RETRY_MODE, "goal": GOAL,
                "scripted_inputs": ["planner", "architect", "developer_call_1"],
                "live_inputs": ["developer_call_2+", "reviewer", "all gates", "ownership gate", "restore", "commit"]
                if RETRY_MODE == "live" else ["all gates", "ownership gate", "restore", "commit (retry also scripted)"]}
    try:
        result, calls = _run_chain(cfg, ws)
        events = _trace_events(cfg)
        restored = [e for e in events if e["kind"] == "ownership.redirect_restored"]
        after = {path: text for path, text in _workspace_files(ws).items() if not path.startswith(".kriya/")}
        evidence.update({
            "developer_call_count": len(calls), "retry_request": {
                key: calls[1].get(key) for key in ("known_target_files", "implicated_files", "operation_by_file",
                                                   "prior_error_context")} if len(calls) > 1 else None,
            "retry_files_with_current_content": sorted((calls[1].get("files_with_current_content") or {}))
            if len(calls) > 1 else None,
            "redirect_restored_events": restored, "event_kinds": sorted({e["kind"] for e in events}),
            "quality_gates_passed": result.get("quality_gates_passed"), "failure_category": result.get("failure_category"),
            "files": result.get("files"), "workspace_after": sorted(after)})

        # 1. A REAL redirect happened (the first Developer answer was rejected by the production ownership gate).
        assert len(calls) >= 2, "no corrective retry request was made: the redirect did not fire (fixture/trigger failure)"
        assert calls[1].get("prior_error_context") == (
            f"owner=src/pricing.py, candidate={PARALLEL_FILE}, test=tests/test_pricing.py"), calls[1].get("prior_error_context")
        recorded = [e for e in events if e["kind"] == "failure.recorded"]
        evidence["recorded_failure_types"] = [(e.get("failure_type"), e.get("source")) for e in recorded]
        assert any(e.get("failure_type") == "brownfield_ownership_redirect" for e in recorded), recorded

        # 2. The immediately following request: the exact grounded owner, narrow authority, nothing unrelated.
        retry = calls[1]
        assert retry["known_target_files"] == ["src/pricing.py"]
        assert retry["implicated_files"] == ["src/pricing.py"]
        assert {path: op.value for path, op in retry["operation_by_file"].items()} == {"src/pricing.py": "repair_with_patch"}
        assert "src/pricing.py" in retry["files_with_current_content"]
        blob = json.dumps(retry, default=str)
        assert json.dumps(OWNER)[1:-1] in blob  # the exact current owner source reached the request
        assert "def ship" not in blob and "src/shipping.py" not in blob  # no unrelated full-file authority or content

        # 3. Persisted evidence records the redirect -> recovery chain.
        assert restored, "no ownership.redirect_restored event was persisted"
        details = restored[0]["details"]
        assert details["restored_tests"] == ["tests/test_pricing.py"]
        assert details["removed_candidates"] == [PARALLEL_FILE]
        assert details["owners"] == ["src/pricing.py"]

        # 4. Terminal criterion, fixed up front: (a) verified fix of the OWNER, or (b) fail closed with the seed untouched.
        if result.get("quality_gates_passed"):
            evidence["terminal_branch"] = "a_success"
            assert after["src/pricing.py"] != OWNER, "success was reported while the grounded owner is untouched"
            assert after["tests/test_pricing.py"] == OWNER_TEST, "the redirected test is not byte-identical to its baseline"
            assert PARALLEL_FILE not in after, "the abandoned parallel file survived the commit"
            assert after["src/shipping.py"] == UNRELATED
        else:
            evidence["terminal_branch"] = "b_fail_closed"
            assert after == SEED, f"a failed run changed the workspace: {sorted(set(after) ^ set(SEED))}"
        evidence["status"] = "LIVE_EXERCISED"
    except BaseException as error:
        evidence["status"] = "FAILED"
        evidence["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        _write_evidence("prd022_redirect_chain.json", evidence)
