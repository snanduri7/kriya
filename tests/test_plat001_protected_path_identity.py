"""PLAT-001 (ARCH-PLATFORM-001): a protected file can never be written through
another spelling of its path - a case or Unicode-normalization variant on an
insensitive filesystem, a symlink or a hard link.

Each expectation follows the behaviour of the filesystem the test runs on
(case- and normalization-insensitive on macOS, case-sensitive on Linux), so
both hosts assert the correct result for their own semantics. The shared
primitive's own tests are here too.
"""
import os
import unicodedata

import _path_identity_probes as probes
import pytest
from _path_identity_probes import NFC_NAME, NFD_NAME

from kriya.platform import filesystem_semantics as fs
from kriya.platform.filesystem_semantics import PathIdentity, PathRelation, path_identity, path_relation
from kriya.policy.errors import PolicyDeniedError
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import read_file_revision


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "Workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def case_insensitive(tmp_path):
    return probes.case_insensitive(tmp_path)


@pytest.fixture
def normalization_insensitive(tmp_path):
    return probes.normalization_insensitive(tmp_path)


def _listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _attempt(writer, target):
    """Try to overwrite ``target`` through the writer: True when refused."""
    try:
        writer.commit_file(str(target), "overwritten\n", expected_revision=read_file_revision(str(target)))
    except PolicyDeniedError:
        return True
    return False


# --- PLAT-001: a protected file under another spelling ----------------------

def _protected(workspace, relpath):
    path = workspace / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("the real goal\n")
    return AuthorizedFileWriter(str(workspace), protected_relpaths=[relpath]), path


@pytest.mark.parametrize("variant", ["GOAL.md", "Goal.MD", "gOaL.md"])
def test_plat001_ascii_case_variant(workspace, case_insensitive, variant):
    writer, goal = _protected(workspace, "goal.md")
    refused = _attempt(writer, workspace / variant)
    assert goal.read_text() == "the real goal\n"
    # Refused exactly where the variant is the protected file; on a
    # case-sensitive filesystem it is a different, ordinary file.
    assert refused is case_insensitive


def test_plat001_exact_spelling_is_still_refused(workspace):
    writer, goal = _protected(workspace, "goal.md")
    assert _attempt(writer, goal) is True


@pytest.mark.parametrize("variant", [NFD_NAME, NFD_NAME.upper(), NFC_NAME.upper()])
def test_plat001_unicode_normalization_and_combined_variants(workspace, case_insensitive, normalization_insensitive,
                                                            variant):
    writer, goal = _protected(workspace, NFC_NAME)
    refused = _attempt(writer, workspace / variant)
    assert goal.read_text() == "the real goal\n"
    needs_case = variant.casefold() != variant
    needs_normalization = unicodedata.normalize("NFC", variant) != variant
    aliases = (case_insensitive or not needs_case) and (normalization_insensitive or not needs_normalization)
    assert refused is aliases


def test_plat001_symlink_alias_is_refused_on_every_host(workspace):
    writer, goal = _protected(workspace, "docs/goal.md")
    os.symlink("goal.md", workspace / "docs" / "alias.md")
    os.symlink("docs", workspace / "docs_link")
    assert _attempt(writer, workspace / "docs" / "alias.md") is True
    assert _attempt(writer, workspace / "docs_link" / "goal.md") is True
    assert goal.read_text() == "the real goal\n"


def test_plat001_hard_link_alias_is_refused_on_every_host(workspace):
    writer, goal = _protected(workspace, "goal.md")
    os.link(goal, workspace / "hard.md")
    assert _attempt(writer, workspace / "hard.md") is True
    assert goal.read_text() == "the real goal\n"


def test_plat001_non_existing_protected_leaf_folds_conservatively(workspace):
    # The protected path does not exist yet: its variants are refused on any
    # filesystem (fail closed), while an unrelated new file is allowed.
    writer = AuthorizedFileWriter(str(workspace), protected_relpaths=["new/goal.md"])
    assert writer.authorize(str(workspace / "NEW" / "Goal.md")).reason_code == "GOAL_SOURCE_FILE_PROTECTED"
    assert writer.authorize(str(workspace / "new" / "goal.md")).reason_code == "GOAL_SOURCE_FILE_PROTECTED"
    assert writer.authorize(str(workspace / "new" / "other.md")).reason_code != "GOAL_SOURCE_FILE_PROTECTED"


def test_plat001_ordinary_files_are_unaffected(workspace):
    writer, _goal = _protected(workspace, "goal.md")
    (workspace / "main.py").write_text("x = 1\n")
    assert _attempt(writer, workspace / "main.py") is False
    assert writer.authorize(str(workspace / "goal.md.bak")).reason_code == "PATH_WITHIN_WORKSPACE_ALLOWED"


def test_plat001_unknown_identity_fails_closed(workspace, monkeypatch):
    writer, goal = _protected(workspace, "goal.md")
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path).endswith("goal.md"):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "stat", stat)
    assert writer.authorize(str(workspace / "other.md")).reason_code == "GOAL_SOURCE_FILE_PROTECTED"


def test_plat001_decisions_create_no_file(workspace):
    writer, _goal = _protected(workspace, "goal.md")
    before = _listing(workspace)
    for target in ("GOAL.md", NFD_NAME, "new/deep/x.md", "goal.md"):
        writer.authorize(str(workspace / target))
    assert _listing(workspace) == before


# --- the primitive ------------------------------------------------------------

def test_path_relation_and_identity_on_plain_paths(workspace):
    (workspace / "a.txt").write_text("a")
    assert path_relation(str(workspace), str(workspace)) is PathRelation.WITHIN
    assert path_relation(str(workspace), str(workspace / "missing" / "leaf")) is PathRelation.WITHIN
    assert path_relation(str(workspace), str(workspace.parent)) is PathRelation.OUTSIDE
    assert path_relation(str(workspace / "gone"), str(workspace / "gone" / "x")) is PathRelation.WITHIN
    assert path_relation(str(workspace / "gone"), str(workspace / "other")) is PathRelation.OUTSIDE
    assert path_identity(str(workspace / "a.txt"), str(workspace / "a.txt")) is PathIdentity.SAME
    assert path_identity(str(workspace / "a.txt"), str(workspace / "b.txt")) is PathIdentity.DIFFERENT
    assert path_identity(str(workspace / "x" / "y"), str(workspace / "X" / "Y")) is PathIdentity.SAME
    assert path_identity(str(workspace / "x" / "y"), str(workspace / "x" / "z")) is PathIdentity.DIFFERENT


def test_non_existing_paths_under_different_directories_are_different(workspace):
    (workspace / "a").mkdir()
    (workspace / "b").mkdir()
    assert path_identity(str(workspace / "a" / "new" / "x"), str(workspace / "b" / "new" / "x")) is PathIdentity.DIFFERENT
    assert path_identity(str(workspace / "a" / "new" / "x"), str(workspace / "a" / "NEW" / "X")) is PathIdentity.SAME


def test_an_unreadable_ancestor_makes_the_relation_unknown(workspace, monkeypatch):
    outside = workspace.parent / "outside"
    outside.mkdir()
    ancestor = os.path.realpath(workspace.parent)
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path) == ancestor:
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(fs.os, "stat", stat)
    assert path_relation(str(workspace), str(outside / "x.json")) is PathRelation.UNKNOWN
