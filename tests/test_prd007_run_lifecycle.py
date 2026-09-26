"""PRD-007 (reopened): the RunRecord is the canonical, fail-closed lifecycle
truth of a run, driven by the REAL commit paths.

Covers: schema v2 + deterministic v1 migration; explicit commit cycles (one
run, several real-workspace commits); invariants that make a missed hook
fail loudly; strict loading (stray files ignored, unreadable records never
treated as absent); single-read compare-and-swap; the shared terminal-commit
seam through the real controller and the real generation workflow; and a
real process crash between durable intent and commit result.
"""
import asyncio
import json
import multiprocessing
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _strict_doubles import strict_config, strict_kernel

import kriya.control.persistence as persistence_module
import kriya.control.run_coordinator as run_coordinator_module
from kriya.control.persistence import (
    UnreadableRunRecordError,
    list_run_records,
    load_run_record,
    run_record_path,
    save_run_record,
    scan_run_records,
)
from kriya.control.run_coordinator import (
    authorize_candidate_workspace,
    begin_mutating_run,
    coordinated_mutation,
    current_run_context,
    mark_run_stage,
    transition_mutating_run,
)
from kriya.control.run_record import (
    STORE_CLASSIFICATION,
    IllegalRunTransitionError,
    RunLifecycle,
    RunRecord,
    UnsupportedRunRecordError,
)
from kriya.control.workspace_identity import ownership_metadata
from kriya.workflow.checkpoint import compute_config_fingerprint
from kriya.workflow.edit_safety import (
    CommitState,
    FileRevisionConflict,
    commit_state_for_transaction,
    read_file_revision,
)
from kriya.workflow.milestones import run_milestones
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.plan_validation import PlanValidationResult
from kriya.workflow.terminal_commit import (
    CandidateFile,
    CandidateMaterializationError,
    commit_terminal_candidate,
    materialize_candidate,
)
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.workflow_controller import WorkflowController

MP = multiprocessing.get_context("fork")


# ---------------------------------------------------------------- schema

def _v1_payload(**overrides):
    payload = {
        "schema_version": 1, "revision": 7, "run_id": "old-run", "workspace_identity": "w",
        "base_source_revision": None, "base_tree_hash": None, "lifecycle_state": "SUCCESS",
        "commit_intent": "APPLY_VERIFIED_CANDIDATE", "commit_result": "COMMITTED",
        "commit_transaction_id": "tx-old", "terminal_status": "SUCCESS",
        "store_revisions": {}, "candidate_revision": None,
    }
    payload.update(overrides)
    return payload


def test_v1_record_with_a_commit_migrates_to_one_explicit_cycle():
    record = RunRecord.from_dict(_v1_payload())
    assert record.schema_version == 3  # v1 -> v2 -> v3 (PRD-008 S4)
    assert record.commits == [{
        "transaction_id": "tx-old", "intent": "APPLY_VERIFIED_CANDIDATE",
        "candidate_hash": None, "result": "COMMITTED",
    }]
    assert RunRecord.from_dict(record.to_dict()) == record


def test_v1_fabricated_no_changes_cycle_migrates_to_no_commit():
    record = RunRecord.from_dict(_v1_payload(commit_result="NO_CHANGES"))
    assert record.commits == []
    assert record.commit_intent is None
    assert record.commit_result == "NO_COMMIT"


def test_v1_interrupted_intent_stays_unsettled_after_migration():
    record = RunRecord.from_dict(_v1_payload(lifecycle_state="COMMIT_ELIGIBLE", commit_result=None,
                                             terminal_status=None))
    assert record.commit_state_unknown


@pytest.mark.parametrize("payload", [
    {**_v1_payload(), "schema_version": 99},
    {**_v1_payload(), "schema_version": None},
    {**_v1_payload(), "surprise": 1},
    {**_v1_payload(), "lifecycle_state": "WHATEVER"},
    {**_v1_payload(), "revision": 0},
    [],
])
def test_unknown_or_malformed_schema_is_unsupported(payload):
    with pytest.raises(UnsupportedRunRecordError):
        RunRecord.from_dict(payload)


def test_every_persistent_store_is_classified():
    assert STORE_CLASSIFICATION["run_record"].startswith("authoritative")
    assert STORE_CLASSIFICATION["commit_evidence"].startswith("authoritative")
    for derived in ("control_state", "approved_plan", "checkpoint", "decision_ledger"):
        assert STORE_CLASSIFICATION[derived] == "derived"


