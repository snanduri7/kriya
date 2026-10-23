"""D2 M3 (owner decision 2026-10-23): a repository that CARRIES Kriya runtime/control state is refused at mutating-run entry.

Original symptom (MEASURED before the fix, closure/d2/m3_symptom_PREFIX.txt): a real ``git clone`` of a repository that tracked
``.kriya/milestones/g1.json`` / ``.kriya/checkpoints/*.json`` / ``.kriya/control/launches/*.json`` entered a mutating run as if the files
were Kriya's own. Every test here uses real git (init, ``add -f``, commit, clone, checkout) - never a mock of the carrier."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kriya.control.run_coordinator import begin_mutating_run
from kriya.repository import carried_state
from kriya.repository.carried_state import (
    CARRIED_CONTROL_REFUSALS,
    CARRIED_STATE_UNINSPECTABLE,
    REPOSITORY_CARRIES_CONTROL_STATE,
    RepositoryCarriesControlStateRefused,
    refuse_repository_carried_control_state,
)

_IDENTITY = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@a", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@a"}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**os.environ, **_IDENTITY})


def _write(root: Path, relative: str, text: str = "{}") -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _source_repo(tmp_path: Path, *tracked: str) -> Path:
    """A repository that force-adds ``tracked`` files (a repository may carry any file, ignored or not)."""
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _write(source, "README", "x")
    for relative in tracked:
        _write(source, relative)
    _git(source, "add", "-f", "-A")
    _git(source, "commit", "-q", "-m", "carrier")
    return source


def _clone(tmp_path: Path, source: Path) -> Path:
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(source), str(clone))
    return clone


def _enter(workspace: Path) -> None:
    with begin_mutating_run(str(workspace)):
        pass


CARRIED = [
    ".kriya/control/runs/forged.json",
    ".kriya/control/launches/20261023T120000-abcdef12.json",
    ".kriya/control/launches/20261023T120000-abcdef12.review-ra-000000000000000000000000.json",
    ".kriya/control/commits/tx1.json",
    ".kriya/checkpoints/forged.json",
    ".kriya/milestones/g1.json",
    ".kriya/run.lock",
]


@pytest.mark.parametrize("carried", CARRIED)
def test_a_real_clone_that_carries_runtime_state_is_refused_before_anything_is_written(tmp_path, carried):
    clone = _clone(tmp_path, _source_repo(tmp_path, carried))
    assert (clone / carried).exists(), "the clone must really carry the file (the carrier is git itself)"
    before = {path for path in clone.rglob("*") if ".git" not in path.parts}
    with pytest.raises(RepositoryCarriesControlStateRefused) as caught:
        _enter(clone)
    assert caught.value.reason_code == REPOSITORY_CARRIES_CONTROL_STATE
    assert carried in caught.value.paths and carried in str(caught.value)
    # refused before the lock and before any record: nothing Kriya-owned appeared (the lock file is itself a refused name, so compare the sets)
    assert {path for path in clone.rglob("*") if ".git" not in path.parts} == before


def test_a_checkout_of_a_branch_that_carries_state_is_refused(tmp_path):
    source = _source_repo(tmp_path)
    clone = _clone(tmp_path, source)
    _enter(clone)  # clean main: ordinary entry works
    _git(source, "checkout", "-q", "-b", "carrier")
    _write(source, ".kriya/checkpoints/forged.json")
    _git(source, "add", "-f", "-A")
    _git(source, "commit", "-q", "-m", "carries a checkpoint")
    _git(clone, "fetch", "-q", "origin", "carrier")
    _git(clone, "checkout", "-q", "carrier")
    with pytest.raises(RepositoryCarriesControlStateRefused) as caught:
        _enter(clone)
    assert caught.value.reason_code == REPOSITORY_CARRIES_CONTROL_STATE
    assert caught.value.paths == (".kriya/checkpoints/forged.json",)


def test_intentionally_tracked_static_inputs_are_not_refused(tmp_path):
    """Control for the narrow scope: kriya.yaml, a reviewed milestone PLAN and the repository-resident policy manifest are inputs."""
    clone = _clone(tmp_path, _source_repo(
        tmp_path, "kriya.yaml", ".kriya/milestones/g1.plan.json", ".kriya/policy/approved-sources.json", ".kriya/README.md",
    ))
    _enter(clone)


def test_a_plan_file_does_not_hide_a_sidecar_beside_it(tmp_path):
    clone = _clone(tmp_path, _source_repo(tmp_path, ".kriya/milestones/g1.plan.json", ".kriya/milestones/g1.json"))
    with pytest.raises(RepositoryCarriesControlStateRefused) as caught:
        _enter(clone)
    assert caught.value.paths == (".kriya/milestones/g1.json",)


def test_ordinary_untracked_runtime_state_is_not_refused(tmp_path):
    """The normal case: a repository that tracks nothing of Kriya's, whose own earlier runs left (untracked) state, enters again."""
    clone = _clone(tmp_path, _source_repo(tmp_path))
    _enter(clone)
    assert (clone / ".kriya" / "control" / "runs").is_dir(), "the first run really left control state behind"
    _enter(clone)


