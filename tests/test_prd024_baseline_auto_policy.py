"""PRD-024: the brownfield full-regression baseline `auto` policy.

`auto` now has a real, deterministic trigger (route, planned files, existing
tests, git identity - no model), a triggered baseline is as binding as
`required` (captured before the first mutation, indeterminate stops the run),
PRE and POST carry a recorded environment identity (a different one is
NOT_COMPARABLE and blocks), and an unavailable per-test comparison is stated,
never read as "no failures".
"""
import json
import os
import sqlite3
import subprocess
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.workflow.baseline_policy import decide_auto_baseline, effective_baseline_policy
from kriya.workflow.triage import ChangeKind, EngineeringRoute, ExecutionWeight, ImpactVector, RiskClass
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    ValidationInvocation,
    build_validation_outcome,
    capture_validation_baseline,
    classify_baseline_delta,
)


def _route(kind=ChangeKind.TASK, risk=RiskClass.LOW, weight=ExecutionWeight.LIGHT, **impact):
    return EngineeringRoute(kind=kind, impact=ImpactVector(**impact), initial_risk_class=risk,
                            current_risk_class=risk, max_observed_risk_class=risk, execution_weight=weight)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "pricing.py").write_text("def price(q, u):\n    return q * u\n")
    (tmp_path / "src" / "banner.py").write_text("TEXT = 'hi'\n")
    (tmp_path / "tests" / "test_pricing.py").write_text("from src.pricing import price\n\ndef test_price():\n"
                                                        "    assert price(2, 3) == 6\n")
    return tmp_path


# ------------------------------------------------ the trigger matrix

@pytest.mark.parametrize("route, planned, revision, expected", [
    (None, ["src/pricing.py"], "rev", False),                                           # no route
    (_route(kind=ChangeKind.MILESTONE, risk=RiskClass.HIGH), ["src/pricing.py"], "rev", False),
    (_route(risk=RiskClass.HIGH), ["src/pricing.py"], None, False),                     # no git identity
    (_route(risk=RiskClass.HIGH), ["src/new_module.py"], "rev", False),                 # nothing existing changes
    (_route(), ["src/banner.py"], "rev", False),                                        # low, light, untested
    (_route(risk=RiskClass.MEDIUM), ["src/banner.py"], "rev", True),
    (_route(weight=ExecutionWeight.HEAVY), ["src/banner.py"], "rev", True),
    (_route(kind=ChangeKind.REFACTOR), ["src/banner.py"], "rev", True),
    (_route(dependency_change=True), ["src/banner.py"], "rev", True),
    (_route(public_contract_change=True), ["src/pricing.py"], "rev", True),             # API change
    (_route(configuration_change=True), ["src/banner.py"], "rev", True),                # runtime/config
    (_route(shared_entrypoint_change=True), ["src/banner.py"], "rev", True),            # shared owner
    (_route(), ["src/pricing.py", "src/banner.py", "src/extra.py"], "rev", True),       # broad change
    (_route(), ["src/pricing.py"], "rev", False),                   # tested source alone: supporting only
])
def test_auto_trigger_matrix(repo, route, planned, revision, expected):
    (repo / "src" / "extra.py").write_text("EXTRA = 1\n")
    decision = decide_auto_baseline(route, planned, str(repo), workspace_revision=revision)
    assert decision.required is expected, decision.reasons
    assert effective_baseline_policy("auto", decision) == ("required" if expected else "disabled")


def test_a_low_risk_edit_to_a_tested_source_records_the_test_but_takes_no_baseline(repo):
    """A docstring-sized LOW/LIGHT change to a file an existing test names:
    the test reference is recorded as supporting evidence, never a trigger."""
    decision = decide_auto_baseline(_route(), ["src/pricing.py"], str(repo), workspace_revision="rev")
    assert decision.required is False
    assert decision.signals["supporting"]["tested_changed_sources"] == ["src/pricing.py"]
    assert "alone does not require a baseline" in decision.reasons[0]
    # The same file under a real risk signal: triggered, the test is supporting.
    risky = decide_auto_baseline(_route(public_contract_change=True), ["src/pricing.py"], str(repo),
                                 workspace_revision="rev")
    assert risky.required is True
    assert risky.reasons[0] == "impact public_contract_change"
    assert risky.reasons[-1].startswith("supporting: changed source(s) named by existing tests")


