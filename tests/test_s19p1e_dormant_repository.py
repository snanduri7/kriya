"""S19 P1e item 1 (OI-161, owner decision 2026-10-21): a REPOSITORY-origin content-filter definition (scope ``local`` /
``worktree``, and everything those include) refuses only when the actual Git operation can ACTIVATE it.

MEASURED by the independent P1d characterization (scratch p1d_char: results.json SUMMARY, probes a_ops / a_div / a_mech;
git 2.54.0 Apple and Homebrew): for every guarded operation class (status, diff, diff --name-only, add -u / -A / <path>,
commit, blame, checkout -f, archive, worktree add --no-checkout -> reset --hard, scratch-index read-tree + add,
submodule parent status) a dormant repository definition ran NOTHING with the guard switched off and ran when a path
bound it; P1d's activation rule told the two apart in every case and no divergence probe found a counterexample.
Accepted owner invariant: a filter command matters when the actual operation can activate it.

Kept fail closed (each pinned here with REPOSITORY-ONLY fixtures - an inert global configuration, so no operator
definition can carry a pin): no work tree (activation unknown) judges every definition; a check-attr failure or an
inconsistent answer and an unresolvable materialized revision refuse; any scope other than local / worktree refuses
outright (tests/test_s19q1: the ``blob`` record); an active repository definition is never admitted by the P1c
provenance rule. The divergence cases the characterization listed are deterministic pins below: each is refused by
the operation's own inspection or provably runs nothing. A definition that appears only DURING an operation (an
include of a file that operation creates) is the next operation's inspection's to refuse (pinned).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest

from tests.test_s19q1_content_filter_refusal import (
    _NEEDS_WORKTREE,
    _REPORTS_REFUSAL,
    GOLDEN,
    SITES,
    _bind,
    _dirty,
    _golden_repository,
    _marker_script,
    _plant_include,
    _plant_local,
    _reason,
    _repository,
    _setup,
    _sites,
)

REFUSED = "GIT_CONTENT_FILTER_REPOSITORY_DEFINED"
UNINSPECTABLE = "GIT_CONFIG_UNINSPECTABLE"


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("s19p1e-git-home")
    config = home / ".gitconfig"
    config.write_text("[user]\n\tname = s19q1\n\temail = s19q1@local\n")       # inert: no operator filter at all
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")       # the Q1 golden predictor's dates
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_ATTR_SOURCE", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    return config


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


def _guard(path: Path, revisions: Tuple[str, ...] = ()) -> Optional[object]:
    """The refusal (or None) of the one authority for ``path``."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    try:
        refuse_repository_content_filters(str(path), revisions=revisions)
    except RepositoryContentFilterRefused as refusal:
        return refusal
    return None


def _assert_active_repository_refusal(refusal, driver: str = "kx") -> None:
    assert refusal is not None, "an ACTIVE repository-defined filter was admitted"
    message = str(refusal)
    assert refusal.reason_code == REFUSED, message
    assert refusal.driver == driver, message
    assert f"the filter driver '{driver}' is ACTIVE here: git resolves filter={driver} for " in message


def _kriya_git(cwd: Path, *args: str) -> None:
    """The operation itself, spawned exactly as Kriya spawns git (kriya.platform.git_argv)."""
    from kriya.platform import git_argv

    subprocess.run(git_argv(["git", *args]), cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL, check=False)


# --------------------------------------------------------------------------------- the new contract over every site

@pytest.mark.parametrize("site", SITES)
def test_a_dormant_repository_filter_lets_every_guarded_site_run(owner, site, caplog):
    """POSITIVE (the owner invariant): .git/config defines clean, smudge and process commands but no path any operation
    can touch resolves filter=kx - every guarded Kriya entry runs, and the planted command never does."""
    import logging

    from kriya.workflow import worktree

    if site in _NEEDS_WORKTREE:
        worktree.create_git_worktree(str(owner))
        (Path(worktree.managed_worktree_path(str(owner))) / "a.py").write_text("x = 9\n")   # something to reset
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean", "smudge", "process")
    _dirty(owner)
    assert "filter" in (owner / ".git" / "config").read_text()        # the plant is really there
    with caplog.at_level(logging.WARNING):
        try:
            _sites()[site](owner)
        except Exception as error:                    # noqa: BLE001 - e.g. GitTool commit with nothing staged
            assert _reason(error) is None, f"{site}: {error!r}"
    assert "GIT_CONTENT_FILTER" not in caplog.text    # a cleanup site reports a refusal instead of raising it
    if site in _NEEDS_WORKTREE:                       # the reuse / reset checkout really ran (reuse then syncs the
        expected = "x = 2\n" if site == "worktree.create_git_worktree(reuse)" else "x = 1\n"   # owner's dirty a.py)
        assert (Path(worktree.managed_worktree_path(str(owner))) / "a.py").read_text() == expected
    assert not marker.exists(), f"{site} ran the dormant repository filter"


