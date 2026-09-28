"""Live-model smoke tests: run the real CLI against an actual local LLM/
embedding endpoint, not mocks. Excluded from the default test run
(pyproject.toml's addopts is `-m "not live_model"`) since they need a real
Ollama instance - run explicitly with `pytest -m live_model`. CI runs the
primary model as a blocking job and a scheduled multi-model matrix (see
.github/workflows/ci.yml).

The bar here is deliberately narrow: "did the real pipeline run end to end
without crashing, on a real API response shape", not "is the generated code
good". A small CI-pulled model isn't held to the same quality bar as whatever
model a real project actually configures - this tier exists to catch
integration/regression bugs in Kriya's own code (a prompt/response contract
change, a JSON-parsing edge case, config threading) that only a real model
response can trigger, not to grade model output quality. Every other test in
this suite already covers Kriya's own logic against controlled mock
responses; this file's only job is proving the real wiring still works.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml
from _live_process import run_reporting_timeout
from _live_smoke_contract import (
    SMOKE_AUTONOMY_BOUNDS,
    SMOKE_LLM_BOUNDS,
    SMOKE_PROCESS_LIMIT_SECONDS,
    SUCCESS,
    assert_smoke_contract,
    workspace_snapshot,
)

pytestmark = pytest.mark.live_model

LIVE_LLM_MODEL = os.environ.get("KRIYA_LIVE_LLM_MODEL", "qwen2.5-coder:1.5b")
LIVE_EMBED_MODEL = os.environ.get("KRIYA_LIVE_EMBED_MODEL", "all-minilm")
LIVE_BASE_URL = os.environ.get("KRIYA_LIVE_BASE_URL", "http://localhost:11434/v1")


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@kriya.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Kriya CI"], cwd=path, check=True)
    (path / "README.md").write_text("live smoke test scratch project\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def _write_config(path):
    config = {
        "llm": {
            "provider": "openai", "model": LIVE_LLM_MODEL, "base_url": LIVE_BASE_URL,
            "temperature": 0.2, "api_key": "local-key",
            # Kriya's own output bounds, set explicitly for the smoke: a
            # wiring smoke needs short answers, and a degenerating small model
            # on a CPU runner otherwise spends many minutes in one runaway call.
            **SMOKE_LLM_BOUNDS,
        },
        "embedding": {"model": LIVE_EMBED_MODEL, "base_url": LIVE_BASE_URL},
        "autonomy": {
            "mode": "guardrails", "run_verification_enabled": False, "web_lookup_enabled": False,
            **SMOKE_AUTONOMY_BOUNDS,
        },
        "paths": {"skills": "./skills", "memory": "./memory"},
    }
    (path / "kriya.yaml").write_text(yaml.dump(config))
    (path / "skills").mkdir(exist_ok=True)
    # llm/embedding base_url and autonomy.mode are SECURITY_AUTHORITY
    # (SEC-009): approve exactly this configuration, as an operator would,
    # in a disposable trust store outside the workspace.
    approval = _run_kriya(["authority", "approve", "--confirm"], cwd=path, timeout=30)
    assert approval.returncode == 0, approval.stdout + approval.stderr


def _kriya_executable():
    """Resolves the `kriya` console script next to the running interpreter,
    rather than trusting bare PATH lookup - robust both for a local venv not
    currently activated and for CI's `pip install -e .` layout."""
    candidate = os.path.join(os.path.dirname(sys.executable), "kriya")
    return candidate if os.path.exists(candidate) else "kriya"


def _authority_home(workspace):
    """A per-test SEC-009 trust store beside the workspace (a trust
    artifact must live outside it), never the user's ~/.kriya/authority."""
    return str(workspace.parent / f"{workspace.name}-authority")


def _state_dir(workspace):
    return str(workspace.parent / f"{workspace.name}-state")


