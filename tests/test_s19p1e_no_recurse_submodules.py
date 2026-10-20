"""S19 P1e item 2 (OI-161; owner pre-authorized 2026-10-21): the managed worktree's two `checkout -f` sites never recurse
into submodules.

MEASURED by the independent P1d characterization (scratch p1d_char/probes/b_sub.py, c_controls.py; git 2.54.0 Apple and
Homebrew): with a repository's own `submodule.recurse=true`, Kriya's worktree reuse / remove `checkout -f <target>`
recursed into a submodule and ran that CHILD's content filter where the guard could not see it:

* B3 - the gitlink exists only in the TARGET tree, the child's git dir persists in `<gitdir>/modules/sub`, its work tree
  is not populated: `_Listing.gitlinks` reads only the index and HEAD, `_refuse` skips a gitlink without `<child>/.git`,
  and git reattaches the kept git dir and smudges the child's files;
* B4 - the child's config `include.path` names a file the PARENT's recursing checkout itself creates: the definition
  does not exist at inspection time.

Controls K2/K3: `--no-recurse-submodules` runs nothing in either. The fix passes it at both sites, exactly as
`_add_worktree` (reset) and the operator scan already do; the child guard stays for populated children and status-type
operations. Each pin drives the REAL create_git_worktree reuse path or remove_git_worktree; the marker FAILS on the
unfixed bytes (S19 P1e report). Isolation: per-test GIT_CONFIG_GLOBAL + HOME, GIT_CONFIG_NOSYSTEM=1.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict

import pytest

INERT = "[user]\n\tname = s19p1e\n\temail = s19p1e@local\n[protocol \"file\"]\n\tallow = always\n"


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("s19p1e-git-home")
    config = home / ".gitconfig"
    config.write_text(INERT)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_ATTR_SOURCE", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    return config


def _git(cwd: Path, *args: str) -> str:
    """The test's own git (hooks / fsmonitor off; runs before any filter is planted, or never in a filter's reach)."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()


def _marker_command(tmp_path: Path, marker: Path) -> str:
    script = tmp_path / "smudge.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\nexec cat\n')
    script.chmod(0o755)
    return str(script)


def _fixture(tmp_path: Path, *, gap: bool) -> Dict[str, str]:
    """The characterization's shape: a child whose second commit binds `*.txt filter=kx`; a parent recording it
    (gap=False: p1 records c1, p2 records c2; gap=True: p1 has no submodule, p2 adds it at c2); the owner workspace a
    clone of the parent whose submodule is NOT initialized there."""
    child = tmp_path / "child_origin"
    child.mkdir()
    _git(child, "init", "-q", "-b", "main")
    (child / "a.txt").write_text("hello\n")
    _git(child, "add", "-A")
    _git(child, "commit", "-q", "-m", "c1")
    c1 = _git(child, "rev-parse", "HEAD")
    (child / ".gitattributes").write_text("*.txt filter=kx\n")
    (child / "x.txt").write_text("smudged on checkout\n")
    _git(child, "add", "-A")
    _git(child, "commit", "-q", "-m", "c2 binds kx")
    c2 = _git(child, "rev-parse", "HEAD")
    parent = tmp_path / "parent_origin"
    parent.mkdir()
    _git(parent, "init", "-q", "-b", "main")
    (parent / "p.md").write_text("parent\n")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-q", "-m", "p0")
    if gap:
        p1 = _git(parent, "rev-parse", "HEAD")
        _git(parent, "submodule", "add", "-q", str(child), "sub")
        _git(parent / "sub", "checkout", "-q", c2)
        _git(parent, "add", "-A")
        _git(parent, "commit", "-q", "-m", "p2 adds sub@c2")
    else:
        _git(parent, "submodule", "add", "-q", str(child), "sub")
        _git(parent / "sub", "checkout", "-q", c1)
        _git(parent, "add", "-A")
        _git(parent, "commit", "-q", "-m", "p1 sub@c1")
        p1 = _git(parent, "rev-parse", "HEAD")
        _git(parent / "sub", "checkout", "-q", c2)
        _git(parent, "add", "-A")
        _git(parent, "commit", "-q", "-m", "p2 sub@c2")
    p2 = _git(parent, "rev-parse", "HEAD")
    workspace = tmp_path / "ws"
    _git(tmp_path, "clone", "-q", str(parent), str(workspace))
    (workspace / ".git" / "info" / "exclude").write_text(".kriya/\n")
    return {"child": str(child), "parent": str(parent), "ws": str(workspace), "c1": c1, "c2": c2, "p1": p1, "p2": p2}