# ---------------------------------------------------------------- invariants

def _running():
    return RunRecord.new("r", "w", None, None).transition(RunLifecycle.RUNNING)


def test_commit_states_are_reachable_only_through_begin_and_settle():
    record = _running()
    for state in (RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.COMMITTED):
        with pytest.raises(IllegalRunTransitionError, match="begin_commit"):
            record.transition(state)
    with pytest.raises(IllegalRunTransitionError, match="required"):
        record.begin_commit("", intent="APPLY", candidate_hash=None)
    with pytest.raises(IllegalRunTransitionError, match="no unsettled commit"):
        record.settle_commit("COMMITTED")


def test_an_unsettled_commit_cannot_be_downgraded_to_failure_or_success():
    eligible = _running().begin_commit("tx", intent="APPLY", candidate_hash="h")
    for state in (RunLifecycle.FAILURE, RunLifecycle.SUCCESS):
        with pytest.raises(IllegalRunTransitionError):
            eligible.transition(state)
    with pytest.raises(IllegalRunTransitionError, match="unsettled"):
        eligible.begin_commit("tx2", intent="APPLY", candidate_hash="h")
    uncertain = eligible.transition(RunLifecycle.UNCERTAIN)
    assert uncertain.commit_result == "UNCERTAIN"
    assert uncertain.commits[0]["result"] == "UNCERTAIN"


def test_success_requires_a_settled_committed_cycle_or_none_at_all():
    assert _running().transition(RunLifecycle.SUCCESS).commit_result == "NO_COMMIT"
    with pytest.raises(IllegalRunTransitionError, match="without a commit cycle"):
        _running().transition(RunLifecycle.SUCCESS, commit_result="COMMITTED")
    rolled_back = _running().begin_commit("tx", intent="A", candidate_hash=None).settle_commit("ROLLED_BACK")
    assert rolled_back.lifecycle_state == RunLifecycle.CANDIDATE
    with pytest.raises(IllegalRunTransitionError):
        rolled_back.transition(RunLifecycle.SUCCESS)
    assert rolled_back.transition(RunLifecycle.FAILURE).commit_result == "ROLLED_BACK"


def test_several_commit_cycles_summarize_truthfully():
    first = _running().begin_commit("m1", intent="A", candidate_hash="h1").settle_commit("COMMITTED")
    retried = (first.transition(RunLifecycle.CANDIDATE)
               .begin_commit("m2a", intent="A", candidate_hash="h2").settle_commit("ROLLED_BACK")
               .begin_commit("m2b", intent="A", candidate_hash="h2").settle_commit("COMMITTED"))
    assert retried.transition(RunLifecycle.SUCCESS).commit_result == "COMMITTED"
    partial = first.begin_commit("m2", intent="A", candidate_hash="h2").settle_commit("NOT_COMMITTED")
    assert partial.transition(RunLifecycle.FAILURE).commit_result == "PARTIALLY_COMMITTED"
    with pytest.raises(IllegalRunTransitionError, match="reused"):
        first.begin_commit("m1", intent="A", candidate_hash="h1")


def test_stage_marker_cannot_rewrite_the_current_cycle_result():
    rolled_back = _running().begin_commit("tx", intent="A", candidate_hash=None).settle_commit("ROLLED_BACK")
    with pytest.raises(IllegalRunTransitionError, match="stage marker"):
        rolled_back.transition(RunLifecycle.VERIFYING, commit_result="COMMITTED")


def test_annotate_cannot_touch_lifecycle_or_commit_state():
    for field_name in ("lifecycle_state", "commit_result", "commits", "commit_intent", "revision"):
        with pytest.raises(IllegalRunTransitionError):
            _running().annotate(**{field_name: "x"})
    with pytest.raises(IllegalRunTransitionError):
        _running().transition(RunLifecycle.CANDIDATE, commit_intent="sneaky")


# ---------------------------------------------------------------- persistence

