"""OI-164 residual Q1 (TIP-CLOSE C): a planned git read never renders the bytes of a control object through another
name for it.

A TRACKED file replaced by a hard link to a control object (the owner's .git/config, .kriya/control/state.json, a
worktree's admin files) is a modified entry, and ``git diff`` prints its working-tree bytes - the control bytes
(MEASURED on the unfixed bytes, 03954d8, below). The fix is at the GitTool's planned-read authorization: before a planned
diff runs, the tracked paths of the repository it describes are listed WITHOUT converting any content (``git ls-files
-z``: the index only, no content filter), and every one that is a regular file with more than one link is judged by
the one PLAT-039 / OI-164 owner (kriya.policy.filesystem.is_trusted_control_path through ControlObjectIdentities, one
index per call, never cached); another name for a control object refuses the call with TRUSTED_CONTROL_PATH_DENIED,
naming the alias only, before git diff runs. A path whose identity cannot be established refuses (fail closed).

Reachable content surfaces of the GitTool (TRACED, plugins/core_tools GitArgs / GitTool._run_in): ``diff`` (every
tracked path - this fix); ``blame`` (one file, already authorized by _authorized_read, pinned below and in
test_oi164_control_object_identity.py); status, log (``-n 5 --oneline``) and branch render no file content; a planned
commit is refused; show, grep and ``log -p`` are not exposed (an unsupported subcommand is refused).
"""
import os
import subprocess
from pathlib import Path

import pytest
from test_oi164_control_object_identity import (
    DENIED,
    MARK,
    _append_marker,
    _protected_object,
    _refused,
    _worktree_admin,
)
from test_s18d_read_view_and_enforce_prompt import _core, _in_view, dotted, view  # noqa: F401

from kriya.workflow.workflow_types import SubtaskStatus

TRACKED = "pkg/mod.py"   # tracked in the owner and in its worktree candidate (S18c three-state fixture)


def _replace_with_alias(root, protected, name=TRACKED):
    path = os.path.join(root, *name.split("/"))
    os.unlink(path)
    os.link(protected, path)
    assert os.path.samefile(path, protected) and os.stat(path).st_nlink > 1, "a real hard link"
    return path


def _git_diff(root):
    """What git itself prints for the same diff (the GitTool's own flags)."""
    return subprocess.run(["git", "diff", "--no-ext-diff", "--no-textconv"], cwd=root, capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL).stdout


def _assert_refused_naming_only_the_alias(result, alias, protected):
    assert _refused(result), result
    assert result.reason_codes == DENIED
    assert os.path.basename(alias) in str(result.error), "the refusal names the alias"
    assert protected not in str(result.error), "never the control object it aliases"
    assert MARK not in str(result.error) and "[core]" not in str(result.error)


# (protected object kind, where the planned diff runs): the candidate view (two roots: diff describes the candidate,
# roots[1]) and the in-place run (one root: the owner folder IS what the run changes).
CASES = {
    "candidate-to-owner-git-config": ("worktree", "candidate-to-owner-git-config"),
    "candidate-to-owner-kriya-state": ("worktree", "candidate-to-owner-kriya-state"),
    "candidate-to-worktree-admin": ("worktree", "candidate-to-worktree-admin"),
    "in-place-git-config": ("in-place", "git-config"),
    "in-place-kriya-state": ("in-place", "kriya-state"),
    "in-place-git-index": ("in-place", "git-index"),
}


def _diff(s, where):
    if where == "in-place":
        return view("git", {"subcommand": "diff"}, (s.owner,))
    return _in_view(s, "worktree", "git", {"subcommand": "diff"})


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_planned_diff_of_a_tracked_file_replaced_by_an_alias_is_refused(dotted, case):  # noqa: F811
    """MEASURED on the unfixed bytes: the diff COMPLETED and printed the control bytes (the marker / [core])."""
    s = dotted
    where, kind = CASES[case]
    _view, _root, protected = _protected_object(s, kind)
    root = s.owner if where == "in-place" else s.candidate
    if kind.endswith(("git-config", "kriya-state")):
        _append_marker(protected)   # a comment line git and the JSON reader never act on in this test
    alias = _replace_with_alias(root, protected)
    if kind != "git-index":         # binary: git prints no bytes for it, but it stays another name for a control object
        first_line = Path(protected).read_text(encoding="utf-8").splitlines()[0]
        assert first_line in _git_diff(root), "git itself prints the control bytes"
    _assert_refused_naming_only_the_alias(_diff(s, where), alias, protected)


def test_a_planned_diff_with_no_roots_judges_its_process_folder(dotted, monkeypatch):  # noqa: F811
    """No roots supplied: the diff runs in the process folder (S18b), and that folder's own control objects decide."""
    s = dotted
    protected = os.path.join(s.owner, ".git", "config")
    alias = _replace_with_alias(s.owner, protected)
    monkeypatch.chdir(s.owner)
    _assert_refused_naming_only_the_alias(view("git", {"subcommand": "diff"}, ()), alias, protected)


def test_only_the_alias_decides_not_the_first_multi_link_path(dotted):  # noqa: F811
    """An ordinary multi-link tracked file sorts first; the alias is judged too (every listed path, not the first)."""
    s = dotted
    os.link(os.path.join(s.candidate, "app.py"), os.path.join(s.candidate, "store_of_app.txt"))
    protected = os.path.join(s.owner, ".git", "config")
    alias = _replace_with_alias(s.candidate, protected)
    _assert_refused_naming_only_the_alias(_in_view(s, "worktree", "git", {"subcommand": "diff"}), alias, protected)


