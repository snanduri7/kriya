"""PRD-010 logging-location closure: Kriya's log files derive from
Kriya-controlled state (KRIYA_LOG_DIR > logging.directory > ~/.kriya/logs),
never from the process CWD, and every mutating run gets its own run log.

configure_logging() is a no-op while the root logger has handlers, and pytest
installs its own, so every test here clears them first (and restores them) or
it would pass without exercising anything."""
import hashlib
import json
import logging
import os
import subprocess
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.config.config import LoggingConfig, RemovedConfigFieldError
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config, resolve_config_state
from kriya.control.run_coordinator import begin_mutating_run
from kriya.core import logging_setup
from kriya.core.logging_setup import (
    ENV_LOG_DIR,
    LOG_DIR_SOURCE_CONFIG,
    LOG_DIR_SOURCE_DEFAULT,
    LOG_DIR_SOURCE_ENV,
    LogDirectoryError,
    configure_logging,
    resolve_log_directory,
)


@contextmanager
def _fresh_root_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()
    logging_setup.reset_logging_state()
    try:
        yield root
    finally:
        for handler in root.handlers:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        logging_setup.reset_logging_state()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME with no KRIYA_LOG_DIR, so the default applies."""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv(ENV_LOG_DIR, raising=False)
    return user_home


def _git_workspace(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=path, check=True,
    )
    return path


def _tree(path):
    """Every directory and file (including .git) with its content digest."""
    snapshot = {}
    for root, dirs, files in os.walk(path):
        for name in dirs:
            snapshot[os.path.relpath(os.path.join(root, name), path) + "/"] = None
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as handle:
                snapshot[os.path.relpath(full, path)] = hashlib.sha256(handle.read()).hexdigest()
    return snapshot


def _cfg(**logging_fields):
    cfg = AppConfig()
    for key, value in logging_fields.items():
        setattr(cfg.logging, key, value)
    return cfg


# --- resolution: one canonical location, independent of the CWD ---------------------------

def test_default_log_directory_is_under_home_and_identical_across_cwds(home, tmp_path, monkeypatch):
    seen = set()
    for cwd in (tmp_path, home, _git_workspace(tmp_path / "repo"), tmp_path / "repo"):
        monkeypatch.chdir(cwd)
        seen.add(resolve_log_directory(_cfg()))
    assert seen == {(os.path.realpath(str(home / ".kriya" / "logs")), LOG_DIR_SOURCE_DEFAULT)}


def test_configured_absolute_directory_is_used(home, tmp_path):
    target = tmp_path / "configured"
    assert resolve_log_directory(_cfg(directory=str(target))) == (
        os.path.realpath(str(target)), LOG_DIR_SOURCE_CONFIG,
    )


def test_environment_override_wins_over_config_and_default(home, tmp_path, monkeypatch):
    env_dir = tmp_path / "from-env"
    monkeypatch.setenv(ENV_LOG_DIR, str(env_dir))
    assert resolve_log_directory(_cfg(directory=str(tmp_path / "configured"))) == (
        os.path.realpath(str(env_dir)), LOG_DIR_SOURCE_ENV,
    )


@pytest.mark.parametrize("value", ["logs", "./logs", "../logs", ""])
def test_a_relative_or_empty_directory_is_a_typed_error_never_cwd_anchored(home, value):
    with pytest.raises(LogDirectoryError):
        resolve_log_directory(_cfg(directory=value))


def test_a_relative_environment_override_is_a_typed_error(home, monkeypatch):
    monkeypatch.setenv(ENV_LOG_DIR, "relative/logs")
    with pytest.raises(LogDirectoryError):
        resolve_log_directory(_cfg())


def test_load_config_canonicalizes_logging_directory_once(home, tmp_path, monkeypatch):
    """The value SEC-009 digests is exactly the directory that will be opened:
    ~ is expanded and symlinks resolved at load, not later against $HOME/CWD."""
    real = tmp_path / "real-logs"
    real.mkdir()
    link = home / "linked-logs"
    link.symlink_to(real)
    config = tmp_path / "operator.yaml"
    config.write_text(yaml.safe_dump({"logging": {"directory": "~/linked-logs"}}), encoding="utf-8")
    state = resolve_config_state(str(config))
    assert state.config_dict["logging"]["directory"] == os.path.realpath(str(real))


def test_load_config_rejects_a_relative_logging_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"logging": {"directory": "logs"}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    with pytest.raises(LogDirectoryError):
        load_config()
    assert not (workspace / "logs").exists()


def test_a_repository_logging_directory_needs_security_authority(tmp_path, monkeypatch):
    """A repository cannot point Kriya's log writes at an arbitrary directory."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"logging": {"directory": str(outside)}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    with pytest.raises(ConfigAuthorityError, match="logging.directory"):
        load_config()
    assert not outside.exists()


