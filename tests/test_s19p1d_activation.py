"""S19 P1d (OI-161, owner decision 2026-10-21): ACTIVATION-aware content-filter inspection.

MEASURED at base 746fc93 (scratch p1d): the owner host's global configuration defines `filter.media.clean/smudge = git
media ... %f` and `filter.lfs.*` without git-lfs installed; under the P1c provenance rule every repository was refused,
used or not. Now an operator (global/system) or command-scope definition is judged only when the actual git operation
can ACTIVATE it: a path it can touch resolves `filter=<driver>` through git's own attribute machinery (`git check-attr`
in the inspected context). Dormant -> usable; active + unproven -> GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN naming the
ACTIVE driver and the operator remedy; repository-origin definitions are still refused, active or not.

Every fixture reproduces the host's stale lines in a job-local HOME / GIT_CONFIG_GLOBAL (never the real ~/.gitconfig).
The planted executable vector is the measured one: the global `git media clean %f` + a repository-local
`alias.media = !<script>` runs repository code (P1c); the script leaves a marker under tmp_path.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List

import pytest

from tests.test_s19q1_content_filter_refusal import (
    _NEEDS_WORKTREE,
    GOLDEN,
    SITES,
    _begin_run,
    _bind,
    _dirty,
    _embedded,
    _golden_repository,
    _marker_script,
    _repository,
    _setup,
    _sites,
)

UNPROVEN = "GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN"
UNINSPECTABLE = "GIT_CONFIG_UNINSPECTABLE"
# The owner host's stale lines (OI-161), reproduced verbatim in shape: git media (an evaluator: `git <alias>`) and an
# uninstalled git-lfs (unresolved).
STALE = ('[filter "media"]\n\tclean = git media clean %f\n\tsmudge = git media smudge %f\n\trequired = true\n'
         '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n'
         '\tprocess = git-lfs filter-process\n\trequired = true\n')


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19p1d-git-home")
    config = home / ".gitconfig"                     # HOME/.gitconfig: the Open Folder probe reads this one too
    # The S19 Q1 commit identity: the golden predictor (GOLDEN) binds commit ids made with it.
    config.write_text("[user]\n\tname = s19q1\n\temail = s19q1@local\n" + STALE)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_EXEC_PATH", "GIT_CEILING_DIRECTORIES", "GIT_ATTR_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    assert shutil.which("git-lfs") is None            # the host shape: git-lfs is NOT installed
    return config


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


def _plain(cwd: Path, *args: str) -> str:
    """The test's own git WITHOUT the stale operator lines (an inert global configuration beside the fixture's), so
    committing a bound file runs no planted driver and only Kriya's call can leave a marker."""
    inert = Path(os.environ["GIT_CONFIG_GLOBAL"]).parent / "inert.gitconfig"
    if not inert.exists():
        inert.write_text("[user]\n\tname = s19q1\n\temail = s19q1@local\n")
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                          check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": str(inert)}).stdout


def _alias_marker(repo: Path, driver: str = "media") -> Path:
    """The measured vector: `git <driver> ...` falls back to a repository-local alias that runs repository code."""
    marker = repo.parent / f"{driver.upper()}_RAN"
    _setup(repo, "config", f"alias.{driver}", "!" + _marker_script(repo.parent / f"{driver}-alias.sh", marker))
    return marker


def _status(repo: Path):
    from kriya.workflow import worktree
    return worktree.git_status_entries(str(repo))


def _refusal(error: BaseException):
    from kriya.repository.git import RepositoryContentFilterRefused

    while error is not None and not isinstance(error, RepositoryContentFilterRefused):
        error = error.__cause__ or error.__context__
    return error


def _assert_active_refusal(call: Callable[[], object], marker: Path, driver: str = "media",
                           cause: str = "evaluator (git)") -> str:
    with pytest.raises(Exception) as raised:
        call()
    assert not marker.exists(), "the active filter ran (during the inspection or after it)"
    refusal = _refusal(raised.value)
    assert refusal is not None, repr(raised.value)
    message = str(refusal)
    assert refusal.reason_code == UNPROVEN, message
    assert refusal.driver == driver, message
    assert f"the filter driver '{driver}' is ACTIVE here: git resolves filter={driver} for " in message
    assert cause in message and "Operator remedy: " in message and "Kriya never changes git configuration" in message
    assert "git media" not in message and "git-lfs clean" not in message          # never the command text
    return message


