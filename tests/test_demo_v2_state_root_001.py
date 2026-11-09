"""DEMO-RESET-REUSES-DOCKER-BIND-ANCESTOR-PATH-001: a demo/walkthrough reset never removes and recreates the active state pathname; it allocates a brand-new, never-before-active state root, publishes it as the single
authoritative active state, and retires the old one best-effort.

Why (MEASURED, ~/kriya-evidence/product-72h-confidence/p1-bind-source-2026-11-09/): once a pathname that is an ANCESTOR of a bind source has been removed and recreated, Docker Desktop's file sharing cannot see any new
path below it (`bind source path does not exist` although the directory exists on the host), persistently and at any ancestor level; 295/295 failures had the source present on the host. The state root holds every gate bind
source (views, dependency caches, the Git-metadata export). The deterministic invariant tested here - no state pathname is ever active twice - is authoritative; the Docker mechanism evidence is the causal proof.

These tests run the REAL scripts (reset_demo.sh, prepare_demo.sh, launch_gui.sh, verify_demo.sh, walkthrough/launch.sh) in a temporary work area: no Docker, no model, no network.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "demo-v2" / "scripts"
WALKTHROUGH = ROOT / "handover" / "PRODUCT-72H" / "walkthrough" / "launch.sh"
ROOT_NAME = re.compile(r"^state-\d{8}T\d{6}Z-[A-Za-z0-9]{6}$")


def _env(work: Path, **extra: str) -> dict:
    # KRIYA_BIN points at nothing: the reset then reports "not found; any earlier approval was NOT revoked" and touches no operator approval; KRIYA_PY is this interpreter
    return {**os.environ, "DEMO_V2_WORK": str(work), "KRIYA_BIN": str(work / "no-such-kriya"), "KRIYA_PY": sys.executable, **extra}


def _run(script: str, work: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPTS / script), *args], capture_output=True, text=True, check=False, timeout=300, env=_env(work, **extra))


def _active(work: Path, scenario: str = "04") -> Path:
    return Path((work / f"scenario-{scenario}" / "state.active").read_text(encoding="utf-8").strip())


def _history(work: Path, scenario: str = "04") -> list:
    return (work / f"scenario-{scenario}" / "state.history").read_text(encoding="utf-8").split()


def _authority(work: Path, scenario: str = "04") -> subprocess.CompletedProcess:
    """What every script gets when it asks for the scenario's state path: `scenario_state` of _common.sh."""
    return subprocess.run(["bash", "-c", f'source "{SCRIPTS}/_common.sh"; scenario_state {scenario}'], capture_output=True, text=True, check=False, env=_env(work))


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    return tmp_path / "work"


# ------------------------------------------------------------------------------------------------------------------- allocation and the single authority

def test_two_resets_give_two_distinct_brand_new_state_roots_and_the_first_is_retired(work):
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    first = _active(work)
    assert ROOT_NAME.match(first.name) and first.parent == work / "scenario-04" and first.is_dir() and stat.S_IMODE(first.stat().st_mode) == 0o700
    assert not (work / "scenario-04" / "state").exists()  # the legacy fixed pathname does not exist and is never created
    (first / "run-leftover").write_text("what a run left behind")
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    second = _active(work)
    assert second != first and second.is_dir() and list(second.iterdir()) == []
    assert not first.exists()  # retired
    assert _history(work) == [str(first), str(second)] and not (work / "scenario-04" / "state").exists()


def test_a_previous_state_root_never_becomes_active_again(work):
    roots = []
    for _ in range(5):
        assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
        roots.append(_active(work))
    assert len(set(roots)) == 5 and _history(work) == [str(r) for r in roots]
    # even a root that STILL EXISTS cannot be re-published: the authority refuses anything but the newest allocation
    old = roots[1]
    old.mkdir()
    (work / "scenario-04" / "state.active").write_text(f"{old}\n", encoding="utf-8")
    refused = _authority(work)
    assert refused.returncode != 0 and "not the newest allocated state root" in refused.stderr
    # and the retired legacy fixed pathname is refused outright
    legacy = work / "scenario-04" / "state"
    legacy.mkdir()
    (work / "scenario-04" / "state.active").write_text(f"{legacy}\n", encoding="utf-8")
    assert _authority(work).returncode != 0 and "retired fixed state pathname" in _authority(work).stderr