def _move(workspace: str, revision: str) -> None:
    """The owner workspace moves on (the test's own git, never recursing): Kriya's next reuse targets ``revision``."""
    _git(Path(workspace), "checkout", "-q", "--no-recurse-submodules", "-f", revision)


def _run_site(site: str, workspace: str) -> None:
    from kriya.workflow import worktree

    if site == "reuse":
        worktree.create_git_worktree(workspace)
    else:
        worktree.remove_git_worktree(workspace, worktree.managed_worktree_path(workspace))


def _b3(tmp_path: Path, definition: str, global_config: Path):
    """B3: after the setup below the managed worktree is at p1 (no gitlink), its child git dir kept in
    <worktree gitdir>/modules/sub, the child work tree NOT populated; the definition lives in that kept git dir's
    config (repository origin) or in the operator's global file (an unproven `/bin/sh` command the guard would refuse
    if it saw it active)."""
    from kriya.workflow import worktree

    shape = _fixture(tmp_path, gap=True)
    _move(shape["ws"], shape["p2"])
    wt = Path(worktree.create_git_worktree(shape["ws"]))
    _git(wt, "submodule", "update", "--init", "-q")                    # the child populated in the worktree (@c2)
    _git(Path(shape["ws"]), "config", "submodule.recurse", "true")      # the repository's own configuration
    _git(wt, "-c", "submodule.recurse=true", "checkout", "-q", "-f", shape["p1"])   # setup: the recursing move to p1
    modules = Path(_git(wt, "rev-parse", "--path-format=absolute", "--git-dir")) / "modules" / "sub"
    assert not os.path.lexists(wt / "sub" / ".git") and modules.is_dir()           # the B3 shape, really
    marker = tmp_path / "KX_SMUDGE_RAN"
    command = _marker_command(tmp_path, marker)
    if definition == "child_repository":
        _git(modules, "config", "filter.kx.smudge", command)
    else:
        global_config.write_text(INERT + f'[filter "kx"]\n\tsmudge = /bin/sh {command}\n')
    _move(shape["ws"], shape["p2"])
    return shape, wt, marker


@pytest.mark.parametrize("definition", ["child_repository", "operator_unproven"])
@pytest.mark.parametrize("site", ["reuse", "remove"])
def test_b3_a_gitlink_only_the_target_records_never_reattaches_a_kept_child(tmp_path, _isolated_git, site, definition):
    shape, wt, marker = _b3(tmp_path, definition, _isolated_git)
    _run_site(site, shape["ws"])
    assert not marker.exists(), "the kept child's filter ran through Kriya's recursing checkout"
    assert _git(wt, "rev-parse", "HEAD") == shape["p2"]                  # the checkout itself still happened
    assert not os.path.lexists(wt / "sub" / ".git")                      # and left the submodule alone


def test_b3_the_vector_is_real_when_the_checkout_recurses(tmp_path, _isolated_git):
    """Negative-control validity: the same state, the same checkout WITH recursion, runs the kept child's filter."""
    shape, wt, marker = _b3(tmp_path, "child_repository", _isolated_git)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "-q", "-f", shape["p2"]], cwd=wt,
                   capture_output=True, stdin=subprocess.DEVNULL, check=False)  # recurses via the repository config
    assert marker.exists()


def _b4(tmp_path: Path, materialized: bool):
    """B4: a populated child (@c1) whose own config includes <worktree>/cfg/child.cfg; the target p3 records the child
    at c2 (binds kx) and - materialized=True - creates that file (defining filter.kx.smudge); K1 (False): it creates an
    unrelated file instead."""
    from kriya.workflow import worktree

    shape = _fixture(tmp_path, gap=False)
    marker = tmp_path / "KX_SMUDGE_RAN"
    parent = Path(shape["parent"])
    _git(parent, "-c", "submodule.recurse=true", "checkout", "-q", "-f", "main")   # p2, on the branch the clone fetches
    (parent / "cfg").mkdir()
    if materialized:
        (parent / "cfg" / "child.cfg").write_text(f'[filter "kx"]\n\tsmudge = {_marker_command(tmp_path, marker)}\n')
    else:
        (parent / "cfg" / "other.cfg").write_text("[core]\n\tquotepath = true\n")
    _git(parent, "add", "cfg")
    _git(parent, "commit", "-q", "-m", "p3")
    p3 = _git(parent, "rev-parse", "HEAD")
    _git(Path(shape["ws"]), "fetch", "-q", "origin")
    _move(shape["ws"], shape["p1"])
    wt = Path(worktree.create_git_worktree(shape["ws"]))
    _git(wt, "submodule", "update", "--init", "-q")                    # the child populated in the worktree (@c1)
    _git(wt / "sub", "config", "include.path", str(wt / "cfg" / "child.cfg"))      # not there yet
    _git(Path(shape["ws"]), "config", "submodule.recurse", "true")
    _move(shape["ws"], p3)
    return shape, wt, marker, p3


