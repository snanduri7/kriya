"""S19 P1b (OI-141, fix-forward of S19 Q1): the residual paths the content-filter refusal left open.

A. `kriya review <folder>` runs `git status` (it can run a repository-defined clean/process filter); it reaches the ONE
   authority (kriya.repository.git) through the APPLICATION use case ProjectService.admit_repository_read - never by
   importing REPOSITORY from the CLI (no new import-direction row, owner decision A).
D. GUI Open Folder (`workspace.describe`, kriya/tools/git_metadata.describe_checkout): its `git status` probe used to
   blank EVERY scope's filter commands (a second, GUI-only policy that also broke operator filters such as Git LFS).
   MEASURED (git 2.54.0, scratch p1b/probes/probe_d_commands.out): every git command that reports work-tree changes
   (status, diff-files, ls-files -m, diff-index against the work tree) can run a clean or process driver, so the facts
   cannot be read filter-free: the probe asks the same authority first - with the probe's OWN git and environment -
   and a refused folder is reported typed (dirty/changed_entries null, ``reason`` = the refusal) without any schema change.
B. Kriya-source/installation trust is ROLE-based: exactly the call sites in INSTALLATION_STATE read Kriya's own checkout
   as operator-managed infrastructure; the same folder opened as a TARGET is refused at every real target entry.
C. An operator (global/system) definition is trusted only when its ULTIMATE file is outside the repository: direct,
   transitive, relative, conditional (includeIf gitdir/onbranch/hasconfig) and symlinked includes all report the
   ultimate file (MEASURED, scratch p1b/probes/probe_c_includes.*.out) and refuse; a file of ANY of the repository's work
   trees is repository content - git's own `rev-parse --git-common-dir` in the file's folder decides (MEASURED gap closed: inspected from a linked worktree - Kriya's managed worktree under <ws>/.kriya is one -
   the MAIN work tree was not a root; inspected from the main work tree, a linked worktree OUTSIDE it - a user's
   `git worktree add ../x`, the operator scan's temporary base worktree - was not a root).

Every pin plants the filter AFTER the fixture repository is built, binds it through .git/info/attributes, makes a
same-size (racily clean) edit, drives the REAL entry point and asserts the typed outcome AND the marker. The helpers and
the isolation (GIT_CONFIG_GLOBAL per test, GIT_CONFIG_NOSYSTEM=1, fixed commit dates) are S19 Q1's.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from tests.test_s19q1_content_filter_refusal import (
    IN_REPOSITORY,
    REFUSED,
    _bind,
    _dirty,
    _marker_script,
    _plant_local,
    _repository,
    _setup,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("s19p1b-git-home")
    config = home / "gitconfig"
    config.write_text("[user]\n\tname = s19p1b\n\temail = s19p1b@local\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-10-20T00:00:00+00:00")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def owner(tmp_path) -> Path:
    return _repository(tmp_path)


def _planted(repo: Path) -> Path:
    marker = repo.parent / "FILTER_RAN"
    _plant_local(repo, marker, "clean", "smudge", "process")
    _bind(repo)
    _dirty(repo)
    return marker


# --------------------------------------------------------------------------------- A: kriya review <folder>

def _review(folder: Path):
    """`kriya review <folder>` through the real CLI; the model is a recorder (nothing leaves the process)."""
    from kriya.cli import main
    from kriya.config import AppConfig
    from kriya.core import LLMClient

    prompts: List[str] = []

    async def complete(_llm, _system, prompt, **_kwargs):
        prompts.append(prompt)
        return "Looks fine."

    with patch("kriya.cli.load_config", return_value=AppConfig()), patch.object(LLMClient, "complete", new=complete):
        result = CliRunner().invoke(main, ["review", str(folder)])
    return result, prompts


def test_kriya_review_of_a_folder_refuses_a_repository_filter_before_any_git_runs_it(owner):
    marker = _planted(owner)
    result, prompts = _review(owner)
    assert not marker.exists(), "kriya review ran the repository's content filter"
    assert result.exit_code == 1, result.output
    assert f"{REFUSED}: " in result.output and "filter.kx." in result.output
    assert prompts == []                                                 # nothing was reviewed


def test_kriya_review_still_reviews_git_s_changed_files_where_no_repository_filter_is_defined(owner):
    """Positive control: the same folder without the plant reviews exactly the file git reports as changed."""
    _dirty(owner)
    result, prompts = _review(owner)
    assert result.exit_code == 0, result.output
    assert len(prompts) == 1 and "x = 2" in prompts[0] and "SKILL" not in prompts[0]   # a.py only (.md is not reviewed)


def test_kriya_review_inspects_immediately_before_its_own_git_status(owner, monkeypatch):
    """The inspection (git config listing) precedes the review's `git status`, with no git in between."""
    _dirty(owner)
    calls: List[List[str]] = []
    real = subprocess.run

    def recording(argv, *args, **kwargs):
        if isinstance(argv, list) and argv and os.path.basename(str(argv[0])) == "git":
            calls.append([str(a) for a in argv])
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", recording)
    result, _ = _review(owner)
    assert result.exit_code == 0, result.output
    verbs = [next(a for a in call[1:] if not a.startswith("-") and "=" not in a) for call in calls]
    status = verbs.index("status")
    inspection = max(i for i, verb in enumerate(verbs[:status]) if verb == "config")
    assert set(verbs[inspection + 1:status]) <= {"rev-parse", "ls-files", "ls-tree"}, verbs   # the inspection's own reads


