"""FS-1C2 B2-a: the operator acceptance file is the only producer of BEHAVIOR
evidence (kriya/workflow/acceptance_oracle.py).

Every acceptance run here is real: Kriya's runner in a subprocess of the test
interpreter, on a candidate tree in tmp_path. The A1 cases run on the real
freezegun code (tests/_b2a_fixtures.py): the base, the live helper-only
candidate (byte-identical to the one Kriya refused at the FS-1C1 sentinel) and
a correct candidate.
"""
import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _b2a_fixtures import (
    A1_ACCEPTANCE,
    A1_GOAL,
    CALC,
    CALC_ACCEPTANCE,
    CALC_GOAL,
    LIVE_CANDIDATE_DIGEST,
    calc_project,
    freezegun_api,
    freezegun_project,
    write_files,
)
from _fs1c1_a1_specimen import live_c0_judgment
from _strict_doubles import strict_kernel
from click.testing import CliRunner

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.obligations import ObligationLedger, ObligationStatus
from kriya.workflow.requirements import (
    BEHAVIOR,
    REGRESSION_PRESERVATION,
    RequirementOutcome,
    blocking_requirements,
    close_mutation_scope_requirements,
    close_unverified_requirements_with_named_tests,
    derive_requirements,
    record_requirement_claim,
    record_requirement_verdicts,
    requirement_evidence,
    requirement_outcomes,
    seed_requirement_obligations,
)

PRODUCTION = {"unknown_policy": "block", "unverified_policy": "block"}


def _validator(root):
    return lambda: PolymorphicValidator(str(root), autonomy_cfg=AutonomyConfig())


def _artifact(tmp_path, source, goal, name="acceptance.py"):
    path = tmp_path / name
    path.write_text(source)
    return ao.load_acceptance(str(path), derive_requirements(goal), str(tmp_path / "state"))


def _run(artifact, root, paths=()):
    return ao.run_acceptance(artifact, str(root), candidate_paths=list(paths), validator_factory=_validator(root))


def _judge(artifact, root, rid="REQ-1"):
    return ao.judge_acceptance(artifact, _run(artifact, root))[rid]


def _ledger(goal, verdict=RequirementOutcome.SATISFIED, candidate="cand"):
    reqs = derive_requirements(goal)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, reqs)
    record_requirement_verdicts(ledger, reqs, {r.id: (verdict, "model") for r in reqs.requirements}, revision=1,
                                evidence_fingerprint=candidate, source="test")
    return reqs, ledger


def _close(ledger, reqs, artifact, root, test_files=(), paths=()):
    return ao.close_requirements_with_acceptance(
        ledger, reqs, artifact, test_files=list(test_files),
        execute=lambda a: _run(a, root, paths), source="test", revision=1)


# ---------------------------------------------------------------- 1, 2: bound before generation

def test_the_artifact_is_digested_stored_outside_the_workspace_and_bound_to_the_requirement_set(tmp_path):
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    assert artifact.digest == hashlib.sha256(CALC_ACCEPTANCE.encode()).hexdigest()
    assert artifact.requirement_set_digest == derive_requirements(CALC_GOAL).digest
    assert artifact.stored_path == str(tmp_path / "state" / "acceptance" / f"{artifact.digest}.py")
    assert open(artifact.stored_path).read() == CALC_ACCEPTANCE
    assert artifact.cases == (ao.AcceptanceCase(("REQ-1",), "kriya_acceptance.test_double_doubles", 7),)
    assert artifact.imports == ("calc",)


@pytest.mark.parametrize("source, code", [
    (CALC_ACCEPTANCE.replace('"REQ-1"', '"REQ-7"'), ao.ACCEPTANCE_UNKNOWN_REQUIREMENT),
    (CALC_ACCEPTANCE.replace('@pytest.mark.kriya_requirement("REQ-1")\n', ""), ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE.replace('@pytest.mark.kriya_requirement("REQ-1")\n',
                             '@pytest.mark.kriya_requirement("REQ-1")\n@pytest.mark.xfail\n'),
     ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE.replace('@pytest.mark.kriya_requirement("REQ-1")\n',
                             '@pytest.mark.kriya_requirement("REQ-1")\n@pytest.mark.skip\n'),
     ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE + "pytestmark = pytest.mark.skip\n", ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE.replace("from calc import double", "from .calc import double"), ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE.replace("def test_double_doubles", "async def test_double_doubles"),
     ao.ACCEPTANCE_ARTIFACT_INVALID),
    (CALC_ACCEPTANCE.replace('("REQ-1")', "(REQ)"), ao.ACCEPTANCE_ARTIFACT_INVALID),
    ("def broken(:\n", ao.ACCEPTANCE_ARTIFACT_INVALID),
    ("import calc\n", ao.ACCEPTANCE_ARTIFACT_INVALID),
])
def test_an_unknown_requirement_or_a_case_that_could_skip_or_invert_is_refused_before_generation(
        tmp_path, source, code):
    with pytest.raises(ao.AcceptanceError) as refused:
        _artifact(tmp_path, source, CALC_GOAL)
    assert refused.value.reason_code == code
    assert not (tmp_path / "state" / "acceptance").exists() or not os.listdir(tmp_path / "state" / "acceptance")