# --------------------------------------------------------------------------------- POSITIVE: dormant stale lines

def test_the_vector_is_real_when_unguarded(owner):
    """Negative-control validity: with the inspection switched off, an ACTIVE `git media` filter plus a repository alias
    really runs repository code through Kriya's own status (so an absent marker below means 'never ran')."""
    from unittest.mock import patch

    marker = _alias_marker(owner)
    _bind(owner, "media")
    _dirty(owner)
    with patch("kriya.workflow.worktree.refuse_repository_content_filters", lambda *a, **k: None):
        _status(owner)
    assert marker.exists()


@pytest.mark.parametrize("site", SITES)
def test_stale_unused_global_media_and_lfs_lines_never_refuse_an_unrelated_repository(owner, site):
    """OI-161 positive control over every guarded Kriya entry: the stale lines are defined but no path any operation
    can touch resolves filter=media or filter=lfs."""
    from kriya.workflow import worktree

    if site in _NEEDS_WORKTREE:
        worktree.create_git_worktree(str(owner))
    marker = _alias_marker(owner)
    _dirty(owner)
    try:
        _sites()[site](owner)
    except Exception as error:                        # noqa: BLE001 - e.g. GitTool commit with nothing staged
        assert _refusal(error) is None, f"{site}: {error}"
    assert not marker.exists()


def test_the_stale_lines_move_no_identity_digest(tmp_path):
    """Byte-identical behaviour: the golden predictor of the filter-free repository (S19 Q1, MEASURED at 7eddc5d)
    holds unchanged with the stale lines defined."""
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


def test_open_folder_describes_an_unrelated_repository_under_the_stale_lines(owner):
    from kriya.tools import git_metadata

    _dirty(owner)
    facts = git_metadata.describe_checkout(str(owner))
    assert facts.reason is None and facts.dirty is True and facts.changed_entries == 2


def test_a_run_begins_in_a_plain_folder_under_the_stale_lines(tmp_path):
    """Not a repository: git activates nothing there (MEASURED: `not a git repository`); the greenfield bootstrap that
    follows (init + an EMPTY commit) converts no file, and the new worktree is inspected in its own context."""
    from kriya.workflow import worktree

    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "a.py").write_text("x = 1\n")
    assert _begin_run(folder)
    created = Path(worktree.create_git_worktree(str(folder)))
    assert created.is_dir()


def test_an_unborn_repository_s_content_hash_is_unavailable_not_refused(tmp_path):
    """`read-tree HEAD` of a repository without a commit materializes nothing: None, exactly as before."""
    from kriya.workflow import checkpoint

    repo = tmp_path / "unborn"
    repo.mkdir()
    _setup(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    assert checkpoint.compute_workspace_content_hash(str(repo)) is None


@pytest.mark.parametrize("attributes", ["*.py -filter\n", "*.py !filter\n", "*.py filter=\n", "*.py filter\n",
                                        "*.py filter=undefined\n", "*.txt filter=media\n"],
                         ids=["unset", "unspecified", "empty", "set", "undefined-driver", "other-paths"])
def test_a_negated_empty_or_unrelated_filter_attribute_activates_nothing(owner, attributes):
    """Git looks a driver up by the attribute's STRING value only (MEASURED check-attr: unset, unspecified, "", set)."""
    marker = _alias_marker(owner)
    (owner / ".gitattributes").write_text(attributes)
    _dirty(owner)
    assert (" M", "a.py", None) in _status(owner)
    assert not marker.exists()


def test_an_active_operator_program_outside_the_repository_still_runs(owner, _isolated_git):
    """An ACTIVE trusted definition whose program is provably the operator's (absolute, outside the repository) is
    admitted and really runs, next to the dormant stale lines."""
    marker = owner.parent / "OPERATOR_FILTER_RAN"
    operator = owner.parent / "operator-bin"
    operator.mkdir()
    program = _marker_script(operator / "op-filter", marker)
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f'[filter "op"]\n\tclean = {program}\n\tsmudge = {program}\n')
    _bind(owner, "op")
    _dirty(owner)
    assert [entry for entry in _status(owner) if entry[0] != "??"] == [(" M", "a.py", None), (" M", "skill.md", None)]
    assert marker.exists()


