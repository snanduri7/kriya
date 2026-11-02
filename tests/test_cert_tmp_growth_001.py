"""CERT-TMP-GROWTH-001 / PRD-034: the canonical certification run must not be able to crawl again, must be observable while it runs, and must never
turn a Linux-host-only mandatory measurement into a macOS pass.

c3-run1 (2026-11-02) fell from normal throughput to ~4 tests/min with ~83 % kernel CPU. MEASURED: path-identity checks list every ancestor directory in full,
and pytest's basetemp (5 entries per test, never emptied) plus an ambient temp root full of earlier leftovers made each listing O(tens of thousands).
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from _certification_progress import (
    DOCKER_INCONSISTENT,
    FAILURE_SEEN,
    KERNEL_BOUND,
    NO_PROGRESS,
    PROJECTION_DIVERGES,
    SLOW_NODE,
    THROUGHPUT_COLLAPSE,
    ProgressWriter,
    assess,
    read_progress,
    render_line,
)

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
SAMPLES = TESTS / "certification"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_run(tmp_path, sample, *args, certification=True):
    report = tmp_path / "cert"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(SAMPLES / sample), "-p", "no:cacheprovider", "-q", "--certification-report", str(report),
         *(["--certification"] if certification else []), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "KRIYA_CERTIFICATION": "", "TMPDIR": str(tmp_path / "t")},
    )
    return completed, json.loads((report / "skips.json").read_text()), report


# --- bounded basetemp -----------------------------------------------------------------------------------------------------------------

def test_a_passed_test_leaves_no_per_test_directory_and_a_failed_one_keeps_them_all(tmp_path):
    (tmp_path / "t").mkdir()
    completed, _, _ = _sample_run(tmp_path, "sample_tmp_retention.py", certification=False)
    assert completed.returncode == 1 and "1 failed, 1 passed" in completed.stdout, completed.stdout[-1500:]
    bases = sorted((tmp_path / "t").glob("pytest-of-*/pytest-[0-9]*"))
    assert len(bases) == 1, "the failed test keeps the base temp directory"
    # ``mktemp`` numbers each directory and keeps one ``<name>current`` symlink per prefix; only the numbered directories are per-test state
    names = sorted(entry.name for entry in bases[0].iterdir() if entry.name[-1].isdigit())
    # the failed test's tmp_path and its isolation directories are all kept for diagnosis ...
    assert [n for n in names if n.startswith("test_fails")] == ["test_fails0"], names
    for prefix in ("kriya-state", "kriya-authority", "kriya-qualifications", "kriya-certifications"):
        assert len([n for n in names if re.fullmatch(prefix + r"\d+", n)]) == 1, (prefix, names)
    # ... and the passed test left NOTHING (no tmp_path, no isolation directory of its own, and not the sibling file named after its tmp_path: pytest REUSES a released number,
    # so the next parametrization of the same test would be handed the stale sibling - MEASURED with tests/test_d1_tool_crash.py's append-mode effects log)
    assert not any(n.startswith("test_passes") for n in names), names
    everything = sorted(entry.name for entry in bases[0].iterdir())
    assert "test_fails0-sibling.log" in everything and not any(n.startswith("test_passes") for n in everything), everything


def test_the_milestone_harness_releases_its_candidate_directory(tmp_path, monkeypatch):
    """The harness used to leave one ``tempfile.mkdtemp()`` per applied change in the temp root (~2 per milestone run: 88 over 47 tests)."""
    import _milestone_proof_harness as harness

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    workspace = harness._workspace(tmp_path)  # pylint: disable=protected-access
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    harness._apply(workspace, {"m0.py": b"x = 1\n"})  # pylint: disable=protected-access
    assert (workspace / "m0.py").read_bytes() == b"x = 1\n", "the change really was committed"
    assert list(scratch.iterdir()) == [], "the candidate directory was released"


# --- the performance probe's verdict --------------------------------------------------------------------------------------------------

HEALTHY = {"failed": 0, "exit_status": 0, "tmp_root_entries_before": 0, "tmp_root_entries_after": 2, "wall_s": 70.0, "listing_s": 3.5,
           "listing_max_entries": 800, "listing_worst_path": "/tmp/x", "basetemp_samples": [[i, 4] for i in range(1, 60)]}


def test_the_probe_passes_a_healthy_measurement_and_names_each_way_the_crawl_could_recur():
    probe = _load_script("certification_perf_probe")
    assert probe.evaluate(HEALTHY) == (True, [])
    growing = {**HEALTHY, "basetemp_samples": [[i, 5 * i] for i in range(1, 60)]}  # the c3-run1 shape: 5 entries per test, none released
    ok, problems = probe.evaluate(growing)
    assert not ok and any("basetemp grew" in p for p in problems)
    ok, problems = probe.evaluate({**HEALTHY, "listing_s": 40.0, "listing_max_entries": 22857})  # 57 % of the time listing directories
    assert not ok and any("directory listing" in p and "22857" in p for p in problems)
    ok, problems = probe.evaluate({**HEALTHY, "tmp_root_entries_before": 23332})  # the ambient polluted temp root
    assert not ok and any("already held 23332" in p for p in problems)
    ok, problems = probe.evaluate({**HEALTHY, "tmp_root_entries_after": 89})  # a test that leaks a scratch directory per run
    assert not ok and any("left 89 entries" in p for p in problems)
    ok, problems = probe.evaluate({**HEALTHY, "failed": 1, "exit_status": 1})
    assert not ok and any("did not all pass" in p for p in problems)
    # only the FIRST class must start in an empty temp root: the second legitimately follows the first
    assert probe.evaluate({**HEALTHY, "tmp_root_entries_before": 60, "tmp_root_entries_after": 62}, first_class=False)[0]


# --- progress and health --------------------------------------------------------------------------------------------------------------

class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Report:
    def __init__(self, nodeid, when="call", outcome="passed"):
        self.nodeid, self.when = nodeid, when
        self.passed, self.failed, self.skipped = outcome == "passed", outcome == "failed", outcome == "skipped"


def _run_nodes(writer, clock, count, seconds_each, *, outcome="passed"):
    for _ in range(count):
        node = f"tests/test_x.py::test_{writer._state['done']}"  # pylint: disable=protected-access
        writer.start(node)
        clock.now += seconds_each
        writer.report(Report(node, "call", outcome))
        writer.report(Report(node, "teardown"))


def test_the_progress_writer_is_immune_to_a_test_that_patches_the_os_primitives_it_uses(tmp_path, monkeypatch):
    """c3-run2: tests/test_d1_durable_write.py patches ``os.replace``/``os.fsync`` to fail; the progress flush ran during that test and aborted pytest at 15 %."""
    clock = Clock()
    writer = ProgressWriter(str(tmp_path), {"tests/test_x.py::test_0": []}, clock=clock)

    def hostile(*_args, **_kwargs):
        raise OSError(18, "injected failure")

    for name in ("replace", "fsync", "getpid"):
        monkeypatch.setattr(os, name, hostile)
    monkeypatch.setattr(json, "dump", hostile)
    writer.start("tests/test_x.py::test_0")
    clock.now += 3.0
    writer.report(Report("tests/test_x.py::test_0", "call", "passed"))
    writer.report(Report("tests/test_x.py::test_0", "teardown"))
    writer.finish(0)
    done = read_progress(str(tmp_path))
    assert (done["done"], done["passed"], done["finished"], done["write_errors"]) == (1, 1, True, 0), "written through the bound primitives, not the patched ones"
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_progress_write_is_counted_never_raised_and_the_file_recovers(tmp_path):
    clock = Clock()
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    writer = ProgressWriter(str(report_dir), {"tests/test_x.py::test_0": []}, clock=clock)
    moved = tmp_path / "moved"
    report_dir.rename(moved)                             # the report directory vanishes under a running writer
    writer.start("tests/test_x.py::test_0")              # must not raise: observability never changes the run's outcome
    writer.report(Report("tests/test_x.py::test_0", "call", "passed"))
    assert writer._state["write_errors"] == 2            # pylint: disable=protected-access
    moved.rename(report_dir)
    writer.report(Report("tests/test_x.py::test_0", "teardown"))
    recovered = read_progress(str(report_dir))
    assert recovered["done"] == 1 and recovered["passed"] == 1 and recovered["write_errors"] == 2, "the next write succeeds and records how many were lost"


def test_the_exact_c3_run2_scenario_a_real_pytest_session_with_the_report_active_completes_over_the_replace_patching_test(tmp_path):
    root = Path(__file__).resolve().parent.parent
    done = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "-q", "tests/test_d1_durable_write.py", f"--certification-report={tmp_path}"],
                          cwd=root, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0 and "INTERNALERROR" not in done.stdout + done.stderr, done.stdout[-1500:] + done.stderr[-1500:]
    final = read_progress(str(tmp_path / "pytest")) or read_progress(str(tmp_path))
    assert final is not None and final["finished"] is True and final["done"] == final["total"] > 0 and final["exit_status"] == 0


def test_the_progress_file_counts_outcomes_names_the_current_node_and_its_tiers(tmp_path):
    clock = Clock()
    tiers = {"tests/test_x.py::test_0": ["docker"], "tests/test_x.py::test_1": [], "tests/test_x.py::test_2": ["live_static_analysis"]}
    writer = ProgressWriter(str(tmp_path), tiers, clock=clock)
    assert read_progress(str(tmp_path))["total"] == 3 and read_progress(str(tmp_path))["done"] == 0
    writer.start("tests/test_x.py::test_0")
    running = read_progress(str(tmp_path))
    assert running["current"] == "tests/test_x.py::test_0" and running["current_tiers"] == ["docker"]
    clock.now += 12.0
    writer.report(Report("tests/test_x.py::test_0", "call", "passed"))
    writer.report(Report("tests/test_x.py::test_0", "teardown"))
    writer.start("tests/test_x.py::test_1")
    writer.report(Report("tests/test_x.py::test_1", "setup", "failed"))  # a setup error is an ERROR, not a failure
    writer.report(Report("tests/test_x.py::test_1", "teardown"))
    writer.start("tests/test_x.py::test_2")
    writer.report(Report("tests/test_x.py::test_2", "call", "failed"))
    writer.report(Report("tests/test_x.py::test_2", "teardown"))
    snapshot = read_progress(str(tmp_path))
    assert (snapshot["done"], snapshot["passed"], snapshot["errors"], snapshot["failed"]) == (3, 1, 1, 1)
    assert snapshot["longest_node_s"] == 12.0 and snapshot["failed_nodes"] == ["setup:tests/test_x.py::test_1", "call:tests/test_x.py::test_2"]
    writer.finish(1)
    assert read_progress(str(tmp_path))["finished"] is True and assess(read_progress(str(tmp_path)), now=clock.now) == []
    assert not list(tmp_path.glob("*.tmp")), "replaced atomically: no half-written temporary left"


def _healthy_history(tmp_path, clock, windows=6):
    writer = ProgressWriter(str(tmp_path), {f"tests/test_x.py::test_{i}": () for i in range(20000)}, clock=clock)
    _run_nodes(writer, clock, windows * 30, 30.0)  # 30 tests per 15 min... steady 1 test / 30 s for 6 windows
    return writer


def test_a_live_process_is_not_proof_of_health(tmp_path):
    clock = Clock()
    writer = _healthy_history(tmp_path, clock)
    snapshot = read_progress(str(tmp_path))
    assert assess(snapshot, now=clock.now, cpu={"user": 100.0, "system": 30.0}) == []  # steady, user-dominated: healthy
    # throughput collapse against the run's OWN earlier throughput: 1 test per 30 s becomes 1 per 10 min
    _run_nodes(writer, clock, 4, 600.0)
    flags = {item["flag"] for item in assess(read_progress(str(tmp_path)), now=clock.now)}
    assert THROUGHPUT_COLLAPSE in flags and PROJECTION_DIVERGES in flags
    # kernel-dominated CPU over the recent window (the run-1 signature: 83 % system)
    flags = {item["flag"] for item in assess(read_progress(str(tmp_path)), now=clock.now, cpu={"user": 20.0, "system": 100.0})}
    assert KERNEL_BOUND in flags
    # a node that stopped completing
    writer.start("tests/test_x.py::stuck")
    clock.now += 1300.0
    flags = {item["flag"] for item in assess(read_progress(str(tmp_path)), now=clock.now)}
    assert NO_PROGRESS in flags
    # an exited process is reported, whatever its file says
    assert assess(read_progress(str(tmp_path)), now=clock.now, process_alive=False)[0]["flag"] == "PROCESS_GONE"


def test_a_failure_that_makes_certification_impossible_is_reported_at_once_and_a_slow_node_is_judged_against_the_slowest_completed(tmp_path):
    clock = Clock()
    writer = ProgressWriter(str(tmp_path), {f"tests/test_x.py::test_{i}": () for i in range(100)}, clock=clock)
    _run_nodes(writer, clock, 3, 5.0)
    writer.start("tests/test_x.py::boom")
    writer.report(Report("tests/test_x.py::boom", "call", "failed"))
    writer.report(Report("tests/test_x.py::boom", "teardown"))
    flags = assess(read_progress(str(tmp_path)), now=clock.now)
    assert [item["flag"] for item in flags] == [FAILURE_SEEN] and "boom" in flags[0]["detail"]
    (tmp_path / "clean").mkdir()
    clean = ProgressWriter(str(tmp_path / "clean"), {"n": ()}, clock=clock)
    clean.start("n")
    clock.now += 100.0  # slower than anything completed so far but well under 15 min: still healthy
    assert assess(read_progress(str(tmp_path / "clean")), now=clock.now) == []
    clock.now += 1000.0
    assert SLOW_NODE in {item["flag"] for item in assess(read_progress(str(tmp_path / "clean")), now=clock.now)}


def test_a_docker_tier_node_with_no_container_and_no_child_process_is_inconsistent_and_docker_ps_alone_is_not_the_evidence(tmp_path):
    clock = Clock()
    writer = ProgressWriter(str(tmp_path), {"tests/test_d.py::test_real": ["docker"], "tests/test_o.py::test_plain": []}, clock=clock)
    writer.start("tests/test_d.py::test_real")
    clock.now += 400.0
    snapshot = read_progress(str(tmp_path))
    assert DOCKER_INCONSISTENT in {f["flag"] for f in assess(snapshot, now=clock.now, docker_containers=0, child_processes=0)}
    assert assess(snapshot, now=clock.now, docker_containers=0, child_processes=2) == [], "a child process (docker CLI, mvn) is activity"
    assert assess(snapshot, now=clock.now, docker_containers=1, child_processes=0) == []
    writer.start("tests/test_o.py::test_plain")
    clock.now += 400.0
    assert assess(read_progress(str(tmp_path)), now=clock.now, docker_containers=0, child_processes=0) == [], "an ordinary test needs no container"


def test_the_progress_line_names_the_tier_and_says_there_is_no_live_model_inference(tmp_path):
    clock = Clock()
    writer = ProgressWriter(str(tmp_path), {"tests/test_d.py::test_real": ["docker"], "tests/test_o.py::test_plain": []}, clock=clock)
    writer.start("tests/test_d.py::test_real")
    line = render_line(read_progress(str(tmp_path)), now=clock.now, flags=[], docker_containers=1)
    assert "0 / 2" in line and "[docker]" in line and "Docker tier ACTIVE (1 containers)" in line and "no live-model inference" in line
    writer.report(Report("tests/test_d.py::test_real", "teardown"))
    writer.start("tests/test_o.py::test_plain")
    line = render_line(read_progress(str(tmp_path)), now=clock.now, flags=[{"flag": "KERNEL_BOUND", "detail": "83%"}], docker_containers=0)
    assert "[ordinary pytest]" in line and "Docker tier inactive" in line and "HEALTH: KERNEL_BOUND - 83%" in line


def test_the_watchdog_reads_the_progress_file_and_prints_one_line(tmp_path):
    (tmp_path / "pytest").mkdir()
    writer = ProgressWriter(str(tmp_path / "pytest"), {"tests/test_x.py::test_0": ()})
    writer.start("tests/test_x.py::test_0")
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "certification_watch.py"), str(tmp_path), "--once"],
                               capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("C3: 0 / 1") and "pytest alive" in completed.stdout, completed.stdout
    assert json.loads((tmp_path / "health.json").read_text())["snapshot"]["current"] == "tests/test_x.py::test_0"
    assert (tmp_path / "health.log").read_text().count("C3: 0 / 1") == 1


# --- certification-platform matrix: a Linux-host-only mandatory test is PENDING on macOS, never a pass --------------------------------

def test_a_test_mandatory_on_another_platform_is_pending_evidence_not_a_failure_and_not_a_pass(tmp_path):
    completed, skips, _ = _sample_run(tmp_path, "sample_platform.py", "-k", "far_platform")
    assert completed.returncode == 0, completed.stdout[-1500:]
    assert skips["unexpected"] == 0 and skips["skipped"] == 1
    assert [(p["nodeid"].split("::")[-1], p["required_platform"]) for p in skips["platform_pending"]] == [("test_far_platform", "nosuchos")]
    assert skips["skips"][0]["allowlisted"] is None, "pending is NOT an allowlist entry"


def test_the_same_skip_on_the_required_platform_is_an_unexpected_skip_and_fails(tmp_path):
    completed, skips, _ = _sample_run(tmp_path, "sample_platform.py", "-k", "this_platform")
    assert completed.returncode == 1, "on the platform it is required of, a skip is a failure like any other"
    assert skips["unexpected"] == 1 and skips["platform_pending"] == []
    assert "UNEXPECTED SKIP in certification mode" in completed.stdout


def test_the_linux_host_measurement_carries_the_platform_requirement():
    import _certification_collection as collection
    from _certification import required_platform

    source = (TESTS / "test_d2_m2_fd_inheritance.py").read_text()
    assert re.search(r'@pytest\.mark\.requires_platform\("linux"\)[^\n]*\n@pytest\.mark\.skipif\(not os\.path\.isdir\("/proc/self/fd"\)[^\n]*\n[^\n]*\ndef '
                     r'test_linux_a_child_cannot_reopen_the_waiters_socket_through_proc', source), "the Linux-host measurement declares its platform"
    record = collection.census_record("t.py::test_x", [pytest.mark.requires_platform("linux").mark], False)
    assert record["requires_platform"] == "linux" and required_platform([]) is None


def _census(*records):
    return {"tests": [{"id": i, "markers": [], "skip_reasons": [], "docker": False, "requires_platform": r} for i, r in records]}


def test_execution_audit_keeps_other_platform_evidence_pending_whatever_it_did_locally_and_requires_a_pass_where_it_applies():
    from _certification_collection import executed

    census = _census(("a.py::test_linux", "linux"), ("a.py::test_plain", None))
    outcomes = {("a", "test_linux"): "skipped", ("a", "test_plain"): "passed"}
    mac = executed(census, outcomes, floors={}, platform="darwin")
    assert mac["ok"] is True and mac["platform_pending"] == [{"id": "a.py::test_linux", "required_platform": "linux", "local_outcome": "skipped"}]
    # even if it somehow PASSED here, that is not the evidence its platform requires
    assert executed(census, {**outcomes, ("a", "test_linux"): "passed"}, floors={}, platform="darwin")["platform_pending"][0]["local_outcome"] == "passed"
    linux = executed(census, outcomes, floors={}, platform="linux")
    assert linux["ok"] is False and linux["platform_pending"] == [] and "platform linux: a.py::test_linux -> skipped" in linux["problems"]
    assert executed(census, {**outcomes, ("a", "test_linux"): "passed"}, floors={}, platform="linux")["ok"] is True


def _summary(tmp_path, stages, pending):
    sys.path.insert(0, str(TESTS))
    from test_prd034_certification import PASSING, _certification_out, _summary_module

    out = _certification_out(tmp_path, {**PASSING, **stages})
    Path(out, "tier-execution.json").write_text(json.dumps({"per_tier": {}, "platform_pending": pending}))
    return _summary_module(), out


PENDING = [{"id": "tests/test_d2_m2_fd_inheritance.py::test_linux_x", "required_platform": "linux", "local_outcome": "skipped"}]


def test_the_summary_is_never_certified_while_required_platform_evidence_is_missing(tmp_path):
    module, out = _summary(tmp_path / "a", {}, PENDING)
    summary = module.summarize(out)
    assert summary["status"] == "INCOMPLETE_PLATFORM_EVIDENCE" and summary["status"] != "CERTIFIED" and summary["problems"] == []
    assert module.main(out) == 3, "distinct from 0 (CERTIFIED) and 1 (NOT_CERTIFIED)"
    text = Path(out, "certification-summary.md").read_text()
    assert "REQUIRED LINUX-HOST EVIDENCE - NOT EXERCISED LOCALLY" in text and "test_linux_x" in text
    # a real failure outranks missing platform evidence
    failing, out_failing = _summary(tmp_path / "b", {"pytest": "FAIL"}, PENDING)
    assert failing.summarize(out_failing)["status"] == "NOT_CERTIFIED"
    # with nothing pending (the hosted Linux cell) the same stages ARE certified
    clean, out_clean = _summary(tmp_path / "c", {}, [])
    assert clean.summarize(out_clean)["status"] == "CERTIFIED" and clean.main(out_clean) == 0


# --- certify.sh ------------------------------------------------------------------------------------------------------------------------

def test_certify_runs_in_its_own_fresh_temp_root_and_removes_only_that_and_probes_before_the_long_run():
    script = (ROOT / "scripts" / "certify.sh").read_text()
    assert 'CERT_TMP="$(mktemp -d /tmp/kriya-cert.XXXXXX)"' in script and 'export TMPDIR="$CERT_TMP"' in script
    assert 'rm -rf "$CERT_TMP"' in script and script.count("rm -rf") == 1, "it deletes exactly its own directory, nothing ambient"
    assert script.index("export TMPDIR=") < script.index("stage environment RECORDED")
    probe, canonical = script.index("scripts/certification_perf_probe.py"), script.index("-m \"not live_model\" -p no:cacheprovider -q --certification")
    assert probe < canonical, "the probe runs before the canonical population"
    assert 'elif [ "$PERF_PROBE_OK" != "1" ]; then' in script, "a failing probe blocks the canonical run"
    assert script.count('pytest -o addopts= -m "not live_model" -p no:cacheprovider -q --certification --certification-report') == 1


# --- deterministic Maven provisioning wiring (MAVEN-CENTRAL-CERTIFICATION-NONDETERMINISM-001) ---------------------------------------------

def test_certify_requires_the_attested_frozen_maven_repository_before_the_long_run_and_audits_what_was_served():
    script = (ROOT / "scripts" / "certify.sh").read_text()
    assert "certification_maven_repo.py verify" in script and "--require-attested" in script
    assert script.index("stage maven_repository") < script.index("stage collection")
    assert 'elif [ "$MAVEN_REPO_OK" != "1" ]; then' in script, "no frozen repository -> the canonical run is UNAVAILABLE, never a run on live Central"
    assert "KRIYA_CERT_MAVEN_MIRROR" in script and "stage maven_served" in script
    assert script.count("certification_maven_repo.py serve") == 1 and "--progress" in script, "served files are attributed to the pytest node that fetched them"
    assert "pip" not in script.split("stage maven_repository")[0].split("MAVEN-CENTRAL-CERTIFICATION-NONDETERMINISM-001")[-1], "provisioning here is network-free"


def test_the_hosted_job_provisions_the_same_manifest_with_the_same_tool():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    step = ci.index("Provision the frozen certification Maven repository")
    assert "certification_maven_repo.py fetch tests/certification/maven_repository.manifest.json" in ci[step:step + 1500]
    assert step < ci.index("scripts/certify.sh certification-out/ci"), "provisioned before the certification runs"


def test_a_manifest_that_was_not_attested_cannot_certify():
    """The shipped manifest may be a bootstrap CANDIDATE (built from an unverified pool); certify.sh passes --require-attested, so it refuses until `attest` ran."""
    tool = _load_script("certification_maven_repo")
    manifest = tool.load_manifest(str(ROOT / "tests" / "certification" / "maven_repository.manifest.json"))
    unattested = [i for i in manifest["files"] if i["kind"] in ("artifact", "pom") and not i["provenance"].get("verified_against")]
    assert bool(manifest.get("attested")) == (not unattested), "the manifest's attested flag is exactly 'every artifact and pom was compared with Central's published SHA-1'"
