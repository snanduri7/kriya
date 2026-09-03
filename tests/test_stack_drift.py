"""MA7.5: find_established_stack_drift (kriya/workflow/static_checks.py) -
MA6 spec section 72's permanent regression category "Django goal doesn't
drift to Spring, Python goal doesn't invent Maven layout" - generalized to
one marker-based rule with no per-framework-pair logic, per explicit user
direction ("we cannot afford tests for all kinds of combinations, the
design should handle it").

Also covers find_goal_stack_mismatch (added 2026-08-25, external review
P1) - the first-milestone counterpart: find_established_stack_drift
structurally cannot fire when nothing is established yet (see
test_brand_new_first_milestone_workspace_never_fires_even_with_a_marker
below); this closes that exact gap using the goal text's own declared
language family instead of established file history."""

from kriya.workflow.static_checks import (
    derive_stack_contract,
    find_established_stack_drift,
    find_goal_stack_mismatch,
    validate_stack_contract_artifacts,
)


def _touch(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")


def test_no_established_markers_never_fires(tmp_path):
    _touch(tmp_path / "app.py")
    result = find_established_stack_drift(str(tmp_path), ["app.py"])
    assert result is None


def test_new_marker_contradicting_an_established_python_project_fires(tmp_path):
    _touch(tmp_path / "requirements.txt")  # established BEFORE this attempt
    _touch(tmp_path / "pom.xml")  # written BY this attempt
    result = find_established_stack_drift(str(tmp_path), ["pom.xml"])
    assert result is not None
    assert "pom.xml" in result
    assert "python" in result
    assert "java (maven)" in result


def test_new_marker_contradicting_an_established_java_project_fires_the_reverse_direction(tmp_path):
    """Confirms the check is truly generic - no hardcoded 'python->java'
    direction, or any named-framework logic at all."""
    _touch(tmp_path / "pom.xml")  # established
    _touch(tmp_path / "requirements.txt")  # newly written
    result = find_established_stack_drift(str(tmp_path), ["requirements.txt"])
    assert result is not None
    assert "requirements.txt" in result
    assert "java (maven)" in result
    assert "python" in result


def test_rewriting_the_same_established_marker_does_not_fire(tmp_path):
    _touch(tmp_path / "pom.xml")
    result = find_established_stack_drift(str(tmp_path), ["pom.xml"])
    assert result is None


def test_adding_ordinary_non_marker_files_to_an_established_project_does_not_fire(tmp_path):
    _touch(tmp_path / "pom.xml")
    _touch(tmp_path / "App.java")
    result = find_established_stack_drift(str(tmp_path), ["App.java"])
    assert result is None


def test_a_second_file_in_the_same_established_ecosystem_does_not_fire(tmp_path):
    _touch(tmp_path / "requirements.txt")
    _touch(tmp_path / "pyproject.toml")  # also python - not a competing ecosystem
    result = find_established_stack_drift(str(tmp_path), ["pyproject.toml"])
    assert result is None


def test_npm_marker_contradicting_established_ruby_project_fires(tmp_path):
    """A third ecosystem pair, still with zero framework-specific logic -
    proves this scales to any combination rather than a fixed list."""
    _touch(tmp_path / "Gemfile")
    _touch(tmp_path / "package.json")
    result = find_established_stack_drift(str(tmp_path), ["package.json"])
    assert result is not None
    assert "npm" in result
    assert "ruby" in result


def test_brand_new_first_milestone_workspace_never_fires_even_with_a_marker(tmp_path):
    """The honest scope boundary: nothing established yet (first
    milestone, EVERYTHING in the worktree is this attempt's own fresh
    write) means nothing to contradict - matches this check's own
    docstring, deliberately not goal-text-based. A goal-text-vs-generated
    mismatch on a first milestone is covered by the separate
    find_goal_stack_mismatch below (closed 2026-08-25), not this function."""
    _touch(tmp_path / "pom.xml")
    _touch(tmp_path / "App.java")
    result = find_established_stack_drift(str(tmp_path), ["pom.xml", "App.java"])
    assert result is None


def test_empty_all_files_written_never_fires(tmp_path):
    _touch(tmp_path / "requirements.txt")
    result = find_established_stack_drift(str(tmp_path), [])
    assert result is None


def test_nonexistent_worktree_path_never_fires():
    result = find_established_stack_drift("/definitely/does/not/exist", ["pom.xml"])
    assert result is None


# ============================================================
# find_goal_stack_mismatch - the first-milestone counterpart, 2026-08-25
# ============================================================

def test_goal_mismatch_fires_when_declared_family_contradicts_a_fresh_marker():
    result = find_goal_stack_mismatch("Build a Django app for managing tasks", ["pom.xml"])
    assert result is not None
    assert "pom.xml" in result
    assert "python" in result
    assert "java" in result


def test_authoritative_django_stack_contract_is_scoped_to_changed_artifacts():
    contract = derive_stack_contract("Build a Python Django application")
    assert contract.languages == ("python",)
    assert contract.frameworks == ("django",)
    assert contract.authority == "USER_GOAL"
    assert validate_stack_contract_artifacts(contract, ["manage.py", "customers/views.py"]) is None
    assert validate_stack_contract_artifacts(contract, ["pom.xml", "src/main/java/App.java"]) is not None
    # A caller supplies only the planned/change boundary; unrelated brownfield
    # artifacts are intentionally outside this decision.
    assert validate_stack_contract_artifacts(contract, ["customers/views.py"]) is None


def test_goal_mismatch_reverse_direction_also_fires():
    """Confirms this is truly generic, no hardcoded direction - mirrors
    find_established_stack_drift's own equivalent test."""
    result = find_goal_stack_mismatch("Build a Spring Boot REST API", ["requirements.txt"])
    assert result is not None
    assert "requirements.txt" in result
    assert "java" in result
    assert "python" in result


def test_goal_mismatch_does_not_fire_when_marker_matches_declared_family():
    assert find_goal_stack_mismatch("Build a Django app", ["requirements.txt"]) is None


def test_goal_mismatch_does_not_fire_with_no_marker_files_at_all():
    assert find_goal_stack_mismatch("Build a Django app", ["app.py", "templates/index.html"]) is None


def test_goal_mismatch_does_not_fire_when_goal_names_no_family():
    """No declared family means nothing to check against - the same
    best-effort, real-evidence-only philosophy as every other check here."""
    assert find_goal_stack_mismatch("Write a script to sum a list of numbers", ["pom.xml"]) is None


def test_goal_mismatch_does_not_fire_on_an_intentionally_mixed_stack_goal():
    """Two DIFFERENT families named in the same goal is ambiguous by
    construction (e.g. a polyglot client/server pair) - not this check's
    business to referee, matching find_established_stack_drift's own
    'best-effort, never a guess' convention."""
    assert find_goal_stack_mismatch("Call this Python service from a Java client", ["pom.xml"]) is None


def test_goal_mismatch_bare_go_the_common_english_word_never_matches():
    """The real risk this check was built to avoid, learned the hard way
    earlier this same session (_EXPLICIT_TEST_REQUEST_RE's three real
    false-positive incidents): a bare common-English word must never be
    treated as a stack declaration. 'go' alone is deliberately excluded
    from the keyword table - only 'golang' counts."""
    assert find_goal_stack_mismatch("Please go ahead and build a task tracker", ["pom.xml"]) is None
    result = find_goal_stack_mismatch("Build a Golang CLI tool", ["pom.xml"])
    assert result is not None
    assert "go" in result


def test_goal_mismatch_javascript_does_not_false_positive_as_java():
    """'java' is matched with a real word boundary - must not match as a
    substring inside 'javascript'."""
    assert find_goal_stack_mismatch("This uses JavaScript on the frontend", ["pom.xml"]) is None


def test_goal_mismatch_only_reports_the_first_violation_found():
    result = find_goal_stack_mismatch("Build a Django app", ["pom.xml", "package.json"])
    assert result is not None
    assert result.count("establishes a") == 1
