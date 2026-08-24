"""MA7.5: find_established_stack_drift (kriya/workflow/static_checks.py) -
MA6 spec section 72's permanent regression category "Django goal doesn't
drift to Spring, Python goal doesn't invent Maven layout" - generalized to
one marker-based rule with no per-framework-pair logic, per explicit user
direction ("we cannot afford tests for all kinds of combinations, the
design should handle it")."""

from kriya.workflow.static_checks import find_established_stack_drift


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
    mismatch on a first milestone is a real, different, out-of-scope gap
    (see this function's own docstring)."""
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
