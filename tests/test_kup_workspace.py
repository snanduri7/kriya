"""KUP ``workspace.describe`` and the workspace scope of ``history.list`` (GUI M5 Open Folder).

Kriya is the only interpreter of a picked folder. These tests pin: the facts are Git's and Kriya's own, opening a folder never
writes to it or runs anything it configures, a folder is never classified greenfield/brownfield, and a run belongs to a workspace
only when its evidence manifest says so (everything else is counted as unattributed, never guessed)."""
from __future__ import annotations

import os
import subprocess

import pytest
from _kup_evidence_fixtures import acquire_snapshot_id, seed_trace_run, state_dir
from _strict_doubles import strict_config

from kriya.config import AppConfig
from kriya.control.workspace_identity import workspace_identity
from kriya.core.attempt_evidence import model
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation


def _describe(path, cfg=None):
    return run_kup_operation(cfg or AppConfig(), {"operation": "workspace.describe", "workspace": str(path)})


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _repo(path, dirty=False):
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "trunk")
    (path / "f.txt").write_text("one\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "c")
    if dirty:
        (path / "f.txt").write_text("two\n")
        (path / "untracked.txt").write_text("u\n")
    return path


def _tree(path):
    return sorted((os.path.relpath(os.path.join(d, n), path), os.stat(os.path.join(d, n)).st_mtime_ns) for d, _dirs, names in os.walk(path) for n in names)


# ------------------------------------------------------------------ describe

def test_empty_plain_and_git_folders_use_the_same_fields_and_no_classification(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "a.txt").write_text("a")
    (tmp_path / "empty").mkdir()
    repo = _repo(tmp_path / "repo")
    keys = None
    for folder in ("empty", "plain", "repo"):
        env = _describe(tmp_path / folder)
        assert env["error"] is None and env["operation"] == "workspace.describe", folder
        data = env["data"]
        keys = keys or set(data)
        assert set(data) == keys, folder
        assert not {"greenfield", "brownfield", "project_type", "change_type"} & set(data), folder
        assert data["identity"] == workspace_identity(str(tmp_path / folder)) and data["name"] == folder
        assert data["recovery"] == {"status": "CLEAN", "run_active": False}
    assert _describe(tmp_path / "empty")["data"]["checkout"]["state"] == "not_a_repository"
    assert _describe(tmp_path / "plain")["data"]["checkout"]["state"] == "not_a_repository"
    git = _describe(repo)["data"]["checkout"]
    assert git["state"] == "checkout_root" and git["branch"] == "trunk" and git["detached"] is False and len(git["head"]) == 40
    assert git["dirty"] is False and git["changed_entries"] == 0


def test_a_dirty_repository_is_reported_dirty_and_a_detached_head_has_no_branch(tmp_path):
    repo = _repo(tmp_path / "repo", dirty=True)
    git = _describe(repo)["data"]["checkout"]
    assert git["dirty"] is True and git["changed_entries"] == 2 and git["changed_truncated"] is False
    _git(repo, "stash", "-u", "-q")
    head = _describe(repo)["data"]["checkout"]["head"]
    _git(repo, "checkout", "-q", "--detach", head)
    detached = _describe(repo)["data"]["checkout"]
    assert detached["branch"] is None and detached["detached"] is True and detached["head"] == head and detached["dirty"] is False


def test_a_subdirectory_of_a_repository_inherits_none_of_its_facts(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "sub").mkdir()
    git = _describe(repo / "sub")["data"]["checkout"]
    assert git["state"] == "inside_repository" and git["root"] == os.path.realpath(repo)
    assert git["head"] is None and git["branch"] is None and git["dirty"] is None


def test_an_unborn_repository_has_no_head_but_is_a_checkout_root(tmp_path):
    os.makedirs(tmp_path / "fresh")
    _git(tmp_path / "fresh", "init", "-q")
    git = _describe(tmp_path / "fresh")["data"]["checkout"]
    assert git["state"] == "checkout_root" and git["head"] is None


def test_opening_a_folder_writes_nothing_to_it(tmp_path):
    repo = _repo(tmp_path / "repo", dirty=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a")
    for folder in (repo, plain):
        before = _tree(folder)
        index_before = (os.stat(folder / ".git" / "index").st_mtime_ns,) if (folder / ".git").exists() else None
        for _ in range(2):
            assert _describe(folder)["error"] is None
        assert _tree(folder) == before, "describe changed a file in the opened folder"
        assert not (folder / ".kriya").exists()
        if index_before:
            assert (os.stat(folder / ".git" / "index").st_mtime_ns,) == index_before, "describe refreshed the Git index"


def test_a_repository_configured_command_is_never_executed(tmp_path):
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "executed"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nprintf '\\0'\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    _git(repo, "config", "core.hooksPath", str(tmp_path))
    (repo / "g.txt").write_text("x")
    data = _describe(repo)["data"]
    assert data["checkout"]["state"] == "checkout_root" and data["checkout"]["dirty"] is True
    assert not marker.exists(), "opening a folder executed a command the repository configures"


def test_a_content_filter_driver_the_repository_defines_is_never_executed(tmp_path):
    """`git status` runs a filter's clean/process command to compare a file whose stat data changed (reviewer P3-5). Opening a folder must not."""
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "filter-ran"
    script = tmp_path / "clean.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("f.txt filter=mark\n")
    _git(repo, "config", "filter.mark.clean", str(script))
    (repo / "f.txt").write_text("one\n")  # same bytes, new stat data: git must look at the content
    os.utime(repo / "f.txt", (1, 1))
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True)
    assert marker.exists(), "premise: a plain `git status` DOES run the repository's clean filter here"
    marker.unlink()
    os.utime(repo / "f.txt", (2, 2))
    data = _describe(repo)["data"]
    assert data["checkout"]["state"] == "checkout_root"
    assert not marker.exists(), "opening a folder executed a content-filter command the repository configures"


def _a_file(p):
    (p / "file.txt").write_text("x")
    return p / "file.txt"


@pytest.mark.parametrize("make,reason", [(lambda p: p / "absent", "not_found"), (_a_file, "not_a_directory")])
def test_a_missing_or_non_directory_folder_is_a_typed_refusal(tmp_path, make, reason):
    env = _describe(make(tmp_path))
    assert env["data"] is None and env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == reason


def test_an_unreadable_folder_is_a_typed_refusal(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("the test user can read a mode-0 directory (root)")
        env = _describe(locked)
        assert env["error"]["code"] == policy.WORKSPACE_UNAVAILABLE and env["error"]["reason"] == "unreadable"
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("bad", ["relative/path", "", "/tmp/a\nb"])
def test_a_relative_empty_or_control_character_path_is_invalid_request(bad):
    env = run_kup_operation(AppConfig(), {"operation": "workspace.describe", "workspace": bad})
    assert env["error"]["code"] == policy.INVALID_REQUEST
    assert run_kup_operation(AppConfig(), {"operation": "workspace.describe"})["error"]["code"] == policy.INVALID_REQUEST


def test_the_state_identity_comes_from_kriyas_own_resolution_and_a_workspace_config_is_reported_not_applied(tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "kriya.yaml").write_text("evidence:\n  attempt_recorder:\n    content_visibility: 'on'\n")
    data = _describe(tmp_path / "ws")["data"]
    assert data["state"]["directory"] == state_dir() and data["state"]["source"] == "env"
    assert data["configuration"]["file_in_workspace"] is True and "not applied" in data["configuration"]["note"]
    # opening a folder whose own config asks for content widens nothing: the authority is the cockpit's configuration
    caps = run_kup_operation(strict_config(), {"operation": "capabilities"})["data"]["features"]
    assert caps["evidence_content"] is False
    assert "workspace.describe" in run_kup_operation(AppConfig(), {"operation": "capabilities"})["data"]["operations"]
    assert policy.WORKSPACE_UNAVAILABLE in run_kup_operation(AppConfig(), {"operation": "capabilities"})["data"]["features"]["error_codes"]


# ------------------------------------------------------------------ run scope

def _run_in(workspace, store_id, trace_id, *, store=True, event=True, seal=True):
    if store:
        writer = AttemptEvidenceWriter(state_dir(), store_id, capture=model.CAPTURE_FULL,
                                       manifest={"workspace_identity": workspace_identity(str(workspace)), "workspace_path": str(workspace)})
        writer.append("run.opened", {"goal_digest": "sha256:" + "1" * 64})
        if seal:
            writer.seal("run_closed")
    seed_trace_run(trace_id, {"run_id": store_id, "status": "OPEN", "capture": "full"} if store or event else None, with_event=event)


def _list(snapshot, workspace, **extra):
    env = run_kup_operation(AppConfig(), {"operation": "history.list", "snapshot_id": snapshot, "workspace": str(workspace), **extra})
    assert env["error"] is None, env
    return env["data"]


def test_a_workspace_sees_only_the_runs_its_manifests_record(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _run_in(a, "ev-a1", "t-a1")
    _run_in(b, "ev-b1", "t-b1")
    _run_in(a, "ev-a2", "t-a2")
    seed_trace_run("t-legacy", None, with_event=False)               # no attempt_store event at all
    _run_in(a, "ev-gone", "t-gone", store=False)                     # event names a store that does not exist
    snap = acquire_snapshot_id()
    data = _list(snap, a)
    assert sorted(r["run_id"] for r in data["runs"]) == ["t-a1", "t-a2"]
    scope = data["workspace_scope"]
    assert scope["matched"] == 2 and scope["other"] == 1 and scope["unattributed"] == 2 and scope["scanned"] == 5 and scope["truncated"] is False
    assert scope["workspace_identity"] == workspace_identity(str(a)) and data["next_cursor"] is None
    assert [r["run_id"] for r in _list(snap, b)["runs"]] == ["t-b1"]
    assert _list(snap, tmp_path)["runs"] == []                        # a folder no manifest names has no runs; nothing is guessed


def test_a_symlinked_spelling_of_the_workspace_is_the_same_workspace(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _run_in(real, "ev-r", "t-r")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert [r["run_id"] for r in _list(acquire_snapshot_id(), link)["runs"]] == ["t-r"]


def test_paging_through_a_scoped_list_never_repeats_or_skips_a_run(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    for i in range(5):
        _run_in(a, f"ev-a{i}", f"t-a{i}")
        _run_in(b, f"ev-b{i}", f"t-b{i}")
    snap = acquire_snapshot_id()
    seen, cursor, pages = [], None, 0
    while True:
        page = _list(snap, a, limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [r["run_id"] for r in page["runs"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20
    assert sorted(seen) == [f"t-a{i}" for i in range(5)] and len(seen) == len(set(seen)) and pages >= 3
    unscoped = run_kup_operation(AppConfig(), {"operation": "history.list", "snapshot_id": snap})["data"]
    assert "workspace_scope" not in unscoped and len(unscoped["runs"]) == 10


def test_the_scan_is_bounded_and_says_so(tmp_path, monkeypatch):
    from kriya.kup import workspace_ops
    a = tmp_path / "a"
    a.mkdir()
    for i in range(6):
        seed_trace_run(f"t-x{i}", None, with_event=False)
    monkeypatch.setattr(workspace_ops, "MAX_SCANNED_RUNS", 3)
    monkeypatch.setattr(workspace_ops, "SCAN_PAGE", 2)
    data = _list(acquire_snapshot_id(), a)
    assert data["workspace_scope"]["truncated"] is True and data["next_cursor"] is not None and data["workspace_scope"]["scanned"] <= 4


def test_a_scoped_list_needs_an_absolute_workspace():
    seed_trace_run("t-any", None, with_event=False)
    snap = acquire_snapshot_id()
    env = run_kup_operation(AppConfig(), {"operation": "history.list", "snapshot_id": snap, "workspace": "relative"})
    assert env["error"]["code"] == policy.INVALID_REQUEST


# ------------------------------------------------------------------ a broken git shim must not hide a working git later in PATH (MEASURED on this Mac)

def _fake_git(directory, script):
    os.makedirs(directory, exist_ok=True)
    path = directory / "git"
    path.write_text(script)
    path.chmod(0o755)
    return str(directory)


def test_a_git_shim_that_cannot_run_is_skipped_for_the_next_git_on_path(tmp_path, monkeypatch):
    import shutil
    real = os.path.dirname(shutil.which("git"))
    repo = _repo(tmp_path / "repo")
    shim = _fake_git(tmp_path / "shim", "#!/bin/sh\necho \"You have not agreed to the Xcode license agreements.\" >&2\nexit 69\n")
    monkeypatch.setenv("PATH", os.pathsep.join([shim, real]))
    git = _describe(repo)["data"]["checkout"]
    assert git["state"] == "checkout_root" and git["branch"] == "trunk" and git["dirty"] is False


def test_when_no_git_on_path_runs_the_reason_is_gits_own_and_nothing_is_guessed(tmp_path, monkeypatch):
    shim_a = _fake_git(tmp_path / "a", "#!/bin/sh\necho \"first shim refuses\" >&2\nexit 69\n")
    shim_b = _fake_git(tmp_path / "b", "#!/bin/sh\necho \"second shim refuses\" >&2\nexit 70\n")
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([shim_a, shim_b]))
    git = _describe(tmp_path / "ws")["data"]["checkout"]
    assert git["state"] == "git_unavailable" and git["reason"] == "second shim refuses"
    assert git["head"] is None and git["branch"] is None and git["dirty"] is None
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    assert _describe(tmp_path / "ws")["data"]["checkout"] == {**git, "reason": "the git executable was not found"}


def test_not_a_repository_is_a_conclusive_answer_and_later_gits_are_not_asked(tmp_path, monkeypatch):
    import shutil
    marker = tmp_path / "second-git-was-run"
    first = _fake_git(tmp_path / "first", "#!/bin/sh\necho \"fatal: not a git repository (or any of the parent directories): .git\" >&2\nexit 128\n")
    second = _fake_git(tmp_path / "second", f"#!/bin/sh\ntouch {marker}\nexit 0\n")
    (tmp_path / "ws").mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([first, second, os.path.dirname(shutil.which("git"))]))
    assert _describe(tmp_path / "ws")["data"]["checkout"]["state"] == "not_a_repository"
    assert not marker.exists()