# --------------------------------------------------------------------------------- NEGATIVE: the driver is ACTIVE

def _activate(repo: Path, how: str, global_config: Path, driver: str = "media") -> None:
    if how == "committed .gitattributes":
        (repo / ".gitattributes").write_text(f"*.py filter={driver}\n")
        _plain(repo, "add", ".gitattributes")
        _plain(repo, "commit", "-q", "-m", "bind")
    elif how == "work-tree .gitattributes":
        (repo / ".gitattributes").write_text(f"*.py filter={driver}\n")
    elif how == ".git/info/attributes":
        _bind(repo, driver)
    elif how == "subdirectory .gitattributes":
        (repo / "sub").mkdir()
        (repo / "sub" / "m.py").write_text("m = 1\n")
        _plain(repo, "add", "sub/m.py")
        _plain(repo, "commit", "-q", "-m", "sub")
        (repo / "sub" / ".gitattributes").write_text(f"*.py filter={driver}\n")
        (repo / "sub" / "m.py").write_text("m = 2\n")
    elif how == "global attributes file":
        attributes = global_config.parent / "operator-attributes"
        attributes.write_text(f"*.py filter={driver}\n")
        with open(global_config, "a", encoding="utf-8") as handle:
            handle.write(f"[core]\n\tattributesFile = {attributes}\n")
    elif how == "macro":
        (repo / ".gitattributes").write_text(f"[attr]bound filter={driver}\n*.py bound\n")
    elif how == "attr.tree":
        (repo / ".gitattributes").write_text(f"*.py filter={driver}\n")
        _plain(repo, "add", ".gitattributes")
        _plain(repo, "commit", "-q", "-m", "bound")
        bound = _setup(repo, "rev-parse", "HEAD").strip()
        _plain(repo, "rm", "-q", ".gitattributes")
        _plain(repo, "commit", "-q", "-m", "unbound")
        _setup(repo, "config", "attr.tree", bound)            # git reads attributes from that tree only
    else:
        raise AssertionError(how)


_SOURCES = ["committed .gitattributes", "work-tree .gitattributes", ".git/info/attributes",
            "subdirectory .gitattributes", "global attributes file", "macro", "attr.tree"]


@pytest.mark.parametrize("how", _SOURCES)
def test_a_repository_that_activates_the_stale_media_filter_is_refused_naming_it(owner, _isolated_git, how):
    marker = _alias_marker(owner)
    _activate(owner, how, _isolated_git)
    _dirty(owner)
    _assert_active_refusal(lambda: _status(owner), marker)


@pytest.mark.parametrize("how", ["committed .gitattributes", ".git/info/attributes"])
def test_the_uninstalled_lfs_filter_is_refused_when_active_and_named(owner, _isolated_git, how):
    """The ACTIVE driver is named, not the first definition listed (the dormant media lines come first)."""
    _activate(owner, how, _isolated_git, driver="lfs")
    _dirty(owner)
    _assert_active_refusal(lambda: _status(owner), owner.parent / "UNUSED", driver="lfs",
                           cause="does not resolve to an executable")


def test_a_driver_literally_named_set_is_active_for_a_bare_filter_attribute(owner, _isolated_git):
    """check-attr prints `set` both for a bare `filter` and for `filter=set`: matched literally (over-approximation)."""
    marker = _alias_marker(owner, "set")
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write('[filter "set"]\n\tclean = git set clean %f\n')
    (owner / ".gitattributes").write_text("*.py filter\n")
    _dirty(owner)
    _assert_active_refusal(lambda: _status(owner), marker, driver="set")


