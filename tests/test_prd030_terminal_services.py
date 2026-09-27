"""PRD-030: the enforce run's terminal gates and terminal commit as two
services, exercised here without a WorkflowEngine.

The controller-level behaviour (the PRD-004 gate matrix, PRD-004/005 commit
failures) stays pinned by the unchanged characterization tests in
test_workflow_controller_enforce.py, test_prd004_commit_failure.py and
test_prd005_commit_transactions.py. These tests pin each service's own
contract: gate order and events, a gate that raises is a failure, the report
decides commit eligibility, the commit refuses anything else, and no gate
writes to the real workspace. The architecture tests pin one implementation
of each, with the dependency direction controller -> services.
"""
import ast
import asyncio
import os
from pathlib import Path

import pytest

import kriya.workflow.commit_service as commit_service
import kriya.workflow.workflow as workflow_module
from kriya.config import AppConfig
from kriya.control.artifacts import ArtifactRegistry
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.requirements import RequirementOutcome, derive_requirements
from kriya.workflow.terminal_gate_service import (
    TERMINAL_GATES_NOT_RUN,
    TerminalGateReport,
    TerminalGateRequest,
    TerminalGateService,
    TerminalGateValidators,
    enforce_preserved_reference_terminal_integrity,
)
from kriya.workflow.triage import ChangeKind

REPO = Path(__file__).resolve().parent.parent
GATES = ["migration", "stack_contract", "preserved_references", "terminal_obligations",
         "original_requirements", "artifact_registry"]


def _plan(*files):
    return EngineeringPlan(plan_id="prd030", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="change", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=path, action=action) for path, action in files],
    )])


def _validators(**overrides):
    async def verify(*args, **kwargs):
        del args, kwargs
        return []

    fields = {
        "find_migration_incomplete": lambda *args, **kwargs: None,
        "validate_stack_contract_artifacts": lambda *args, **kwargs: None,
        "enforce_preserved_reference_terminal_integrity": lambda ledger, root: None,
        "blocking_requirements": lambda *args, **kwargs: [],
        "verify_original_requirements": verify,
        **overrides,
    }
    return TerminalGateValidators(**fields)


def _request(tmp_path, *, autonomy=None, migration=None, ledger=None, goal="Update app.py."):
    candidate = tmp_path / "candidate"
    workspace = tmp_path / "workspace"
    for root in (candidate, workspace):
        root.mkdir(exist_ok=True)
        (root / "app.py").write_text("print('hi')\n")
    return TerminalGateRequest(
        plan=_plan(("app.py", FileAction.MODIFY)), goal=goal,
        candidate_root=str(candidate), workspace_path=str(workspace),
        migration_resolution=migration or MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE),
        obligation_ledger=ledger or ObligationLedger(), requirement_set=derive_requirements(goal),
        autonomy=autonomy, spec_compliance=None, milestone_id="m1",
    )


def _run(request, validators):
    events = []

    async def emit(gate, status, reason):
        events.append((gate, status, reason))

    report = asyncio.run(TerminalGateService(validators).run(request, emit))
    return report, events


def _snapshot(root):
    return {
        os.path.relpath(os.path.join(dirpath, name), root): Path(dirpath, name).read_bytes()
        for dirpath, _dirs, files in os.walk(root) for name in files
    }


def test_every_gate_passes_in_order_and_the_report_is_commit_eligible(tmp_path):
    request = _request(tmp_path)
    before = _snapshot(request.workspace_path)
    report, events = _run(request, _validators())
    assert events == [(gate, "passed", None) for gate in GATES]
    assert report.ran and report.commit_eligible
    assert all(value is None for _key, value in report.global_gaps())
    assert report.artifact_error is None and report.requirement_closure_attempts == ()
    assert _snapshot(request.workspace_path) == before  # the gates never write the workspace


def _violated_terminal_obligation(ledger):
    ledger.record(ObligationRecord(
        id="terminal.test", kind=ObligationKind.PLAN_STRUCTURAL_VALIDITY, status=ObligationStatus.VIOLATED,
        authority=ObligationAuthority.DETERMINISTIC, description="forced", source="test",
        terminal_required=True,
    ))


def _raise(message):
    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(message)
    return fail