@pytest.mark.parametrize("where", ["worktree", "in-place"])
def test_an_ordinary_multi_link_tracked_file_still_diffs(dotted, where):  # noqa: F811
    """Preservation (passes at 03954d8 too): a tracked file sharing its object with an ordinary file is not refused, and
    the planned diff is git's own output, byte for byte."""
    s = dotted
    root = s.owner if where == "in-place" else s.candidate
    Path(root, "store.txt").write_text("ORDINARY_STORE = 1\n")
    _replace_with_alias(root, os.path.join(root, "store.txt"))
    result = _diff(s, where)
    assert result.status == SubtaskStatus.COMPLETED, result
    assert "ORDINARY_STORE" in result.tool_output and result.tool_output == _git_diff(root)


@pytest.mark.parametrize("where", ["worktree", "in-place"])
def test_a_single_link_change_diffs_byte_identically(dotted, where):  # noqa: F811
    """Preservation (passes at 03954d8 too): a tracked file changed in place (one link: what every Kriya write leaves,
    an atomic replace) diffs exactly as git prints it, and no index is built for it."""
    s = dotted
    root = s.owner if where == "in-place" else s.candidate
    Path(root, TRACKED).write_text("PKG = 2  # CHANGED_IN_PLACE\n")
    assert os.stat(os.path.join(root, TRACKED)).st_nlink == 1
    result = _diff(s, where)
    assert result.status == SubtaskStatus.COMPLETED, result
    assert "CHANGED_IN_PLACE" in result.tool_output and result.tool_output == _git_diff(root)


def test_a_multi_link_tracked_path_whose_identity_cannot_be_read_fails_closed(dotted, monkeypatch):  # noqa: F811
    """The stat of a listed path fails (not 'absent'): the call is refused, naming that path, before git diff runs."""
    s = dotted
    Path(s.candidate, "store.txt").write_text("ORDINARY_STORE = 1\n")
    alias = _replace_with_alias(s.candidate, os.path.join(s.candidate, "store.txt"))
    real_lstat, real_stat = os.lstat, os.stat

    def failing(function):
        def call(path, *args, **kwargs):
            if os.fspath(path) == alias:
                raise PermissionError(13, "Permission denied", alias)
            return function(path, *args, **kwargs)
        return call

    monkeypatch.setattr(os, "lstat", failing(real_lstat))
    monkeypatch.setattr(os, "stat", failing(real_stat))
    result = _in_view(s, "worktree", "git", {"subcommand": "diff"})
    assert _refused(result), result
    assert os.path.basename(alias) in str(result.error)


def test_a_deleted_tracked_file_needs_no_identity(dotted):  # noqa: F811
    """Preservation: a listed path that no longer exists (diff shows the index blob, no working-tree bytes) is not
    refused; the fixture's tracked symlinks (diff shows link text) are listed too; the diff is git's own output."""
    s = dotted
    os.unlink(os.path.join(s.candidate, TRACKED))
    result = _in_view(s, "worktree", "git", {"subcommand": "diff"})
    assert result.status == SubtaskStatus.COMPLETED and result.tool_output == _git_diff(s.candidate), result


def test_a_listing_git_cannot_give_refuses_before_the_diff_runs(dotted):  # noqa: F811
    """Fail closed: the index cannot be read, so the tracked paths cannot be judged - refused, typed as a failure,
    before git diff runs (its own 'index file corrupt' failure never appears) and with no output."""
    s = dotted
    Path(s.owner, ".git", "index").write_bytes(b"NOT AN INDEX")
    result = view("git", {"subcommand": "diff"}, (s.owner,))
    assert result.status == SubtaskStatus.FAILED and result.tool_output is None, result
    assert "could not be listed" in str(result.error) and "Nothing was run" in str(result.error), result.error


def test_a_truncated_listing_refuses(dotted, monkeypatch):  # noqa: F811
    """Fail closed: a listing the process controller truncated (it keeps only the tail) is never judged as whole."""
    core = _core()

    class Tiny(core.ProcessController):
        def __init__(self, **kwargs):
            super().__init__(max_output_chars=8, **kwargs)

    monkeypatch.setattr(core, "ProcessController", Tiny)
    with pytest.raises(core.ToolExecutionError, match="could not be listed"):
        core.GitTool()._tracked_paths(dotted.owner)


def test_the_listing_is_every_tracked_path_once(dotted):  # noqa: F811
    """The path set is git's index, each path once (an unmerged path has several index entries)."""
    listed = _core().GitTool()._tracked_paths(dotted.owner)
    assert listed == subprocess.run(["git", "ls-files"], cwd=dotted.owner, capture_output=True, text=True, check=True,
                                    stdin=subprocess.DEVNULL).stdout.splitlines()
    assert TRACKED in listed and "rel_link.py" in listed and len(listed) == len(set(listed))


def test_a_planned_blame_of_a_tracked_file_replaced_by_an_alias_is_refused(dotted):  # noqa: F811
    """Preservation (passes at 03954d8 too): blame's one file is already authorized (_authorized_read)."""
    s = dotted
    protected = os.path.join(s.owner, ".git", "config")
    alias = _replace_with_alias(s.owner, protected)
    result = _in_view(s, "owner", "git", {"subcommand": "blame", "file_path": TRACKED})
    _assert_refused_naming_only_the_alias(result, alias, protected)


def test_the_candidate_worktree_admin_alias_is_the_worktrees_own(dotted):  # noqa: F811
    """Guard: the worktree-admin case really aliases the candidate's admin HEAD under the owner's .git/worktrees."""
    s = dotted
    assert os.path.isfile(os.path.join(_worktree_admin(s.candidate), "HEAD"))
