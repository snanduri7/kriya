"""PRD-008 S1: one commit-state gate for every mutating entry point, exact
per-operation commit evidence, and reference-safe retention.

Covers:
* begin_mutating_run takes the workspace lock, THEN assesses prior commit
  state, and only then creates the new run's record; an uncertain workspace
  is refused before any record, planning or model call, and the lock is
  released;
* every workflow entry point refuses the same way (structured result for
  WorkflowEngine/WorkflowController, UncertainWorkspaceStateError otherwise,
  "[Recovery Required]" at every CLI begin_mutating_run site);
* commit evidence schema 2 records kind + exact before/after byte state and
  the candidate hash that links it to its RunRecord cycle, and is durable
  before the first workspace byte (even a staged temp file) exists;
* retention prunes a run record and the evidence it references as a unit
  and never removes anything an unsettled cycle, a live/crashed run, or a
  resume checkpoint may still need.
"""
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

import kriya.control.run_coordinator as run_coordinator_module
import kriya.workflow.edit_safety as edit_safety_module
from kriya.cli import main as cli_main
from kriya.config.config import AppConfig
from kriya.control.commit_state import (
    RECOVERY_COMMAND,
    UncertainWorkspaceStateError,
    assess_workspace_commit_state,
)
from kriya.control.persistence import load_run_record, save_run_record, scan_run_records
from kriya.control.retention import prune_run_state
from kriya.control.run_coordinator import (
    begin_mutating_run,
    coordinated_mutation,
    current_run_context,
    transition_mutating_run,
)
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock
from kriya.control.run_record import RunLifecycle, RunRecord
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.edit_safety import (
    CommitState,
    StagedFileWrite,
    commit_revision_grounded_batch,
    content_revision,
    list_commit_evidence,
    load_commit_evidence,
    read_file_revision,
)
from kriya.workflow.milestones import run_milestones
from kriya.workflow.terminal_commit import (
    CandidateFile,
    commit_terminal_candidate,
    materialize_candidate,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow import WorkflowEngine
from kriya.workflow.workflow_controller import WorkflowController

# ---------------------------------------------------------------- helpers

def _save_chain(workspace, records):
    for index, record in enumerate(records):
        save_run_record(str(workspace), record, expected_revision=index or None)
    return records[-1]


def _unsettled_record(workspace, run_id="prior", transaction_id="tx-prior"):
    new = RunRecord.new(run_id, "workspace", None, None)
    running = new.transition(RunLifecycle.RUNNING)
    eligible = running.begin_commit(
        transaction_id, intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None,
    )
    return _save_chain(workspace, [new, running, eligible])


def _terminal_record(workspace, run_id, transaction_id=None, state=RunLifecycle.SUCCESS):
    chain = [RunRecord.new(run_id, "workspace", None, None)]
    chain.append(chain[-1].transition(RunLifecycle.RUNNING))
    if transaction_id is not None:
        chain.append(chain[-1].begin_commit(
            transaction_id, intent="APPLY_VERIFIED_CANDIDATE", candidate_hash=None,
        ))
        chain.append(chain[-1].settle_commit("COMMITTED"))
    chain.append(chain[-1].transition(state))
    return _save_chain(workspace, chain)


def _running_record(workspace, run_id):
    new = RunRecord.new(run_id, "workspace", None, None)
    return _save_chain(workspace, [new, new.transition(RunLifecycle.RUNNING)])


def _evidence_file(workspace, name, payload):
    directory = Path(workspace) / ".kriya" / "control" / "commits"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def _raw_evidence(transaction_id, state):
    return {
        "schema_version": 1, "transaction_id": transaction_id, "state": state,
        "started_at_unix": 1.0, "updated_at_unix": 1.0, "operations": [],
        "result_revisions": {}, "failure": None,
    }


def _commit(workspace, transaction_id, name="f.txt"):
    target = Path(workspace) / name
    if not target.exists():
        target.write_text("0")
    return commit_revision_grounded_batch([
        StagedFileWrite(str(target), target.read_text() + "+", str(target),
                        content_revision(target.read_text()), expected_base_exists=True),
    ], workspace_path=str(workspace), transaction_id=transaction_id)


def _evidence_ids(workspace):
    return sorted(
        os.path.basename(path)[:-len(".json")] for path, _, _ in list_commit_evidence(str(workspace))
    )


def _record_ids(workspace):
    return sorted(record.run_id for record in scan_run_records(str(workspace)).records)


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    )


