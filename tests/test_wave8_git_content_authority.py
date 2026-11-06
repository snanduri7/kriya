"""WAVE8-GIT-CONTENT-AUTHORITY-BYPASS-001 / OI-172: a planned ``git diff`` never prints the content of a path a direct model read of it would refuse.

Measured defect (2026-11-06 reconciliation, scratch repo): a modified tracked ``deploy.pem`` - the planned filesystem read of it is denied
(``SENSITIVE_PATH_DENIED``) - was printed in full by the planned ``git diff`` (COMPLETED). The fix is at the read-authority boundary, not an output filter:
``GitTool.authorize_model_invocation`` lists the changed tracked paths (``git diff --name-only -z --no-renames``) and judges each by the SAME function a
direct read uses (``_authorize_read_target``); a listing it cannot get refuses (fail closed). Reachable content surfaces of GitTool (TRACED): ``diff`` (this
fix) and ``blame`` (one file, already authorized); status/log/branch print no file content; show, grep, ``log -p`` and any free argument are not exposed.
"""
import os
import subprocess
from pathlib import Path

import pytest
from test_s18d_read_view_and_enforce_prompt import _in_view, view

from kriya.workflow.workflow_types import SubtaskStatus

PEM = "-----BEGIN PRIVATE KEY-----\nWAVE8-PEM-BODY-7f3a\n-----END PRIVATE KEY-----\n"
BODY = "WAVE8-PEM-BODY-7f3a"
DENIED = ("SENSITIVE_PATH_DENIED",)


def _git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)


