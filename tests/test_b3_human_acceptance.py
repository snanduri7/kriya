"""FS-1C2 B3: human-bound acceptance authority (kriya/workflow/acceptance_approval.py).

H1-H9 and the owner's required tests. Every acceptance run is real (Kriya's runners: pytest for Python, Maven/Surefire
for JVM). A GENERAL requirement closed this way is HUMAN_ACCEPTED - human authority over an exact approved suite,
never CLOSED_BY_EVIDENCE, never a proof.
"""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _b2a_fixtures import CALC, CALC_ACCEPTANCE, calc_project
from click.testing import CliRunner
from test_b2a_acceptance_oracle import _ledger, _run

from kriya.config.config import AppConfig
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow import acceptance_approval as b3
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.requirements import (
    BEHAVIOR,
    BEHAVIOR_EXAMPLES,
    REGRESSION_PRESERVATION,
    RequirementOutcome,
    blocking_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_claim,
    requirement_evidence,
    requirement_outcomes,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}
GENERAL_GOAL = "Add a double(x) function to calc/__init__.py that returns 2 * x for any number x.\n"
EXACT_GOAL = "Add a double(x) function to calc/__init__.py so that double(5) returns 10.\n"


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path, correct=True, extra=None):
    """A git workspace whose HEAD is the base; the candidate tree is the workspace itself."""
    root = calc_project(tmp_path / "ws", correct, extra)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


def _acceptance(tmp_path, goal=GENERAL_GOAL, source=CALC_ACCEPTANCE, name="acceptance.py"):
    path = tmp_path / name
    path.write_text(source)
    return ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / "state"))


def _approval_file(tmp_path, goal, acceptance, base, requirement_ids=("REQ-1",), approve=True, edit=None,
                   name="approval.json"):
    document = b3.approval_template(derive_requirements(goal), acceptance, list(requirement_ids), base)
    for entry in document["approvals"]:
        entry["accept_suite_as_sufficient"] = approve
    if edit:
        edit(document)
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def _approval(tmp_path, goal, acceptance, base, workspace, **kwargs):
    path = _approval_file(tmp_path, goal, acceptance, base, **kwargs)
    return b3.load_approval(str(path), derive_requirements(goal), acceptance, state_root=str(tmp_path / "state"),
                            workspace=str(workspace), base_revision=base)


def _close(ledger, reqs, acceptance, root, *, approval=None, base=None, test_files=()):
    return ao.close_requirements_with_acceptance(
        ledger, reqs, acceptance, test_files=list(test_files), execute=lambda a: _run(a, root, ()),
        source="test", revision=1, approval=approval, base_revision=base)


# ---------------------------------------------------------------- H1/H2/H3, 1-4, 19, 20

def test_h1_1_19_general_with_passing_cases_and_no_approval_stays_unverified(tmp_path):
    root, base = _repo(tmp_path)
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, _acceptance(tmp_path), root, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_h2_2_20_general_with_the_exact_approved_suite_passing_is_human_accepted(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert (attempt["reason_code"], attempt["outcome"], attempt["closed"]) == (
        b3.ACCEPTANCE_HUMAN_ACCEPTED, "human_accepted", True)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED  # never CLOSED_BY_EVIDENCE
    assert not blocking_requirements(ledger, reqs, **PRODUCTION)  # terminal closure permitted
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]
    assert evidence["method"] == evidence["closure_method"] == "human_bound_acceptance"
    assert evidence["approval_digest"] == approval.digest and evidence["acceptance_digest"] == acceptance.digest
    assert evidence["strength"] == "GENERAL" and evidence["base_revision"] == base
    assert requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]["approval"]["expected_cases"] == [
        "kriya_acceptance.test_double_doubles"]


def test_h3_3_4_a_failing_approved_case_is_violated_whatever_the_approval(tmp_path):
    root, base = _repo(tmp_path, correct=False)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED


# ---------------------------------------------------------------- binding: H4, H5, 5-8, 13