def _runs_dir(workspace):
    path = Path(workspace) / ".kriya" / "control" / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_scan_ignores_stray_files_and_reports_unreadable_records(tmp_path):
    workspace = str(tmp_path)
    good = RunRecord.new("good-1", "w", None, None)
    save_run_record(workspace, good, expected_revision=None)
    runs = _runs_dir(workspace)
    (runs / "notes.txt").write_text("operator notes")
    (runs / "has space.json").write_text("{}")
    (runs / "good-1.json~").write_text("editor backup")
    (runs / "bad-1.json").write_text("{not json")
    (runs / "future-1.json").write_text(json.dumps({**_v1_payload(run_id="future-1"), "schema_version": 99}))
    foreign = {**RunRecord.new("other-1", "w", None, None).to_dict(),
               "_workspace": {**ownership_metadata(workspace), "workspace_id": "someone-else"}}
    (runs / "other-1.json").write_text(json.dumps(foreign))
    (runs / "renamed-1.json").write_text(json.dumps(RunRecord.new("elsewhere", "w", None, None).to_dict()))

    scan = scan_run_records(workspace)
    assert [record.run_id for record in scan.records] == ["good-1"]
    assert sorted(Path(item.path).name for item in scan.unreadable) == [
        "bad-1.json", "future-1.json", "other-1.json", "renamed-1.json",
    ]
    assert [record.run_id for record in list_run_records(workspace)] == ["good-1"]
    with pytest.raises(UnreadableRunRecordError):
        load_run_record(workspace, "bad-1")


def test_an_unreadable_record_is_never_overwritten_as_if_absent(tmp_path):
    workspace = str(tmp_path)
    path = Path(run_record_path(workspace, "run-1"))
    path.parent.mkdir(parents=True)
    path.write_text("{truncated")
    with pytest.raises(UnreadableRunRecordError):
        save_run_record(workspace, RunRecord.new("run-1", "w", None, None), expected_revision=None)
    assert path.read_text() == "{truncated"


def test_concurrent_writer_between_check_and_write_is_a_conflict_not_a_lost_update(tmp_path):
    workspace = str(tmp_path)
    initial = RunRecord.new("run-1", "w", None, None)
    save_run_record(workspace, initial, expected_revision=None)
    real_read = persistence_module._read_run_record
    newer = json.dumps({**initial.annotate(goal_hash="concurrent").to_dict(),
                        "_workspace": ownership_metadata(workspace)})

    def read_then_concurrent_write(workspace_path, run_id):
        observed = real_read(workspace_path, run_id)
        Path(run_record_path(workspace_path, run_id)).write_text(newer)
        return observed

    with patch.object(persistence_module, "_read_run_record", side_effect=read_then_concurrent_write):
        with pytest.raises(FileRevisionConflict):
            save_run_record(workspace, initial.annotate(goal_hash="stale"), expected_revision=1)
    assert load_run_record(workspace, "run-1").goal_hash == "concurrent"


# ---------------------------------------------------------------- the seam

def test_materialized_candidate_is_byte_and_mode_exact(tmp_path):
    candidate, workspace = tmp_path / "candidate", tmp_path / "workspace"
    candidate.mkdir()
    workspace.mkdir()
    (candidate / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    (candidate / "latin1.txt").write_bytes(b"caf\xe9\n")
    (candidate / "mvnw").write_bytes(b"#!/bin/sh\n")
    os.chmod(candidate / "mvnw", 0o755)
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile(name, read_file_revision(str(workspace / name)), expected_base_exists=False)
        for name in ("crlf.txt", "latin1.txt", "mvnw")
    ])
    outcome = commit_terminal_candidate(writes, workspace_path=str(workspace), transaction_id="exact")
    assert outcome.committed
    assert (workspace / "crlf.txt").read_bytes() == b"a\r\nb\r\n"
    assert (workspace / "latin1.txt").read_bytes() == b"caf\xe9\n"
    assert stat.S_IMODE(os.stat(workspace / "mvnw").st_mode) == 0o755
    with pytest.raises(CandidateMaterializationError):
        materialize_candidate(str(candidate), str(workspace), [CandidateFile("missing.txt", "x")])


def _candidate_commit(candidate_root, workspace, relpath, content, transaction_id):
    Path(candidate_root, relpath).write_text(content)
    writes = materialize_candidate(str(candidate_root), str(workspace), [
        CandidateFile(relpath, read_file_revision(os.path.join(workspace, relpath))),
    ])
    return commit_terminal_candidate(writes, workspace_path=str(workspace), transaction_id=transaction_id)


