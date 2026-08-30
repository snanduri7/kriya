from kriya.workflow.acceptance import (
    goal_explicitly_requires_tests,
    output_confirms_nonzero_test_execution,
    subtask_owns_test_obligation,
)
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.triage import ChangeKind


def _plan(subtasks):
    return EngineeringPlan(plan_id="p1", kind=ChangeKind.TASK, subtasks=subtasks)


def _model_subtask(**overrides):
    defaults = dict(id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL)
    defaults.update(overrides)
    return Subtask(**defaults)


# --- subtask_owns_test_obligation (PRV-11, 2026-08-30) ---
#
# Live incident: a Planner subtask (s1, Customer.java) that owns zero test
# responsibility kept failing TEST ACCEPTANCE FAILURE because the goal
# mentions tests SOMEWHERE in the plan (owned entirely by a later subtask,
# s3) - goal_explicitly_requires_tests(ctx.goal) used to be called directly
# and blindly on the full goal text, with no ownership awareness at all.
# Harmless before the authority-isolation fix (ctx.goal was narrowly
# Planner-scoped then); a real defect once ctx.goal legitimately started
# carrying the full top-level goal on every subtask.

def test_top_level_goal_requires_tests_but_other_subtask_owns_them_implementation_subtask_unaffected():
    """s1 owns implementation only; s3 owns "Add unit tests..." with its own
    planned test file. s1 must NOT be required to produce a runnable test
    module - that obligation is FUTURE_ORDERED, owned by s3."""
    s1 = _model_subtask(
        id="s1", description="Modify Customer.java to add a displayName field.",
        planned_files=[PlannedFile(path="Customer.java", action=FileAction.MODIFY)],
    )
    s3 = _model_subtask(
        id="s3", description="Add unit tests for the new displayName behavior in CustomerService.",
        planned_files=[PlannedFile(path="CustomerServiceTest.java", action=FileAction.CREATE)],
    )
    plan = _plan([s1, s3])
    goal = "Add an uppercase displayName. Add tests covering the new behavior."
    assert subtask_owns_test_obligation(goal, plan, "s1") is False


def test_subtask_that_genuinely_owns_the_test_file_still_required_to_produce_it():
    """Same plan as above: s3 itself, executing, IS the owner - test
    acceptance must still apply to it."""
    s1 = _model_subtask(
        id="s1", description="Modify Customer.java to add a displayName field.",
        planned_files=[PlannedFile(path="Customer.java", action=FileAction.MODIFY)],
    )
    s3 = _model_subtask(
        id="s3", description="Add unit tests for the new displayName behavior in CustomerService.",
        planned_files=[PlannedFile(path="CustomerServiceTest.java", action=FileAction.CREATE)],
    )
    plan = _plan([s1, s3])
    goal = "Add an uppercase displayName. Add tests covering the new behavior."
    assert subtask_owns_test_obligation(goal, plan, "s3") is True


def test_plan_scope_recovery_reopening_implementation_subtask_does_not_acquire_test_obligation():
    """The exact PRV-11 shape: s1 is REOPENED by plan-scope recovery
    (its own description now carries an "Authoritative scope revision"
    suffix, and its planned_files grew to include the grounded owner file) -
    the test obligation must remain owned by s3, unchanged by the reopening.
    Recovery never reassigns verification ownership on its own."""
    s1_recovered = _model_subtask(
        id="s1",
        description=(
            "Modify Customer.java to add a displayName field. Authoritative scope "
            "revision: modify grounded owner(s) src/main/java/com/example/customer/CustomerService.java."
        ),
        planned_files=[
            PlannedFile(path="Customer.java", action=FileAction.MODIFY),
            PlannedFile(path="CustomerService.java", action=FileAction.MODIFY),
        ],
    )
    s3 = _model_subtask(
        id="s3", description="Add unit tests for the new displayName behavior in CustomerService.",
        planned_files=[PlannedFile(path="CustomerServiceTest.java", action=FileAction.CREATE)],
    )
    plan = _plan([s1_recovered, s3])
    goal = "Add an uppercase displayName. Add tests covering the new behavior."
    assert subtask_owns_test_obligation(goal, plan, "s1") is False


def test_single_subtask_plan_explicitly_requiring_tests_still_applies():
    """No other subtask exists to defer to - the current (only) subtask
    remains responsible, exactly like the pre-fix behavior for this shape."""
    s1 = _model_subtask(
        id="s1", description="Implement the service and write tests for it.",
        planned_files=[PlannedFile(path="Service.java", action=FileAction.CREATE)],
    )
    plan = _plan([s1])
    goal = "Implement the service. Add tests covering the new behavior."
    assert subtask_owns_test_obligation(goal, plan, "s1") is True


def test_no_plan_or_ownership_metadata_falls_back_to_legacy_full_text_behavior():
    """The ordinary, unpartitioned single-shot pipeline (plan=None,
    current_subtask_id=None) has no ownership state to consult - behaves
    exactly as goal_explicitly_requires_tests(goal) always did."""
    goal_with_tests = "Build the service and include unit tests."
    goal_without_tests = "Build the service."
    assert subtask_owns_test_obligation(goal_with_tests, None, None) is True
    assert subtask_owns_test_obligation(goal_without_tests, None, None) is False


def test_explicit_test_contract_is_distinct_from_optional_language():
    assert goal_explicitly_requires_tests("Create the service and include unit tests")
    assert goal_explicitly_requires_tests("Build the application with integration tests")
    assert not goal_explicitly_requires_tests("Add tests where appropriate")
    assert not goal_explicitly_requires_tests("Fix the application")


