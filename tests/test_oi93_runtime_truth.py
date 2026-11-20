"""OI-93 (WAVE8-OI93-RUNTIME-INTENT-FALSE-SUCCESS-001): runtime-truthfulness of a terminal SUCCESS.

Two layers, deliberately separate so a classifier fix can never hide a terminal-state hole:

1. THE TERMINAL INVARIANT (this file, first half). When the goal requires observable runtime behaviour (``goal_requires_runtime_behavior`` is True) and
   no authoritative runtime verification was actually performed and satisfied, the terminal status is never ``success``. It is pinned here through the REAL
   controller/workflow (only the model transport and toolchain gates are stubbed, tests/_enforce_unit_harness.py) and independently of how well the
   Planner behaved: a plan with no runtime step, a runtime step whose judge says "do not run", and run-verification switched off all have to end
   not-success. Every case asserts the invariant itself (success implies the application really ran), never just one status string.
2. THE CLASSIFIER (second half). ``goal_requires_runtime_behavior`` recognises an execution-condition clause ("prints X when executed", "It prints X
   when run") when the executable artifact is established by the goal, and does not turn library/API behaviour, documentation, comments, compile-only
   requests or negated requests into a standalone runtime obligation. Reproduced pre-fix at d7afeb0 (MEASURED): three phrasings reached terminal success
   without the application being executed.
"""
import json
from pathlib import Path
from unittest.mock import patch

import _enforce_unit_harness as harness
import pytest
from test_greenfield_runtime_verification_dropped import GOAL, JUDGE, POM, UNITS, _plan, _run

from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.plan_schema import EngineeringPlan


def _plan_without_runtime_step():
    """The Planner "forgot" the runtime verifier: s4 keeps its compile check but carries no application_runtime judgment."""
    data = _plan().model_dump(mode="json")
    for subtask in data["subtasks"]:
        subtask["verification"] = [v for v in subtask["verification"] if not v.get("requires_runtime_execution") and v.get("verifier_kind") != "application_runtime"]
    return EngineeringPlan.model_validate(data)


def _run_with(tmp_path, *, plan, judge=JUDGE, run_verification=True, goal=GOAL):
    return harness.run_enforce(tmp_path, plan=plan, units=UNITS, goal=goal, run_verification=run_verification, judge_reply=judge, app_output="Hello from Kriya\n")


def _assert_no_success_without_execution(run):
    """THE invariant: success => the application was really executed."""
    status = run.result.legacy_result["status"]
    assert not (status == "success" and not run.executed), f"terminal SUCCESS without the required runtime execution (status={status!r}, executed={run.executed!r})"
    return status


# ------------------------------------------------------------------------------------------------------------- 1. the terminal invariant

def test_the_goal_under_test_requires_runtime_behaviour():
    assert goal_requires_runtime_behavior(GOAL) is True


def test_control_the_complete_plan_runs_the_application_and_succeeds(tmp_path):
    run = _run(tmp_path)
    assert run.executed and _assert_no_success_without_execution(run) == "success"


def test_a_plan_with_no_runtime_step_cannot_end_in_success(tmp_path):
    """Classifier right + Planner forgot the runtime step must not equal a false success."""
    run = _run_with(tmp_path, plan=_plan_without_runtime_step())
    assert _assert_no_success_without_execution(run) != "success"
    assert run.executed == []


def test_a_runtime_step_whose_judge_declines_to_run_cannot_end_in_success(tmp_path):
    run = _run_with(tmp_path, plan=_plan(), judge=dict(JUDGE, should_run=False, run_commands=[]))
    assert _assert_no_success_without_execution(run) != "success"


def test_run_verification_switched_off_cannot_end_in_success(tmp_path):
    run = _run_with(tmp_path, plan=_plan(), run_verification=False)
    assert _assert_no_success_without_execution(run) != "success"


def test_the_runtime_command_approval_declined_cannot_end_in_success(tmp_path):
    run = _run(tmp_path, callback=lambda files, reason: bool(files))     # source changes approved, the runtime-command approval (no files) declined
    assert _assert_no_success_without_execution(run) != "success"
    assert run.executed == []


# ------------------------------------------------------------------------------------------------------------------- 2. the classifier