# ---------------------------------------------------------------- the gate

def test_gate_refuses_before_creating_a_record_and_releases_the_lock(tmp_path):
    _unsettled_record(tmp_path)

    with pytest.raises(UncertainWorkspaceStateError) as refused:
        with begin_mutating_run(str(tmp_path)):
            pytest.fail("an uncertain workspace must never get a run context")

    assert refused.value.assessment.reason_codes == ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]
    assert refused.value.assessment.uncertain_run_ids == ("prior",)
    assert RECOVERY_COMMAND in str(refused.value)
    assert _record_ids(tmp_path) == ["prior"]  # no record for the refused run
    with acquire_run_lock(str(tmp_path)):
        pass  # lock released


@pytest.mark.parametrize("setup, expected_codes", [
    (lambda ws: _evidence_file(ws, "crashed", _raw_evidence("crashed", "in_progress")),
     ["UNCERTAIN_COMMIT_STATE"]),
    (lambda ws: _evidence_file(ws, "broken", _raw_evidence("broken", "uncertain")),
     ["UNCERTAIN_COMMIT_STATE"]),
    (lambda ws: _evidence_file(ws, "garbage", "{"), ["UNCERTAIN_COMMIT_STATE"]),
    (lambda ws: (Path(ws) / ".kriya/control/runs").mkdir(parents=True)
     or (Path(ws) / ".kriya/control/runs/half.json").write_text('{"schema_version": 2'),
     ["RUN_RECORD_UNREADABLE"]),
    (lambda ws: _terminal_record(ws, "gone-wrong", state=RunLifecycle.UNCERTAIN),
     ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]),
    (lambda ws: _unsettled_record(ws), ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]),
], ids=["in-progress-evidence", "uncertain-evidence", "unreadable-evidence",
        "unreadable-record", "uncertain-record", "unsettled-cycle"])
def test_every_unsafe_prior_state_refuses_a_new_run(tmp_path, setup, expected_codes):
    setup(tmp_path)
    with pytest.raises(UncertainWorkspaceStateError) as refused:
        with begin_mutating_run(str(tmp_path)):
            pytest.fail("refused")
    assert refused.value.assessment.reason_codes == expected_codes


def test_safe_prior_states_do_not_refuse(tmp_path):
    _terminal_record(tmp_path, "done", transaction_id="tx-done")
    _commit(tmp_path, "tx-done")
    _running_record(tmp_path, "crashed-before-commit")  # no cycle: provably no commit
    (Path(tmp_path) / ".kriya/control/runs/README.txt").write_text("not a record")
    assert assess_workspace_commit_state(str(tmp_path)).safe
    with begin_mutating_run(str(tmp_path)) as context:
        assert context.run_id not in ("done", "crashed-before-commit")


def test_lock_is_held_and_no_new_record_exists_while_prior_state_is_assessed(tmp_path):
    real_assess = run_coordinator_module.assess_workspace_commit_state
    seen = []

    def checking_assess(workspace_path, **kwargs):
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock(workspace_path):
                pass
        seen.append(_record_ids(workspace_path))
        return real_assess(workspace_path, **kwargs)

    with patch.object(run_coordinator_module, "assess_workspace_commit_state", checking_assess):
        with begin_mutating_run(str(tmp_path)) as context:
            run_id = context.run_id
    assert seen == [[]]
    assert run_id in _record_ids(tmp_path)


def test_workflow_engine_returns_structured_refusal_before_any_model_call(tmp_path):
    _unsettled_record(tmp_path)
    config = AppConfig()
    llm = LLMClient(config)
    llm.complete = AsyncMock()
    result = asyncio.run(WorkflowEngine(Kernel(config=config), llm).run_generation_workflow(
        goal="anything", workspace_path=str(tmp_path),
    ))
    assert result["status"] == "needs_review"
    assert result["quality_gates_passed"] is False
    assert result["reason_codes"] == ["UNCERTAIN_RUN_RECORD_COMMIT_STATE"]
    assert result["recovery_command"] == RECOVERY_COMMAND
    llm.complete.assert_not_awaited()
    assert _record_ids(tmp_path) == ["prior"]