def test_nested_real_workspace_commits_are_each_recorded_candidate_commits_are_not(tmp_path):
    """The milestone shape: an outer run whose nested calls each commit to
    the run's own workspace, plus a commit into an authorized candidate."""
    workspace, candidate, sandbox = tmp_path / "ws", tmp_path / "cand", tmp_path / "sandbox"
    for path in (workspace, candidate, sandbox):
        path.mkdir()
    (workspace / "app.py").write_text("v0\n")
    (sandbox / "app.py").write_text("v0\n")

    @coordinated_mutation
    async def milestone(workspace_path, content, transaction_id):
        return _candidate_commit(candidate, workspace_path, "app.py", content, transaction_id)

    @coordinated_mutation
    async def sequence(workspace_path):
        authorize_candidate_workspace(str(sandbox))
        outcomes = [await milestone(workspace_path=workspace_path, content="v1\n", transaction_id="m1")]
        outcomes.append(await milestone(workspace_path=str(sandbox), content="sb\n", transaction_id="sb"))
        outcomes.append(await milestone(workspace_path=workspace_path, content="v2\n", transaction_id="m2"))
        assert all(outcome.committed for outcome in outcomes)
        # A later stage that changes nothing (e.g. the final integration
        # pass) moves the record off COMMITTED; the run still succeeded.
        mark_run_stage(workspace_path, RunLifecycle.CANDIDATE)
        assert current_run_context()._lease.record.lifecycle_state == RunLifecycle.CANDIDATE
        return {"status": "success", "quality_gates_passed": True}

    asyncio.run(sequence(workspace_path=str(workspace)))
    [record] = list_run_records(str(workspace))
    assert record.lifecycle_state == RunLifecycle.SUCCESS
    assert [(cycle["transaction_id"], cycle["result"]) for cycle in record.commits] == [
        ("m1", "COMMITTED"), ("m2", "COMMITTED"),
    ]
    assert record.commit_result == "COMMITTED"
    assert (workspace / "app.py").read_text() == "v2\n"
    assert run_milestones.__wrapped__  # the real milestone driver is coordinated


def test_later_commit_that_does_not_land_makes_the_run_partially_committed(tmp_path):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    candidate.mkdir()
    (workspace / "app.py").write_text("v0\n")

    @coordinated_mutation
    async def sequence(workspace_path):
        assert _candidate_commit(candidate, workspace_path, "app.py", "v1\n", "m1").committed
        writes = materialize_candidate(str(candidate), workspace_path, [
            CandidateFile("app.py", read_file_revision(os.path.join(workspace_path, "app.py"))),
        ])
        (workspace / "app.py").write_text("edited meanwhile\n")  # real concurrent edit
        outcome = commit_terminal_candidate(writes, workspace_path=workspace_path, transaction_id="m2")
        assert outcome.reason_code == "WORKSPACE_REVISION_CONFLICT"
        return {"status": "failed", "quality_gates_passed": False}

    asyncio.run(sequence(workspace_path=str(workspace)))
    [record] = list_run_records(str(workspace))
    assert record.lifecycle_state == RunLifecycle.FAILURE
    assert record.commit_result == "PARTIALLY_COMMITTED"
    assert (workspace / "app.py").read_text() == "edited meanwhile\n"


def test_commit_is_refused_when_intent_cannot_be_persisted(tmp_path):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    candidate.mkdir()
    (workspace / "app.py").write_text("v0\n")
    real_save = run_coordinator_module.save_run_record

    def refuse_intent(workspace_path, record, *, expected_revision):
        if record.lifecycle_state == RunLifecycle.COMMIT_ELIGIBLE:
            raise OSError("disk full")
        return real_save(workspace_path, record, expected_revision=expected_revision)

    with begin_mutating_run(str(workspace)) as context:
        transition_mutating_run(context, RunLifecycle.RUNNING)
        with patch.object(run_coordinator_module, "save_run_record", side_effect=refuse_intent):
            outcome = _candidate_commit(candidate, str(workspace), "app.py", "v1\n", "tx")
    assert outcome.reason_code == "RUN_RECORD_INTENT_NOT_PERSISTED"
    assert (workspace / "app.py").read_text() == "v0\n"
    assert commit_state_for_transaction(str(workspace), "tx") is CommitState.NOT_STARTED
    [record] = list_run_records(str(workspace))
    assert record.commits == [] and record.commit_result == "NOT_COMMITTED"