def test_a_failed_deletion_of_the_old_state_does_not_cause_reuse_or_block_the_reset(work):
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    old = _active(work)
    stuck = old / "stuck"
    stuck.mkdir()
    (stuck / "file").write_text("x")
    stuck.chmod(0)  # rm -rf cannot descend into it: the old root cannot be deleted now (the Docker Desktop release lag has the same effect)
    try:
        again = _run("reset_demo.sh", work, "--scenario", "04")
        assert again.returncode == 0, again.stderr
        assert "could not be deleted now; it is never reused" in again.stderr
        new = _active(work)
        assert new != old and old.exists()  # the old root is still on disk ...
        assert _authority(work).stdout.strip() == str(new)  # ... but it is not the active state, and nothing resolves it
        assert _history(work)[-1] == str(new)
    finally:
        stuck.chmod(0o700)
    # a LATER reset retires it (the cleanup is retried by the next reset, never by reuse)
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    assert not old.exists()


def test_unrelated_paths_are_untouched_by_a_reset(work):
    assert _run("reset_demo.sh", work, "--scenario", "03").returncode == 0
    other_state = _active(work, "03")
    (other_state / "keep").write_text("another scenario's state")
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    sentinel_dir = work / "scenario-04" / "state-keep-this-notes"
    sentinel_dir.mkdir()
    (sentinel_dir / "x").write_text("not an allocated root: its name merely starts like one")
    sibling_file = work / "scenario-04" / "state.txt"
    sibling_file.write_text("unrelated")
    assert _run("reset_demo.sh", work, "--scenario", "04").returncode == 0
    assert (other_state / "keep").read_text() == "another scenario's state"      # scenario 03's active state is not scenario 04's business
    assert (sentinel_dir / "x").exists() and sibling_file.exists()               # only roots this tooling allocated (the history) and the legacy path are ever retired


def test_a_scenario_that_was_never_prepared_has_no_state_path_and_the_authority_says_so(work):
    work.mkdir()
    refused = _authority(work)
    assert refused.returncode != 0 and "no active state root" in refused.stderr


# -------------------------------------------------------------------------------------------------------------- every consumer resolves the same path

def test_the_rendered_configuration_names_the_active_state_root(work):
    assert _run("prepare_demo.sh", work, "--scenario", "04").returncode == 0
    active = _active(work)
    for placed in (work / "scenario-04" / "config" / "kriya.yaml", work / "scenario-04" / "workspace" / "kriya.yaml"):
        assert yaml.safe_load(placed.read_text(encoding="utf-8"))["paths"]["state"] == str(active)
    assert _authority(work).stdout.strip() == str(active)
    # a second prepare renders the NEW root, never the old one
    assert _run("prepare_demo.sh", work, "--scenario", "04").returncode == 0
    new = _active(work)
    assert new != active and yaml.safe_load((work / "scenario-04" / "config" / "kriya.yaml").read_text(encoding="utf-8"))["paths"]["state"] == str(new)


def test_the_idle_verifier_looks_at_the_active_state_root_not_a_fixed_pathname(work):
    assert _run("prepare_demo.sh", work, "--scenario", "04").returncode == 0
    active = _active(work)
    clean = _run("verify_demo.sh", work, "--scenario", "04", "--phase", "idle")
    assert "PASS  no Kriya state from an earlier run" in clean.stdout, clean.stdout
    (active / "traces.db").write_text("a run was here")
    dirty = _run("verify_demo.sh", work, "--scenario", "04", "--phase", "idle")
    assert f"FAIL  Kriya state from an earlier run is present in {active}" in dirty.stdout, dirty.stdout   # names the active root, and the check really looks there
    # a fixed work/state is invisible to it (it is not the state path): content there must not be what the verifier reports
    (work / "scenario-04" / "state").mkdir()
    (work / "scenario-04" / "state" / "decoy").write_text("x")
    assert f"present in {active}" in _run("verify_demo.sh", work, "--scenario", "04", "--phase", "idle").stdout
    unprepared = work / "other"
    unprepared.mkdir()
    stopped = subprocess.run(["bash", str(SCRIPTS / "verify_demo.sh"), "--scenario", "04", "--phase", "idle"], capture_output=True, text=True, check=False, env=_env(unprepared))
    assert stopped.returncode != 0 and "no active state root" in stopped.stderr   # never a silent pass for an unresolvable state path


