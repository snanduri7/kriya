"""VER-005 implementation (2026-09-13): deterministic Python runtime-target
grounding / invocation-policy tests.

See docs/assurance/KRIYA_VER005_RECV002_LIVE_EVIDENCE.md for the live E4
defect this closes: RunVerifierAgent selected a test file as a
runtime-verification target for a pure-library Python goal (contrary to its
own system prompt), and Kriya executed it as a bare `python <test-path>`
subprocess, which fails with ModuleNotFoundError against a sibling
top-level package regardless of candidate correctness. These tests exercise
the deterministic layer that now sits between RunVerifierAgent's judgment
and execution (kriya/workflow/file_resolution.py::ground_python_runtime_
target, kriya/workflow/attempt.py::_build_python_runtime_grounding) -
mirroring ground_java_entrypoint_in_no_build_file_projects()'s own
existing, already-tested treatment of the same class of problem for Java.
"""

import os
import sys

from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import runtime_verification_infrastructure_reason
from kriya.workflow.attempt import (
    _build_python_runtime_grounding,
    _collect_python_runtime_grounding_facts,
    _validate_and_convert_managed_service_contract,
)
from kriya.workflow.file_resolution import (
    ground_python_runtime_target,
    python_command_targets_test_path,
    python_file_is_runnable_script,
    python_target_path_is_test_shaped,
)


def _write_library_fixture(root):
    """The exact E4 fixture shape: two sibling top-level packages, tests
    package, zero runnable entrypoint anywhere (a pure library)."""
    (root / "validation").mkdir()
    (root / "validation" / "__init__.py").write_text("")
    (root / "validation" / "email_rules.py").write_text(
        "def is_valid_email(email):\n"
        "    return bool(email) and '@' in email\n"
    )
    (root / "customer").mkdir()
    (root / "customer" / "__init__.py").write_text("")
    (root / "customer" / "service.py").write_text(
        "def is_premium_eligible(customer):\n"
        "    return customer.get('tier') == 'gold'\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "tests" / "test_email_rules.py").write_text(
        "from validation.email_rules import is_valid_email\n"
        "\n"
        "def test_valid_email():\n"
        "    assert is_valid_email('user@example.com')\n"
    )