def test_entry_point_records_the_resume_config_fingerprint(tmp_path):
    config = strict_config(llm={"model": "m"}, autonomy={"mode": "guardrails"})

    class Engine:
        kernel = strict_kernel(config)

        @coordinated_mutation
        async def run(self, goal, workspace_path):
            return current_run_context()._lease.record

    running = asyncio.run(Engine().run("goal", workspace_path=str(tmp_path)))
    assert running.effective_config_fingerprint == compute_config_fingerprint(config.model_dump())
    # Same paths, so only the llm/autonomy overrides differ: they must reach the fingerprint.
    baseline = strict_config(paths=config.paths.model_dump())
    assert running.effective_config_fingerprint != compute_config_fingerprint(baseline.model_dump())


# ---------------------------------------------------------------- crash

def _crash_inside_commit(workspace, candidate):
    real_replace = os.replace

    def die_on_first_apply(source, target):
        if Path(source).name.startswith(".kriya-stage-"):
            os._exit(9)  # SIGKILL-equivalent: no Python cleanup runs
        return real_replace(source, target)

    with begin_mutating_run(workspace, run_id="crashed"):
        transition_mutating_run(current_run_context(), RunLifecycle.RUNNING)
        with patch("kriya.workflow.edit_safety.os.replace", side_effect=die_on_first_apply):
            _candidate_commit(candidate, workspace, "app.py", "v1\n", "crash-tx")


def _route():
    return EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(),
        initial_risk_class=RiskClass.LOW, current_risk_class=RiskClass.LOW,
        max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT,
    )


def _gate_engine():
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=_route())
    engine.planner.run = AsyncMock(side_effect=AssertionError("gate must stop before planning"))
    engine.kernel = None
    return engine


def test_crash_between_intent_and_result_is_recognized_and_blocks_enforce(tmp_path):
    workspace, candidate = tmp_path / "ws", tmp_path / "cand"
    workspace.mkdir()
    candidate.mkdir()
    (workspace / "app.py").write_text("v0\n")
    process = MP.Process(target=_crash_inside_commit, args=(str(workspace), str(candidate)))
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 9

    record = load_run_record(str(workspace), "crashed")
    assert record.lifecycle_state == RunLifecycle.COMMIT_ELIGIBLE  # last durable state
    assert record.commits == [{
        "transaction_id": "crash-tx", "intent": "APPLY_VERIFIED_CANDIDATE",
        "candidate_hash": record.candidate_hash, "result": None,
    }]
    assert record.commit_state_unknown
    assert commit_state_for_transaction(str(workspace), "crash-tx") is CommitState.IN_PROGRESS

    result = asyncio.run(WorkflowController(_gate_engine()).execute(
        "goal", str(workspace), migration_mode="enforce",
    ))
    assert "UNCERTAIN_RUN_RECORD_COMMIT_STATE" in result.legacy_result["reason_codes"]
    assert result.legacy_result["uncertain_run_ids"] == ["crashed"]


def test_unreadable_record_blocks_enforce_but_a_stray_file_does_not(tmp_path):
    runs = _runs_dir(str(tmp_path))
    (runs / "README.txt").write_text("not a record")
    # An unreadable commit-evidence file stops the run at the NEXT gate, so
    # the first run proves the run-record gate let the stray file through
    # without any planning happening.
    commits = tmp_path / ".kriya" / "control" / "commits"
    commits.mkdir(parents=True)
    (commits / "garbage.json").write_text("{")

    def run_once():
        return asyncio.run(WorkflowController(_gate_engine()).execute(
            "goal", str(tmp_path), migration_mode="enforce",
        ))

    assert run_once().legacy_result["reason_codes"] == ["UNCERTAIN_COMMIT_STATE"]
    (runs / "half-written.json").write_text('{"schema_version": 2, "revis')
    second = run_once().legacy_result
    # PRD-008: the one shared assessment reports every unsafe store at once.
    assert second["reason_codes"] == ["RUN_RECORD_UNREADABLE", "UNCERTAIN_COMMIT_STATE"]
    assert second["unreadable_run_records"][0]["path"].endswith("half-written.json")