@pytest.mark.parametrize(("gate", "field", "expected"), [
    ("migration", "migration_gap", "MIGRATION INCOMPLETE (global final-state check)"),
    ("stack_contract", "stack_contract_gap", "forced stack gap"),
    ("preserved_references", "preserved_reference_gap", "PRESERVED REFERENCE FINAL-STATE CHECK INDETERMINATE"),
    ("terminal_obligations", "terminal_obligation_gap", "TERMINAL OBLIGATIONS UNSATISFIED"),
    ("original_requirements", "requirement_gap", "REQUIREMENTS_UNRESOLVED: REQ-1 (violated)"),
    ("artifact_registry", "artifact_error", "derivation exploded"),
])
def test_one_failing_gate_fails_only_itself_and_blocks_the_commit(tmp_path, monkeypatch, gate, field, expected):
    ledger = ObligationLedger()
    migration = None
    overrides = {}
    if gate == "migration":
        migration = MigrationResolution(MigrationResolutionStatus.RESOLVED, obligation=object())
        overrides["find_migration_incomplete"] = lambda *args, **kwargs: {
            "source_identity": "old", "target_identity": "new", "reason_codes": ["SOURCE_USAGE_REMAINS"]}
    elif gate == "stack_contract":
        overrides["validate_stack_contract_artifacts"] = lambda *args, **kwargs: "forced stack gap"
    elif gate == "preserved_references":
        overrides["enforce_preserved_reference_terminal_integrity"] = _raise("exploded")
    elif gate == "terminal_obligations":
        _violated_terminal_obligation(ledger)
    elif gate == "original_requirements":
        overrides["blocking_requirements"] = lambda ledger, requirements, **kwargs: [
            (requirements.requirements[0], RequirementOutcome.VIOLATED)]
    else:
        monkeypatch.setattr(ArtifactRegistry, "derive_from_workspace", _raise("derivation exploded"))
    report, events = _run(_request(tmp_path, migration=migration, ledger=ledger), _validators(**overrides))

    assert [event[0] for event in events] == GATES
    assert [event[0] for event in events if event[1] == "failed"] == [gate]
    failed = next(event for event in events if event[0] == gate)
    assert expected in getattr(report, field) and failed[2] == getattr(report, field)
    assert report.ran and not report.commit_eligible


@pytest.mark.parametrize("validator", [
    "find_migration_incomplete", "validate_stack_contract_artifacts", "blocking_requirements",
])
def test_a_validator_that_raises_is_an_indeterminate_failure_never_a_pass(tmp_path, validator):
    migration = MigrationResolution(MigrationResolutionStatus.RESOLVED, obligation=object())
    report, events = _run(_request(tmp_path, migration=migration), _validators(**{validator: _raise("boom")}))
    assert not report.commit_eligible
    assert [status for _gate, status, _reason in events].count("failed") == 1
    assert any("RuntimeError: boom" in (reason or "") for _gate, _status, reason in events)


def test_an_indeterminate_migration_records_a_terminal_obligation(tmp_path):
    ledger = ObligationLedger()
    migration = MigrationResolution(MigrationResolutionStatus.INDETERMINATE, reason="two candidates")
    report, _events = _run(_request(tmp_path, migration=migration, ledger=ledger), _validators())
    assert report.migration_gap.startswith("MIGRATION OBLIGATION INDETERMINATE")
    record = ledger.current("migration.identity_resolution")
    assert record.status == ObligationStatus.INDETERMINATE and record.terminal_required
    # The same ledger is what the terminal aggregation gate reads next.
    assert "migration.identity_resolution" in report.terminal_obligation_gap


def test_the_verifier_path_closes_by_evidence_and_reports_findings(tmp_path, monkeypatch):
    """spec_compliance on: the verifier runs on the candidate's own files,
    both closure producers run, their attempts are reported, and the
    verifier's findings join a blocking requirement's message."""
    autonomy = AppConfig().autonomy
    autonomy.spec_compliance_enabled = True
    calls = {}

    async def verify(spec, requirements, goal, candidate_root, paths, ledger):
        del spec, requirements, goal, ledger
        calls["verify"] = (candidate_root, paths)
        return ["REQ-9 is not a requirement id"]

    def by_scope(ledger, requirements, candidate_root, workspace_path, **kwargs):
        calls["scope"] = (candidate_root, workspace_path, kwargs["revision"])
        return [{"producer": "mutation_scope"}]

    def by_tests(autonomy_cfg, ledger, requirements, candidate_root, workspace_path, **kwargs):
        calls["tests"] = (candidate_root, workspace_path, kwargs["modified"])
        return [{"producer": "named_tests"}]

    monkeypatch.setattr(workflow_module, "close_requirements_by_mutation_scope", by_scope)
    monkeypatch.setattr(workflow_module, "close_requirements_with_named_tests", by_tests)
    request = _request(tmp_path, autonomy=autonomy)
    report, _events = _run(request, _validators(
        verify_original_requirements=verify,
        blocking_requirements=lambda ledger, requirements, **kwargs: [
            (requirements.requirements[0], RequirementOutcome.UNKNOWN)],
    ))
    assert calls["verify"] == (request.candidate_root, ["app.py"])
    assert calls["scope"] == (request.candidate_root, request.workspace_path, "terminal")
    assert calls["tests"] == (request.candidate_root, request.workspace_path, ["app.py"])
    assert report.requirement_closure_attempts == ({"producer": "mutation_scope"}, {"producer": "named_tests"})
    assert report.requirement_gap.endswith("[verifier: REQ-9 is not a requirement id]")


