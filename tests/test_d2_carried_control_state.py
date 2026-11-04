"""D2 M3 (owner decision 2026-10-23): a repository that CARRIES Kriya runtime/control state is refused at mutating-run entry.

Original symptom (MEASURED before the fix, closure/d2/m3_symptom_PREFIX.txt): a real ``git clone`` of a repository that tracked
``.kriya/milestones/g1.json`` / ``.kriya/checkpoints/*.json`` / ``.kriya/control/launches/*.json`` entered a mutating run as if the files
were Kriya's own. Every test here uses real git (init, ``add -f``, commit, clone, checkout) - never a mock of the carrier."""
from __future__ import annotations

import json
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



_AMBIENT_SAFE_DIRECTORY = "[safe]\n\tdirectory = *\n"  # what the hosted runner's /etc/gitconfig carries (F14, run 41785193323 diagnostics)


def _hermetic_git_config(monkeypatch, home: Path) -> None:
    """Make git's configuration deterministic for an ownership scenario: no system file, an empty user config, no environment-injected config. Mirrors the canonical
    isolation of tests/test_s19e_honest_refusal.py (_isolated_git). Ambient ``safe.directory=*`` would otherwise make git trust every repository, whatever
    GIT_TEST_ASSUME_DIFFERENT_OWNER says."""
    home.mkdir(parents=True, exist_ok=True)
    config = home / ".gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for name in ("GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):  # GIT_CONFIG_SYSTEM is left alone on purpose: NOSYSTEM must win over it
        monkeypatch.delenv(name, raising=False)


def _foreign_repository(tmp_path: Path, monkeypatch) -> Path:
    repository = tmp_path / "owned_elsewhere"
    repository.mkdir()
    _git(repository, "init", "-q")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")  # git's own test switch: every repository is foreign
    return repository


def _ambient_system_safe_directory(monkeypatch, tmp_path: Path) -> None:
    """Reproduce the hosted runner's /etc/gitconfig (safe.directory=*) through git's own override of the system file (GIT_CONFIG_SYSTEM, honoured unless NOSYSTEM)."""
    system = tmp_path / "ambient-system-gitconfig"
    system.write_text(_AMBIENT_SAFE_DIRECTORY)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)


def test_a_repository_git_refuses_for_its_ownership_is_the_ownership_refusal_not_uninspectable(tmp_path, monkeypatch):
    """OI-163/S19e: before the fix the carried-state inspection answered CARRIED_STATE_UNINSPECTABLE first and the S19e ownership refusal
    (the cause git states) never surfaced. F14: the test owns its git configuration, so an ambient system/global ``safe.directory=*`` cannot defeat it."""
    from kriya.repository.git import GIT_REPOSITORY_OWNERSHIP_REFUSED, RepositoryContentFilterRefused

    _ambient_system_safe_directory(monkeypatch, tmp_path)  # the hosted environment, present on purpose ...
    _hermetic_git_config(monkeypatch, tmp_path / "git-home")  # ... and neutralised by the test's own isolation
    repository = _foreign_repository(tmp_path, monkeypatch)
    try:
        with pytest.raises(RepositoryContentFilterRefused) as caught:
            refuse_repository_carried_control_state(str(repository))
    except pytest.fail.Exception as did_not_raise:  # F14 diagnostics stay: recording only
        pytest.fail(f"{did_not_raise}\nF14-DIAGNOSTICS {json.dumps(_f14_diagnostics(repository), sort_keys=True)}", pytrace=False)
    assert caught.value.reason_code == GIT_REPOSITORY_OWNERSHIP_REFUSED


def test_f14_negative_control_an_ambient_system_safe_directory_defeats_the_unisolated_scenario(tmp_path, monkeypatch):
    """The OLD behaviour (no isolation) with the ambient file present: git trusts the foreign repository, so the product raises nothing. This is exactly the hosted failure;
    it proves the isolation above is what makes the scenario real (the same scenario without the ambient file must raise)."""
    from kriya.repository.git import RepositoryContentFilterRefused

    monkeypatch.setenv("HOME", str(tmp_path / "home-without-config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    repository = _foreign_repository(tmp_path, monkeypatch)
    # sanity: without the ambient file the unisolated scenario is the ownership refusal ...
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    with pytest.raises(RepositoryContentFilterRefused):
        refuse_repository_carried_control_state(str(repository))
    # ... and with it, nothing is raised (the hosted symptom)
    _ambient_system_safe_directory(monkeypatch, tmp_path)
    refuse_repository_carried_control_state(str(repository))


def _f14_diagnostics(repository: Path) -> dict:
    """F14 - UNKNOWN / HOSTED-ONLY, NOT REPRODUCED (run 41758354007: this test did not raise on ubuntu-latest; it passes in a faithful Ubuntu 24.04 / git 2.56.0 / uid 1001
    reproduction). RECORDING ONLY: read-only facts, taken after the failure, that discriminate the candidate causes - git configuration that makes the repository safe, whether
    git itself honours the switch for this directory, what the product's own git call answered, and who owns the directory. Never raises (a broken probe is itself recorded)."""
    def run(*args: str, **kw):
        try:
            done = subprocess.run(["git", *args], cwd=kw.get("cwd"), capture_output=True, text=True, timeout=20, check=False, env=kw.get("env", os.environ))
            return {"rc": done.returncode, "out": done.stdout.strip()[:600], "err": done.stderr.strip()[:300]}
        except Exception as error:  # pylint: disable=broad-except
            return {"probe_error": f"{type(error).__name__}: {error}"}
    info = {"git_version": run("--version"), "uid": os.getuid(), "euid": os.geteuid(), "dir_owner_uid": None, "in_container": os.path.exists("/.dockerenv"),
            "git_env": {k: v for k, v in sorted(os.environ.items()) if k.startswith("GIT_") or k in ("HOME", "XDG_CONFIG_HOME", "LANG", "LC_ALL")}}
    try:
        info["dir_owner_uid"] = os.stat(repository).st_uid
    except OSError as error:
        info["dir_owner_uid"] = f"{type(error).__name__}"
    info["safe_directory_config"] = run("config", "--show-scope", "--show-origin", "--get-all", "safe.directory", cwd=str(repository))
    info["system_and_global_config"] = run("config", "--show-scope", "--show-origin", "--list", cwd="/")
    info["git_rev_parse_in_repository"] = run("rev-parse", "--is-inside-work-tree", cwd=str(repository))
    try:
        info["product_tracked_paths"] = carried_state.tracked_paths(str(repository), carried_state.RUNTIME_CONTROL_PATHSPECS)
    except Exception as error:  # pylint: disable=broad-except
        info["product_tracked_paths_raised"] = f"{type(error).__name__}: {str(error)[:200]}"
    return info


def test_the_f14_diagnostics_are_read_only_and_never_raise(tmp_path):
    """The recording must work on the NORMAL path too (a NameError inside it would otherwise hide behind the failure it is meant to explain)."""
    repository = tmp_path / "plain"
    repository.mkdir()
    _git(repository, "init", "-q")
    before = sorted(path.name for path in repository.iterdir())
    info = _f14_diagnostics(repository)
    assert {"git_version", "uid", "dir_owner_uid", "safe_directory_config", "git_rev_parse_in_repository", "git_env", "system_and_global_config"} <= set(info)
    assert info["git_rev_parse_in_repository"]["rc"] == 0 and info["dir_owner_uid"] == os.stat(repository).st_uid and info["product_tracked_paths"] == []
    assert sorted(path.name for path in repository.iterdir()) == before, "diagnostics changed the repository"
    json.dumps(info)  # serialisable
