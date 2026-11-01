"""Gate F (Wave 6), owner ruling 2026-11-01: a milestone must not mutate the authoritative real workspace when its
required artifact-registry derivation cannot be satisfied.

Before: ``milestones._complete_milestone`` derived the registry from the REAL workspace AFTER the milestone's source had
been committed, so a derivation failure returned ``artifact_registry_failed`` with the source bytes already changed (the
enforce path derives from the candidate BEFORE its commit and leaves the workspace untouched).

After (the contract these tests pin, through the REAL engine and the real controller, only the model scripted):

1. PRE-COMMIT: the milestone unit's candidate is derived by the canonical ``ArtifactRegistry.derive_from_workspace``
   before the terminal apply (``stage_apply.run_artifact_registry_gate``). A failure is a deterministic stop and the real
   workspace stays byte-identical.
2. NO PREMATURE AUTHORITY: the candidate derivation is validation only; nothing is persisted before the source commit.
3. REVISION BINDING: after the commit, settlement derives from the committed workspace, requires it to equal the validated
   candidate derivation, and binds every record to the committing transaction (``resolved_at_commit``).
4. COMMIT -> SETTLEMENT WINDOW: a failure after the commit is a typed, explicitly UNSETTLED result
   (``ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN``), never a clean ``artifact_registry_failed``; the milestone is not complete,
   its dependents do not run, and the existing S4c reconstruction completes it from the durable commit evidence on resume.
5. SINGLE AUTHORITY: the derivation is the one in ``kriya/control/artifacts.py``; no milestone-specific second one.
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from kriya.config.config import AppConfig
from kriya.control import artifacts as artifacts_module
from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry, registry_digest
from kriya.control.persistence import load_artifact_registry
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow import milestones as milestones_module
from kriya.workflow.milestones import (
    ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN,
    load_or_resume_milestone_run_state,
    settle_artifact_registry,
)
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

GROUP = "grp-gatef"
CALC = "def add(a, b):\n    return a + b\n"
WITH_MUL = CALC + "\n\ndef mul(a, b):\n    return a * b\n"
WITH_DIV = WITH_MUL + "\n\ndef div(a, b):\n    return a / b\n"
POISONED = WITH_MUL + "# POISON: makes the injected derivation raise\n"
PYPROJECT = '[project]\nname = "myapp"\nversion = "0.1.0"\n'
INTEGRATION = 3


def _plan() -> Dict[str, Any]:
    return {"group_id": GROUP, "original_goal": "Add mul and div to calc.py.", "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": []},
        {"id": "M2", "goal": "Add div(a, b) to calc.py.", "success_criterion": "div works", "depends_on": ["M1"]},
    ]}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "pyproject.toml").write_text(PYPROJECT)
    for argv in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"],
                 ["add", "calc.py", "pyproject.toml"], ["commit", "-qm", "seed"]):
        subprocess.run(["git", *argv], cwd=root, check=True)
    return root


@pytest.fixture
def injected_derivation_failure(monkeypatch):
    """The python deriver raises (an I/O-class failure - the real derivers are fail-soft for malformed manifests) whenever
    the tree it is asked about has a calc.py carrying the marker. Everything else is the real deriver."""
    real = artifacts_module.derive_python_artifact
    seen: List[str] = []

    def derive(workspace_path, milestone_id):
        seen.append(workspace_path)
        try:
            text = (Path(workspace_path) / "calc.py").read_text()
        except OSError:
            text = ""
        if "POISON" in text:
            raise OSError("injected derivation I/O failure")
        return real(workspace_path, milestone_id)

    monkeypatch.setattr(artifacts_module, "derive_python_artifact", derive)
    return seen


class Run:
    """One real engine with a scripted model (the tests/test_e11_milestones_real_engine.py approach)."""

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
        state = load_or_resume_milestone_run_state(str(workspace), _plan())
        result = asyncio.run(WorkflowController(self.engine).execute_milestones(state, str(workspace)))
        return result, state


def _registry(workspace: Path) -> ArtifactRegistry:
    return load_artifact_registry(str(workspace))


def _committed_transactions(workspace: Path) -> List[str]:
    commits = workspace / ".kriya" / "control" / "commits"
    return sorted(path.stem for path in commits.glob("*.json")) if commits.is_dir() else []


# --- 1 + 2: pre-commit derivation; nothing persisted before the commit -------------------------------------------------

def test_a_derivation_failure_on_the_candidate_prevents_the_commit_and_the_real_workspace_is_byte_identical(
        workspace, tmp_path, injected_derivation_failure, monkeypatch):
    saves: List[Any] = []
    real_save = milestones_module.save_artifact_registry
    monkeypatch.setattr(milestones_module, "save_artifact_registry", lambda *a, **k: (saves.append(a), real_save(*a, **k))[1])
    run = Run(tmp_path, {1: POISONED})
    result, state = run.execute(workspace)

    legacy = result.legacy_result
    assert legacy["status"] == "milestone_failed" and legacy["milestone_id"] == "M1"
    assert legacy.get("status") != "artifact_registry_failed"
    assert (workspace / "calc.py").read_text() == CALC                    # the real workspace never changed
    assert (workspace / "pyproject.toml").read_text() == PYPROJECT
    assert _committed_transactions(workspace) == []                       # no source commit was even started
    assert state.completed_milestone_ids == []
    assert _registry(workspace).all_records() == ()                       # no authoritative registry state
    assert saves == []                                                    # and nothing was published, candidate or otherwise
    assert run.units == [1]                                               # M2 and the integration unit never ran
    assert any(root != str(workspace) for root in injected_derivation_failure)   # the candidate was what got derived


def test_a_candidate_that_derives_but_is_not_committed_leaks_no_registry_state(workspace, tmp_path, monkeypatch):
    """Validation success is not authority: when the terminal commit then refuses, the registry stays empty."""
    from kriya.workflow import workflow as workflow_module
    from kriya.workflow.terminal_commit import COMMIT_NOT_COMMITTED, TerminalCommitOutcome

    saves: List[Any] = []
    real_save = milestones_module.save_artifact_registry
    monkeypatch.setattr(milestones_module, "save_artifact_registry", lambda *a, **k: (saves.append(a), real_save(*a, **k))[1])

    def refuse(writes, **kwargs):
        return TerminalCommitOutcome(
            committed=False, workspace_state="UNCHANGED", commit_result=COMMIT_NOT_COMMITTED,
            transaction_id=kwargs["transaction_id"], reason_code="WORKSPACE_COMMIT_FAILED", error=RuntimeError("refused"))

    monkeypatch.setattr(workflow_module, "commit_terminal_candidate", refuse)
    run = Run(tmp_path, {1: WITH_MUL})
    result, state = run.execute(workspace)
    assert result.legacy_result["status"] == "milestone_failed"
    assert (workspace / "calc.py").read_text() == CALC
    assert _registry(workspace).all_records() == () and saves == []
    assert state.completed_milestone_ids == []


# --- 3: revision binding on success -----------------------------------------------------------------------------------

def test_a_successful_milestone_commits_source_and_a_registry_bound_to_the_committing_transaction(workspace, tmp_path):
    run = Run(tmp_path, {1: WITH_MUL, 2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, state = run.execute(workspace)
    assert result.legacy_result["status"] == "success"
    assert (workspace / "calc.py").read_text() == WITH_DIV
    proofs = state.completion_proofs
    for milestone_id in ("M1", "M2"):
        transactions = proofs[milestone_id]["transaction_ids"]
        records = _registry(workspace).resolve_for_milestone(milestone_id)
        assert len(transactions) == 1 and len(records) == 1
        assert records[0].coordinates == {"name": "myapp", "version": "0.1.0"}
        assert records[0].resolved_at_commit == transactions[-1]          # bound to the source commit that produced it
    assert {t for p in proofs.values() for t in p["transaction_ids"]} <= set(_committed_transactions(workspace))


# --- 4: the commit -> settlement window; downstream; recovery ---------------------------------------------------------

def test_a_failure_after_the_commit_is_explicitly_unsettled_blocks_dependents_and_recovers_on_resume(
        workspace, tmp_path, monkeypatch):
    real_save = milestones_module.save_artifact_registry
    calls = {"n": 0}

    def flaky_save(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected registry publication failure after the commit")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(milestones_module, "save_artifact_registry", flaky_save)
    first = Run(tmp_path, {1: WITH_MUL, 2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, state = first.execute(workspace)
    legacy = result.legacy_result
    assert legacy["status"] == "artifact_registry_settlement_uncertain"
    assert legacy["reason_codes"] == [ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN]
    assert legacy["workspace_state"] == "COMMITTED_REGISTRY_UNSETTLED"
    assert legacy["quality_gates_passed"] is False
    assert (workspace / "calc.py").read_text() == WITH_MUL                # the source IS committed: said so, not hidden
    assert state.completed_milestone_ids == []                            # never a false completion
    assert _registry(workspace).resolve_for_milestone("M1") == ()         # no stale or partial authoritative registry
    assert first.units == [1]                                             # the dependent M2 did not consume it

    second = Run(tmp_path, {2: WITH_DIV, INTEGRATION: WITH_DIV})
    result, state = second.execute(workspace)
    assert result.legacy_result["status"] == "success"
    assert 1 not in second.units and 1 not in second.developer_units      # M1 was reconstructed from durable evidence
    [record] = _registry(workspace).resolve_for_milestone("M1")
    assert record.resolved_at_commit == state.completion_proofs["M1"]["transaction_ids"][-1]
    assert state.completion_proofs["M1"]["completion_origin"] != "run"    # settled by the S4c reconstruction


# --- the settlement function itself: binding and the candidate-equality check ----------------------------------------

def _python_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "settle"
    root.mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT)
    return root


def test_settlement_binds_every_record_to_the_committing_transaction_and_publishes_them(tmp_path):
    root = _python_workspace(tmp_path)
    registry = ArtifactRegistry()
    candidate = registry_digest(ArtifactRegistry().derive_from_workspace(str(root), "M1"))
    assert settle_artifact_registry(str(root), "M1", "tx-1", candidate, registry) is None
    [record] = load_artifact_registry(str(root)).resolve_for_milestone("M1")
    assert record.resolved_at_commit == "tx-1"
    assert isinstance(record, ArtifactRecord)


def test_settlement_refuses_a_committed_derivation_that_differs_from_the_validated_candidate(tmp_path):
    root = _python_workspace(tmp_path)
    registry = ArtifactRegistry()
    error = settle_artifact_registry(str(root), "M1", "tx-1", "0" * 64, registry)
    assert error is not None and error["code"] == ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN
    assert "different registry than the candidate" in error["detail"]
    assert load_artifact_registry(str(root)).all_records() == ()          # nothing published on a mismatch


def test_settlement_without_a_candidate_digest_still_derives_and_binds(tmp_path):
    """The reconstruction path (and a fake engine) has no candidate digest: the committed workspace is derived and bound."""
    root = _python_workspace(tmp_path)
    assert settle_artifact_registry(str(root), "M1", "tx-9", None, ArtifactRegistry()) is None
    assert load_artifact_registry(str(root)).resolve_for_milestone("M1")[0].resolved_at_commit == "tx-9"


def test_a_derivation_exception_at_settlement_is_the_typed_unsettled_error_and_publishes_nothing(
        tmp_path, injected_derivation_failure):
    root = _python_workspace(tmp_path)
    (root / "calc.py").write_text("# POISON\n")
    error = settle_artifact_registry(str(root), "M1", "tx-1", None, ArtifactRegistry())
    assert error["code"] == ARTIFACT_REGISTRY_SETTLEMENT_UNCERTAIN and "injected derivation" in error["detail"]
    assert not os.path.exists(root / ".kriya" / "control" / "artifacts.json")


def test_settlement_publishes_only_after_validation_and_replaces_a_prior_attempt_instead_of_duplicating(tmp_path):
    """A retried settlement (after a failed publication) is idempotent: one record per artifact, never two."""
    root = _python_workspace(tmp_path)
    registry = ArtifactRegistry()
    assert settle_artifact_registry(str(root), "M1", "tx-1", None, registry) is None
    assert settle_artifact_registry(str(root), "M1", "tx-1", None, registry) is None
    assert len(registry.resolve_for_milestone("M1")) == 1
    assert len(load_artifact_registry(str(root)).resolve_for_milestone("M1")) == 1


def test_the_registry_digest_is_stable_order_independent_and_ignores_the_binding_field():
    a = ArtifactRecord("M1", "python", "library", "", {"name": "a"}, "wheel")
    b = ArtifactRecord("M1", "python", "library", "m", {"name": "b"}, "wheel")
    bound = ArtifactRecord("M1", "python", "library", "", {"name": "a"}, "wheel", resolved_at_commit="tx")
    assert registry_digest([a, b]) == registry_digest([b, a])
    assert registry_digest([a]) == registry_digest([bound])
    assert registry_digest([a]) != registry_digest([b])
    assert registry_digest([]) == registry_digest(())


# --- single authority + the gate's scope -----------------------------------------------------------------------------

def test_the_gate_derives_only_a_milestone_units_candidate_and_through_the_canonical_registry(tmp_path):
    from kriya.workflow.execution_plan import PlanSourceKind
    from kriya.workflow.plan_executor import WorkUnitInvocation
    from kriya.workflow.stage_apply import milestone_registry_unit

    def invocation(kind, unit_id, requirement_goal=None):
        return WorkUnitInvocation(kind, "plan", unit_id, "fp", requirement_goal=requirement_goal)

    assert milestone_registry_unit(None) is None
    assert milestone_registry_unit(invocation(PlanSourceKind.DIRECT, "u")) is None
    assert milestone_registry_unit(invocation(PlanSourceKind.STRUCTURED, "s1")) is None        # enforce keeps its own gate
    assert milestone_registry_unit(invocation(PlanSourceKind.MILESTONE, "M1", "goal")) is None   # the integration unit
    assert milestone_registry_unit(invocation(PlanSourceKind.MILESTONE, "M1")) == "M1"


def test_there_is_no_second_artifact_derivation_in_the_workflow_package():
    root = Path(__file__).resolve().parent.parent / "kriya" / "workflow"
    offenders = [p.name for p in root.glob("*.py")
                 if "def derive_maven_artifact" in p.read_text() or "def derive_python_artifact" in p.read_text()]
    assert offenders == []