def test_without_spec_compliance_no_verifier_or_closure_runs(tmp_path):
    def must_not_run(*args, **kwargs):
        raise AssertionError("verifier ran without spec_compliance_enabled")

    report, _events = _run(_request(tmp_path, autonomy=AppConfig().autonomy),
                           _validators(verify_original_requirements=must_not_run))
    assert report.commit_eligible and report.requirement_closure_attempts == ()


def test_a_preserved_reference_changed_in_the_candidate_fails_its_gate(tmp_path):
    """The real preservation check: a SATISFIED preserved reference whose
    candidate bytes differ from its baseline becomes VIOLATED and fails."""
    ledger = ObligationLedger()
    ledger.record(ObligationRecord(
        id="preserve.app", kind=ObligationKind.PRESERVED_REFERENCE, status=ObligationStatus.SATISFIED,
        authority=ObligationAuthority.DETERMINISTIC, description="app.py unchanged", source="test",
        evidence={"target": "app.py", "baseline_hash": content_revision("the original\n")},
    ))
    report, _events = _run(_request(tmp_path, ledger=ledger), _validators(
        enforce_preserved_reference_terminal_integrity=enforce_preserved_reference_terminal_integrity))
    assert report.preserved_reference_gap == "PRESERVED REFERENCES UNSATISFIED: preserve.app (violated)"
    assert not report.commit_eligible


def test_gates_that_never_ran_are_not_commit_eligible():
    assert not TERMINAL_GATES_NOT_RUN.ran and not TERMINAL_GATES_NOT_RUN.commit_eligible
    assert all(value is None for _key, value in TERMINAL_GATES_NOT_RUN.global_gaps())


# --- the commit ---------------------------------------------------------------------

def _commit_request(tmp_path, *, candidate=None, action=FileAction.MODIFY, run_id="run/1"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "app.py").write_text("original\n")
    if candidate is None:
        candidate = tmp_path / "candidate"
        candidate.mkdir(exist_ok=True)
        (candidate / "app.py").write_text("verified\n")
    transitions = []
    return commit_service.TerminalCommitRequest(
        plan=_plan(("app.py", action)), candidate_root=str(candidate), workspace_path=str(workspace),
        original_plan_revisions={"app.py": content_revision("original\n")}, approved_plan_hash="plan-hash",
        obligation_ledger=ObligationLedger(), run_id=run_id,
        contract_transition_for=lambda writes, transaction_id: transitions.append(
            ([w.target_path for w in writes], transaction_id)),
    ), transitions


ELIGIBLE = TerminalGateReport(ran=True)


def test_a_verified_candidate_is_committed_with_its_evidence(tmp_path):
    request, transitions = _commit_request(tmp_path)
    result = commit_service.commit_verified_candidate(ELIGIBLE, request)
    assert result.completed and result.failure is None
    assert (tmp_path / "workspace" / "app.py").read_text() == "verified\n"
    assert result.evidence["transaction_id"].startswith("run1-")
    # The PRD-029 builder got this commit's own writes and transaction id.
    ((paths, transaction_id),) = transitions
    assert [os.path.basename(p) for p in paths] == ["app.py"] and transaction_id == result.evidence["transaction_id"]


@pytest.mark.parametrize("report", [
    TerminalGateReport(ran=True, stack_contract_gap="gap"),
    TerminalGateReport(ran=True, artifact_error="error"),
    TERMINAL_GATES_NOT_RUN,
])
def test_a_candidate_that_did_not_pass_every_gate_is_never_committed(tmp_path, monkeypatch, report):
    def must_not_commit(*args, **kwargs):
        raise AssertionError("committed a candidate that failed a gate")

    monkeypatch.setattr(commit_service, "commit_terminal_candidate", must_not_commit)
    request, _transitions = _commit_request(tmp_path)
    with pytest.raises(commit_service.TerminalCommitNotEligibleError):
        commit_service.commit_verified_candidate(report, request)
    assert (tmp_path / "workspace" / "app.py").read_text() == "original\n"