def test_disabling_log_files_and_a_null_directory_are_repository_safe(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"logging": {
        "directory": None, "file_enabled": False, "run_file_enabled": False,
    }}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    cfg = load_config()
    assert (cfg.logging.directory, cfg.logging.file_enabled, cfg.logging.run_file_enabled) == (None, False, False)


def test_packaged_default_names_no_relative_log_file():
    cfg = AppConfig()
    assert "file" not in LoggingConfig.model_fields
    assert cfg.logging.directory is None
    with open(os.path.join(os.path.dirname(logging_setup.__file__), "..", "config", "default_config.yaml"),
              encoding="utf-8") as f:
        packaged = yaml.safe_load(f)["logging"]
    assert "file" not in packaged  # the retired per-file setting
    assert packaged["directory"] is None


# --- application log ------------------------------------------------------------------------

def test_application_log_goes_to_the_canonical_directory_not_the_cwd(home, tmp_path, monkeypatch):
    cwd = tmp_path / "somewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    with _fresh_root_logging():
        configure_logging(_cfg())
        logging.getLogger("kriya.test").warning("application-line")
        for handler in logging.getLogger().handlers:
            handler.flush()
    app_log = home / ".kriya" / "logs" / "kriya.log"
    assert "application-line" in app_log.read_text(encoding="utf-8")
    assert os.listdir(cwd) == []


