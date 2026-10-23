"""OI-29 (full modularization E11): the milestone path through the REAL engine.

The E1 milestone goldens (tests/state_machine/_kernel_golden.py ``milestone_*``) drive ``run_milestones`` with a
FakeEngine. These pins drive ``WorkflowController(real WorkflowEngine).execute_milestones`` with only the model scripted
(a role-keyed ``LLMClient.complete``, the tests/test_prd008a_plan_executor.py ``_role_llm`` approach): the real triage,
control state, legacy-ownership migration, milestone plan executor, per-milestone ``run_generation_workflow`` (Planner,
Architect, Developer, gates, commit, final review) and the integration unit. A pass-through spy on
``run_generation_workflow`` records which unit each call ran and tells the scripted Developer which unit it answers.

Pinned (characterization of today's outcomes, written before E11 moved ``_migrate_legacy_milestone_ownership`` out of
the controller): a two-milestone run that completes; a milestone that fails and stops the rest; a resume of a
half-completed run (PRD-008 S4c: the completed milestone is reused, never rerun); the legacy ownership migration on that
resume. Explicit fields only (no digest golden): statuses, unit states, the units each run executed, the Developer and
model call counts, the milestone states in the result and on disk, the reuse decision, the migration's recorded stores
and the ownership stamps it wrote. Every workspace, skills and memory path is under ``tmp_path`` (OI-45); no fallback
chain is configured, so no lesson is staged (the OI-53 calendar hazard); run ids and times are never pinned.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from kriya.config.config import AppConfig
from kriya.context.workspace_identity import WorkspaceOwnershipMissing
from kriya.control.decisions import load_decision_ledger
from kriya.control.ownership_audit import audit_control_ownership
from kriya.control.persistence import load_control_state
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.milestones import load_or_resume_milestone_run_state
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

GROUP = "grp-e11"
GOAL = "Add mul and div to calc.py."
CALC = "def add(a, b):\n    return a + b\n"
WITH_MUL = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
WITH_DIV = WITH_MUL + "\n\ndef div(a, b):\n    return a / b\n"
INTEGRATION = 3   # run_milestones' integration unit runs as milestone_index len(milestones) + 1


def _plan() -> Dict[str, Any]:
    return {"group_id": GROUP, "original_goal": GOAL, "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": []},
        {"id": "M2", "goal": "Add div(a, b) to calc.py.", "success_criterion": "div works", "depends_on": ["M1"]},
    ]}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"],
                 ["add", "calc.py"], ["commit", "-qm", "calc"]):
        subprocess.run(["git", *argv], cwd=root, check=True)
    return root


class Run:
    """One real engine with a scripted model. ``answers`` maps a milestone index (1, 2; 3 = the integration unit) to
    the Developer's content for calc.py; a missing index answers ``[]`` (no file: the unit fails its gates)."""

    def __init__(self, tmp_path: Path, answers: Dict[int, str]):
        cfg = AppConfig()
        cfg.autonomy.mode = "guardrails"
        cfg.autonomy.run_verification_enabled = False
        cfg.llm_chain = []
        cfg.paths.memory = str(tmp_path / "memory")
        cfg.paths.skills = str(tmp_path / "skills")
        self.answers = answers
        self.units: List[int] = []
        self.developer_units: List[int] = []
        self.llm = LLMClient(cfg)
        self.llm.complete = AsyncMock(side_effect=self._complete)
        self.engine = WorkflowEngine(Kernel(config=cfg), self.llm)
        self.engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        real = self.engine.run_generation_workflow

        async def spy(*args, **kwargs):
            self.units.append(kwargs.get("milestone_index"))
            return await real(*args, **kwargs)

        self.engine.run_generation_workflow = spy

    async def _complete(self, system_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "Developer Agent" in first:
            unit = self.units[-1]
            self.developer_units.append(unit)
            content = self.answers.get(unit)
            return "[]" if content is None else json.dumps([{"filepath": "calc.py", "content": content}])
        if "File List Planner" in first:
            return '["calc.py"]'
        if "Planner Agent" in first:
            return "Step 1: edit calc.py"
        if "Architect Agent" in first:
            return "Design: edit calc.py"
        return "Review: Approved"

    def execute(self, workspace: Path):
        """One ``kriya generate --from-milestones`` invocation through the controller: state reloaded from the plan and
        the sidecar, exactly as the CLI does."""
        state = load_or_resume_milestone_run_state(str(workspace), _plan())
        result = asyncio.run(WorkflowController(self.engine).execute_milestones(state, str(workspace)))
        return result, state


def _outcome(result, state, workspace: Path) -> Dict[str, Any]:
    legacy = result.legacy_result
    durable = load_control_state(str(workspace))
    return {
        "status": legacy.get("status"), "milestone_id": legacy.get("milestone_id"),
        "work_unit_states": {k: v["status"] for k, v in (legacy.get("work_unit_states") or {}).items()},
        "run_id": result.run_id, "completed": list(state.completed_milestone_ids),
        "milestone_states": dict(result.control_state.milestone_states),
        "current_milestone_id": result.control_state.current_milestone_id,
        "durable_group": durable.milestone_group_id, "durable_states": dict(durable.milestone_states),
        "calc": (workspace / "calc.py").read_text(),
    }


def _ownership_findings(workspace: Path) -> Any:
    """Positive proof that Kriya's own writers stamp every control document: the ownership audit finds nothing."""
    return audit_control_ownership(str(workspace))


def _make_legacy(workspace: Path) -> List[str]:
    """The control documents as a pre-ownership Kriya wrote them: the ``_workspace`` stamp removed."""
    stripped = []
    kriya = workspace / ".kriya"
    for path in (sorted((kriya / "control").glob("*.json")) + sorted((kriya / "milestones").glob("*.json"))
                 + sorted((kriya / "checkpoints").glob("*.json"))):
        payload = json.loads(path.read_text())
        if payload.pop("_workspace", None) is not None:
            path.write_text(json.dumps(payload))
            name = "<run id>.json" if path.parent.name == "checkpoints" else path.name   # a checkpoint is named by its run
            stripped.append(f"{path.parent.name}/{name}")
    return stripped


def _owner(path: Path) -> Any:
    return (json.loads(path.read_text()).get("_workspace") or {}).get("workspace_id")


def test_a_two_milestone_run_completes_through_the_real_engine(workspace, tmp_path):
    run = Run(tmp_path, {1: WITH_MUL, 2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, state = run.execute(workspace)
    assert _outcome(result, state, workspace) == {
        "status": "success", "milestone_id": None,
        "work_unit_states": {"M1": "VERIFIED", "M2": "VERIFIED", "__integration__": "VERIFIED"},
        "run_id": GROUP, "completed": ["M1", "M2"], "milestone_states": {"M1": "done", "M2": "done"},
        "current_milestone_id": "M2", "durable_group": GROUP, "durable_states": {"M1": "done", "M2": "done"},
        "calc": WITH_DIV,
    }
    assert run.units == [1, 2, INTEGRATION] and run.developer_units == [1, 2, INTEGRATION]
    assert run.llm.complete.await_count == 11
    assert result.legacy_result["milestone_reuse"]["decisions"] == []
    assert _ownership_findings(workspace) == ()   # nothing legacy to migrate on a fresh workspace


def test_a_failed_milestone_stops_the_rest(workspace, tmp_path):
    run = Run(tmp_path, {})   # M1's Developer never answers a file: M1 exhausts its gates
    result, state = run.execute(workspace)
    assert _outcome(result, state, workspace) == {
        "status": "milestone_failed", "milestone_id": "M1",
        "work_unit_states": {"M1": "FAILED", "M2": "BLOCKED", "__integration__": "BLOCKED"},
        "run_id": GROUP, "completed": [], "milestone_states": {"M1": "pending", "M2": "pending"},
        "current_milestone_id": "M1", "durable_group": GROUP, "durable_states": {"M1": "pending", "M2": "pending"},
        "calc": CALC,
    }
    assert run.units == [1]                      # M2 and the integration unit never run
    assert run.developer_units == [1] * 7        # M1's own retry budget, then abandon (no failure callback)
    assert run.llm.complete.await_count == 11
    assert _ownership_findings(workspace) == ()


def _half_completed(workspace: Path, tmp_path: Path) -> Run:
    first = Run(tmp_path, {1: WITH_MUL})   # M1 completes, M2 fails
    result, state = first.execute(workspace)
    assert _outcome(result, state, workspace) == {
        "status": "milestone_failed", "milestone_id": "M2",
        "work_unit_states": {"M1": "VERIFIED", "M2": "FAILED", "__integration__": "BLOCKED"},
        "run_id": GROUP, "completed": ["M1"], "milestone_states": {"M1": "done", "M2": "pending"},
        "current_milestone_id": "M2", "durable_group": GROUP, "durable_states": {"M1": "done", "M2": "pending"},
        "calc": WITH_MUL,
    }
    assert first.units == [1, 2] and first.developer_units == [1] + [2] * 7
    assert first.llm.complete.await_count == 15
    return first


def test_a_half_completed_run_resumes_reusing_the_completed_milestone(workspace, tmp_path):
    _half_completed(workspace, tmp_path)
    second = Run(tmp_path, {2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, state = second.execute(workspace)
    assert _outcome(result, state, workspace) == {
        "status": "success", "milestone_id": None,
        "work_unit_states": {"M1": "VERIFIED", "M2": "VERIFIED", "__integration__": "VERIFIED"},
        "run_id": GROUP, "completed": ["M1", "M2"], "milestone_states": {"M1": "done", "M2": "done"},
        "current_milestone_id": "M2", "durable_group": GROUP, "durable_states": {"M1": "done", "M2": "done"},
        "calc": WITH_DIV,
    }
    assert second.units == [2, INTEGRATION] and second.developer_units == [2, INTEGRATION]   # M1 is never rerun
    assert second.llm.complete.await_count == 7
    assert result.legacy_result["milestone_reuse"]["decisions"] == [
        {"milestone_id": "M1", "status": "MATCH", "action": "SKIP", "reasons": []}]
    assert _ownership_findings(workspace) == ()   # the first run's documents are owned: nothing to migrate


def _kriya_bytes(workspace: Path) -> Dict[str, bytes]:
    kriya = workspace / ".kriya"
    return {str(p.relative_to(kriya)): p.read_bytes() for sub in ("control", "milestones", "checkpoints") for p in sorted((kriya / sub).rglob("*.json"))}


def test_the_resume_of_legacy_ownerless_documents_is_refused_and_adopts_nothing(workspace, tmp_path):
    """D2 M4 (owner decision 2026-10-23): the one-time ownership migration is retired. The CLI shape (state reloaded from the plan and the
    sidecar) meets an ownerless sidecar first: typed refusal, no model work, every document byte-identical (nothing was stamped)."""
    _half_completed(workspace, tmp_path)
    assert sorted(_make_legacy(workspace)) == ["checkpoints/<run id>.json", "control/contracts.json",
                                               "control/state.json", f"milestones/{GROUP}.json"]
    before = _kriya_bytes(workspace)
    second = Run(tmp_path, {2: WITH_DIV, INTEGRATION: WITH_DIV})
    with pytest.raises(WorkspaceOwnershipMissing) as refused:
        second.execute(workspace)
    assert refused.value.reason_code == "WORKSPACE_OWNERSHIP_MISSING"
    assert second.units == []
    after = _kriya_bytes(workspace)
    assert {name: after[name] for name in before} == before  # the refused run's own RunRecord is new; nothing that existed was touched


def test_the_controller_refuses_ownerless_control_state_before_any_model_work_and_adopts_nothing(workspace, tmp_path):
    """The controller's own preflight (the sidecar is stamped, so the load passes): a typed needs_review naming every ownerless document, no model
    work, no document touched, no decision recorded."""
    _half_completed(workspace, tmp_path)
    sidecar = workspace / ".kriya" / "milestones" / f"{GROUP}.json"
    stamped_sidecar = sidecar.read_bytes()
    _make_legacy(workspace)
    sidecar.write_bytes(stamped_sidecar)
    before = _kriya_bytes(workspace)
    second = Run(tmp_path, {2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, _state = second.execute(workspace)
    legacy = result.legacy_result
    assert legacy["status"] == "needs_review" and legacy["quality_gates_passed"] is False
    assert legacy["reason_codes"] == ["WORKSPACE_OWNERSHIP_MISSING"]
    assert sorted(Path(f["path"]).name for f in legacy["ownership_findings"]) == sorted(
        ["contracts.json", "state.json", next((workspace / ".kriya" / "checkpoints").glob("*.json")).name])
    assert "outside the active Kriya control-state location" in legacy["persistence_error"]
    assert second.units == []
    after = _kriya_bytes(workspace)
    assert {name: after[name] for name in before} == before  # the refused run's own RunRecord is new; nothing that existed was touched
    assert not any(d.type == "legacy_workspace_ownership_migrated" for d in load_decision_ledger(str(workspace)).all())