@pytest.mark.parametrize("site", ["reuse", "remove"])
def test_b4_a_child_include_the_checkout_creates_never_loads_in_that_checkout(tmp_path, site):
    shape, wt, marker, p3 = _b4(tmp_path, materialized=True)
    _run_site(site, shape["ws"])
    assert not marker.exists(), "the child's include, created by Kriya's recursing checkout, ran its filter"
    assert _git(wt, "rev-parse", "HEAD") == p3 and (wt / "cfg" / "child.cfg").is_file()
    assert _git(wt / "sub", "rev-parse", "HEAD") == shape["c1"]          # the submodule is not updated any more


def test_k1_b4_negative_control_an_include_the_target_does_not_create_runs_nothing(tmp_path):
    """K1 (passes on the unfixed bytes too): the include mechanism is the cause - nothing else in B4 runs a filter."""
    shape, wt, marker, p3 = _b4(tmp_path, materialized=False)
    _run_site("reuse", shape["ws"])
    assert not marker.exists() and _git(wt, "rev-parse", "HEAD") == p3


def test_b4_the_vector_is_real_when_the_checkout_recurses(tmp_path):
    shape, wt, marker, p3 = _b4(tmp_path, materialized=True)
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "-q", "-f", p3], cwd=wt,
                   capture_output=True, stdin=subprocess.DEVNULL, check=False)  # recurses via the repository config
    assert marker.exists()


def test_a_recursing_configuration_no_longer_breaks_the_worktree_reuse(tmp_path, _isolated_git):
    """K4 shape, no filter at all (MEASURED behaviour change): the submodule initialized in the MAIN workspace only and
    `submodule.recurse=true` in the operator's global file. The recursing reuse checkout died (`fatal: not a git
    repository: .../worktrees/worktree/modules/sub`, `could not reset submodule index`, exit 128); now the worktree
    moves to the target and the submodule stays unpopulated there, exactly as a freshly added worktree."""
    from kriya.workflow import worktree

    shape = _fixture(tmp_path, gap=False)
    _move(shape["ws"], shape["p1"])
    _git(Path(shape["ws"]), "submodule", "update", "--init", "-q")      # MAIN workspace only
    wt = Path(worktree.create_git_worktree(shape["ws"]))
    assert not os.path.lexists(wt / "sub" / ".git")
    _isolated_git.write_text(INERT + "[submodule]\n\trecurse = true\n")
    _move(shape["ws"], shape["p2"])
    assert worktree.create_git_worktree(shape["ws"]) == str(wt)
    assert _git(wt, "rev-parse", "HEAD") == shape["p2"] and not os.path.lexists(wt / "sub" / ".git")


def test_both_checkout_sites_pass_no_recurse_submodules(tmp_path, monkeypatch):
    """The argv of both sites (the dynamic proof; the pins above prove the effect)."""
    from kriya.workflow import worktree

    shape = _fixture(tmp_path, gap=False)
    _move(shape["ws"], shape["p1"])
    worktree.create_git_worktree(shape["ws"])
    _move(shape["ws"], shape["p2"])
    spawned = []
    real = subprocess.run

    def recording(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and "checkout" in argv:
            spawned.append(list(argv))
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(worktree.subprocess, "run", recording)
    _run_site("reuse", shape["ws"])
    _run_site("remove", shape["ws"])
    assert len(spawned) == 2
    for argv in spawned:
        tail = argv[argv.index("checkout"):]
        assert tail == ["checkout", "-f", "--no-recurse-submodules", shape["p2"]], argv