# ---------------------------------------------------------------- real paths

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "app.py").write_text("original\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _record_states():
    """Spy on every durable RunRecord write, in order."""
    states = []
    real_save = run_coordinator_module.save_run_record

    def spy(workspace, record, *, expected_revision):
        real_save(workspace, record, expected_revision=expected_revision)
        if not states or states[-1] != record.lifecycle_state:
            states.append(record.lifecycle_state)

    return states, patch.object(run_coordinator_module, "save_run_record", side_effect=spy)


@pytest.mark.asyncio
async def test_enforce_controller_run_records_real_stages_and_evidence(tmp_path):
    repo = _repo(tmp_path)
    plan = EngineeringPlan(
        plan_id="prd007-enforce", kind=ChangeKind.TASK,
        subtasks=[Subtask(
            id="s1", description="update app", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path="app.py", action=FileAction.MODIFY)],
        )],
    )
    engine = MagicMock()
    engine.engineering_triage.classify = AsyncMock(return_value=_route())
    engine.planner.run = AsyncMock(return_value="fake plan text")
    engine.kernel = None

    @coordinated_mutation
    async def run_generation_workflow(goal=None, workspace_path=None, **kwargs):
        with open(f"{workspace_path}/app.py", "w") as handle:
            handle.write("verified candidate\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["app.py"]}

    engine.run_generation_workflow = run_generation_workflow
    states, spy = _record_states()
    with spy, patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
                    return_value=(MagicMock(), None)), \
            patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                  return_value=plan), \
            patch("kriya.workflow.workflow_controller.validate_plan",
                  new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await WorkflowController(engine).execute("goal", str(repo), migration_mode="enforce")

    assert result.legacy_result["status"] == "success", result.legacy_result
    assert states == [
        RunLifecycle.NEW, RunLifecycle.RUNNING, RunLifecycle.PLANNING, RunLifecycle.CANDIDATE,
        RunLifecycle.VERIFYING, RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.COMMITTED,
        RunLifecycle.SUCCESS,
    ]
    [record] = list_run_records(str(repo))
    [cycle] = record.commits
    assert cycle["transaction_id"].startswith(result.run_id + "-") and cycle["result"] == "COMMITTED"
    assert cycle["transaction_id"] == result.legacy_result["commit_evidence"]["transaction_id"]
    assert record.commit_result == "COMMITTED"
    assert record.approved_plan_hash == plan.content_hash()
    assert record.candidate_hash and cycle["candidate_hash"] == record.candidate_hash
    assert record.obligation_ledger_hash and record.obligation_ledger_revision is not None
    assert record.verification_evidence_ids == [f"commit:{cycle['transaction_id']}"]
    assert record.goal_hash
    # Runtime model digests belong to PRD-013/014; empty means unverified.
    assert record.model_runtime_fingerprint_ids == []
    assert commit_state_for_transaction(str(repo), cycle["transaction_id"]) is CommitState.COMMITTED


@pytest.mark.asyncio
async def test_generation_workflow_terminal_apply_goes_through_the_recorded_seam(tmp_path):
    """The default (non-controller) path: a REAL WorkflowEngine run, with
    only the model mocked, applies its verified sandbox through the shared
    seam - byte-exact, with durable intent and a settled cycle."""
    import kriya.workflow.workflow as workflow_module
    from kriya.config import AppConfig
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        "def add(a, b):\r\n    return a + b\r\n",
        "Review: Approved",
    ])
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    committed_batches = []
    real_commit = workflow_module.commit_terminal_candidate

    def spy_commit(writes, **kwargs):
        committed_batches.append([(w.target_path, w.content_bytes, w.mode) for w in writes])
        return real_commit(writes, **kwargs)

    states, spy = _record_states()
    with spy, patch.object(workflow_module, "commit_terminal_candidate", side_effect=spy_commit):
        result = await engine.run_generation_workflow(goal="Create math library", workspace_path=str(tmp_path))

    assert result["quality_gates_passed"] is True
    [batch] = committed_batches
    [(target, content_bytes, mode)] = batch
    assert content_bytes is not None and mode is not None
    assert Path(target).read_bytes() == content_bytes  # exact verified bytes reached disk
    [record] = list_run_records(str(tmp_path))
    assert record.lifecycle_state == RunLifecycle.SUCCESS
    assert states == [
        RunLifecycle.NEW, RunLifecycle.RUNNING, RunLifecycle.CANDIDATE,
        RunLifecycle.COMMIT_ELIGIBLE, RunLifecycle.COMMITTED, RunLifecycle.SUCCESS,
    ]
    [cycle] = record.commits
    assert cycle["result"] == "COMMITTED"
    assert commit_state_for_transaction(str(tmp_path), cycle["transaction_id"]) is CommitState.COMMITTED
    assert record.effective_config_fingerprint == compute_config_fingerprint(cfg.model_dump())
    assert set(record.retry_counters) == {"retry_count", "targeted_retry_count"}
