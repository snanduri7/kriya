"""PRD-034: certification mode turns every unexpected skip or xfail into a
failure; outside it, skips are only recorded. The allowlist is explicit,
owned and dated, and an expired entry allows nothing.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from _certification import AllowedSkip, AllowlistError, load_allowlist, match_skip

TESTS = Path(__file__).resolve().parent
SAMPLE = TESTS / "certification" / "sample_skips.py"


# The final pytest summary line, e.g. "1 failed, 1 passed, 1 warning, 1 error in 0.11s" (or "= 1 failed ... =" with
# padding on a wide terminal). Counts are read by outcome name, so warnings, duration and terminal formatting never
# decide the assertion; only the asserted outcome counts do.
_SUMMARY_COUNT = re.compile(r"(\d+) (failed|passed|errors?|skipped|xfailed|xpassed|warnings?|deselected)\b")


def summary_counts(stdout):
    """Outcome counts of the LAST summary line in ``stdout`` (``{'failed': 1, 'passed': 1, 'error': 1, ...}``);
    plural names are normalized (``errors`` -> ``error``, ``warnings`` -> ``warning``)."""
    lines = [line for line in stdout.splitlines() if _SUMMARY_COUNT.search(line) and " in " in line]
    if not lines:
        return {}
    return {name.rstrip("s"): int(count) for count, name in _SUMMARY_COUNT.findall(lines[-1])}


def assert_outcome_counts(stdout, **expected):
    """Assert exactly the given outcome counts (absent = 0) and ignore every other summary field (warnings, time)."""
    counts = summary_counts(stdout)
    actual = {name: counts.get(name, 0) for name in expected}
    assert actual == expected, f"summary counts {counts} (expected {expected}) in: {stdout[-2000:]}"


def _run(tmp_path, *args, env=None):
    report = tmp_path / "cert"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(SAMPLE), "-p", "no:cacheprovider", "-q",
         "--certification-report", str(report), *args],
        cwd=str(TESTS.parent), capture_output=True, text=True, timeout=300,
        env={**os.environ, "KRIYA_CERTIFICATION": "", **(env or {})},
    )
    return completed, json.loads((report / "skips.json").read_text())


def test_outside_certification_skips_are_recorded_not_failed(tmp_path):
    completed, skips = _run(tmp_path)
    assert completed.returncode == 0, completed.stdout[-2000:]
    assert skips["certification_mode"] is False and skips["skipped"] == 2 and skips["unexpected"] == 2
    assert {s["kind"] for s in skips["skips"]} == {"skip", "xfail"}


def test_certification_fails_every_unexpected_skip_and_xfail(tmp_path):
    completed, skips = _run(tmp_path, "--certification")
    assert completed.returncode == 1
    # The structured record, not a stdout count: the summary lines are
    # truncated to the terminal width, so how often the message is printed
    # depends on the environment (2 locally, 4 on the hosted runner).
    assert skips["certification_mode"] is True and skips["unexpected"] == 2
    assert {s["kind"] for s in skips["skips"]} == {"skip", "xfail"}
    assert "UNEXPECTED SKIP in certification mode" in completed.stdout
    # A setup-phase skip becomes a setup error; either way the run fails. Counts only: an intervening "1 warning"
    # (e.g. an unregistered marker in a run without pytest-xdist) or the duration never decides this assertion.
    assert_outcome_counts(completed.stdout, failed=1, passed=1, error=1)


def test_outcome_counts_ignore_warnings_and_formatting_but_not_the_counts():
    with_warning = "...\n1 failed, 1 passed, 1 warning, 1 error in 0.11s\n"
    assert summary_counts(with_warning) == {"failed": 1, "passed": 1, "warning": 1, "error": 1}
    assert_outcome_counts(with_warning, failed=1, passed=1, error=1)  # the warning is accepted
    assert_outcome_counts("=========== 1 failed, 1 passed, 3 warnings, 2 errors in 12.50s (0:00:12) ===========\n", failed=1, passed=1, error=2)
    assert_outcome_counts("1 passed, 1 skipped, 1 xfailed in 0.10s\n", passed=1, skipped=1, xfailed=1, failed=0, error=0)
    for wrong in ("2 failed, 1 passed, 1 warning, 1 error in 0.11s\n",   # wrong count
                  "1 failed, 1 passed, 1 warning in 0.11s\n",            # missing outcome
                  "1 failed, 1 passed, 1 error\n",                       # no summary line (no duration)
                  ""):
        with pytest.raises(AssertionError):
            assert_outcome_counts(wrong, failed=1, passed=1, error=1)
    # the earlier substring assertion is what broke on the owner's run: it is NOT accepted by the fragile form
    assert "1 failed, 1 passed, 1 error" not in with_warning


def _allowlist(tmp_path, expires):
    path = tmp_path / "allow.yaml"
    path.write_text(
        "schema_version: 1\nallowed_skips:\n"
        "  - {id: DOCKER-X, nodeid: '*sample_skips.py::test_needs_docker', reason: 'test fixture', "
        f"owner: qa, expires: {expires}, reason_contains: docker}}\n"
        "  - {id: GAP-X, nodeid: '*::test_known_gap', reason: 'tracked gap', owner: qa, "
        f"expires: {expires}}}\n")
    return str(path)


def test_an_allowlisted_skip_passes_and_an_expired_entry_does_not(tmp_path):
    completed, skips = _run(tmp_path, "--certification", env={"KRIYA_SKIP_ALLOWLIST": _allowlist(tmp_path, "2999-01-01")})
    assert completed.returncode == 0, completed.stdout[-2000:]
    assert sorted(s["allowlisted"] for s in skips["skips"]) == ["DOCKER-X", "GAP-X"]
    expired, skips = _run(tmp_path, "--certification", env={"KRIYA_SKIP_ALLOWLIST": _allowlist(tmp_path, "2000-01-01")})
    assert expired.returncode == 1 and "expired" in expired.stdout
    assert all(s["expired_entry"] for s in skips["skips"])


def test_match_is_by_node_pattern_and_reason():
    entry = AllowedSkip("A", "*::test_x", "r", "o", date(2999, 1, 1), reason_contains="symlink")
    assert match_skip([entry], "t.py::test_x", "symlink not supported")[0] is entry
    assert match_skip([entry], "t.py::test_x", "docker CLI not available")[0] is None
    assert match_skip([entry], "t.py::test_y", "symlink not supported")[0] is None
    assert match_skip([entry], "t.py::test_x", "symlink", today=date(3000, 1, 1)) == (entry, True)


def test_a_malformed_allowlist_is_refused(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 1\nallowed_skips:\n  - {id: X, nodeid: '*', reason: r}\n")
    with pytest.raises(AllowlistError, match="needs"):
        load_allowlist(str(bad))


def test_the_repository_allowlist_is_valid_and_every_entry_is_owned_and_dated():
    for entry in load_allowlist():
        assert entry.owner and entry.reason and entry.expires > date(2026, 1, 1)


def _summary_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("certification_summary", TESTS.parent / "scripts" / "certification_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_IDENTITY = {"version": 1, "kriya": {"revision": "a" * 40, "dirty": False}, "digest": "d" * 64}


def _certification_out(tmp_path, stages, *, unexpected=0, doctor=None, identity=PINNED_IDENTITY):
    out = tmp_path / "out"
    (out / "pytest").mkdir(parents=True)
    (out / "stages.jsonl").write_text("".join(json.dumps({"stage": n, "status": s, "detail": ""}) + "\n"
                                              for n, s in stages.items()))
    (out / "pytest" / "skips.json").write_text(json.dumps({"skipped": unexpected, "unexpected": unexpected}))
    (out / "pytest" / "junit.xml").write_text(
        '<testsuites><testsuite tests="10" failures="0" errors="0" skipped="1"/></testsuites>')
    (out / "environment.json").write_text(json.dumps({"python": "3.14"}))
    if identity is not None:
        (out / "release-identity.json").write_text(json.dumps(identity))
    if doctor is not None:
        (out / "doctor.json").write_text(json.dumps(doctor))
    return str(out)


PASSING = {"environment": "RECORDED", "static": "PASS", "maven_repository": "PASS", "maven_served": "PASS", "python_wheelhouse": "PASS", "python_served": "PASS", "product_images": "PASS", "images_unchanged": "PASS", "collection": "PASS", "perf_probe": "PASS", "pytest": "PASS", "tier_execution": "PASS", "images": "PASS",
           "scanner": "PASS", "release": "PASS", "doctor": "RECORDED"}
DOCTOR = {"production_ready": False, "checks": [
    {"id": "model.qualification", "required": True, "status": "FAIL"},
    {"id": "profile.production", "required": True, "status": "PASS"}]}


def test_certified_only_when_every_mandatory_stage_passes_and_the_doctor_is_reported_as_is(tmp_path):
    module = _summary_module()
    summary = module.summarize(_certification_out(tmp_path / "a", PASSING, doctor=DOCTOR))
    assert summary["status"] == "CERTIFIED" and summary["problems"] == []
    assert summary["doctor"]["production_ready"] is False
    assert summary["doctor"]["failed_required"] == ["model.qualification"]
    assert summary["tiers"]["pytest"]["junit"]["passed"] == 9
    for stage, status in (("scanner", "UNAVAILABLE"), ("pytest", "FAIL"), ("images", "FAIL"), ("release", "FAIL"), ("collection", "FAIL"),
                          ("tier_execution", "FAIL"), ("perf_probe", "FAIL"), ("maven_repository", "FAIL"), ("maven_served", "FAIL"), ("python_wheelhouse", "FAIL"), ("python_served", "FAIL"), ("product_images", "FAIL"), ("images_unchanged", "FAIL")):
        failing = module.summarize(_certification_out(tmp_path / stage, {**PASSING, stage: status}, doctor=DOCTOR))
        assert failing["status"] == "NOT_CERTIFIED" and f"{stage}: {status}" in failing["problems"]


def test_a_requested_thresholds_freeze_that_did_not_record_is_never_certified(tmp_path):
    module = _summary_module()
    recorded = module.summarize(_certification_out(tmp_path / "r", {**PASSING, "thresholds": "RECORDED"}, doctor=DOCTOR))
    assert recorded["status"] == "CERTIFIED"
    failed = module.summarize(_certification_out(tmp_path / "f", {**PASSING, "thresholds": "FAIL"}, doctor=DOCTOR))
    assert failed["status"] == "NOT_CERTIFIED" and "thresholds: FAIL" in failed["problems"]


def test_an_unexpected_skip_or_a_missing_doctor_is_never_certified(tmp_path):
    module = _summary_module()
    skipped = module.summarize(_certification_out(tmp_path / "s", PASSING, unexpected=2, doctor=DOCTOR))
    assert skipped["status"] == "NOT_CERTIFIED" and "pytest: 2 unexpected skip(s)" in skipped["problems"]
    no_doctor = module.summarize(_certification_out(tmp_path / "d", {**PASSING, "doctor": "FAIL"}))
    assert no_doctor["status"] == "NOT_CERTIFIED" and no_doctor["doctor"] is None
    assert module.main(_certification_out(tmp_path / "m", PASSING, doctor=DOCTOR)) == 0


@pytest.mark.parametrize("identity,problem", [
    (None, "release_identity: not recorded"),
    ({"kriya": {"revision": "a" * 40, "dirty": False}}, "release_identity: not recorded"),
    ({**PINNED_IDENTITY, "kriya": {"revision": "a" * 40, "dirty": True}}, "release_identity: Kriya revision not pinned"),
    ({**PINNED_IDENTITY, "kriya": {"revision": "unavailable", "dirty": None}}, "release_identity: Kriya revision not pinned"),
])
def test_certification_requires_a_pinned_release_identity(tmp_path, identity, problem):
    module = _summary_module()
    summary = module.summarize(_certification_out(tmp_path, PASSING, doctor=DOCTOR, identity=identity))
    assert summary["status"] == "NOT_CERTIFIED"
    assert any(p.startswith(problem) for p in summary["problems"]), summary["problems"]
    assert "Release identity" in module.render(summary)


# --- scripts/certify.sh structure: authority is never minted, the canonical command is literal ----------------------------------------------

CERTIFY = (TESTS.parent / "scripts" / "certify.sh").read_text(encoding="utf-8")


def test_certify_never_executes_an_authority_approval():
    """SEC-009: certification is not the operator. `authority approve` may appear only as text (a comment, or the owner command the script prints);
    no line executes it (kriya/config/authority_approval.py: 'there is no CI runs approve non-interactively path')."""
    offenders = [line.strip() for line in CERTIFY.splitlines()
                 if "authority approve" in line and not line.lstrip().startswith("#") and "OWNER_CMD=" not in line and "printf" not in line]
    assert offenders == [], offenders
    assert "--confirm" not in CERTIFY


def test_certify_runs_the_literal_canonical_population_once():
    """C3: `pytest -o addopts= -m "not live_model"` includes real_toolchain and live_static_analysis; there is no second, smaller pytest population."""
    runs = [line for line in CERTIFY.splitlines() if line.lstrip().startswith(("if pytest ", "pytest ")) and "--collect-only" not in line]
    assert len(runs) == 1 and '-o addopts= -m "not live_model"' in runs[0]
    assert "-m live_static_analysis" not in CERTIFY and "and not live_static_analysis" not in CERTIFY and "and not real_toolchain" not in CERTIFY
    assert "--certification" in runs[0] and "--junitxml" in CERTIFY and "--collection-census" in CERTIFY


CI = (TESTS.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _certification_job():
    return CI.split("  production-certification:", 1)[1].split("\n  live-model-smoke:", 1)[0]


def test_the_hosted_certification_job_has_the_capacity_and_the_cold_runner_provisioning():
    job = _certification_job()
    assert int(re.search(r"timeout-minutes: (\d+)", job).group(1)) >= 360       # owner ruling 2026-11-04: GitHub's hosted-job ceiling for the first hosted measurement
    assert "serial by" not in job and "CERT_TOPOLOGY" in job and "union == census" in job     # the comment states the sharded topology, not the stale serial one
    assert job.index("pinned_images.py --pull") < job.index("run: scripts/seed_offline_maven.sh") < job.index("run: scripts/certify.sh")
    assert "-n " not in job and "xdist" not in job                                # the canonical certification stays serial
    assert "authority approve" not in job and "--trust-file" not in job            # a hosted job never mints (or smuggles in) an approval


def _jobs():
    body = CI.split("\njobs:\n", 1)[1]
    parts = re.split(r"\n  ([a-z][a-z-]*):\n", "\n" + body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_a_manual_dispatch_never_starts_a_live_inference_job():
    """Owner ruling 2026-11-04 (section 4): the PRD-034 hosted dispatch is non-live; PRD-035 owns the live matrix. Every job that installs or uses Ollama
    must be excluded from ``workflow_dispatch`` (and the nightly one stays schedule-only), while the certification job must still run on a dispatch."""
    jobs = _jobs()
    assert {"production-certification", "live-model-smoke", "nightly-live-model-matrix", "test", "lint"} <= set(jobs)
    live = [name for name, text in jobs.items() if "ollama" in text.lower()]
    assert sorted(live) == ["live-model-smoke", "nightly-live-model-matrix"]            # no third job quietly starts live inference
    assert "github.event_name != 'workflow_dispatch'" in jobs["live-model-smoke"]
    assert "github.event_name == 'schedule'" in jobs["nightly-live-model-matrix"]
    assert re.search(r"(?m)^    if:", jobs["production-certification"]) is None             # the certification job has no gate: a dispatch runs it
    assert "workflow_dispatch:" in CI.split("\njobs:\n", 1)[0]                          # and a dispatch is possible at all


def test_the_seed_script_resolves_from_coordinates_with_both_mavens_and_never_copies_a_cache():
    seed = (TESTS.parent / "scripts" / "seed_offline_maven.sh").read_text(encoding="utf-8")
    assert "mvn -B -ntp -q -Dmaven.repo.local=" in seed and "docker run" in seed and "--pull never" in seed
    assert "cp -R \"$HOME/.m2" not in seed and "rsync" not in seed
