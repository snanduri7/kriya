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
import sys

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
    from kriya.core import execution_environment, model_certification, model_qualification, model_runtime

    if request.node.get_closest_marker("live_model") is None:
        monkeypatch.setenv(model_runtime.PROBE_ENV_VAR, "0")
        # Qualification environment identity: a fixed, exact fake host, so a
        # mocked test never depends on the machine (sysctl/nvidia-smi/PATH)
        # it runs on. Environment tests override this themselves.
        monkeypatch.setattr(execution_environment, "_cached_host_properties", lambda: dict(_TEST_HOST))
        # PRD-014: never read or write the developer's real qualification store.
        monkeypatch.setenv(model_qualification.QUALIFICATION_HOME_ENV,
                           str(tmp_path_factory.mktemp("kriya-qualifications")))
        # PRD-035/036: never read or write the real certification records or release streaks.
        monkeypatch.setenv(model_certification.CERTIFICATION_HOME_ENV,
                           str(tmp_path_factory.mktemp("kriya-certifications")))
    model_runtime.clear_model_runtime_cache()
    yield
    model_runtime.clear_model_runtime_cache()


@pytest.fixture(autouse=True)
def _scripted_developer_answers_speak_the_requested_protocol(request, monkeypatch):
    """FILE-INTEGRITY-CONTRACT-001: the production Developer protocol is the
    structured sentinel protocol, while most scripted model answers in this
    suite predate it (raw file content or the legacy markers). At the one
    per-file completion seam (DeveloperAgent._complete_file) such an answer
    is rendered into the protocol the prompt asks for - the same intent,
    read by the real legacy parser (tests/_protocol_responses.py). The
    production parser is untouched, a malformed answer stays malformed, and
    an answer already containing a sentinel line is never rewritten. A test
    that asserts how the production parser treats a non-sentinel answer opts
    out with @pytest.mark.developer_answers_verbatim; live tests never
    translate."""
    if request.node.get_closest_marker("developer_answers_verbatim") or request.node.get_closest_marker("live_model"):
        yield
        return
    from _protocol_responses import as_requested

    from kriya.agents.agent import DeveloperAgent

    real = DeveloperAgent._complete_file

    async def speaking_the_requested_protocol(self, system_prompt, prompt, **options):
        answer = await real(self, system_prompt, prompt, **options)
        return as_requested(answer, system_prompt) if isinstance(answer, str) else answer

    monkeypatch.setattr(DeveloperAgent, "_complete_file", speaking_the_requested_protocol)
    yield


# --- PRD-032 chaos report ----------------------------------------------------------
# Every @chaos test's verdict and observation is collected; with
# `--chaos-report DIR` the session writes chaos-report.json/.md there
# (tests/_chaos_report.py). Collection never changes a test's outcome.

def pytest_addoption(parser):
    parser.addoption("--chaos-report", default=None, metavar="DIR",
                     help="PRD-032: write the chaos report (JSON + Markdown) to DIR")
    parser.addoption("--certification", action="store_true", default=False,
                     help="PRD-034: an unexpected skip or xfail fails (tests/certification/skip_allowlist.yaml)")
    parser.addoption("--certification-report", default=None, metavar="DIR",
                     help="PRD-034: write skips.json (every skip, allowlisted or not) to DIR")
    parser.addoption("--model-certification", default=None, metavar="DIR",
                     help="PRD-035: write model-certification.json/.md (the live matrix report) to DIR")


# --- state-machine tier ------------------------------------------------------------
# `pytest -m state_machine` (docs: handover/STATE_MACHINE_HARDENING_REPORT.md):
# tests/state_machine/ marks itself; these existing deterministic suites own
# the rest of the tier's transition families and are marked here, in one
# place, rather than file by file. None may carry a live marker
# (tests/state_machine/test_sm_tier_guard.py).
STATE_MACHINE_TIER_FILES = frozenset({
    # retry / recovery / fallback
    "test_retry_policy.py", "test_prd026_retry_progress.py", "test_workflow_recovery_handback.py",
    "test_failure_signature_run_noise.py", "test_prd031_coordinators.py", "test_best_of_n.py",
    "test_prd017_fallback_transition.py",
    # resume / invalidation
    "test_prd008_resume_fingerprints.py", "test_prd008a_resume_convergence.py", "test_resume_integrity.py",
    "test_prd008_recovery.py",
    # verification -> terminal gates -> commit, static analysis -> commit
    "test_candidate_verification_binding.py", "test_prd030_terminal_services.py",
    "test_prd008_commit_state_gate.py", "test_prd032_terminal_commit_stop.py", "test_prd032_chaos_commit.py",
    "test_prd032_chaos_static_analysis.py", "test_prd031a_static_analysis.py",
    # process lifecycle feeding workflow decisions
    "test_platform_process_control_contract.py", "test_prd032_chaos_runtime.py",
})


