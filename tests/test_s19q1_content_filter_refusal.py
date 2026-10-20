"""S19 Q1 (OI-141 remainder): a content-filter driver defined by REPOSITORY-CONTROLLED configuration is refused, typed,
before any Kriya git operation that could run it (owner decision 2026-10-20: refusal, not blanking; no digest moves).

handover/MODULARIZATION/S19-PREFLIGHT.md section 2 (MEASURED, git 2.54.0): a repository's filter.<x>.clean / smudge /
process ran on the host through 19 Kriya call sites. Every pin plants the configuration AFTER the fixture is built (the
fixture's own git is hardened), binds it through .git/info/attributes (no tracked content changes), modifies a tracked
file WITHOUT changing its size (a same-second, same-size edit is racily clean, so status must run the clean filter to
compare - MEASURED: a size change never needs the filter), calls the REAL Kriya function, and asserts the typed refusal
AND that the marker the planted command would create is absent.

Isolation: GIT_CONFIG_GLOBAL points at a per-test file and GIT_CONFIG_NOSYSTEM=1, so a developer's own global filters
(git-lfs) can neither fire nor mask a pin; commit dates are fixed so the golden predictor is deterministic.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pytest

from tests._plugin_test_support import load_core_tools_module

REFUSED = "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"
# Characterization (expected-to-flip, MEASURED failing on 7eddc5d: every site ran the planted filter): each owner's
# correction commit removes its sites from _PENDING and the marks from the tests it flips.
_FLIPS = pytest.mark.xfail(strict=True, reason="S19 Q1: a repository-defined content filter still runs through Kriya git")
IN_REPOSITORY = "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY"


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19q1-git-home")
    config = home / "gitconfig"
    config.write_text("[user]\n\tname = s19q1\n\temail = s19q1@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def global_config(_isolated_git) -> Path:
    import os
    return Path(os.environ["GIT_CONFIG_GLOBAL"])


def _setup(cwd: Path, *args: str) -> str:
    """The test's own git: never runs a hook or fsmonitor, so only Kriya's call can create a marker."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def _marker_script(path: Path, marker: Path) -> str:
    """A passthrough filter (clean/smudge keep the bytes) that leaves a marker when it runs."""
    path.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
    path.chmod(0o755)
    return str(path)