def test_a_mutation_scope_requirement_is_never_an_acceptance_target(tmp_path):
    goal = CALC_GOAL + "- Do not modify any other file.\n"
    with pytest.raises(ao.AcceptanceError) as refused:
        _artifact(tmp_path, CALC_ACCEPTANCE.replace('"REQ-1"', '"REQ-2"'), goal)
    assert refused.value.reason_code == ao.ACCEPTANCE_REQUIREMENT_NOT_BEHAVIORAL


def _cli(tmp_path, monkeypatch, args):
    """`kriya generate` through the real CLI up to the dispatch (no model)."""
    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")
    dispatch = AsyncMock(return_value={"status": "success", "run_id": "r", "quality_gates_passed": True})
    kernel = strict_kernel(cfg)
    with patch("kriya.cli.load_config", return_value=cfg), patch("kriya.cli.LLMClient", autospec=True) as llm, \
         patch("kriya.cli.Kernel", return_value=kernel), patch("kriya.cli.WorkflowEngine") as engine, \
         patch("kriya.cli._learned_reference_context", new=AsyncMock(return_value="")), \
         patch("kriya.cli._dispatch_generation", new=dispatch):
        result = CliRunner().invoke(__import__("kriya.cli", fromlist=["main"]).main,
                                    ["generate", CALC_GOAL, "-y", *args])
    return result, dispatch, llm, engine


def test_the_cli_binds_the_operator_file_before_the_workflow_starts(tmp_path, monkeypatch):
    (tmp_path / "acceptance.py").write_text(CALC_ACCEPTANCE)
    result, dispatch, _, engine = _cli(tmp_path, monkeypatch, ["--acceptance", str(tmp_path / "acceptance.py")])
    assert result.exit_code == 0, result.output
    bound = engine.return_value.acceptance
    assert isinstance(bound, ao.AcceptanceArtifact)
    assert bound.digest == hashlib.sha256(CALC_ACCEPTANCE.encode()).hexdigest()
    assert bound.requirement_set_digest == derive_requirements(CALC_GOAL).digest
    assert dispatch.await_args.args[0] is engine.return_value  # the engine the run executes carries it


def test_the_cli_refuses_an_unknown_requirement_id_before_any_model_client_exists(tmp_path, monkeypatch):
    (tmp_path / "acceptance.py").write_text(CALC_ACCEPTANCE.replace('"REQ-1"', '"REQ-9"'))
    result, dispatch, llm, _ = _cli(tmp_path, monkeypatch, ["--acceptance", str(tmp_path / "acceptance.py")])
    assert result.exit_code == 1
    assert "ACCEPTANCE_UNKNOWN_REQUIREMENT" in result.output and "REQ-9" in result.output
    assert dispatch.await_count == 0 and llm.call_count == 0


def test_without_acceptance_the_engine_carries_none(tmp_path, monkeypatch):
    result, _, _, engine = _cli(tmp_path, monkeypatch, [])
    assert result.exit_code == 0, result.output
    assert engine.return_value.acceptance is None


# ---------------------------------------------------------------- 3: immutable to the candidate

