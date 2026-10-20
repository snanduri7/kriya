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

Every pin plants the filter AFTER the fixture repository is built, binds it through .git/info/attributes, makes a
same-size (racily clean) edit, drives the REAL entry point and asserts the typed outcome AND the marker. The helpers and
the isolation (GIT_CONFIG_GLOBAL per test, GIT_CONFIG_NOSYSTEM=1, fixed commit dates) are S19 Q1's.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from typing import List
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
