"""VERIFICATION-CONTRACT-003 slice 4: the baseline authority run and NO_MUTATION_REQUIRED (owner decision,
2026-10-08). Before the first model call every bound behaviour authority judges the untouched baseline: a baseline
that already satisfies every mandatory claim is a success without a model (a false premise never forces a mutation);
a remaining artifact claim ("add a test") proceeds to the model; a failing baseline is recorded as discriminating.

Real runs: the goal's compiled examples execute under the B2-a runner on a git workspace; both execution paths.
"""
import json
import sqlite3
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_prd020_requirement_lineage import _engine, _gates_pass, _verdicts_json

from kriya.config.config import AppConfig
from kriya.core.state_paths import trace_db_path
from kriya.workflow import contract_baseline as cb
from kriya.workflow.contract_compilation import ExternalAuthority, compile_verification_contract
from kriya.workflow.requirements import BEHAVIOR, derive_requirements, statement_origins

EXACT_GOAL = ("Examples:\n  calc.lower('ABC') -> 'abc'\n  calc.upper('abc') -> 'ABC'\n\n"
              "Every existing test must keep passing unchanged.\n")
WITH_TEST_GOAL = EXACT_GOAL + "\nAdd a regression test for it.\n"
CALC_OK = "def lower(s):\n    return s.lower()\n\n\ndef upper(s):\n    return s.upper()\n"
CALC_WRONG = "def lower(s):\n    return s.upper()\n\n\ndef upper(s):\n    return s.upper()\n"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _workspace(tmp_path, calc):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(calc)
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text("import calc\n\n\ndef test_upper():\n    assert calc.upper('a') == 'A'\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return root


def _events(cfg, kind):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events, status FROM runs ORDER BY rowid").fetchall()
    return [(e["details"], status) for (payload, status) in rows for e in json.loads(payload or "[]") if e["kind"] == kind]


# ---------------------------------------------------------------- the decision, from scripted authority judgments
def _contract(goal, authorities):
    reqs = derive_requirements(goal)
    return reqs, compile_verification_contract(reqs, origins=statement_origins(goal), test_files=["tests/test_calc.py"],
                                               external_authorities=authorities, project_language="python")


def test_01_no_mutation_required_only_when_every_mandatory_claim_holds_at_baseline():
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {"claim": BEHAVIOR, "accepted_strength": "EXACT"}})
    reqs, contract = _contract(EXACT_GOAL, [examples])
    assert contract.refusal() is None
    passing = SimpleNamespace(passed=True, violated=False)
    failing = SimpleNamespace(passed=False, violated=True)
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": passing},
                                         examples_digest="e" * 64)
    assert report.no_mutation_required is True and report.discriminating is False
    # "Examples:" followed by indented example lines is ONE statement (REQ-1, EXACT); REQ-2 is the suite statement
    assert report.claims["REQ-1"][BEHAVIOR]["state"] == "PASS"
    assert report.claims["REQ-2"]["REGRESSION_PRESERVATION"]["state"] == "IDENTITY"
    assert report.claims["REQ-2"]["TEST_IMMUTABILITY"]["state"] == "IDENTITY"
    assert report.outcomes() == {"REQ-1": "closed_by_evidence", "REQ-2": "closed_by_evidence"}
    # a failing baseline: discriminating, mutation ahead
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", judge_examples=lambda: {"REQ-1": failing},
                                         examples_digest="e" * 64)
    assert report.no_mutation_required is False and report.discriminating is True and report.unsatisfied == {"REQ-1": [BEHAVIOR]}
    # an indeterminate judgment is never a pass
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc",
                                         judge_examples=lambda: {"REQ-1": SimpleNamespace(passed=False, violated=False)},
                                         examples_digest="e" * 64)
    assert report.no_mutation_required is False and report.discriminating is False
    # an authority that never ran leaves the claim UNBOUND: never a pass
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc")
    assert report.no_mutation_required is False and report.claims["REQ-1"][BEHAVIOR]["state"] == "UNBOUND"


def test_02_an_artifact_claim_requires_a_mutation_even_when_behaviour_already_holds():
    examples = ExternalAuthority("goal_examples", "e" * 64, {"REQ-1": {"claim": BEHAVIOR, "accepted_strength": "EXACT"}})
    reqs, contract = _contract(WITH_TEST_GOAL, [examples])
    assert contract.refusal() is None
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc",
                                         judge_examples=lambda: {"REQ-1": SimpleNamespace(passed=True, violated=False)},
                                         examples_digest="e" * 64)
    assert report.no_mutation_required is False and report.mutation_required == {"REQ-3": ["TEST_ADDITION"]}
    assert report.claims["REQ-1"][BEHAVIOR]["state"] == "PASS"