def test_a_repository_without_tests_never_triggers(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    decision = decide_auto_baseline(_route(risk=RiskClass.HIGH), ["app.py"], str(tmp_path), workspace_revision="r")
    assert not decision.required and "no existing tests" in decision.reasons[0]


def test_explicit_policies_are_unchanged():
    assert effective_baseline_policy("required", None) == "required"
    assert effective_baseline_policy("disabled", None) == "disabled"
    assert effective_baseline_policy("auto", None) == "disabled"


# ------------------------------------------------ comparison

FAILING = ("FAILED tests/test_legacy.py::test_old - AssertionError: 1 != 2\n"
           "========== 1 failed, 3 passed in 0.10s ==========\n")
NEW_FAILING = ("FAILED tests/test_legacy.py::test_old - AssertionError: 1 != 2\n"
               "FAILED tests/test_pricing.py::test_price - AssertionError: 5 != 6\n"
               "========== 2 failed, 2 passed in 0.10s ==========\n")


def _baseline(output, success=False, environment="env-A"):
    invocation = ValidationInvocation(command_identity="polymorphic_validator.run_tests",
                                      selection_identity="full_suite", environment_fingerprint=environment)
    return capture_validation_baseline(workspace_revision="rev", run_id="r", invocation=invocation,
                                       raw_result={"success": success, "output": output})


def test_pre_existing_new_and_fixed_failures_are_told_apart():
    same = classify_baseline_delta(_baseline(FAILING), build_validation_outcome({"success": False, "output": FAILING}),
                                   post_environment="env-A")
    assert same.level1.classification is DeltaClassification.PRE_EXISTING_FAILURE and not same.blocking
    new = classify_baseline_delta(_baseline(FAILING), build_validation_outcome({"success": False, "output": NEW_FAILING}),
                                  post_environment="env-A")
    # The whole invocation changed (CHANGED_FAILURE, blocking). Per test, a
    # FAILED-only parser cannot tell a newly failing test from a new one when
    # PRE already had failures: NOT_COMPARABLE, stated rather than guessed.
    assert new.blocking and new.level1.classification is DeltaClassification.CHANGED_FAILURE
    assert new.level2["tests/test_pricing.py::test_price"] is DeltaClassification.NOT_COMPARABLE
    assert new.level2["tests/test_legacy.py::test_old"] is DeltaClassification.PRE_EXISTING_FAILURE
    fixed = classify_baseline_delta(_baseline(FAILING), build_validation_outcome(
        {"success": True, "output": "========== 4 passed in 0.10s ==========\n"}), post_environment="env-A")
    assert fixed.level1.classification is DeltaClassification.RESOLVED_FAILURE and not fixed.blocking  # FIXED


def test_a_different_environment_is_not_comparable_and_blocks():
    result = classify_baseline_delta(_baseline(FAILING), build_validation_outcome({"success": False, "output": FAILING}),
                                     post_environment="env-B")
    assert result.level1.classification is DeltaClassification.NOT_COMPARABLE
    assert result.blocking and result.blocking_reasons == ("environment_not_comparable",)


POM = ("<project><properties><maven.compiler.release>{release}</maven.compiler.release></properties>"
       "<dependencies>{deps}</dependencies></project>\n")
DEP = "<dependency><groupId>g</groupId><artifactId>a</artifactId><version>1</version></dependency>"


def test_under_contained_execution_only_a_real_toolchain_change_alters_the_environment(tmp_path):
    """PRE and POST identities are computed over the same declaration
    overlay: a dependency added to pom.xml is the same environment; a
    changed Java release is not."""
    from kriya.workflow.baseline_policy import baseline_environment_identity

    (tmp_path / "pom.xml").write_text(POM.format(release=17, deps=""))
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    with patch("kriya.tools.containment_oci.local_image_content_digest", return_value="sha256:image"):
        pre = baseline_environment_identity(str(tmp_path), cfg.autonomy)
        same = baseline_environment_identity(str(tmp_path), cfg.autonomy,
                                             candidate_files={"pom.xml": POM.format(release=17, deps=DEP)})
        migrated = baseline_environment_identity(str(tmp_path), cfg.autonomy,
                                                 candidate_files={"pom.xml": POM.format(release=21, deps="")})
    assert json.loads(pre)["toolchain"] is not None and json.loads(pre)["execution"] == "contained"
    assert pre == same
    assert pre != migrated


def test_a_toolchain_migration_excuses_no_failure_but_a_green_suite_passes():
    green = classify_baseline_delta(_baseline(FAILING), build_validation_outcome(
        {"success": True, "output": "========== 4 passed in 0.10s ==========\n"}), post_environment="env-B")
    assert green.level1.classification is DeltaClassification.NOT_COMPARABLE and not green.blocking


def test_an_unavailable_per_test_comparison_is_stated_never_read_as_no_failures():
    result = classify_baseline_delta(_baseline("mvn: BUILD FAILURE in module core\n"),
                                     build_validation_outcome({"success": False, "output": "mvn: BUILD FAILURE in module core\n"}),
                                     post_environment="env-A")
    assert result.level2 == {} and result.level2_available is False
    assert "PRE and POST" in result.level2_unavailable_reason
    parsed = classify_baseline_delta(_baseline(FAILING), build_validation_outcome({"success": False, "output": FAILING}),
                                     post_environment="env-A")
    assert parsed.level2_available is True and parsed.level2_unavailable_reason is None


# ------------------------------------------------ end to end

def _git(ws):
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=ws, check=True)


