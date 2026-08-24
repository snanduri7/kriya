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


def test_traces_shows_kind_column_from_failure_report(tmp_path):
    """MA7.6's categorize_failure()/build_failure_report_entry() were dead
    code (zero real callers anywhere) until wired into workflow.py's real
    failure path - this confirms the KIND column (dominant_category over
    the persisted failure_report JSON) actually renders, additive to the
    pre-existing CATEGORY column which test_traces_shows_failure_category_column
    above already locks in and which this must NOT disturb."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    logger = TraceLogger(db_path)
    logger.log_run(
        run_id="run-kind-1",
        goal="A goal that keeps hitting edit-targeting failures",
        duration_sec=1.0,
        attempts=3,
        status="failure",
        files_modified=["a.py"],
        failure_category="quality_gates_exhausted",
        failure_report=[
            {"failure_type": "no_op_edit", "category": "edit_targeting", "attribution_tier": "locator"},
            {"failure_type": "no_op_edit", "category": "edit_targeting", "attribution_tier": "locator"},
            {"failure_type": "compile", "category": "build", "attribution_tier": None},
        ],
    )

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces"])

    assert res.exit_code == 0
    assert "KIND" in res.output
    assert "edit_targeting" in res.output
    assert "quality_gates_exhausted" in res.output


def test_traces_kind_column_blank_for_rows_predating_failure_report(tmp_path):
    """A row from before this field existed (or a clean success, which
    never populates failure_report) must render a blank KIND, not crash."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    db_path = str(logs_dir / "traces.db")
    _seed_traces(db_path, 1)

    cfg = AppConfig()
    cfg.paths.logs = str(logs_dir)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["traces"])

    assert res.exit_code == 0


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
