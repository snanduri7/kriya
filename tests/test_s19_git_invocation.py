"""S19 (OI-141): Kriya's OWN git invocations never execute repository-controlled configuration.

handover/MODULARIZATION/S19-PREFLIGHT.md (MEASURED, git 2.54.0, 684 probes): a repository's .git/config (directly, through
include.path / includeIf / config.worktree, or a submodule's config), its hooks and its gpg / diff drivers ran code on the
host through 26 of 38 probed Kriya call sites. Every pin plants the configuration AFTER the fixture is built (setup git is
itself hardened), asserts the plant is really there, calls the REAL Kriya function and then asserts the marker the
planted command would create is absent.

Isolation: GIT_CONFIG_GLOBAL points at a per-test file holding only an identity and GIT_CONFIG_NOSYSTEM=1, so a
developer's own global fsmonitor / hooksPath / signing can neither fire nor mask a pin.

Held (Q1, owner decision, digest STOP): content-filter drivers (filter.<x>.clean|smudge|process). The held pin stays a
strict xfail until the owner decides; it XPASSes (and so fails) the moment anything closes the vector.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Callable, Dict

import pytest

from tests._plugin_test_support import load_core_tools_module

# The owner modules whose git spawns already go through kriya.platform.git_argv: each pin flips with its owner's commit.
_FIXED_OWNERS: frozenset = frozenset()


def _flip(owner: str):
    """The strict expected-to-flip mark until ``owner``'s spawns go through the seam (then inert)."""
    return pytest.mark.xfail(owner not in _FIXED_OWNERS, strict=True,
                             reason=f"S19 OI-141: {owner}'s git spawns still run repository-controlled commands")