REQUIRED = [
    # an executable artifact established by the goal, then an execution-condition clause about it (the OI-93 defect: the subject is a pronoun)
    "prints hello world when executed",
    "Create a small Java 17 Maven application named hello-kriya. It prints hello world when executed. Include a unit test.",
    "Create hello-kriya, a small Java 17 Maven project, that prints hello world when executed, and include a unit test.",
    "Create an application. It prints hello when run.",
    "Create a CLI that outputs hello when executed.",
    "Build a command-line tool. When launched it displays the version.",
    "Write a script. It should print the sum when invoked.",
    "Create a Spring Boot service. It responds with OK when started.",
    "The generated service should start and respond with HTTP 200.",
    "Create a program that returns 42 when invoked.",
    "Create a CLI tool with a helper function. It prints hello when run.",       # an executable named anywhere in the antecedent keeps the uncertain reading on the verifying side
    "Create a CLI tool 'greet' that, when executed with --name Bob, prints Hello Bob.",
    "Fix the app so it prints hello when run.",                                  # a repair that STATES the wanted behaviour
    "Add logging so the service prints a line when started.",
    # the pre-existing positive forms keep working
    'Create a small Java 17 Maven application named hello-kriya that prints "Hello from Kriya" and includes a unit test.',
    "Run the application and confirm it prints done.",
]
NOT_REQUIRED = [
    "The method returns 0 when invoked with empty input.",
    "The API method returns Foo.",
    "Write a function that returns the sum when called with two numbers.",
    "Add a unit test asserting the function returns 0 when invoked with an empty list.",
    "Document what happens when the application runs.",
    "Add a comment noting that the program prints a banner when executed.",
    "Create a small Java 17 Maven application and include a unit test. Make the application runnable.",
    "Add a static analysis check; do not run the application.",
    "Fix the application that prints the wrong total.",
    "Refactor the logger. Compile only, no need to execute anything.",
    "Add a helper function. It prints a banner when executed.",                  # the pronoun's antecedent is a code construct
    "Implement a logger class that prints messages when run in debug mode.",
    "Make the tests print verbose output when run.",
    "Create a Java library. It exposes a Parser class. The parser returns a Token when invoked.",
    "Implement the REST endpoint /ping. It returns pong when invoked.",
    "Create an application. It prints hello when run. No runtime execution is required.",   # explicit negation wins for the whole goal
    "Fix the bug where the report prints a wrong value when executed.",         # describes the defect, states no requirement
]


@pytest.mark.parametrize("goal", REQUIRED)
def test_execution_condition_language_requires_runtime_verification(goal):
    assert goal_requires_runtime_behavior(goal) is True, goal


@pytest.mark.parametrize("goal", NOT_REQUIRED)
def test_library_documentation_and_compile_only_wording_does_not(goal):
    assert goal_requires_runtime_behavior(goal) is False, goal


# ------------------------------------------------------------ NO-CHANGE-RUNTIME-PROOF-OWNER-001 (Stage C runs 1 and 6)
#
# MEASURED (run 20261120T070741-bcce2d0b): the plan gave pom.xml a second unit (s6) whose only criterion (ac3, judgment) can be proven only by
# running the application - the job of the later verification unit s7, which depends on s6. s1 had already written the whole pom; s6
# returned it byte-identical, passed its gates and was refused VERIFIED_NO_CHANGE_REFUSED until its retry budgets ran out. s7 never ran.
# The accepted candidate of that run passes the sealed oracle (owner diagnostic, 2026-11-20): only this dead end stopped it.
#
# Now a unit that changed nothing completes as NO_CHANGE_DEFERRED_TO_RUNTIME_VERIFIER when every criterion it did not prove is owned by a
# later runtime-verification unit depending on it. Nothing is marked proven. These tests go through the REAL controller (scheduling,
# completion, recovery, invalidation, resume and the terminal status are Kriya's own); only the model transport, the toolchain gates and
# the application's exit/output are scripted. The invariant of this file holds throughout: success implies the application really ran.

DEFERRED = "NO_CHANGE_DEFERRED_TO_RUNTIME_VERIFIER"
REDUNDANT = "s3b"
FIXED_POM = "<project><!-- fixed --></project>\n"