def _engine(tmp_path, route, developer_output, policy="auto"):
    from kriya.workflow.workflow import WorkflowEngine

    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.brownfield_full_regression_baseline_policy = policy
    cfg.engineering_triage.enabled = True
    cfg.llm_chain = []
    cfg.paths.skills = str(tmp_path / "skills")
    llm = LLMClient(cfg)

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        if "Kriya Planner Agent" in (system_prompt or ""):
            return "Step 1: adjust pricing"
        if "Kriya Architect Agent" in (system_prompt or ""):
            return 'Design\n```json\n{"files": ["src/pricing.py"]}\n```'
        return "Review: Approved"

    llm.complete = complete
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.engineering_triage.classify = AsyncMock(return_value=route)
    engine.developer.run_generation = AsyncMock(return_value=developer_output)
    return cfg, engine


def _events(cfg, kind):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    return [e["details"] for (payload,) in rows for e in json.loads(payload or "[]") if e["kind"] == kind]


FIX = [{"filepath": "src/pricing.py", "content": "def price(q, u):\n    return q * u  # unchanged behaviour\n"}]


def _full_suite(outputs):
    """run_tests stand-in: targeted runs pass; each full-suite run returns
    the next output (PRE first, then POST)."""
    queue = list(outputs)

    def run_tests(self, target_test=None, *args, **kwargs):
        if target_test:
            return {"success": True, "output": "1 passed"}
        output = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"success": "failed" not in output, "output": output}

    return patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests)


@pytest.mark.asyncio
async def test_a_known_pre_existing_failure_is_not_misread_as_a_new_regression(repo):
    _git(repo)
    cfg, engine = _engine(repo, _route(risk=RiskClass.MEDIUM), FIX)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), _full_suite([FAILING, FAILING]):
        res = await engine.run_generation_workflow(goal="Tidy the pricing comment", workspace_path=str(repo))

    assert res["quality_gates_passed"] is True
    [policy] = _events(cfg, "validation_baseline.policy")
    assert policy["configured"] == "auto" and policy["effective"] == "required"
    [delta] = _events(cfg, "validation_baseline.full_regression_delta")
    assert delta["level1_classification"] == "PRE_EXISTING_FAILURE" and delta["blocking"] is False
    assert delta["pre_environment"] == delta["post_environment"] and delta["pre_environment"]