def test_a_repository_alias_behind_an_active_operator_filter_never_runs(owner):
    from kriya.repository.git import refuse_repository_content_filters

    marker = _alias_marker(owner)
    _bind(owner, "media")
    _dirty(owner)
    _assert_active_refusal(lambda: refuse_repository_content_filters(str(owner)), marker)


def test_activation_is_decided_for_the_whole_work_tree_even_from_a_subdirectory(owner):
    """check-attr runs at the work-tree top over top-relative paths: a caller inspecting from `sub/` still sees the
    bound file outside it (git status there covers the whole work tree)."""
    from kriya.repository.git import refuse_repository_content_filters

    marker = _alias_marker(owner)
    (owner / "sub").mkdir()
    (owner / "sub" / "keep.txt").write_text("k\n")
    (owner / ".gitattributes").write_text("a.py filter=media\n")
    _dirty(owner)
    _assert_active_refusal(lambda: refuse_repository_content_filters(str(owner / "sub")), marker)


# --------------------------------------------------------------------------------- the actual context and tree

def test_a_filter_active_only_in_a_new_worktree_s_context_is_refused_before_its_reset(owner, _isolated_git):
    """The operator defines `wtonly` only for linked worktrees (global includeIf gitdir:**/worktrees/**) and the
    repository's committed .gitattributes binds it. The main work tree's context does not define it; the new worktree
    (`worktree add --no-checkout`: no index, empty work tree - MEASURED) activates it only through HEAD's tree, which
    its reset materializes."""
    from kriya.workflow import worktree

    operator_file = owner.parent / "worktree-only.gitcfg"
    operator_file.write_text('[filter "wtonly"]\n\tclean = git wtonly clean %f\n\tsmudge = git wtonly smudge %f\n')
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f'[includeIf "gitdir:**/worktrees/**"]\n\tpath = {operator_file}\n')
    marker = _alias_marker(owner, "wtonly")
    _activate(owner, "committed .gitattributes", _isolated_git, driver="wtonly")
    _status(owner)                                    # the main context: not defined there, nothing refused
    _assert_active_refusal(lambda: worktree.create_git_worktree(str(owner)), marker, driver="wtonly")


def test_the_operator_scan_base_worktree_reads_its_head_tree(owner, _isolated_git):
    from kriya.config import AppConfig
    from kriya.static_analysis import operator_scan

    operator_file = owner.parent / "worktree-only.gitcfg"
    operator_file.write_text('[filter "wtonly"]\n\tsmudge = git wtonly smudge %f\n')
    with open(_isolated_git, "a", encoding="utf-8") as handle:
        handle.write(f'[includeIf "gitdir:**/worktrees/**"]\n\tpath = {operator_file}\n')
    marker = _alias_marker(owner, "wtonly")
    _activate(owner, "committed .gitattributes", _isolated_git, driver="wtonly")
    _assert_active_refusal(lambda: operator_scan.run_operator_scan(AppConfig(), str(owner), "HEAD"), marker,
                           driver="wtonly")
    assert _setup(owner, "worktree", "list", "--porcelain").count("worktree ") == 1


def _bound_then_unbound(repo: Path) -> str:
    """A commit whose tree binds *.py to media, then HEAD without it: only that older tree's attributes activate it."""
    (repo / ".gitattributes").write_text("*.py filter=media\n")
    _plain(repo, "add", ".gitattributes")
    _plain(repo, "commit", "-q", "-m", "bound")
    bound = _setup(repo, "rev-parse", "HEAD").strip()
    _plain(repo, "rm", "-q", ".gitattributes")
    (repo / "a.py").write_text("x = 3\n")
    _plain(repo, "commit", "-q", "-a", "-m", "unbound")
    return bound


def test_export_base_reads_the_attributes_of_the_tree_it_archives(owner):
    from kriya.workflow import named_test_oracle

    marker = _alias_marker(owner)
    bound = _bound_then_unbound(owner)
    _status(owner)                                    # HEAD, index and work tree activate nothing
    destination = owner.parent / "export"
    destination.mkdir()
    _assert_active_refusal(lambda: named_test_oracle.export_base(named_test_oracle.BaseTree(str(owner), bound),
                                                                 str(destination)), marker)