def test_an_in_place_candidate_needs_no_transaction(tmp_path, monkeypatch):
    def must_not_commit(*args, **kwargs):
        raise AssertionError("an in-place candidate has nothing to transfer")

    monkeypatch.setattr(commit_service, "commit_terminal_candidate", must_not_commit)
    request, _transitions = _commit_request(tmp_path, candidate=tmp_path / "workspace")
    result = commit_service.commit_verified_candidate(ELIGIBLE, request)
    assert result == commit_service.TerminalCommitResult(completed=True)


def test_record_errors_after_a_landed_commit_are_reported_not_raised(tmp_path, monkeypatch):
    from kriya.workflow.terminal_commit import TerminalCommitOutcome

    errors = [{"operation": "settle_run_commit", "error": "OSError: disk full"}]
    monkeypatch.setattr(commit_service, "commit_terminal_candidate", lambda writes, **kwargs: TerminalCommitOutcome(
        committed=True, workspace_state="COMMITTED", commit_result="COMMITTED",
        transaction_id=kwargs["transaction_id"], record_errors=errors,
        contract_registry={"transition": "t1"}))
    request, _transitions = _commit_request(tmp_path)
    result = commit_service.commit_verified_candidate(ELIGIBLE, request)
    assert result.completed and result.record_errors == tuple(errors)
    assert result.contract_registry == {"transition": "t1"} and result.evidence is None


def test_a_missing_candidate_file_is_a_structured_unchanged_failure(tmp_path):
    empty = tmp_path / "candidate"
    empty.mkdir()
    request, transitions = _commit_request(tmp_path, candidate=empty)
    result = commit_service.commit_verified_candidate(ELIGIBLE, request)
    assert not result.completed and result.evidence is None and transitions == []
    assert result.failure["reason_code"] == "CANDIDATE_MATERIALIZATION_FAILED"
    assert result.failure["workspace_state"] == "UNCHANGED"
    assert (tmp_path / "workspace" / "app.py").read_text() == "original\n"


def test_a_concurrent_workspace_edit_is_a_structured_unchanged_failure(tmp_path):
    request, _transitions = _commit_request(tmp_path)
    (tmp_path / "workspace" / "app.py").write_text("edited by someone else\n")
    result = commit_service.commit_verified_candidate(ELIGIBLE, request)
    assert not result.completed and result.failure["workspace_state"] == "UNCHANGED"
    assert result.failure["commit_transaction_id"].startswith("run1-")
    assert (tmp_path / "workspace" / "app.py").read_text() == "edited by someone else\n"


# --- architecture ---------------------------------------------------------------------

def _imports(module_path):
    tree = ast.parse((REPO / module_path).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_the_services_never_import_the_controller():
    for module in ("kriya/workflow/terminal_gate_service.py", "kriya/workflow/commit_service.py"):
        assert "kriya.workflow.workflow_controller" not in _imports(module), module


def test_the_gate_service_has_no_commit_path():
    imports = _imports("kriya/workflow/terminal_gate_service.py")
    assert not imports & {"kriya.workflow.commit_service", "kriya.workflow.terminal_commit"}
    source = (REPO / "kriya/workflow/terminal_gate_service.py").read_text()
    assert "commit_revision_grounded" not in source and "materialize_candidate" not in source


def test_one_terminal_gate_and_one_commit_implementation():
    """The controller only calls the services: each gate message and the
    commit primitives live in exactly one module."""
    owners = {}
    for path in (REPO / "kriya").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for marker in ("MIGRATION INCOMPLETE (global final-state check)", "TERMINAL OBLIGATIONS UNSATISFIED",
                       "PRESERVED REFERENCES UNSATISFIED", "CANDIDATE_MATERIALIZATION_FAILED"):
            if marker in source:
                owners.setdefault(marker, set()).add(path.relative_to(REPO).as_posix())
    assert owners == {
        "MIGRATION INCOMPLETE (global final-state check)": {"kriya/workflow/terminal_gate_service.py"},
        "TERMINAL OBLIGATIONS UNSATISFIED": {"kriya/workflow/terminal_gate_service.py"},
        "PRESERVED REFERENCES UNSATISFIED": {"kriya/workflow/terminal_gate_service.py"},
        "CANDIDATE_MATERIALIZATION_FAILED": {"kriya/workflow/commit_service.py"},
    }
    controller = (REPO / "kriya/workflow/workflow_controller.py").read_text()
    for primitive in ("commit_terminal_candidate", "materialize_candidate", "CandidateFile("):
        assert primitive not in controller, primitive
    assert controller.count("commit_verified_candidate(") == 1
    assert controller.count("TerminalGateService(") == 1