def test_the_candidate_cannot_write_the_staged_or_stored_acceptance_file(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    writer = AuthorizedFileWriter(str(root))
    staged = root / ao.STAGING_DIR / "any" / "kriya_acceptance.py"
    assert writer.authorize(str(staged)).reason_code == "TRUSTED_CONTROL_PATH_DENIED"
    assert os.path.relpath(artifact.stored_path, root).startswith("..")  # outside the workspace


def test_a_candidate_rewriting_the_staged_file_during_the_run_makes_it_indeterminate(tmp_path):
    tamper = ("import glob, os\n"
              "for staged in glob.glob(os.path.join(os.getcwd(), '.kriya', 'acceptance-runs', '*', "
              "'kriya_acceptance.py')):\n"
              "    open(staged, 'a').write('\\n# rewritten\\n')\n\n"
              "def double(x):\n    return 2 * x\n")
    root = write_files(tmp_path / "ws", {"calc/__init__.py": tamper})
    judgment = _judge(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert judgment.code == ao.ACCEPTANCE_ARTIFACT_CHANGED


def test_a_stored_copy_changed_after_binding_is_refused(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    with open(artifact.stored_path, "a") as handle:
        handle.write("\n")
    run = _run(artifact, root)
    assert run.refusal.reason_code == ao.ACCEPTANCE_ARTIFACT_CHANGED and run.result == {}


def test_candidate_code_changing_candidate_files_during_the_run_makes_it_indeterminate(tmp_path):
    rewrite = ("import os\n"
               "open(os.path.join(os.path.dirname(__file__), 'extra.py'), 'w').write('X = 1\\n')\n\n"
               "def double(x):\n    return 2 * x\n")
    root = write_files(tmp_path / "ws", {"calc/__init__.py": rewrite, "calc/extra.py": "X = 0\n"})
    judgment = _judge(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert judgment.code == ao.ACCEPTANCE_CANDIDATE_CHANGED_DURING_RUN


# ---------------------------------------------------------------- 4: flat layouts import through the acceptance path

@pytest.mark.parametrize("files, source", [
    ({"calc/__init__.py": "def double(x):\n    return 2 * x\n"}, CALC_ACCEPTANCE),
    ({"calc.py": "def double(x):\n    return 2 * x\n"}, CALC_ACCEPTANCE),
    ({"calc/__init__.py": "from .core import double\n", "calc/core.py": "def double(x):\n    return 2 * x\n",
      "tests/__init__.py": "", "tests/test_core.py": "from calc import double\n\ndef test_d():\n    assert double(1)\n"},
     CALC_ACCEPTANCE.replace("from calc import double", "from calc.core import double")),
])
def test_a_flat_package_or_module_imports_through_the_acceptance_runner(tmp_path, files, source):
    root = write_files(tmp_path / "ws", files)
    artifact = _artifact(tmp_path, source, CALC_GOAL)
    run = _run(artifact, root)
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_PASSED, judgment.reason
    assert run.observations["modules"]["calc"].startswith(os.path.realpath(root))
    assert run.report.complete and run.report.runner == "pytest"
    assert not list(root.rglob("__pycache__")) and not (root / ".pytest_cache").exists()  # -B, no cache
    assert not os.listdir(root / ao.STAGING_DIR)  # staging removed
    assert judgment.evidence["acceptance_digest"] == artifact.digest
    assert judgment.evidence["runner_contract_digest"] == ao.RUNNER_CONTRACT_DIGEST


# ---------------------------------------------------------------- 5, 6, 7: no candidate pytest machinery

_FORCE_PASS_CONFTEST = (
    "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n"
)
_BROKEN_CONFTEST = "raise RuntimeError('a candidate conftest')\n"
_HOSTILE_CONFIG = {
    "pytest.ini": "[pytest]\naddopts = -p no:junitxml -k nothing\npython_functions = check_*\n",
    "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-x --deselect kriya_acceptance.py'\n",
    "setup.cfg": "[tool:pytest]\naddopts = -p no:terminal\n",
    "tox.ini": "[pytest]\naddopts = --collect-only\n",
}


@pytest.mark.parametrize("hostile", [
    {"conftest.py": _FORCE_PASS_CONFTEST, "calc/conftest.py": _FORCE_PASS_CONFTEST},
    {"conftest.py": _BROKEN_CONFTEST},
    _HOSTILE_CONFIG,
    {"evil_plugin.py": _FORCE_PASS_CONFTEST, "pytest.ini": "[pytest]\naddopts = -p evil_plugin\n"},
])
@pytest.mark.parametrize("correct", [True, False])
def test_candidate_conftest_config_and_plugins_cannot_change_the_acceptance_result(tmp_path, hostile, correct):
    root = calc_project(tmp_path / "ws", correct, hostile)
    judgment = _judge(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert judgment.code == (ao.ACCEPTANCE_PASSED if correct else ao.ACCEPTANCE_VIOLATED), judgment.reason


def test_only_pytest_itself_takes_part_in_the_run(tmp_path):
    """Measured: with plugin autoload on, anyio/pytest_asyncio/testmon/xdist
    load from this environment. The runner turns autoload off and reports
    every registered plugin; none may come from outside pytest."""
    root = calc_project(tmp_path / "ws", True)
    run = _run(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    plugins = run.observations["plugins"]
    assert plugins and all(name == "__main__" or name.startswith("_pytest.") for name in plugins), plugins


def test_a_foreign_plugin_in_the_run_makes_it_indeterminate(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    run = _run(artifact, root)
    run.observations["plugins"].append("xdist.plugin")
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_FOREIGN_PLUGIN


# ---------------------------------------------------------------- 8, 9, 10: closure and counter-evidence

def test_a_passing_case_closes_a_pure_behaviour_requirement(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert attempt["closed"] is True and attempt["reason_code"] == ao.ACCEPTANCE_PASSED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    evidence = requirement_evidence(ledger, reqs)["REQ-1"]["claims"][BEHAVIOR]
    assert evidence["method"] == ao.ACCEPTANCE_METHOD and evidence["required_claims"] == [BEHAVIOR]
    assert evidence["evidence_id"] == "cand" and evidence["requirement_set_digest"] == reqs.digest
    assert not blocking_requirements(ledger, reqs, **PRODUCTION)


def test_an_assertion_against_the_candidate_result_is_violated(tmp_path):
    root = calc_project(tmp_path / "ws", False)
    reqs, ledger = _ledger(CALC_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED and attempt["violated"] is True
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs)] == ["REQ-1"]  # VIOLATED blocks under any policy


def test_a_candidate_exception_contradicting_the_behaviour_is_violated(tmp_path):
    root = write_files(tmp_path / "ws", {"calc/__init__.py": "def double(x):\n    raise TypeError('no')\n"})
    assert _judge(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root).code == ao.ACCEPTANCE_VIOLATED


# ---------------------------------------------------------------- A1 reproducer (real freezegun code)

def test_the_live_a1_candidate_is_byte_identical_to_the_fixture():
    assert hashlib.sha256(freezegun_api("helper_only").encode()).hexdigest() == LIVE_CANDIDATE_DIGEST


def _a1(tmp_path, variant):
    from _fs1c1_a1_specimen import TARGET, TRACKED

    root = freezegun_project(tmp_path / "ws", variant)
    reqs, ledger = _ledger(A1_GOAL, candidate="a1-live-candidate")
    artifact = _artifact(tmp_path, A1_ACCEPTANCE, A1_GOAL)
    close_mutation_scope_requirements(
        ledger, reqs, tracked_paths=TRACKED, scope_evidence={"actual_paths": [TARGET], "foreign_paths": []},
        source="test", revision=1)
    acceptance = _close(ledger, reqs, artifact, root, test_files=["tests/test_operations.py"], paths=[TARGET])
    named = close_unverified_requirements_with_named_tests(
        ledger, reqs, test_files=["tests/test_operations.py"], modified=[TARGET], judge=live_c0_judgment,
        source="test", revision=1)
    return reqs, ledger, acceptance, named


def test_a1_the_helper_only_candidate_is_violated_and_never_succeeds(tmp_path):
    reqs, ledger, [acceptance], named = _a1(tmp_path, "helper_only")
    assert acceptance["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert acceptance["case_results"] == {"kriya_acceptance.test_freeze_time_at_epoch": ["failed"],
                                          "kriya_acceptance.test_freeze_time_fractional_timestamp": ["failed"]}
    assert named == []  # VIOLATED is final: no closure attempt on it
    outcomes = requirement_outcomes(ledger, reqs)
    assert outcomes == {"REQ-1": RequirementOutcome.VIOLATED, "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_a1_the_correct_candidate_closes_by_acceptance_and_c0_together(tmp_path):
    reqs, ledger, [acceptance], [named] = _a1(tmp_path, "correct")
    assert acceptance["reason_code"] == ao.ACCEPTANCE_PASSED
    assert acceptance["claims"] == [BEHAVIOR, REGRESSION_PRESERVATION]
    assert acceptance["closed"] is False  # behaviour alone does not close the compound statement
    assert named["closed"] is True and named["regression_preserved"] is True
    assert requirement_outcomes(ledger, reqs) == {"REQ-1": RequirementOutcome.CLOSED_BY_EVIDENCE,
                                                  "REQ-2": RequirementOutcome.CLOSED_BY_EVIDENCE}
    assert not blocking_requirements(ledger, reqs, **PRODUCTION)
    closure = requirement_evidence(ledger, reqs)["REQ-1"]
    assert set(closure["claims"]) == {BEHAVIOR, REGRESSION_PRESERVATION}


def test_a1_behaviour_without_the_regression_claim_stays_open(tmp_path):
    root = freezegun_project(tmp_path / "ws", "correct")
    reqs, ledger = _ledger(A1_GOAL)
    _close(ledger, reqs, _artifact(tmp_path, A1_ACCEPTANCE, A1_GOAL), root, test_files=["tests/test_operations.py"])
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


# ---------------------------------------------------------------- 11, 12, 14: not a contradiction -> never VIOLATED

@pytest.mark.parametrize("source, code", [
    (CALC_ACCEPTANCE + "\ndel test_double_doubles\n", ao.ACCEPTANCE_IDENTITY_NOT_EXECUTED),
    (CALC_ACCEPTANCE.replace("    assert double(5) == 10\n", "    assert undefined_name(5) == 10\n"),
     ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION),
    (CALC_ACCEPTANCE.replace("    assert double(5) == 10\n",
                             "    from calc import tripled\n    assert tripled(5) == 15\n"),
     ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION),
    (CALC_ACCEPTANCE.replace("def test_double_doubles():", "def test_double_doubles(missing_fixture):"),
     ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION),
    (CALC_ACCEPTANCE.replace("    assert double(5) == 10\n", "    pytest.skip('later')\n"),
     ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION),
    (CALC_ACCEPTANCE.replace("from calc import double", "from calc import double\nimport not_installed_anywhere"),
     ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),  # collection error: the session did not complete
    # An assertion before any candidate code ran (the candidate module is never imported).
    (CALC_ACCEPTANCE.replace("from calc import double\n", "").replace(
        "    assert double(5) == 10\n", "    assert 1 == 2\n    from calc import double\n    assert double(5) == 10\n"),
     ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION),
])
def test_a_case_that_did_not_observe_a_contradiction_is_never_violated(tmp_path, source, code):
    root = calc_project(tmp_path / "ws", False)  # the candidate is wrong, but no case observed it
    reqs, ledger = _ledger(CALC_GOAL)
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, source, CALC_GOAL), root)
    assert attempt["reason_code"] == code, attempt
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_missing_dependency_inside_candidate_code_is_never_violated(tmp_path):
    root = write_files(tmp_path / "ws", {"calc/__init__.py": "def double(x):\n    import not_installed_anywhere\n"})
    assert _judge(_artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root).code == (
        ao.ACCEPTANCE_FAILED_WITHOUT_CONTRADICTION)


def _passing_run(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    return artifact, _run(artifact, root)


@pytest.mark.parametrize("damage, code", [
    (lambda run: setattr(run, "report", None), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: setattr(run.report, "completeness", "INDETERMINATE"), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: setattr(run.report, "runner", "maven"), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: setattr(run, "observations", None), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: run.observations.update(exit_code=2), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: run.observations.update(reports=[]), ao.ACCEPTANCE_EVIDENCE_INDETERMINATE),
    (lambda run: run.observations["modules"].update(calc="/elsewhere/site-packages/calc/__init__.py"),
     ao.ACCEPTANCE_CANDIDATE_NOT_EXERCISED),
])
def test_missing_malformed_or_inconsistent_evidence_is_indeterminate(tmp_path, damage, code):
    artifact, run = _passing_run(tmp_path)
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_PASSED
    damage(run)
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == code


def test_a_stale_report_from_another_invocation_is_never_read(tmp_path):
    """The runner process 'succeeds' without writing anything; a passing
    report and observations of an earlier invocation lie beside it."""
    artifact, earlier = _passing_run(tmp_path)
    root = tmp_path / "ws"
    reports = root / ".kriya" / "test-reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "earlier.xml").write_text(
        '<testsuite><testcase classname="kriya_acceptance" name="test_double_doubles"/></testsuite>')
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout",
                      return_value={"returncode": 0, "stdout": "", "stderr": ""}):
        run = _run(artifact, root)
    judgment = ao.judge_acceptance(artifact, run)["REQ-1"]
    assert judgment.code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE
    assert "STRUCTURED_REPORT_MISSING" in judgment.reason and earlier.report.complete


def test_a_timed_out_run_is_indeterminate(tmp_path):
    artifact, _ = _passing_run(tmp_path)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout",
                      return_value={"returncode": -9, "stdout": "", "stderr": "", "timed_out": True}):
        run = _run(artifact, tmp_path / "ws")
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE


def test_an_environment_failure_is_indeterminate_and_runs_nothing(tmp_path):
    artifact, _ = _passing_run(tmp_path)
    with patch.object(PolymorphicValidator, "_resolve_python_interpreter",
                      return_value=("python3", "pip install failed")), \
         patch.object(PolymorphicValidator, "_run_cmd_with_timeout") as process:
        run = _run(artifact, tmp_path / "ws")
    assert process.call_count == 0
    assert ao.judge_acceptance(artifact, run)["REQ-1"].code == ao.ACCEPTANCE_EVIDENCE_INDETERMINATE


# ---------------------------------------------------------------- 13: unsupported layouts are refused, no fallback

@pytest.mark.parametrize("files, source", [
    ({"src/calc/__init__.py": "def double(x):\n    return 2 * x\n", "pyproject.toml": ""}, CALC_ACCEPTANCE),
    ({"calc/core.py": "def double(x):\n    return 2 * x\n"},
     CALC_ACCEPTANCE.replace("from calc import double", "from calc.core import double")),
    ({"calc/__init__.py": "", "tests/__init__.py": "", "tests/helpers.py": "def double(x):\n    return 2 * x\n"},
     CALC_ACCEPTANCE.replace("from calc import double", "from tests.helpers import double")),
    ({"calc/__init__.py": ""}, CALC_ACCEPTANCE.replace("from calc import double", "from math import floor as double")),
])
def test_an_unsupported_layout_is_a_typed_refusal_that_runs_nothing(tmp_path, files, source):
    root = write_files(tmp_path / "ws", files)
    artifact = _artifact(tmp_path, source, CALC_GOAL)
    with patch.object(PolymorphicValidator, "run_tests") as tests, \
         patch.object(PolymorphicValidator, "run_acceptance") as acceptance:
        reqs, ledger = _ledger(CALC_GOAL)
        [attempt] = _close(ledger, reqs, artifact, root)
    assert tests.call_count == 0 and acceptance.call_count == 0  # no fallback to the repository's tests
    assert attempt["reason_code"] == ao.ACCEPTANCE_LAYOUT_UNSUPPORTED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_non_python_project_is_refused_without_running_its_build(tmp_path):
    root = calc_project(tmp_path / "ws", True, {"pom.xml": "<project/>"})
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    with patch.object(PolymorphicValidator, "run_tests") as tests, \
         patch.object(PolymorphicValidator, "run_acceptance") as acceptance:
        judgment = _judge(artifact, root)
    assert judgment.code == ao.ACCEPTANCE_RUNNER_UNSUPPORTED
    assert tests.call_count == 0 and acceptance.call_count == 0


# ---------------------------------------------------------------- 14, 15, 16: nothing else closes behaviour

def test_evidence_for_another_candidate_never_closes_this_one(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL, candidate="cand-A")
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    record_requirement_verdicts(ledger, reqs, {"REQ-1": (RequirementOutcome.SATISFIED, "model")}, revision=2,
                                evidence_fingerprint="cand-B", source="test")
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_model_satisfied_verdict_never_replaces_missing_acceptance_evidence(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL, verdict=RequirementOutcome.SATISFIED)
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE + "\ndel test_double_doubles\n", CALC_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, **PRODUCTION)] == ["REQ-1"]


def test_candidate_written_tests_never_close_behaviour(tmp_path):
    proof = "from calc import double\n\n\ndef test_new():\n    assert double(5) == 10\n"
    root = calc_project(tmp_path / "ws", False, {"tests/__init__.py": "", "tests/test_new.py": proof})
    reqs, ledger = _ledger(CALC_GOAL)
    assert _close(ledger, reqs, None, root, test_files=["tests/test_new.py"]) == []  # no operator file: no oracle
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    with pytest.raises(ValueError):  # a test run can never be recorded as behaviour evidence
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method="named_test_oracle",
                                 detail={}, source="test", revision=1)
    importing = CALC_ACCEPTANCE.replace("from calc import double", "from tests.test_new import double")
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, importing, CALC_GOAL), root)
    assert attempt["reason_code"] == ao.ACCEPTANCE_LAYOUT_UNSUPPORTED