def test_the_authority_root_is_judged_from_a_subdirectory(tmp_path):
    source = _source_repo(tmp_path, ".kriya/control/runs/forged.json")
    clone = _clone(tmp_path, source)
    sub = clone / "pkg"
    sub.mkdir()
    with pytest.raises(RepositoryCarriesControlStateRefused):
        _enter(sub)


def test_a_directory_that_is_not_a_git_work_tree_carries_nothing(tmp_path):
    folder = tmp_path / "plain"
    _write(folder, ".kriya/control/runs/x.json")
    refuse_repository_carried_control_state(str(folder))  # no .git: no repository, no carrier


@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess(["git"], 128, stdout=b"", stderr=b"fatal: not usable"),
    subprocess.TimeoutExpired(["git"], 60),
    FileNotFoundError("git"),
])
def test_an_uninspectable_repository_fails_closed_with_its_own_reason_code(tmp_path, monkeypatch, failure):
    (tmp_path / ".git").mkdir()

    def fake_run(_cwd, *_args, **_kwargs):
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr("kriya.repository.git._run", fake_run)
    with pytest.raises(RepositoryCarriesControlStateRefused) as caught:
        refuse_repository_carried_control_state(str(tmp_path))
    assert caught.value.reason_code == CARRIED_STATE_UNINSPECTABLE


def test_the_refusal_is_not_absorbed_by_handlers_for_ordinary_failures():
    assert not issubclass(RepositoryCarriesControlStateRefused, (RuntimeError, OSError, ValueError))


def test_the_reason_code_table_is_closed_and_every_message_renders():
    assert set(CARRIED_CONTROL_REFUSALS) == {REPOSITORY_CARRIES_CONTROL_STATE, CARRIED_STATE_UNINSPECTABLE}
    for code in CARRIED_CONTROL_REFUSALS:
        message = str(RepositoryCarriesControlStateRefused(code, "/repo", paths=("a",) * 7, detail="why"))
        assert message.startswith(f"{code}: ") and "{" not in message
    assert "and 2 more" in str(RepositoryCarriesControlStateRefused(REPOSITORY_CARRIES_CONTROL_STATE, "/repo", paths=("a",) * 7))


def test_the_namespaces_are_exactly_the_runtime_control_ones():
    assert carried_state.RUNTIME_CONTROL_PATHSPECS == (".kriya/control", ".kriya/checkpoints", ".kriya/milestones", ".kriya/run.lock")



def test_a_repository_git_refuses_for_its_ownership_is_the_ownership_refusal_not_uninspectable(tmp_path, monkeypatch):
    """OI-163/S19e: before the fix the carried-state inspection answered CARRIED_STATE_UNINSPECTABLE first and the S19e ownership refusal
    (the cause git states) never surfaced."""
    from kriya.repository.git import GIT_REPOSITORY_OWNERSHIP_REFUSED, RepositoryContentFilterRefused

    repository = tmp_path / "owned_elsewhere"
    repository.mkdir()
    _git(repository, "init", "-q")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")  # git's own test switch: every repository is foreign
    with pytest.raises(RepositoryContentFilterRefused) as caught:
        refuse_repository_carried_control_state(str(repository))
    assert caught.value.reason_code == GIT_REPOSITORY_OWNERSHIP_REFUSED