@pytest.mark.parametrize("kind", ["include.path", "includeIf", "nested include", "include from the work tree",
                                  "config.worktree"])
def test_every_repository_scope_shape_is_activation_aware(owner, kind):
    """Dormant -> allowed; the same repository with a bound path -> refused naming the active driver (local and
    worktree scopes, and everything they include, MEASURED reported as those scopes)."""
    marker = owner.parent / "FILTER_RAN"
    _plant_include(owner, marker, kind)
    _dirty(owner)
    assert _guard(owner) is None
    _bind(owner)
    _assert_active_repository_refusal(_guard(owner))
    assert not marker.exists()


def test_a_dormant_repository_filter_moves_no_identity_digest(tmp_path):
    """Byte-identical behaviour: the Q1 golden predictor of the filter-free repository holds with a dormant repository
    definition (nothing was refused, nothing ran)."""
    from kriya.analyzer import analyzer
    from kriya.workflow import checkpoint, milestone_completion

    repo = _golden_repository(tmp_path)
    _setup(repo, "config", "filter.kx.clean", _marker_script(tmp_path / "kx.sh", tmp_path / "KX_RAN"))
    measured = {
        "compute_workspace_content_hash": checkpoint.compute_workspace_content_hash(str(repo)),
        "workspace_evidence_hash": milestone_completion.workspace_evidence_hash(str(repo)),
        "compute_workspace_fingerprint": checkpoint.compute_workspace_fingerprint(str(repo)),
        "compute_tree_hash": checkpoint.compute_tree_hash(str(repo)),
        "_repository_revision": analyzer._repository_revision(str(repo)),
    }
    assert measured == GOLDEN
    assert not (tmp_path / "KX_RAN").exists()


def test_an_active_repository_filter_is_refused_even_when_its_program_is_operator_code(owner):
    """Never judged by provenance: a repository command is refused whatever it runs (/bin/cat would be admitted as an
    operator program by the P1c rule)."""
    _setup(owner, "config", "filter.kx.clean", "/bin/cat")
    assert _guard(owner) is None
    _bind(owner)
    _assert_active_repository_refusal(_guard(owner))


def test_the_refusal_names_the_active_driver_and_its_count(owner):
    _setup(owner, "config", "filter.kx.smudge", "/bin/cat")
    _setup(owner, "config", "filter.other.clean", "/bin/cat")          # dormant beside it: never named
    _bind(owner)
    refusal = _guard(owner)
    _assert_active_repository_refusal(refusal)
    assert "filter=kx for 2 path(s)" in str(refusal) and refusal.key == "filter.kx.smudge"
    assert "filter.other" not in str(refusal)


# --------------------------------------------------------------------------------- fail closed (repository-only fixtures)

def test_without_a_work_tree_every_repository_definition_is_refused(owner):
    """A git dir: activation cannot be decided there, so a repository definition refuses (nothing binds it)."""
    _setup(owner, "config", "filter.kx.clean", "/bin/cat")
    refusal = _guard(owner / ".git")
    assert refusal is not None and refusal.reason_code == REFUSED and refusal.driver == "kx"
    assert "whether the filter driver 'kx' is active cannot be determined here" in str(refusal)


def test_a_repository_definition_reported_where_git_finds_no_repository_refuses(tmp_path, monkeypatch):
    """Fail closed on an inconsistent answer: the listing reports a local definition (faked) while git says the folder
    is not a git repository - no activation can be decided, so the definition refuses (it never takes the 'no
    repository, nothing can activate' shortcut, which is for trusted definitions only)."""
    from kriya.repository import git

    real = git._run

    def run(cwd, *args, **kwargs):
        if args and args[0] == "config":
            return subprocess.CompletedProcess(list(args), 0, b"local\x00file:.git/config\x00filter.kx.clean\nx\x00", b"")
        return real(cwd, *args, **kwargs)
    monkeypatch.setattr(git, "_run", run)
    plain = tmp_path / "plain"
    plain.mkdir()
    refusal = _guard(plain)
    assert refusal is not None and refusal.reason_code == REFUSED and refusal.driver == "kx"
    assert "cannot be determined here" in str(refusal)