@pytest.mark.parametrize("edit, message", [
    (lambda d: d["approvals"][0].update(acceptance_sha256="0" * 64), "acceptance artifact differs"),        # H4, 5
    (lambda d: d["approvals"][0].update(requirement_text_sha256="0" * 64), "requirement text differs"),     # H5, 7
    (lambda d: d["approvals"][0].update(goal_sha256="0" * 64), "goal differs"),                             # 7
    (lambda d: d["approvals"][0].update(base_revision="0" * 40), "base revision differs"),                  # 8
    (lambda d: d["approvals"][0].update(runner_contract_sha256="0" * 64), "runner contract differs"),
    (lambda d: d["approvals"][0].update(expected_cases=[]), "not exactly the acceptance cases"),
    (lambda d: d["approvals"][0].update(expected_cases=["tests.test_new.test_x"]), "not exactly"),           # 11
    (lambda d: d["approvals"][0].update(requirement_id="REQ-*"), "never a pattern"),                         # 13
    (lambda d: d["approvals"][0].update(requirement_id="*"), "never a pattern"),                             # 13
    (lambda d: d["approvals"][0].update(requirement_id="REQ-7"), "not a requirement of this goal"),          # 6
    (lambda d: d["approvals"].append(dict(d["approvals"][0])), "duplicate approval"),                         # 13
    (lambda d: d["approvals"][0].update(all_requirements=True), "exactly these fields"),                     # 13
    (lambda d: d["approvals"][0].update(accept_suite_as_sufficient="yes"), "literal true"),
    (lambda d: d.update(approvals=[]), "at least one approval"),
])
def test_an_approval_binds_exactly_one_requirement_artifact_suite_and_base_or_is_refused(tmp_path, edit, message):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    with pytest.raises(ao.AcceptanceError) as refused:
        _approval(tmp_path, GENERAL_GOAL, acceptance, base, root, edit=edit)
    assert refused.value.reason_code == b3.ACCEPTANCE_APPROVAL_INVALID and message in refused.value.message


def test_an_unapproved_template_is_refused(tmp_path):
    root, base = _repo(tmp_path)
    with pytest.raises(ao.AcceptanceError):
        _approval(tmp_path, GENERAL_GOAL, _acceptance(tmp_path), base, root, approve=False)