@pytest.mark.parametrize("migration_mode", ["legacy", "shadow", "enforce"])
def test_controller_refuses_in_every_migration_mode_before_triage(tmp_path, migration_mode):
    _evidence_file(tmp_path, "crashed", _raw_evidence("crashed", "in_progress"))
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=_route())
    result = asyncio.run(WorkflowController(engine).execute(
        "goal", str(tmp_path), migration_mode=migration_mode,
    ))
    assert result.legacy_result["reason_codes"] == ["UNCERTAIN_COMMIT_STATE"]
    assert result.legacy_result["uncertain_commit_ids"] == ["crashed"]
    engine.engineering_triage.classify.assert_not_awaited()


def test_milestones_and_undecorated_owners_raise(tmp_path):
    _unsettled_record(tmp_path)
    with pytest.raises(UncertainWorkspaceStateError):
        asyncio.run(run_milestones(MagicMock(), MagicMock(), str(tmp_path)))

    @coordinated_mutation
    async def future_api(workspace_path):
        pytest.fail("must not run")

    with pytest.raises(UncertainWorkspaceStateError):
        asyncio.run(future_api(workspace_path=str(tmp_path)))


def _enclosing_handlers(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "begin_mutating_run":
            handled, cursor = set(), node
            while cursor in parents:
                cursor = parents[cursor]
                if isinstance(cursor, ast.Try):
                    for handler in cursor.handlers:
                        if isinstance(handler.type, ast.Name):
                            handled.add(handler.type.id)
            sites.append((node.lineno, handled))
    return sites


def test_every_cli_mutating_entry_reports_recovery_required():
    source = Path(__file__).resolve().parents[1] / "kriya" / "cli.py"
    sites = _enclosing_handlers(ast.parse(source.read_text()))
    assert len(sites) >= 5
    missing = [line for line, handled in sites if "UncertainWorkspaceStateError" not in handled]
    assert missing == [], f"begin_mutating_run call(s) at cli.py lines {missing} lack a refusal handler"


def test_cli_fix_prints_recovery_required(tmp_path):
    _unsettled_record(tmp_path)
    result = CliRunner().invoke(cli_main, ["fix", "--error", "boom", "--workspace", str(tmp_path), "-y"])
    assert result.exit_code == 1
    assert "[Recovery Required]" in result.output
    assert RECOVERY_COMMAND in result.output


# ---------------------------------------------------------------- evidence

def test_evidence_records_kind_and_exact_byte_state(tmp_path):
    created, modified, deleted = tmp_path / "created.txt", tmp_path / "modified.txt", tmp_path / "deleted.txt"
    modified.write_bytes(b"old\r\n")
    deleted.write_bytes(b"gone\n")
    os.chmod(modified, 0o754)
    crlf = b"new\r\nline\r\n"
    result = commit_revision_grounded_batch([
        StagedFileWrite(str(created), "fresh\n", str(created), content_revision(""),
                        expected_base_exists=False, mode=0o755),
        StagedFileWrite(str(modified), "new\nline\n", str(modified), read_file_revision(str(modified)),
                        expected_base_exists=True, content_bytes=crlf),
        StagedFileWrite(str(deleted), "", str(deleted), read_file_revision(str(deleted)),
                        expected_base_exists=True, delete=True),
    ], workspace_path=str(tmp_path), transaction_id="exact")
    persisted = load_commit_evidence(str(tmp_path / ".kriya/control/commits/exact.json"))
    assert persisted.schema_version == 2
    ops = {op["target_path"]: op for op in persisted.operations}
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731

    assert ops["created.txt"]["kind"] == "CREATE"
    assert ops["created.txt"]["before"] == {"exists": False, "sha256": None, "mode": None}
    assert ops["created.txt"]["after"] == {"exists": True, "sha256": sha(b"fresh\n"), "mode": 0o755}
    assert ops["modified.txt"]["kind"] == "MODIFY"
    assert ops["modified.txt"]["before"] == {"exists": True, "sha256": sha(b"old\r\n"), "mode": 0o754}
    # Byte-exact: the CRLF bytes, not the decoded text's revision.
    assert ops["modified.txt"]["after"] == {"exists": True, "sha256": sha(crlf), "mode": 0o754}
    assert ops["deleted.txt"]["kind"] == "DELETE"
    assert ops["deleted.txt"]["after"] == {"exists": False, "sha256": None, "mode": None}
    assert ops["deleted.txt"]["before"]["sha256"] == sha(b"gone\n")
    assert persisted.candidate_hash == result.evidence.candidate_hash is not None


def test_evidence_candidate_hash_is_the_run_record_cycle_candidate_hash(tmp_path):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    candidate.mkdir()
    (workspace / "app.py").write_text("v0\n")
    (candidate / "app.py").write_text("v1\n")
    with begin_mutating_run(str(workspace)) as context:
        transition_mutating_run(current_run_context(), RunLifecycle.RUNNING)
        writes = materialize_candidate(str(candidate), str(workspace), [
            CandidateFile("app.py", read_file_revision(str(workspace / "app.py"))),
        ])
        outcome = commit_terminal_candidate(writes, workspace_path=str(workspace), transaction_id="link")
        run_id = context.run_id
    assert outcome.committed
    record = load_run_record(str(workspace), run_id)
    evidence = load_commit_evidence(str(workspace / ".kriya/control/commits/link.json"))
    assert record.commits[-1]["candidate_hash"] == evidence.candidate_hash is not None


def test_schema_1_evidence_still_loads(tmp_path):
    path = _evidence_file(tmp_path, "old", _raw_evidence("old", "committed"))
    evidence = load_commit_evidence(str(path))
    assert evidence.schema_version == 1 and evidence.candidate_hash is None


def test_run_record_intent_and_in_progress_evidence_precede_the_first_workspace_byte(tmp_path):
    """Durability order: RunRecord intent -> IN_PROGRESS evidence (with exact
    byte state) -> staged temp files -> first replace. Staged files already
    live beside source files, so even they come after the evidence."""
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    candidate.mkdir()
    (workspace / "app.py").write_text("v0\n")
    (candidate / "app.py").write_text("v1\n")
    real_stage = edit_safety_module._stage_content
    observed = []

    def checking_stage(item, transaction_id, index):
        stage_dir = os.path.dirname(item.target_path)
        assert not [n for n in os.listdir(stage_dir) if n.startswith(".kriya-stage-")]
        evidence = load_commit_evidence(str(workspace / ".kriya/control/commits/ordered.json"))
        record = load_run_record(str(workspace), current_run_context().run_id)
        observed.append((
            evidence.state, evidence.operations[0]["before"]["exists"],
            record.lifecycle_state, record.commits[-1]["result"],
            (workspace / "app.py").read_text(),
        ))
        return real_stage(item, transaction_id, index)

    with begin_mutating_run(str(workspace)):
        transition_mutating_run(current_run_context(), RunLifecycle.RUNNING)
        writes = materialize_candidate(str(candidate), str(workspace), [
            CandidateFile("app.py", read_file_revision(str(workspace / "app.py"))),
        ])
        with patch.object(edit_safety_module, "_stage_content", checking_stage):
            outcome = commit_terminal_candidate(writes, workspace_path=str(workspace), transaction_id="ordered")
    assert outcome.committed
    assert observed == [(CommitState.IN_PROGRESS, True, RunLifecycle.COMMIT_ELIGIBLE, None, "v0\n")]


# ---------------------------------------------------------------- retention

def test_unsettled_cycle_evidence_survives_any_number_of_later_commits(tmp_path):
    """Pre-fix reproduction: each batch pruned terminal evidence beyond the
    newest 50 on its own, so 50 later commits deleted the COMMITTED evidence
    of an unsettled cycle and recovery would have read it as never started."""
    _commit(tmp_path, "tx-prior")  # committed, but its run died before settling
    _unsettled_record(tmp_path, transaction_id="tx-prior")
    for index in range(60):
        _commit(tmp_path, f"later-{index}")
    assert "tx-prior" in _evidence_ids(tmp_path)

    report = prune_run_state(str(tmp_path), keep_unreferenced_evidence=5)
    remaining = _evidence_ids(tmp_path)
    assert "tx-prior" in remaining
    assert len(remaining) == 6
    assert "prior" in report.protected_run_ids


def test_record_and_its_evidence_are_pruned_as_a_unit(tmp_path):
    for index in range(5):
        _commit(tmp_path, f"tx-{index}")
        _terminal_record(tmp_path, f"run-{index}", transaction_id=f"tx-{index}")

    report = prune_run_state(str(tmp_path), keep_terminal_runs=2)
    assert report.pruned_run_ids == ["run-0", "run-1", "run-2"]
    assert _record_ids(tmp_path) == ["run-3", "run-4"]
    assert _evidence_ids(tmp_path) == ["tx-3", "tx-4"]


def test_prune_protects_every_run_that_may_still_be_needed(tmp_path):
    _running_record(tmp_path, "live-or-crashed")
    _unsettled_record(tmp_path, run_id="unsettled", transaction_id="tx-unsettled")
    _terminal_record(tmp_path, "uncertain", state=RunLifecycle.UNCERTAIN)
    _terminal_record(tmp_path, "resumable")
    _terminal_record(tmp_path, "caller")
    _terminal_record(tmp_path, "plain")
    checkpoints = Path(tmp_path) / ".kriya" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "cp.json").write_text(json.dumps({
        "stage": "plan", "run_id": "cp", "_run_record": {"run_id": "resumable"},
    }))

    report = prune_run_state(str(tmp_path), keep_terminal_runs=0, protect_run_ids=("caller",))
    assert report.pruned_run_ids == ["plain"]
    assert _record_ids(tmp_path) == ["caller", "live-or-crashed", "resumable", "uncertain", "unsettled"]


