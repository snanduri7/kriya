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
import shutil
import subprocess
from pathlib import Path

import pytest

from kriya.workflow import worktree


@pytest.fixture(autouse=True)
def _isolated_git(scratch_dir, monkeypatch):
    home = scratch_dir("taf4-home")
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


# ------------------------------------------------------------------------------------------------ Q3: the git-source cleanup

def _create(repo: Path, files: dict) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_the_git_source_cleanup_never_deletes_a_protected_control_path_and_still_removes_ordinary_files(tmp_path):
    """Q3 of the consolidated addendum (MEASURED at 72d9182, pre-existing): with git as the snapshot source, the cleanup
    deleted untracked `.kriya/...` control files, the scoped-snapshot sentinel, a case variant of `.kriya`, and an untracked
    embedded repository INCLUDING its `.git` (git lists it as one directory entry). The protection is the one canonical
    rule (kriya.policy.filesystem: ANY path component, any spelling) - and a directory entry that holds a control path beneath
    it is kept whole. Ordinary run artifacts, including look-alike names, are still removed."""
    repo = _repo(tmp_path / "repo")
    baseline = worktree.snapshot_untracked_files(str(repo))
    assert not isinstance(baseline, worktree._FilesystemSnapshot)               # the GIT source (this is not the fallback)
    protected = {".kriya/state.json": "control", ".kriya-scoped-snapshot": "sentinel", "p/.KrIyA/y.txt": "case variant"}
    ordinary = {"ordinary_new.txt": "runtime state", "pkg/.github/ci.yml": "look-alike", "docs/.kriya-notes/n.md": "look-alike",
                "gitlab/ci.yml": "look-alike", "kriya/z.py": "look-alike"}
    _create(repo, {**protected, **ordinary})
    embedded = repo / "embedded"
    embedded.mkdir()
    _git(embedded, "init", "-q")
    (embedded / "f.txt").write_text("x")
    worktree.clean_untracked_files_since(str(repo), baseline)
    assert all((repo / rel).exists() for rel in protected), [rel for rel in protected if not (repo / rel).exists()]
    assert (embedded / ".git").exists() and (embedded / "f.txt").exists()      # the embedded repository is kept whole
    assert not any((repo / rel).exists() for rel in ordinary), [rel for rel in ordinary if (repo / rel).exists()]
    assert (repo / "a.py").exists()                                              # tracked content untouched


def test_the_git_source_cleanup_never_deletes_content_a_symlink_the_run_created_points_to(tmp_path):
    """Q3 top-level symlink (consolidated addendum #3, E): DISPROVED as an escape for links present at snapshot time. MEASURED with
    a production-shaped run that creates symlinks to an external directory, to the repository's own `.kriya` control directory,
    to the repository root, to a file, and a dangling one: nothing a symlink points at is deleted. Addendum #4 adds the residual
    it found: the run's own links were LEFT BEHIND (a stale directory link then persisted into the next attempt's baseline); a link
    is an artifact the run created, so it is removed - itself, never its target."""
    repo = _repo(tmp_path / "repo")
    external = tmp_path / "external"
    (external / "sub").mkdir(parents=True)
    (external / "precious.txt").write_text("keep")
    (external / "sub" / "deep.txt").write_text("keep")
    _create(repo, {".kriya/control.json": "{}"})
    baseline = worktree.snapshot_untracked_files(str(repo))
    assert not isinstance(baseline, worktree._FilesystemSnapshot)               # the GIT source
    links = ["link_external", "link_control", "link_root", "link_file", "link_dangling"]
    os.symlink(external, repo / "link_external")
    os.symlink(repo / ".kriya", repo / "link_control")
    os.symlink(repo, repo / "link_root")
    os.symlink(external / "precious.txt", repo / "link_file")
    os.symlink(tmp_path / "nowhere", repo / "link_dangling")
    _create(repo, {"plain_artifact.txt": "run state"})
    worktree.clean_untracked_files_since(str(repo), baseline)
    assert sorted(p.name for p in external.rglob("*")) == ["deep.txt", "precious.txt", "sub"]      # nothing behind a link deleted
    assert (external / "precious.txt").read_text() == "keep" and (external / "sub" / "deep.txt").read_text() == "keep"
    assert (repo / ".kriya" / "control.json").exists() and (repo / "a.py").exists()
    assert not (repo / "plain_artifact.txt").exists()                           # control: the cleanup did run
    assert [name for name in links if os.path.lexists(repo / name)] == []       # every link the run created is gone


@pytest.mark.parametrize("depth", [1, 2], ids=["parent", "grandparent"])
def test_a_directory_swapped_for_a_symlink_after_the_snapshot_never_leads_the_cleanup_outside_the_worktree(tmp_path, monkeypatch, depth):
    """Addendum #4 (MEASURED by deterministic simulation at the tip): the cleanup runs after a verification step whose processes may
    still be alive in the worktree. A run-created real directory replaced by a link to an outside directory between the cleanup's
    own snapshot and its removal made `os.remove(<repo>/d/out.txt)` delete `external/out.txt`. No component of the path below the
    worktree is followed any more."""
    repo = _repo(tmp_path / "repo")
    external = tmp_path / "external"
    (external / "inner").mkdir(parents=True)
    (external / "out.txt").write_text("outside")
    (external / "inner" / "out.txt").write_text("outside")
    baseline = worktree.snapshot_untracked_files(str(repo))
    leaf = "d/out.txt" if depth == 1 else "d/e/out.txt"
    _create(repo, {leaf: "run artifact"})
    real = worktree.snapshot_untracked_files
    swapped = []

    def snapshot(path):
        result = real(path)
        if not swapped and os.path.realpath(path) == os.path.realpath(repo):
            if depth == 1:
                shutil.rmtree(repo / "d")
                os.symlink(external, repo / "d")
            else:
                shutil.rmtree(repo / "d" / "e")
                os.symlink(external, repo / "d" / "e")
            swapped.append(True)
        return result
    monkeypatch.setattr(worktree, "snapshot_untracked_files", snapshot)
    worktree.clean_untracked_files_since(str(repo), baseline)
    assert swapped == [True]                                                    # the swap really happened between snapshot and removal
    assert (external / "out.txt").read_text() == "outside" and (external / "inner" / "out.txt").read_text() == "outside"