def _repository(root: Path, *, name: str = "owner") -> Path:
    repo = root / name
    repo.mkdir()
    _setup(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    (repo / ".gitignore").write_text("__pycache__/\n.kriya/\n")
    (repo / "skill.md").write_text("skill\n")
    _setup(repo, "add", "-A")
    _setup(repo, "commit", "-q", "-m", "init")
    return repo


def _dirty(repo: Path) -> None:
    """A tracked change of the SAME size (racily clean: git must run the clean filter to compare)."""
    (repo / "a.py").write_text("x = 2\n")
    (repo / "skill.md").write_text("SKILL\n")


def _bind(repo: Path, name: str = "kx") -> None:
    info = Path(_setup(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text(f"*.py filter={name}\n*.md filter={name}\n")


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


def _reason(error: BaseException) -> Optional[str]:
    """The reason code of a refusal, also through a wrapping tool error's cause chain."""
    seen = 0
    while error is not None and seen < 10:
        code = getattr(error, "reason_code", None)
        if code:
            return code
        error = error.__cause__ or error.__context__
        seen += 1
    return None


def _gittool(sub: str, **kwargs) -> Callable[[Path], object]:
    def run(repo: Path) -> object:
        core_tools = load_core_tools_module()
        return asyncio.run(core_tools.GitTool()._run_in(core_tools.GitArgs(subcommand=sub, **kwargs), str(repo)))
    return run


def _index_changed(repo: Path) -> object:
    from unittest.mock import patch

    from kriya.analyzer.analyzer import RepositoryAnalyzer
    from kriya.config import AppConfig
    from tests.test_indexing import _embed_post

    cfg = AppConfig()
    cfg.paths.memory = str(repo.parent / "memory")
    with patch("httpx.AsyncClient.post", new=_embed_post()):
        return asyncio.run(RepositoryAnalyzer(str(repo)).index_repository(cfg, generate_conventions_skill=False,
                                                                           changed=True))


def _begin_run(repo: Path) -> object:
    from kriya.control.run_coordinator import begin_mutating_run

    with begin_mutating_run(str(repo)) as context:
        return context.run_id


def _sites() -> Dict[str, Callable[[Path], object]]:
    """Every Kriya entry whose git operation can run a content filter (S19 preflight: 19 sites), plus the run entry."""
    from kriya.analyzer import analyzer
    from kriya.static_analysis import operator_scan
    from kriya.workflow import checkpoint, milestone_completion, named_test_oracle, worktree

    def export_base(repo: Path) -> object:
        base = named_test_oracle.BaseTree(str(repo), "HEAD")      # ls-tree only: runs no filter
        (repo.parent / "export").mkdir(exist_ok=True)
        return named_test_oracle.export_base(base, str(repo.parent / "export"))

    return {
        "run_coordinator.begin_mutating_run": _begin_run,
        "worktree.git_status_entries": lambda o: worktree.git_status_entries(str(o)),
        "worktree.create_git_worktree": lambda o: worktree.create_git_worktree(str(o)),
        "worktree.create_git_worktree(reuse)": lambda o: worktree.create_git_worktree(str(o)),
        "worktree.remove_git_worktree": lambda o: worktree.remove_git_worktree(str(o), worktree.managed_worktree_path(str(o))),
        "checkpoint.compute_workspace_fingerprint": lambda o: checkpoint.compute_workspace_fingerprint(str(o)),
        "checkpoint.compute_workspace_content_hash": lambda o: checkpoint.compute_workspace_content_hash(str(o)),
        "milestone_completion.workspace_evidence_hash": lambda o: milestone_completion.workspace_evidence_hash(str(o)),
        "named_test_oracle.export_base": export_base,
        "analyzer._repository_revision": lambda o: analyzer._repository_revision(str(o)),
        "analyzer.index_repository(changed)": _index_changed,
        "operator_scan.changed_paths": lambda o: operator_scan.changed_paths(str(o), "HEAD"),
        "GitTool status": _gittool("status"),
        "GitTool diff": _gittool("diff"),
        "GitTool blame": _gittool("blame", file_path="a.py"),
        "GitTool commit": _gittool("commit", message="s19q1"),
    }


SITES = ["run_coordinator.begin_mutating_run", "worktree.git_status_entries", "worktree.create_git_worktree",
         "worktree.create_git_worktree(reuse)",
         "worktree.remove_git_worktree", "checkpoint.compute_workspace_fingerprint",
         "checkpoint.compute_workspace_content_hash", "milestone_completion.workspace_evidence_hash",
         "named_test_oracle.export_base", "analyzer._repository_revision", "analyzer.index_repository(changed)",
         "operator_scan.changed_paths", "GitTool status", "GitTool diff", "GitTool blame", "GitTool commit"]
_PENDING = set(SITES) - {"worktree.git_status_entries", "worktree.create_git_worktree",
                         "worktree.create_git_worktree(reuse)", "worktree.remove_git_worktree",
                         "checkpoint.compute_workspace_fingerprint", "checkpoint.compute_workspace_content_hash",
                         "milestone_completion.workspace_evidence_hash", "named_test_oracle.export_base",
                         "run_coordinator.begin_mutating_run", "analyzer._repository_revision",
                         "analyzer.index_repository(changed)", "operator_scan.changed_paths"}


def _site_params():
    return [pytest.param(site, marks=_FLIPS) if site in _PENDING else site for site in SITES]


# Sites that need the managed worktree to exist before the plant (the reuse / reset paths).
_NEEDS_WORKTREE = {"worktree.remove_git_worktree", "worktree.create_git_worktree(reuse)"}
# Cleanup that runs after a durable commit and from finally blocks: it reports the typed refusal and skips its git
# (raising there would fail a committed run or mask the error being handled); the next worktree creation refuses.
_REPORTS_REFUSAL = {"worktree.remove_git_worktree"}


def _plant_local(repo: Path, marker: Path, *keys: str, scope: str = "--local") -> None:
    command = _marker_script(repo.parent / "filter.sh", marker)
    for key in keys:
        _setup(repo, "config", scope, f"filter.kx.{key}", command)


def _assert_refused(site: str, repo: Path, marker: Path, code: str = REFUSED, caplog=None) -> None:
    error: Optional[BaseException] = None
    try:
        _sites()[site](repo)
    except Exception as raised:                       # noqa: BLE001 - the refusal may arrive wrapped (GitTool)
        error = raised
    assert not marker.exists(), f"{site} ran the repository's filter"          # the vector first
    if site in _REPORTS_REFUSAL:
        assert error is None and caplog is not None and f"{code}: " in caplog.text and "filter.kx." in caplog.text
        return
    assert error is not None and _reason(error) == code, f"{site}: {error!r}"   # then the typed refusal
    assert "filter.kx." in str(error)


# --------------------------------------------------------------------------------- the site matrix

@pytest.mark.parametrize("site", _site_params())
def test_a_repository_filter_is_refused_before_any_kriya_git_runs_it(owner, site, caplog):
    from kriya.workflow import worktree

    if site in _NEEDS_WORKTREE:
        worktree.create_git_worktree(str(owner))
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean", "smudge")
    _bind(owner)
    _dirty(owner)
    assert "filter" in (owner / ".git" / "config").read_text()     # the plant is really there
    with caplog.at_level(logging.ERROR):
        _assert_refused(site, owner, marker, caplog=caplog)


def test_the_skill_commit_refuses_without_running_the_filter_or_committing(owner, caplog):
    """skill.git_commit_if_tracked is best-effort bookkeeping that never raises: the refusal is logged (typed) and
    nothing is committed - the filter never runs."""
    from kriya.skills import skill

    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean")
    _bind(owner)
    _dirty(owner)
    head = _setup(owner, "rev-parse", "HEAD").strip()
    with caplog.at_level(logging.WARNING):
        skill.git_commit_if_tracked(str(owner / "skill.md"), "s19q1 skill")
    assert not marker.exists()
    assert _setup(owner, "rev-parse", "HEAD").strip() == head
    assert REFUSED in caplog.text


# --------------------------------------------------------------------------------- every composition (MEASURED origins)

def _plant_include(repo: Path, marker: Path, kind: str) -> None:
    command = _marker_script(repo.parent / "filter.sh", marker)
    body = f'[filter "kx"]\n\tclean = {command}\n\tsmudge = {command}\n'
    if kind == "include.path":
        (repo.parent / "inc.cfg").write_text(body)
        _setup(repo, "config", "include.path", str(repo.parent / "inc.cfg"))
    elif kind == "includeIf":
        (repo.parent / "incif.cfg").write_text(body)
        with open(repo / ".git" / "config", "a", encoding="utf-8") as handle:
            handle.write(f'[includeIf "gitdir:/"]\n\tpath = {repo.parent / "incif.cfg"}\n')
    elif kind == "nested include":
        (repo.parent / "inner.cfg").write_text(body)
        (repo.parent / "outer.cfg").write_text(f"[include]\n\tpath = {repo.parent / 'inner.cfg'}\n")
        _setup(repo, "config", "include.path", str(repo.parent / "outer.cfg"))
    elif kind == "include from the work tree":
        (repo / "filters.gitcfg").write_text(body)                     # untracked file of the work tree
        _setup(repo, "config", "include.path", "../filters.gitcfg")
    elif kind == "config.worktree":
        _setup(repo, "config", "extensions.worktreeConfig", "true")
        _plant_local(repo, marker, "clean", "smudge", scope="--worktree")
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["include.path", "includeIf", "nested include", "include from the work tree",
                                  "config.worktree"])
def test_an_included_or_worktree_scoped_filter_is_refused(owner, kind):
    marker = owner.parent / "FILTER_RAN"
    _plant_include(owner, marker, kind)
    _bind(owner)
    _dirty(owner)
    _assert_refused("worktree.git_status_entries", owner, marker)


def test_the_reused_worktree_s_own_config_worktree_is_refused_before_its_checkout(owner):
    """The managed worktree is reused across runs: its OWN config.worktree (extensions.worktreeConfig) is not the
    owner's configuration, so the reuse path inspects the worktree itself before `checkout -f` there."""
    from kriya.workflow import worktree

    candidate = Path(worktree.create_git_worktree(str(owner)))
    marker = owner.parent / "FILTER_RAN"
    _setup(owner, "config", "extensions.worktreeConfig", "true")
    _setup(candidate, "config", "--worktree", "filter.kx.smudge", _marker_script(owner.parent / "smudge.sh", marker))
    _bind(owner)
    (candidate / "a.py").write_text("x = 9\n")                        # the reset checkout has something to restore
    assert "filter.kx.smudge" not in _setup(owner, "config", "--list")   # the owner's own view: nothing defined
    _assert_refused("worktree.create_git_worktree(reuse)", owner, marker)


@pytest.mark.parametrize("key, site", [("clean", "worktree.git_status_entries"),
                                       ("smudge", "worktree.create_git_worktree"),
                                       ("process", "checkpoint.compute_workspace_content_hash")])
def test_each_driver_kind_alone_is_refused(owner, key, site):
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, key)
    _bind(owner)
    _dirty(owner)
    _assert_refused(site, owner, marker)


def test_a_repository_override_of_a_trusted_global_filter_is_refused(owner, global_config):
    """The ORIGIN decides, not the effective value: the global (trusted) definition is overridden by the repository."""
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write('[filter "kx"]\n\tclean = cat\n\tsmudge = cat\n')
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean")
    _bind(owner)
    _dirty(owner)
    _assert_refused("checkpoint.compute_workspace_content_hash", owner, marker)


def test_a_global_file_including_a_repository_file_is_refused(owner, global_config):
    """Fail-closed choice (owner question in the S19 Q1 report): an operator file that includes a file INSIDE the
    repository makes repository content the origin of the command (MEASURED: git reports scope global)."""
    marker = owner.parent / "FILTER_RAN"
    command = _marker_script(owner.parent / "filter.sh", marker)
    (owner / "filters.gitcfg").write_text(f'[filter "kx"]\n\tclean = {command}\n')
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {owner / 'filters.gitcfg'}\n")
    _bind(owner)
    _dirty(owner)
    _assert_refused("worktree.git_status_entries", owner, marker, code=IN_REPOSITORY)


def _embedded(parent: Path, path: str) -> Path:
    """An embedded repository at ``parent/path`` whose .py files are bound to filter kx by its OWN .gitattributes."""
    nested = parent / path
    nested.mkdir()
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "n.py").write_text("y = 1\n")
    (nested / ".gitattributes").write_text("*.py filter=kx\n")
    _setup(nested, "add", "-A")
    _setup(nested, "commit", "-q", "-m", "n")
    return nested


