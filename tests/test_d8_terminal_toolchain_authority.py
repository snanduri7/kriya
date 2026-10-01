"""D8 (KNOW B on demo-runtime-6, 2026-10-01, run 20261001T190528-d8bfbf8b):
terminal named-test closure verifies the already-authorized candidate; it
never re-decides a toolchain change.

Live: every gate passed and the application ran ([VERIFICATION] PASS), then
the terminal original-requirement step built a PolymorphicValidator for
named-test closure without the plan's toolchain authority; under contained
execution it compared the greenfield workspace (default Java 21) with the
candidate's authorized pom.xml (Java 17) and raised
TOOLCHAIN_REQUIREMENT_CONFLICT, so the run became REQUIREMENTS_UNRESOLVED -
although the candidate named no tests at all. Now the validator exists only
when a named test runs, and carries the run's own authority (the same
toolchain_declaration_mutable derivation its gates used); without that
authority a changed declaration still fails closed."""
import asyncio
from unittest.mock import patch

import pytest

import kriya.workflow.workflow as workflow_module
from kriya.config import AppConfig
from kriya.config.config import AutonomyConfig
from kriya.policy.filesystem import WriteScopeMode
from kriya.static_analysis.service import StaticAnalysisCandidate, StaticAnalysisService
from kriya.tools.toolchain_identity import ToolchainRequirementConflictError
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.requirements import (
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    seed_requirement_obligations,
)
from kriya.workflow.terminal_gate_service import TerminalGateRequest, TerminalGateService, TerminalGateValidators
from kriya.workflow.toolchain import toolchain_declaration_mutable
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow import close_requirements_with_named_tests

CONTAINED = AutonomyConfig(contained_execution_required=True, containment_backend="oci")
NO_TESTS_GOAL = "Create a Maven application targeting the requested Java version."
NAMED_TEST_GOAL = NO_TESTS_GOAL + "\n- AppTest keeps passing\n"
PASSING = {"success": True, "output": "Tests run: 1, Failures: 0, Errors: 0"}


def _pom(release):
    return ("<project><modelVersion>4.0.0</modelVersion><groupId>d</groupId><artifactId>d</artifactId>"
            f"<version>1</version><properties><maven.compiler.release>{release}</maven.compiler.release>"
            "</properties></project>")


def _trees(tmp_path, candidate_release, baseline_release=None, named_test=False):
    """A candidate declaring ``candidate_release`` and the workspace it is
    compared with (greenfield when ``baseline_release`` is None)."""
    workspace, candidate = tmp_path / "workspace", tmp_path / "candidate"
    workspace.mkdir()
    (candidate / "src/main/java/demo").mkdir(parents=True)
    (candidate / "pom.xml").write_text(_pom(candidate_release))
    (candidate / "src/main/java/demo/App.java").write_text("package demo; public class App {}")
    if baseline_release is not None:
        (workspace / "pom.xml").write_text(_pom(baseline_release))
    if named_test:
        (candidate / "src/test/java/demo").mkdir(parents=True)
        (candidate / "src/test/java/demo/AppTest.java").write_text("package demo; public class AppTest {}")
    return str(workspace), str(candidate)


def _ledger(goal):
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.UNVERIFIED, "") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return reqs, ledger


class _Spy:
    """Real PolymorphicValidator construction, counted; test execution stubbed."""

    def __init__(self):
        self.validators, self.runs = [], []

    def __enter__(self):
        real_init, spy = PolymorphicValidator.__init__, self

        def init(validator, *args, **kwargs):
            real_init(validator, *args, **kwargs)
            spy.validators.append(validator)

        def run_tests(_validator, target_test=None):
            spy.runs.append(list(target_test or []))
            return PASSING

        self._patches = [patch.object(PolymorphicValidator, "__init__", init),
                         patch.object(PolymorphicValidator, "run_tests", run_tests)]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self._patches:
            item.stop()


def _close(tmp_path, *, candidate_release, baseline_release=None, named_test, authority):
    workspace, candidate = _trees(tmp_path, candidate_release, baseline_release, named_test)
    reqs, ledger = _ledger(NAMED_TEST_GOAL if named_test else NO_TESTS_GOAL)
    return close_requirements_with_named_tests(
        CONTAINED, ledger, reqs, candidate, workspace, modified=["pom.xml"], revision="terminal",
        toolchain_declaration_mutable=authority)


# ---------------------------------------------------------------- 1, 6: no named tests -> no validator at all

@pytest.mark.parametrize("authority", [True, False])
def test_no_named_tests_builds_no_validator(tmp_path, authority):
    with _Spy() as spy:
        assert _close(tmp_path, candidate_release=17, named_test=False, authority=authority) == []
    assert spy.validators == [] and spy.runs == []


def test_a_named_test_whose_requirement_needs_no_closure_builds_no_validator(tmp_path):
    """Only an UNVERIFIED requirement is closed by its named tests; one the
    verifier already satisfied needs nothing, so nothing is built."""
    workspace, candidate = _trees(tmp_path, 17, named_test=True)
    reqs = derive_requirements(NAMED_TEST_GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.SATISFIED, "") for r in reqs.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    with _Spy() as spy:
        assert close_requirements_with_named_tests(
            CONTAINED, ledger, reqs, candidate, workspace, modified=["pom.xml"], revision="terminal",
            toolchain_declaration_mutable=False) == []
    assert spy.validators == []


