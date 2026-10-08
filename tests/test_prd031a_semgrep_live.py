"""PRD-031A: the Semgrep adapter against the real pinned scanner (§16 tests 14, 15, 19, 20, 22, 29, 36; §18).

Marker ``live_static_analysis``: excluded by default, run with
``pytest -m live_static_analysis``. Needs Semgrep exactly 1.178.0 on PATH
(skips otherwise - another version never runs); the OCI cases also need
Docker and the pinned image present locally. No model is involved.

Every case goes through the real adapter and the real ProcessController
(and, for OCI, the real OCIContainmentBackend) on Kriya-style snapshot
directories built in tmp_path from the recorded fixture sources.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

import pytest
from _strict_doubles import strict_config

from kriya.static_analysis.adapters.semgrep import SemgrepAdapter, SemgrepSettings
from kriya.static_analysis.model import (
    CAPABILITY_UNKNOWN,
    CONTAINMENT_UNAVAILABLE,
    NEW_FINDING_BLOCKED,
    PROVIDER_PROBE_FAILED,
    RULE_PACK_INVALID,
    SCAN_INCOMPLETE,
    SCAN_OUTPUT_MALFORMED,
    SCAN_TIMEOUT,
    STATIC_ANALYSIS_EVIDENCE_STALE,
    TARGET_OVERSIZED,
    Outcome,
    ScanStatus,
    Severity,
    canonical_digest,
)
from kriya.static_analysis.port import ExecutionContext, ScanRequest
from kriya.static_analysis.service import StaticAnalysisRequest, StaticAnalysisService, commit_guard
from kriya.tools.containment import ContainmentProfile, PreparedContainment
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.workflow.edit_safety import StagedFileWrite, content_revision
from kriya.workflow.terminal_commit import commit_terminal_candidate
from kriya.workflow.verification_binding import bind_candidate

pytestmark = pytest.mark.live_static_analysis

PINNED_VERSION = "1.178.0"
IMAGE = "semgrep/semgrep@sha256:32e459968daabe7ab86968184a29109b9564aa00392401156f9788452b42786b"
FIXTURES = Path(__file__).parent / "fixtures" / "static_analysis" / "semgrep" / PINNED_VERSION
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
RULES = str(FIXTURES / "rules")
BAD = FIXTURES / "bad"
FINDINGS_TARGETS = list(MANIFEST["cases"]["findings_exit0"]["targets"])
PARTIAL_TARGETS = list(MANIFEST["cases"]["partial_parse"]["targets"])


@pytest.fixture(scope="module")
def pinned_semgrep() -> str:
    executable = shutil.which("semgrep")
    if executable is None:
        pytest.skip("semgrep is not on PATH; the live static-analysis tier needs Semgrep 1.178.0 exactly")
    with tempfile.TemporaryDirectory() as home:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "SEMGREP_SEND_METRICS": "off",
               "SEMGREP_ENABLE_VERSION_CHECK": "0"}
        out = subprocess.run([executable, "--version"], env=env, capture_output=True, text=True, timeout=60)
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    version = lines[-1] if lines else "<none>"
    if version != PINNED_VERSION:
        pytest.skip(f"installed semgrep is {version}, the live tier is pinned to {PINNED_VERSION} exactly")
    return executable


@pytest.fixture(scope="module")
def pinned_image(pinned_semgrep) -> str:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not available; the OCI case needs Docker and the pinned Semgrep image")
    info = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=30)
    if info.returncode != 0:
        pytest.skip("the docker daemon is not reachable")
    present = subprocess.run([docker, "image", "inspect", IMAGE], capture_output=True, timeout=30)
    if present.returncode != 0:
        pytest.skip(f"pinned image {IMAGE} is not present locally (docker pull it first)")
    return IMAGE


def build_snapshot(root: Path, extra=None) -> Path:
    for rel, text in {**MANIFEST["sources"], **(extra or {})}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def host(workspace: Path, packs: List = None, **overrides) -> SemgrepAdapter:
    settings = SemgrepSettings.model_validate({"version": PINNED_VERSION, "rule_packs": packs or [RULES], **overrides})
    return SemgrepAdapter(settings, ExecutionContext(contained=False, containment_backend=None,
                                                     workspace_root=str(workspace)))


def scan(adapter: SemgrepAdapter, root: Path, targets: List[str], tmp_path: Path, timeout: int = 120,
         max_target_bytes: int = 1_000_000):
    scratch = tmp_path / f"scratch-{len(list(tmp_path.glob('scratch-*')))}"
    scratch.mkdir()
    return adapter.scan(ScanRequest(root=str(root), targets=tuple(targets), timeout_seconds=timeout,
                                    scratch_dir=str(scratch), scan_id="live", max_target_bytes=max_target_bytes))


def rows(result):
    return sorted((f.rule_id, f.path, f.start_line, f.severity) for f in result.findings)


EXPECTED_FINDINGS = sorted([
    ("semgrep:java-runtime-exec", "src/main/java/app/App.java", 7, Severity.HIGH),
    ("semgrep:java-weak-hash", "src/main/java/app/App.java", 8, Severity.CRITICAL),
    ("semgrep:java-runtime-exec", "src/main/java/app/App.java", 12, Severity.HIGH),
    ("semgrep:java-runtime-exec", "src/test/java/app/AppTest.java", 5, Severity.HIGH),
    ("semgrep:python-print-info", "tests/test_x.py", 2, Severity.LOW),
    ("semgrep:python-eval", "tests/test_x.py", 2, Severity.MEDIUM),
])


def test_findings_at_exit_zero_are_reported_and_nosem_does_not_suppress(pinned_semgrep, workspace, tmp_path):
    adapter = host(workspace)
    assert adapter.probe().reason_code is None, adapter.probe().detail
    result = scan(adapter, build_snapshot(tmp_path / "snap"), FINDINGS_TARGETS, tmp_path)
    # COMPLETE means exit 0 (any nonzero exit maps to FAILED): findings at exit 0 (V3).
    assert (result.status, result.reason_code) == (ScanStatus.COMPLETE, None), result.detail
    assert rows(result) == EXPECTED_FINDINGS
    # The `// nosemgrep` line 12 finding is reported: --disable-nosem (V4, §8.4.1, test 19).
    assert ("semgrep:java-runtime-exec", "src/main/java/app/App.java", 12, Severity.HIGH) in rows(result)
    assert result.reported_version == PINNED_VERSION
    assert result.raw_sha256 == hashlib.sha256(result.raw_output.encode()).hexdigest()


def test_explicit_targets_under_default_ignored_dirs_are_analyzed(pinned_semgrep, workspace, tmp_path):
    # Test 20 / V5-V6: with NO .semgrepignore, explicitly passed targets under
    # src/test/ and tests/ (Semgrep's default-ignore list for directory scans)
    # ARE scanned by 1.178.0 and confirmed analyzed. Observed, not relied on:
    # coverage still comes from paths.scanned.
    snapshot = build_snapshot(tmp_path / "snap")
    assert not (snapshot / ".semgrepignore").exists()
    result = scan(host(workspace), snapshot, FINDINGS_TARGETS, tmp_path)
    assert result.analyzed == frozenset(FINDINGS_TARGETS)
    assert {"src/test/java/app/AppTest.java", "tests/test_x.py"} <= result.analyzed
    assert result.skipped == {}
    # A .semgrepignore naming them does not skip explicit targets either (V6);
    # the service never copies it into a snapshot (§8.4.2), this only records
    # the scanner's behaviour.
    ignored = build_snapshot(tmp_path / "snap-ignored", {".semgrepignore": "src/test/\ntests/\n"})
    again = scan(host(workspace), ignored, FINDINGS_TARGETS, tmp_path)
    assert again.status is ScanStatus.COMPLETE and again.analyzed == frozenset(FINDINGS_TARGETS)


def test_partial_parse_is_not_analyzed_but_its_finding_is_reported(pinned_semgrep, workspace, tmp_path):
    result = scan(host(workspace), build_snapshot(tmp_path / "snap"), PARTIAL_TARGETS, tmp_path)
    assert (result.status, result.reason_code) == (ScanStatus.INCOMPLETE, SCAN_INCOMPLETE)
    # V10: Partial.java is in scanned, skipped and errors[] (list-typed PartialParsing).
    assert "src/main/java/app/Partial.java" not in result.analyzed
    assert "src/main/java/app/Broken.java" not in result.analyzed
    assert ("semgrep:java-runtime-exec", "src/main/java/app/Partial.java", 5) in {
        (f.rule_id, f.path, f.start_line) for f in result.findings}
    assert {e.path for e in result.errors if e.kind == "target"} == {
        "src/main/java/app/Partial.java", "src/main/java/app/Broken.java"}
    # V11: the Python syntax error is silently recovered - Semgrep's report is the authority.
    assert "tests/syntax_err.py" in result.analyzed


@pytest.mark.parametrize("pack,reason", [
    ("invalid_pattern.yml", RULE_PACK_INVALID),
    ("invalid_schema.yml", RULE_PACK_INVALID),
    ("unknown_language.yml", CAPABILITY_UNKNOWN),
])
def test_malformed_rules_are_config_errors_not_findings(pinned_semgrep, workspace, tmp_path, pack, reason):
    result = scan(host(workspace, [str(BAD / pack)]), build_snapshot(tmp_path / "snap"),
                  ["src/main/java/app/App.java"], tmp_path)
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, reason), result.detail
    assert any(e.kind == "config" for e in result.errors)
    if pack == "invalid_pattern.yml":
        # V14: exit 2 with the valid rule's finding - kept as evidence only.
        assert [f.rule_id for f in result.findings] == ["semgrep:good-rule"]


def test_invalid_yaml_pack_is_rule_pack_invalid(pinned_semgrep, workspace, tmp_path):
    adapter = host(workspace, [str(BAD / "invalid_yaml.yml")])
    assert adapter.probe().reason_code == RULE_PACK_INVALID
    result = scan(adapter, build_snapshot(tmp_path / "snap"), ["src/main/java/app/App.java"], tmp_path)
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, RULE_PACK_INVALID)
    assert result.findings == ()


def test_invalid_invocation_and_timeout_are_never_clean(pinned_semgrep, workspace, tmp_path):
    snapshot = build_snapshot(tmp_path / "snap")
    # An option value the pinned CLI rejects: usage text on stdout, exit 2 -> malformed (test 14).
    crash = scan(host(workspace), snapshot, FINDINGS_TARGETS, tmp_path, max_target_bytes=-5)
    assert (crash.status, crash.reason_code) == (ScanStatus.MALFORMED_OUTPUT, SCAN_OUTPUT_MALFORMED)
    timed_out = scan(host(workspace), snapshot, FINDINGS_TARGETS, tmp_path, timeout=0)
    assert (timed_out.status, timed_out.reason_code) == (ScanStatus.TIMEOUT, SCAN_TIMEOUT)


def test_identity_is_exact_and_rule_edits_change_it(pinned_semgrep, workspace, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(RULES, pack)
    first_adapter = host(workspace, [str(pack)])
    first = first_adapter.probe().identity
    assert first.version == PINNED_VERSION and first.edition == "community"
    assert first.executable_digest == independent_host_digest(pinned_semgrep)
    assert first.rule_packs[0].digest == MANIFEST["rule_pack_digests"]["rules"]
    fingerprint = first_adapter.runtime_fingerprint()
    assert host(workspace, [str(pack)]).runtime_fingerprint() == fingerprint

    text = (pack / "java.yml").read_text()
    (pack / "java.yml").write_text(text.replace("Runtime.exec with", "Runtime.exec() with"))
    second_adapter = host(workspace, [str(pack)])
    second = second_adapter.probe().identity
    assert second.rule_packs[0].digest != first.rule_packs[0].digest
    assert second.identity_digest != first.identity_digest
    assert second_adapter.runtime_fingerprint() != fingerprint


def independent_host_digest(executable: str) -> str:
    """The expected host identity, derived without the adapter's locator:
    the entry point plus the one semgrep-core packaged in its venv."""
    entry = Path(os.path.realpath(executable))
    engines = list((entry.parent.parent / "lib").glob("python3*/site-packages/semgrep/bin/semgrep-core"))
    assert len(engines) == 1, engines
    return canonical_digest({"entry_point": hashlib.sha256(entry.read_bytes()).hexdigest(),
                             "semgrep_core": hashlib.sha256(engines[0].read_bytes()).hexdigest()})


class RecordingBackend:
    """The real OCI backend; records what it prepared for the assertion."""

    def __init__(self) -> None:
        self._backend = OCIContainmentBackend()
        self.name = self._backend.name
        self.prepared: List[PreparedContainment] = []

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        prepared = self._backend.prepare(profile, command)
        self.prepared.append(prepared)
        return prepared


def contained(workspace: Path, backend, image: str) -> SemgrepAdapter:
    settings = SemgrepSettings.model_validate({"version": PINNED_VERSION, "rule_packs": [RULES], "image": image})
    return SemgrepAdapter(settings, ExecutionContext(contained=True, containment_backend=backend,
                                                     workspace_root=str(workspace)))


def test_oci_contained_scan_matches_host_with_no_network(pinned_image, workspace, tmp_path):
    backend = RecordingBackend()
    adapter = contained(workspace, backend, pinned_image)
    probe = adapter.probe()
    assert probe.reason_code is None, probe.detail
    assert probe.identity.execution_location == "container" and probe.identity.network_enforced is True
    assert probe.identity.executable_digest == pinned_image.rsplit(":", 1)[1]

    snapshot = build_snapshot(tmp_path / "snap")
    result = scan(adapter, snapshot, FINDINGS_TARGETS, tmp_path)
    assert (result.status, result.reason_code) == (ScanStatus.COMPLETE, None), result.detail
    # Same normalized findings as the pipx run: the container's check_id prefix
    # (kriya.rules.0.<id>) differs from the host's dotted path and is stripped.
    assert rows(result) == EXPECTED_FINDINGS
    assert result.analyzed == frozenset(FINDINGS_TARGETS)
    assert result.reported_version == PINNED_VERSION

    argv = backend.prepared[-1].command_prefix
    assert argv[argv.index("--network") + 1] == "none"
    assert (f"type=bind,src={os.path.realpath(snapshot)},dst=/kriya/workspace,readonly" in argv
            or f"type=bind,src={snapshot},dst=/kriya/workspace,readonly" in argv)
    assert f"type=bind,src={os.path.realpath(RULES)},dst=/kriya/rules/0,readonly" in argv
    assert argv[argv.index("--user") + 1] == "65534:65534"
    assert argv[argv.index("--pull") + 1] == "never"
    assert "TMPDIR=/kriya/tmp" in argv
    assert argv[-1] == pinned_image

    partial = scan(adapter, snapshot, PARTIAL_TARGETS, tmp_path)
    assert partial.status is ScanStatus.INCOMPLETE
    assert "src/main/java/app/Partial.java" not in partial.analyzed


def test_oci_missing_image_is_unavailable_with_no_pull_and_no_host_fallback(pinned_image, workspace, tmp_path):
    # A digest of the real repository that is not present locally: with
    # --pull never the daemon refuses at once, no registry is contacted.
    absent = "semgrep/semgrep@sha256:" + "0" * 64
    assert subprocess.run(["docker", "image", "inspect", absent], capture_output=True).returncode != 0
    backend = RecordingBackend()
    adapter = contained(workspace, backend, absent)
    probe = adapter.probe()
    assert probe.identity is None and probe.reason_code == PROVIDER_PROBE_FAILED
    assert "No such image" in probe.detail
    argv = backend.prepared[-1].command_prefix
    assert argv[argv.index("--pull") + 1] == "never" and argv[-1] == absent
    result = scan(adapter, build_snapshot(tmp_path / "snap"), FINDINGS_TARGETS, tmp_path)
    assert result.status is not ScanStatus.COMPLETE and result.findings == ()
    assert subprocess.run(["docker", "image", "inspect", absent], capture_output=True).returncode != 0


def test_oci_without_backend_refuses(pinned_semgrep, workspace, tmp_path):
    adapter = contained(workspace, None, IMAGE)
    assert adapter.probe().reason_code == CONTAINMENT_UNAVAILABLE
    result = scan(adapter, build_snapshot(tmp_path / "snap"), FINDINGS_TARGETS, tmp_path)
    assert result.reason_code == CONTAINMENT_UNAVAILABLE and result.raw_output is None


# --- The real service end to end (§16 tests 7, 8, 9, 15, 17, 19, 22, 29, 36) ---
#
# StaticAnalysisService + the registered Semgrep adapter + the real process
# layer (and, contained, the real OCI backend), on a small Java workspace.
# Each case runs in host mode and, when Docker and the pinned image are
# present, in contained mode.

SVC = "src/main/java/app/Svc.java"
EXEC_SOURCE = """package app;

