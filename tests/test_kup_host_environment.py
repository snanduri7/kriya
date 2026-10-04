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


# ------------------------------------------------------------------ explicit configuration directory (08 review F-5)

def _config_dir_with_state(root, name="config"):
    """A configuration directory whose kriya.yaml names a workspace-local state directory."""
    cfg = os.path.join(root, name)
    os.makedirs(cfg)
    with open(os.path.join(cfg, "kriya.yaml"), "w", encoding="utf-8") as f:
        f.write("paths:\n  state: .kriya/state\n")
    return cfg


def _run(args, env, cwd):
    proc = subprocess.run([*cli_subprocess_argv(), *args], env=env, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    envelope = json.loads(proc.stdout)
    assert envelope["error"] is None, envelope
    return envelope


def test_an_explicit_child_cwd_makes_resolution_independent_of_where_the_host_was_launched(tmp_path):
    """The host passes the validated configuration directory as the child's cwd. Launching the HOST from `/`, from HOME
    or from an unrelated temporary directory must not change which kriya.yaml is found or which store is resolved; a
    shell `cd <config dir> && kriya` resolves the same store."""
    home = str(tmp_path / "home")
    os.makedirs(home)
    config_dir = _config_dir_with_state(str(tmp_path))
    expected_dir = os.path.join(os.path.realpath(config_dir), ".kriya", "state")
    unrelated = str(tmp_path / "unrelated")
    os.makedirs(unrelated)
    before = os.getcwd()
    results = {}
    try:
        for launched_from in ("/", home, unrelated):
            os.chdir(launched_from)  # the host process's own working directory: irrelevant by construction
            results[launched_from] = _capabilities(host_child_env(home), config_dir)
    finally:
        os.chdir(before)
    assert len({json.dumps(r, sort_keys=True) for r in results.values()}) == 1, results
    gui = next(iter(results.values()))
    assert gui["state_directory"] == expected_dir and gui["trace_database"] == os.path.join(expected_dir, "traces.db")
    assert _capabilities(_shell_env(home), config_dir) == gui  # the operator's shell, started in the config directory
    # the SAME host launched with a different configuration directory resolves a different store: the directory, not
    # the launch location, is the input
    other = _config_dir_with_state(str(tmp_path), "other-config")
    assert _capabilities(host_child_env(home), other)["state_directory"] == os.path.join(os.path.realpath(other), ".kriya", "state")


def test_configuration_directory_is_distinct_from_the_recovery_workspace(tmp_path):
    """The recovery workspace travels only as an explicit argument. Acquisition with --workspace W from configuration
    directory C writes the snapshot under C's store and nothing under W; `runs status --workspace W` assesses W and
    loads no configuration; swapping C changes the store but not the assessed workspace."""
    from _kup_fixtures import seed_store

    home = str(tmp_path / "home")
    os.makedirs(home)
    config_a = _config_dir_with_state(str(tmp_path), "config-a")
    config_b = _config_dir_with_state(str(tmp_path), "config-b")
    workspace = str(tmp_path / "workspace")
    os.makedirs(workspace)
    for cfg in (config_a, config_b):
        seed_store(os.path.join(cfg, ".kriya", "state", "traces.db"), 3)
    env = host_child_env(home)
    before_ws = sorted(os.listdir(workspace))

    acquired_a = _run(["traces", "--json", "--snapshot", "--workspace", workspace], env, config_a)
    acquired_b = _run(["traces", "--json", "--snapshot", "--workspace", workspace], env, config_b)
    for acquired, cfg in ((acquired_a, config_a), (acquired_b, config_b)):
        state_dir = os.path.join(os.path.realpath(cfg), ".kriya", "state")
        assert acquired["source"]["trace_database"] == os.path.join(state_dir, "traces.db")
        assert acquired["source"]["snapshot_directory"].startswith(os.path.join(state_dir, "kup-snapshots") + os.sep)
        assert os.path.isdir(acquired["source"]["snapshot_directory"])
    assert acquired_a["data"]["snapshot_id"] != acquired_b["data"]["snapshot_id"]
    assert sorted(os.listdir(workspace)) == before_ws, "the recovery workspace must not receive any file"
    assert not os.path.exists(os.path.join(workspace, ".kriya")) and not os.path.exists(os.path.join(home, ".kriya", "state"))

    status_a = _run(["runs", "status", "--workspace", workspace, "--json", "--kup-version", "1"], env, config_a)
    status_b = _run(["runs", "status", "--workspace", workspace, "--json", "--kup-version", "1"], env, config_b)
    assert status_a["data"]["workspace"] == status_b["data"]["workspace"] == os.path.realpath(workspace)
    assert status_a["source"] is None and status_b["source"] is None  # the assessment loads no configuration at all
    assert status_a["data"]["run_active"] is False