def _answering(monkeypatch, verb: str, answer):
    from kriya.repository import git

    real = git._run

    def run(cwd, *args, **kwargs):
        if args and args[0] == verb:
            return answer(list(args), real(cwd, *args, **kwargs).stdout)
        return real(cwd, *args, **kwargs)
    monkeypatch.setattr(git, "_run", run)


@pytest.mark.parametrize("answer", [
    lambda args, out: subprocess.CompletedProcess(args, 128, b"", b"fatal: attribute resolution failed"),
    lambda args, out: subprocess.CompletedProcess(args, 0, out.rsplit(b"\0", 4)[0] + b"\0", b""),     # a record short
    lambda args, out: subprocess.CompletedProcess(args, 0, out.replace(b"a.py\0", b"b.py\0", 1), b""),  # wrong echo
], ids=["exit", "short", "echo"])
def test_a_check_attr_answer_git_did_not_give_cleanly_refuses_a_repository_definition(owner, monkeypatch, answer):
    """Never read as 'dormant': a repository-only definition plus an attribute answer that cannot be trusted."""
    _setup(owner, "config", "filter.kx.clean", "/bin/cat")
    _dirty(owner)
    _answering(monkeypatch, "check-attr", answer)
    refusal = _guard(owner)
    assert refusal is not None and refusal.reason_code == UNINSPECTABLE


def test_git_s_own_attribute_resolution_failure_refuses_a_repository_definition(owner, monkeypatch):
    """Unfaked (git 2.54.0: check-attr exits 128 for a GIT_ATTR_SOURCE naming no tree)."""
    from kriya.repository.git import RepositoryContentFilterRefused, refuse_repository_content_filters

    _setup(owner, "config", "filter.kx.clean", "/bin/cat")
    with pytest.raises(RepositoryContentFilterRefused) as raised:
        refuse_repository_content_filters(str(owner), env={**os.environ, "GIT_ATTR_SOURCE": "no-such-revision"})
    assert raised.value.reason_code == UNINSPECTABLE and "git check-attr failed" in str(raised.value)


def test_a_materialized_revision_git_cannot_resolve_refuses_a_repository_definition(owner):
    _setup(owner, "config", "filter.kx.clean", "/bin/cat")
    refusal = _guard(owner, ("no-such-revision",))
    assert refusal is not None and refusal.reason_code == UNINSPECTABLE


