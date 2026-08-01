"""Live-model smoke tests: run the real CLI against an actual local LLM/
embedding endpoint, not mocks. Excluded from the default test run
(pyproject.toml's addopts is `-m "not live_model"`) since they need a real
Ollama instance - run explicitly with `pytest -m live_model`. CI runs them in
a dedicated, non-blocking job that installs Ollama and pulls a small model
fresh (see .github/workflows/ci.yml's `live-model-smoke` job).

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
import os
import subprocess
import sys

import pytest
import yaml

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
        },
        "embedding": {"model": LIVE_EMBED_MODEL, "base_url": LIVE_BASE_URL},
        "autonomy": {
            "mode": "guardrails", "run_verification_enabled": False, "web_lookup_enabled": False,
        },
        "paths": {"skills": "./skills", "memory": "./memory", "logs": "./logs"},
    }
    (path / "kriya.yaml").write_text(yaml.dump(config))
    (path / "skills").mkdir(exist_ok=True)


def _kriya_executable():
    """Resolves the `kriya` console script next to the running interpreter,
    rather than trusting bare PATH lookup - robust both for a local venv not
    currently activated and for CI's `pip install -e .` layout."""
    candidate = os.path.join(os.path.dirname(sys.executable), "kriya")
    return candidate if os.path.exists(candidate) else "kriya"


def _run_kriya(args, cwd, timeout):
    return subprocess.run(
        [_kriya_executable(), "--config", "kriya.yaml", *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
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


def test_generate_runs_the_real_pipeline_without_crashing(tmp_path):
    """A trivial goal through the real pipeline end to end - Planner,
    Architect, Developer, Quality Gates, and Reviewer all making real LLM
    calls, real RAG retrieval against a real embedding call. Deliberately
    does NOT assert quality_gates_passed - see module docstring. What matters
    is that the pipeline completes and reports a defined outcome, rather than
    crashing on an unexpected real response shape.

    Generous timeout: SkillEngine always loads Kriya's global skill library
    (kriya/skills/skill.py's `load_global=True`, no config override exists,
    by design) in addition to any project-local skills - confirmed live that
    this repo's own accumulated skill content adds meaningfully to prompt
    size regardless of relevance to the goal. A small CI-pulled model is
    proportionally slower against that larger context than the project's
    real configured model would be - a throughput expectation, not a bug."""
    _init_git_repo(tmp_path)
    _write_config(tmp_path)
    goal = "write a python function called add(a, b) in add.py that returns a + b"

    result = _run_kriya(["generate", goal, "-y"], cwd=tmp_path, timeout=600)

    assert "Traceback (most recent call last)" not in result.stderr, result.stderr
    assert "=== Generation Workflow Completed ===" in result.stdout, result.stdout
    assert "Quality Gates: PASSED" in result.stdout or "Quality Gates: FAILED" in result.stdout, result.stdout


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