def test_explicit_test_contract_ignores_test_used_as_sample_data_adjective():
    """Regression test for a real live bug, 2026-08-21 (protocol_encoder_java,
    milestone 5/5): the Planner's own milestone goal read "initializes it with
    test values, encodes it to byte array, decodes back into new object, and
    prints all field values..." - ordinary English for "some sample values,"
    not a request for a test suite. The old regex matched anyway, because
    "test" alone is a whole-word match for "tests?" (the trailing 's' is
    optional) - "with test values" satisfied "with (appropriate)?
    (unit|integration|automated)? tests?" purely by accident. Quality Gates
    then demanded a runnable test module for a milestone that was never about
    testing at all, burning its entire retry budget on an unwinnable gate
    before the milestone failed outright."""
    assert not goal_explicitly_requires_tests(
        "Write a main class that creates an instance of the protocol class, "
        "initializes it with test values, encodes it to byte array, decodes "
        "back into new object, and prints all field values plus total length "
        "to stdout"
    )
    assert not goal_explicitly_requires_tests("Populate the DB with test data")
    # A genuine request must still be detected even when it also uses "test"
    # as a data adjective elsewhere in the same goal text.
    assert goal_explicitly_requires_tests(
        "Seed the DB with test data and write tests for the importer"
    )


def test_explicit_test_contract_ignores_test_used_as_logic_or_code_adjective():
    """Regression test for a real live bug, 2026-08-24 (protocol_encoder_java,
    WorkflowController enforce mode, subtask s2): the Planner's own subtask
    description read "Create Main.java file with test logic for encode/decode
    round-trip demonstration" and its acceptance criterion read "...with
    working encode/decode round-trip test logic that demonstrates successful
    serialization/deserialization..." - same bug class as the "test values"
    incident above (test as an attributive adjective before a non-suite
    noun), new phrasing ("test logic" rather than "test values"). The subtask
    was never about writing a test suite; Quality Gates demanded a runnable
    test module anyway and burned all 8 retry attempts on an unwinnable gate
    before the subtask failed outright."""
    assert not goal_explicitly_requires_tests(
        "Create Main.java file with test logic for encode/decode round-trip "
        "demonstration"
    )
    assert not goal_explicitly_requires_tests(
        "Main.java file is created with working encode/decode round-trip "
        "test logic that demonstrates successful serialization/"
        "deserialization of Protocol objects"
    )
    assert not goal_explicitly_requires_tests("Add test code to validate the flow")
    assert not goal_explicitly_requires_tests("Write a test routine for the demo")
    # A genuine request must still be detected alongside a "test logic" adjective use.
    assert goal_explicitly_requires_tests(
        "Add test logic to Main.java and write tests for the parser"
    )


def test_explicit_test_contract_ignores_test_used_as_functionality_adjective():
    """Regression test for a real live bug, 2026-08-24 (protocol_encoder_java,
    WorkflowController enforce mode, subtask s2, minutes after the "test
    logic" fix above shipped): the Planner's own subtask description read
    "Create Main.java with test functionality to demonstrate encode/decode
    round-trip for Protocol class" and its acceptance criterion read
    "...with proper encode/decode round-trip test functionality that
    validates Protocol class encoding/decoding logic" - the THIRD real
    live occurrence of the same bug class (test values -> test logic ->
    test functionality), each with a different noun the previous fix's
    blocklist didn't happen to cover. This is why the fix was rebuilt
    structurally (see _EXPLICIT_TEST_REQUEST_RE's own updated docstring) -
    "functionality" was never added to any list; the whole class of
    "singular test + arbitrary noun" no longer matches, period."""
    assert not goal_explicitly_requires_tests(
        "Create Main.java with test functionality to demonstrate encode/decode "
        "round-trip for Protocol class"
    )
    assert not goal_explicitly_requires_tests(
        "Main.java file is created with proper encode/decode round-trip test "
        "functionality that validates Protocol class encoding/decoding logic"
    )


def test_explicit_test_contract_structurally_rejects_any_unqualified_singular_test_noun():
    """The generic case the three regression tests above are each one
    instance of: bare singular "test" followed by ANY noun that isn't
    itself a test-suite artifact must never match, for any noun at all -
    not just the three specific ones (values/logic/functionality) that
    happened to be found live. A future paraphrase of this exact shape
    must not need a fourth regression test to catch it."""
    for noun in ("values", "logic", "functionality", "code", "data", "output", "scenario", "harness", "setup"):
        assert not goal_explicitly_requires_tests(f"Create Main.java with test {noun} for the demo"), noun

    # But a genuinely qualified singular "test" (unit/integration/automated
    # prefix, or a real suite-noun suffix) - or any plural "tests" - must
    # still be detected as a real request, regardless of this fix.
    assert goal_explicitly_requires_tests("add unit test coverage for the parser")
    assert goal_explicitly_requires_tests("include test suite for the parser")
    assert goal_explicitly_requires_tests("write tests for the parser")


def test_test_acceptance_detects_known_zero_execution_outputs():
    assert not output_confirms_nonzero_test_execution("collected 0 items")
    assert not output_confirms_nonzero_test_execution("Tests run: 0, Failures: 0")
    assert not output_confirms_nonzero_test_execution("Task :test NO-SOURCE")
    assert output_confirms_nonzero_test_execution("3 passed in 0.04s")
    # Unknown runner output is not guessed at; its successful exit remains valid.
    assert output_confirms_nonzero_test_execution("custom test command completed")
