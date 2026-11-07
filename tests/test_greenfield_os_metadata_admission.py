"""GREENFIELD-OS-METADATA-BLOCKS-FIRST-RUN-001 (owner sign-off 2026-11-07): a desktop shell's metadata file must not make a brand-new project folder a refused brownfield.

MEASURED (owner walkthrough, run 619b4e7b): Finder wrote ``.DS_Store`` into the freshly created project folder while the first run was starting; ``admit_workspace_for_mutation`` saw "an
entry" and stopped the run with ``BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY`` before any model-originated write. This is a CHANGE TO THE PREMISE of W3-3 U4 ("a non-empty non-Git folder
necessarily holds pre-existing user content"), not a removal of U4: every other entry still refuses (``tests/test_w33_u4_no_git_admission.py`` is untouched and still pins that).

The exemption is exactly ``OS_METADATA_FILE_NAMES`` (``kriya.platform.filesystem_semantics``), case-sensitive, and only as a REGULAR file; it is one definition shared with the skills fingerprint and
the skills shadow copy, and the greenfield bootstrap additionally excludes those names (root-anchored, in the repository's local ``info/exclude``) so they never become project membership.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_w33_u4_no_git_admission as u4  # noqa: E402

from kriya.platform.filesystem_semantics import OS_METADATA_FILE_NAMES  # noqa: E402
from kriya.repository.bound_tree import BoundTreeError  # noqa: E402

NEW_CODE = u4.NEW_CODE


def _admit(path):
    return u4._admit(path)


def _refused(path):
    with pytest.raises(BoundTreeError) as stop:
        _admit(path)
    assert stop.value.reason_code == NEW_CODE
    return stop.value


@pytest.fixture
def clean_git_home(tmp_path_factory, monkeypatch):
    """No global ``core.excludesFile`` (this Mac ignores ``.DS_Store`` globally, which would hide the very thing under test; a customer's machine need not)."""
    home = tmp_path_factory.mktemp("clean-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return home


# ---- the canonical set --------------------------------------------------------------------------------------------------------------------------------------
def test_the_canonical_os_metadata_set_is_exactly_the_three_shell_names():
    assert OS_METADATA_FILE_NAMES == frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def test_the_two_existing_consumers_still_ignore_exactly_what_they_ignored_before():
    """``skills_fingerprint`` feeds resume identity: any drift in its ignored set would turn recorded fingerprints STALE."""
    from kriya.skills import run_shadow
    from kriya.workflow import resume_fingerprints

    assert resume_fingerprints._SKILL_IGNORED_FILES == frozenset({"staged_rules.txt", ".DS_Store", "Thumbs.db", "desktop.ini"})
    assert run_shadow._IGNORED_NAMES == frozenset({".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__"})


# ---- A. the measured case, and the other canonical names ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(OS_METADATA_FILE_NAMES))
def test_A_a_regular_os_metadata_file_alone_is_a_greenfield(tmp_path, name):
    u4._put(tmp_path, name, "finder")
    _admit(tmp_path)
    assert (tmp_path / name).read_text() == "finder"                               # admission never deletes or rewrites it


def test_A_the_measured_folder_state_kriya_state_plus_ds_store_is_a_greenfield(tmp_path):
    u4._put(tmp_path, ".kriya/run.lock", "")
    u4._put(tmp_path, ".DS_Store", "\x00\x00\x00\x01Bud1")
    _admit(tmp_path)


def test_A_ds_store_beside_the_canonical_configuration_is_a_greenfield(tmp_path):
    u4._put(tmp_path, "kriya.yaml", "llm:\n  model: x\n")
    u4._put(tmp_path, ".DS_Store")
    _admit(tmp_path)


# ---- B/C/F/H. real user content still refuses ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("entry", ["README.md", ".gitignore", "app.py", "src/App.java", ".hidden", ".env", ".DS_Store.bak", "Thumbs.db.orig", "desktop.ini.txt", "._.DS_Store", "._app.py"])
def test_B_C_F_H_the_metadata_names_do_not_launder_a_real_entry(tmp_path, entry):
    u4._put(tmp_path, ".DS_Store")
    u4._put(tmp_path, entry)
    error = _refused(tmp_path)
    assert entry.split("/")[0] in error.paths
    assert ".DS_Store" not in error.paths                                          # the refusal names only what blocks, never the exempt file


def test_the_exemption_is_exact_not_a_dotfile_or_prefix_rule(tmp_path):
    for index, entry in enumerate((".ds_store", ".DS_STORE", "thumbs.db", "DESKTOP.INI", ".DS_Store2", "x.DS_Store")):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        u4._put(folder, entry)
        with pytest.raises(BoundTreeError) as stop:
            _admit(folder)
        assert stop.value.reason_code == NEW_CODE, entry


# ---- D/E. the reserved name with another file type is a user entry ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(OS_METADATA_FILE_NAMES))
def test_D_a_directory_carrying_a_reserved_name_is_refused(tmp_path, name):
    (tmp_path / name).mkdir()
    assert name in _refused(tmp_path).paths


@pytest.mark.parametrize("name", sorted(OS_METADATA_FILE_NAMES))
def test_E_a_symlink_carrying_a_reserved_name_is_refused_even_to_a_regular_file(tmp_path, name):
    target = tmp_path.parent / f"{tmp_path.name}-target"
    target.write_text("secret\n")
    os.symlink(target, tmp_path / name)
    assert name in _refused(tmp_path).paths


def test_E_a_dangling_symlink_with_a_reserved_name_is_refused(tmp_path):
    os.symlink(tmp_path / "nowhere", tmp_path / ".DS_Store")
    assert ".DS_Store" in _refused(tmp_path).paths


def test_a_fifo_with_a_reserved_name_is_refused(tmp_path):
    os.mkfifo(tmp_path / ".DS_Store")
    assert ".DS_Store" in _refused(tmp_path).paths


# ---- G. the bootstrap commits nothing and the metadata never becomes project membership ----------------------------------------------------------------------
def _bootstrap(path):
    from kriya.workflow.worktree import _bootstrap_greenfield_repository

    _bootstrap_greenfield_repository(str(path))


def test_G_the_bootstrap_commits_nothing_of_the_metadata_file_and_leaves_it_untouched(tmp_path, clean_git_home):
    u4._put(tmp_path, ".DS_Store", "finder")
    _bootstrap(tmp_path)
    assert (tmp_path / ".git").is_dir() and (tmp_path / ".DS_Store").read_text() == "finder"
    assert u4._git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD") == ""
    assert u4._git(tmp_path, "ls-files") == ""


def test_G_the_metadata_file_is_not_a_member_of_the_bound_tree_even_on_a_machine_with_no_global_ignore(tmp_path, clean_git_home):
    """MEASURED: with a clean HOME, an untracked ``.DS_Store`` after ``git init`` is a ``visible_repository_paths`` member (copied into every gate view). The bootstrap's own exclude removes it."""
    from kriya.repository.snapshot import visible_repository_paths

    u4._put(tmp_path, ".DS_Store")
    _bootstrap(tmp_path)
    assert visible_repository_paths(str(tmp_path)) == frozenset()


def test_G_a_metadata_file_written_by_the_shell_after_the_bootstrap_is_not_a_member_either(tmp_path, clean_git_home):
    from kriya.repository.snapshot import visible_repository_paths

    _bootstrap(tmp_path)
    for name in OS_METADATA_FILE_NAMES:
        u4._put(tmp_path, name)
    u4._put(tmp_path, "app.py", "x = 1\n")
    assert visible_repository_paths(str(tmp_path)) == frozenset({"app.py"})              # a real file is still a member: the exclude is not a blanket


def test_G_the_exclude_is_root_anchored_and_keeps_kriyas_own_entry(tmp_path, clean_git_home):
    from kriya.repository.snapshot import visible_repository_paths

    _bootstrap(tmp_path)
    lines = (tmp_path / ".git" / "info" / "exclude").read_text().splitlines()
    assert ".kriya/" in lines and {f"/{name}" for name in OS_METADATA_FILE_NAMES} <= set(lines)
    u4._put(tmp_path, "sub/.DS_Store")                                                   # a nested one is outside the admission's scope: unchanged behaviour
    assert visible_repository_paths(str(tmp_path)) == frozenset({"sub/.DS_Store"})


def test_G_a_second_bootstrap_pass_does_not_duplicate_the_exclude_entries(tmp_path, clean_git_home):
    _bootstrap(tmp_path)
    first = (tmp_path / ".git" / "info" / "exclude").read_text()
    _bootstrap(tmp_path)
    assert (tmp_path / ".git" / "info" / "exclude").read_text() == first


# ---- H. the original refusal is intact, through the bootstrap and through a real run -------------------------------------------------------------------------
def test_H_the_bootstrap_still_refuses_ds_store_plus_a_user_file_and_creates_no_repository(tmp_path, clean_git_home):
    u4._put(tmp_path, ".DS_Store")
    u4._put(tmp_path, "README.md", "mine\n")
    with pytest.raises(BoundTreeError) as stop:
        _bootstrap(tmp_path)
    assert stop.value.reason_code == NEW_CODE and not (tmp_path / ".git").exists()


def test_the_contract_sealing_admission_accepts_a_metadata_only_folder_with_an_empty_tracked_set(tmp_path):
    from kriya.workflow.requirement_closers import authoritative_tracked_workspace_paths

    u4._put(tmp_path, ".DS_Store")
    assert authoritative_tracked_workspace_paths(str(tmp_path)) == []


# ---- the reproducer: the exact failed workflow through the real direct pipeline ----------------------------------------------------------------------------------
def test_REPRODUCER_a_real_run_on_a_new_folder_the_shell_has_already_written_into_proceeds_and_generates(tmp_path, clean_git_home):
    """Before the fix this returned ``AUTHORITY_EXECUTION_UNAVAILABLE`` / ``BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY`` with ``runtime.requests == []`` (the measured run)."""
    u4._put(tmp_path, ".kriya/run.lock", "")
    u4._put(tmp_path, ".DS_Store", "finder")
    result, runtime = u4._direct(tmp_path, "Create app.py with an add function")
    assert result.get("reason_codes") != ["AUTHORITY_EXECUTION_UNAVAILABLE"], result
    assert "membership_authority" not in result
    assert runtime.count("developer") >= 1                                              # development really started
    assert (tmp_path / ".git").is_dir() and u4._git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD") == ""
    assert (tmp_path / ".DS_Store").read_text() == "finder"


def test_REPRODUCER_control_a_real_run_beside_ds_store_and_a_user_file_is_still_refused_before_any_model_call(tmp_path, clean_git_home):
    u4._put(tmp_path, ".DS_Store")
    u4._put(tmp_path, "README.md", "hello\n")
    before = u4._digest(tmp_path)
    result, runtime = u4._direct(tmp_path, "Create app.py with an add function")
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    assert result["membership_authority"]["reason_code"] == NEW_CODE
    assert runtime.requests == [] and u4._digest(tmp_path) == before and not (tmp_path / ".git").exists()


# ---- the run event the desktop failure card reads ---------------------------------------------------------------------------------------------------------
def test_the_membership_stop_event_details_carry_the_typed_codes_and_bounded_entry_names_only():
    from kriya.workflow.authority_bundle import (
        AUTHORITY_EXECUTION_UNAVAILABLE,
        MEMBERSHIP_EVENT_MAX_ENTRY_NAMES,
        MEMBERSHIP_EVENT_MAX_NAME_LENGTH,
        membership_stop_event_details,
    )

    many = tuple(f"file{n}.txt" for n in range(10)) + ("x" * 400,)
    details = membership_stop_event_details(BoundTreeError(NEW_CODE, "detail with /a/long/absolute/path", many), stage="development")
    assert details == {"reason_code": AUTHORITY_EXECUTION_UNAVAILABLE, "membership_reason_code": NEW_CODE, "stage": "development", "paths": [f"file{n}.txt" for n in range(MEMBERSHIP_EVENT_MAX_ENTRY_NAMES)]}
    long_name = membership_stop_event_details(BoundTreeError(NEW_CODE, "d", ("y" * 400,)), stage="s")["paths"][0]
    assert len(long_name) == MEMBERSHIP_EVENT_MAX_NAME_LENGTH
    assert "/" not in "".join(details["paths"]) and "detail" not in str(details)                       # names, never the message text or a path
    assert membership_stop_event_details(BoundTreeError(NEW_CODE, "d"), stage="s")["paths"] == []


def test_a_refused_real_run_records_the_blocking_names_on_its_run_event(tmp_path, clean_git_home, monkeypatch):
    """Through the real direct pipeline: the event the refused run records (the same event the controller persists to the trace row the desktop reads)."""
    from kriya.workflow.state import GenerationState

    recorded = []
    original = GenerationState.record_event

    def spy(self, event):
        recorded.append(event)
        return original(self, event)

    monkeypatch.setattr(GenerationState, "record_event", spy)
    u4._put(tmp_path, ".DS_Store")
    u4._put(tmp_path, "README.md", "hello\n")
    result, _ = u4._direct(tmp_path, "Create app.py with an add function")
    assert result["reason_codes"] == ["AUTHORITY_EXECUTION_UNAVAILABLE"]
    events = [e for e in recorded if e.kind == "membership_authority.unavailable"]
    assert len(events) == 1 and events[0].details["membership_reason_code"] == NEW_CODE
    assert events[0].details["paths"] == ["README.md"]                                                 # the exempt .DS_Store is not named as blocking
