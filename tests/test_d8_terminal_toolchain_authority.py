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
authority a changed declaration still fails closed.

FS-1C0: the build declaration is part of the named test's oracle trust
surface, so a candidate that changed it can no longer close a requirement
through a named test at all - the closure is refused before any validator
(and so any toolchain resolution) exists. The caller's authority still
reaches the validator on the path that remains: an unchanged declaration."""
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
    record_requirement_closure,
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
CASE = {"identity": "demo.AppTest.works", "classname": "demo.AppTest", "name": "works", "status": "passed"}


def _passing(runner):
    """A passing run with its COMPLETE structured report (FS-1A)."""
    return {"success": True, "output": "Tests run: 1, Failures: 0, Errors: 0",
            "test_execution": {"version": 1, "gate_id": "g", "runner": runner, "workspace": "w",
                               "completeness": "COMPLETE", "test_cases": [CASE], "report_files": []}}


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


def _git_trees(tmp_path, *, base_release, candidate_release):
    """A git repository (the run's base: a pom declaring ``base_release``,
    App and AppTest) whose working tree is the candidate, declaring
    ``candidate_release``; candidate root and workspace are the same tree."""
    import subprocess

    repo = tmp_path / "repo"
    (repo / "src/main/java/demo").mkdir(parents=True)
    (repo / "src/test/java/demo").mkdir(parents=True)
    (repo / "pom.xml").write_text(_pom(base_release))
    (repo / "src/main/java/demo/App.java").write_text("package demo; public class App {}")
    (repo / "src/test/java/demo/AppTest.java").write_text("package demo; public class AppTest {}")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                       capture_output=True)
    (repo / "pom.xml").write_text(_pom(candidate_release))
    (repo / "src/main/java/demo/App.java").write_text("package demo; public class App { int x; }")
    return str(repo), str(repo)


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

        def run_tests(validator, target_test=None):
            spy.runs.append(list(target_test or []))
            return _passing(validator._test_runner())

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


@pytest.mark.parametrize("resolved", ["violated", "already_closed"])
def test_a_named_test_whose_requirement_needs_no_closure_builds_no_validator(tmp_path, resolved):
    """Only an UNVERIFIED requirement is closed by its named tests; one made
    VIOLATED by deterministic counter-evidence (never closable) or one already
    closed by deterministic evidence needs nothing, so nothing is built. (A
    verifier's "satisfied" is a model claim, UNVERIFIED - FS-1B - so it no
    longer stands for "needs no closure"; GR-R0: neither is its "missing",
    which deterministic evidence may now close.)"""
    workspace, candidate = _trees(tmp_path, 17, named_test=True)
    reqs = derive_requirements(NAMED_TEST_GOAL)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    if resolved == "violated":
        record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.SATISFIED, "")
                                                   for r in reqs.requirements},
                                    revision=1, evidence_fingerprint="cand", source="test")
        for requirement in reqs.requirements:  # deterministic counter-evidence, e.g. a mutation out of scope
            record_requirement_closure(ledger, reqs, requirement.id, evidence_id="cand", method="mutation_scope",
                                       detail={}, source="test", revision=1, violated=True)
    else:
        record_requirement_verdicts(ledger, reqs, {r.id: (RequirementOutcome.SATISFIED, "")
                                                   for r in reqs.requirements},
                                    revision=1, evidence_fingerprint="cand", source="test")
        for requirement in reqs.requirements:  # each by its own kind of evidence (FS-1C1)
            named = "AppTest" in requirement.text
            record_requirement_closure(
                ledger, reqs, requirement.id, evidence_id="cand",
                method="named_test_oracle" if named else "acceptance_oracle",
                detail={"tests": ["src/test/java/demo/AppTest.java"]} if named else {}, source="test", revision=1)
    with _Spy() as spy:
        assert close_requirements_with_named_tests(
            CONTAINED, ledger, reqs, candidate, workspace, modified=["pom.xml"], revision="terminal",
            toolchain_declaration_mutable=False) == []
    assert spy.validators == []


# ---------------------------------------------------------------- 2-5: a changed declaration never reaches a validator

@pytest.mark.parametrize("authority", [True, False])
@pytest.mark.parametrize(("baseline", "candidate"), [(None, 17), (None, 11), (11, 21), (21, 8)])
def test_a_changed_toolchain_declaration_is_refused_before_any_validator(tmp_path, baseline, candidate, authority):
    """Greenfield (the live shape: the candidate wrote AppTest) or brownfield
    (pom.xml changed): never an oracle, with or without the plan's authority;
    no validator, no toolchain resolution, no false conflict, no run."""
    if baseline is None:
        workspace, root = _trees(tmp_path, candidate_release=candidate, named_test=True)
        expected = "ORACLE_BASE_UNAVAILABLE"
    else:
        workspace, root = _git_trees(tmp_path, base_release=baseline, candidate_release=candidate)
        expected = "ORACLE_DEPENDENCY_CHANGED"
    reqs, ledger = _ledger(NAMED_TEST_GOAL)
    with _Spy() as spy:
        [closure] = close_requirements_with_named_tests(
            CONTAINED, ledger, reqs, root, workspace, modified=["pom.xml"], revision="terminal",
            toolchain_declaration_mutable=authority)
    assert closure["closed"] is False and closure["reason_code"] == expected
    assert spy.validators == [] and spy.runs == []


@pytest.mark.parametrize("authority", [True, False])
def test_an_unchanged_declaration_runs_the_named_test_with_the_callers_authority(tmp_path, authority):
    """The candidate validator carries exactly the caller's authority (never
    re-decided here); the base export's validator changes nothing."""
    workspace, root = _git_trees(tmp_path, base_release=17, candidate_release=17)
    reqs, ledger = _ledger(NAMED_TEST_GOAL)
    with _Spy() as spy:
        [closure] = close_requirements_with_named_tests(
            CONTAINED, ledger, reqs, root, workspace, modified=["src/main/java/demo/App.java"],
            revision="terminal", toolchain_declaration_mutable=authority)
    assert closure["closed"] is True and closure["reason_code"] == "ORACLE_PASSED"
    assert spy.runs == [["src/test/java/demo/AppTest.java"]] * 2  # base export, then the candidate
    candidate_validator, base_validator = spy.validators
    assert candidate_validator.workspace_path == root
    assert candidate_validator.toolchain_declaration_mutable is authority
    assert base_validator.toolchain_declaration_mutable is False and base_validator.workspace_path != root


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


def test_a_changed_declaration_is_refused_at_the_terminal_without_a_false_conflict(tmp_path, monkeypatch):
    """With or without the plan's authority over pom.xml, the candidate's
    own AppTest (greenfield) is no oracle: refused before any validator."""
    for plan_files in (["pom.xml", "src/main/java/demo/App.java"], ["src/main/java/demo/App.java"]):
        (tmp_path / str(len(plan_files))).mkdir()
        report, spy = _terminal(tmp_path / str(len(plan_files)), monkeypatch, plan_files=plan_files,
                                goal=NAMED_TEST_GOAL, named_test=True)
        assert report.requirement_gap is None and spy.validators == [] and spy.runs == []
        [closure] = [c for c in report.requirement_closure_attempts if c.get("tests")]
        assert closure["closed"] is False and closure["reason_code"] == "ORACLE_BASE_UNAVAILABLE"