def test_h4_5_an_approval_for_another_acceptance_artifact_closes_nothing(tmp_path):
    root, base = _repo(tmp_path)
    approved = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, approved, base, root)
    other = _acceptance(tmp_path, source=CALC_ACCEPTANCE + "\n# another suite\n", name="other.py")
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, other, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN and "acceptance artifact differs" in attempt["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_6_an_approval_for_req_a_never_closes_req_b(tmp_path):
    goal = GENERAL_GOAL + "- Add a triple(x) function to calc/__init__.py that returns 3 * x for any number x.\n"
    source = CALC_ACCEPTANCE + ('\n\n@pytest.mark.kriya_requirement("REQ-2")\ndef test_triple():\n'
                                "    from calc import triple\n    assert triple(2) == 6\n")
    root, base = _repo(tmp_path, extra={"calc/__init__.py": CALC[True] + "\n\ndef triple(x):\n    return 3 * x\n"})
    acceptance = _acceptance(tmp_path, goal, source)
    approval = _approval(tmp_path, goal, acceptance, base, root, requirement_ids=("REQ-1",))
    reqs, ledger = _ledger(goal)
    _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes == {"REQ-1": RequirementOutcome.HUMAN_ACCEPTED, "REQ-2": RequirementOutcome.UNVERIFIED}


def test_6_an_approval_binds_the_requirement_id_even_when_another_requirement_has_the_same_words(tmp_path):
    """REQ-1 and REQ-2 with identical text and one shared case: the REQ-1
    approval must never stand for REQ-2 (id lookup and id binding, two guards)."""
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs = derive_requirements(GENERAL_GOAL)
    twin = SimpleNamespace(id="REQ-2", text=reqs.requirements[0].text)
    shared = SimpleNamespace(digest=acceptance.digest, language="python",
                             identities_for=lambda rid: acceptance.identities_for("REQ-1"))
    assert b3.approval_problem(approval, reqs.requirements[0], reqs, shared, base) is None
    assert b3.approval_problem(approval, twin, reqs, shared, base) is not None


@pytest.mark.parametrize("change", ["goal", "base"])
def test_h5_7_8_a_changed_goal_or_base_revision_invalidates_a_bound_approval(tmp_path, change):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    goal = GENERAL_GOAL.replace("any number x", "every number x") if change == "goal" else GENERAL_GOAL
    other_acceptance = _acceptance(tmp_path, goal, name="a2.py") if change == "goal" else acceptance
    reqs, ledger = _ledger(goal)
    [attempt] = _close(ledger, reqs, other_acceptance, root, approval=approval,
                       base="f" * 40 if change == "base" else base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_h5_7_a_changed_goal_invalidates_the_approval_even_when_the_requirement_text_is_the_same(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    goal = GENERAL_GOAL + "- Keep the module importable.\n"  # REQ-1 text unchanged, goal different
    reqs, ledger = _ledger(goal)
    assert reqs.requirements[0].text == derive_requirements(GENERAL_GOAL).requirements[0].text
    moved = ao.load_acceptance(str(tmp_path / "acceptance.py"), reqs, str(tmp_path / "state"))
    [attempt] = _close(ledger, reqs, moved, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN and "goal differs" in attempt["reason"]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- H7, 9-12: who can create authority

def test_h7_9_the_approval_never_lives_where_a_candidate_can_write(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    inside = _approval_file(root, GENERAL_GOAL, acceptance, base)  # shipped by the repository
    with pytest.raises(ao.AcceptanceError) as refused:
        b3.load_approval(str(inside), derive_requirements(GENERAL_GOAL), acceptance,
                         state_root=str(tmp_path / "state"), workspace=str(root), base_revision=base)
    assert "outside the workspace" in refused.value.message
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    assert os.path.relpath(approval.stored_path, root).startswith("..")
    from kriya.policy.model import PolicyDecision

    assert AuthorizedFileWriter(str(root)).authorize(approval.stored_path).decision is not PolicyDecision.ALLOW


def test_10_only_the_cli_creates_approval_authority():
    """The one production caller of load_approval is the CLI, before any model call."""
    callers = []
    for base, _, files in os.walk("kriya"):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                if "load_approval(" in open(path, encoding="utf-8").read() and not path.endswith("acceptance_approval.py"):
                    callers.append(path)
    assert callers == [os.path.join("kriya", "cli.py")]


def test_10_a_model_satisfied_verdict_creates_no_approval(tmp_path):
    root, base = _repo(tmp_path)
    reqs, ledger = _ledger(GENERAL_GOAL, verdict=RequirementOutcome.SATISFIED)
    _close(ledger, reqs, _acceptance(tmp_path), root, base=base)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_11_candidate_tests_create_no_approval(tmp_path):
    proof = "from calc import double\n\n\ndef test_new():\n    assert double(5) == 10\n"
    root, base = _repo(tmp_path, extra={"tests/__init__.py": "", "tests/test_new.py": proof})
    reqs, ledger = _ledger(GENERAL_GOAL)
    assert _close(ledger, reqs, None, root, base=base, test_files=["tests/test_new.py"]) == []
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    with pytest.raises(ValueError):  # no producer but the acceptance closure may record the human method
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="named_test_oracle",
                                 detail={}, source="test", revision=1)


def test_12_c0_regression_evidence_creates_no_approval(tmp_path):
    from kriya.workflow.named_test_oracle import CLOSURE_METHOD, ORACLE_PASSED, OracleJudgment

    goal = GENERAL_GOAL.replace(".\n", ", keeping tests/test_calc.py passing.\n")
    root, base = _repo(tmp_path)
    reqs, ledger = _ledger(goal)
    _close(ledger, reqs, _acceptance(tmp_path, goal), root, base=base, test_files=["tests/test_calc.py"])
    close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_calc.py"], modified=[],
        judge=lambda named: OracleJudgment(ORACLE_PASSED, "", {"method": CLOSURE_METHOD, "tests": list(named)}),
        source="test", revision=1)
    claims = requirement_evidence(ledger, reqs)["REQ-1"]["claims"]
    assert claims[REGRESSION_PRESERVATION] and claims[BEHAVIOR] is None
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- 14, 15: integrity still fails closed

def test_14_a_missing_approved_identity_is_never_human_accepted(tmp_path):
    root, base = _repo(tmp_path)
    source = CALC_ACCEPTANCE + "\ndel test_double_doubles\n"
    acceptance = _acceptance(tmp_path, source=source)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL)
    [attempt] = _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_IDENTITY_NOT_EXECUTED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_15_a_stale_or_missing_report_is_never_human_accepted(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL)
    from kriya.tools.validate import PolymorphicValidator

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout",
                      return_value={"returncode": 0, "stdout": "", "stderr": ""}):
        [attempt] = _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- H6, H8, 16, 17: resume and withdrawal

def test_h6_16_the_approval_joins_the_resume_identity_the_candidate_depends_on(tmp_path):
    from kriya.workflow.resume_fingerprints import ARTIFACT_DEPENDENCIES, generation_resume_fingerprints

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")

    def fingerprints(**extra):
        return generation_resume_fingerprints(cfg, str(tmp_path), goal=GENERAL_GOAL, **extra)

    without, approved, other = fingerprints(), fingerprints(approval_digest="a" * 64), fingerprints(approval_digest="b" * 64)
    assert without == fingerprints(approval_digest=None)  # a run without one: byte-identical
    assert {name for name in without if without[name] != approved[name]} == {"goal"}
    assert approved["goal"] != other["goal"]
    assert "goal" in ARTIFACT_DEPENDENCIES["candidate"]  # an added/changed approval regenerates the candidate


def test_h8_16_a_resumed_human_record_for_other_words_closes_nothing(tmp_path):
    """A human record whose approval was for another requirement text (an
    older checkpoint, a changed goal) never closes, whatever it says."""
    reqs, ledger = _ledger(GENERAL_GOAL)
    stale = {"required_claims": [BEHAVIOR], "approval_digest": "d" * 64, "closure_method": "human_bound_acceptance",
             "approval": {"requirement_id": "REQ-1", "requirement_text_sha256": b3.text_sha256("other words")}}
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="human_bound_acceptance",
                             detail=stale, source="resume", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    stale["approval"]["requirement_text_sha256"] = b3.text_sha256(reqs.requirements[0].text)
    record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="human_bound_acceptance",
                             detail=stale, source="resume", revision=2)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED


def test_h8_a_whole_requirement_closure_by_the_human_method_closes_nothing():
    from kriya.workflow.requirements import record_requirement_closure

    reqs, ledger = _ledger(GENERAL_GOAL)
    record_requirement_closure(ledger, reqs, "REQ-1", evidence_id="cand", method="human_bound_acceptance",
                               detail={}, source="resume", revision=1)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_17_withdrawing_or_changing_the_approval_supersedes_human_acceptance(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    approval = _approval(tmp_path, GENERAL_GOAL, acceptance, base, root)
    reqs, ledger = _ledger(GENERAL_GOAL)
    _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED
    _close(ledger, reqs, acceptance, root, approval=None, base=base)  # withdrawn
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    [gone] = _close(ledger, reqs, None, root, approval=approval, base=base)  # suite withdrawn: supersedes too
    assert gone["reason_code"] == ao.ACCEPTANCE_SUPERSEDED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- H9, 18: exact requirements need no B3

@pytest.mark.parametrize("with_approval", [False, True])
def test_h9_18_an_exact_requirement_closes_through_b2_with_or_without_an_approval(tmp_path, with_approval):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path, EXACT_GOAL)
    approval = _approval(tmp_path, EXACT_GOAL, acceptance, base, root) if with_approval else None
    reqs, ledger = _ledger(EXACT_GOAL)
    [attempt] = _close(ledger, reqs, acceptance, root, approval=approval, base=base)
    assert attempt["reason_code"] == ao.ACCEPTANCE_PASSED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    assert requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]["method"] == "acceptance_oracle"


def test_supporting_examples_are_kept_beside_human_acceptance(tmp_path):
    root, base = _repo(tmp_path)
    acceptance = _acceptance(tmp_path)
    reqs, ledger = _ledger(GENERAL_GOAL)
    _close(ledger, reqs, acceptance, root, approval=_approval(tmp_path, GENERAL_GOAL, acceptance, base, root), base=base)
    assert requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]["method"] == "human_bound_acceptance"
    record = ledger.current("requirement.REQ-1.claim." + BEHAVIOR_EXAMPLES.lower())
    assert record.evidence["method"] == "acceptance_oracle" and record.evidence["reason_code"] == ao.ACCEPTANCE_PASSED


# ---------------------------------------------------------------- 21: Maven B2-c with B3