@pytest.mark.skipif(sys.platform != "darwin", reason="launch_gui.sh reads the app's mtime with BSD stat (-f %m), as the desktop packaging it guards does")
def test_the_launcher_starts_the_app_with_the_active_state_root(work, tmp_path):
    assert _run("prepare_demo.sh", work, "--scenario", "04").returncode == 0
    active = _active(work)
    app = tmp_path / "Kriya.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    fake = app / "Contents" / "MacOS" / "Kriya"
    seen = tmp_path / "seen-state.txt"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "$KRIYA_STATE_DIR" > "{seen}"\n', encoding="utf-8")
    fake.chmod(0o755)
    kriya = tmp_path / "kriya"
    kriya.write_text("#!/bin/sh\n", encoding="utf-8")
    kriya.chmod(0o755)
    done = subprocess.run(["bash", str(SCRIPTS / "launch_gui.sh"), "--scenario", "04", "--app", str(app)], capture_output=True, text=True, check=False, timeout=60,
                          env={**_env(work), "KRIYA_BIN": str(kriya)})
    assert done.returncode == 0, done.stderr + done.stdout
    for _ in range(100):
        if seen.exists() and seen.read_text():
            break
        subprocess.run(["sleep", "0.1"], check=False)
    assert seen.read_text() == str(active)
    assert f"state: {active}" in done.stdout


def test_no_demo_script_names_a_fixed_scenario_state_pathname():
    """Static: the fixed `<work>/state` of a scenario work area (and the verifier's per-scenario spellings) appear in no consumer; the only place that spells the legacy name is the authority that RETIRES it."""
    offenders = []
    for path in sorted(SCRIPTS.glob("*.sh")) + [WALKTHROUGH]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r'-e "\$HOME_DIR/state"', "", line.split("#", 1)[0])  # the walkthrough's read-only probe that DETECTS a pre-change home (it refuses it) is not a use of the fixed path
            if re.search(r"\$\{?(WORK|W0[0-4]|HOME_DIR)\}?/state(?![-.\w])", code) and path.name != "_common.sh":
                offenders.append(f"{path.name}:{number}: {line.strip()[:120]}")
    assert offenders == [], offenders
    reset = (SCRIPTS / "reset_demo.sh").read_text(encoding="utf-8")
    assert "state_root_allocate" in reset and not re.search(r'rm -rf[^\n#]*state', reset.split("state_root_allocate", 1)[1].split("\n", 3)[2])


# ------------------------------------------------------------------------------------------------------------------------------ the walkthrough kit

@pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "kriya").exists() or not (ROOT / "ui" / "node_modules").is_dir(), reason="the kit needs this checkout's .venv and ui/node_modules")
def test_a_walkthrough_home_from_before_the_change_is_refused_not_silently_wiped_and_not_reused(tmp_path):
    home = tmp_path / "kriya-walkthrough-legacy"
    (home / "state").mkdir(parents=True)
    (home / "state" / "project-memory").write_text("a pre-change home's memory")
    (home / ".kriya-walkthrough").write_text("")
    refused = subprocess.run(["bash", str(WALKTHROUGH), "--prepare-only"], capture_output=True, text=True, check=False, timeout=120, env={**os.environ, "KRIYA_WALKTHROUGH_HOME": str(home)})
    assert refused.returncode != 0 and "run with --reset once" in refused.stderr
    assert (home / "state" / "project-memory").read_text() == "a pre-change home's memory"   # nothing was deleted without --reset, and the fixed state pathname was not used


@pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "kriya").exists() or not (ROOT / "ui" / "node_modules").is_dir(), reason="the kit needs this checkout's .venv and ui/node_modules")
def test_the_walkthrough_reset_keeps_the_home_directory_and_allocates_a_brand_new_state_root(tmp_path):
    home = tmp_path / "kriya-walkthrough-test"
    env = {**os.environ, "KRIYA_WALKTHROUGH_HOME": str(home)}

    def kit(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(WALKTHROUGH), "--prepare-only", *args], capture_output=True, text=True, check=False, timeout=120, env=env)

    assert kit().returncode == 0
    first = Path((home / "state.active").read_text(encoding="utf-8").strip())
    assert ROOT_NAME.match(first.name) and first.parent == home and not (home / "state").exists()
    (first / "memory-from-a-run").write_text("x")
    home_inode = home.stat().st_ino
    assert kit("--reset").returncode == 0
    second = Path((home / "state.active").read_text(encoding="utf-8").strip())
    assert second != first and not first.exists() and list(second.iterdir()) == []
    assert home.stat().st_ino == home_inode                      # the home directory (an ancestor of every bind source) is never removed and recreated
    assert (home / "brownfield-python" / ".git").is_dir()         # the rest of the kit is rebuilt
    assert kit().returncode == 0 and Path((home / "state.active").read_text(encoding="utf-8").strip()) == second   # a plain launch keeps the active root
