"""Suite-wide isolation: every Kriya log (application and per-run) written
while the suite runs, including by CLI subprocesses, which inherit the
environment, goes to a temporary directory, never the real ~/.kriya/logs.
Tests of the precedence rules unset KRIYA_LOG_DIR themselves."""
import os

import pytest

from kriya.core.logging_setup import ENV_LOG_DIR


@pytest.fixture(scope="session", autouse=True)
def _isolated_kriya_log_dir(tmp_path_factory):
    previous = os.environ.get(ENV_LOG_DIR)
    os.environ[ENV_LOG_DIR] = str(tmp_path_factory.mktemp("kriya-logs"))
    yield
    if previous is None:
        os.environ.pop(ENV_LOG_DIR, None)
    else:
        os.environ[ENV_LOG_DIR] = previous
