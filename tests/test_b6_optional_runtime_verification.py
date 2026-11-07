"""B6 (walkthrough run 304c8d2e, 2026-11-07): a model-chosen, NOT-required runtime check turned compile PASS + tests PASS into a terminal failure.

MEASURED (saved run evidence, ``~/kriya-walkthrough/state``): goal "Add a function multiply(a, b) to calc.py ... and add a pytest test for it." ``goal_requires_runtime_behavior`` is False, so no
runtime verification was required. The Run Verification Judge nevertheless answered ``should_run`` True with ``python -m pytest test_calc.py -v`` (``command_source`` inferred) and the
direct (unstructured) path executed it as an APPLICATION runtime command: the host-interpreter substitution (``_resolve_run_command``: no bare ``python`` on PATH -> ``sys.executable``,
``.../.venv/bin/python3.14``) made the argv's basename ``python3.14``, which ``deterministic_verification_kind`` did not recognise, so the pytest command lost its ``test`` authority;
the contained runtime venv has no pytest (``No module named pytest``) and the text classifier called that "the verifier infrastructure failed" (STOP_ENVIRONMENT). Reproduced with the exact
recorded argv: the classifier answers None and ``_raise_runtime_verification_infrastructure_failure`` raises.

Three owners, three decisions, each pinned separately:

1. POLICY (``_runtime_verification_is_advisory_only``, the ONE authority both runtime call sites consult): a NOT-required judged sequence that is a deterministic TEST command is not an
   application runtime verification - the test gate already owns that verification (and already passed in this attempt) - so it is not executed. A REQUIRED sequence is untouched.
2. CLASSIFIER (``deterministic_verification_kind``): a version-suffixed or host-path Python interpreter (``python3.14``) is the same interpreter as ``python``/``python3``.
3. OPTIONAL INFRASTRUCTURE (``_raise_runtime_verification_infrastructure_failure``): when the check was NOT required and could not run (verifier infrastructure), nothing about the candidate was
   observed and nothing was required, so it is recorded as an unobserved optional check (``run_verification_declined``, the OI-55 precedent) and the attempt continues to its other gates;
   a REQUIRED check still stops the run exactly as before.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gate_orchestrator as tg  # noqa: E402
import test_runtime_verification as trv  # noqa: E402

from kriya.workflow import attempt, gate_orchestrator  # noqa: E402
from kriya.workflow.verification_authority import (  # noqa: E402
    deterministic_sequence_kind,
    deterministic_verification_kind,
)

PYTEST_RUN = [["python", "-m", "pytest", "test_calc.py", "-v"]]
JUDGE_PYTEST = {**tg.JUDGE_RUN, "run_commands": PYTEST_RUN, "command_source": "inferred", "success_criteria": "The pytest test suite should pass"}


# ------------------------------------------------------------------------------------------------------------------ 2. the classifier
@pytest.mark.parametrize("argv", [
    ["/Users/x/.venv/bin/python3.14", "-m", "pytest", "test_calc.py", "-v"],       # the exact recorded argv shape
    ["python3.12", "-m", "pytest"], ["/usr/bin/python3.12", "-m", "unittest"], ["python3.9.7", "-m", "pytest"],
    ["python", "-m", "pytest"], ["python3", "-m", "pytest"], [".kriya/venv/bin/python", "-m", "pytest", "t.py"], ["python3.14.exe", "-m", "pytest"],
])
def test_a_python_interpreter_of_any_version_keeps_the_test_authority_of_its_module(argv):
    assert deterministic_verification_kind(argv) == "test"


@pytest.mark.parametrize("argv", [
    ["python3.14", "app.py"], ["python3.14", "-m", "http.server"], ["/x/.venv/bin/python3.14", "main.py"],       # an application is never a deterministic verification
    ["pythonx", "-m", "pytest"], ["python3.", "-m", "pytest"], ["python3.x", "-m", "pytest"], ["mypython3.14", "-m", "pytest"], ["python3.14-config", "-m", "pytest"],
])
def test_only_a_python_interpreter_name_is_normalised(argv):
    assert deterministic_verification_kind(argv) is None


def test_the_recorded_b6_argv_is_a_test_sequence_after_the_host_interpreter_substitution():
    resolved = ["/Users/x/.venv/bin/python3.14", "-m", "pytest", "test_calc.py", "-v"]
    assert deterministic_sequence_kind([resolved]) == "test"


# ------------------------------------------------------------------------------------------------------------------ 1. the policy
def _ctx(*, required, structured=None, subtask=None):
    return SimpleNamespace(structured_plan=structured, current_subtask_id=subtask, runtime_verification_required=required)


@pytest.mark.parametrize("structured,subtask", [(None, None), (object(), "s1")])
def test_a_judged_test_command_for_a_goal_with_no_runtime_obligation_is_not_an_application_runtime_check(structured, subtask):
    judgment = {"should_run": True, "run_commands": PYTEST_RUN}
    assert gate_orchestrator._judged_test_command_is_owned_by_the_test_gate(_ctx(required=False, structured=structured, subtask=subtask), judgment) is True


def test_a_required_runtime_check_is_never_suppressed_by_the_test_command_rule():
    assert gate_orchestrator._judged_test_command_is_owned_by_the_test_gate(_ctx(required=True), {"should_run": True, "run_commands": PYTEST_RUN}) is False


@pytest.mark.parametrize("judgment", [
    {"should_run": True, "run_commands": [["python", "main.py"]]},                               # an application: still the judge's call, unchanged
    {"should_run": True, "run_commands": [["mvn", "-q", "compile"]]},                             # build-only: untouched by this rule
    {"should_run": True, "run_commands": [["python", "main.py"], ["pytest"]]},                    # not every command has process authority
    {"should_run": False, "run_commands": PYTEST_RUN},
    {"should_run": True, "run_commands": None}, {"should_run": True, "run_commands": []}, {"should_run": True},
])
def test_only_a_pure_test_command_sequence_is_treated_as_owned_by_the_test_gate(judgment):
    assert gate_orchestrator._judged_test_command_is_owned_by_the_test_gate(_ctx(required=False), judgment) is False


def test_a_maven_test_sequence_is_owned_by_the_test_gate_too():
    assert gate_orchestrator._judged_test_command_is_owned_by_the_test_gate(_ctx(required=False), {"should_run": True, "run_commands": [["mvn", "test"]]}) is True


def _gates(tmp_path, *, required, judge=JUDGE_PYTEST, candidate=None):
    async def case(state, ctx, script):
        ctx.run_verifier.judge = AsyncMock(return_value=judge)
        await tg._executing(state, ctx, script)
    cfg = tg._config(run_verification_enabled=True)
    return tg.run_gates(tmp_path, case, script=tg._Script(), cfg=cfg, candidate=candidate, ctx_overrides=dict(runtime_verification_required=required))


def _executed(form):
    return [call for call in form["seams"] if call.get("seam") == "_approve_and_execute_runtime_verification"]


def test_the_b6_reproducer_an_optional_judged_pytest_run_is_not_executed_and_the_attempt_proceeds(tmp_path):
    form = _gates(tmp_path, required=False)
    assert form["outcome"] == "returned", form["outcome"]
    assert _executed(form) == []
    assert form["state"]["candidate_gates_succeeded"] is True


def test_control_the_same_judgment_for_a_required_runtime_goal_is_still_executed(tmp_path):
    form = _gates(tmp_path, required=True)
    assert len(_executed(form)) == 1


def test_control_an_optional_application_command_is_still_executed(tmp_path):
    form = _gates(tmp_path, required=False, judge=tg.JUDGE_APP, candidate={"calc.py": tg.CALC_NEW, "app.py": tg.PY_APP})
    assert len(_executed(form)) == 1


# ------------------------------------------------------------------------------------------------------------------ 3. optional infrastructure
CRASH = trv._crash("Traceback (most recent call last):\nModuleNotFoundError: No module named 'calcx'\n")


def _verify(tmp_path, *, required, **kwargs):
    run = trv._AttemptRun(tmp_path, required=required, scripted={"app": (trv._app, [CRASH])}, **kwargs)
    return run.call(trv._approve_and_execute)


def test_an_optional_check_that_could_not_run_is_recorded_as_unobserved_and_does_not_fail_the_attempt(tmp_path):
    run = _verify(tmp_path, required=False)
    assert run.outcome == "returned", run.failure
    assert run.state.run_verification_declined is True
    assert not [o for o in run.state.gate_outcomes if o.get("type") == "verification_infrastructure_failure"]
    assert [row for row in run.trace if row.get("model") == "grader"] == []          # nothing was graded: there was nothing to grade


def test_control_a_required_check_that_could_not_run_still_stops_the_run_fail_closed(tmp_path):
    run = _verify(tmp_path, required=True)
    assert run.outcome == "raised" and run.failure.type == "verification_infrastructure_failure"
    assert run.state.run_verification_declined is False


def test_control_an_optional_check_that_RAN_and_failed_is_still_a_failure(tmp_path):
    run = trv._AttemptRun(tmp_path, required=False, scripted={"app": (trv._app, [trv._crash()])}, grades=({"passed": False, "verdict": "FAIL", "reasoning": "NameError", "likely_files": ["calc.py"]},))
    run.call(trv._approve_and_execute)
    assert run.outcome == "raised" and run.failure.type == "run_verification"


# ---- the typed environment stop (PYTHON_ENVIRONMENT_UNAVAILABLE: the project interpreter could not be prepared, nothing ran)
def _unavailable(tmp_path, *, required):
    return trv._AttemptRun(tmp_path, required=required, patches=(trv._environment_unavailable,)).call(trv._approve_and_execute)


def test_an_optional_check_whose_interpreter_could_not_be_prepared_is_unobserved_not_a_failure(tmp_path):
    run = _unavailable(tmp_path, required=False)
    assert run.outcome == "returned", run.failure
    assert run.state.run_verification_declined is True
    assert [e.kind for e in run.state.run_events if e.kind == "runtime_verification.unobserved"] == ["runtime_verification.unobserved"]
    assert not run.state.gate_outcomes


def test_control_a_required_check_whose_interpreter_could_not_be_prepared_still_stops_with_the_typed_environment_failure(tmp_path):
    run = _unavailable(tmp_path, required=True)
    assert run.outcome == "raised" and run.failure.type == "verification_infrastructure_failure"
    assert "PYTHON_ENVIRONMENT_UNAVAILABLE" in run.failure.message
    assert run.state.run_verification_declined is False


def test_the_raiser_is_fail_closed_unless_the_caller_says_the_check_is_optional():
    from unittest.mock import MagicMock

    from kriya.workflow.failure import QualityGateFailure

    result = {"success": False, "output": "ModuleNotFoundError: No module named 'calcx'\n", "steps": [{"exit_code": 1}]}
    state = MagicMock()
    state.attempt_number = 1
    with pytest.raises(QualityGateFailure):
        attempt._raise_runtime_verification_infrastructure_failure(state, result, [["python3", "main.py"]])
    assert attempt._raise_runtime_verification_infrastructure_failure(MagicMock(), result, [["python3", "main.py"]], required=False) is True
    assert attempt._raise_runtime_verification_infrastructure_failure(MagicMock(), {"success": True, "output": "6\n", "steps": [{"exit_code": 0}]}, [["python3", "main.py"]], required=False) is None


def test_the_owner_function_itself_is_fail_closed_by_default():
    """The wrapper on kriya.workflow.attempt has its own default; the owner's default is pinned separately (a mutation of either must fail)."""
    from unittest.mock import MagicMock

    from kriya.workflow import runtime_verification
    from kriya.workflow.failure import QualityGateFailure

    result = {"success": False, "output": "ModuleNotFoundError: No module named 'calcx'\n", "steps": [{"exit_code": 1}]}
    state = MagicMock()
    state.attempt_number = 1
    with pytest.raises(QualityGateFailure):
        runtime_verification._raise_runtime_verification_infrastructure_failure(attempt._runtime_deps(), state, result, [["python3", "main.py"]])
