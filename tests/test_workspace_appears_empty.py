"""kriya/workflow/triage.py::_workspace_appears_empty - first real test
coverage for this function (tests/test_triage.py's own docstring
explicitly scoped it out: "Deliberately NOT covering... repository signal
detectors here"). Written after two real, live-found bugs (2026-08-24,
protocol_encoder_java): this function counted Kriya's own logs/memory
directories and its own kriya.yaml/goal-text file as "established project
content," silently affecting BOTH its original caller
(EngineeringTriageService.classify's own "repo empty" signal, wrong since
before this session) and plan_validation.py's newer extension_points
exemption (MA7.8) that actually surfaced the bug via two failed live
validation runs in a row."""

from kriya.workflow.triage import _workspace_appears_empty


def _touch(path, content="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_a_genuinely_empty_directory_is_empty(tmp_path):
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_a_nonexistent_path_is_treated_as_empty(tmp_path):
    assert _workspace_appears_empty(str(tmp_path / "does-not-exist")) is True


def test_a_real_source_file_makes_it_not_empty(tmp_path):
    _touch(tmp_path / "App.java", "class App {}")
    assert _workspace_appears_empty(str(tmp_path)) is False


# --- real bug #1: logs/memory directories ---

def test_kriya_logs_directory_alone_still_counts_as_empty(tmp_path):
    _touch(tmp_path / "logs" / "kriya.log", "some log lines")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_kriya_memory_directory_alone_still_counts_as_empty(tmp_path):
    _touch(tmp_path / "memory" / "vector_index.db", "binary-ish content")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_a_real_file_inside_an_otherwise_ignored_looking_subdir_still_counts(tmp_path):
    """logs/memory are ignored at the TOP level - a real project file that
    happens to live under a nested path sharing that name should still be
    caught if it's not actually one of Kriya's own directories. (Narrower
    guard: this test documents current behavior - ignored_dirs matches by
    basename anywhere in the tree, matching this function's own existing,
    pre-2026-08-24 convention for .git/.kriya/node_modules/etc. This is a
    deliberate, accepted tradeoff, not a gap this fix set out to close.)"""
    _touch(tmp_path / "src" / "main.py", "print('hi')")
    assert _workspace_appears_empty(str(tmp_path)) is False


# --- real bug #2: kriya.yaml and a bare goal-text .md file ---

def test_kriya_yaml_alone_still_counts_as_empty(tmp_path):
    _touch(tmp_path / "kriya.yaml", "paths:\n  skills: ./skills\n")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_kriya_yml_alone_still_counts_as_empty(tmp_path):
    _touch(tmp_path / "kriya.yml", "paths:\n  skills: ./skills\n")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_a_bare_goal_md_file_alone_still_counts_as_empty(tmp_path):
    _touch(tmp_path / "goal.md", "Build a thing that does X.")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_a_differently_named_md_goal_file_is_also_excluded_by_shape_not_name(tmp_path):
    """The exclusion is by SHAPE (any bare .md at the root), not a fixed
    "goal.md" name - a caller-chosen filename via `kriya generate -f
    <file>` has no fixed basename to hardcode."""
    _touch(tmp_path / "my_custom_prompt.md", "Build a thing that does X.")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_kriya_yaml_plus_goal_md_together_still_count_as_empty(tmp_path):
    """The exact real shape that caused the live bug: both files present
    together, nothing else."""
    _touch(tmp_path / "kriya.yaml", "paths:\n  skills: ./skills\n")
    _touch(tmp_path / "goal.md", "Build a thing that does X.")
    assert _workspace_appears_empty(str(tmp_path)) is True


def test_a_real_readme_md_does_not_make_a_workspace_with_other_content_appear_empty(tmp_path):
    """The .md exclusion only prevents an md file from being the SOLE
    reason a workspace looks non-empty - real content elsewhere still
    correctly makes it non-empty."""
    _touch(tmp_path / "README.md", "# My Project")
    _touch(tmp_path / "App.java", "class App {}")
    assert _workspace_appears_empty(str(tmp_path)) is False


def test_a_non_md_file_at_the_root_still_makes_it_not_empty(tmp_path):
    """The exclusion is narrow - only .md files and the two exact kriya
    config basenames, nothing broader."""
    _touch(tmp_path / "pom.xml", "<project></project>")
    assert _workspace_appears_empty(str(tmp_path)) is False


def test_a_md_file_nested_in_a_real_subdirectory_still_makes_it_not_empty(tmp_path):
    """The .md exclusion is root-only (is_root check) - a real project's
    own docs/design.md living alongside real code should not silently
    exempt that whole subtree."""
    _touch(tmp_path / "docs" / "design.md", "# Design notes")
    assert _workspace_appears_empty(str(tmp_path)) is False