public class Svc {
    public void run(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd);
    }
}
"""
CLEAN_SOURCE = """package app;

public class Svc {
    public void run(String cmd) throws Exception {
        System.out.println(cmd);
    }
}
"""
SHIFTED_EXEC_SOURCE = EXEC_SOURCE.replace(
    "public class Svc {", "".join(f"// padding line {i}\n" for i in range(20)) + "public class Svc {",
)
NOSEM_EXEC_SOURCE = EXEC_SOURCE.replace("exec(cmd);", "exec(cmd); // nosemgrep")


@pytest.fixture(autouse=True)
def _waiver_home_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "static-analysis-home"))


@pytest.fixture(params=["host", "contained"])
def mode(request) -> str:
    request.getfixturevalue("pinned_image" if request.param == "contained" else "pinned_semgrep")
    return request.param


@pytest.fixture
def rule_copy(tmp_path: Path) -> Path:
    """The configured rule pack: a copy outside the workspace, so a test can edit it."""
    pack = tmp_path / "configured-rules"
    shutil.copytree(RULES, pack)
    return pack


@pytest.fixture
def prepared_argv(monkeypatch) -> List[List[str]]:
    """Every docker argv the real OCI backend prepares during the test."""
    recorded: List[List[str]] = []
    original = OCIContainmentBackend.prepare

    def recording(self, profile, command):
        prepared = original(self, profile, command)
        recorded.append(list(prepared.command_prefix or []))
        return prepared

    monkeypatch.setattr(OCIContainmentBackend, "prepare", recording)
    return recorded


@pytest.fixture
def submitted(monkeypatch) -> List[ScanRequest]:
    """Every ScanRequest the service hands the real adapter (then scanned for
    real). A target missing from its snapshot at submission time (the
    snapshot is removed after the gate) is recorded as a failure."""
    requests: List[ScanRequest] = []
    original = SemgrepAdapter.scan

    def recording(self, scan_request):
        missing = [t for t in scan_request.targets if not os.path.isfile(os.path.join(scan_request.root, t))]
        assert not missing, f"target(s) submitted that do not exist in the {scan_request.scan_id} snapshot: {missing}"
        requests.append(scan_request)
        return original(self, scan_request)

    monkeypatch.setattr(SemgrepAdapter, "scan", recording)
    return requests


def service_config(mode: str, rules: Path, **static):
    provider = {"version": PINNED_VERSION, "rule_packs": [str(rules)]}
    autonomy = {"egress_policy": "local_only"}
    if mode == "contained":
        provider["image"] = IMAGE
        autonomy.update({"contained_execution_required": True, "containment_backend": "oci"})
    return strict_config(
        static_analysis={"enabled": True, "provider": "semgrep", "requirement": "required",
                         "providers": {"semgrep": provider}, **static},
        autonomy=autonomy,
    )


def service_workspace(tmp_path: Path, files) -> Path:
    ws = tmp_path / "ws"
    for rel, text in files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return ws


def staged(ws: Path, rel: str, new, *, base=None) -> StagedFileWrite:
    target = str(ws / rel)
    if new is None:
        return StagedFileWrite(target_path=target, content="", base_path=target,
                               expected_base_revision=content_revision(base or ""), delete=True,
                               expected_base_exists=True)
    return StagedFileWrite(target_path=target, content=new, base_path=target,
                           expected_base_revision=content_revision(base or ""),
                           expected_base_exists=base is not None, content_bytes=new.encode())


def evaluate(cfg, ws: Path, writes, tmp_path: Path):
    service = StaticAnalysisService(cfg, evidence_root=str(tmp_path / "ev"))
    return service.evaluate(StaticAnalysisRequest(writes=writes, workspace_path=str(ws), run_id="r", unit_id="u"))


def assert_contained_without_network(mode: str, argv: List[List[str]]) -> None:
    if mode != "contained":
        assert argv == []  # host mode never touches the OCI backend
        return
    assert argv, "contained mode prepared no container"
    for prefix in argv:
        assert prefix[prefix.index("--network") + 1] == "none"
        assert prefix[prefix.index("--pull") + 1] == "never"
        assert prefix[-1] == IMAGE


def test_service_introduced_finding_at_exit_zero_blocks(mode, rule_copy, tmp_path, prepared_argv):
    ws = service_workspace(tmp_path, {SVC: CLEAN_SOURCE})
    result = evaluate(service_config(mode, rule_copy), ws, [staged(ws, SVC, EXEC_SOURCE, base=CLEAN_SOURCE)], tmp_path)
    assert result.outcome is Outcome.BLOCKED, result.evidence
    assert NEW_FINDING_BLOCKED in result.reason_codes and not result.permits_commit
    assert result.evidence["summary"]["introduced"] == 1
    # COMPLETE = exit 0: a finding at exit 0 is never "clean".
    assert result.evidence["scans"]["post"]["status"] == "COMPLETE"
    assert_contained_without_network(mode, prepared_argv)


def test_service_candidate_nosemgrep_still_blocks(mode, rule_copy, tmp_path):
    ws = service_workspace(tmp_path, {SVC: CLEAN_SOURCE})
    result = evaluate(service_config(mode, rule_copy), ws, [staged(ws, SVC, NOSEM_EXEC_SOURCE, base=CLEAN_SOURCE)],
                      tmp_path)
    assert result.outcome is Outcome.BLOCKED and result.evidence["summary"]["introduced"] == 1


def test_service_preexisting_finding_survives_a_line_shift(mode, rule_copy, tmp_path):
    ws = service_workspace(tmp_path, {SVC: EXEC_SOURCE})
    result = evaluate(service_config(mode, rule_copy), ws, [staged(ws, SVC, SHIFTED_EXEC_SOURCE, base=EXEC_SOURCE)],
                      tmp_path)
    # existing.high defaults to warn.
    assert result.outcome is Outcome.PASS_WITH_WARNINGS, result.evidence
    assert result.evidence["summary"]["unchanged"] == 1 and result.evidence["summary"]["introduced"] == 0
    assert result.permits_commit


def test_service_resolved_finding_passes_with_recorded_identity(mode, rule_copy, tmp_path, prepared_argv):
    ws = service_workspace(tmp_path, {SVC: EXEC_SOURCE})
    result = evaluate(service_config(mode, rule_copy), ws, [staged(ws, SVC, CLEAN_SOURCE, base=EXEC_SOURCE)], tmp_path)
    assert result.outcome is Outcome.PASS, result.evidence
    assert result.evidence["summary"]["resolved"] == 1

    provider = result.evidence["provider"]
    assert provider["provider"] == "semgrep" and provider["version"] == PINNED_VERSION
    assert provider["edition"] == "community" and provider["severity_map_version"] == 2
    assert provider["rule_packs"][0]["digest"] == MANIFEST["rule_pack_digests"]["rules"]
    assert provider["effective_options_digest"] and provider["identity_digest"]
    assert result.evidence["runtime_fingerprint"]
    if mode == "contained":
        assert provider["execution_location"] == "container" and provider["network_enforced"] is True
        assert provider["executable_digest"] == IMAGE.rsplit(":", 1)[1]
    else:
        assert provider["execution_location"] == "local_process" and provider["network_enforced"] is False
        assert provider["executable_digest"] == independent_host_digest(shutil.which("semgrep"))
    egress = result.evidence["egress"]
    assert egress["policy"] == "local_only" and egress["admitted"] is True
    assert egress["provider_network_requirement"] == "none"
    assert_contained_without_network(mode, prepared_argv)


def test_service_added_is_post_only_and_deleted_is_pre_only(mode, rule_copy, tmp_path, submitted):
    old, new = "src/main/java/app/Old.java", "src/main/java/app/New.java"
    ws = service_workspace(tmp_path, {SVC: CLEAN_SOURCE, old: EXEC_SOURCE.replace("Svc", "Old")})
    writes = [staged(ws, new, CLEAN_SOURCE.replace("Svc", "New")),
              staged(ws, old, None, base=EXEC_SOURCE.replace("Svc", "Old"))]
    result = evaluate(service_config(mode, rule_copy), ws, writes, tmp_path)
    assert result.outcome is Outcome.PASS, result.evidence
    assert result.evidence["summary"]["resolved"] == 1
    by_side = {Path(r.root).name: set(r.targets) for r in submitted}
    assert by_side == {"post": {new}, "pre": {old}}
    # Every submitted target existed (checked at submission): no V15 whole-scan abort.
    assert result.evidence["scans"]["pre"]["status"] == result.evidence["scans"]["post"]["status"] == "COMPLETE"


def test_service_oversized_target_is_never_submitted_and_unknown(mode, rule_copy, tmp_path, submitted):
    big = "src/main/java/app/Big.java"
    big_source = EXEC_SOURCE.replace("Svc", "Big") + "// " + "x" * 3000 + "\n"
    ws = service_workspace(tmp_path, {SVC: CLEAN_SOURCE})
    writes = [staged(ws, big, big_source), staged(ws, SVC, CLEAN_SOURCE + "// touched\n", base=CLEAN_SOURCE)]
    result = evaluate(service_config(mode, rule_copy, max_target_bytes=2000), ws, writes, tmp_path)
    assert result.outcome is Outcome.UNKNOWN and TARGET_OVERSIZED in result.reason_codes
    assert result.evidence["scope"]["oversized"] == [
        {"path": big, "side": "post", "size": len(big_source.encode()), "limit": 2000,
         "reason": "exceeds static_analysis.max_target_bytes"},
    ]
    assert submitted and all(big not in r.targets for r in submitted)
    assert all(r.max_target_bytes == 2000 for r in submitted)


def test_service_stale_evidence_blocks_the_commit(mode, rule_copy, tmp_path):
    ws = service_workspace(tmp_path, {SVC: EXEC_SOURCE})
    cfg = service_config(mode, rule_copy)
    writes = [staged(ws, SVC, CLEAN_SOURCE, base=EXEC_SOURCE)]
    result = evaluate(cfg, ws, writes, tmp_path)
    assert result.outcome is Outcome.PASS and result.permits_commit, result.evidence

    def commit(batch):
        return commit_terminal_candidate(batch, workspace_path=str(ws), verified_candidate=bind_candidate(batch, str(ws)), transaction_id="t",
                                         static_analysis=commit_guard(cfg, result))

    rule = rule_copy / "java.yml"
    original = rule.read_bytes()
    rule.write_bytes(original.replace(b"Weak hash", b"Weak hasH"))  # one rule byte
    refused = commit(writes)
    assert (refused.committed, refused.workspace_state) == (False, "UNCHANGED")
    assert refused.reason_code == STATIC_ANALYSIS_EVIDENCE_STALE and "rule packs" in str(refused.error)
    assert (ws / SVC).read_text() == EXEC_SOURCE
    rule.write_bytes(original)

    changed = [staged(ws, SVC, CLEAN_SOURCE.replace("println", "print"), base=EXEC_SOURCE)]  # candidate bytes
    refused = commit(changed)
    assert (refused.committed, refused.reason_code) == (False, STATIC_ANALYSIS_EVIDENCE_STALE)
    assert "candidate batch" in str(refused.error)
    assert (ws / SVC).read_text() == EXEC_SOURCE

    # Control: the unchanged evidence and batch do commit.
    committed = commit(writes)
    assert committed.committed and (ws / SVC).read_text() == CLEAN_SOURCE