# ---------------------------------------------------------------- 17, 18: resume and a changed artifact

def test_resume_fingerprints_bind_the_exact_acceptance_digest(tmp_path):
    from kriya.workflow.resume_fingerprints import generation_resume_fingerprints

    cfg = AppConfig()
    cfg.paths.skills = str(tmp_path / "skills")

    def fingerprints(**extra):
        return generation_resume_fingerprints(cfg, str(tmp_path), goal=CALC_GOAL, **extra)

    without, none = fingerprints(), fingerprints(acceptance_digest=None)
    a, a_again, b = (fingerprints(acceptance_digest=d) for d in ("a" * 64, "a" * 64, "b" * 64))
    assert without == none  # a run without an acceptance file keeps its fingerprints byte-identical
    changed = {name for name in without if without[name] != a[name]}
    assert changed == {"verification_policy"}
    assert a == a_again and a["verification_policy"] != b["verification_policy"]


def test_a_changed_or_withdrawn_artifact_supersedes_earlier_evidence_for_the_same_candidate(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL)
    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE

    stricter = CALC_ACCEPTANCE.replace("assert double(5) == 10", "assert double(5) == 11")
    [attempt] = _close(ledger, reqs, _artifact(tmp_path, stricter, CALC_GOAL, "v2.py"), root)
    assert attempt["reason_code"] == ao.ACCEPTANCE_VIOLATED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED

    _close(ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE
    [withdrawn] = _close(ledger, reqs, None, root)  # resumed without the file
    assert withdrawn["reason_code"] == ao.ACCEPTANCE_SUPERSEDED
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_failed_rerun_never_leaves_an_earlier_pass_standing(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    _close(ledger, reqs, artifact, root)
    (root / "calc" / "__init__.py").write_text("def double(x):\n    return x + 2\n")
    _close(ledger, reqs, artifact, root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.VIOLATED
    (root / "calc" / "__init__.py").write_text("raise ImportError('gone')\n")
    _close(ledger, reqs, artifact, root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_an_artifact_bound_to_another_requirement_set_closes_nothing(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    other_goal = CALC_GOAL.replace("double(5) returns 10", "double(6) returns 12")
    reqs, ledger = _ledger(other_goal)
    [attempt] = _close(ledger, reqs, artifact, root)
    assert attempt["reason_code"] == ao.ACCEPTANCE_REQUIREMENT_SET_MISMATCH
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_claims_record_only_judgments(tmp_path):
    reqs, ledger = _ledger(CALC_GOAL)
    with pytest.raises(ValueError):
        record_requirement_claim(ledger, reqs, "REQ-1", BEHAVIOR, evidence_id="cand", method=ao.ACCEPTANCE_METHOD,
                                 detail={}, source="test", revision=1, status=ObligationStatus.PENDING)


# ---------------------------------------------------------------- 19: direct path

WRONG_GREETING_GOAL = "Create greeting.py with a greet(name) function so that greet('Ann') returns 'Hello, Ann!'\n"
GREETING_GOAL = "Create greeting.py with a greet(name) function so that greet('Ann') returns 'Hello, Ann'\n"
GREETING_ACCEPTANCE = ("import pytest\n\nfrom greeting import greet\n\n\n"
                       "@pytest.mark.kriya_requirement(\"REQ-1\")\n"
                       "def test_greets_by_name():\n    assert greet('Ann') == EXPECTED\n")


@pytest.mark.parametrize("goal, expected, outcome", [
    (WRONG_GREETING_GOAL, "Hello, Ann!", "violated"),
    (GREETING_GOAL, "Hello, Ann", "closed_by_evidence"),
    # B2-COV: a general rule ("<name>") - the passing case only supports it, never applied
    ("Create greeting.py with a greet(name) function that returns 'Hello, <name>'\n", "Hello, Ann", "unverified"),
])
@pytest.mark.asyncio
async def test_direct_path_the_acceptance_file_decides_the_behaviour_requirement(tmp_path, goal, expected, outcome):
    from test_prd020_requirement_lineage import _engine, _events, _gates_pass, _verdicts_json

    cfg, engine, _ = _engine(tmp_path, lambda n, prompt: _verdicts_json(prompt),
                             requirement_unknown_policy="block", requirement_unverified_policy="block")
    source = GREETING_ACCEPTANCE.replace("EXPECTED", repr(expected))
    engine.acceptance = _artifact(tmp_path, source, goal)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    p1, p2 = _gates_pass()
    with p1, p2:
        res = await engine.run_generation_workflow(goal=goal, workspace_path=str(workspace))
    succeeded = outcome == "closed_by_evidence"
    assert res["quality_gates_passed"] is succeeded
    assert (workspace / "greeting.py").exists() is succeeded  # never applied without success
    if outcome == "unverified":
        # VERIFICATION-CONTRACT-003 (D3): B2-COV decided before any model call - finite cases can never close the
        # general rule, so the goal is refused as VERIFICATION_AUTHORITY_REQUIRED (the approval is the authority)
        assert res["failure_category"] == "verification_authority_required"
        assert "B2-COV" in res["requirements_admission"]["residual"][0]["why"]
        assert _events(cfg, "requirement.closure") == []
        return
    assert res["requirements"]["outcomes"]["REQ-1"] == outcome
    [closure] = [c for e in _events(cfg, "requirement.closure") for c in e["closures"]
                 if c.get("kind") == ao.ACCEPTANCE_METHOD][-1:]
    assert closure["reason_code"] == {"closed_by_evidence": ao.ACCEPTANCE_PASSED, "violated": ao.ACCEPTANCE_VIOLATED,
                                      "unverified": ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN}[outcome]


# ---------------------------------------------------------------- 20: enforce path

@pytest.mark.parametrize("correct", [True, False])
@pytest.mark.asyncio
async def test_enforce_path_the_terminal_gate_judges_the_acceptance_file(tmp_path, monkeypatch, correct):
    from test_prd020_requirement_lineage import _git_base, _verdicts_json

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

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _git_base(workspace, {"calc/__init__.py": "def double(x):\n    raise NotImplementedError\n"})
    monkeypatch.setattr(wc, "create_git_worktree", lambda path: path)
    plan = EngineeringPlan(plan_id="b2a", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="double", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="calc/__init__.py", action=FileAction.MODIFY)], requirement_ids=["REQ-1"],
    )])
    cfg = AppConfig()
    cfg.autonomy.spec_compliance_enabled = True
    cfg.autonomy.requirement_unknown_policy = "block"
    cfg.autonomy.requirement_unverified_policy = "block"
    we = MagicMock()
    we.engineering_triage.classify = AsyncMock(return_value=EngineeringRoute(
        kind=ChangeKind.TASK, impact=ImpactVector(), initial_risk_class=RiskClass.LOW,
        current_risk_class=RiskClass.LOW, max_observed_risk_class=RiskClass.LOW,
        execution_weight=ExecutionWeight.LIGHT,
    ))
    we.kernel = SimpleNamespace(config=cfg)
    we.acceptance = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)

    async def check(**kwargs):
        prompt = "\n".join(f"{r.id}: {r.text}" for r in kwargs["requirements"].requirements)
        return json.loads(_verdicts_json(prompt))  # "satisfied": a model claim (FS-1B)

    we.spec_compliance.check = check

    async def fake_run(**kwargs):
        (workspace / "calc" / "__init__.py").write_text(
            "def double(x):\n    return 2 * x\n" if correct else "def double(x):\n    return x + 2\n")
        return {"status": "success", "quality_gates_passed": True, "files": ["calc/__init__.py"]}

    we.run_generation_workflow = fake_run
    we.planner.run = AsyncMock(return_value="fake plan text")
    with patch.object(wc, "parse_planner_structured_output", return_value=(MagicMock(), None)), \
         patch.object(wc, "build_engineering_plan_from_planner_output", return_value=plan), \
         patch.object(wc, "validate_plan", new=AsyncMock(return_value=PlanValidationResult(valid=True))):
        result = await wc.WorkflowController(we).execute(CALC_GOAL, str(workspace), migration_mode="enforce")

    outcomes = result.legacy_result["requirements"]["outcomes"]
    gap = result.legacy_result.get("global_requirement_gap")
    if correct:
        assert outcomes == {"REQ-1": "closed_by_evidence"} and not gap, gap
    else:
        assert outcomes == {"REQ-1": "violated"} and "REQ-1 (violated)" in gap
        assert result.legacy_result["quality_gates_passed"] is False


