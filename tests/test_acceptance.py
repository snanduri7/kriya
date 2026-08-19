from kriya.workflow.acceptance import (
    goal_explicitly_requires_tests,
    test_output_confirms_nonzero_execution,
)


def test_explicit_test_contract_is_distinct_from_optional_language():
    assert goal_explicitly_requires_tests("Create the service and include unit tests")
    assert goal_explicitly_requires_tests("Build the application with integration tests")
    assert not goal_explicitly_requires_tests("Add tests where appropriate")
    assert not goal_explicitly_requires_tests("Fix the application")


def test_test_acceptance_detects_known_zero_execution_outputs():
    assert not test_output_confirms_nonzero_execution("collected 0 items")
    assert not test_output_confirms_nonzero_execution("Tests run: 0, Failures: 0")
    assert not test_output_confirms_nonzero_execution("Task :test NO-SOURCE")
    assert test_output_confirms_nonzero_execution("3 passed in 0.04s")
    # Unknown runner output is not guessed at; its successful exit remains valid.
    assert test_output_confirms_nonzero_execution("custom test command completed")
