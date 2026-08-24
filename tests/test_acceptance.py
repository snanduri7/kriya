from kriya.workflow.acceptance import (
    goal_explicitly_requires_tests,
    output_confirms_nonzero_test_execution,
)


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


def test_test_acceptance_detects_known_zero_execution_outputs():
    assert not output_confirms_nonzero_test_execution("collected 0 items")
    assert not output_confirms_nonzero_test_execution("Tests run: 0, Failures: 0")
    assert not output_confirms_nonzero_test_execution("Task :test NO-SOURCE")
    assert output_confirms_nonzero_test_execution("3 passed in 0.04s")
    # Unknown runner output is not guessed at; its successful exit remains valid.
    assert output_confirms_nonzero_test_execution("custom test command completed")