@pytest.mark.parametrize("shape", ["submodule", "gitlink without .gitmodules", "gitlink only HEAD still has"])
def test_a_filter_in_a_submodule_s_own_config_is_refused(tmp_path, shape):
    """MEASURED: the parent's status / diff / add run the CHILD git of every populated gitlink with the child's own
    configuration - also without .gitmodules, and (scratch-index hashes) for a gitlink only HEAD still has."""
    owner = _repository(tmp_path)
    marker = tmp_path / "SUBMODULE_FILTER_RAN"
    if shape == "submodule":
        source = _embedded(tmp_path, "source")
        _setup(owner, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "smod")
        nested = owner / "smod"
    else:
        nested = _embedded(owner, "smod")
        _setup(owner, "add", "smod")
    _setup(owner, "commit", "-q", "-m", "nested")
    _setup(nested, "config", "filter.kx.clean", _marker_script(tmp_path / "filter.sh", marker))
    (nested / "n.py").write_text("y = 2\n")                            # same size: racily clean
    site = "worktree.git_status_entries"
    if shape == "gitlink only HEAD still has":
        _setup(owner, "rm", "-q", "--cached", "smod")
        site = "checkpoint.compute_workspace_content_hash"
    _assert_refused(site, owner, marker)


# --------------------------------------------------------------------------------- positive controls (never refused)