def test_unwritable_log_directory_is_a_typed_error(home, tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv(ENV_LOG_DIR, str(locked / "logs"))
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        with _fresh_root_logging():
            with pytest.raises(LogDirectoryError):
                configure_logging(_cfg())
    finally:
        locked.chmod(0o700)


def test_unwritable_log_directory_fails_the_cli_cleanly(home, tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv(ENV_LOG_DIR, str(locked / "logs"))
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        with _fresh_root_logging(), patch("kriya.cli.load_config", return_value=_cfg()):
            result = CliRunner().invoke(main, ["plugins"])
        assert result.exit_code == 1
        assert "Error configuring logging" in result.stderr
    finally:
        locked.chmod(0o700)


# --- run logs -------------------------------------------------------------------------------

def _run_once(workspace, message):
    with begin_mutating_run(str(workspace)) as context:
        logging.getLogger("kriya.test").warning(message)
        run_id = context.run_id
    logging.getLogger("kriya.test").warning(f"after-{message}")
    return run_id


def test_each_run_gets_its_own_run_log_keyed_by_run_id(home, tmp_path, monkeypatch):
    first_ws = _git_workspace(tmp_path / "first")
    second_ws = _git_workspace(tmp_path / "second")
    monkeypatch.chdir(tmp_path)
    with _fresh_root_logging():
        configure_logging(_cfg())
        first = _run_once(first_ws, "first-run-line")
        second = _run_once(second_ws, "second-run-line")
    runs = home / ".kriya" / "logs" / "runs"
    assert first != second
    assert sorted(os.listdir(runs)) == sorted([first, second])
    first_log = (runs / first / "kriya.log").read_text(encoding="utf-8")
    second_log = (runs / second / "kriya.log").read_text(encoding="utf-8")
    assert first_log.startswith(f"# Kriya run {first} workspace {os.path.realpath(first_ws)}")
    assert second_log.startswith(f"# Kriya run {second} workspace {os.path.realpath(second_ws)}")
    assert "first-run-line" in first_log and "second-run-line" not in first_log
    assert "second-run-line" in second_log and "first-run-line" not in second_log
    assert "after-first-run-line" not in first_log  # detached when the run ends
    application = (home / ".kriya" / "logs" / "kriya.log").read_text(encoding="utf-8")
    assert "first-run-line" in application and "second-run-line" in application
    assert not (tmp_path / "logs").exists()
    assert not (first_ws / "logs").exists() and not (second_ws / "logs").exists()


def test_run_file_disabled_writes_no_run_log(home, tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    with _fresh_root_logging():
        configure_logging(_cfg(run_file_enabled=False))
        _run_once(workspace, "line")
    assert not (home / ".kriya" / "logs" / "runs").exists()


def test_a_run_without_configured_logging_attaches_nothing(home, tmp_path):
    workspace = _git_workspace(tmp_path / "ws")
    logging_setup.reset_logging_state()
    _run_once(workspace, "line")
    assert not (home / ".kriya").exists()


# --- commands from arbitrary CWDs never create <cwd>/logs ---------------------------------

def test_generate_bootstrap_creates_no_cwd_logs(home, tmp_path, monkeypatch):
    cwd = _git_workspace(tmp_path / "repo")
    monkeypatch.chdir(cwd)
    before = _tree(cwd)
    cfg = _cfg()
    cfg.paths.memory = str(tmp_path / "memory")
    kernel = MagicMock(start=AsyncMock(), stop=AsyncMock())
    with _fresh_root_logging(), \
         patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.cli.Kernel", return_value=kernel), \
         patch("kriya.cli.LLMClient"), \
         patch("kriya.cli.WorkflowEngine"), \
         patch("kriya.cli._dispatch_generation", new=AsyncMock(return_value={"status": "success", "run_id": "r1"})):
        result = CliRunner().invoke(main, ["generate", "a goal", "--json"])
    assert json.loads(result.stdout)["run_id"] == "r1"  # --json stdout contract unaffected
    assert not (cwd / "logs").exists()
    # generate is a mutating run: its own .kriya/ run state is the only addition
    added = {path for path in set(_tree(cwd)) - set(before)}
    assert added and all(path.startswith(".kriya/") for path in added)
    assert (home / ".kriya" / "logs" / "kriya.log").exists()
    [run_record] = [p for p in added if p.startswith(".kriya/control/runs/") and p.endswith(".json")]
    run_id = os.path.basename(run_record)[:-len(".json")]
    run_log = home / ".kriya" / "logs" / "runs" / run_id / "kriya.log"
    assert run_log.read_text(encoding="utf-8").startswith(f"# Kriya run {run_id} workspace {os.path.realpath(cwd)}")


def test_production_doctor_from_arbitrary_cwd_creates_no_cwd_logs(home, tmp_path, monkeypatch):
    from kriya.production_doctor import ProductionDoctorReport

    for cwd in (tmp_path / "a", home):
        cwd.mkdir(exist_ok=True)
        monkeypatch.chdir(cwd)
        before = _tree(cwd)
        report = ProductionDoctorReport(schema_version=1, production_ready=False, checks=())
        with _fresh_root_logging(), \
             patch("kriya.cli.load_config", return_value=_cfg()), \
             patch("kriya.production_doctor.run_production_doctor", return_value=report):
            result = CliRunner().invoke(main, ["doctor", "--production", "--json"])
        assert json.loads(result.stdout)["production_ready"] is False
        assert _tree(cwd) == before
    assert not (home / ".kriya").exists()  # the preflight writes no log file at all


def test_plain_doctor_leaves_the_workspace_byte_tree_unchanged(home, tmp_path, monkeypatch):
    import urllib.error

    workspace = _git_workspace(tmp_path / "repo")
    monkeypatch.chdir(workspace)
    before = _tree(workspace)
    cfg = _cfg()
    cfg.paths.memory = str(tmp_path / "memory")
    with _fresh_root_logging(), \
         patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", side_effect=RuntimeError("offline")):
        CliRunner().invoke(main, ["doctor"])
    assert _tree(workspace) == before
    assert (home / ".kriya" / "logs" / "kriya.log").exists()


def test_runs_status_and_authority_inspect_leave_the_workspace_byte_tree_unchanged(home, tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path / "repo")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    before = _tree(workspace)
    with _fresh_root_logging():
        status = CliRunner().invoke(main, ["runs", "status", "--workspace", str(workspace)])
        inspect = CliRunner().invoke(main, ["authority", "inspect"])
    assert status.exit_code == 0, status.output
    assert inspect.exit_code == 0, inspect.output
    assert _tree(workspace) == before
    assert not (home / ".kriya" / "logs").exists()


# --- the retired per-file setting --------------------------------------------------------

def test_logging_file_is_rejected_with_an_actionable_typed_error(home, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"logging": {"file": "./logs/kriya.log"}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    with pytest.raises(RemovedConfigFieldError,
                       match="logging.file was removed; use logging.directory to configure the log directory"):
        load_config()
    with _fresh_root_logging():
        result = CliRunner().invoke(main, ["plugins"])
    assert result.exit_code == 1
    assert "logging.file was removed" in result.stderr
    assert sorted(os.listdir(workspace)) == ["kriya.yaml"]


def test_programmatic_logging_file_is_rejected_too():
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="logging.file was removed"):
        AppConfig(logging={"file": "/tmp/kriya.log"})
    with pytest.raises(ValueError):
        AppConfig().logging.file = "/tmp/kriya.log"
