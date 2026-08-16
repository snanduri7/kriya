from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.core.trace import TraceLogger


def _seed_traces(db_path: str, count: int, failure_category: str = None) -> None:
    logger = TraceLogger(db_path)
    for i in range(count):
        logger.log_run(
            run_id=f"run-{i}",
            goal=f"Goal number {i}",
            duration_sec=1.23,
            attempts=1,
            status="success" if i % 2 == 0 else "failure",
            files_modified=["a.py"],
            failure_category=failure_category if i % 2 != 0 else None,
        )


def test_traces_uses_shared_wal_connection_helper(tmp_path):
    """Regression test for a real bug found via code review: kriya/cli.py's traces()
    used a bare sqlite3.connect(db_path) instead of kriya.core.db.get_connection(),
    which is the only thing that gives the connection WAL journal mode + a 30s busy
    timeout. Verified live that kriya.core.db is NOT imported anywhere in the
    import chain leading up to a bare `kriya traces` invocation, so the old code
    got none of that protection - a concurrent `generate` run writing to the same
    traces.db could make `kriya traces` raise 'database is locked' instead of
    just waiting briefly like every other DB access path in this app."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 1)

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.core.db.get_connection", wraps=__import__("kriya.core.db", fromlist=["get_connection"]).get_connection) as mock_get_conn:
        res = runner.invoke(main, ["traces"])

    assert res.exit_code == 0
    assert mock_get_conn.called, (
        "traces() must open its connection via kriya.core.db.get_connection() "
        "(for WAL mode + busy timeout), not a bare sqlite3.connect()"
    )


def test_traces_default_limit_truncates_and_shows_footer(tmp_path):
    """Regression test for a real bug found via code review: the traces() query had
    no LIMIT clause, so a repo with a long history of `generate`/`fix` runs dumps
    every single row - confirmed live against this project's own real traces.db,
    which produced a 485KB, 1000+ row terminal dump for a single `kriya traces`
    call. Default output must be capped with a clear note of how many rows were
    hidden."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 30)

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces"])

    assert res.exit_code == 0
    assert res.output.count("Goal number") == 20
    assert "Showing 20 of 30 recorded runs" in res.output


def test_traces_all_flag_shows_every_row_with_no_footer(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 30)

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces", "--all"])

    assert res.exit_code == 0
    assert res.output.count("Goal number") == 30
    assert "Showing" not in res.output


def test_traces_custom_limit_option(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 30)

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces", "-n", "5"])

    assert res.exit_code == 0
    assert res.output.count("Goal number") == 5
    assert "Showing 5 of 30 recorded runs" in res.output


def test_traces_shows_failure_category_column(tmp_path):
    """failure_category is persisted (kriya/core/trace.py) so an eval harness
    reading traces.db can aggregate by it - confirm `kriya traces` itself
    surfaces it too, not just the raw DB."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 2, failure_category="quality_gates_exhausted")

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces"])

    assert res.exit_code == 0
    assert "CATEGORY" in res.output
    assert "quality_gates_exhausted" in res.output