def _global_filter(global_config: Path, marker: Path) -> None:
    command = _marker_script(global_config.parent / "global-filter.sh", marker)
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f'[filter "kx"]\n\tclean = {command}\n\tsmudge = {command}\n\trequired = true\n')


def test_a_global_only_filter_still_works_and_a_repository_may_reference_it(owner, global_config):
    """git-lfs-like: the operator defines the filter globally; the repository only REFERENCES it (.gitattributes is
    tracked content). Nothing is refused, the operator's filter still runs, and Kriya's answers are git's own."""
    from kriya.workflow import checkpoint, worktree

    marker = owner.parent / "GLOBAL_FILTER_RAN"
    _global_filter(global_config, marker)
    (owner / ".gitattributes").write_text("*.py filter=kx\n")
    _setup(owner, "add", ".gitattributes")
    _setup(owner, "commit", "-q", "-m", "reference the global filter")
    _dirty(owner)
    entries = worktree.git_status_entries(str(owner))
    assert entries == [(" M", "a.py", None), (" M", "skill.md", None)]
    assert checkpoint.compute_workspace_content_hash(str(owner)) is not None
    candidate = worktree.create_git_worktree(str(owner))
    assert (Path(candidate) / "a.py").read_text() == "x = 2\n"
    assert marker.exists()                                             # the operator's filter still runs


