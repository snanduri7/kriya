"""GUI/CLI parity of history-store resolution under the GUI host's child environment policy (08 review F-2; owner's
environment policy 2026-10-04): fixed PATH, fixed PYTHONDONTWRITEBYTECODE=1, the operator's HOME, an optional absolute
KRIYA_STATE_DIR - nothing else. Isolated fixture-only CLI subprocesses (owner permission 2026-10-04): temporary HOME,
state and working directories; no protected store, no model, no network."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from _kup_fixtures import HOST_CHILD_ENV_KEYS, HOST_CHILD_PATH, cli_subprocess_argv, host_child_env


def _capabilities(env, cwd):
    proc = subprocess.run([*cli_subprocess_argv(), "traces", "--json", "--capabilities"], env=env, cwd=cwd,
                          capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    envelope = json.loads(proc.stdout)
    assert envelope["error"] is None, envelope
    return envelope["source"]


def _shell_env(home, state_dir=None):
    """What the operator's own terminal gives `kriya`: the whole ambient environment, with the fixture HOME and
    state directory (tests/conftest.py already points KRIYA_LOG_DIR and the *_HOME roots at temporary directories)."""
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("KRIYA_STATE_DIR", None)
    if state_dir is not None:
        env["KRIYA_STATE_DIR"] = state_dir
    return env


def test_host_child_env_is_exactly_the_policy_and_refuses_relative_state_dirs(tmp_path):
    env = host_child_env(str(tmp_path))
    assert env == {"PATH": HOST_CHILD_PATH, "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(tmp_path)}
    env = host_child_env(str(tmp_path), str(tmp_path / "s"))
    assert set(env) == set(HOST_CHILD_ENV_KEYS) and env["KRIYA_STATE_DIR"] == str(tmp_path / "s")
    for bad in ("relative/state", "", "~/state", ".kriya/state"):
        with pytest.raises(ValueError):
            host_child_env(str(tmp_path), bad)
    with pytest.raises(ValueError):
        host_child_env("home")
    # by construction nothing else can get in: the policy never reads the ambient environment
    assert "PYTHONPATH" not in env and "KRIYA_TRUST_FILE" not in env and "PYTHONHOME" not in env


@pytest.mark.parametrize("case", ["home_only", "operator_state_dir", "config_paths_state"])
def test_gui_and_shell_launches_resolve_the_same_history_store(tmp_path, case):
    """Non-default fixture paths: the GUI child (policy environment) and the operator's shell (ambient environment)
    must name the same state directory and trace database, or the GUI would acquire snapshots of a different store."""
    home = str(tmp_path / "home")
    cwd = str(tmp_path / "cwd")
    os.makedirs(home)
    os.makedirs(cwd)
    state_dir = None
    if case == "operator_state_dir":
        state_dir = str(tmp_path / "custom-state")
        expected_dir = state_dir
    elif case == "config_paths_state":
        # a kriya.yaml in the working directory (Kriya's own discovery rule) naming a workspace-local state directory
        with open(os.path.join(cwd, "kriya.yaml"), "w", encoding="utf-8") as f:
            f.write("paths:\n  state: .kriya/state\n")
        expected_dir = os.path.join(os.path.realpath(cwd), ".kriya", "state")
    else:
        expected_dir = os.path.join(home, ".kriya", "state")
    gui = _capabilities(host_child_env(home, state_dir), cwd)
    shell = _capabilities(_shell_env(home, state_dir), cwd)
    assert gui == shell, (gui, shell)
    assert gui["state_directory"] == expected_dir and gui["trace_database"] == os.path.join(expected_dir, "traces.db")
    assert not os.path.exists(expected_dir), "capabilities must not create the state directory"
