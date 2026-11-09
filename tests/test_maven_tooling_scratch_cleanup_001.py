"""MAVEN-TOOLING-SCRATCH-CLEANUP-001: a successful Maven tooling acquisition never becomes a failed step because Kriya's OWN scratch directory could not be deleted afterwards.

MEASURED (qualification at 10e5134, then a Kriya-independent reproducer): on Docker Desktop (macOS) the host cannot delete a directory for 0.5-2.3 s after a container that had
read-only mask mounts under it exited (10/10 immediate removals failed with the mask shape, 0/10 with a plain bind mount). ``_acquire_maven_tooling`` kept its working directory in a
``tempfile.TemporaryDirectory`` whose ``__exit__`` raised that PermissionError out of an acquisition that had succeeded ("Failed to execute: [Errno 13] ...").

The contract these tests pin: the directory is an explicitly owned unique scratch unit (the existing ephemeral-unit primitive: pid marker, under the state root); after the last use its removal is
attempted and can never change the outcome already decided; a removal that fails is logged with the exact owned path; the residue is removed by the existing dead-owner sweep and ONLY then -
never a live owner's unit, never a path that is not a unit, never general temp space. Setup/use failures stay semantic. No sleeps, no retries: nothing here waits for Docker.

Also pinned here (QUALIFY-MUTANTS-MAVEN-HOME-001): the qualification script's read-back mutation self-check runs under ``env -i`` and receives the pinned ``MAVEN_HOME`` through its own line,
the helper refuses to run Maven without a verified pinned authority, and no ambient ``mvn`` on PATH is ever a fallback.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from test_oi142_gate_view import _repo
from test_s3_execution_views import _contained

from kriya.tools import dependency_cache
from kriya.tools.containment import ContainmentSetupError
from kriya.tools.dependency_cache import CACHES_DIRECTORY, EPHEMERAL_MARKER, caches_directory
from kriya.tools.validate import PolymorphicValidator

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "demo-v2" / "scripts" / "qualify_spring_ignite.sh"
HELPER = REPO_ROOT / "demo-v2" / "scripts" / "qualify_spring_ignite.py"
REFERENCE = REPO_ROOT / "demo-v2" / "fixtures" / "spring-ignite-protocol" / "reference"
OK_RESULT = {"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": "", "timeout": False}


def _validator(tmp_path, runner):
    origin = _repo(tmp_path)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    validator._run_cmd_with_timeout = runner  # type: ignore[method-assign]
    return validator


def _mk(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def _acquire(validator, tmp_path):
    return validator._acquire_maven_tooling(["compiler:compile"], str(tmp_path / "cache"), 10)


def _units() -> list:
    base = caches_directory(PolymorphicValidator._state_root())
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


class _Spy:
    """A stand-in for the container run that records the mounted workspace and returns a fixed outcome (or raises)."""

    def __init__(self, result=None, error=None):
        self.result, self.error, self.mounted = result, error, []

    def __call__(self, _argv, **kwargs):
        self.mounted.append(os.path.realpath(kwargs["workspace_path"]))
        assert os.path.isdir(self.mounted[-1])  # the directory exists and is usable while the work runs
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _unit_of(mounted: str) -> str:
    return os.path.dirname(mounted)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not dependency_cache._process_alive(proc.pid)  # noqa: SLF001 - the sweep's own liveness predicate
    return proc.pid


# ------------------------------------------------------------------------------------------------ the outcome is decided before, and survives, the disposal

def test_a_successful_acquisition_stays_successful_when_the_scratch_cannot_be_removed(tmp_path, monkeypatch, caplog):
    """The recorded failure: the work succeeded and the removal raised PermissionError. The step result is the work's; the failed disposal is logged with the exact owned path."""
    spy = _Spy(result=OK_RESULT)
    validator = _validator(tmp_path, spy)

    def denied(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(dependency_cache, "_remove_tree", denied)
    with caplog.at_level(logging.WARNING, logger=dependency_cache.logger.name):
        evidence = _acquire(validator, tmp_path)
    assert evidence["invoked"] is True and evidence["returncode"] == 0 and "reason" not in evidence
    unit = _unit_of(spy.mounted[0])
    assert os.path.isdir(unit)  # the residue is left, not hidden
    assert any(unit in record.getMessage() and "could not be removed" in record.getMessage() for record in caplog.records)
    assert spy.mounted[0] not in validator._owned_trees  # noqa: SLF001 - ownership is released even though the removal failed


def test_a_removal_that_silently_leaves_residue_is_reported_with_its_exact_path(tmp_path, monkeypatch, caplog):
    """``rmtree(ignore_errors=True)`` raises nothing when the daemon still holds the directory; the old code could not even notice. The residue is reported by its path."""
    spy = _Spy(result=OK_RESULT)
    validator = _validator(tmp_path, spy)
    monkeypatch.setattr(dependency_cache, "_remove_tree", lambda path: None)  # what an ignore_errors removal looks like when entries cannot be deleted
    with caplog.at_level(logging.WARNING, logger=dependency_cache.logger.name):
        evidence = _acquire(validator, tmp_path)
    unit = _unit_of(spy.mounted[0])
    assert evidence["returncode"] == 0 and os.path.isdir(unit)
    assert any(unit in record.getMessage() and "could not be fully removed" in record.getMessage() for record in caplog.records)


def test_when_the_removal_works_nothing_is_left_and_nothing_is_reported(tmp_path, caplog):
    """The normal path (also the guard against a vacuous log assertion above)."""
    spy = _Spy(result=OK_RESULT)
    validator = _validator(tmp_path, spy)
    with caplog.at_level(logging.WARNING, logger=dependency_cache.logger.name):
        evidence = _acquire(validator, tmp_path)
    assert evidence["returncode"] == 0 and not os.path.lexists(_unit_of(spy.mounted[0])) and _units() == []
    assert not [record for record in caplog.records if "could not be" in record.getMessage()]


def test_the_work_failing_stays_a_semantic_outcome_with_or_without_a_failing_removal(tmp_path, monkeypatch):
    """Setup/use failures are not softened: a containment setup failure propagates (SEC-006), any other invocation failure is the typed 'failed to invoke' evidence - and a
    failing disposal on top of either never replaces it."""
    monkeypatch.setattr(dependency_cache, "_remove_tree", lambda path: (_ for _ in ()).throw(PermissionError(13, "denied", path)))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    setup = _validator(tmp_path / "a", _Spy(error=ContainmentSetupError("PYTHON_ENVIRONMENT_UNAVAILABLE: setup")))
    with pytest.raises(ContainmentSetupError, match="setup"):
        _acquire(setup, tmp_path)
    broken = _validator(tmp_path / "b", _Spy(error=RuntimeError("daemon unreachable")))
    evidence = _acquire(broken, tmp_path)
    assert evidence["invoked"] is False and "daemon unreachable" in evidence["reason"]


def test_the_scratch_is_a_unique_owned_unit_under_the_state_root_and_a_path_is_never_handed_out_twice(tmp_path, monkeypatch):
    """Explicit ownership (pid marker, inside gate-caches) - not the system temp space - and a path whose removal failed cannot be handed out again."""
    monkeypatch.setattr(dependency_cache, "_remove_tree", lambda path: None)  # every removal leaves its unit behind
    spy = _Spy(result=OK_RESULT)
    validator = _validator(tmp_path, spy)
    for _ in range(3):
        _acquire(validator, tmp_path)
    assert len(set(spy.mounted)) == 3
    for mounted in spy.mounted:
        unit = _unit_of(mounted)
        assert os.path.basename(os.path.dirname(unit)) == CACHES_DIRECTORY and os.path.basename(unit).startswith("cache-")
        assert os.path.isfile(os.path.join(unit, EPHEMERAL_MARKER)) and not mounted.startswith(tempfile_root())


def tempfile_root() -> str:
    import tempfile

    return os.path.realpath(tempfile.gettempdir()) + os.sep + "kriya-maven-tooling-"


# ------------------------------------------------------------------------------------------------------------- the later sweep removes residue, and only residue

def test_a_later_scope_sweeps_the_residue_of_a_dead_owner_and_nothing_else(tmp_path, monkeypatch):
    # residue of the failed disposal (an owned unit left behind) ...
    spy = _Spy(result=OK_RESULT)
    validator = _validator(_mk(tmp_path, "first"), spy)
    with monkeypatch.context() as failing:
        failing.setattr(dependency_cache, "_remove_tree", lambda path: None)
        _acquire(validator, tmp_path)
    residue = _unit_of(spy.mounted[0])
    base = os.path.dirname(residue)
    # ... whose owner is now dead (the unit name carries the owner's pid) ...
    dead = os.path.join(base, f"cache-{_dead_pid()}-residue")
    os.rename(residue, dead)
    # ... next to things the sweep must never touch
    live = os.path.join(base, f"cache-{os.getpid()}-live")                      # a live owner's unit
    os.mkdir(live)
    foreign_dir = os.path.join(base, "not-a-unit")                                # not a unit name
    os.mkdir(foreign_dir)
    outside = tmp_path / "kriya-maven-tooling-outside"                            # system-temp style leftovers are not this sweep's business
    outside.mkdir()
    # the next unit creation (any later scope, here the next acquisition) sweeps
    later = _Spy(result=OK_RESULT)
    _acquire(_validator(_mk(tmp_path, "second"), later), tmp_path)
    assert not os.path.lexists(dead)
    assert os.path.isdir(live) and os.path.isdir(foreign_dir) and outside.is_dir()


def test_the_sweep_does_not_remove_a_unit_whose_owner_is_alive_even_if_it_looks_abandoned(tmp_path):
    base = caches_directory(PolymorphicValidator._state_root())
    os.makedirs(base, exist_ok=True)
    live = os.path.join(base, f"cache-{os.getpid()}-parked")
    os.mkdir(live)
    _acquire(_validator(tmp_path, _Spy(result=OK_RESULT)), tmp_path)
    assert os.path.isdir(live)


# ------------------------------------------------------------------------------ the qualification script: the pinned MAVEN_HOME reaches the mutation self-check

def _mutants_block() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if '"$HELPER" mutants' in line)
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith("; fi"))
    return "\n".join(lines[start:end + 1])