@pytest.mark.parametrize("plant", ["required only", "driver metadata", "empty command", "command scope"])
def test_benign_or_operator_filter_configuration_is_not_refused(owner, monkeypatch, plant):
    from kriya.workflow import checkpoint, worktree

    if plant == "required only":
        _setup(owner, "config", "filter.kx.required", "true")
    elif plant == "driver metadata":
        _setup(owner, "config", "filter.kx.driver", "metadata")
        _setup(owner, "config", "filter.kx.name", "a filter")
    elif plant == "empty command":
        _setup(owner, "config", "filter.kx.clean", "")
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.kx.clean")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "cat")
    if plant != "required only":
        _bind(owner)
    _dirty(owner)
    assert worktree.git_status_entries(str(owner)) == [(" M", "a.py", None), (" M", "skill.md", None)]
    assert checkpoint.compute_workspace_fingerprint(str(owner)).endswith(":dirty")


def _golden_repository(tmp_path: Path) -> Path:
    repo = _repository(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("b\n")
    _setup(repo, "add", "-A")
    _setup(repo, "commit", "-q", "-m", "second")
    _dirty(repo)
    (repo / "u.py").write_text("u = 1\n")
    return repo


# MEASURED at base 7eddc5d (before the refusal existed) on _golden_repository; the refusal moves none of them.
GOLDEN = {
    "compute_workspace_content_hash": "11823fc010ed3ca1895da30ebc4751cc36cc104af1e1f4b5fef3ac7fd824a68d",
    "workspace_evidence_hash": "11823fc010ed3ca1895da30ebc4751cc36cc104af1e1f4b5fef3ac7fd824a68d",
    "compute_workspace_fingerprint": "cc3cdff9d79473f47f204d70187cb717a7a92a7f:dirty",
    "compute_tree_hash": "f3793c220ece8c23538aad169dd02dbdece840af",
    "_repository_revision": "cc3cdff9d79473f47f204d70187cb717a7a92a7f+dirty",
}


def test_a_filter_free_repository_keeps_every_identity_digest(tmp_path):
    """Golden predictor over the touched hash functions (no digest may move)."""
    from kriya.analyzer import analyzer
    from kriya.workflow import checkpoint, milestone_completion

    repo = _golden_repository(tmp_path)
    measured = {
        "compute_workspace_content_hash": checkpoint.compute_workspace_content_hash(str(repo)),
        "workspace_evidence_hash": milestone_completion.workspace_evidence_hash(str(repo)),
        "compute_workspace_fingerprint": checkpoint.compute_workspace_fingerprint(str(repo)),
        "compute_tree_hash": checkpoint.compute_tree_hash(str(repo)),
        "_repository_revision": analyzer._repository_revision(str(repo)),
    }
    assert measured == GOLDEN


def test_a_run_still_begins_in_a_folder_that_is_not_a_repository(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert _begin_run(folder)


# --------------------------------------------------------------------------------- the choke point (dynamic proof)

_FILTER_CAPABLE = {"status", "diff", "add", "commit", "checkout", "archive", "blame", "rm", "stash", "reset", "merge",
                   "worktree"}
_INSPECTION = ("config", "--show-scope", "--show-origin", "-z", "--get-regexp")


def _subcommand(argv: List[str]) -> List[str]:
    """argv after git's global options (the seam's and any ``-c k=v`` / ``-C dir``)."""
    rest = argv[1:]
    while rest and rest[0] in ("-c", "-C"):
        rest = rest[2:]
    return rest


@pytest.mark.parametrize("site", _site_params())
def test_the_inspection_precedes_every_filter_capable_git_operation(owner, site, monkeypatch):
    """Record every git process the real site starts (subprocess and GitTool's ProcessController): the inspection of
    a folder comes before any git operation there that can run a filter."""
    import os

    from kriya.workflow import worktree

    if site in _NEEDS_WORKTREE:
        worktree.create_git_worktree(str(owner))
    _dirty(owner)
    spawned = []
    real_popen = subprocess.Popen

    class Recording(real_popen):
        def __init__(self, args, *rest, **kwargs):
            if isinstance(args, (list, tuple)) and args and Path(str(args[0])).name == "git":
                spawned.append((_subcommand([str(a) for a in args]), os.path.realpath(kwargs.get("cwd") or os.getcwd())))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    try:
        _sites()[site](owner)
    except Exception:                                    # e.g. GitTool commit with nothing new: the order is the subject
        pass
    inspected = set()
    capable = 0
    for command, cwd in spawned:
        if tuple(command[:len(_INSPECTION)]) == _INSPECTION:
            inspected.add(cwd)
        elif command and command[0] in _FILTER_CAPABLE:
            capable += 1
            assert cwd in inspected, (site, command, cwd)
    assert inspected, f"{site} never inspected the repository configuration"
    assert capable or site == "run_coordinator.begin_mutating_run"


# --------------------------------------------------------------------------------- the owner (kriya.repository.git)

def test_the_refusal_table_is_closed():
    from kriya.repository import git

    assert set(git.CONTENT_FILTER_REFUSALS) == {
        "GIT_CONTENT_FILTER_REPOSITORY_DEFINED", "GIT_CONTENT_FILTER_ORIGIN_IN_REPOSITORY",
        "GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED", "GIT_CONFIG_UNINSPECTABLE"}
    assert git.OPERATOR_FILE_SCOPES == {"system", "global"}
    assert git.COMMAND_SCOPE == "command"
    assert not issubclass(git.RepositoryContentFilterRefused, (RuntimeError, OSError, ValueError))


def test_the_refusal_names_key_scope_and_origin_never_the_command(owner):
    """The command can carry a secret: the message names where it is defined, never what it runs (one redactor)."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    _setup(owner, "config", "filter.kx.clean", "deploy --token=sk-supersecretvalue1234567890 https://u:pw@h/x")
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    message = str(raised.value)
    assert message.startswith("GIT_CONTENT_FILTER_REPOSITORY_DEFINED: ")
    assert "filter.kx.clean" in message and "local scope" in message and "file:.git/config" in message
    assert "deploy" not in message and "supersecret" not in message and "pw@" not in message
    assert (raised.value.key, raised.value.scope, raised.value.origin) == ("filter.kx.clean", "local",
                                                                           "file:.git/config")


def test_the_refusal_message_goes_through_the_one_redactor(tmp_path):
    """The origin is a path the repository controls; a secret-shaped part of it is redacted like any other text."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    owner = _repository(tmp_path)
    secret_dir = tmp_path / "password=hunter2hunter2"
    secret_dir.mkdir()
    (secret_dir / "inc.cfg").write_text('[filter "kx"]\n\tclean = x\n')
    _setup(owner, "config", "include.path", str(secret_dir / "inc.cfg"))
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert "hunter2" in raised.value.origin and "hunter2" not in str(raised.value)


def test_the_inspection_itself_runs_no_filter(owner):
    """Inspecting (and refusing) never executes the planted command, with the file bound and racily dirty."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean", "smudge", "process")
    _bind(owner)
    _dirty(owner)
    with pytest.raises(RepositoryContentFilterRefused):
        refuse_repository_content_filters(str(owner))
    assert not marker.exists()


@pytest.mark.parametrize("value, refused", [("", False), ("   ", True), (None, True)])
def test_only_an_explicitly_empty_command_is_not_executable(owner, value, refused):
    """MEASURED: an empty command runs nothing; whitespace is a (shell) command; a valueless key is not a value Git
    accepts (fatal at use), so it refuses (fail closed)."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    if value is None:
        with open(owner / ".git" / "config", "a", encoding="utf-8") as handle:
            handle.write('[filter "kx"]\n\tclean\n')
    else:
        _setup(owner, "config", "filter.kx.clean", value)
    if refused:
        with pytest.raises(RepositoryContentFilterRefused):
            refuse_repository_content_filters(str(owner))
    else:
        refuse_repository_content_filters(str(owner))


def test_dotted_and_mixed_case_driver_names_are_refused(owner):
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    with open(owner / ".git" / "config", "a", encoding="utf-8") as handle:
        handle.write('[FILTER "A.b"]\n\tSMUDGE = x\n')
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.key == "filter.A.b.smudge"


def test_an_unreadable_configuration_refuses(owner):
    from kriya.repository.git import (
        GIT_CONFIG_UNINSPECTABLE,
        RepositoryContentFilterRefused,
        refuse_repository_content_filters,
    )

    with open(owner / ".git" / "config", "a", encoding="utf-8") as handle:
        handle.write('[filter "kx"\n\tclean = x\n')
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == GIT_CONFIG_UNINSPECTABLE


def _fake_run(listing: bytes, monkeypatch):
    from kriya.repository import git

    real = git._run

    def run(cwd, *args):
        if args[0] == "config":
            return subprocess.CompletedProcess(list(args), 0, listing, b"")
        return real(cwd, *args)
    monkeypatch.setattr(git, "_run", run)


@pytest.mark.parametrize("record, code", [
    (b"blob\x00blob:HEAD:.gitconfig\x00filter.kx.clean\nx\x00", "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"),  # not trusted
    (b"global\x00standard input:\x00filter.kx.clean\nx\x00", "GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED"),
    (b"command\x00file:/etc/x\x00filter.kx.clean\nx\x00", "GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED"),
    (b"global\x00file:/etc/x\x00filter.kx.clean\nx", "GIT_CONFIG_UNINSPECTABLE"),           # not NUL-terminated
    (b"global\x00file:/etc/x\x00", "GIT_CONFIG_UNINSPECTABLE"),                            # a record cut short
])
def test_an_unrecognised_scope_origin_or_listing_refuses(owner, monkeypatch, record, code):
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    _fake_run(record, monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == code


@pytest.mark.parametrize("record", [b"global\x00file:/etc/gitconfig\x00filter.lfs.clean\ngit-lfs clean -- %f\x00",
                                    b"system\x00file:/etc/gitconfig\x00filter.lfs.smudge\ngit-lfs smudge\x00",
                                    b"command\x00command line:\x00filter.lfs.process\ngit-lfs filter-process\x00"])
def test_operator_scopes_outside_the_repository_are_trusted(owner, monkeypatch, record):
    from kriya.repository.git import refuse_repository_content_filters

    _fake_run(record, monkeypatch)
    refuse_repository_content_filters(str(owner))


def test_a_timeout_or_a_failing_spawn_refuses(owner, monkeypatch):
    from kriya.repository import git

    def timeout(cwd, *args):
        raise subprocess.TimeoutExpired(args, 60)
    monkeypatch.setattr(git, "_run", timeout)
    with pytest.raises(git.RepositoryContentFilterRefused) as raised:
        git.refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == git.GIT_CONFIG_UNINSPECTABLE

    def broken(cwd, *args):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(git, "_run", broken)
    with pytest.raises(git.RepositoryContentFilterRefused):
        git.refuse_repository_content_filters(str(owner))


def test_no_git_or_no_folder_means_nothing_can_run(tmp_path, monkeypatch):
    """Without a git executable (or a folder), no Kriya git call - and so no filter - can run: nothing to refuse."""
    from kriya.repository import git

    git.refuse_repository_content_filters(str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", str(tmp_path))
    git.refuse_repository_content_filters(str(tmp_path))


def test_an_unpopulated_gitlink_is_not_inspected_and_nesting_is_bounded(tmp_path, monkeypatch):
    from kriya.repository import git

    owner = _repository(tmp_path)
    nested = _embedded(owner, "smod")
    _setup(owner, "add", "smod")
    _setup(owner, "commit", "-q", "-m", "nested")
    git.refuse_repository_content_filters(str(owner))                  # populated, clean config: inspected, passes
    monkeypatch.setattr(git, "_MAX_NESTING", 0)
    with pytest.raises(git.RepositoryContentFilterRefused) as raised:
        git.refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == git.GIT_CONFIG_UNINSPECTABLE
    monkeypatch.setattr(git, "_MAX_NESTING", 16)
    shutil.rmtree(nested)
    git.refuse_repository_content_filters(str(owner))                  # the gitlink's folder is gone: nothing to inspect
    nested.mkdir()                                                     # unpopulated: git does not recurse there
    _setup(owner, "config", "-f", str(owner / ".gitmodules"), "submodule.smod.path", "smod")
    git.refuse_repository_content_filters(str(owner))


# --------------------------------------------------------------------------------- the refusal reaches the caller as itself
# A refusal raised mid-run (configuration that appeared after the run's entry check) must stay typed: never a generic
# sandbox error, a "plan unavailable" needs-review result or a sync failure. (Best-of-N's reset keeps its own contract:
# a failed reset ends sampling and the loop continues in its worktree, where the next inspection refuses, typed.)

def _refusal(path: str = "/ws"):
    from kriya.repository.git import GIT_CONTENT_FILTER_REPOSITORY_DEFINED, RepositoryContentFilterRefused

    return RepositoryContentFilterRefused(GIT_CONTENT_FILTER_REPOSITORY_DEFINED, path, key="filter.kx.smudge",
                                          scope="local", origin="file:.git/config")


def test_the_worktree_sync_lets_the_refusal_through(owner, monkeypatch):
    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow import worktree

    def refuse(*_args, **_kwargs):
        raise _refusal(str(owner))
    monkeypatch.setattr(worktree, "git_status_entries", refuse)
    with pytest.raises(RepositoryContentFilterRefused):
        worktree._sync_uncommitted_changes_into_worktree(str(owner), str(owner.parent / "sandbox"))


def _entry_checks_pass(monkeypatch) -> None:
    """The configuration appears AFTER the run's entry check and the content hashes (no-op there); the sandbox
    creation (kriya.workflow.worktree) is the first inspection that sees it."""
    from kriya.control import run_coordinator
    from kriya.workflow import checkpoint

    monkeypatch.setattr(checkpoint, "refuse_repository_content_filters", lambda _path: None)
    monkeypatch.setattr(run_coordinator, "refuse_repository_content_filters", lambda _path: None)


def test_the_direct_development_loop_lets_the_sandbox_refusal_through(tmp_path, monkeypatch):
    from _chaos_harness import (
        CALC,
        CALC_WITH_SUB,
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        run_direct,
    )

    from kriya.repository.git import RepositoryContentFilterRefused

    workspace = git_workspace(tmp_path, {"calc.py": CALC})
    marker = tmp_path / "SMUDGE_RAN"
    _setup(workspace, "config", "filter.kx.smudge", _marker_script(tmp_path / "smudge.sh", marker))  # smudge only:
    _bind(workspace)                                                    # nothing before the sandbox runs it
    _entry_checks_pass(monkeypatch)
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime), pytest.raises(RepositoryContentFilterRefused):
        run_direct(chaos_engine(chaos_config()), "add sub to calc.py", workspace)
    assert not marker.exists()


def test_the_enforce_plan_worktree_lets_the_refusal_through(tmp_path, monkeypatch):
    from _enforce_unit_harness import run_enforce

    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow.plan_schema import EngineeringPlan

    marker = tmp_path / "SMUDGE_RAN"

    def plant(workspace):
        _setup(workspace, "config", "filter.kx.smudge", _marker_script(tmp_path / "smudge.sh", marker))
        _bind(workspace)

    _entry_checks_pass(monkeypatch)
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}], "acceptance_criteria": [],
        "subtasks": [{"id": "s1", "description": "edit shop.py", "execution_method": "model",
                      "planned_files": [{"path": "shop.py", "action": "modify"}], "depends_on": [], "provides": ["p_s1"],
                      "relevant_global_invariant_ids": ["gi1"],
                      "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}]})
    with pytest.raises(RepositoryContentFilterRefused):
        run_enforce(tmp_path, plan=plan, units={"s1": ("shop.py", "PAGE = 2\n")}, base_files={"shop.py": "PAGE = 1\n"},
                    on_workspace=plant)
    assert not marker.exists()