def test_the_cli_reaches_the_authority_only_through_the_application_use_case(owner, monkeypatch):
    """No REPOSITORY import in the CLI (owner decision A), and the refusal the CLI reports is the one the APPLICATION
    use case raised from the canonical owner."""
    tree = ast.parse((REPO / "kriya" / "cli.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not {name for name in imported if name == "kriya.repository" or name.startswith("kriya.repository.")}

    from kriya.application import project
    from kriya.repository.git import GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED, RepositoryContentFilterRefused

    seen: List[str] = []

    def refuse(path, **_kwargs):
        seen.append(os.fspath(path))
        raise RepositoryContentFilterRefused(GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED, os.fspath(path), key="filter.zz.clean",
                                             scope="global", origin="standard input:")
    monkeypatch.setattr(project, "refuse_repository_content_filters", refuse)
    result, prompts = _review(owner)
    assert seen == [str(owner)] and result.exit_code == 1 and prompts == []
    assert f"{GIT_CONTENT_FILTER_ORIGIN_UNRECOGNISED}: " in result.output


def test_admit_repository_read_is_a_typed_application_refusal_carrying_the_repository_reason(owner):
    from kriya.application import ApplicationRefusal, ProjectService
    from kriya.application.services import WORKSPACE_UNAVAILABLE
    from kriya.context import RequestContext
    from kriya.repository.git import RepositoryContentFilterRefused

    ctx = RequestContext.local(None, request_id="s19p1b")
    ProjectService().admit_repository_read(ctx, str(owner))               # nothing defined: admitted
    marker = _planted(owner)
    with pytest.raises(ApplicationRefusal) as raised:
        ProjectService().admit_repository_read(ctx, str(owner))
    refusal = raised.value
    assert refusal.reason_code == WORKSPACE_UNAVAILABLE and refusal.extra == {"reason": REFUSED}
    assert isinstance(refusal.__cause__, RepositoryContentFilterRefused) and refusal.message == str(refusal.__cause__)
    assert not marker.exists()


# --------------------------------------------------------------------------------- D: GUI Open Folder

def _describe(folder: Path) -> dict:
    from kriya.config import AppConfig
    from kriya.kup.cli_ops import run_kup_operation

    envelope = run_kup_operation(AppConfig(), {"operation": "workspace.describe", "workspace": str(folder)})
    assert envelope["error"] is None, envelope
    return envelope["data"]["checkout"]


def test_open_folder_reports_a_repository_filter_as_a_typed_refusal_and_runs_nothing(owner):
    marker = _planted(owner)
    checkout = _describe(owner)
    assert not marker.exists(), "opening the folder ran the repository's content filter"
    head = _setup(owner, "rev-parse", "HEAD").strip()
    assert {k: checkout[k] for k in ("state", "head", "branch", "detached", "dirty", "changed_entries", "changed_truncated")} \
        == {"state": "checkout_root", "head": head, "branch": "main", "detached": False, "dirty": None, "changed_entries": None,
            "changed_truncated": False}
    assert checkout["reason"].startswith(f"{REFUSED}: ") and "filter.kx." in checkout["reason"]


def _home_global_filter(monkeypatch, home: Path, body: str) -> None:
    """The probe's git reads HOME/.gitconfig (its sanitized environment carries no GIT_CONFIG_* variable)."""
    home.mkdir(exist_ok=True)
    (home / ".gitconfig").write_text("[user]\n\tname = s19p1b\n\temail = s19p1b@local\n" + body)
    monkeypatch.setenv("HOME", str(home))


def test_open_folder_keeps_an_operator_global_filter_working(owner, monkeypatch):
    """Positive control (owner decision D): a global (operator, Git LFS style) filter is NOT blanked any more - the
    probe's status runs it exactly as the user's own git would, and the folder's facts are reported."""
    marker = owner.parent / "FILTER_RAN"
    command = _marker_script(owner.parent / "global.sh", marker)
    _home_global_filter(monkeypatch, owner.parent / "home", f'[filter "kx"]\n\tclean = {command}\n\tsmudge = {command}\n')
    _bind(owner)
    _dirty(owner)
    checkout = _describe(owner)
    assert checkout["reason"] is None and checkout["dirty"] is True and checkout["changed_entries"] == 2
    assert marker.exists(), "the operator's own filter did not run for the probe's status"


def test_open_folder_inspects_the_configuration_its_own_probe_reads(owner, monkeypatch):
    """Discriminator: THIS process's global config (GIT_CONFIG_GLOBAL, the fixture's) is clean, but the probe's sanitized
    environment reads HOME/.gitconfig, which includes a file INSIDE the repository defining the filter. An inspection run
    with the process environment would admit the folder and the probe's status would run the filter."""
    marker = owner.parent / "FILTER_RAN"
    command = _marker_script(owner.parent / "filter.sh", marker)
    (owner / "filters.gitcfg").write_text(f'[filter "kx"]\n\tclean = {command}\n\tsmudge = {command}\n')
    _home_global_filter(monkeypatch, owner.parent / "home", f"[include]\n\tpath = {owner / 'filters.gitcfg'}\n")
    _bind(owner)
    _dirty(owner)
    from kriya.repository.git import refuse_repository_content_filters
    refuse_repository_content_filters(str(owner))                        # the process's own view: nothing to refuse
    checkout = _describe(owner)
    assert not marker.exists(), "the probe ran a filter its inspection never saw"
    assert checkout["dirty"] is None and checkout["reason"].startswith(f"{IN_REPOSITORY}: ")


def test_the_open_folder_probe_keeps_no_filter_policy_of_its_own(owner, monkeypatch):
    """ONE authority (owner decision D): the probe's status carries no filter override of any scope, and the GUI module
    holds no filter-key policy (the former `_filter_overrides` blanking)."""
    from kriya.tools import git_metadata

    argvs: List[List[str]] = []
    real = subprocess.run

    def recording(argv, *args, **kwargs):
        argvs.append([str(a) for a in argv])
        return real(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", recording)
    _dirty(owner)
    assert _describe(owner)["dirty"] is True
    status = [argv for argv in argvs if "status" in argv]
    assert len(status) == 1 and not [a for a in status[0] if a.startswith("filter.")]
    assert not hasattr(git_metadata, "_filter_overrides") and not hasattr(git_metadata, "_FILTER_COMMAND_KEYS")


# --------------------------------------------------------------------------------- C: the ultimate definition origin

def _include_shape(shape: str, root: Path, repo: Path, global_config: Path, monkeypatch) -> None:
    """Plant the filter in ``repo/x.gitcfg`` (repository content) and reach it from the operator configuration."""
    body = (repo.parent / "body.cfg").read_text()
    target = repo / "x.gitcfg"
    target.write_text(body)
    outside = root / "outside"
    outside.mkdir(exist_ok=True)

    def add(text: str) -> None:
        with open(global_config, "a", encoding="utf-8") as handle:
            handle.write(text)
    if shape == "direct":
        add(f"[include]\n\tpath = {target}\n")
    elif shape == "transitive":
        (outside / "a.cfg").write_text(f"[include]\n\tpath = {target}\n")
        add(f"[include]\n\tpath = {outside / 'a.cfg'}\n")
    elif shape == "transitive relative to the including file":
        (outside / "a.cfg").write_text(f"[include]\n\tpath = ../{repo.name}/x.gitcfg\n")
        add(f"[include]\n\tpath = {outside / 'a.cfg'}\n")
    elif shape == "relative to the global file":
        add(f"[include]\n\tpath = {os.path.relpath(target, global_config.parent)}\n")
    elif shape == "includeIf gitdir":
        add(f'[includeIf "gitdir:{repo}/"]\n\tpath = {target}\n')
    elif shape == "includeIf onbranch":
        add(f'[includeIf "onbranch:main"]\n\tpath = {target}\n')
    elif shape == "includeIf hasconfig":
        _setup(repo, "remote", "add", "origin", "https://example.invalid/r.git")
        add(f'[includeIf "hasconfig:remote.*.url:https://example.invalid/**"]\n\tpath = {target}\n')
    elif shape == "includeIf gitdir, relative, from an outside file":
        (outside / "a.cfg").write_text(f'[includeIf "gitdir:{repo}/"]\n\tpath = ../{repo.name}/x.gitcfg\n')
        add(f"[include]\n\tpath = {outside / 'a.cfg'}\n")
    elif shape == "symlinked file":
        (outside / "link.cfg").symlink_to(target)
        add(f"[include]\n\tpath = {outside / 'link.cfg'}\n")
    elif shape == "symlinked directory":
        (repo / "cfg").mkdir()
        (repo / "cfg" / "x.gitcfg").write_text(body)
        (root / "linkdir").symlink_to(repo / "cfg")
        add(f"[include]\n\tpath = {root / 'linkdir' / 'x.gitcfg'}\n")
    elif shape == "system file delegating to the repository":
        system = root / "etc-gitconfig"
        system.write_text(f"[include]\n\tpath = {target}\n")
        monkeypatch.delenv("GIT_CONFIG_NOSYSTEM")
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    elif shape == "the global file itself is in the repository":
        target.write_text("[user]\n\tname = s19p1b\n\temail = s19p1b@local\n" + body)
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(target))
    else:
        raise AssertionError(shape)


_SHAPES = ["direct", "transitive", "transitive relative to the including file", "relative to the global file",
           "includeIf gitdir", "includeIf onbranch", "includeIf hasconfig",
           "includeIf gitdir, relative, from an outside file", "symlinked file", "symlinked directory",
           "system file delegating to the repository", "the global file itself is in the repository"]


@pytest.fixture
def global_config() -> Path:
    return Path(os.environ["GIT_CONFIG_GLOBAL"])


def _filter_body(repo: Path) -> Path:
    marker = repo.parent / "FILTER_RAN"
    command = _marker_script(repo.parent / "filter.sh", marker)
    (repo.parent / "body.cfg").write_text(f'[filter "kx"]\n\tclean = {command}\n\tsmudge = {command}\n'
                                          f"\tprocess = {command}\n")
    return marker


def _status_refused(folder: Path, marker: Path, code: str = IN_REPOSITORY) -> None:
    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow import worktree

    with pytest.raises(RepositoryContentFilterRefused) as raised:
        worktree.git_status_entries(str(folder))
    assert not marker.exists(), "the filter ran"
    assert raised.value.reason_code == code and raised.value.key.startswith("filter.kx.")


@pytest.mark.parametrize("shape", _SHAPES)
def test_an_operator_definition_whose_ultimate_file_is_in_the_repository_is_refused(owner, tmp_path, global_config,
                                                                                     monkeypatch, shape):
    marker = _filter_body(owner)
    _include_shape(shape, tmp_path, owner, global_config, monkeypatch)
    _bind(owner)
    _dirty(owner)
    _status_refused(owner, marker)


def _linked(repo: Path, where: Path) -> Path:
    _setup(repo, "worktree", "add", "-q", "--detach", str(where))
    return where


def test_inspected_from_a_linked_worktree_a_main_work_tree_file_is_repository_content(owner, tmp_path, global_config):
    """MEASURED gap at the base (041ad84): ALLOWED and the filter ran through worktree.git_status_entries. Kriya runs git
    in its managed linked worktree (GitTool, the reuse path); also the managed one, under <ws>/.kriya, is refused."""
    from kriya.workflow import worktree

    marker = _filter_body(owner)
    managed = Path(worktree.create_git_worktree(str(owner)))
    linked = _linked(owner, tmp_path / "linked")
    (owner / "x.gitcfg").write_text((owner.parent / "body.cfg").read_text())
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {owner / 'x.gitcfg'}\n")
    _bind(owner)
    (linked / "a.py").write_text("x = 2\n")
    (managed / "a.py").write_text("x = 2\n")
    _status_refused(linked, marker)
    _status_refused(managed, marker)


def test_inspected_from_the_main_work_tree_a_linked_worktree_file_is_repository_content(owner, tmp_path, global_config):
    """MEASURED gap at the base (041ad84, scratch p1b/probes/probe_c_reverse_and_listing.out): a file in a linked
    worktree OUTSIDE the main work tree (a sibling, like `git worktree add ../x` or the operator scan's temporary base
    worktree) included by the operator's global configuration was admitted for the MAIN work tree, and its filter ran.
    (Kriya's own managed worktree lives under <ws>/.kriya, inside the main work tree: already a root at the base.)"""
    marker = _filter_body(owner)
    sibling = _linked(owner, tmp_path / "sibling")
    assert not str(sibling).startswith(str(owner) + os.sep)
    (sibling / "x.gitcfg").write_text((owner.parent / "body.cfg").read_text())
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {sibling / 'x.gitcfg'}\n")
    _bind(owner)
    _dirty(owner)
    _status_refused(owner, marker)


@pytest.mark.parametrize("shape", ["include of an outside file", "includeIf gitdir of an outside file"])
def test_an_operator_definition_outside_every_work_tree_still_runs(owner, tmp_path, global_config, shape):
    """Positive control: an operator-managed global definition (Git LFS style), also behind includeIf, keeps working -
    with a linked worktree present (the new roots add no false refusal)."""
    from kriya.workflow import worktree

    marker = _filter_body(owner)
    _linked(owner, tmp_path / "linked")
    outside = tmp_path / "operator" / "filters.cfg"
    outside.parent.mkdir()
    outside.write_text((owner.parent / "body.cfg").read_text())
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {outside}\n" if shape.startswith("include of")
                     else f'[includeIf "gitdir:{owner}/"]\n\tpath = {outside}\n')
    _bind(owner)
    _dirty(owner)
    worktree.git_status_entries(str(owner))
    assert marker.exists(), "the operator's filter did not run"


@pytest.mark.parametrize("answer, refused", [((128, b"", b"fatal: not a git repository (or any parent)"), False),
                                             ((128, b"", b"fatal: detected dubious ownership"), True),
                                             ((0, b"", b""), True), ((0, b"/a\n/b\n", b""), True)])
def test_the_same_repository_question_fails_closed(owner, global_config, monkeypatch, answer, refused):
    """An operator FILE definition outside every root is checked by asking git which repository its folder belongs to;
    only git's plain "not a git repository" answer means another place - any other failure or an unparseable answer
    refuses (fail closed). The question is asked only for an operator file definition (none = no extra spawn)."""
    from kriya.repository import git

    outside = owner.parent / "operator" / "filters.cfg"
    outside.parent.mkdir()
    outside.write_text('[filter "lfs"]\n\tclean = git-lfs clean -- %f\n')
    with open(global_config, "a", encoding="utf-8") as handle:
        handle.write(f"[include]\n\tpath = {outside}\n")
    real = git._run
    asked = []

    def run(cwd, *args):
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            asked.append(cwd)
            return subprocess.CompletedProcess(list(args), answer[0], answer[1], answer[2])
        return real(cwd, *args)
    monkeypatch.setattr(git, "_run", run)
    if refused:
        with pytest.raises(git.RepositoryContentFilterRefused) as raised:
            git.refuse_repository_content_filters(str(owner))
        assert raised.value.reason_code == git.GIT_CONFIG_UNINSPECTABLE
    else:
        git.refuse_repository_content_filters(str(owner))
    assert asked == [os.path.realpath(outside.parent)]


def test_the_same_repository_question_is_not_asked_without_an_operator_file_definition(owner, monkeypatch):
    from kriya.repository import git

    real = git._run
    asked = []

    def run(cwd, *args):
        asked.append(args[:1])
        return real(cwd, *args)
    monkeypatch.setattr(git, "_run", run)
    git.refuse_repository_content_filters(str(owner))
    assert ("rev-parse",) in asked and len([a for a in asked if a == ("rev-parse",)]) == 1   # the layout only


# --------------------------------------------------------------------------------- B: installation state is a ROLE

_INSTALLATION = "installation state"
# Every Kriya function that holds a git argv literal and does NOT call the inspection itself, although its git can run
# a content filter (a filter-capable verb) or its argv is built by its callers. Exactly one reason each; closed.
_FILTER_CAPABLE_VERBS = {"status", "diff", "add", "commit", "checkout", "archive", "blame", "rm", "stash", "reset", "merge",
                         "worktree"}
_INSPECTIONS = {"refuse_repository_content_filters", "admit_repository_read"}
UNINSPECTED_GIT = {
    ("kriya/core/release_identity.py", "kriya_revision"): _INSTALLATION,
    ("kriya/repository/git.py", "_run"): "the inspection itself (config listing, rev-parse, ls-files, ls-tree, worktree list)",
    ("kriya/repository/git.py", "run"): "the inspection itself, with the caller's git and environment (_runner)",
    ("kriya/production_doctor.py", "_check_git_worktree"): "filter-free verbs only: rev-parse, worktree list (MEASURED)",
    ("kriya/workflow/worktree.py", "_registered_worktrees"): "filter-free verb only: worktree list (MEASURED)",
    ("kriya/workflow/worktree.py", "_remove_legacy_nested_worktrees"): "called by create_git_worktree after its inspection",
    ("kriya/workflow/worktree.py", "_bootstrap_greenfield_repository"): "called by create_git_worktree after its inspection",
    ("kriya/workflow/worktree.py", "git_read_lines"): "argv from its callers: rev-parse and ls-files only (filter-free)",
    ("kriya/tools/git_metadata.py", "_git"): "argv from the metadata export: object/ref plumbing, no work-tree content",
    ("kriya/tools/git_metadata.py", "_probe"): "argv from describe_checkout, which inspects before its status probe",
    ("kriya/static_analysis/operator_scan.py", "_git"): "argv from run_operator_scan, after changed_paths' inspection",
    ("kriya/workflow/named_test_oracle.py", "_git"): "argv from BaseTree: ls-tree only (export_base inspects)",
}
# Exactly the call sites that read KRIYA'S OWN checkout as operator-managed application infrastructure, and why.
INSTALLATION_STATE = {
    ("kriya/core/release_identity.py", "kriya_revision"):
        "the git revision/dirty flag of Kriya's own source (release identity, certification); its `status` CAN run a "
        "filter (MEASURED) and is not inspected: whoever can write that checkout's configuration can write Kriya's code",
    ("kriya/distribution.py", "tracked_release_files"):
        "the release tool's own source checkout (--source-root); `ls-files` runs no filter (MEASURED)",
}


def _enclosing_function(node: ast.AST, parents: dict) -> Optional[ast.AST]:
    node = parents.get(node)
    while node is not None and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parents.get(node)
    return node


def _git_functions():
    from tests.architecture.test_git_invocation_seam import NON_SPAWN, _callee, _is_git_literal, _python_files

    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for relpath in _python_files():
        tree = ast.parse((REPO / relpath).read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for function in ast.walk(tree):
            if not isinstance(function, functions):
                continue
            literals = [node for node in ast.walk(function)
                        if _is_git_literal(node) and _enclosing_function(node, parents) is function
                        and (relpath, function.name, ast.unparse(node)) not in NON_SPAWN]   # S19: spawns nothing
            if literals:
                inspects = any(isinstance(c, ast.Call) and _callee(c) in _INSPECTIONS for c in ast.walk(function))
                yield relpath, function.name, literals, inspects


def _verb(literal: ast.AST) -> Optional[str]:
    words = [e.value for e in literal.elts[1:] if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    words = [w for w in words if not w.startswith("-") and "=" not in w]
    return words[0] if words else None                                  # None: the argv is built by the callers


def test_the_installation_state_git_sites_are_exactly_these():
    """Owner decision B: the P1 exemption is explicit. Every git function that runs a filter-capable (or caller-built)
    argv without inspecting is in UNINSPECTED_GIT with its reason; the installation-state ones are exactly
    INSTALLATION_STATE. A new git site must be classified here (fail closed)."""
    uninspected, functions = {}, set()
    for relpath, name, literals, inspects in _git_functions():
        functions.add((relpath, name))
        verbs = {_verb(literal) for literal in literals}
        if not inspects and (None in verbs or verbs & _FILTER_CAPABLE_VERBS):
            uninspected[(relpath, name)] = sorted(v or "<callers>" for v in verbs)
    assert (sorted(set(uninspected) - set(UNINSPECTED_GIT)), sorted(set(UNINSPECTED_GIT) - set(uninspected))) == ([], []), uninspected
    assert {site for site, why in UNINSPECTED_GIT.items() if why == _INSTALLATION} <= set(INSTALLATION_STATE)
    assert set(INSTALLATION_STATE) == {("kriya/core/release_identity.py", "kriya_revision"),
                                       ("kriya/distribution.py", "tracked_release_files")}
    assert set(INSTALLATION_STATE) <= functions                          # each is still a git site


def test_an_installation_root_comes_only_from_the_installation():
    """No production code hands a workspace to an installation-state function: every call that passes a source root
    explicitly is one of these (a parameter forwarded unchanged, the doctor's own package root, the release tool's
    --source-root)."""
    names = {"kriya_revision", "release_identity", "release_candidate_identity", "tracked_release_files",
             "check_distribution"}
    positional = {"kriya_revision": 0, "tracked_release_files": 0, "check_distribution": 1}   # the source root's index
    explicit = set()
    for path in (REPO / "kriya").rglob("*.py"):
        relpath = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if not isinstance(call, ast.Call):
                    continue
                callee = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
                if callee not in names:
                    continue
                root = next((k.value for k in call.keywords if k.arg == "source_root"), None)
                index = positional.get(callee)
                if root is None and index is not None and len(call.args) > index:
                    root = call.args[index]
                if root is not None:
                    explicit.add((relpath, function.name, callee, ast.unparse(root)))
    assert explicit == {
        ("kriya/core/release_identity.py", "release_identity", "kriya_revision", "source_root"),      # its own parameter
        ("kriya/core/release_candidate.py", "release_candidate_identity", "release_identity", "source_root"),
        ("kriya/distribution.py", "check_distribution", "tracked_release_files", "source_root"),
        ("kriya/distribution.py", "main", "check_distribution", "args.source_root"),                  # the release tool
    }, explicit


def test_trust_is_never_decided_by_the_installation_path():
    """`if path == kriya_source_root` is forbidden (owner decision B): the source root is named only where the
    installation-state call sites default to it, and the authority knows no installation path at all."""
    users = sorted(str(p.relative_to(REPO)) for p in (REPO / "kriya").rglob("*.py")
                   if "_SOURCE_ROOT" in p.read_text(encoding="utf-8"))
    assert users == ["kriya/core/release_identity.py"]
    authority = (REPO / "kriya" / "repository" / "git.py").read_text(encoding="utf-8")
    assert "__file__" not in authority and "release_identity" not in authority and "SOURCE_ROOT" not in authority


def test_kriya_s_own_checkout_opened_as_the_target_is_refused_like_any_repository(owner, monkeypatch):
    """The same folder in its two roles. As the TARGET (run entry, worktree creation, `kriya review`, GUI Open Folder)
    a repository-local executable filter is refused at every real entry and never runs; as Kriya's INSTALLATION
    (release_identity.kriya_revision) it is read under the operator trust boundary - by role, not by path."""
    from kriya.control.run_coordinator import begin_mutating_run
    from kriya.core import release_identity
    from kriya.repository.git import RepositoryContentFilterRefused
    from kriya.workflow import worktree

    monkeypatch.setattr(release_identity, "_SOURCE_ROOT", str(owner))   # this folder IS the installation's checkout
    marker = _planted(owner)
    for entry in (lambda: begin_mutating_run(str(owner)).__enter__(), lambda: worktree.create_git_worktree(str(owner))):
        with pytest.raises(RepositoryContentFilterRefused) as raised:
            entry()
        assert raised.value.reason_code == REFUSED
    result, prompts = _review(owner)
    assert result.exit_code == 1 and f"{REFUSED}: " in result.output and prompts == []
    assert _describe(owner)["reason"].startswith(f"{REFUSED}: ")
    assert not marker.exists(), "a target entry ran the filter of Kriya's own checkout"
    identity = release_identity.kriya_revision(release_identity._SOURCE_ROOT)
    assert identity["revision"] == _setup(owner, "rev-parse", "HEAD").strip() and identity["dirty"] is True