def _run_kriya(args, cwd, timeout):
    return run_reporting_timeout(
        [_kriya_executable(), "--config", "kriya.yaml", *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env=dict(os.environ, KRIYA_AUTHORITY_HOME=_authority_home(cwd), KRIYA_STATE_DIR=_state_dir(cwd)),
    )


def test_doctor_connects_to_a_real_local_llm_and_embedding_server(tmp_path):
    """The simplest possible live signal: kriya doctor's own connectivity
    check, against a real endpoint, actually reports success - not just that
    the command exits without crashing."""
    _write_config(tmp_path)
    result = _run_kriya(["doctor"], cwd=tmp_path, timeout=60)

    assert "Traceback (most recent call last)" not in result.stderr, result.stderr
    assert result.returncode == 0
    assert "[SUCCESS] Connected to local LLM server" in result.stdout, result.stdout
    assert "[SUCCESS] Connected and successfully generated embedding" in result.stdout, result.stdout


def test_generate_meets_the_smoke_contract(tmp_path):
    """A trivial goal through the real pipeline with real LLM and embedding
    calls, held to the smoke contract (tests/_live_smoke_contract.py): a real
    model request with its runtime recorded, Kriya's own bounds kept, a
    typed terminal outcome (SUCCESS or an allowlisted model-capability
    failure), the workspace untouched unless verified SUCCESS, and no safety
    invariant broken. A small CI model need not finish the task - generation
    quality is PRD-035's (`live_target`)."""
    _init_git_repo(tmp_path)
    _write_config(tmp_path)
    goal = "write a python function called add(a, b) in add.py that returns a + b"
    before = workspace_snapshot(tmp_path)

    result = _run_kriya(["generate", goal, "-y", "--json"], cwd=tmp_path, timeout=SMOKE_PROCESS_LIMIT_SECONDS)

    payload = json.loads(result.stdout)  # one typed JSON result, never narrative
    evidence = assert_smoke_contract(tmp_path, payload, before=before, stderr=result.stderr,
                                     state_dir=_state_dir(tmp_path), model=LIVE_LLM_MODEL)
    assert "=== Generation Workflow Completed ===" in result.stderr, result.stderr[-6000:]
    assert result.returncode == (0 if evidence["outcome"] == SUCCESS else 1)
    print(f"smoke contract evidence: {json.dumps(evidence)}")


def test_ask_answers_a_question_about_the_repo_without_crashing(tmp_path):
    """kriya ask's RAG path (hybrid vector+lexical query, then an LLM call)
    against a real embedding endpoint and a real repo to index."""
    _init_git_repo(tmp_path)
    _write_config(tmp_path)
    (tmp_path / "main.py").write_text("def greet(name):\n    return f'hello, {name}'\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add main.py"], cwd=tmp_path, check=True)

    result = _run_kriya(["ask", "what does the greet function do?"], cwd=tmp_path, timeout=120)

    assert "Traceback (most recent call last)" not in result.stderr, result.stderr
    assert result.returncode == 0
    assert result.stdout.strip()


def test_plan_milestones_runs_the_real_planner_without_crashing(tmp_path):
    """MA3.8's own required real-model check (design doc section 28's
    "not merely a prompt snapshot test... run the real MilestonePlanner
    model path" - the deterministic DAG/capability/extension/acceptance/
    physical-topology assertions themselves already live at the validator
    level, tests/test_milestone_validation.py and
    test_plan_milestones_end_to_end_planner_correction_against_real_
    validator in tests/test_milestones.py, per that same section's "a
    deterministic validator test should exist separately").

    Same narrow bar as every other test in this module (see this file's own
    docstring): does the real MilestonePlannerAgent -> parse_milestone_list_v2
    -> MilestonePlanValidator -> bounded-retry pipeline survive a REAL small
    model's actual JSON response shape, not "is the resulting decomposition
    good." A tiny CI-pulled model may genuinely exhaust its bounded
    correction attempts on a real single-module Maven goal (a small model
    reliably emitting a fully v2-compliant, single-entrypoint 3-milestone
    plan is a real, non-trivial ask) - that is still a CLEAN, DEFINED
    outcome (`Milestone planning failed: ...`), not a crash, and is accepted
    here exactly like `test_generate_runs_the_real_pipeline_without_crashing`
    accepts either PASSED or FAILED quality gates above."""
    _init_git_repo(tmp_path)
    _write_config(tmp_path)
    (tmp_path / "pom.xml").write_text(
        "<project><groupId>test</groupId><artifactId>app</artifactId><version>1.0</version></project>"
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add pom.xml"], cwd=tmp_path, check=True)
    goal = (
        "Create one Maven application that reads a message, stores it, "
        "and exposes the stored value."
    )

    result = _run_kriya(["plan-milestones", goal], cwd=tmp_path, timeout=300)

    assert "Traceback (most recent call last)" not in result.stderr, result.stderr
    if result.returncode == 0:
        assert "=== Proposed" in result.stdout, result.stdout
        assert "Plan written to:" in result.stdout, result.stdout
    else:
        # A clean, defined validation-exhaustion failure - not a crash.
        assert "Milestone planning failed:" in result.stdout, result.stdout