# ---------------------------------------------------------------- 21: milestone path through the real CLI

@pytest.mark.parametrize("correct", [True, False])
def test_milestone_path_the_integration_unit_judges_the_plans_acceptance_file(tmp_path, monkeypatch, correct):
    from test_prd020_milestone_requirements import ORIGINAL_GOAL, Transport, _events, _run

    reqs = derive_requirements(ORIGINAL_GOAL)
    assert reqs.ids == ["REQ-1", "REQ-2", "REQ-3"]
    expected = "m1.py" if correct else "wrong"
    acceptance = tmp_path / "acceptance.py"
    acceptance.write_text("import pytest\n\nimport m1\nimport m2\n\n\n"
                          "@pytest.mark.kriya_requirement(\"REQ-2\")\n"
                          f"def test_m1_value():\n    assert m1.VALUE == {expected!r}\n\n\n"
                          "@pytest.mark.kriya_requirement(\"REQ-3\")\n"
                          "def test_m2_value():\n    assert m2.VALUE == 'm2.py'\n")
    import kriya.cli as cli

    original = cli._generate_impl

    def with_acceptance(*args, **kwargs):
        kwargs["acceptance"] = str(acceptance)
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "_generate_impl", with_acceptance)
    cfg, result = _run(tmp_path, monkeypatch, Transport(), requirement_unverified_policy="record")
    closures = {c["requirement"]: c for e in _events(cfg, "requirement.closure") for c in e["closures"]
                if c.get("kind") == ao.ACCEPTANCE_METHOD}
    # "m1.py defines VALUE" states no concrete case: GENERAL (B2-COV). Passing
    # cases only support it; a contradicting case still disproves it.
    assert closures["REQ-3"]["reason_code"] == ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN, result.output
    assert closures["REQ-2"]["reason_code"] == (ao.ACCEPTANCE_GENERAL_RULE_UNPROVEN if correct
                                                else ao.ACCEPTANCE_VIOLATED)
    assert "REQ-1" not in closures  # "Build a two-module project.": no acceptance case
    assert closures["REQ-3"]["closed"] is False
    assert (closures["REQ-2"]["closed"], closures["REQ-2"]["violated"]) == (False, not correct)
    assert (result.exit_code == 0) is correct, result.output  # VIOLATED blocks under any policy