def test_an_unborn_repository_with_a_dormant_definition_is_allowed_and_refused_once_bound(tmp_path):
    """Unborn HEAD keeps P1d's handling (nothing to materialize, not refused); the index still decides activation."""
    repo = tmp_path / "unborn"
    repo.mkdir()
    _setup(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("x = 1\n")
    _setup(repo, "config", "filter.kx.clean", "/bin/cat")
    assert _guard(repo, ("HEAD",)) is None
    _bind(repo)
    _assert_active_repository_refusal(_guard(repo, ("HEAD",)))


# --------------------------------------------------------------------------------- divergence cases (characterization)

def _diverging(tmp_path: Path, target_files: Dict[str, str], main_attrs: str = "*.dat filter=other\n"):
    """The characterization's shape: `main` (a.txt + .gitattributes binding nothing used), branch `target` adding
    ``target_files``; checked out at `main`. Returns (repo, target sha, marker)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _setup(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("hello\n")
    (repo / ".gitattributes").write_text(main_attrs)
    _setup(repo, "add", "-A")
    _setup(repo, "commit", "-q", "-m", "m1")
    _setup(repo, "checkout", "-q", "-b", "target")
    (repo / "a.txt").write_text("hallo\n")
    for relative, text in target_files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text)
    _setup(repo, "add", "-A")
    _setup(repo, "commit", "-q", "-m", "t1")
    target = _setup(repo, "rev-parse", "HEAD").strip()
    _setup(repo, "checkout", "-q", "-f", "main")
    return repo, target, tmp_path / "KX_RAN"


def _define(repo: Path, marker: Path, driver: str = "kx") -> None:
    command = _marker_script(repo.parent / "kx.sh", marker)
    _setup(repo, "config", f"filter.{driver}.clean", command)
    _setup(repo, "config", f"filter.{driver}.smudge", command)


def _refused_or_runs_nothing(repo: Path, revisions: Tuple[str, ...], marker: Path, operation: Tuple[str, ...],
                             expected: str, driver: str = "kx") -> None:
    """The divergence contract: the operation's own inspection refuses it, or the operation provably runs nothing.
    ``expected`` pins which one git 2.54.0 does (MEASURED in the characterization)."""
    refusal = _guard(repo, revisions)
    if expected == "refused":
        _assert_active_repository_refusal(refusal, driver)
    else:
        assert refusal is None, str(refusal)
        _kriya_git(repo, *operation)
    assert not marker.exists()


@pytest.mark.parametrize("spelling", ["relative", "absolute"])
def test_core_attributes_file_the_checkout_itself_creates_runs_nothing_then_refuses(tmp_path, spelling):
    """D1: repository core.attributesFile names a file only the TARGET carries: dormant at inspection, and the checkout
    ran nothing either (MEASURED, characterization D1); once the file exists the NEXT operation's inspection sees the
    binding and refuses."""
    repo, target, marker = _diverging(tmp_path, {"tools/attrs": "*.txt filter=kx\n"})
    _define(repo, marker)
    _setup(repo, "config", "core.attributesFile", "tools/attrs" if spelling == "relative" else str(repo / "tools/attrs"))
    _refused_or_runs_nothing(repo, (target,), marker, ("checkout", "-f", "--no-recurse-submodules", target), "allowed")
    _assert_active_repository_refusal(_guard(repo))


def test_attr_tree_head_is_over_approximated_and_refused(tmp_path):
    """D2: attr.tree=HEAD with the binding only in the target: git's default reading follows attr.tree, the
    materialized target is asked too - refused (the operation itself ran nothing: an over-approximation)."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "*.txt filter=kx\n"})
    _define(repo, marker)
    _setup(repo, "config", "attr.tree", "HEAD")
    _refused_or_runs_nothing(repo, (target,), marker, (), "refused")


@pytest.mark.parametrize("defined, bound, expected", [
    ("KX", "kx", "allowed"),            # git looks a driver up by the attribute's exact string (MEASURED: ran nothing)
    ("kx", "KX", "allowed"),
    ("a.b", "a.b", "refused"),          # a dotted driver name
])
def test_driver_names_are_matched_exactly_as_git_does(tmp_path, defined, bound, expected):
    """D3: case and dots of the driver name."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": f"*.txt filter={bound}\n"})
    _define(repo, marker, defined)
    _refused_or_runs_nothing(repo, (target,), marker, ("checkout", "-f", "--no-recurse-submodules", target), expected,
                             bound)


def test_the_deprecated_dotted_section_spelling_is_the_lower_case_driver(tmp_path):
    """D3: `[filter.KX]` is git's deprecated spelling of subsection `kx` (git lower-cases it: MEASURED, it ran for
    filter=kx); the listing reports filter.kx.*, so the attribute kx activates it - refused."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "*.txt filter=kx\n"})
    command = _marker_script(repo.parent / "kx.sh", marker)
    with open(repo / ".git" / "config", "a", encoding="utf-8") as handle:
        handle.write(f"[filter.KX]\n\tclean = {command}\n\tsmudge = {command}\n")
    _refused_or_runs_nothing(repo, (target,), marker, (), "refused")