@pytest.mark.asyncio
async def test_a_new_failure_still_blocks_under_the_auto_baseline(repo):
    _git(repo)
    cfg, engine = _engine(repo, _route(risk=RiskClass.MEDIUM), FIX)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), _full_suite([FAILING, NEW_FAILING]):
        res = await engine.run_generation_workflow(goal="Tidy the pricing comment", workspace_path=str(repo))

    assert res["quality_gates_passed"] is False
    deltas = _events(cfg, "validation_baseline.full_regression_delta")
    assert deltas and all(d["blocking"] for d in deltas)


@pytest.mark.asyncio
async def test_a_required_baseline_that_cannot_be_captured_stops_before_generation(repo):
    _git(repo)
    cfg, engine = _engine(repo, _route(risk=RiskClass.HIGH), FIX)

    def exploding(self, target_test=None, *args, **kwargs):
        raise RuntimeError("test runner crashed")

    with patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=exploding):
        res = await engine.run_generation_workflow(goal="Tidy the pricing comment", workspace_path=str(repo))

    assert res["status"] == "baseline_indeterminate"
    assert "'auto' baseline policy required" in res["reason"] and "risk HIGH" in res["reason"]
    engine.developer.run_generation.assert_not_called()  # never reached the first mutation


@pytest.mark.asyncio
async def test_a_trivial_change_does_not_pay_for_a_full_baseline(repo):
    _git(repo)
    cfg, engine = _engine(repo, _route(), [{"filepath": "src/banner.py", "content": "TEXT = 'hello'\n"}])
    calls = []

    def run_tests(self, target_test=None, *args, **kwargs):
        calls.append(target_test)
        return {"success": True, "output": "4 passed"}

    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests), \
         patch.object(engine.architect, "run_with_file_list",
                      new=AsyncMock(return_value=("Design: banner", ["src/banner.py"]))):
        res = await engine.run_generation_workflow(goal="Say hello in the banner", workspace_path=str(repo))

    assert res["quality_gates_passed"] is True
    [policy] = _events(cfg, "validation_baseline.policy")
    assert policy["effective"] == "disabled" and not policy["auto_decision"]["required"]
    assert _events(cfg, "validation_baseline.full_regression_delta") == []


# ------------------------------------------------ reusing an exact prior full-suite result

from kriya.workflow.validation_baseline import capture_brownfield_baselines  # noqa: E402


def _prior(revision="rev-A", environment="env-A", target_test=None, output="4 passed", success=True):
    invocation = ValidationInvocation(
        command_identity="polymorphic_validator.run_tests",
        selection_identity="full_suite" if target_test is None else f"target_test:{json.dumps(list(target_test))}",
        environment_fingerprint=environment, target_test=target_test)
    return capture_validation_baseline(workspace_revision=revision, run_id="earlier", invocation=invocation,
                                       raw_result={"success": success, "output": output}).to_dict()


def _capture(prior, revision="rev-A", environment="env-A"):
    calls = []

    def run_validator(target_test):
        calls.append(target_test)
        return {"success": True, "output": "4 passed"}

    result = capture_brownfield_baselines(
        run_id="now", target_test=None, full_regression_policy="required", run_validator=run_validator,
        compute_revision=lambda: revision, environment_identity=environment, prior_full_regression=prior)
    return result, calls


def test_an_exact_prior_full_suite_result_is_reused_not_rerun():
    result, calls = _capture(_prior())
    assert calls == []
    assert result.full_regression_source == "prior_full_suite"
    assert result.full_regression.run_id == "earlier"


@pytest.mark.parametrize("prior, why", [
    (_prior(revision="rev-B"), "the workspace changed since"),
    (_prior(environment="env-B"), "a different toolchain/containment/verification policy"),
    (_prior(target_test=("tests/test_pricing.py",)), "a targeted run is not the full suite"),
])
def test_prior_evidence_for_another_state_is_not_reused(prior, why):
    result, calls = _capture(prior)
    assert calls == [None], why
    assert result.full_regression_source == "captured" and result.full_regression.run_id == "now"


def test_a_prior_run_that_did_not_complete_is_not_reused():
    prior = _prior()
    prior["outcome"]["execution_status"] = "environment_failure"
    result, calls = _capture(prior)
    assert calls == [None] and result.full_regression_source == "captured"