# Parallel runs (`-n 8 --dist loadgroup`): every test that starts real
# containers runs on ONE worker, serially. Their leak checks list every
# kriya-oci-*/kriya-acq-* container on the daemon - deliberately global - so
# two such tests running at once see each other's live containers (measured:
# 7 parallel-only failures, zero real leftovers after the run). A real-Docker
# test is the one gated on Docker being there: a skip/skipif naming docker.
# Grouping by file text instead pinned ~540 tests (config tests that merely
# say "docker") to one worker and doubled the wall time.
def _needs_real_docker(item):
    return any("docker" in str(marker.kwargs.get("reason", "")).lower()
               for marker in item.iter_markers() if marker.name in ("skip", "skipif"))


# tryfirst: xdist's own hook turns the xdist_group marker into the node id
# suffix that --dist loadgroup schedules on; it must see the marker.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in STATE_MACHINE_TIER_FILES:
            item.add_marker(pytest.mark.state_machine)
        if _needs_real_docker(item):
            item.add_marker(pytest.mark.xdist_group("docker"))


_SKIPS = pytest.StashKey[list]()
_CERTIFICATION_CASES = pytest.StashKey[list]()
_ALLOWLIST = pytest.StashKey[tuple]()


def _certification_mode(config):
    return config.getoption("--certification") or os.environ.get("KRIYA_CERTIFICATION") == "1"


def pytest_configure(config):
    from _certification import load_allowlist
    from _chaos_report import RESULTS

    config.stash[RESULTS] = []
    config.stash[_SKIPS] = []
    config.stash[_CERTIFICATION_CASES] = []
    # A malformed allowlist fails certification up front, never later.
    config.stash[_ALLOWLIST] = load_allowlist() if _certification_mode(config) else ()


@pytest.fixture(scope="session", autouse=True)
def _interpreter_tools_on_path():
    """PRD-034: the environment under test is the interpreter running pytest.
    Tests that exercise Kriya running `python`/`pip` from PATH resolve them
    here, never from whatever the operator's shell happened to have first."""
    previous = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([os.path.dirname(sys.executable), previous])
    yield
    os.environ["PATH"] = previous


def _audit_skip(item, report):
    """PRD-034: record every skip/xfail; in certification mode an
    unallowlisted one becomes a failure."""
    if not (report.skipped or hasattr(report, "wasxfail")):
        return
    from _certification import match_skip, skip_reason

    reason = str(getattr(report, "wasxfail", "")) or skip_reason(report.longrepr)
    entry, expired = match_skip(item.config.stash[_ALLOWLIST], item.nodeid, reason)
    allowed = entry is not None and not expired
    item.config.stash[_SKIPS].append({
        "nodeid": item.nodeid, "phase": report.when, "reason": reason,
        "kind": "xfail" if hasattr(report, "wasxfail") else "skip",
        "allowlisted": entry.id if allowed else None, "expired_entry": entry.id if expired else None,
    })
    if _certification_mode(item.config) and not allowed:
        report.outcome = "failed"
        report.longrepr = (f"UNEXPECTED SKIP in certification mode: {reason}"
                           + (f" (allowlist entry {entry.id} expired)" if expired else ""))


def _record_chaos_phase(item, report):
    """PRD-032: every @chaos test's phases, verdict and observation."""
    marker = item.get_closest_marker("chaos")
    if marker is None:
        return
    from _chaos_report import PHASE_REPORTS, RESULTS, item_verdict

    phases = item.stash.setdefault(PHASE_REPORTS, {})
    phases[report.when] = report
    if report.when == "teardown":
        observation = next((value for key, value in item.user_properties if key == "chaos_observation"), None)
        item.config.stash[RESULTS].append({
            "scenario_id": marker.args[0], "nodeid": item.nodeid,
            "verdict": item_verdict(phases), "observation": observation,
        })


def _record_certification_case(item, report):
    """PRD-035: every live certification case's verdict and record."""
    marker = item.get_closest_marker("certification_case")
    if marker is None:
        return
    phases = item.stash.setdefault(_CERTIFICATION_PHASES, {})
    phases[report.when] = report
    if report.when == "teardown":
        from _chaos_report import item_verdict

        record = next((value for key, value in item.user_properties if key == "model_certification_case"), None)
        item.config.stash[_CERTIFICATION_CASES].append({
            "case_id": marker.args[0], "verdict": item_verdict(phases), "record": record or {},
        })


_CERTIFICATION_PHASES = pytest.StashKey[dict]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    _audit_skip(item, report)  # first: a certification failure is the verdict the chaos report records
    _record_chaos_phase(item, report)
    _record_certification_case(item, report)


@pytest.fixture
def chaos_case(request, tmp_path):
    """PRD-032: the invariant checker of one @chaos scenario (tests/_chaos_harness.py)."""
    from _chaos_harness import close_case, open_case

    case = open_case(request, tmp_path)
    yield case
    close_case(request, case)


def pytest_sessionfinish(session):
    from _strict_doubles import release_default_roots

    release_default_roots()
    model_dir = session.config.getoption("--model-certification")
    if model_dir:
        from _model_certification import build_report as build_model_report
        from _model_certification import write_report as write_model_report

        write_model_report(model_dir, build_model_report(session.config.stash[_CERTIFICATION_CASES]))
    certification_dir = session.config.getoption("--certification-report")
    if certification_dir:
        from _certification import write_skips

        write_skips(certification_dir, session.config.stash[_SKIPS], bool(_certification_mode(session.config)))
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