@pytest.mark.skipif(shutil.which("mvn") is None, reason="real Maven is not installed")
@pytest.mark.parametrize("variant, outcome", [("correct", RequirementOutcome.HUMAN_ACCEPTED),
                                              ("wrong", RequirementOutcome.VIOLATED)])
def test_21_maven_acceptance_with_a_human_approval(tmp_path, variant, outcome):
    from _b2c_fixtures import ACCEPTANCE, TARGET, base_revision, candidate, maven_workspace
    from _b2c_fixtures import GENERAL_GOAL as JAVA_GOAL
    from test_b2c_jvm_acceptance import _run as java_run

    ws = maven_workspace(tmp_path / "ws")
    base = base_revision(ws)
    cand = candidate(ws, tmp_path / "c", variant)
    (tmp_path / "KriyaAcceptanceTest.java").write_text(ACCEPTANCE)
    acceptance = ao.load_acceptance(str(tmp_path / "KriyaAcceptanceTest.java"), derive_requirements(JAVA_GOAL),
                                    str(tmp_path / "state"))
    approval = _approval(tmp_path, JAVA_GOAL, acceptance, base, ws)
    reqs, ledger = _ledger(JAVA_GOAL)
    ao.close_requirements_with_acceptance(ledger, reqs, acceptance, test_files=[],
                                          execute=lambda a: java_run(a, ws, cand, (TARGET,)), source="test",
                                          revision=1, approval=approval, base_revision=base)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is outcome


# ---------------------------------------------------------------- 22, 23, 24: the run paths

GREETING_GENERAL = "Create greeting.py with a greet(name) function that returns 'Hello, <name>'\n"
GREETING_ACCEPTANCE = ("import pytest\n\nfrom greeting import greet\n\n\n"
                       "@pytest.mark.kriya_requirement(\"REQ-1\")\n"
                       "def test_greets_by_name():\n    assert greet('Ann') == 'Hello, Ann'\n")


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.asyncio
async def test_22_direct_workflow(tmp_path, approved):
    from test_prd020_requirement_lineage import _engine, _gates_pass, _verdicts_json

    cfg, engine, _ = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                             requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("seed\n")
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "base")
    base = _git(workspace, "rev-parse", "HEAD")
    engine.acceptance = _acceptance(tmp_path, GREETING_GENERAL, GREETING_ACCEPTANCE)
    engine.acceptance_approval = (_approval(tmp_path, GREETING_GENERAL, engine.acceptance, base, workspace)
                                  if approved else None)
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=GREETING_GENERAL, workspace_path=str(workspace))
    if approved:
        assert res["requirements"]["outcomes"]["REQ-1"] == "human_accepted"
    else:
        # VERIFICATION-CONTRACT-003 (D3): a GENERAL statement with an acceptance file but no approval has no
        # authority that could ever close it, so it is refused before any model call (it used to spend a
        # Developer attempt and end UNVERIFIED at the terminal); the approval is the authority for it.
        assert res["failure_category"] == "verification_authority_required" and "requirements" not in res
    assert res["quality_gates_passed"] is approved
    assert (workspace / "greeting.py").exists() is approved


def _enforce(tmp_path, monkeypatch, *, correct, approved, prior_digest=None):
    from test_prd020_requirement_lineage import _verdicts_json

    from kriya.control.persistence import save_control_state
    from kriya.control.state import CURRENT_SCHEMA_VERSION, ControlState
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import (
        ChangeKind,
        EngineeringPlan,
        ExecutionMethod,
        FileAction,
        PlannedFile,
        Subtask,
    )
    from kriya.workflow.plan_validation import PlanValidationResult
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    workspace, base = _repo(tmp_path, correct=False, extra={"calc/__init__.py": "def double(x):\n    raise NotImplementedError\n"})
    monkeypatch.setattr(wc, "create_git_worktree", lambda path: path)
    plan = EngineeringPlan(plan_id="b3", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="double", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="calc/__init__.py", action=FileAction.MODIFY)], requirement_ids=["REQ-1"])])
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    we.acceptance = _acceptance(tmp_path)
    we.acceptance_approval = _approval(tmp_path, GENERAL_GOAL, we.acceptance, base, workspace) if approved else None
    if prior_digest is not None:
        save_control_state(str(workspace), ControlState(
            schema_version=CURRENT_SCHEMA_VERSION, run_id="earlier", subtask_states={"s1": "completed"},
            subtask_completion_scope="workspace", acceptance_approval_digest=prior_digest))

    async def check(**kwargs):
        prompt = "\n".join(f"{r.id}: {r.text}" for r in kwargs["requirements"].requirements)
        return json.loads(_verdicts_json(prompt))

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (workspace / "calc" / "__init__.py").write_text(CALC[correct])
        return {"status": "success", "quality_gates_passed": True, "files": ["calc/__init__.py"]}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="plan")
    return wc, we, workspace, plan, PlanValidationResult