def test_the_mutation_self_check_line_hands_the_pinned_maven_home_to_the_env_i_child(tmp_path):
    """Behaviour of the script's OWN line: run it with a stub interpreter that reports the environment it received. The stub is the only substitution."""
    block = _mutants_block()
    assert block.lstrip().startswith("if env -i ")  # the child runs in an empty environment, so nothing but what the line names reaches it
    stub = tmp_path / "stub_python"
    stub.write_text('#!/bin/sh\nprintf \'{"maven_home": "%s", "path": "%s"}\' "${MAVEN_HOME-UNSET}" "$PATH"\nexit 1\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    pinned = tmp_path / "pinned-maven"
    harness = (
        'pass() { :; }\nfail() { :; }\n'
        f'APP_PATH=/usr/bin:/bin\nMAVEN_HOME={pinned}\nKRIYA_PY={stub}\nHELPER=h.py\nREPO_ROOT=r\nDEMO_V2_DIR=d\nREFERENCE_DIR=f\nEVIDENCE={evidence}\n'
        f'{block}\n'
    )
    subprocess.run(["bash", "-c", harness], check=True, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    received = (evidence / "read-back-mutants.json").read_text(encoding="utf-8")
    assert f'"maven_home": "{pinned}"' in received  # propagated exactly; removing it from the script line makes this UNSET
    assert '"path": "/usr/bin:/bin"' in received    # and nothing ambient came with it: PATH is the app path only


def _helper_env(tmp_path, maven_home=None):
    ambient = tmp_path / "ambient-bin"
    ambient.mkdir(exist_ok=True)
    marker = tmp_path / "ambient-mvn-ran"
    (ambient / "mvn").write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
    (ambient / "mvn").chmod(0o755)
    env = {"PATH": f"{ambient}:/usr/bin:/bin", "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"}
    if maven_home is not None:
        env["MAVEN_HOME"] = str(maven_home)
    return env, marker


def _run_mutants(tmp_path, maven_home=None):
    env, marker = _helper_env(tmp_path, maven_home)
    done = subprocess.run([sys.executable, "-I", str(HELPER), "mutants", "--repo", str(REPO_ROOT), "--fixture", str(REFERENCE)],
                          capture_output=True, text=True, env=env, check=False, timeout=120)
    return done, marker


def test_the_helper_refuses_to_run_the_mutation_self_check_without_the_pinned_authority_and_never_falls_back_to_ambient_maven(tmp_path):
    done, marker = _run_mutants(tmp_path)
    assert done.returncode != 0 and "MAVEN_HOME is not set" in done.stderr
    assert not marker.exists()  # the ambient mvn on PATH was never executed
    unmarked = tmp_path / "some-maven"
    (unmarked / "bin").mkdir(parents=True)
    done, marker = _run_mutants(tmp_path, unmarked)
    assert done.returncode != 0 and "not the authoritative Maven" in done.stderr
    assert not marker.exists()


def test_the_script_has_exactly_one_helper_mutants_call_and_it_is_the_one_under_test():
    assert len(re.findall(r'"\$HELPER" mutants', SCRIPT.read_text(encoding="utf-8"))) == 1


def test_a_failed_use_still_disposes_of_its_scratch_when_removal_works(tmp_path):
    """The disposal runs on every exit path of the use, not only after success (a leak on the failure path would otherwise go unseen)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(ContainmentSetupError):
        _acquire(_validator(tmp_path / "a", _Spy(error=ContainmentSetupError("setup"))), tmp_path)
    assert _units() == []
    evidence = _acquire(_validator(tmp_path / "b", _Spy(error=RuntimeError("boom"))), tmp_path)
    assert evidence["invoked"] is False and _units() == []