@pytest.mark.parametrize("site", ["reuse", "remove"])
def test_the_worktree_checkout_reads_its_target_s_tree(owner, caplog, site):
    """The repository's HEAD binds the driver but its index and work tree no longer do (a staged deletion): the main
    context activates nothing; the managed worktree's `checkout -f <HEAD>` materializes the bound tree."""
    import logging

    from kriya.workflow import worktree

    worktree.create_git_worktree(str(owner))
    marker = _alias_marker(owner)
    (owner / ".gitattributes").write_text("*.py filter=media\n")
    (owner / "a.py").write_text("x = 5\n")
    _plain(owner, "add", "-A")
    _plain(owner, "commit", "-q", "-m", "bind")
    _plain(owner, "rm", "-q", ".gitattributes")       # staged deletion: HEAD still binds
    _status(owner)
    if site == "reuse":
        _assert_active_refusal(lambda: worktree.create_git_worktree(str(owner)), marker)
    else:
        with caplog.at_level(logging.ERROR):
            worktree.remove_git_worktree(str(owner), worktree.managed_worktree_path(str(owner)))
        assert not marker.exists()
        assert f"{UNPROVEN}: " in caplog.text and "the filter driver 'media' is ACTIVE" in caplog.text


def test_the_content_hash_reads_head_s_attributes_for_its_scratch_index(owner):
    """`read-tree HEAD` seeds the scratch index; with the work-tree .gitattributes deleted, `add -A` falls back to
    that index (= HEAD) - so HEAD's tree activates the driver even though the real index does not."""
    from kriya.workflow import checkpoint, milestone_completion

    marker = _alias_marker(owner)
    (owner / ".gitattributes").write_text("*.py filter=media\n")
    _plain(owner, "add", ".gitattributes")
    _plain(owner, "commit", "-q", "-m", "bind")
    _plain(owner, "rm", "-q", ".gitattributes")
    _dirty(owner)
    _status(owner)
    _assert_active_refusal(lambda: checkpoint.compute_workspace_content_hash(str(owner)), marker)
    _assert_active_refusal(lambda: milestone_completion.workspace_evidence_hash(str(owner)), marker)


def test_an_untracked_file_the_hash_would_add_activates_the_driver(owner):
    """Only `add -A` converts an untracked file: the untracked listing is part of the path superset."""
    from kriya.workflow import checkpoint

    marker = _alias_marker(owner)
    (owner / ".gitattributes").write_text("new.py filter=media\n")
    (owner / "new.py").write_text("n = 1\n")
    _assert_active_refusal(lambda: checkpoint.compute_workspace_content_hash(str(owner)), marker)


def test_a_path_only_the_archived_tree_has_activates_the_driver(owner):
    """The materialized tree's own paths are part of the superset: `gone.py` exists only in the archived revision."""
    from kriya.workflow import named_test_oracle

    marker = _alias_marker(owner)
    (owner / ".gitattributes").write_text("gone.py filter=media\n")
    (owner / "gone.py").write_text("g = 1\n")
    _plain(owner, "add", "-A")
    _plain(owner, "commit", "-q", "-m", "bound")
    bound = _setup(owner, "rev-parse", "HEAD").strip()
    _plain(owner, "rm", "-q", "gone.py", ".gitattributes")
    _plain(owner, "commit", "-q", "-m", "gone")
    _status(owner)
    destination = owner.parent / "export"
    destination.mkdir()
    _assert_active_refusal(lambda: named_test_oracle.export_base(named_test_oracle.BaseTree(str(owner), bound),
                                                                 str(destination)), marker)


