"""traces.db storage follow-up: persistent run history lives in Kriya's state
directory (KRIYA_STATE_DIR > paths.state > ~/.kriya/state), independent of
paths.logs and of the log directory, and never derived from the process CWD.
A legacy <paths.logs>/traces.db is reported and copied only on request."""
import os
import sqlite3
import subprocess
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import _mark_run_in_progress, main
from kriya.config import AppConfig
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config
from kriya.core.state_paths import (
    ENV_STATE_DIR,
    STATE_DIR_SOURCE_CONFIG,
    STATE_DIR_SOURCE_DEFAULT,
    STATE_DIR_SOURCE_ENV,
    LegacyTraceMigrationError,
    StateDirectoryError,
    legacy_trace_db_path,
    migrate_legacy_trace_db,
    resolve_state_directory,
    trace_db_path,
)
from kriya.core.trace import TraceLogger
from kriya.production_doctor import CheckStatus, _check_traces, _Context


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME with no KRIYA_STATE_DIR, so the default applies."""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv(ENV_STATE_DIR, raising=False)
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


def _cfg_with_legacy(tmp_path, run_ids=("legacy-1", "legacy-2")):
    legacy_dir = tmp_path / "old-logs"
    legacy_dir.mkdir()
    _seed(legacy_dir / "traces.db", run_ids)
    cfg = AppConfig()
    cfg.paths.logs = str(legacy_dir)
    return cfg, legacy_dir / "traces.db"


# --- resolution ----------------------------------------------------------------------------

def test_default_trace_db_is_under_home_state_and_stable_across_cwds(home, tmp_path, monkeypatch):
    seen = set()
    for cwd in (tmp_path, home, tmp_path / "home"):
        monkeypatch.chdir(cwd)
        seen.add((trace_db_path(AppConfig()), resolve_state_directory(AppConfig())[1]))
    assert seen == {(os.path.realpath(str(home / ".kriya" / "state" / "traces.db")), STATE_DIR_SOURCE_DEFAULT)}


def test_changing_paths_logs_or_the_log_directory_leaves_the_trace_db_unchanged(home, tmp_path, monkeypatch):
    baseline = trace_db_path(AppConfig())
    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "elsewhere")
    cfg.logging.directory = str(tmp_path / "log-dir")
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "env-logs"))
    assert trace_db_path(cfg) == baseline


def test_explicit_state_directory_and_environment_override(home, tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.paths.state = str(tmp_path / "configured")
    assert resolve_state_directory(cfg) == (os.path.realpath(str(tmp_path / "configured")), STATE_DIR_SOURCE_CONFIG)
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


def test_relative_paths_state_resolves_against_the_config_files_directory_not_the_cwd(home, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    (workspace / "sub").mkdir(parents=True)
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"paths": {"state": "kriya-state"}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    cfg = load_config()
    expected = os.path.realpath(str(workspace / "kriya-state" / "traces.db"))
    assert trace_db_path(cfg) == expected
    monkeypatch.chdir(workspace / "sub")
    assert trace_db_path(load_config(str(workspace / "kriya.yaml"))) == expected


def test_a_repository_state_directory_outside_the_workspace_needs_security_authority(home, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside-state"
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"paths": {"state": str(outside)}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    with pytest.raises(ConfigAuthorityError, match="paths.state"):
        load_config()
    assert not outside.exists()


def test_a_null_state_directory_is_repository_safe(home, tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "kriya.yaml").write_text(yaml.safe_dump({"paths": {"state": None}}), encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    assert load_config().paths.state is None


# --- writes go to the state directory -------------------------------------------------------

def test_trace_writes_land_in_the_state_directory_not_paths_logs_or_the_cwd(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "logs")
    _mark_run_in_progress(cfg, "run-1", "goal")
    assert _run_ids(home / ".kriya" / "state" / "traces.db") == ["run-1"]
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "traces.db").exists()


# --- legacy database: detected, reported, copied only on request -----------------------------

def test_legacy_trace_db_is_detected_only_when_present_and_distinct(home, tmp_path):
    cfg = AppConfig()
    cfg.paths.logs = str(tmp_path / "old-logs")
    assert legacy_trace_db_path(cfg) is None
    cfg, legacy = _cfg_with_legacy(tmp_path)
    assert legacy_trace_db_path(cfg) == os.path.realpath(str(legacy))
    cfg.paths.state = str(legacy.parent)  # the same file is not "legacy"
    assert legacy_trace_db_path(cfg) is None


def test_a_relative_paths_logs_is_never_probed_against_the_cwd(home, tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    _seed(tmp_path / "logs" / "traces.db", ["cwd-run"])
    monkeypatch.chdir(tmp_path)
    cfg = AppConfig()
    cfg.paths.logs = "./logs"
    assert legacy_trace_db_path(cfg) is None


def test_traces_reports_the_legacy_db_and_creates_nothing(home, tmp_path, monkeypatch):
    cfg, legacy = _cfg_with_legacy(tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    with patch("kriya.cli.load_config", return_value=cfg):
        result = CliRunner().invoke(main, ["traces"])
    assert result.exit_code == 0
    assert "No run traces recorded yet." in result.stdout
    assert "older trace database exists" in result.stderr
    assert "--migrate-legacy" in result.stderr
    assert not os.path.exists(trace_db_path(cfg))  # read-only: never creates the DB
    assert os.listdir(cwd) == []
    assert _run_ids(legacy) == ["legacy-1", "legacy-2"]


def test_migrate_copies_the_legacy_history_and_keeps_the_original(home, tmp_path):
    cfg, legacy = _cfg_with_legacy(tmp_path)
    migration = migrate_legacy_trace_db(cfg)
    assert migration.runs == 2
    assert _run_ids(trace_db_path(cfg)) == ["legacy-1", "legacy-2"]
    assert _run_ids(legacy) == ["legacy-1", "legacy-2"]
    assert not os.path.exists(trace_db_path(cfg) + ".migrating")


def test_migrate_never_merges_into_an_existing_trace_db(home, tmp_path):
    cfg, legacy = _cfg_with_legacy(tmp_path)
    _seed(trace_db_path(cfg), ["current-1"])
    with pytest.raises(LegacyTraceMigrationError, match="refusing to merge"):
        migrate_legacy_trace_db(cfg)
    assert _run_ids(trace_db_path(cfg)) == ["current-1"]
    assert _run_ids(legacy) == ["legacy-1", "legacy-2"]


def test_migrate_without_a_legacy_db_is_refused(home, tmp_path):
    with pytest.raises(LegacyTraceMigrationError):
        migrate_legacy_trace_db(AppConfig())


def test_migrate_legacy_cli_is_explicit_and_refuses_a_second_run(home, tmp_path):
    cfg, legacy = _cfg_with_legacy(tmp_path)
    with patch("kriya.cli.load_config", return_value=cfg):
        first = CliRunner().invoke(main, ["traces", "--migrate-legacy"])
        second = CliRunner().invoke(main, ["traces", "--migrate-legacy"])
        listing = CliRunner().invoke(main, ["traces"])
    assert first.exit_code == 0 and "Copied 2 run(s)" in first.stdout
    assert second.exit_code == 1 and "refusing to merge" in second.stderr
    assert "goal legacy-1" in listing.stdout
    assert "older trace database" not in listing.stderr  # history present: no notice
    assert legacy.exists()


# --- doctor: trace storage checked separately from logs ------------------------------------

def _doctor(cfg, workspace):
    return _check_traces(_Context(cfg=cfg, workspace=str(workspace)))


def test_doctor_checks_the_state_directory_independently_of_logs(home, tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "logs-here"))
    check = _doctor(AppConfig(), tmp_path)
    assert check.status is CheckStatus.PASS
    assert check.evidence["source"] == STATE_DIR_SOURCE_DEFAULT
    assert check.evidence["trace_db"] == os.path.realpath(str(home / ".kriya" / "state" / "traces.db"))
    assert check.evidence["exists"] is False
    assert not (home / ".kriya").exists()  # probed, not created


def test_doctor_warns_about_a_legacy_trace_db(home, tmp_path):
    cfg, legacy = _cfg_with_legacy(tmp_path)
    check = _doctor(cfg, tmp_path)
    assert check.status is CheckStatus.WARN
    assert check.evidence["legacy_trace_db"] == os.path.realpath(str(legacy))
    assert "kriya traces --migrate-legacy" in check.remediation


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


def test_read_only_traces_from_a_repository_creates_no_trace_db(home, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    with patch("kriya.cli.load_config", return_value=AppConfig()):
        result = CliRunner().invoke(main, ["traces"])
    assert result.exit_code == 0
    assert sorted(os.listdir(repo)) == [".git"]
    assert not (home / ".kriya" / "state").exists()
