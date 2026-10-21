"""TAF-4: snapshot_untracked_files' filesystem fallback (owner decision 2026-10-21: acceptable iff it runs no git, obeys
the protected-path rules, yields a snapshot clean_untracked_files_since can safely compare, and never passes a partial
snapshot off as complete).

MEASURED at ea56d08 (scratch taf/pre_taf4_measure.txt):
* the fallback spawns nothing (condition 1 holds);
* a managed worktree's `.git` is a FILE and the fallback listed it (only directories were pruned);
* MIXED SOURCES: the baseline came from git (untracked files only); the run then wrote a `.gitattributes` binding a
  dormant repository filter, so the clean-time snapshot was refused by the S19 inspection and fell back to EVERY file
  on disk - clean_untracked_files_since deleted every tracked file of the worktree (a.py, pkg/b.py, README.md);
* an unlistable directory was silently skipped (os.walk without onerror) and the partial set returned as complete.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kriya.workflow import worktree


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("taf4-home")
    (home / ".gitconfig").write_text("[user]\n\tname = taf4\n\temail = taf4@local\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


TRACKED = ("a.py", "pkg/b.py", "README.md")


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for rel in TRACKED:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _files(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)


def test_the_git_snapshot_is_the_untracked_files_only(tmp_path):
    """Quality bar rule 4: the normal (non-exception) output of the git branch - exactly git's untracked entries."""
    repo = _repo(tmp_path / "repo")
    (repo / "a.py").write_text("changed\n")
    (repo / "new" / "deep").mkdir(parents=True)
    (repo / "new" / "deep" / "n.txt").write_text("n\n")
    snapshot = worktree.snapshot_untracked_files(str(repo))
    assert snapshot == {"new/deep/n.txt"} and not isinstance(snapshot, worktree._FilesystemSnapshot)


def test_a_refused_git_snapshot_falls_back_to_a_complete_filesystem_snapshot_without_running_git(tmp_path, monkeypatch):
    """The behavioural pin: refused git snapshot -> the filesystem fallback (no process) -> every file, the protected
    names excluded (the managed worktree's `.git` gitfile, the scoped-snapshot sentinel)."""
    from kriya.repository.git import RepositoryContentFilterRefused

    repo = _repo(tmp_path / "repo")
    _git(repo, "config", "filter.kx.clean", "cat")
    (repo / ".gitattributes").write_text("*.py filter=kx\n")       # ACTIVE: the inspection refuses git status
    with pytest.raises(RepositoryContentFilterRefused):
        worktree.git_status_entries(str(repo))
    (repo / ".kriya-scoped-snapshot").write_text("sentinel\n")
    (repo / "sub").mkdir()
    (repo / "sub" / ".git").write_text("gitdir: /elsewhere\n")
    spawned = []
    real = subprocess.Popen.__init__

    def recording(self, *args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        real(self, *args, **kwargs)
    snapshot = worktree.snapshot_untracked_files(str(repo))
    monkeypatch.setattr(subprocess.Popen, "__init__", recording)
    again = worktree._snapshot_all_files_without_git(str(repo))
    monkeypatch.undo()
    assert spawned == []
    assert isinstance(snapshot, worktree._FilesystemSnapshot) and snapshot == again
    assert snapshot == {".gitattributes", "a.py", "pkg/b.py", "README.md"}, snapshot


def test_a_git_baseline_is_never_compared_with_a_filesystem_snapshot(tmp_path):
    """The measured destructive path, reproduced: baseline from git, the run binds a dormant repository filter, the
    clean-time snapshot falls back. Nothing tracked (or any other pre-existing file) is deleted."""
    repo = _repo(tmp_path / "repo")
    _git(repo, "config", "filter.kx.clean", "cat")                  # dormant repository definition: allowed
    baseline = worktree.snapshot_untracked_files(str(repo))
    assert baseline == set() and not isinstance(baseline, worktree._FilesystemSnapshot)
    (repo / ".gitattributes").write_text("*.py filter=kx\n")       # what a generated app can write
    (repo / "runtime_state.json").write_text("{}\n")
    assert isinstance(worktree.snapshot_untracked_files(str(repo)), worktree._FilesystemSnapshot)
    worktree.clean_untracked_files_since(str(repo), baseline)
    assert _files(repo) == sorted([*TRACKED, ".gitattributes", "runtime_state.json"])


def test_a_filesystem_baseline_is_never_compared_with_a_git_snapshot(tmp_path):
    """The other mix: a non-git folder becomes a repository during the run (an app's `git init`). The git snapshot lists
    untracked files the fallback never listed (inside pruned build directories): nothing is deleted."""
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "app.py").write_text("x\n")
    (folder / "target").mkdir()
    (folder / "target" / "cache.bin").write_text("c\n")
    baseline = worktree.snapshot_untracked_files(str(folder))
    assert isinstance(baseline, worktree._FilesystemSnapshot) and baseline == {"app.py"}
    _git(folder, "init", "-q", "-b", "main")
    worktree.clean_untracked_files_since(str(folder), baseline)
    assert (folder / "target" / "cache.bin").exists() and (folder / "app.py").exists()


