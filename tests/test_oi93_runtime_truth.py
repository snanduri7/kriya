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
import _enforce_unit_harness as harness
import pytest
from test_greenfield_runtime_verification_dropped import GOAL, JUDGE, UNITS, _plan, _run

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