_HELD_Q1 = pytest.mark.xfail(strict=True, reason="S19 Q1 (owner decision, digest STOP): repository content-filter "
                                                 "drivers still run through Kriya's own git")


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19-git-home")
    config = home / "gitconfig"
    config.write_text("[user]\n\tname = s19\n\temail = s19@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)


def _setup(cwd: Path, *args: str) -> str:
    """The test's own git: never runs a hook or fsmonitor, so only Kriya's call can create a marker."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def _marker_script(path: Path, marker: Path, *, passthrough: bool = False, exit_code: int = 0) -> str:
    path.write_text(f'#!/bin/sh\ntouch "{marker}"\n' + ("cat\n" if passthrough else "") + f"exit {exit_code}\n")
    path.chmod(0o755)
    return str(path)


def _repository(root: Path, *, commit: bool = True) -> Path:
    repo = root / "owner"
    repo.mkdir()
    _setup(repo, "init", "-q", "-b", "main")
    if commit:
        (repo / "a.py").write_text("x = 1\n")
        (repo / ".gitignore").write_text("__pycache__/\n.kriya/\n")
        (repo / "sub").mkdir()
        (repo / "sub" / "b.txt").write_text("b\n")
        _setup(repo, "add", "-A")
        _setup(repo, "commit", "-q", "-m", "init")
        (repo / "a.py").write_text("x = 2\n")           # a tracked change: status/diff compare content
        (repo / "u.py").write_text("u = 1\n")           # untracked
        (repo / "skill.md").write_text("skill\n")       # untracked, committed by git_commit_if_tracked
    return repo


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


def _gittool(sub: str, **kwargs) -> Callable[[Path], str]:
    def run(repo: Path) -> str:
        core_tools = load_core_tools_module()
        return asyncio.run(core_tools.GitTool()._run_in(core_tools.GitArgs(subcommand=sub, **kwargs), str(repo)))
    return run


def _sites() -> Dict[str, Callable[[Path], object]]:
    """The REAL Kriya call sites the preflight measured executing a planted core.fsmonitor (git 2.54.0)."""
    from kriya import distribution
    from kriya.analyzer import analyzer
    from kriya.code_intel import service as code_intel
    from kriya.core import release_identity
    from kriya.repository import snapshot
    from kriya.skills import skill
    from kriya.static_analysis import operator_scan
    from kriya.workflow import checkpoint, milestone_completion, worktree

    return {
        "snapshot.untracked_repository_paths": lambda o: snapshot.untracked_repository_paths(str(o)),
        "worktree.repository_content_paths": lambda o: worktree.repository_content_paths(str(o)),
        "worktree.git_status_entries": lambda o: worktree.git_status_entries(str(o)),
        "worktree.git_read_lines": lambda o: worktree.git_read_lines(str(o), "ls-files"),
        "worktree.create_git_worktree": lambda o: worktree.create_git_worktree(str(o)),
        "checkpoint.compute_workspace_fingerprint": lambda o: checkpoint.compute_workspace_fingerprint(str(o)),
        "checkpoint.compute_workspace_content_hash": lambda o: checkpoint.compute_workspace_content_hash(str(o)),
        "milestone_completion.workspace_evidence_hash": lambda o: milestone_completion.workspace_evidence_hash(str(o)),
        "skill.git_commit_if_tracked": lambda o: skill.git_commit_if_tracked(str(o / "skill.md"), "s19 skill"),
        "analyzer._repository_revision": lambda o: analyzer._repository_revision(str(o)),
        "code_intel.discover_source_files": lambda o: code_intel.discover_source_files(str(o)),
        "release_identity.kriya_revision": lambda o: release_identity.kriya_revision(str(o)),
        "distribution.tracked_release_files": lambda o: distribution.tracked_release_files(o, ("sub",)),
        "operator_scan.changed_paths": lambda o: operator_scan.changed_paths(str(o), "HEAD"),
        "GitTool status": _gittool("status"),
        "GitTool diff": _gittool("diff"),
        "GitTool blame": _gittool("blame", file_path="a.py"),
    }


SITE_OWNERS = {
    "snapshot.untracked_repository_paths": "REPOSITORY", "worktree.repository_content_paths": "KERNEL",
    "worktree.git_status_entries": "KERNEL", "worktree.git_read_lines": "KERNEL", "worktree.create_git_worktree": "KERNEL",
    "checkpoint.compute_workspace_fingerprint": "KERNEL", "checkpoint.compute_workspace_content_hash": "KERNEL",
    "milestone_completion.workspace_evidence_hash": "KERNEL", "skill.git_commit_if_tracked": "SKILLS",
    "analyzer._repository_revision": "RETRIEVAL", "code_intel.discover_source_files": "RETRIEVAL",
    "release_identity.kriya_revision": "QUALIFICATION", "distribution.tracked_release_files": "ADAPTER-CLI",
    "operator_scan.changed_paths": "EXECUTION", "GitTool status": "PLUGIN", "GitTool diff": "PLUGIN",
    "GitTool blame": "PLUGIN",
}
SITES = [pytest.param(site, marks=_flip(owner), id=site) for site, owner in SITE_OWNERS.items()]


def _plant_fsmonitor(repo: Path, marker: Path, *, scope: str = "--local") -> None:
    _setup(repo, "config", scope, "core.fsmonitor", _marker_script(repo.parent / "fsmonitor.sh", marker, exit_code=1))


# --------------------------------------------------------------------------------- before-pins (pass before and after)

def test_kriya_git_answers_are_git_s_own_answers(owner):
    """Positive control: on an ordinary repository the hardened invocation changes no answer a caller depends on."""
    from kriya.repository import snapshot
    from kriya.workflow import checkpoint, worktree

    head = _setup(owner, "rev-parse", "HEAD").strip()
    assert worktree.git_status_entries(str(owner)) == [(" M", "a.py", None), ("??", "skill.md", None), ("??", "u.py", None)]
    assert snapshot.untracked_repository_paths(str(owner)) == frozenset({"skill.md", "u.py"})
    assert worktree.repository_content_paths(str(owner)) == [".gitignore", "a.py", "sub/b.txt"]
    assert worktree.git_read_lines(str(owner), "rev-parse", "HEAD") == [head]
    assert checkpoint.compute_workspace_fingerprint(str(owner)) == f"{head}:dirty"
    assert checkpoint.compute_tree_hash(str(owner)) == _setup(owner, "rev-parse", "HEAD^{tree}").strip()
    assert checkpoint.compute_base_commit(str(owner)) == head


def test_gittool_reads_are_git_s_own_output_without_drivers(owner):
    """Positive control: with no diff driver configured, GitTool's diff and blame equal plain git's."""
    assert _gittool("diff")(owner) == _setup(owner, "diff")
    assert _gittool("status")(owner) == _setup(owner, "status")


def test_kriya_commits_still_commit(tmp_path):
    """Positive control: the skill bookkeeping commit and the zero-commit bootstrap still create their commits."""
    from kriya.skills import skill
    from kriya.workflow import worktree

    repo = _repository(tmp_path)
    skill.git_commit_if_tracked(str(repo / "skill.md"), "s19 skill")
    assert _setup(repo, "log", "-1", "--format=%s").strip() == "s19 skill"
    (tmp_path / "empty").mkdir()
    empty = _repository(tmp_path / "empty", commit=False)
    candidate = worktree.create_git_worktree(str(empty))
    assert Path(candidate).is_dir() and _setup(empty, "rev-list", "--count", "HEAD").strip() == "1"


# --------------------------------------------------------------------------------- after-pins (strict xfail -> flip)

@pytest.mark.parametrize("site", SITES)
def test_a_repository_fsmonitor_never_runs_through_kriya_git(owner, site):
    marker = owner.parent / "FSMONITOR_RAN"
    _plant_fsmonitor(owner, marker)
    assert "fsmonitor" in (owner / ".git" / "config").read_text()       # the plant is really there
    assert not marker.exists()
    _sites()[site](owner)
    assert not marker.exists(), f"{site} ran the repository's core.fsmonitor command"


@_flip("KERNEL")
def test_an_included_fsmonitor_never_runs(owner):
    marker = owner.parent / "INCLUDED_FSMONITOR_RAN"
    included = owner.parent / "included.cfg"
    included.write_text(f"[core]\n\tfsmonitor = {_marker_script(owner.parent / 'inc.sh', marker, exit_code=1)}\n")
    _setup(owner, "config", "include.path", str(included))
    _sites()["worktree.git_status_entries"](owner)
    assert not marker.exists()


@_flip("REPOSITORY")
def test_an_include_if_fsmonitor_never_runs(owner):
    marker = owner.parent / "INCLUDEIF_FSMONITOR_RAN"
    included = owner.parent / "includedif.cfg"
    included.write_text(f"[core]\n\tfsmonitor = {_marker_script(owner.parent / 'incif.sh', marker, exit_code=1)}\n")
    with open(owner / ".git" / "config", "a", encoding="utf-8") as handle:
        handle.write(f'[includeIf "gitdir:/"]\n\tpath = {included}\n')
    _sites()["snapshot.untracked_repository_paths"](owner)
    assert not marker.exists()


@_flip("KERNEL")
def test_a_worktree_config_fsmonitor_never_runs(owner):
    marker = owner.parent / "WORKTREE_CONFIG_FSMONITOR_RAN"
    _setup(owner, "config", "extensions.worktreeConfig", "true")
    _plant_fsmonitor(owner, marker, scope="--worktree")
    _sites()["checkpoint.compute_workspace_content_hash"](owner)
    assert not marker.exists()


@_flip("KERNEL")
def test_a_submodule_fsmonitor_never_runs(tmp_path):
    source = tmp_path / "submodule-source"
    source.mkdir()
    _setup(source, "init", "-q", "-b", "main")
    (source / "s.txt").write_text("s\n")
    _setup(source, "add", "-A")
    _setup(source, "commit", "-q", "-m", "s")
    repo = _repository(tmp_path)
    _setup(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "smod")
    _setup(repo, "commit", "-q", "-m", "submodule")
    marker = tmp_path / "SUBMODULE_FSMONITOR_RAN"
    _plant_fsmonitor(repo / "smod", marker)
    (repo / "smod" / "s.txt").write_text("s2\n")                         # status recurses into the dirty submodule
    _sites()["worktree.git_status_entries"](repo)
    assert not marker.exists()


@_flip("KERNEL")
@pytest.mark.parametrize("via_hooks_path", [False, True], ids=["dot-git-hooks", "core.hooksPath"])
def test_a_repository_index_hook_never_runs(owner, via_hooks_path):
    marker = owner.parent / "POST_INDEX_CHANGE_RAN"
    hooks = owner.parent / "hooks" if via_hooks_path else owner / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    _marker_script(hooks / "post-index-change", marker)
    if via_hooks_path:
        _setup(owner, "config", "core.hooksPath", str(hooks))
    _sites()["worktree.git_status_entries"](owner)
    _sites()["checkpoint.compute_workspace_fingerprint"](owner)
    assert not marker.exists()


@_flip("EXECUTION")
def test_the_operator_scan_base_worktree_runs_no_checkout_hook(owner):
    from kriya.static_analysis import operator_scan

    markers = {name: owner.parent / f"HOOK_{name}" for name in ("post-checkout", "reference-transaction")}
    for name, marker in markers.items():
        _marker_script(owner / ".git" / "hooks" / name, marker)
    base = owner.parent / "operator-scan-base"
    operator_scan._git(str(owner), "worktree", "add", "--detach", str(base), "HEAD")   # run_operator_scan's argv
    operator_scan._git(str(owner), "worktree", "remove", "--force", str(base))
    assert [name for name, marker in markers.items() if marker.exists()] == []


def _plant_gpg(repo: Path, marker: Path) -> None:
    _setup(repo, "config", "commit.gpgSign", "true")
    _setup(repo, "config", "gpg.program", _marker_script(repo.parent / "gpg.sh", marker, exit_code=1))


@_flip("SKILLS")
def test_the_skill_commit_never_runs_a_repository_gpg_program(owner):
    from kriya.skills import skill

    marker = owner.parent / "GPG_RAN"
    _plant_gpg(owner, marker)
    skill.git_commit_if_tracked(str(owner / "skill.md"), "s19 skill")
    assert not marker.exists()
    assert _setup(owner, "log", "-1", "--format=%s").strip() == "s19 skill"   # and the commit is still made


@_flip("KERNEL")
def test_the_bootstrap_commit_never_runs_a_repository_gpg_program(tmp_path):
    from kriya.workflow import worktree

    repo = _repository(tmp_path, commit=False)
    marker = tmp_path / "BOOTSTRAP_GPG_RAN"
    _plant_gpg(repo, marker)
    worktree.create_git_worktree(str(repo))
    assert not marker.exists()
    assert _setup(repo, "rev-list", "--count", "HEAD").strip() == "1"


@_flip("PLUGIN")
def test_gittool_diff_runs_no_repository_diff_driver(owner):
    marker = owner.parent / "DIFF_DRIVER_RAN"
    (owner / ".git" / "info").mkdir(exist_ok=True)
    (owner / ".git" / "info" / "attributes").write_text("*.py diff=kx\n")
    _setup(owner, "config", "diff.kx.command", _marker_script(owner.parent / "diffcmd.sh", marker))
    expected = _setup(owner, "diff", "--no-ext-diff", "--no-textconv")
    assert _gittool("diff")(owner) == expected
    assert not marker.exists()


@_flip("PLUGIN")
def test_gittool_blame_runs_no_repository_textconv(owner):
    marker = owner.parent / "TEXTCONV_RAN"
    (owner / ".git" / "info").mkdir(exist_ok=True)
    (owner / ".git" / "info" / "attributes").write_text("*.py diff=kx\n")
    _setup(owner, "config", "diff.kx.textconv", _marker_script(owner.parent / "textconv.sh", marker, passthrough=True))
    _gittool("blame", file_path="a.py")(owner)
    assert not marker.exists()


# --------------------------------------------------------------------------------- held (owner decision Q1)

@_HELD_Q1
@pytest.mark.parametrize("site", ["checkpoint.compute_workspace_content_hash", "worktree.git_status_entries"])
def test_held_q1_a_repository_content_filter_never_runs(owner, site):
    marker = owner.parent / "FILTER_RAN"
    (owner / ".git" / "info").mkdir(exist_ok=True)
    (owner / ".git" / "info" / "attributes").write_text("*.py filter=kx\n")
    _setup(owner, "config", "filter.kx.clean", _marker_script(owner.parent / "clean.sh", marker, passthrough=True))
    _sites()[site](owner)
    assert not marker.exists()


# --------------------------------------------------------------------------------- the seam (kriya.platform.git_argv)

def test_the_seam_inserts_the_options_after_the_executable_and_returns_a_new_list():
    from kriya.platform import GIT_INVOCATION_OPTIONS, git_argv

    assert GIT_INVOCATION_OPTIONS == ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                                      "-c", "commit.gpgSign=false")
    audited = ["git", "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "m"]
    spawned = git_argv(audited)
    assert spawned == ["git", *GIT_INVOCATION_OPTIONS, "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "m"]
    assert spawned is not audited and audited == ["git", "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "m"]
    assert git_argv(("/usr/bin/git", "status")) == ["/usr/bin/git", *GIT_INVOCATION_OPTIONS, "status"]   # a resolved path


@pytest.mark.parametrize("argv", [[], ["gitk"], ["/usr/bin/python3", "-c", "x"], ["legit", "status"], ["git2"]])
def test_the_seam_refuses_anything_but_git(argv):
    from kriya.platform import git_argv

    with pytest.raises(ValueError):
        git_argv(argv)


def test_the_hardened_options_change_no_git_answer(owner):
    """The options are accepted by the installed git and change none of these answers (MEASURED on 684 probes)."""
    from kriya.platform import GIT_INVOCATION_OPTIONS

    for command in (["status", "--porcelain=v1", "-z", "--untracked-files=all"], ["ls-files", "-z"],
                    ["rev-parse", "HEAD"], ["diff", "--name-only"], ["log", "--oneline"]):
        assert _setup(owner, *GIT_INVOCATION_OPTIONS, *command) == _setup(owner, *command), command