def test_the_verification_policy_is_part_of_the_environment_identity(repo):
    from kriya.workflow.baseline_policy import baseline_environment_identity

    cfg = AppConfig()
    before = baseline_environment_identity(str(repo), cfg.autonomy)
    cfg.autonomy.sandbox_cpu_seconds = cfg.autonomy.sandbox_cpu_seconds + 1
    assert baseline_environment_identity(str(repo), cfg.autonomy) != before


def _counting_suite(calls):
    def run_tests(self, target_test=None, *args, **kwargs):
        calls.append(target_test)
        return {"success": True, "output": "4 passed"}

    return patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests)


@pytest.mark.asyncio
async def test_the_next_run_reuses_the_applied_candidates_full_suite_run(repo):
    """Run 1 captures PRE and runs POST; run 2 starts from exactly what run 1
    applied, so run 1's POST is its baseline (one full-suite run, not two)."""
    _git(repo)
    cfg, engine = _engine(repo, _route(risk=RiskClass.MEDIUM), FIX)
    calls = []
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), _counting_suite(calls):
        first = await engine.run_generation_workflow(goal="Tidy the pricing comment", workspace_path=str(repo))
        first_runs = calls.count(None)
        engine.developer.run_generation = AsyncMock(return_value=[
            {"filepath": "src/pricing.py", "content": "def price(q, u):\n    return q * u  # tidy\n"}])
        second = await engine.run_generation_workflow(goal="Tidy the pricing comment again",
                                                      workspace_path=str(repo))

    assert first["quality_gates_passed"] is True and second["quality_gates_passed"] is True
    assert first_runs == 2 and calls.count(None) == 3
    sources = [e["source"] for e in _events(cfg, "validation_baseline.full_regression_source")]
    assert sources == ["captured", "prior_full_suite"]


@pytest.mark.asyncio
async def test_a_changed_workspace_or_policy_is_baselined_again(repo):
    _git(repo)
    cfg, engine = _engine(repo, _route(risk=RiskClass.MEDIUM), FIX)
    calls = []
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}), _counting_suite(calls):
        await engine.run_generation_workflow(goal="Tidy the pricing comment", workspace_path=str(repo))
        (repo / "src" / "banner.py").write_text("TEXT = 'edited outside Kriya'\n")  # workspace moved on
        engine.developer.run_generation = AsyncMock(return_value=[
            {"filepath": "src/pricing.py", "content": "def price(q, u):\n    return q * u  # two\n"}])
        await engine.run_generation_workflow(goal="Tidy the pricing comment again", workspace_path=str(repo))
        cfg.autonomy.sandbox_cpu_seconds = cfg.autonomy.sandbox_cpu_seconds + 1  # verification policy
        engine.developer.run_generation = AsyncMock(return_value=[
            {"filepath": "src/pricing.py", "content": "def price(q, u):\n    return q * u  # three\n"}])
        await engine.run_generation_workflow(goal="Tidy the pricing comment once more", workspace_path=str(repo))

    sources = [e["source"] for e in _events(cfg, "validation_baseline.full_regression_source")]
    assert sources == ["captured", "captured", "captured"]
    assert calls.count(None) == 6


def test_a_milestone_reuses_the_previous_milestones_full_suite_run(tmp_path, monkeypatch):
    """Through the real CLI and milestone driver, under production's
    `required`: M1 captures PRE and runs POST; M2 and the integration unit
    each start from exactly what the unit before them committed, so that
    unit's POST run is their baseline - one full-suite run per unit, plus
    M1's PRE, instead of two per unit."""
    import test_prd020_milestone_requirements as harness

    calls = []

    def run_tests(self, target_test=None, *args, **kwargs):
        calls.append(target_test)
        return {"success": True, "output": "1 passed"}

    with patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=run_tests), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               return_value={"success": True, "output": ""}):
        cfg, result = harness._run(tmp_path, monkeypatch, harness.Transport(),
                                   brownfield_full_regression_baseline_policy="required")

    assert result.exit_code == 0, result.output
    sources = [e["source"] for e in _events(cfg, "validation_baseline.full_regression_source")]
    assert sources == ["captured", "prior_full_suite", "prior_full_suite"], sources  # M1, M2, integration
    assert calls.count(None) == 4  # M1's PRE, then one POST per unit
