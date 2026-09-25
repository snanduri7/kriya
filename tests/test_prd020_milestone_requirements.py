"""PRD-020 on the milestone path (`kriya generate --from-milestones`).

Through the real CLI, milestone driver, WorkflowEngine and LLMClient (the
transport and runtime probe are stand-ins): each milestone verifies its own
narrower goal; the plan's integration unit is the verifier of the user's
original requirements, derived from the plan's original goal - never from a
milestone's rewording of it.
"""
import json
import re
import sqlite3
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import ModelRuntimeFingerprint
from kriya.core.state_paths import trace_db_path
from kriya.workflow.requirements import derive_requirements

ORIGINAL_GOAL = "Build a two-module project.\n- m1.py defines VALUE\n- m2.py defines VALUE too\n"


def _probe(**kw):
    return ModelRuntimeFingerprint(
        alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
        artifact_digest=f"sha256:{kw['model']}", tokenizer_digest="sha256:tok",
        model_context_length=262144, configured_context_window=kw["configured_context"],
        effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"],
    )


class Transport:
    """LLMClient._request_once stand-in; the verifier gives a verdict for
    every REQ id it is shown unless ``omit_verdicts``."""

    def __init__(self, omit_verdicts=False):
        self.spec_prompts = []
        self.omit_verdicts = omit_verdicts

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        prompt = user_prompt or ""
        target = "m2.py" if "build M2" in prompt or "m2.py" in prompt else "m1.py"
        if "Goal Spec Compliance Checker" in first:
            self.spec_prompts.append(prompt)
            ids = [] if self.omit_verdicts else re.findall(r"^(REQ-\d+):", prompt, flags=re.MULTILINE)
            content = json.dumps({"compliant": True, "reasoning": "ok", "missing_requirements": [],
                                  "likely_files": [],
                                  "requirement_verdicts": [{"id": i, "verdict": "satisfied"} for i in ids]})
        elif "File List Planner" in first:
            content = json.dumps({"files": [target]})
        elif "Planner Agent" in first:
            content = f"Step 1: create {target}"
        elif model == "dev-model":
            content = f"VALUE = '{target}'\n"
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}


def _run(tmp_path, monkeypatch, transport, **autonomy):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe)
    model_runtime.clear_model_runtime_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": "prd020", "original_goal": ORIGINAL_GOAL, "milestones": [
        {"id": "M1", "goal": "build M1: create m1.py", "success_criterion": "m1.py exists", "depends_on": []},
        {"id": "M2", "goal": "build M2: create m2.py", "success_criterion": "m2.py exists", "depends_on": ["M1"]},
    ]}))
    monkeypatch.chdir(workspace)
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = True
    for key, value in autonomy.items():
        setattr(cfg.autonomy, key, value)
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch.object(LLMClient, "_request_once", new=transport):
        result = CliRunner().invoke(main, ["generate", "--from-milestones", str(plan), "-y"])
    return cfg, result


def _events(cfg, kind):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    return [e["details"] for (payload,) in rows for e in json.loads(payload or "[]") if e["kind"] == kind]


def test_only_the_integration_unit_verifies_the_plans_original_requirements(tmp_path, monkeypatch):
    transport = Transport()
    cfg, result = _run(tmp_path, monkeypatch, transport)

    expected = derive_requirements(ORIGINAL_GOAL)
    carrying = [p for p in transport.spec_prompts if "Original Requirements" in p]
    assert carrying, result.output
    # The milestone units' checks never carry them; the integration unit's always do.
    assert len(carrying) < len(transport.spec_prompts)
    assert all(f"{r.id}: {r.text}" in p for p in carrying for r in expected.requirements)
    derived = _events(cfg, "requirement.derived")
    assert [d["digest"] for d in derived] == [expected.digest]
    verdicts = _events(cfg, "requirement.verdicts")
    assert verdicts and set(verdicts[-1]["outcomes"].values()) == {"satisfied"}


def test_a_milestone_plan_is_not_successful_with_an_unverified_original_requirement(tmp_path, monkeypatch):
    transport = Transport(omit_verdicts=True)
    cfg, result = _run(tmp_path, monkeypatch, transport, requirement_unknown_policy="block")

    assert any("Original Requirements" in p for p in transport.spec_prompts), result.output
    assert "REQUIREMENTS_UNRESOLVED" in result.output
    assert result.exit_code != 0


def test_a_failed_integration_check_reports_the_milestones_it_leaves_committed(tmp_path, monkeypatch):
    """Milestones commit incrementally. When the integration unit's original
    requirement check then fails, the plan is not successful, and the
    result, the RunRecord and the CLI all say which units are committed and
    still applied - none of it reads as rolled back."""
    import glob
    import os

    from kriya.control.run_record import RunRecord

    transport = Transport(omit_verdicts=True)
    cfg, result = _run(tmp_path, monkeypatch, transport, requirement_unknown_policy="block")

    assert result.exit_code != 0
    assert ("Committed and still applied (not rolled back): M1, M2. "
            "integration failed before its changes were applied.") in result.output
    payload = json.loads(result.output[result.output.index("{", result.output.index("=== Milestone sequence")):])
    assert payload["status"] == "integration_failed"
    assert payload["committed_work_units"] == ["M1", "M2"] and payload["committed_changes_retained"] is True
    assert payload["work_unit_states"]["M1"]["status"] == "VERIFIED"
    workspace = tmp_path / "ws"
    assert (workspace / "m1.py").exists() and (workspace / "m2.py").exists()  # really still applied
    [path] = glob.glob(os.path.join(str(workspace), ".kriya", "control", "runs", "*.json"))
    with open(path, encoding="utf-8") as handle:
        record = RunRecord.from_dict(json.load(handle))
    assert record.committed_work_units() == ["M1", "M2"]
    assert record.terminal_status != "SUCCESS"


def test_only_settled_committed_cycles_count_as_committed_units():
    from types import SimpleNamespace

    from kriya.control.run_record import COMMIT_COMMITTED, RunRecord

    commits = [
        {"work_unit": {"kind": "milestone", "milestone_id": "M1"}, "result": COMMIT_COMMITTED},
        {"work_unit": {"kind": "milestone", "milestone_id": "M2"}, "result": "NOT_COMMITTED"},
        {"work_unit": {"kind": "milestone", "milestone_id": "M3"}, "result": None},  # still open
        {"work_unit": {"kind": "integration", "milestone_id": None}, "result": COMMIT_COMMITTED},
        {"work_unit": {"kind": "milestone", "milestone_id": "M1"}, "result": COMMIT_COMMITTED},
    ]
    assert RunRecord.committed_work_units(SimpleNamespace(commits=commits)) == ["M1", "integration"]


def test_a_milestone_that_fails_after_its_own_commit_is_reported_as_applied(tmp_path, monkeypatch):
    """The dependency-drop guard runs after M2's changes were committed: M2
    fails, and the CLI names it as applied rather than implying otherwise."""
    calls = []

    def dropped(workspace, established):
        calls.append(1)
        return ["com.example:lib:1"] if len(calls) >= 2 else []

    with patch("kriya.workflow.milestones.check_dependency_regression", new=dropped):
        cfg, result = _run(tmp_path, monkeypatch, Transport())

    assert result.exit_code != 0
    payload = json.loads(result.output[result.output.index("{", result.output.index("=== Milestone sequence")):])
    assert payload["status"] == "milestone_failed" and payload["milestone_id"] == "M2"
    assert payload["committed_work_units"] == ["M1", "M2"]
    assert "M2 failed after its changes were committed; they remain applied." in result.output
    assert "before its changes were applied" not in result.output