@pytest.mark.parametrize("correct, approved, outcome", [
    (True, True, "human_accepted"), (True, False, "unverified"), (False, True, "violated")])
@pytest.mark.asyncio
async def test_23_enforce_terminal_workflow(tmp_path, monkeypatch, correct, approved, outcome):
    wc, we, workspace, plan, valid = _enforce(tmp_path, monkeypatch, correct=correct, approved=approved)
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=valid(valid=True))):
        result = await wc.WorkflowController(we).execute(GENERAL_GOAL, str(workspace), migration_mode="enforce")
    if not approved:
        # VERIFICATION-CONTRACT-003 (D3): without the approval the GENERAL statement has no closing authority -
        # refused before planning, no Planner call, nothing applied
        assert result.legacy_result["failure_category"] == "verification_authority_required"
        assert we.planner.run.await_count == 0 and (workspace / "calc" / "__init__.py").read_text().endswith("NotImplementedError\n")
        return
    assert result.legacy_result["requirements"]["outcomes"] == {"REQ-1": outcome}
    assert bool(result.legacy_result.get("global_requirement_gap")) is (outcome != "human_accepted")


@pytest.mark.asyncio
async def test_h6_24_enforce_resume_never_reuses_subtasks_recorded_under_another_approval(tmp_path, monkeypatch):
    wc, we, workspace, plan, valid = _enforce(tmp_path, monkeypatch, correct=True, approved=True,
                                              prior_digest="0" * 64)
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=valid(valid=True))):
        result = await wc.WorkflowController(we).execute(GENERAL_GOAL, str(workspace), migration_mode="enforce",
                                                         resume=True)
    decision = result.legacy_result["resume_decision"]
    assert decision["reason"] == "ACCEPTANCE_APPROVAL_CHANGED" and decision["reused_subtasks"] == []


def test_24_cli_binds_the_approval_before_the_workflow_and_refuses_misuse(tmp_path, monkeypatch):
    from _strict_doubles import strict_kernel

    root, base = _repo(tmp_path)
    monkeypatch.chdir(root)
    acceptance_path = tmp_path / "acceptance.py"
    acceptance_path.write_text(CALC_ACCEPTANCE)
    acceptance = ao.load_acceptance(str(acceptance_path), derive_requirements(GENERAL_GOAL), str(tmp_path / "s"))
    approval_path = _approval_file(tmp_path, GENERAL_GOAL, acceptance, base)
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")

    def invoke(args):
        dispatch = AsyncMock(return_value={"status": "success", "run_id": "r", "quality_gates_passed": True})
        with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True) as llm, \
             patch("kriya.cli.Kernel", return_value=strict_kernel(cfg)), patch("kriya.cli.WorkflowEngine") as engine, \
             patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
             patch("kriya.cli._dispatch_generation", new=dispatch):
            result = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main,
                                        ["generate", GENERAL_GOAL, "-y", *args])
        return result, engine.return_value, dispatch, llm

    result, engine, dispatch, _ = invoke(["--acceptance", str(acceptance_path), "--acceptance-approval", str(approval_path)])
    assert result.exit_code == 0, result.output
    assert isinstance(engine.acceptance_approval, b3.AcceptanceApproval) and dispatch.await_count == 1
    result, _, dispatch, llm = invoke(["--acceptance-approval", str(approval_path)])
    assert result.exit_code == 1 and dispatch.await_count == 0 and llm.call_count == 0
    inside = _approval_file(root, GENERAL_GOAL, acceptance, base, name="shipped.json")
    result, _, dispatch, llm = invoke(["--acceptance", str(acceptance_path), "--acceptance-approval", str(inside)])
    assert result.exit_code == 1 and "ACCEPTANCE_APPROVAL_INVALID" in result.output and dispatch.await_count == 0
