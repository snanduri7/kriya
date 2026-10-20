"""S19 P1b (OI-141, fix-forward of S19 Q1): the residual paths the content-filter refusal left open.

A. `kriya review <folder>` runs `git status` (it can run a repository-defined clean/process filter); it reaches the ONE
   authority (kriya.repository.git) through the APPLICATION use case ProjectService.admit_repository_read - never by
   importing REPOSITORY from the CLI (no new import-direction row, owner decision A).

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

from tests.test_s19q1_content_filter_refusal import REFUSED, _bind, _dirty, _plant_local, _repository

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
