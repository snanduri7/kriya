"""Suite-wide isolation, inherited by CLI subprocesses through the environment:
- every Kriya log (application and per-run) goes to one session temp dir,
  never the real ~/.kriya/logs;
- every test gets its own fresh state directory (traces.db), never the real
  ~/.kriya/state: tests read trace rows back, so one shared database would leak
  rows between tests. Locate it with kriya.core.state_paths.trace_db_path();
- the historical pre-move trace database location points at a path that does
  not exist, so a developer's real <install>/logs/traces.db never changes a
  test's outcome (tests that need one patch it explicitly).
- PRD-013: the model runtime fingerprint probe (Ollama /api/version,
  /api/tags, /api/show) is disabled, so a mocked test can never reach a real
  local model endpoint running on the developer's machine. Tests marked
  live_model keep it enabled; probe tests inject their own transport. The
  PRD-014 qualification store is a per-test temp dir for the same reason.
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


_TEST_HOST = {"os": "linux", "architecture": "x86_64", "memory_bytes": 64 * (1 << 30), "cpu_model": None,
              "gpus": [], "gpu_backend": None}


@pytest.fixture(autouse=True)
def _no_model_runtime_probe(request, monkeypatch, tmp_path_factory):
    from kriya.core import execution_environment, model_qualification, model_runtime

    if request.node.get_closest_marker("live_model") is None:
        monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "0")
        # Qualification environment identity: a fixed, exact fake host, so a
        # mocked test never depends on the machine (sysctl/nvidia-smi/PATH)
        # it runs on. Environment tests override this themselves.
        monkeypatch.setattr(execution_environment, "_cached_host_properties", lambda: dict(_TEST_HOST))
        # PRD-014: never read or write the developer's real qualification store.
        monkeypatch.setenv(model_qualification.QUALIFICATION_HOME_ENV,
                           str(tmp_path_factory.mktemp("kriya-qualifications")))
    model_runtime.clear_model_runtime_cache()
    yield
    model_runtime.clear_model_runtime_cache()


# --- PRD-032 chaos report ----------------------------------------------------------
# Every @chaos test's verdict and observation is collected; with
# `--chaos-report DIR` the session writes chaos-report.json/.md there
# (tests/_chaos_report.py). Collection never changes a test's outcome.

def pytest_addoption(parser):
    parser.addoption("--chaos-report", default=None, metavar="DIR",
                     help="PRD-032: write the chaos report (JSON + Markdown) to DIR")


def pytest_configure(config):
    from _chaos_report import RESULTS

    config.stash[RESULTS] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    marker = item.get_closest_marker("chaos")
    if marker is None:
        return
    from _chaos_report import PHASE_REPORTS, RESULTS, item_verdict

    report = outcome.get_result()
    phases = item.stash.setdefault(PHASE_REPORTS, {})
    phases[report.when] = report
    if report.when == "teardown":
        observation = next((value for key, value in item.user_properties if key == "chaos_observation"), None)
        item.config.stash[RESULTS].append({
            "scenario_id": marker.args[0], "nodeid": item.nodeid,
            "verdict": item_verdict(phases), "observation": observation,
        })


@pytest.fixture
def chaos_case(request, tmp_path):
    """PRD-032: the invariant checker of one @chaos scenario (tests/_chaos_harness.py)."""
    from _chaos_harness import close_case, open_case

    case = open_case(request, tmp_path)
    yield case
    close_case(request, case)


def pytest_sessionfinish(session):
    directory = session.config.getoption("--chaos-report")
    if not directory:
        return
    import platform
    import subprocess

    from _chaos_harness import SCENARIOS, SUPPORTING_SUITES
    from _chaos_report import RESULTS, build_report, write_report

    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=str(session.config.rootpath), check=False).stdout.strip() or None
    run = {"revision": revision, "python": platform.python_version(),
           "selection": session.config.getoption("-m") or "",
           "exit_status": int(session.exitstatus)}
    write_report(directory, build_report(session.config.stash[RESULTS], SCENARIOS, SUPPORTING_SUITES, run))