def _plan_with_redundant_pom_unit(*, extra_file=None, verifier_claims="ac1"):
    """The hello-kriya plan with run 6's relationship: a second pom.xml unit (compile-only, judgment criterion ac1) between the application
    unit and the runtime verification unit, which depends on the application unit and on it (run 6: s7 depends on s4 and s6)."""
    data = _plan().model_dump(mode="json")
    redundant = {"id": REDUNDANT, "description": "Configure the build to run the application", "execution_method": "model",
                 "planned_files": [{"path": POM, "action": "create"}] + ([{"path": extra_file, "action": "create"}] if extra_file else []),
                 "depends_on": ["s1", "s2"], "provides": ["p_s3b"], "requires": ["p_s1", "p_s2"], "acceptance_criteria_ids": ["ac1"],
                 "relevant_global_invariant_ids": ["gi1"], "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}
    verifier = data["subtasks"][-1]
    verifier["depends_on"], verifier["requires"] = ["s2", REDUNDANT], ["p_s2", "p_s3b"]
    if verifier_claims != "ac1":
        data["acceptance_criteria"].append({"id": verifier_claims, "description": "another criterion", "method": "judgment"})
        verifier["acceptance_criteria_ids"] = [verifier_claims]
    data["subtasks"].insert(len(data["subtasks"]) - 1, redundant)
    return EngineeringPlan.model_validate(data)


def _deferred(run):
    return [event for event in run.events if event.kind == "unit.no_change_deferred"]


def _statuses(run):
    return {result.subtask_id: result.status.value for result in run.result.subtask_results}


def _why(run):
    legacy = run.result.legacy_result
    return (f"status={legacy.get('status')!r} error={str(legacy.get('error'))[:300]!r} statuses={_statuses(run)} developer_calls={run.developer_calls} "
            f"executed={len(run.executed)} deferred={[(e.details.get('subtask'), e.details.get('deferred_to')) for e in _deferred(run)]}")


def _redundant_run(tmp_path, *, plan=None, units=None, **kwargs):
    kwargs.setdefault("judge_reply", JUDGE)
    kwargs.setdefault("run_verification", True)
    return harness.run_enforce(tmp_path, plan=plan or _plan_with_redundant_pom_unit(), units=units or {**UNITS, REDUNDANT: (POM, UNITS["s1"][1])},
                               goal=GOAL, app_output="Hello from Kriya\n", **kwargs)


def test_run6_shape_an_unchanged_unit_defers_its_runtime_criterion_to_the_later_verifier_which_then_really_runs(tmp_path):
    run = _redundant_run(tmp_path)
    [event] = _deferred(run)
    assert event.details["subtask"] == REDUNDANT and event.details["deferred_to"] == {"ac1": "s4"}, _why(run)
    assert event.details["criteria"] == {"ac1": "UNCOVERED"} and event.details["acceptance_coverage"] == [], _why(run)   # nothing is marked proven
    assert event.details["kind"] == DEFERRED and event.details["partial"] is False and event.details["written_paths"] == []
    assert not [e for e in run.events if e.kind == "unit.verified_no_change" and e.details.get("subtask") == REDUNDANT]
    assert run.developer_calls.count(REDUNDANT) == 1, f"no repair retry on a correct unchanged file - {_why(run)}"
    assert _statuses(run) == {"s1": "completed", "s2": "completed", "s3": "completed", REDUNDANT: "completed", "s4": "completed"}, _why(run)
    [redundant] = [result for result in run.result.subtask_results if result.subtask_id == REDUNDANT]
    assert DEFERRED in redundant.reason_codes and "VERIFIED_NO_CHANGE" not in redundant.reason_codes, redundant.reason_codes
    assert run.executed and _assert_no_success_without_execution(run) == "success", _why(run)


@pytest.mark.parametrize("case", ["judge_declines", "runtime_disabled"])
def test_a_deferred_criterion_is_never_satisfied_without_the_verifiers_execution(tmp_path, case):
    """The deferral proves nothing: when the later verifier does not execute the application, the run does not succeed."""
    kwargs = {"judge_reply": dict(JUDGE, should_run=False, run_commands=[])} if case == "judge_declines" else {"run_verification": False}
    run = _redundant_run(tmp_path, **kwargs)
    assert _assert_no_success_without_execution(run) != "success", _why(run)
    assert run.executed == [] and _statuses(run).get("s4") != "completed", _why(run)
    assert [e.details["subtask"] for e in _deferred(run)] == [REDUNDANT], _why(run)          # the unit did defer; that changed nothing about the verdict


def test_when_the_later_runtime_verifier_claims_another_criterion_the_unchanged_unit_is_refused_as_before(tmp_path):
    """Negative control through the controller: the runtime verifier exists and depends on the unit, but does not claim the unit's
    criterion. Nothing is deferred; the existing refusal stands and the run does not succeed."""
    run = _redundant_run(tmp_path, plan=_plan_with_redundant_pom_unit(verifier_claims="ac9"))
    assert _deferred(run) == [], _why(run)
    assert _statuses(run).get(REDUNDANT) != "completed" and run.result.legacy_result["status"] != "success", _why(run)
    refused = [e for e in run.events if e.kind == "unit.verified_no_change_refused"]
    assert refused and refused[0].details["refusal"]["code"] == "ACCEPTANCE_COVERAGE_INCOMPLETE", _why(run)
    assert run.executed == [], _why(run)


def test_a_unit_that_also_writes_a_file_is_an_ordinary_mutation_and_defers_nothing(tmp_path):
    """Partial writes: the unit creates one planned file and returns the pom unchanged. It makes no no-change proposal at all (it is judged
    like every mutating unit), so there is neither a deferred event nor a deferred completion reason."""
    script = "BUILD-NOTES.md"
    run = _redundant_run(tmp_path, plan=_plan_with_redundant_pom_unit(extra_file=script),
                         units={**UNITS, REDUNDANT: {POM: UNITS["s1"][1], script: "Run with: mvn -q exec:java\n"}})
    assert _deferred(run) == [], _why(run)
    [redundant] = [result for result in run.result.subtask_results if result.subtask_id == REDUNDANT]
    assert DEFERRED not in redundant.reason_codes and redundant.status.value == "completed", (redundant.reason_codes, _why(run))
    assert run.executed and _assert_no_success_without_execution(run) == "success", _why(run)


def _app_fails_until_the_pom_is_fixed(validator, commands):
    pom = (Path(validator.workspace_path) / POM).read_text(encoding="utf-8")
    if "fixed" in pom:
        return None
    output = "[ERROR] Failed to execute goal org.codehaus.mojo:exec-maven-plugin:3.1.0:java (default-cli) on project hello-kriya: build configuration error\n"
    return {"success": False, "returncode": 1, "output": output, "timed_out": False,
            "steps": [{"command": list(command), "exit_code": 1, "stdout": "", "stderr": output, "timed_out": False} for command in commands]}


def test_a_failing_verifier_after_a_deferral_refuses_success_and_recovery_reaches_the_unit_that_really_wrote_the_file(tmp_path):
    """The verifier's application run fails until pom.xml changes. Only s1 ever wrote pom.xml (the deferred unit wrote nothing), and only s1's
    reopened answer changes it. The run may end green only after that change, a fresh pass of the deferred unit's own gates and a real,
    passing application run."""
    poms = iter([UNITS["s1"][1], FIXED_POM])
    last = {"pom": UNITS["s1"][1]}

    def s1_answer():
        last["pom"] = next(poms, FIXED_POM)
        return last["pom"]

    units = {**UNITS, "s1": (POM, s1_answer), REDUNDANT: (POM, lambda: last["pom"])}      # the redundant unit always returns the pom as it stands
    run = _redundant_run(tmp_path, units=units, on_run=_app_fails_until_the_pom_is_fixed)
    assert len(run.executed) >= 2, f"the application ran, failed, and ran again - {_why(run)}"
    assert run.developer_calls.count("s1") == 2, f"the unit that wrote pom.xml was reopened once - {_why(run)}"
    assert run.developer_calls.index(REDUNDANT) < len(run.developer_calls) - 1 and len(_deferred(run)) >= 1, _why(run)
    assert (run.workspace / POM).read_text(encoding="utf-8") == FIXED_POM, _why(run)
    assert _assert_no_success_without_execution(run) == "success", _why(run)
    assert _statuses(run) == {"s1": "completed", "s2": "completed", "s3": "completed", REDUNDANT: "completed", "s4": "completed"}, _why(run)


def test_a_verifier_that_keeps_failing_after_a_deferral_never_ends_in_success(tmp_path):
    def always_fails(validator, commands):
        del validator, commands
        return {"success": False, "returncode": 1, "output": "Exception in thread \"main\" java.lang.IllegalStateException\n", "timed_out": False}

    run = _redundant_run(tmp_path, on_run=always_fails)
    assert run.executed and run.result.legacy_result["status"] != "success", _why(run)
    assert _statuses(run).get("s4") != "completed", _why(run)


def test_resume_after_a_deferral_runs_the_unchanged_unit_again_and_the_verifier_is_still_mandatory(tmp_path):
    """Run 1 stops at the verifier (its judge declines to run). The deferred unit wrote nothing, so the accepted-work snapshot holds no entry
    for it: on resume it is generated and judged again - never carried as completed - and the verifier still has to execute."""
    first = _redundant_run(tmp_path, judge_reply=dict(JUDGE, should_run=False, run_commands=[]))
    assert first.result.legacy_result["status"] != "success" and first.executed == [], _why(first)
    resumed = _redundant_run(tmp_path, existing_workspace=first.workspace, execute_kwargs={"resume": True})
    decision = resumed.result.legacy_result.get("resume_decision") or {}
    assert REDUNDANT not in (decision.get("replayable_subtasks") or []), decision
    assert resumed.developer_calls.count(REDUNDANT) == 1, f"the deferred unit is asked again on resume - {_why(resumed)} decision={decision}"
    assert [e.details["subtask"] for e in _deferred(resumed)] == [REDUNDANT], _why(resumed)
    assert resumed.executed and _assert_no_success_without_execution(resumed) == "success", f"{_why(resumed)} decision={decision}"


RUN6 = Path(__file__).parent / "fixtures" / "stage_c_run6"


def test_run6_replay_the_saved_plan_and_the_accepted_bytes_reach_the_runtime_verifier(tmp_path):
    """The saved run-6 plan (tests/fixtures/stage_c_run6/plan.json) with the run's accepted file bytes as the Developer's answers (rebuilt from
    the sealed attempt evidence, digest-checked: accepted-attempt-manifest.json) and the pom returned byte-identical by s6, as in the run.
    s6 defers ac3 to s7; s7 executes the application; the run completes. The application's exit and output are scripted."""
    plan = EngineeringPlan.model_validate(json.loads((RUN6 / "plan.json").read_text(encoding="utf-8")))
    manifest = json.loads((RUN6 / "accepted-attempt-manifest.json").read_text(encoding="utf-8"))
    accepted = {entry["path"]: (RUN6 / "candidate" / entry["path"]).read_text(encoding="utf-8") for entry in manifest["entries"] if entry.get("accepted")}
    units = {}
    for subtask in plan.subtasks:
        paths = [planned.path for planned in subtask.planned_files]
        if len(paths) == 1:
            units[subtask.id] = (paths[0], accepted[paths[0]])
        elif paths:
            units[subtask.id] = {path: accepted[path] for path in paths}
    assert units["s6"] == units["s1"] and "s7" not in units                       # s6 returns s1's pom byte for byte; s7 owns no file
    result_line = "[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true"
    judge = dict(JUDGE, run_commands=[["mvn", "-q", "exec:exec"]], command_source="inferred", success_criteria=f"The output contains {result_line!r}")
    with patch("kriya.tools.lsp.find_jdtls", return_value=None):
        run = harness.run_enforce(tmp_path, plan=plan, units=units, goal=(RUN6 / "goal.md").read_text(encoding="utf-8"), run_verification=True,
                                  judge_reply=judge, app_output=result_line + "\n", execute_kwargs={"knowledge_risk_confirmed": True},
                                  configure=lambda cfg: setattr(cfg.knowledge, "offline_mode", True))
    [event] = _deferred(run)
    assert event.details["subtask"] == "s6" and event.details["deferred_to"] == {"ac3": "s7"}, _why(run)
    assert event.details["criteria"] == {"ac3": "UNCOVERED"} and event.details["acceptance_coverage"] == [], _why(run)
    assert run.developer_calls.count("s6") == 1, _why(run)
    assert _statuses(run).get("s6") == "completed" and _statuses(run).get("s7") == "completed", _why(run)
    assert run.executed and _assert_no_success_without_execution(run) == "success", _why(run)
    assert {path: (run.workspace / path).read_text(encoding="utf-8") for path in accepted} == accepted, _why(run)

