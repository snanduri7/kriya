"""PRD-010 final state/logging model:
- logging.directory (KRIYA_LOG_DIR > logging.directory > ~/.kriya/logs): file logs;
- paths.state (KRIYA_STATE_DIR > paths.state > ~/.kriya/state): traces.db;
- <workspace>/.kriya: locks, RunRecords, checkpoints, recovery data.
paths.logs is removed (a config naming it gets an actionable typed error), and
neither location is ever derived from the process CWD."""
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pydantic
import pytest
import yaml
from click.testing import CliRunner

import kriya
from kriya.cli import _mark_run_in_progress, main
from kriya.config import AppConfig
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import (
    REMOVED_LOGGING_FILE_MESSAGE,
    REMOVED_PATHS_LOGS_MESSAGE,
    PathsConfig,
    RemovedConfigFieldError,
    load_config,
)
from kriya.core.logging_setup import resolve_log_directory
from kriya.core.state_paths import (
    ENV_STATE_DIR,
    STATE_DIR_SOURCE_CONFIG,
    STATE_DIR_SOURCE_DEFAULT,
    STATE_DIR_SOURCE_ENV,
    LegacyTraceMigrationError,
    StateDirectoryError,
    historical_default_trace_db_for,
    kriya_install_dir,
    legacy_trace_db_path,
    migrate_legacy_trace_db,
    resolve_state_directory,
    trace_db_path,
)
from kriya.core.trace import TraceLogger
from kriya.production_doctor import CheckStatus, _check_traces, _Context


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME with no KRIYA_STATE_DIR/KRIYA_LOG_DIR, so defaults apply."""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv(ENV_STATE_DIR, raising=False)
    monkeypatch.delenv("KRIYA_LOG_DIR", raising=False)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    return user_home


def _seed(db_path, run_ids):
    logger = TraceLogger(str(db_path))
    for run_id in run_ids:
        logger.log_run(run_id=run_id, goal=f"goal {run_id}", duration_sec=1.0, attempts=1,
                       status="success", files_modified=[])
    logger.close()


def _run_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return sorted(row[0] for row in conn.execute("SELECT run_id FROM runs"))
    finally:
        conn.close()


def _legacy_db(tmp_path, run_ids=("legacy-1", "legacy-2")):
    legacy_dir = tmp_path / "old-install" / "logs"
    legacy_dir.mkdir(parents=True)
    _seed(legacy_dir / "traces.db", run_ids)
    return legacy_dir / "traces.db"


def _workspace_with_config(tmp_path, monkeypatch, data):
    workspace = tmp_path / "repo"
    workspace.mkdir(exist_ok=True)
    (workspace / "kriya.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.chdir(workspace)
    return workspace


# --- paths.logs is removed ------------------------------------------------------------------

def test_config_schema_no_longer_exposes_paths_logs():
    assert "logs" not in PathsConfig.model_fields
    assert set(PathsConfig.model_fields) == {"skills", "memory", "state"}
    with open(Path(kriya.__file__).parent / "config" / "default_config.yaml", encoding="utf-8") as f:
        assert "logs" not in yaml.safe_load(f)["paths"]


def test_a_config_file_naming_paths_logs_gets_an_actionable_typed_error(home, tmp_path, monkeypatch):
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"logs": "./logs"}})
    with pytest.raises(RemovedConfigFieldError, match=re.escape(REMOVED_PATHS_LOGS_MESSAGE)):
        load_config()
    assert not (workspace / "logs").exists()


def test_the_cli_reports_the_removed_field_instead_of_reinterpreting_it(home, tmp_path, monkeypatch):
    _workspace_with_config(tmp_path, monkeypatch, {"paths": {"logs": "/somewhere/logs"}})
    result = CliRunner().invoke(main, ["plugins"])
    assert result.exit_code == 1
    assert "paths.logs was removed; use logging.directory for logs or paths.state for trace state." in result.stderr


def test_programmatic_paths_logs_is_rejected_too():
    with pytest.raises(pydantic.ValidationError, match="paths.logs was removed"):
        AppConfig(paths={"logs": "/tmp/logs"})
    with pytest.raises(ValueError):
        AppConfig().paths.logs = "/tmp/logs"


# Removed configuration fields and aliases that must never come back in kriya/.
# The only permitted mentions are the removal messages themselves.
_REMOVED_FIELD_PATTERNS = (
    r"paths\.logs\b",
    r"\blogs_path\b",
    r"logging\.file\b(?!_)",
    r"\(\s*[\"']paths[\"']\s*,\s*[\"']logs[\"']\s*\)",
    r"\(\s*[\"']logging[\"']\s*,\s*[\"']file[\"']\s*\)",
    r"paths\[[\"']logs[\"']\]",
)


def test_no_production_code_references_removed_config_fields():
    """Repository guard for paths.logs, logs_path and logging.file."""
    root = Path(kriya.__file__).parent
    allowed = (REMOVED_PATHS_LOGS_MESSAGE[:24], REMOVED_LOGGING_FILE_MESSAGE[:24])
    offenders = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.yaml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(text in line for text in allowed):
                continue
            if any(re.search(pattern, line) for pattern in _REMOVED_FIELD_PATTERNS):
                offenders.append(f"{path.relative_to(root.parent)}:{number}: {line.strip()}")
    assert offenders == []


def test_the_guard_patterns_catch_each_removed_form():
    for sample in ("cfg.paths.logs", "logs_path=x", "cfg.logging.file", '("paths", "logs")',
                   "('logging', 'file')", 'data["paths"]["logs"]'.replace('data["paths"]', 'paths')):
        assert any(re.search(pattern, sample) for pattern in _REMOVED_FIELD_PATTERNS), sample
    assert not any(re.search(pattern, "cfg.logging.file_enabled") for pattern in _REMOVED_FIELD_PATTERNS)


# --- each setting controls exactly one thing, never the CWD ---------------------------------

def test_default_state_location_is_stable_across_cwds(home, tmp_path, monkeypatch):
    seen = set()
    for cwd in (tmp_path, home, _workspace_with_config(tmp_path, monkeypatch, {})):
        monkeypatch.chdir(cwd)
        seen.add((trace_db_path(AppConfig()), resolve_state_directory(AppConfig())[1]))
    assert seen == {(os.path.realpath(str(home / ".kriya" / "state" / "traces.db")), STATE_DIR_SOURCE_DEFAULT)}


def test_logging_directory_controls_only_logs_and_paths_state_only_traces(home, tmp_path):
    base = AppConfig()
    log_default, state_default = resolve_log_directory(base)[0], trace_db_path(base)

    logs_moved = AppConfig()
    logs_moved.logging.directory = str(tmp_path / "custom-logs")
    assert resolve_log_directory(logs_moved)[0] == os.path.realpath(str(tmp_path / "custom-logs"))
    assert trace_db_path(logs_moved) == state_default

    state_moved = AppConfig()
    state_moved.paths.state = str(tmp_path / "custom-state")
    assert trace_db_path(state_moved) == os.path.realpath(str(tmp_path / "custom-state" / "traces.db"))
    assert resolve_log_directory(state_moved)[0] == log_default


def test_environment_overrides_win_independently(home, tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.paths.state = str(tmp_path / "configured")
    assert resolve_state_directory(cfg) == (os.path.realpath(str(tmp_path / "configured")), STATE_DIR_SOURCE_CONFIG)
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "env-logs"))
    assert resolve_state_directory(cfg)[1] == STATE_DIR_SOURCE_CONFIG  # a log override never moves state
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "from-env"))
    assert resolve_state_directory(cfg) == (os.path.realpath(str(tmp_path / "from-env")), STATE_DIR_SOURCE_ENV)


@pytest.mark.parametrize("value", ["state", "./state", ""])
def test_a_relative_state_directory_outside_config_loading_is_a_typed_error(home, value):
    cfg = AppConfig()
    cfg.paths.state = value
    with pytest.raises(StateDirectoryError):
        trace_db_path(cfg)


def test_a_relative_environment_state_directory_is_a_typed_error(home, monkeypatch):
    monkeypatch.setenv(ENV_STATE_DIR, "relative/state")
    with pytest.raises(StateDirectoryError):
        trace_db_path(AppConfig())


def test_trace_writes_land_in_the_state_directory_not_the_cwd(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _mark_run_in_progress(AppConfig(), "run-1", "goal")
    assert _run_ids(home / ".kriya" / "state" / "traces.db") == ["run-1"]
    assert sorted(os.listdir(tmp_path)) == ["home"]


# --- workspace-local state must live beneath <workspace>/.kriya ------------------------------

@pytest.mark.parametrize("value", [".kriya/state", "./.kriya/state", ".kriya/history/traces-dir"])
def test_workspace_local_state_beneath_dot_kriya_is_valid(home, tmp_path, monkeypatch, value):
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": value}})
    cfg = load_config()
    expected = os.path.realpath(os.path.join(str(workspace), value))
    assert trace_db_path(cfg) == os.path.join(expected, "traces.db")
    sub = workspace / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)  # relative to the config file, never the CWD
    assert trace_db_path(load_config(str(workspace / "kriya.yaml"))) == os.path.join(expected, "traces.db")


@pytest.mark.parametrize("value", ["./state", "./logs", "state", ".kriya", "src/.kriya/state"])
def test_arbitrary_workspace_local_state_is_rejected(home, tmp_path, monkeypatch, value):
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": value}})
    with pytest.raises(StateDirectoryError, match=r"\.kriya/state"):
        load_config()
    assert sorted(os.listdir(workspace)) == ["kriya.yaml"]


def test_external_state_needs_approval_and_then_works(home, tmp_path, monkeypatch):
    outside = tmp_path / "external-state"
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": str(outside)}})
    with pytest.raises(ConfigAuthorityError, match="paths.state"):
        load_config()
    approved = CliRunner().invoke(main, ["authority", "approve", "--confirm"])
    assert approved.exit_code == 0, approved.output
    cfg = load_config()
    assert trace_db_path(cfg) == os.path.realpath(str(outside / "traces.db"))
    assert not outside.exists()  # approval and loading create nothing
    assert sorted(os.listdir(workspace)) == ["kriya.yaml"]


@pytest.mark.parametrize("args", [["inspect"], ["approve", "--confirm"], ["revoke"]])
def test_authority_reports_a_bad_state_directory_as_a_clean_error(home, tmp_path, monkeypatch, args):
    # demo-03 (2026-09-26): `kriya authority approve` run with a workspace-local
    # paths.state printed a raw StateDirectoryError traceback.
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": "./state"}})
    result = CliRunner().invoke(main, ["authority", *args])
    output = result.output  # stdout and stderr
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Error: paths.state" in output and ".kriya/state" in output
    assert "Traceback" not in output
    assert not (tmp_path / "authority").exists()  # nothing approved or revoked
    assert sorted(os.listdir(workspace)) == ["kriya.yaml"]


def test_authority_approve_out_inside_the_workspace_is_a_clean_refusal(home, tmp_path, monkeypatch):
    outside = tmp_path / "external-state"
    workspace = _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": str(outside)}})
    result = CliRunner().invoke(main, ["authority", "approve", "--confirm", "--out", "approval.json"])
    output = result.output  # stdout and stderr
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert output.count("Error: ") == 1 and "Traceback" not in output
    assert "paths.state" in output  # the pending field was still listed first
    assert sorted(os.listdir(workspace)) == ["kriya.yaml"]


def test_authority_does_not_hide_a_coding_error(home, tmp_path, monkeypatch):
    _workspace_with_config(tmp_path, monkeypatch, {})
    with patch("kriya.config.config.resolve_config_state", side_effect=TypeError("bug")):
        result = CliRunner().invoke(main, ["authority", "inspect"])
    assert isinstance(result.exception, TypeError)


def test_a_null_state_directory_is_repository_safe(home, tmp_path, monkeypatch):
    _workspace_with_config(tmp_path, monkeypatch, {"paths": {"state": None}})
    assert load_config().paths.state is None


# --- legacy traces.db migration ---------------------------------------------------------------

def test_historical_default_is_the_install_dir_logs_database(tmp_path):
    install_dir = Path(kriya.__file__).resolve().parent.parent
    assert kriya_install_dir() == str(install_dir)
    assert historical_default_trace_db_for(str(tmp_path)) == os.path.realpath(str(tmp_path / "logs" / "traces.db"))


def test_only_the_historical_default_is_auto_detected(home, tmp_path):
    legacy = _legacy_db(tmp_path)
    with patch("kriya.core.state_paths.historical_default_trace_db", return_value=str(tmp_path / "missing.db")):
        assert legacy_trace_db_path(AppConfig()) is None
    with patch("kriya.core.state_paths.historical_default_trace_db", return_value=os.path.realpath(str(legacy))):
        assert legacy_trace_db_path(AppConfig()) == os.path.realpath(str(legacy))


def test_explicit_legacy_path_migration_copies_and_keeps_the_source(home, tmp_path):
    legacy = _legacy_db(tmp_path)
    migration = migrate_legacy_trace_db(AppConfig(), str(legacy))
    assert migration.runs == 2
    assert _run_ids(trace_db_path(AppConfig())) == ["legacy-1", "legacy-2"]
    assert _run_ids(legacy) == ["legacy-1", "legacy-2"]
    assert not os.path.exists(trace_db_path(AppConfig()) + ".migrating")


def test_migration_refuses_when_the_destination_exists_and_never_merges(home, tmp_path):
    legacy = _legacy_db(tmp_path)
    _seed(trace_db_path(AppConfig()), ["current-1"])
    with pytest.raises(LegacyTraceMigrationError, match="refusing to merge"):
        migrate_legacy_trace_db(AppConfig(), str(legacy))
    assert _run_ids(trace_db_path(AppConfig())) == ["current-1"]
    assert _run_ids(legacy) == ["legacy-1", "legacy-2"]


@pytest.mark.parametrize("bad", ["relative/traces.db", "/definitely/not/here/traces.db"])
def test_a_bad_legacy_path_is_refused(home, bad):
    with pytest.raises(LegacyTraceMigrationError):
        migrate_legacy_trace_db(AppConfig(), bad)


def test_migrate_legacy_cli_with_legacy_path(home, tmp_path):
    legacy = _legacy_db(tmp_path)
    with patch("kriya.cli.load_config", return_value=AppConfig()):
        first = CliRunner().invoke(main, ["traces", "--migrate-legacy", "--legacy-path", str(legacy)])
        second = CliRunner().invoke(main, ["traces", "--migrate-legacy", "--legacy-path", str(legacy)])
        listing = CliRunner().invoke(main, ["traces"])
        misuse = CliRunner().invoke(main, ["traces", "--legacy-path", str(legacy)])
    assert first.exit_code == 0 and "Copied 2 run(s)" in first.stdout
    assert second.exit_code == 1 and "refusing to merge" in second.stderr
    assert "goal legacy-1" in listing.stdout
    assert misuse.exit_code == 2
    assert legacy.exists()


def test_read_only_traces_reports_the_historical_db_and_creates_nothing(home, tmp_path, monkeypatch):
    legacy = _legacy_db(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    with patch("kriya.cli.load_config", return_value=AppConfig()), \
         patch("kriya.core.state_paths.historical_default_trace_db", return_value=os.path.realpath(str(legacy))):
        result = CliRunner().invoke(main, ["traces"])
    assert result.exit_code == 0
    assert "No run traces recorded yet." in result.stdout
    assert "--migrate-legacy" in result.stderr
    assert sorted(os.listdir(repo)) == [".git"]
    assert not (home / ".kriya").exists()  # no destination DB or state directory


# --- doctor: trace storage checked separately from logs, read-only --------------------------

def _doctor(cfg, workspace):
    return _check_traces(_Context(cfg=cfg, workspace=str(workspace)))


def test_doctor_checks_state_independently_of_logs_and_creates_nothing(home, tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "logs-here"))
    with patch("kriya.core.state_paths.historical_default_trace_db", return_value=str(tmp_path / "none.db")):
        check = _doctor(AppConfig(), tmp_path)
    assert check.status is CheckStatus.PASS
    assert check.evidence["source"] == STATE_DIR_SOURCE_DEFAULT
    assert check.evidence["trace_db"] == os.path.realpath(str(home / ".kriya" / "state" / "traces.db"))
    assert not (home / ".kriya").exists()


def test_doctor_warns_about_a_historical_trace_db(home, tmp_path):
    legacy = _legacy_db(tmp_path)
    with patch("kriya.core.state_paths.historical_default_trace_db", return_value=os.path.realpath(str(legacy))):
        check = _doctor(AppConfig(), tmp_path)
    assert check.status is CheckStatus.WARN
    assert check.evidence["legacy_trace_db"] == os.path.realpath(str(legacy))
    assert "kriya traces --migrate-legacy" in check.remediation
    assert not (home / ".kriya").exists()


def test_doctor_fails_on_an_invalid_or_unwritable_state_directory(home, tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_STATE_DIR, "relative/state")
    assert _doctor(AppConfig(), tmp_path).status is CheckStatus.FAIL
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv(ENV_STATE_DIR, str(locked / "state"))
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        assert _doctor(AppConfig(), tmp_path).status is CheckStatus.FAIL
    finally:
        locked.chmod(0o700)