def test_a_macro_defined_only_in_the_target_tree_is_refused(tmp_path):
    """D4: `[attr]mk filter=kx` + `*.txt mk` exist only in the materialized tree: --source=<target> resolves it."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "[attr]mk filter=kx\n*.txt mk\n"})
    _define(repo, marker)
    _refused_or_runs_nothing(repo, (target,), marker, (), "refused")


def test_a_gitattributes_arriving_with_the_checked_out_tree_is_refused(tmp_path):
    """C2: the binding is in a directory only the target creates."""
    repo, target, marker = _diverging(tmp_path, {"newdir/n.txt": "created\n", "newdir/.gitattributes": "n.txt filter=kx\n"})
    _define(repo, marker)
    _refused_or_runs_nothing(repo, (target,), marker, (), "refused")


@pytest.mark.parametrize("by", ["sha", "branch"])
def test_include_if_onbranch_is_invisible_to_the_operation_that_switches_branch(tmp_path, by):
    """D6: `includeIf "onbranch:target"` defines kx (bound in the target): at inspection (on main) there is no
    definition, and the checkout reads its configuration before HEAD moves - it runs nothing (MEASURED, by sha and by
    branch name; Kriya checks out a sha). The next operation's inspection (now on the branch) refuses."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "*.txt filter=kx\n"})
    included = tmp_path / "branch.cfg"
    included.write_text(f'[filter "kx"]\n\tsmudge = {_marker_script(tmp_path / "kx.sh", marker)}\n')
    _setup(repo, "config", "includeIf.onbranch:target.path", str(included))
    _refused_or_runs_nothing(repo, (target,), marker,
                             ("checkout", "-f", "--no-recurse-submodules", target if by == "sha" else "target"),
                             "allowed")
    if by == "branch":
        _assert_active_repository_refusal(_guard(repo))


def test_a_repository_include_the_operation_itself_creates_is_the_next_inspection_s_to_refuse(tmp_path):
    """E2: .git/config includes a work-tree file only the target carries (defining kx, which the target binds): the
    checkout does not load it (git reads its configuration once, MEASURED), and the next inspection refuses."""
    command = _marker_script(tmp_path / "kx.sh", tmp_path / "KX_RAN")
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "*.txt filter=kx\n",
                                                 "cfg/repo.cfg": f'[filter "kx"]\n\tsmudge = {command}\n'})
    _setup(repo, "config", "include.path", str(repo / "cfg" / "repo.cfg"))
    assert not (repo / "cfg").exists()
    _refused_or_runs_nothing(repo, (target,), marker, ("checkout", "-f", "--no-recurse-submodules", target), "allowed")
    _assert_active_repository_refusal(_guard(repo))


def _case_insensitive(path: Path) -> bool:
    probe = path / "CaseProbe"
    probe.write_text("x")
    try:
        return (path / "caseprobe").exists()
    finally:
        probe.unlink()


def test_a_case_variant_attributes_file_on_a_case_insensitive_disk_is_refused(tmp_path):
    """D5b: the target carries `.GitAttributes`; on a case-insensitive disk (core.ignoreCase) git resolves it as the
    attributes file (MEASURED: the unguarded checkout ran the smudge, and the activation rule saw kx for the
    materialized target) - the checkout's own inspection refuses."""
    if not _case_insensitive(tmp_path):
        pytest.skip("needs a case-insensitive file system (the D5b shape)")
    repo, target, marker = _diverging(tmp_path, {".GitAttributes": "*.txt filter=kx\n"})
    _define(repo, marker)
    _refused_or_runs_nothing(repo, (target,), marker, (), "refused")


def test_the_vector_is_real_when_bound(tmp_path):
    """Negative-control validity for the shapes above: the same definition, bound, runs on the guard-off checkout."""
    repo, target, marker = _diverging(tmp_path, {".gitattributes": "*.txt filter=kx\n"})
    _define(repo, marker)
    _kriya_git(repo, "checkout", "-f", "--no-recurse-submodules", target)
    assert marker.exists()


def test_the_inspection_of_a_bound_repository_definition_runs_nothing(owner):
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "clean", "smudge", "process")
    _bind(owner)
    _dirty(owner)
    _assert_active_repository_refusal(_guard(owner, ("HEAD",)))
    assert not marker.exists()


def test_cleanup_reports_an_active_repository_definition_and_skips_its_checkout(owner, caplog):
    import logging

    from kriya.workflow import worktree

    worktree.create_git_worktree(str(owner))
    marker = owner.parent / "FILTER_RAN"
    _plant_local(owner, marker, "smudge")
    _bind(owner)
    with caplog.at_level(logging.ERROR):
        worktree.remove_git_worktree(str(owner), worktree.managed_worktree_path(str(owner)))
    assert "worktree.remove_git_worktree" in _REPORTS_REFUSAL
    assert f"{REFUSED}: " in caplog.text and "the filter driver 'kx' is ACTIVE here" in caplog.text
    assert not marker.exists()