def test_a_submodule_commit_a_materialized_tree_records_is_judged_in_the_submodule(tmp_path):
    """`checkout -f <target>` follows `submodule.recurse` (repository configuration Kriya does not override): it checks
    the submodule out at the commit the TARGET records (MEASURED below, unguarded). So the commits the caller's
    revisions record for a gitlink are that submodule's materialized trees."""
    from kriya.repository.git import refuse_repository_content_filters

    owner = _repository(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    _plain(source, "init", "-q", "-b", "main")
    (source / "n.py").write_text("y = 1\n")
    _plain(source, "add", "-A")
    _plain(source, "commit", "-q", "-m", "c1")
    first = _setup(source, "rev-parse", "HEAD").strip()
    (source / ".gitattributes").write_text("*.py filter=media\n")
    (source / "n.py").write_text("y = 2\n")
    _plain(source, "add", "-A")
    _plain(source, "commit", "-q", "-m", "c2 binds media")
    _plain(owner, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "smod")
    _plain(owner, "commit", "-q", "-m", "submodule at c2")
    nested = owner / "smod"
    _plain(nested, "checkout", "-q", first)            # the submodule's own HEAD, index and work tree: unbound
    marker = _alias_marker(nested)
    refuse_repository_content_filters(str(owner))     # status-like operations: the child activates nothing
    _assert_active_refusal(lambda: refuse_repository_content_filters(str(owner), revisions=("HEAD",)), marker)
    # the vector is real: the recursing checkout of HEAD runs the submodule's (stale, aliased) smudge
    subprocess.run(["git", "-c", "submodule.recurse=true", "checkout", "-q", "-f", "HEAD"], cwd=owner,
                   capture_output=True, stdin=subprocess.DEVNULL, check=False)
    assert marker.exists()


def test_an_untracked_embedded_repository_activating_media_is_refused_in_its_own_context(owner):
    """F3 shape: the parent activates nothing; the embedded repository's OWN .gitattributes binds media, and `add -u`
    on the scratch index would run its git."""
    from kriya.repository.git import refuse_repository_content_filters
    from kriya.workflow import milestone_completion

    nested = owner / "vendored"
    nested.mkdir()
    _setup(nested, "init", "-q", "-b", "main")
    (nested / "lib.py").write_text("v = 1\n")
    (nested / ".gitattributes").write_text("*.py filter=media\n")
    _plain(nested, "add", "-A")
    _plain(nested, "commit", "-q", "-m", "e")
    marker = _alias_marker(nested)
    (nested / "lib.py").write_text("v = 2\n")
    refuse_repository_content_filters(str(owner))     # untracked embedded: the plain inspection does not reach it
    _assert_active_refusal(lambda: milestone_completion.workspace_evidence_hash(str(owner)), marker)


def test_a_submodule_activating_media_is_refused_through_the_parent(tmp_path):
    owner = _repository(tmp_path)
    nested = _embedded(owner, "smod")                 # its own .gitattributes binds *.py to kx
    (nested / ".gitattributes").write_text("*.py filter=media\n")
    _plain(nested, "commit", "-q", "-a", "-m", "media")
    _plain(owner, "add", "smod")
    _plain(owner, "commit", "-q", "-m", "gitlink")
    marker = _alias_marker(nested)
    (nested / "n.py").write_text("y = 2\n")
    _assert_active_refusal(lambda: _status(owner), marker)


def test_mutation_scope_evidence_is_guarded_by_activation(owner):
    from kriya.workflow import requirement_closers

    marker = _alias_marker(owner)
    head = _setup(owner, "rev-parse", "HEAD").strip()

    class Deps:
        run_committed_paths = staticmethod(lambda _workspace: ("run", head, []))
    _dirty(owner)
    tracked, evidence = requirement_closers.mutation_scope_evidence(Deps(), str(owner), str(owner),
                                                                    candidate_paths=["a.py"])
    assert tracked == [".gitignore", "a.py", "skill.md"] and evidence["actual_paths"] == ["a.py"]   # dormant
    _bind(owner, "media")
    _assert_active_refusal(lambda: requirement_closers.mutation_scope_evidence(Deps(), str(owner), str(owner),
                                                                               candidate_paths=["a.py"]), marker)


# --------------------------------------------------------------------------------- repository origin: still refused

def test_a_dormant_repository_defined_filter_is_still_refused(owner):
    """Owner decision 2 (safe default kept): a REPOSITORY-origin definition refuses whether or not any path activates it
    - it is repository-controlled configuration, and a later operation (or a .gitattributes the model writes) can
    activate it. Reproducer: nothing binds `kx` anywhere."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    _setup(owner, "config", "filter.kx.clean", _marker_script(owner.parent / "kx.sh", owner.parent / "KX_RAN"))
    _setup(owner, "check-attr", "filter", "a.py", "skill.md")      # (sanity) unspecified for every path
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == "GIT_CONTENT_FILTER_REPOSITORY_DEFINED" and raised.value.driver == ""


# --------------------------------------------------------------------------------- fail closed

def _answering(monkeypatch, verb: str, answer: Callable[[List[str], bytes], "subprocess.CompletedProcess[bytes]"]):
    from kriya.repository import git

    real = git._run

    def run(cwd, *args, **kwargs):
        if args and args[0] == verb:
            done = real(cwd, *args, **kwargs)
            return answer(list(args), done.stdout)
        return real(cwd, *args, **kwargs)
    monkeypatch.setattr(git, "_run", run)


@pytest.mark.parametrize("answer", [
    lambda args, out: subprocess.CompletedProcess(args, 128, b"", b"fatal: attribute resolution failed"),
    lambda args, out: subprocess.CompletedProcess(args, 0, out.rsplit(b"\0", 4)[0] + b"\0", b""),    # a record short
    lambda args, out: subprocess.CompletedProcess(args, 0, out.replace(b"a.py\0", b"b.py\0", 1), b""),  # wrong echo
    lambda args, out: subprocess.CompletedProcess(args, 0, out[:-1], b""),                            # unterminated
], ids=["exit", "short", "echo", "unterminated"])
def test_a_check_attr_answer_git_did_not_give_cleanly_refuses(owner, monkeypatch, answer):
    """Never read as 'inactive': the dormant stale lines plus an attribute answer that cannot be trusted refuse."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    _dirty(owner)
    _answering(monkeypatch, "check-attr", answer)
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner))
    assert raised.value.reason_code == UNINSPECTABLE


