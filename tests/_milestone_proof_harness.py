"""Shared harness for the PRD-008 milestone-completion tests (S4b, S4c).

Real commits through the one commit seam (terminal_commit.py -> RunRecord
cycle + commit evidence); milestone state reloaded from the sidecar exactly
as `kriya generate --from-milestones` does. FakeEngine stands in only for
model work. Imported by bare name (tests/ is on sys.path under pytest's
default import mode), like _plugin_test_support.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.agents.contracts import MilestoneV2
from kriya.config.config import AppConfig
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.edit_safety import read_file_revision
from kriya.workflow.milestone_validation import topological_order
from kriya.workflow.milestones import load_or_resume_milestone_run_state, run_milestones
from kriya.workflow.terminal_commit import CandidateFile, commit_terminal_candidate, materialize_candidate
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = str(Path(__file__).resolve().parent)
GROUP = "grp"


def _milestone(mid, depends_on=()):
    return MilestoneV2(
        id=mid, goal=f"build {mid}", success_criterion=f"{mid} works", depends_on=list(depends_on),
    )


def _plan(milestones):
    return {"group_id": GROUP, "original_goal": "the goal", "milestones": [m.model_dump() for m in milestones]}


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return Path(os.path.realpath(workspace))


def _apply(workspace_path, changes):
    """Commit ``changes`` ({relpath: bytes | (bytes, mode) | None=delete})
    through the real terminal commit seam, as a verified candidate would be."""
    candidate = Path(tempfile.mkdtemp())
    files = []
    for relpath, spec in sorted(changes.items()):
        target = os.path.join(workspace_path, relpath)
        if spec is None:
            files.append(CandidateFile(relpath, read_file_revision(target), delete=True))
            continue
        data, mode = spec if isinstance(spec, tuple) else (spec, 0o644)
        path = candidate / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, mode)
        files.append(CandidateFile(relpath, read_file_revision(target)))
    outcome = commit_terminal_candidate(
        materialize_candidate(str(candidate), str(workspace_path), files),
        workspace_path=str(workspace_path), transaction_id=uuid.uuid4().hex,
    )
    assert outcome.committed, outcome.failure_payload()


def _route():
    return EngineeringRoute(
        kind=ChangeKind.MILESTONE, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    )


class FakeEngine:
    """Model work replaced by fixed outputs; the commit is real."""

    def __init__(self, milestones, outputs, integration=None):
        self.order = [milestone.id for milestone in topological_order(milestones)]
        self.outputs = outputs
        self.integration = integration or {}
        self.calls = []
        self.run_verifier = MagicMock()
        self.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        self.engineering_triage = MagicMock()
        self.engineering_triage.classify = AsyncMock(return_value=_route())

    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **_):
        name = self.order[milestone_index - 1] if milestone_index <= len(self.order) else "INTEGRATION"
        self.calls.append(name)
        changes = self.integration if name == "INTEGRATION" else self.outputs.get(name, {})
        if changes:
            _apply(workspace_path, changes)
        return {
            "quality_gates_passed": True, "design": "d",
            "files": sorted(path for path, spec in changes.items() if spec is not None),
        }


def _run(workspace, milestones, engine, **kwargs):
    """One `generate --from-milestones` invocation: state reloaded from the
    plan + sidecar, a fresh run owning the workspace."""
    state = load_or_resume_milestone_run_state(str(workspace), _plan(milestones))
    result = asyncio.run(run_milestones(engine, state, str(workspace), **kwargs))
    return result, state


def _decisions(result):
    return {
        item["milestone_id"]: (item["status"], [reason["code"] for reason in item["reasons"]])
        for item in result["milestone_reuse"]["decisions"]
    }


CHAIN = [_milestone("M1"), _milestone("M2", ["M1"])]
CHAIN_OUTPUTS = {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2.py": b"M2 = 1\n"}}


def _completed_chain(tmp_path, outputs=CHAIN_OUTPUTS, milestones=CHAIN):
    workspace = _workspace(tmp_path)
    result, state = _run(workspace, milestones, FakeEngine(milestones, outputs))
    assert result["status"] == "success"
    assert state.completed_milestone_ids == [m.id for m in topological_order(milestones)]
    return workspace


@pytest.fixture
def git_workspace(tmp_path):
    workspace = _workspace(tmp_path)
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    return workspace


def _config():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    return cfg


def _engine(cfg, responses):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=responses)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    return engine, llm


# M1 commits normally; M2's two-file commit is killed with os._exit between
# its staged-file replaces (crash_at "stage2": m2a.py applied, m2b.py not) or
# before the first one ("stage1").
CRASH_MID_COMMIT_SCRIPT = r'''
import asyncio, os, sys
sys.path.insert(0, sys.argv[3])
from _milestone_proof_harness import CHAIN, FakeEngine, _plan
from kriya.workflow.milestones import load_or_resume_milestone_run_state, run_milestones

workspace, crash_at = sys.argv[1], sys.argv[2]
real_replace = os.replace
count = {"stage": 0}

def crashing_replace(src, dst):
    if os.path.basename(src).startswith(".kriya-stage-"):
        count["stage"] += 1
        if f"stage{count['stage']}" == crash_at:
            os._exit(9)
    return real_replace(src, dst)

class CrashingEngine(FakeEngine):
    async def run_generation_workflow(self, goal, workspace_path, milestone_index=None, **kwargs):
        if milestone_index == 2:
            os.replace = crashing_replace
        return await super().run_generation_workflow(goal, workspace_path, milestone_index, **kwargs)

engine = CrashingEngine(CHAIN, {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2a.py": b"A\n", "m2b.py": b"B\n"}})
state = load_or_resume_milestone_run_state(workspace, _plan(CHAIN))
asyncio.run(run_milestones(engine, state, workspace))
os._exit(0)
'''
MID_COMMIT_OUTPUTS = {"M1": {"m1.py": b"M1 = 1\n"}, "M2": {"m2a.py": b"A\n", "m2b.py": b"B\n"}}


def _crash_mid_commit(workspace, crash_at):
    """Run the chain in a subprocess that dies inside M2's commit."""
    crashed = subprocess.run(
        [sys.executable, "-c", CRASH_MID_COMMIT_SCRIPT, str(workspace), crash_at, TESTS_DIR],
        env=dict(os.environ, PYTHONPATH=TESTS_DIR), capture_output=True, text=True, timeout=120,
    )
    assert crashed.returncode == 9, crashed.stderr