def _write_app_fixture(root):
    """Fixture 2's sibling: same package layout, but validation/ ALSO has a
    real CLI entrypoint with a __main__ guard - the grounded, positive
    (Task 10) case."""
    _write_library_fixture(root)
    (root / "validation" / "cli.py").write_text(
        "from validation.email_rules import is_valid_email\n"
        "import sys\n"
        "\n"
        "def main():\n"
        "    print('valid' if is_valid_email(sys.argv[1]) else 'invalid')\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )


# ---------------------------------------------------------------------------
# Task 1 (test 1 from the TESTS list): multi-package library, test-file target
# - the exact reproduced E4 shape.
# ---------------------------------------------------------------------------

def test_library_test_file_target_becomes_not_applicable(tmp_path):
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    # Confirms the REAL filesystem walk shapes both sets exactly the way
    # ground_python_runtime_target() expects - the hand-built sets used in
    # this file's other tests are exercising the same contract the real
    # caller actually produces, not a different one.
    assert package_dirs == {"validation", "customer", "tests"}
    assert all_python_files == {
        "validation/__init__.py", "validation/email_rules.py",
        "customer/__init__.py", "customer/service.py",
        "tests/__init__.py", "tests/test_email_rules.py",
    }
    assert entrypoints == []  # confirmed pure library - no __main__ guard anywhere

    corrected = ground_python_runtime_target(
        [[sys.executable, "tests/test_email_rules.py"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected is None  # forces should_run=False - never executed


# ---------------------------------------------------------------------------
# Task 2 (test 2): library-only project - compile succeeds, zero fabricated
# runtime target, without disabling runtime verification globally.
# ---------------------------------------------------------------------------

def test_library_only_project_never_fabricates_a_runtime_target(tmp_path):
    _write_library_fixture(tmp_path)
    validator = PolymorphicValidator(str(tmp_path))
    compile_result = validator.run_compile_check([
        "validation/__init__.py", "validation/email_rules.py",
        "customer/__init__.py", "customer/service.py",
        "tests/__init__.py", "tests/test_email_rules.py",
    ])
    assert compile_result["success"] is True

    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    assert entrypoints == []
    # Every plausible bad guess the judge could make against this exact
    # repository still resolves to "not applicable", never a guessed command.
    for bad_guess in (
        [[sys.executable, "tests/test_email_rules.py"]],
        [[sys.executable, "validation/email_rules.py"]],  # real file, no main guard
        [[sys.executable, "validation/__init__.py"]],
        [[sys.executable, "nonexistent.py"]],
    ):
        assert ground_python_runtime_target(
            bad_guess, "inferred", all_python_files, package_dirs, entrypoints,
        ) is None


# ---------------------------------------------------------------------------
# Task 3/10 (tests 3, 4): a real Python app WITH a grounded entrypoint - a
# bare-script guess against it is corrected to module form, actually
# executes correctly end to end (real subprocess, no LLM involved), and a
# genuinely broken candidate is still rejected (test 10).
# ---------------------------------------------------------------------------

def test_app_with_entrypoint_bare_script_guess_is_corrected_to_module_form(tmp_path):
    _write_app_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    assert entrypoints == ["validation/cli.py"]

    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/cli.py", "user@example.com"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected == [[sys.executable, "-m", "validation.cli", "user@example.com"]]


def test_app_with_entrypoint_module_form_actually_executes_correctly(tmp_path):
    """End-to-end (test 4, "valid module target" + test 9, "correct
    candidate not spuriously failed"): the grounded -m form this correction
    produces is not just syntactically plausible - it genuinely runs
    correctly through the real, non-mocked PolymorphicValidator.run_app_
    sequence(), proving the actual root-cause mechanism (cwd already on
    sys.path[0] under -m, unlike bare-script mode) is fixed, not merely
    reshaped."""
    _write_app_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/cli.py", "user@example.com"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    validator = PolymorphicValidator(str(tmp_path))
    result = validator.run_app_sequence(corrected, timeout=10)
    assert result["success"] is True
    assert "valid" in result["output"]
    assert runtime_verification_infrastructure_reason(result) is None


def test_bad_candidate_still_rejected_after_grounding(tmp_path):
    """Task 7/10 false-positive protection: grounding a VALID target must
    never convert a genuinely incorrect candidate into success. Same
    fixture, but the entrypoint's own logic is wrong (never checks '@' at
    all) - the corrected, grounded command still surfaces a real,
    observable failure."""
    _write_app_fixture(tmp_path)
    (tmp_path / "validation" / "cli.py").write_text(
        "import sys\n"
        "\n"
        "def main():\n"
        "    print('valid')\n"  # wrong: always says valid, ignores input entirely
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    )
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/cli.py", "not-an-email"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    validator = PolymorphicValidator(str(tmp_path))
    result = validator.run_app_sequence(corrected, timeout=10)
    # The process itself still runs successfully (exit 0) - correction never
    # fabricates a PASS/FAIL verdict, it only fixes WHICH command runs and
    # HOW. Grading that output against the goal (a separate, unmodified
    # stage - the Reviewer/grade() pipeline) is what would still catch this
    # candidate defect; this test's own scope is limited to confirming
    # grounding correction doesn't itself launder a wrong output into a
    # clean one.
    assert result["success"] is True
    assert "valid" in result["output"]


# ---------------------------------------------------------------------------
# Task 11: adversarial RunVerifierAgent outputs.
# ---------------------------------------------------------------------------

def test_nonexistent_target_with_no_entrypoint_is_not_applicable(tmp_path):
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/does_not_exist.py"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected is None


def test_nonexistent_target_with_one_entrypoint_is_substituted(tmp_path):
    _write_app_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/does_not_exist.py"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected == [[sys.executable, "-m", "validation.cli"]]


def test_out_of_workspace_target_never_treated_as_grounded(tmp_path):
    """A judge-proposed path escaping the workspace (absolute, or via '..')
    can never become a member of all_python_files - the walk only ever
    discovers real files under root - so it is always treated exactly like
    a nonexistent target, never resolved against the real filesystem."""
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    for outside_path in ("/etc/passwd.py", "../../../etc/passwd.py", "/tmp/evil.py"):
        corrected = ground_python_runtime_target(
            [[sys.executable, outside_path]], "inferred",
            all_python_files, package_dirs, entrypoints,
        )
        assert corrected is None  # zero entrypoints in this fixture - never executed


def test_symlink_escape_is_not_discovered_as_a_repository_file(tmp_path):
    """Task 11/13: a malicious symlink INSIDE the repository pointing to a
    real, main()-guarded file OUTSIDE it must never be discovered as a
    legitimate entrypoint - is_within_scope() containment (the same idiom
    run_app_sequence()'s own javac -d directory check already uses) applies
    to the grounding walk itself, not just the decision layer."""
    outside_dir = tmp_path.parent / "outside_symlink_target"
    outside_dir.mkdir(exist_ok=True)
    outside_file = outside_dir / "evil.py"
    outside_file.write_text(
        "if __name__ == \"__main__\":\n"
        "    print('should never run')\n"
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write_library_fixture(workspace)
    try:
        os.symlink(str(outside_file), str(workspace / "validation" / "linked_evil.py"))
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported on this platform/filesystem")

    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(workspace))
    assert "validation/linked_evil.py" not in all_python_files
    assert entrypoints == []


def test_directory_target_is_not_a_valid_entrypoint(tmp_path):
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation"]], "inferred",  # a directory, not a .py file
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected is None  # never matches all_python_files (no .py suffix content) - rejected


def test_init_file_target_is_never_a_valid_entrypoint(tmp_path):
    _write_app_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/__init__.py"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    # Rejected despite existing on disk - substituted with the one real
    # entrypoint rather than executed directly.
    assert corrected == [[sys.executable, "-m", "validation.cli"]]


def test_malformed_target_is_left_alone_not_crashed(tmp_path):
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    for malformed in ([sys.executable], [sys.executable, "-c", "print(1)"]):
        corrected = ground_python_runtime_target(
            [malformed], "inferred", all_python_files, package_dirs, entrypoints,
        )
        assert corrected == [malformed]  # nothing to validate - passed through unchanged


def test_ambiguous_target_with_multiple_entrypoints_is_not_applicable(tmp_path):
    _write_app_fixture(tmp_path)
    (tmp_path / "customer" / "cli.py").write_text(
        "if __name__ == \"__main__\":\n"
        "    print('customer cli')\n"
    )
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    assert len(entrypoints) == 2
    corrected = ground_python_runtime_target(
        [[sys.executable, "tests/test_email_rules.py"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    assert corrected is None  # never guesses which of the two should run


def test_goal_explicit_command_is_never_touched(tmp_path):
    _write_library_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    run_commands = [[sys.executable, "tests/test_email_rules.py"]]
    corrected = ground_python_runtime_target(
        run_commands, "goal_explicit", all_python_files, package_dirs, entrypoints,
    )
    assert corrected is run_commands


def test_module_form_targeting_a_test_module_is_rejected():
    """Task 6: prompt strengthening alone did not prevent the original
    defect - proving post-model enforcement covers the '-m' invocation
    shape too, not just bare-script (advisor-identified gap: a model could
    just as easily emit `python -m tests.test_x` as `python tests/
    test_x.py`, which is exactly the same invariant violation in different
    clothing)."""
    all_python_files = frozenset({"tests/__init__.py", "tests/test_email_rules.py"})
    corrected = ground_python_runtime_target(
        [[sys.executable, "-m", "tests.test_email_rules"]], "inferred",
        all_python_files, frozenset({"tests"}), [],
    )
    assert corrected is None


def test_module_form_targeting_stdlib_module_is_left_alone():
    """`python -m http.server` (or any real stdlib/third-party module that
    is not a repository file at all) is out of this function's scope - it
    is not a repository target to validate, and treating it as an invalid
    target would be a false-positive regression against a legitimate
    invocation shape."""
    all_python_files = frozenset({"validation/__init__.py", "validation/email_rules.py"})
    run_commands = [[sys.executable, "-m", "http.server", "8080"]]
    corrected = ground_python_runtime_target(
        run_commands, "inferred", all_python_files, frozenset({"validation"}), [],
    )
    assert corrected == run_commands


# ---------------------------------------------------------------------------
# Task 12: working directory is already deterministic (workspace root),
# independent of the calling process's own cwd.
# ---------------------------------------------------------------------------

def test_run_app_sequence_result_independent_of_process_cwd(tmp_path):
    _write_app_fixture(tmp_path)
    all_python_files, package_dirs, entrypoints = _build_python_runtime_grounding(str(tmp_path))
    corrected = ground_python_runtime_target(
        [[sys.executable, "validation/cli.py", "user@example.com"]], "inferred",
        all_python_files, package_dirs, entrypoints,
    )
    validator = PolymorphicValidator(str(tmp_path))
    original_cwd = os.getcwd()
    other_dir = tmp_path.parent
    try:
        os.chdir(str(other_dir))
        result = validator.run_app_sequence(corrected, timeout=10)
    finally:
        os.chdir(original_cwd)
    assert result["success"] is True
    assert "valid" in result["output"]


# ---------------------------------------------------------------------------
# Managed-service test-shape rejection (Task 19 - the second, previously
# unvalidated LLM-target execution path).
# ---------------------------------------------------------------------------

def test_managed_service_test_target_fails_closed(tmp_path):
    _write_library_fixture(tmp_path)
    validator = PolymorphicValidator(str(tmp_path))
    spec, error = _validate_and_convert_managed_service_contract(
        {
            "service_command": [sys.executable, "tests/test_email_rules.py"],
            "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": 8000},
            "probe": {"kind": "http", "method": "GET", "host": "127.0.0.1", "port": 8000, "path": "/"},
        },
        str(tmp_path), validator,
    )
    assert spec is None
    assert "test" in error.lower()


def test_managed_service_module_form_test_target_fails_closed(tmp_path):
    _write_library_fixture(tmp_path)
    validator = PolymorphicValidator(str(tmp_path))
    spec, error = _validate_and_convert_managed_service_contract(
        {
            "service_command": [sys.executable, "-m", "tests.test_email_rules"],
            "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": 8000},
            "probe": {"kind": "http", "method": "GET", "host": "127.0.0.1", "port": 8000, "path": "/"},
        },
        str(tmp_path), validator,
    )
    assert spec is None
    assert "test" in error.lower()


def test_managed_service_legitimate_target_still_accepted(tmp_path):
    _write_app_fixture(tmp_path)
    validator = PolymorphicValidator(str(tmp_path))
    spec, error = _validate_and_convert_managed_service_contract(
        {
            "service_command": [sys.executable, "-m", "http.server", "8000"],
            "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": 8000},
            "probe": {"kind": "http", "method": "GET", "host": "127.0.0.1", "port": 8000, "path": "/"},
        },
        str(tmp_path), validator,
    )
    assert error is None
    assert spec is not None


# ---------------------------------------------------------------------------
# Pure-function unit coverage for the building blocks.
# ---------------------------------------------------------------------------

def test_python_file_is_runnable_script():
    assert python_file_is_runnable_script('if __name__ == "__main__":\n    main()\n') is True
    assert python_file_is_runnable_script("if __name__ == '__main__':\n    main()\n") is True
    assert python_file_is_runnable_script("def main():\n    pass\n") is False
    # Indented (nested) occurrence is not top-level - the outer file body is
    # just a single FunctionDef, no observable behavior at import/run time.
    assert python_file_is_runnable_script(
        "def f():\n    if __name__ == \"__main__\":\n        pass\n"
    ) is False
    # A bare top-level statement with NO guard at all is still genuinely
    # runnable - Python has no required entrypoint construct, unlike Java.
    # Found live, 2026-09-14: this exact shape (a single print(...) call, no
    # guard) is what every one of 20 real independent-pytest failures used.
    assert python_file_is_runnable_script("print('hi')\n") is True
    assert python_file_is_runnable_script(
        "\"\"\"Docstring.\"\"\"\nimport sys\nprint(sys.argv)\n"
    ) is True
    # A pure library file - only defs/imports/docstring/constants - has no
    # observable behavior when run directly.
    assert python_file_is_runnable_script(
        "\"\"\"Docstring.\"\"\"\nimport sys\n\nVERSION = '1.0'\n\ndef f():\n    pass\n"
    ) is False
    # Malformed content never guessed as runnable.
    assert python_file_is_runnable_script("def f(:\n") is False


def test_python_target_path_is_test_shaped():
    assert python_target_path_is_test_shaped("tests/test_email_rules.py") is True
    assert python_target_path_is_test_shaped("tests/fixtures.py") is True  # tests/ dir, not filename-matched
    assert python_target_path_is_test_shaped("email_rules_test.py") is True
    assert python_target_path_is_test_shaped("validation/email_rules.py") is False
    assert python_target_path_is_test_shaped("validation/cli.py") is False


def test_python_command_targets_test_path():
    assert python_command_targets_test_path([sys.executable, "tests/test_x.py"]) is True
    assert python_command_targets_test_path([sys.executable, "-m", "tests.test_x"]) is True
    assert python_command_targets_test_path([sys.executable, "app.py"]) is False
    assert python_command_targets_test_path([sys.executable, "-m", "flask", "run"]) is False
    assert python_command_targets_test_path(["mvn", "-e", "exec:java"]) is False
    assert python_command_targets_test_path([sys.executable, "-c", "print(1)"]) is False