def test_a_materialized_revision_git_cannot_resolve_refuses(owner):
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner), revisions=("no-such-revision",))
    assert raised.value.reason_code == UNINSPECTABLE


def test_without_a_work_tree_to_ask_every_trusted_definition_is_judged(owner):
    """A git dir (no work tree): activation cannot be determined there, so the P1c rule applies to every definition."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner / ".git"))
    assert raised.value.reason_code == UNPROVEN and "cannot be determined here" in str(raised.value)


# --------------------------------------------------------------------------------- cost shape (bounded, nothing for free-of-filters)

def _spawns(monkeypatch) -> List[List[str]]:
    from kriya.repository import git

    real = subprocess.run
    spawned: List[List[str]] = []

    def recording(argv, *args, **kwargs):
        spawned.append([str(a) for a in argv])
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(git.subprocess, "run", recording)
    return spawned


def _verbs(spawned: List[List[str]], verb: str) -> int:
    return sum(1 for argv in spawned if verb in argv)


def test_a_filter_free_configuration_asks_no_activation_question(owner, _isolated_git, monkeypatch):
    from kriya.repository.git import refuse_repository_content_filters

    _isolated_git.write_text("[user]\n\tname = s19q1\n\temail = s19q1@local\n")
    spawned = _spawns(monkeypatch)
    refuse_repository_content_filters(str(owner), revisions=("HEAD",))
    assert _verbs(spawned, "check-attr") == 0 and not any("-o" in argv for argv in spawned)


@pytest.mark.parametrize("revisions, asked", [((), 1), (("HEAD",), 2)])
def test_activation_is_one_check_attr_spawn_per_source(owner, monkeypatch, revisions, asked):
    from kriya.repository.git import refuse_repository_content_filters

    for index in range(40):
        (owner / f"extra{index}.py").write_text("e\n")
    spawned = _spawns(monkeypatch)
    refuse_repository_content_filters(str(owner), revisions=revisions)
    assert _verbs(spawned, "check-attr") == asked
    assert sum(1 for argv in spawned if "ls-files" in argv and "-o" in argv) == 1