# ---------------------------------------------------------------- a deleted regression test never drops its claim

@pytest.mark.asyncio
async def test_a_candidate_deleting_the_named_regression_test_cannot_close_on_behaviour_alone(tmp_path):
    """"Implement X, keeping T passing": the candidate deletes T. Judged
    against the candidate's files alone, the statement would claim only
    behaviour, and a passing acceptance case would close it."""
    from test_prd020_requirement_lineage import _git_base

    from kriya.workflow.workflow import close_requirements_with_acceptance_tests

    goal = CALC_GOAL.replace(".\n", ", keeping tests/test_calc.py passing.\n")
    workspace = _git_base(tmp_path / "ws", {"calc/__init__.py": "def double(x):\n    return 0\n",
                                            "tests/__init__.py": "", "tests/test_calc.py": "def test_c():\n    pass\n"})
    candidate = tmp_path / "candidate"
    import shutil
    import subprocess
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(candidate)], cwd=workspace, check=True)
    (candidate / "calc" / "__init__.py").write_text(CALC[True])
    (candidate / "tests" / "test_calc.py").unlink()
    reqs, ledger = _ledger(goal)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, goal)
    [attempt] = close_requirements_with_acceptance_tests(
        AutonomyConfig(), ledger, reqs, str(candidate), str(workspace), acceptance=artifact,
        modified=["calc/__init__.py", "tests/test_calc.py"], revision=1, toolchain_declaration_mutable=False)
    assert attempt["reason_code"] == ao.ACCEPTANCE_PASSED
    assert attempt["claims"] == [BEHAVIOR, REGRESSION_PRESERVATION]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED
    shutil.rmtree(candidate)