def _track(root, relpath, text):
    path = Path(root, relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(root, "add", "--", relpath)
    _git(root, "commit", "-q", "-m", f"track {relpath}")
    return path


@pytest.fixture
def three(tmp_path):
    from test_s18d_read_view_and_enforce_prompt import build_three_state

    return build_three_state(tmp_path)


def _diff(s, where):
    return view("git", {"subcommand": "diff"}, (s.owner,)) if where == "in-place" else _in_view(s, "worktree", "git", {"subcommand": "diff"})


def _root(s, where):
    return s.owner if where == "in-place" else s.candidate


def _refused(result):
    return result.status == SubtaskStatus.FAILED and result.reason_codes == DENIED and result.tool_output is None


@pytest.mark.parametrize("where", ["in-place", "candidate-view"])
@pytest.mark.parametrize("name", ["deploy.pem", ".env", "config/id_rsa", "secrets/credentials.json"])
def test_a_modified_tracked_credential_file_is_not_printed_by_the_planned_diff(three, where, name):
    root = _root(three, where)
    path = _track(root, name, "old\n")
    path.write_text(PEM)                                              # modified, not committed: git diff prints it
    result = _diff(three, where)
    assert _refused(result), result
    assert BODY not in str(result.error) and BODY not in str(result.tool_output)
    assert os.path.basename(name) in str(result.error), "the refusal names the path only"


def test_a_deleted_credential_file_does_not_leak_its_committed_content(three):
    path = _track(three.owner, "deploy.pem", PEM)
    path.unlink()                                                      # git diff prints the REMOVED lines
    assert BODY in subprocess.run(["git", "diff"], cwd=three.owner, capture_output=True, text=True).stdout, "control: git itself would print it"
    result = _diff(three, "in-place")
    assert _refused(result) and BODY not in str(result.error)


def test_an_unstaged_rename_of_a_credential_file_does_not_launder_it(three):
    """Renamed in the working tree (not staged): git diff shows the old path as DELETED (its committed lines) - the old name is a credential store."""
    _track(three.owner, "deploy.pem", PEM)
    os.makedirs(os.path.join(three.owner, "docs"), exist_ok=True)
    os.rename(os.path.join(three.owner, "deploy.pem"), os.path.join(three.owner, "docs", "notes.txt"))
    assert BODY in subprocess.run(["git", "diff"], cwd=three.owner, capture_output=True, text=True).stdout, "control: git itself would print it"
    assert _refused(_diff(three, "in-place"))


def test_a_staged_rename_is_judged_by_the_path_a_direct_read_would_judge(three):
    """Documented, path-based contract (same as a direct read): after ``git mv`` the unstaged diff describes the NEW, non-credential name only, so a direct
    read of that name is allowed and so is its diff; the credential NAME is never printed."""
    _track(three.owner, "deploy.pem", PEM)
    os.makedirs(os.path.join(three.owner, "docs"), exist_ok=True)
    _git(three.owner, "mv", "deploy.pem", "docs/notes.txt")
    Path(three.owner, "docs", "notes.txt").write_text(PEM + "# changed\n")
    result = _diff(three, "in-place")
    assert result.status == SubtaskStatus.COMPLETED and "deploy.pem" not in result.tool_output


def test_ordinary_changes_still_print_and_an_unmodified_credential_file_does_not_block_the_diff(three):
    _track(three.owner, "deploy.pem", PEM)                             # committed, UNchanged: the diff prints none of it
    path = Path(three.owner, "pkg", "mod.py")
    path.write_text(path.read_text() + "ORDINARY_WAVE8_CHANGE = 1\n")
    result = _diff(three, "in-place")
    assert result.status == SubtaskStatus.COMPLETED and "ORDINARY_WAVE8_CHANGE" in result.tool_output and BODY not in result.tool_output


@pytest.mark.parametrize("subcommand", ["status", "log", "branch"])
def test_subcommands_that_print_no_file_content_are_unaffected_by_a_modified_credential_file(three, subcommand):
    _track(three.owner, "deploy.pem", PEM).write_text(PEM + "# edited\n")
    result = view("git", {"subcommand": subcommand}, (three.owner,))
    assert result.status == SubtaskStatus.COMPLETED and BODY not in str(result.tool_output)


def test_blame_of_a_credential_file_is_still_refused(three):
    _track(three.owner, "deploy.pem", PEM)
    result = view("git", {"subcommand": "blame", "file_path": "deploy.pem"}, (three.owner,))
    assert result.status == SubtaskStatus.FAILED and BODY not in str(result.tool_output)


def test_the_diff_judges_paths_by_the_direct_read_function(three, monkeypatch):
    """One classifier: the plugin's read-authority function is what the diff consults (replacing it changes the diff's answer)."""
    from test_s18d_read_view_and_enforce_prompt import _core

    core = _core()
    seen = []
    real = core._authorize_read_target
    monkeypatch.setattr(core, "_authorize_read_target", lambda invocation, target, **kw: seen.append(os.path.basename(target)) or real(invocation, target, **kw))
    path = Path(three.owner, "pkg", "mod.py")
    path.write_text(path.read_text() + "X = 1\n")
    assert _diff(three, "in-place").status == SubtaskStatus.COMPLETED
    assert "mod.py" in seen, seen


def test_a_changed_path_listing_git_cannot_give_refuses_closed(three, monkeypatch):
    from test_s18d_read_view_and_enforce_prompt import _core

    core = _core()
    path = Path(three.owner, "pkg", "mod.py")
    path.write_text(path.read_text() + "X = 2\n")
    monkeypatch.setattr(core.GitTool, "_changed_paths", lambda self, root: (_ for _ in ()).throw(core.ToolExecutionError("the changed paths could not be listed")))
    result = _diff(three, "in-place")
    assert result.status == SubtaskStatus.FAILED and "could not be listed" in str(result.error) and result.tool_output is None


def test_mutation_without_the_changed_path_check_the_credential_content_comes_out(three, monkeypatch):
    """Restoring the defect (the check removed) prints the key: the pins above are not vacuous."""
    from test_s18d_read_view_and_enforce_prompt import _core

    core = _core()
    _track(three.owner, "deploy.pem", "old\n").write_text(PEM)
    monkeypatch.setattr(core.GitTool, "_refuse_unreadable_changed_paths", lambda self, root, invocation: None)
    result = _diff(three, "in-place")
    assert result.status == SubtaskStatus.COMPLETED and BODY in result.tool_output
