"""GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001: characterisation matrix for ``goal_requires_runtime_behavior``. Each positive states observable behavior that only EXECUTING the artifact
can show (output, exit, service start/response, explicit execution); each negative discusses a runtime concept without requiring Kriya to execute anything, or is a historical
false positive (P2/P3). Not tuned to HelloWorld: the owner goal is one row among many."""
import pytest

from kriya.workflow.acceptance import goal_requires_runtime_behavior

OWNER_GOAL = ("Create a small Java 17 Maven application named hello-kriya that\nprints \"Hello from Kriya\" and includes a unit test.\nMake the application runnable.")

POSITIVE = [
    OWNER_GOAL,
    "Create a small Java 17 Maven application named hello-kriya that prints 'Hello from Kriya' and includes a unit test. Make the application runnable.",
    'The application prints "Hello from Kriya".',
    "The program should print Hello when run.",
    "The executable outputs the greeting.",
    "The service should start and respond with 200 OK.",
    "The command should exit successfully and print the version.",
    "Run the application and confirm the greeting is shown.",
    "The application must execute successfully.",
    "The application should be runnable and produce the report.",
    "Build a CLI tool that displays the current time.",
    "Write a Python script that prints the first ten primes.",
    "The server will start and respond with the build number.",
    "Fix the existing record-formatting behavior so the application prints:\n\nCUSTOMER=<id>:<name>\n\nRequirements:\n- Preserve all public APIs.",   # PRV-13: explicit output requirement
    "Start the Spring Boot application and confirm startup",
    "Execute the application and verify exit code 2",
    "Execute invalid input and verify exit status is non-zero.",
]
NEGATIVE = [
    "before any location check would even run",                                                                                   # P3 prose
    "The invariant is about which employees get their fields changed and get saved, not about what the storage layer happens to iterate over.",   # P2 prose
    "No live application run is required to verify this change.",                                                                  # P3 explicit denial
    "Do not start the application",
    "Application execution is not required",
    "The application should print Hello. No live application run is required to verify this change.",                             # negation wins over a positive
    "Document in the README how a user could run the application.",                                                                # documentation of how to run
    "Add a code comment noting that the formatter will print a trailing newline.",                                                 # comment mentioning print
    "Add a Javadoc explaining that the program will print a banner.",
    "Add a test named runPipelineTest for the parser.",                                                                            # test named run...
    "The Printer class prints a receipt line; refactor its helper.",                                                               # source-code description
    "Fix the application that prints the wrong total in the invoice summary.",                                                     # repair of existing behavior
    "Fix the sprinter and blueprint naming in the parser.",                                                                        # unrelated words containing run/print
    "Rename the print method to render in the Formatter.",
    "Package the module as a jar and update the Maven build instructions.",                                                        # package/build instructions
    "Make the application runnable.",                                                                                              # readiness only, no observable behavior
    "Create a small Java 17 Maven application named hello-kriya and include a unit test. Make the application runnable.",
    "Describe in Javadoc what the application does.",
    "",
]


@pytest.mark.parametrize("goal", POSITIVE)
def test_explicit_runtime_behavior_is_required(goal):
    assert goal_requires_runtime_behavior(goal) is True


@pytest.mark.parametrize("goal", NEGATIVE)
def test_runtime_concepts_without_an_execution_requirement_are_not(goal):
    assert goal_requires_runtime_behavior(goal) is False


def test_a_hard_wrapped_line_is_not_a_sentence_boundary():
    """The owner goal wraps after "that"; splitting there hid the subject from its verb (found by running the real goal text)."""
    assert goal_requires_runtime_behavior("Create an application\nthat prints hello") is True
    assert goal_requires_runtime_behavior("Create a library.\n\nThe application prints hello") is True