def test_unknown_pre_existing_tests_hold_every_statement_to_both_claims(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL)
    [attempt] = ao.close_requirements_with_acceptance(
        ledger, reqs, _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL), test_files=None,
        execute=lambda a: _run(a, root), source="test", revision=1)
    assert attempt["reason_code"] == ao.ACCEPTANCE_PASSED and attempt["claims"] == [BEHAVIOR, REGRESSION_PRESERVATION]
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_raising_run_records_indeterminate_and_revokes_an_earlier_pass(tmp_path):
    root = calc_project(tmp_path / "ws", True)
    reqs, ledger = _ledger(CALC_GOAL)
    artifact = _artifact(tmp_path, CALC_ACCEPTANCE, CALC_GOAL)
    _close(ledger, reqs, artifact, root)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE

    def boom(_artifact):
        raise RuntimeError("container setup failed")

    with pytest.raises(RuntimeError):
        ao.close_requirements_with_acceptance(ledger, reqs, artifact, test_files=[], execute=boom, source="test",
                                              revision=2)
    assert requirement_outcomes(ledger, reqs)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_reference_test_files_normal_and_unreadable_base(tmp_path, monkeypatch):
    """The workspace + base test files on the normal path (rule 4: the broad
    catch must not hide a coding error there); an unreadable base is unknown."""
    from test_prd020_requirement_lineage import _git_base

    from kriya.workflow import workflow as workflow_module

    workspace = _git_base(tmp_path / "ws", {"calc/__init__.py": "", "tests/test_calc.py": "def test_c():\n    pass\n"})
    candidate = calc_project(tmp_path / "candidate", True)
    assert workflow_module._reference_test_files(str(candidate), str(workspace)) == ["tests/test_calc.py"]
    in_place = workflow_module._reference_test_files(str(workspace), str(workspace))
    assert in_place == ["tests/test_calc.py"]  # the base's files when the candidate is the workspace

    def unreadable(*_args, **_kwargs):
        raise OSError("base unreadable")

    monkeypatch.setattr("kriya.workflow.named_test_oracle.BaseTree", unreadable)
    assert workflow_module._reference_test_files(str(workspace), str(workspace)) is None