def test_03_an_external_authority_pass_at_baseline_is_operator_sufficiency():
    bundle = ExternalAuthority("external_acceptance_command", "b" * 64, {"REQ-1": {"claim": BEHAVIOR, "accepted_strength": "GENERAL"}})
    reqs, contract = _contract(EXACT_GOAL, [bundle])
    run = SimpleNamespace(verdict="PASS", evidence=lambda: {"verdict": "PASS"})
    report = cb.run_baseline_authorities(contract, reqs, base_revision="abc", run_bundle=lambda: run, bundle_digest="b" * 64)
    assert report.no_mutation_required is True and report.outcomes()["REQ-1"] == "human_accepted"
    assert report.authorities_run[0]["kind"] == "external_acceptance_command"
    decision = cb.NoMutationRequired(contract, report)
    result = decision.result()
    assert result["status"] == "success" and result["no_mutation_required"] is True and result["files"] == []
    assert result["reason_codes"] == [cb.NO_MUTATION_REQUIRED]


# ---------------------------------------------------------------- the direct path, real example runs
@pytest.mark.asyncio
async def test_04_direct_path_succeeds_without_a_model_when_the_baseline_already_satisfies_the_goal(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _workspace(tmp_path, CALC_OK)
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=EXACT_GOAL, workspace_path=str(workspace))
    assert res["status"] == "success" and res["no_mutation_required"] is True and res["quality_gates_passed"] is True
    assert res["files"] == [] and engine.developer.run_generation.await_count == 0 and calls["planner"] == []
    assert res["requirements"]["outcomes"] == {"REQ-1": "closed_by_evidence", "REQ-2": "closed_by_evidence"}
    assert res["baseline_authority"]["no_mutation_required"] is True
    assert (workspace / "calc.py").read_text() == CALC_OK
    staging = workspace / ".kriya" / "acceptance-runs"  # the B2-a control path; every run's stage is removed
    assert not staging.exists() or list(staging.iterdir()) == []
    assert _git(workspace, "status", "--porcelain", "--", "calc.py", "tests") == ""
    [(details, status)] = _events(cfg, "verification_contract.no_mutation_required")
    assert status == "success" and details["no_mutation_required"] is True
    assert _events(cfg, "verification_contract.sealed") and _events(cfg, "verification_contract.baseline")


@pytest.mark.asyncio
async def test_05_direct_path_proceeds_when_the_baseline_fails_and_records_it_as_discriminating(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _workspace(tmp_path, CALC_WRONG)
    engine.developer.run_generation = AsyncMock(return_value=[{"filepath": "calc.py", "content": CALC_OK}])
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=EXACT_GOAL, workspace_path=str(workspace))
    assert not res.get("no_mutation_required") and engine.developer.run_generation.await_count >= 1
    [(details, _status)] = _events(cfg, "verification_contract.baseline")
    assert details["discriminating"] is True and details["no_mutation_required"] is False
    assert details["claims"]["REQ-1"]["BEHAVIOR"]["state"] == "FAIL"


@pytest.mark.asyncio
async def test_06_direct_path_still_runs_the_model_when_an_artifact_claim_remains(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    workspace = _workspace(tmp_path, CALC_OK)
    engine.developer.run_generation = AsyncMock(return_value=[{"filepath": "tests/test_new.py",
                                                               "content": "import calc\n\n\ndef test_lower():\n    assert calc.lower('A') == 'a'\n"}])
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=WITH_TEST_GOAL, workspace_path=str(workspace))
    assert not res.get("no_mutation_required") and engine.developer.run_generation.await_count >= 1
    [(details, _status)] = _events(cfg, "verification_contract.baseline")
    assert details["mutation_required"] == {"REQ-3": ["TEST_ADDITION"]} and details["claims"]["REQ-1"]["BEHAVIOR"]["state"] == "PASS"


# ---------------------------------------------------------------- the enforce path
@pytest.mark.asyncio
async def test_07_enforce_path_returns_the_no_mutation_success_before_planning(tmp_path):
    from kriya.workflow import workflow_controller as wc
    from kriya.workflow.plan_schema import ChangeKind
    from kriya.workflow.triage import EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass

    workspace = _workspace(tmp_path, CALC_OK)
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    cfg.paths.skills = str(tmp_path / "skills")
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW, execution_weight=ExecutionWeight.LIGHT))
    we.kernel = SimpleNamespace(config=cfg)
    we.acceptance = None
    we.acceptance_approval = None
    we.requirement_contract = None
    we.verification_authority_bundle = None
    we.verification_authorities = ()
    we.planner.run = AsyncMock(return_value="never")
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value={"success": True, "output": ""}):
        result = await wc.WorkflowController(we).execute(EXACT_GOAL, str(workspace), migration_mode="enforce")
    legacy = result.legacy_result
    assert legacy["status"] == "success" and legacy["no_mutation_required"] is True and legacy["files"] == []
    assert legacy["requirements"]["outcomes"]["REQ-1"] == "closed_by_evidence"
    assert we.planner.run.await_count == 0 and not we.run_generation_workflow.called
    [(details, status)] = _events(cfg, "verification_contract.no_mutation_required")
    assert status == "success" and details["no_mutation_required"] is True