def test_prune_does_nothing_while_any_record_is_unreadable(tmp_path):
    _commit(tmp_path, "tx-old")
    _terminal_record(tmp_path, "old", transaction_id="tx-old")
    (Path(tmp_path) / ".kriya/control/runs/torn.json").write_text("{")
    report = prune_run_state(str(tmp_path), keep_terminal_runs=0, keep_unreferenced_evidence=0)
    assert report.skipped_reason and "torn.json" in report.skipped_reason
    assert _record_ids(tmp_path) == ["old"]
    assert _evidence_ids(tmp_path) == ["tx-old"]


def test_prune_never_removes_uncertain_or_unreadable_evidence_and_dry_run_removes_nothing(tmp_path):
    # Commit first: the batch itself (correctly) refuses while uncertain or
    # unreadable evidence exists.
    _commit(tmp_path, "orphan")
    _evidence_file(tmp_path, "crashed", _raw_evidence("crashed", "in_progress"))
    _evidence_file(tmp_path, "broken", _raw_evidence("broken", "uncertain"))
    _evidence_file(tmp_path, "garbage", "{")

    dry = prune_run_state(str(tmp_path), keep_unreferenced_evidence=0, dry_run=True)
    assert dry.pruned_evidence_ids == ["orphan"]
    assert _evidence_ids(tmp_path) == ["broken", "crashed", "garbage", "orphan"]

    prune_run_state(str(tmp_path), keep_unreferenced_evidence=0)
    assert _evidence_ids(tmp_path) == ["broken", "crashed", "garbage"]


def test_retention_runs_under_the_lock_at_the_end_of_every_run(tmp_path):
    calls = []

    def spy(workspace_path, run_id):
        with pytest.raises(WorkspaceLockHeldError):
            with acquire_run_lock(workspace_path):
                pass
        calls.append((workspace_path, run_id, load_run_record(workspace_path, run_id).terminal))

    with patch("kriya.control.retention.prune_after_run", spy):
        with begin_mutating_run(str(tmp_path)) as context:
            run_id = context.run_id
    assert calls == [(os.path.realpath(str(tmp_path)), run_id, True)]
