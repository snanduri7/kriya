"""Suite-wide isolation, inherited by CLI subprocesses through the environment:
- every Kriya log (application and per-run) goes to one session temp dir,
  never the real ~/.kriya/logs;
- every test gets its own fresh state directory (traces.db), never the real
  ~/.kriya/state: tests read trace rows back, so one shared database would leak
  rows between tests. Locate it with kriya.core.state_paths.trace_db_path();
- the historical pre-move trace database location points at a path that does
  not exist, so a developer's real <install>/logs/traces.db never changes a
  test's outcome (tests that need one patch it explicitly).
Tests of the precedence rules unset KRIYA_LOG_DIR / KRIYA_STATE_DIR themselves."""
import os

import pytest

from kriya.core.logging_setup import ENV_LOG_DIR
from kriya.core.state_paths import ENV_STATE_DIR


@pytest.fixture(scope="session", autouse=True)
def _isolated_kriya_log_dir(tmp_path_factory):
    previous = os.environ.get(ENV_LOG_DIR)
    os.environ[ENV_LOG_DIR] = str(tmp_path_factory.mktemp("kriya-logs"))
    yield
    if previous is None:
        os.environ.pop(ENV_LOG_DIR, None)
    else:
        os.environ[ENV_LOG_DIR] = previous


@pytest.fixture(autouse=True)
def _isolated_kriya_state_dir(tmp_path_factory, monkeypatch):
    from kriya.core import state_paths

    state_dir = tmp_path_factory.mktemp("kriya-state")
    monkeypatch.setenv(ENV_STATE_DIR, str(state_dir))
    monkeypatch.setattr(state_paths, "historical_default_trace_db",
                        lambda: str(state_dir / "no-historical-install" / "logs" / "traces.db"))