def test_two_filesystem_snapshots_still_clean_what_the_run_created(tmp_path):
    """Positive control (the non-git regression's contract): same source both times - only the new file goes."""
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "app.py").write_text("x\n")
    baseline = worktree.snapshot_untracked_files(str(folder))
    (folder / "tasks.json").write_text("{}\n")
    worktree.clean_untracked_files_since(str(folder), baseline)
    assert _files(folder) == ["app.py"]


def test_an_unlistable_directory_makes_the_fallback_say_it_cannot_tell(tmp_path):
    """A partial walk is never a complete snapshot: None (the contract's 'couldn't tell'), so cleanup is skipped."""
    folder = tmp_path / "plain"
    (folder / "locked").mkdir(parents=True)
    (folder / "locked" / "keep.txt").write_text("k\n")
    (folder / "app.py").write_text("x\n")
    os.chmod(folder / "locked", 0)
    try:
        assert worktree._snapshot_all_files_without_git(str(folder)) is None
        assert worktree.snapshot_untracked_files(str(folder)) is None
    finally:
        os.chmod(folder / "locked", 0o755)
    assert worktree._snapshot_all_files_without_git(str(folder)) == {"app.py", "locked/keep.txt"}


def _layout_with_protected_directories(folder: Path) -> None:
    """Every protected control directory under its own parent (so no two spellings collide on a case-insensitive
    filesystem), next to ordinary directories whose names merely resemble them."""
    for parent, control in (("a", ".git"), ("b", ".Git"), ("c", ".GIT"), ("d", ".kriya"), ("e", ".KRIYA"), ("f", ".KrIyA")):
        (folder / parent / control).mkdir(parents=True)
        (folder / parent / control / "state").write_text("control\n")
    for ordinary in ("g/.github", "h/.kriya-notes", "i/gitlab", "j/kriya", "k/Git", "l/.gitx"):
        (folder / ordinary).mkdir(parents=True)
        (folder / ordinary / "keep.txt").write_text("ordinary\n")
    (folder / "app.py").write_text("x\n")
    (folder / ".gitignore").write_text("target/\n")


ORDINARY = {"app.py", ".gitignore", "g/.github/keep.txt", "h/.kriya-notes/keep.txt", "i/gitlab/keep.txt", "j/kriya/keep.txt",
            "k/Git/keep.txt", "l/.gitx/keep.txt"}


def test_the_fallback_never_lists_a_protected_directory_under_any_spelling(tmp_path):
    """TAF-4 (i): the protected-name rule is the one fold-aware definition (kriya.policy.filesystem), applied to directories as
    it is to file names - `.git`, `.Git`, `.GIT`, `.kriya`, `.KRIYA` and mixed spellings are pruned; ordinary similar
    names stay visible."""
    folder = tmp_path / "plain"
    _layout_with_protected_directories(folder)
    assert worktree._snapshot_all_files_without_git(str(folder)) == ORDINARY


def test_files_a_run_creates_under_a_protected_directory_spelling_are_never_cleaned(tmp_path):
    """The harm behind (i): a listed protected file is deletable by cleanup. A file created during the run beneath any
    spelling of a control directory survives clean_untracked_files_since; an ordinary new file is still removed."""
    folder = tmp_path / "plain"
    _layout_with_protected_directories(folder)
    baseline = worktree.snapshot_untracked_files(str(folder))
    assert isinstance(baseline, worktree._FilesystemSnapshot)
    created_in_control = [folder / "b" / ".Git" / "made.txt", folder / "e" / ".KRIYA" / "made.txt", folder / "a" / ".git" / "made.txt"]
    for path in created_in_control:
        path.write_text("run state\n")
    (folder / "new.txt").write_text("runtime state\n")
    worktree.clean_untracked_files_since(str(folder), baseline)
    assert all(path.exists() for path in created_in_control)
    assert not (folder / "new.txt").exists()
    assert (folder / "app.py").exists()