# ---------------------------------------------------------------- 2, 5: named tests run under the authorized toolchain

@pytest.mark.parametrize(("baseline", "candidate"), [(None, 17), (None, 11), (11, 21), (21, 8)])
def test_named_tests_run_under_the_authorized_candidate_toolchain(tmp_path, baseline, candidate):
    with _Spy() as spy:
        [closure] = _close(tmp_path, candidate_release=candidate, baseline_release=baseline, named_test=True,
                           authority=True)
    assert closure["closed"] is True and spy.runs == [["src/test/java/demo/AppTest.java"]]
    [validator] = spy.validators
    identity = validator.toolchain_identity
    assert identity.runtime_version == str(candidate)
    assert identity.selection["basis"] in ("authorized_declaration_change", "repository_declaration")
    assert identity.selection["declaration_mutable"] is True


# ---------------------------------------------------------------- 3, 4: without authority the change is still refused

@pytest.mark.parametrize(("baseline", "candidate"), [(None, 17), (11, 21)])
def test_without_authority_a_changed_toolchain_still_fails_closed(tmp_path, baseline, candidate):
    with _Spy() as spy, pytest.raises(ToolchainRequirementConflictError):
        _close(tmp_path, candidate_release=candidate, baseline_release=baseline, named_test=True, authority=False)
    assert spy.runs == []


def test_the_earlier_unit_gate_still_refuses_an_unauthorized_toolchain_change(tmp_path):
    """The mutation boundary itself is unchanged: a unit whose scope and plan
    do not include pom.xml has no authority, and its validator refuses."""
    workspace, candidate = _trees(tmp_path, candidate_release=21, baseline_release=11)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="app", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="src/main/java/demo/App.java", action=FileAction.MODIFY)])])
    authority = toolchain_declaration_mutable(WriteScopeMode.ALLOWLIST, ["src/main/java/demo/App.java"], plan)
    assert authority is False
    with pytest.raises(ToolchainRequirementConflictError):
        PolymorphicValidator(candidate, original_workspace_path=workspace, autonomy_cfg=CONTAINED,
                             toolchain_declaration_mutable=authority)


# ---------------------------------------------------------------- the terminal gate passes the approved plan's authority

def _terminal(tmp_path, monkeypatch, *, plan_files, goal, named_test):
    workspace, candidate = _trees(tmp_path, candidate_release=17, named_test=named_test)
    monkeypatch.setattr(workflow_module, "close_requirements_by_mutation_scope", lambda *a, **k: [])
    autonomy = AppConfig().autonomy
    autonomy.spec_compliance_enabled = True
    autonomy.contained_execution_required = True
    autonomy.containment_backend = "oci"
    reqs, ledger = _ledger(goal)

    async def verify(*args, **kwargs):
        del args, kwargs
        return []

    request = TerminalGateRequest(
        plan=EngineeringPlan(plan_id="d8", kind=ChangeKind.TASK, subtasks=[Subtask(
            id="s1", description="build", execution_method=ExecutionMethod.MODEL,
            planned_files=[PlannedFile(path=path, action=FileAction.CREATE) for path in plan_files])]),
        goal=goal, candidate_root=candidate, workspace_path=workspace,
        migration_resolution=MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE),
        obligation_ledger=ledger, requirement_set=reqs, autonomy=autonomy, spec_compliance=None,
        milestone_id="m1", commit_batch=list,
        static_analysis_candidate=StaticAnalysisCandidate(materialize=list, workspace_path=workspace, run_id="r",
                                                          unit_id="m1"),
    )
    validators = TerminalGateValidators(
        find_migration_incomplete=lambda *a, **k: None, validate_stack_contract_artifacts=lambda *a, **k: None,
        enforce_preserved_reference_terminal_integrity=lambda ledger, root: None,
        blocking_requirements=lambda *a, **k: [], verify_original_requirements=verify,
        evaluate_static_analysis=StaticAnalysisService(AppConfig()).evaluate_candidate,
    )

    async def emit(*_args):
        return None

    with _Spy() as spy:
        report = asyncio.run(TerminalGateService(validators).run(request, emit))
    return report, spy


def test_the_live_shape_reaches_the_terminal_without_a_false_toolchain_conflict(tmp_path, monkeypatch):
    report, spy = _terminal(tmp_path, monkeypatch, plan_files=["pom.xml", "src/main/java/demo/App.java"],
                            goal=NO_TESTS_GOAL, named_test=False)
    assert report.requirement_gap is None and spy.validators == []


def test_the_terminal_runs_named_tests_with_the_plans_authority(tmp_path, monkeypatch):
    report, spy = _terminal(tmp_path, monkeypatch, plan_files=["pom.xml", "src/main/java/demo/App.java"],
                            goal=NAMED_TEST_GOAL, named_test=True)
    assert report.requirement_gap is None and spy.runs == [["src/test/java/demo/AppTest.java"]]
    assert [v.toolchain_declaration_mutable for v in spy.validators] == [True]


def test_a_plan_without_the_declaration_still_fails_closed_at_the_terminal(tmp_path, monkeypatch):
    report, spy = _terminal(tmp_path, monkeypatch, plan_files=["src/main/java/demo/App.java"],
                            goal=NAMED_TEST_GOAL, named_test=True)
    assert "TOOLCHAIN_REQUIREMENT_CONFLICT" in report.requirement_gap and spy.runs == []
