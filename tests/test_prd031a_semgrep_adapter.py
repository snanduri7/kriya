"""PRD-031A: the Semgrep adapter, deterministic tier (§5.2, §5.3, §5.5, §8.4, §8.5, §16 tests 14/15/19/20/22/36).

No real scanner runs here. The parser is driven by real Semgrep 1.178.0 JSON
recorded under tests/fixtures/static_analysis/semgrep/1.178.0, returned by a
fake ProcessController at the adapter's one execution boundary, which also
records the exact argv, environment and containment profile the adapter
would hand to the process layer.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

import pytest
from pydantic import ValidationError

from kriya.static_analysis import registry
from kriya.static_analysis.adapters import semgrep
from kriya.static_analysis.adapters.semgrep import (
    SemgrepAdapter,
    SemgrepSettings,
    interpret_output,
    map_severity,
    normalize_rule_id,
)
from kriya.static_analysis.model import (
    CAPABILITY_UNKNOWN,
    CONTAINMENT_UNAVAILABLE,
    PROVIDER_PROBE_FAILED,
    PROVIDER_VERSION_MISMATCH,
    RULE_PACK_DIGEST_MISMATCH,
    RULE_PACK_INVALID,
    RULE_PACK_UNPINNED,
    SCAN_FAILED,
    SCAN_INCOMPLETE,
    SCAN_OUTPUT_MALFORMED,
    SCAN_TIMEOUT,
    ScanStatus,
    Severity,
)
from kriya.static_analysis.port import ExecutionContext, ScanRequest
from kriya.tools.containment import (
    BackendUnavailableError,
    DummyContainmentBackend,
    MountSpec,
    NetworkAuthority,
    NullContainmentBackend,
    TrustClass,
)
from kriya.tools.process import ProcessResult

FIXTURES = Path(__file__).parent / "fixtures" / "static_analysis" / "semgrep" / "1.178.0"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
RULES = str(FIXTURES / "rules")
BAD = FIXTURES / "bad"
IMAGE = "semgrep/semgrep@sha256:32e459968daabe7ab86968184a29109b9564aa00392401156f9788452b42786b"
IMAGE_HEX = IMAGE.rsplit(":", 1)[1]
FIXED_FLAGS = [
    "scan", "--metrics=off", "--disable-version-check", "--disable-nosem", "--no-git-ignore", "--oss-only",
    "--json", "--verbose", "--timeout", "5", "--timeout-threshold", "3", "--max-target-bytes", "1000000",
]
CE_LANGUAGES = {"java", "python", "javascript", "typescript", "go", "ruby", "kotlin", "c", "cpp", "rust", "php",
                "swift", "scala"}


def output(case: str) -> str:
    return (FIXTURES / "outputs" / f"{case}.json").read_text()


def case_targets(case: str) -> List[str]:
    return list(MANIFEST["cases"][case]["targets"])


class FakeProcess:
    """ProcessController double: records every call, returns scripted results."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: List[tuple] = []

    def run(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def ok(stdout: str, returncode: int = 0) -> ProcessResult:
    return ProcessResult(returncode=returncode, stdout=stdout, stderr="", timeout=False)


VERSION_OK = ok("1.178.0\n")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    for rel, text in MANIFEST["sources"].items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def settings(**overrides) -> SemgrepSettings:
    data = {"version": "1.178.0", "rule_packs": [RULES]}
    data.update(overrides)
    return SemgrepSettings.model_validate(data)


def make_adapter(workspace: Path, process: FakeProcess, **overrides) -> SemgrepAdapter:
    """S8: the only execution mode is the pinned image through an isolating backend; generic adapter behaviour (rule packs, output
    interpretation, timeouts) is exercised through it with a scripted process controller."""
    return contained_adapter(workspace, process, backend=DummyContainmentBackend(), **overrides)


def contained_adapter(workspace: Path, process: FakeProcess, backend=None, image: Optional[str] = IMAGE,
                      **overrides) -> SemgrepAdapter:
    ctx = ExecutionContext(containment_backend=backend, workspace_root=str(workspace))
    return SemgrepAdapter(settings(image=image, **overrides), ctx, process_controller=process)


def request(root: Path, tmp_path: Path, targets: List[str], timeout: int = 120) -> ScanRequest:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    return ScanRequest(root=str(root), targets=tuple(targets), timeout_seconds=timeout, scratch_dir=str(scratch),
                       scan_id="scan-1", max_target_bytes=1_000_000)


# --- settings (§5.2 "rejected at config validation") ------------------------

@pytest.mark.parametrize("version", ["latest", "1.178", ">=1.178.0", "^1.178.0", "1.178.*", "1.178.0-rc1", ""])
def test_settings_reject_inexact_version(version):
    with pytest.raises(ValidationError, match="exact"):
        settings(version=version)


@pytest.mark.parametrize("image", [
    "semgrep/semgrep", "semgrep/semgrep:latest", "semgrep/semgrep:1.178.0",
    "semgrep/semgrep@sha256:abc", f"semgrep/semgrep@sha256:{IMAGE_HEX.upper()}",
])
def test_settings_reject_unpinned_image(image):
    with pytest.raises(ValidationError, match="pinned by digest"):
        settings(image=image)


def test_settings_accept_pinned_image_and_defaults():
    parsed = settings(image=IMAGE)
    assert parsed.image == IMAGE
    assert parsed.executable == "semgrep"
    assert parsed.min_language_maturity == "ga"
    assert (parsed.per_file_timeout_seconds, parsed.timeout_threshold) == (5, 3)


@pytest.mark.parametrize("pack", ["p/java", "r/java.lang.security", "s/someone", "auto",
                                  "https://semgrep.dev/c/p/java", "file:///etc/rules.yml"])
def test_settings_reject_registry_refs_and_urls(pack):
    with pytest.raises(ValidationError, match="registry reference or URL"):
        settings(rule_packs=[pack])
    with pytest.raises(ValidationError, match="registry reference or URL"):
        settings(rule_packs=[{"path": pack}])


def test_settings_reject_nonexistent_empty_or_missing_packs(tmp_path):
    with pytest.raises(ValidationError, match="does not exist"):
        settings(rule_packs=[str(tmp_path / "nope.yml")])
    with pytest.raises(ValidationError, match="does not exist"):
        settings(rule_packs=[{"path": str(tmp_path / "nope")}])
    with pytest.raises(ValidationError):
        settings(rule_packs=[])
    with pytest.raises(ValidationError):
        SemgrepSettings.model_validate({"version": "1.178.0"})


@pytest.mark.parametrize("bad", [{"sha256": "abc"}, {"sha256": "A" * 64}, {"extra": 1}])
def test_settings_reject_bad_pack_pin_and_unknown_keys(bad):
    with pytest.raises(ValidationError):
        settings(rule_packs=[{"path": RULES, **bad}])


@pytest.mark.parametrize("override", [{"per_file_timeout_seconds": 0}, {"timeout_threshold": -1},
                                      {"min_language_maturity": "alpha"}, {"unknown_field": True}])
def test_settings_reject_invalid_fields(override):
    with pytest.raises(ValidationError):
        settings(**override)


def test_registered_under_its_name_with_its_settings_model(workspace):
    registration = registry.registration("semgrep")
    assert registration.settings_model is SemgrepSettings
    normalized = registry.validate_provider_settings("semgrep", {"version": "1.178.0", "rule_packs": [RULES]})
    ctx = ExecutionContext(containment_backend=None, workspace_root=str(workspace))
    created = registry.create_provider("semgrep", normalized, ctx)
    assert isinstance(created, SemgrepAdapter) and created.name == "semgrep"
    with pytest.raises(ValidationError):
        registry.validate_provider_settings("semgrep", {"version": "latest", "rule_packs": [RULES]})


# --- probe: host -------------------------------------------------------------



def test_probe_is_cached_per_instance(workspace):
    process = FakeProcess(VERSION_OK)
    adapter = make_adapter(workspace, process)
    assert adapter.probe() is adapter.probe()
    assert len(process.calls) == 1


@pytest.mark.parametrize("result", [
    ProcessResult(returncode=2, stdout="", stderr="boom", timeout=False),
    ProcessResult(returncode=-1, stdout="", stderr="", timeout=True),
])
def test_probe_failed_version_command(workspace, result):
    probe = make_adapter(workspace, FakeProcess(result)).probe()
    assert (probe.identity, probe.reason_code) == (None, PROVIDER_PROBE_FAILED)


def test_version_mismatch_is_unavailable_and_names_both(workspace):
    probe = make_adapter(workspace, FakeProcess(ok("1.177.0\n"))).probe()
    assert probe.reason_code == PROVIDER_VERSION_MISMATCH
    assert "1.178.0" in probe.detail and "1.177.0" in probe.detail


def test_version_is_the_last_nonempty_line(workspace):
    probe = make_adapter(workspace, FakeProcess(ok("A new version is available\n\n1.178.0\n\n"))).probe()
    assert probe.identity.version == "1.178.0"


def test_unknown_version_is_capability_unknown(workspace):
    probe = make_adapter(workspace, FakeProcess(ok("1.179.0\n")), version="1.179.0").probe()
    assert (probe.identity, probe.capability, probe.reason_code) == (None, None, CAPABILITY_UNKNOWN)


# --- probe: rule packs -------------------------------------------------------

def write_rule(path: Path, rule_id: str, languages=("java",)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    langs = ", ".join(languages)
    path.write_text(f"rules:\n  - id: {rule_id}\n    languages: [{langs}]\n    severity: ERROR\n"
                    f"    message: m\n    pattern: foo()\n")


def probe_packs(workspace, packs):
    return make_adapter(workspace, FakeProcess(VERSION_OK), rule_packs=packs).probe()


def test_duplicate_rule_id_across_packs_is_invalid(workspace, tmp_path):
    write_rule(tmp_path / "other" / "dup.yml", "java-runtime-exec")
    probe = probe_packs(workspace, [RULES, str(tmp_path / "other")])
    assert probe.reason_code == RULE_PACK_INVALID and "java-runtime-exec" in probe.detail


@pytest.mark.parametrize("content", ["rules: [\n", "not_rules: []\n", "- a\n- b\n", "rules:\n  - languages: [java]\n"])
def test_unparseable_or_ruleless_pack_is_invalid(workspace, tmp_path, content):
    pack = tmp_path / "pack.yml"
    pack.write_text(content)
    assert probe_packs(workspace, [str(pack)]).reason_code == RULE_PACK_INVALID


def test_recorded_invalid_yaml_pack_is_invalid_at_probe(workspace):
    probe = probe_packs(workspace, [str(BAD / "invalid_yaml.yml")])
    assert probe.reason_code == RULE_PACK_INVALID


def test_directory_without_rule_files_is_invalid(workspace, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "notes.txt").write_text("x")
    assert probe_packs(workspace, [str(tmp_path / "empty")]).reason_code == RULE_PACK_INVALID


def test_directory_symlink_inside_pack_is_invalid(workspace, tmp_path):
    write_rule(tmp_path / "pack" / "a.yml", "a")
    write_rule(tmp_path / "elsewhere" / "b.yml", "b")
    os.symlink(tmp_path / "elsewhere", tmp_path / "pack" / "linked")
    probe = probe_packs(workspace, [str(tmp_path / "pack")])
    assert probe.reason_code == RULE_PACK_INVALID and "symlink" in probe.detail


def test_directory_pack_enumerates_like_semgrep(workspace, tmp_path):
    # Observed with 1.178.0: hidden dirs and dotfiles load; *.test.yml does not.
    pack = tmp_path / "pack"
    write_rule(pack / "a.yml", "r-a")
    write_rule(pack / "sub" / "b.yaml", "r-b", ("python",))
    write_rule(pack / ".hidden" / "c.yml", "r-c")
    write_rule(pack / "e.test.yml", "r-a")  # a test fixture: never a (duplicate) rule
    (pack / "README.md").write_text("docs")
    probe = probe_packs(workspace, [str(pack)])
    assert probe.reason_code is None, probe.detail
    (identity,) = probe.identity.rule_packs
    assert identity.rule_count == 3 and identity.languages == ("java", "python")
    before = identity.digest
    (pack / "e.test.yml").write_text("changed")  # still covered by the digest
    assert probe_packs(workspace, [str(pack)]).identity.rule_packs[0].digest != before


def test_in_workspace_pack_requires_a_pin(workspace):
    write_rule(workspace / "rules" / "a.yml", "ws-rule")
    probe = probe_packs(workspace, [str(workspace / "rules")])
    assert probe.reason_code == RULE_PACK_UNPINNED


def test_in_workspace_pinned_pack_is_accepted_and_mismatch_refused(workspace):
    write_rule(workspace / "rules" / "a.yml", "ws-rule")
    digest = semgrep._pack_digest(semgrep._read_pack(semgrep._PackSpec("r", str(workspace / "rules"), None)))
    good = probe_packs(workspace, [{"path": str(workspace / "rules"), "sha256": digest}])
    assert good.reason_code is None and good.identity.rule_packs[0].digest == digest
    bad = probe_packs(workspace, [{"path": str(workspace / "rules"), "sha256": "0" * 64}])
    assert bad.reason_code == RULE_PACK_DIGEST_MISMATCH


def test_pinned_pack_outside_workspace_is_verified_too(workspace):
    probe = probe_packs(workspace, [{"path": RULES, "sha256": "f" * 64}])
    assert probe.reason_code == RULE_PACK_DIGEST_MISMATCH
    ok_probe = probe_packs(workspace, [{"path": RULES, "sha256": MANIFEST["rule_pack_digests"]["rules"]}])
    assert ok_probe.reason_code is None


def test_identity_changes_when_one_rule_byte_changes(workspace, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(RULES, pack)
    first = probe_packs(workspace, [str(pack)]).identity
    text = (pack / "java.yml").read_text()
    (pack / "java.yml").write_text(text.replace("Weak hash", "Weak  hash"))
    second = probe_packs(workspace, [str(pack)]).identity
    assert first.rule_packs[0].digest != second.rule_packs[0].digest
    assert first.identity_digest != second.identity_digest


# --- probe: contained (§8.5: no host fallback) -------------------------------

@pytest.mark.parametrize("backend,image", [
    (None, IMAGE), (NullContainmentBackend(), IMAGE), (DummyContainmentBackend(), None),
])
def test_contained_probe_refuses_without_backend_or_image(workspace, backend, image):
    process = FakeProcess()
    probe = contained_adapter(workspace, process, backend=backend, image=image).probe()
    assert (probe.identity, probe.reason_code) == (None, CONTAINMENT_UNAVAILABLE)
    assert process.calls == []


def test_contained_probe_setup_failure_is_unavailable(workspace):
    process = FakeProcess(BackendUnavailableError("docker daemon is not reachable"))
    probe = contained_adapter(workspace, process, backend=DummyContainmentBackend()).probe()
    assert probe.reason_code == CONTAINMENT_UNAVAILABLE and "docker daemon" in probe.detail


def test_contained_probe_identity_and_profile(workspace):
    backend = DummyContainmentBackend()
    process = FakeProcess(VERSION_OK)
    probe = contained_adapter(workspace, process, backend=backend).probe()
    assert probe.reason_code is None, probe.detail
    assert probe.identity.executable_digest == IMAGE_HEX
    assert probe.identity.execution_location == "container" and probe.identity.network_enforced is True
    assert not any("Host mode" in text for text in probe.capability.limitations)
    ((command, kwargs),) = process.calls
    assert command == ["semgrep", "--version"]
    assert kwargs["containment_backend"] is backend and "env" not in kwargs
    profile = kwargs["containment_profile"]
    # An empty adapter-owned scratch dir, read-only: never the real workspace.
    assert profile.mount_workspace is True and profile.workspace_write is False
    assert profile.workspace_path != str(workspace) and profile.additional_mounts == ()
    assert profile.network is NetworkAuthority.DENIED and profile.image_reference == IMAGE
    assert (profile.run_as_uid, profile.run_as_gid) == (65534, 65534)


# --- scan: invocation (§5.2 flags and environment, §8.4) ----------------------



def test_contained_scan_profile_and_argv(workspace, snapshot, tmp_path):
    backend = DummyContainmentBackend()
    file_pack = str(FIXTURES / "bad" / "invalid_schema.yml")
    process = FakeProcess(ok(output("findings_exit0")))
    targets = case_targets("findings_exit0")
    adapter = contained_adapter(workspace, process, backend=backend, rule_packs=[RULES, file_pack])
    adapter.scan(request(snapshot, tmp_path, targets, timeout=33))

    ((command, kwargs),) = process.calls
    assert command == ["semgrep", *FIXED_FLAGS, "--config", "/kriya/rules/0",
                       "--config", "/kriya/rules/1/invalid_schema.yml", "--", *targets]
    assert set(kwargs) == {"cwd", "timeout", "containment_profile", "containment_backend"}
    assert kwargs["cwd"] == str(snapshot) and kwargs["timeout"] == 33 and kwargs["containment_backend"] is backend
    profile = kwargs["containment_profile"]
    assert profile.trust_class is TrustClass.UNTRUSTED_EXECUTION
    assert profile.workspace_path == str(snapshot)
    assert profile.mount_workspace is True and profile.workspace_write is False
    assert profile.network is NetworkAuthority.DENIED
    assert profile.additional_mounts == (
        MountSpec(host_path=os.path.realpath(RULES), container_path="/kriya/rules/0", writable=False),
        MountSpec(host_path=os.path.realpath(file_pack), container_path="/kriya/rules/1/invalid_schema.yml",
                  writable=False),
    )
    assert profile.resolved_env == {
        "HOME": "/kriya/tmp/home", "TMPDIR": "/kriya/tmp", "SEMGREP_SEND_METRICS": "off",
        "SEMGREP_ENABLE_VERSION_CHECK": "0", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    assert profile.env_allowlist == []
    assert (profile.run_as_uid, profile.run_as_gid) == (65534, 65534)
    assert profile.image_reference == IMAGE and profile.toolchain_identity is None


@pytest.mark.parametrize("backend,image", [
    (None, IMAGE), (NullContainmentBackend(), IMAGE), (DummyContainmentBackend(), None),
])
def test_contained_scan_never_falls_back_to_the_host(workspace, snapshot, tmp_path, backend, image):
    process = FakeProcess()
    result = contained_adapter(workspace, process, backend=backend, image=image).scan(
        request(snapshot, tmp_path, case_targets("findings_exit0")))
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, CONTAINMENT_UNAVAILABLE)
    assert process.calls == []


def test_contained_scan_setup_failure_is_unavailable(workspace, snapshot, tmp_path):
    process = FakeProcess(BackendUnavailableError("image not pinned"))
    result = contained_adapter(workspace, process, backend=DummyContainmentBackend()).scan(
        request(snapshot, tmp_path, case_targets("findings_exit0")))
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, CONTAINMENT_UNAVAILABLE)


def test_missing_target_is_failed_without_running(workspace, snapshot, tmp_path):
    process = FakeProcess()
    result = make_adapter(workspace, process).scan(
        request(snapshot, tmp_path, case_targets("missing_target")))
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, SCAN_FAILED)
    assert process.calls == []


@pytest.mark.parametrize("target", ["../outside.java", "/etc/passwd", "src"])
def test_escaping_or_non_file_target_is_failed_without_running(workspace, snapshot, tmp_path, target):
    (snapshot.parent / "outside.java").write_text("class A {}")
    process = FakeProcess()
    result = make_adapter(workspace, process).scan(request(snapshot, tmp_path, [target]))
    assert result.status is ScanStatus.FAILED and process.calls == []


def test_invalid_pack_at_scan_time_is_config_error_without_running(workspace, snapshot, tmp_path):
    process = FakeProcess()
    result = make_adapter(workspace, process, rule_packs=[str(BAD / "invalid_yaml.yml")]).scan(
        request(snapshot, tmp_path, case_targets("invalid_yaml")))
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, RULE_PACK_INVALID)
    assert process.calls == []


def test_spawn_failure_is_scan_failed(workspace, snapshot, tmp_path):
    result = make_adapter(workspace, FakeProcess(FileNotFoundError("gone"))).scan(
        request(snapshot, tmp_path, case_targets("findings_exit0")))
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, SCAN_FAILED) and "gone" in result.detail


# --- scan: result mapping from recorded 1.178.0 output (§5.3, §5.5) ------------

def scan_case(workspace, snapshot, tmp_path, case, *, returncode=None, stdout=None, packs=None,
              targets=None):
    packs = packs or [str(FIXTURES / p) for p in MANIFEST["cases"][case]["rule_packs"]]
    code = MANIFEST["cases"][case]["exit_code"] if returncode is None else returncode
    process = FakeProcess(ok(output(case) if stdout is None else stdout, code))
    adapter = make_adapter(workspace, process, rule_packs=packs)
    return adapter.scan(request(snapshot, tmp_path, targets or case_targets(case)))


def test_findings_at_exit_zero_complete(workspace, snapshot, tmp_path):
    stdout = output("findings_exit0")
    result = scan_case(workspace, snapshot, tmp_path, "findings_exit0")
    assert (result.status, result.reason_code) == (ScanStatus.COMPLETE, None)
    assert result.analyzed == frozenset(case_targets("findings_exit0"))
    assert result.reported_version == "1.178.0"
    assert result.raw_output == stdout and result.raw_sha256 == hashlib.sha256(stdout.encode()).hexdigest()
    rows = [(f.rule_id, f.path, f.start_line, f.severity, f.raw_index) for f in result.findings]
    assert rows == [
        ("semgrep:java-runtime-exec", "src/main/java/app/App.java", 7, Severity.HIGH, 0),
        ("semgrep:java-weak-hash", "src/main/java/app/App.java", 8, Severity.CRITICAL, 1),
        # The nosemgrep line: reported because --disable-nosem is passed (V4, §8.4.1).
        ("semgrep:java-runtime-exec", "src/main/java/app/App.java", 12, Severity.HIGH, 2),
        ("semgrep:java-runtime-exec", "src/test/java/app/AppTest.java", 5, Severity.HIGH, 3),
        ("semgrep:python-print-info", "tests/test_x.py", 2, Severity.LOW, 4),
        ("semgrep:python-eval", "tests/test_x.py", 2, Severity.MEDIUM, 5),
    ]
    first, weak = result.findings[0], result.findings[1]
    assert first.cwe == ("CWE-78: OS Command Injection",) and first.owasp == ("A03:2021 - Injection",)
    assert weak.cwe == ("CWE-327: Use of a Broken or Risky Cryptographic Algorithm",)
    assert first.provider == "semgrep" and first.message == "Runtime.exec with a dynamic command"
    assert (first.start_col, first.end_col) == (9, 39)


def test_findings_are_kept_whatever_the_exit_code(workspace, snapshot, tmp_path):
    result = scan_case(workspace, snapshot, tmp_path, "findings_exit0", returncode=2)
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, SCAN_FAILED)
    assert len(result.findings) == 6


def test_unconfirmed_target_is_incomplete(workspace, snapshot, tmp_path):
    # An existing target Semgrep silently did not report as scanned (V13).
    (snapshot / "src" / "Extra.java").write_text("class Extra {}")
    targets = case_targets("findings_exit0") + ["src/Extra.java"]
    result = scan_case(workspace, snapshot, tmp_path, "findings_exit0", targets=targets)
    assert (result.status, result.reason_code) == (ScanStatus.INCOMPLETE, SCAN_INCOMPLETE)
    assert "src/Extra.java" not in result.analyzed and "src/Extra.java" in result.detail


def test_partial_parse_is_incomplete_with_findings(workspace, snapshot, tmp_path):
    result = scan_case(workspace, snapshot, tmp_path, "partial_parse")
    assert (result.status, result.reason_code) == (ScanStatus.INCOMPLETE, SCAN_INCOMPLETE)
    # scanned - skipped - errored (V10): Partial.java is in all three; Broken.java has a
    # scalar "Syntax error"; syntax_err.py was silently recovered (V11) and counts.
    assert result.analyzed == frozenset({"tests/syntax_err.py", "tests/test_x.py"})
    assert result.skipped == {
        "src/main/java/app/Partial.java": "analysis_failed_parser_or_internal_error",
        "src/main/java/app/Broken.java": "analysis_failed_parser_or_internal_error",
    }
    assert {(e.kind, e.path, e.level) for e in result.errors} == {
        ("target", "src/main/java/app/Partial.java", "warn"), ("target", "src/main/java/app/Broken.java", "warn"),
    }
    assert ("semgrep:java-runtime-exec", "src/main/java/app/Partial.java", 5) in {
        (f.rule_id, f.path, f.start_line) for f in result.findings}


def test_invalid_pattern_exit_two_is_config_error_with_evidence(workspace, snapshot, tmp_path):
    result = scan_case(workspace, snapshot, tmp_path, "invalid_pattern")
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, RULE_PACK_INVALID)
    assert [f.rule_id for f in result.findings] == ["semgrep:good-rule"]
    assert [e.kind for e in result.errors] == ["config"]


@pytest.mark.parametrize("case,reason", [
    ("invalid_schema", RULE_PACK_INVALID), ("unknown_language", CAPABILITY_UNKNOWN),
])
def test_rule_errors_are_config_errors(workspace, snapshot, tmp_path, case, reason):
    result = scan_case(workspace, snapshot, tmp_path, case)
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, reason)
    assert result.findings == () and all(e.kind == "config" for e in result.errors)


@pytest.mark.parametrize("case,reason", [
    ("invalid_yaml", RULE_PACK_INVALID), ("invalid_schema", RULE_PACK_INVALID),
    ("unknown_language", CAPABILITY_UNKNOWN), ("invalid_pattern", RULE_PACK_INVALID),
])
def test_config_error_kind_wins_over_exit_code(case, reason):
    # The same errors[] at exit 0 are still a config error (§5.3 "kind wins").
    for code in (0, MANIFEST["cases"][case]["exit_code"]):
        result = interpret_output(output(case), code, ["src/main/java/app/App.java"], frozenset(), "/snap")
        assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, reason)


def test_recorded_missing_target_output_is_scan_failed():
    result = interpret_output(output("missing_target"), 2, case_targets("missing_target"), frozenset(), "/snap")
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, SCAN_FAILED)
    assert [e.kind for e in result.errors] == ["other"] and result.analyzed == frozenset()


def document(errors, scanned=("a.java",), skipped=(), results=()):
    return json.dumps({"version": "1.178.0", "results": list(results), "errors": errors,
                       "paths": {"scanned": list(scanned), "skipped": list(skipped)}})


@pytest.mark.parametrize("error_type", ["Syntax error", ["PartialParsing", [{"path": "a.java"}]], "Timeout"])
def test_target_error_scalar_and_list_types(error_type):
    raw = {"code": 3, "level": "warn", "type": error_type, "message": "m"}
    if not isinstance(error_type, list):
        raw["path"] = "a.java"
    result = interpret_output(document([raw]), 0, ["a.java"], frozenset(), "/snap")
    assert result.status is ScanStatus.INCOMPLETE and result.analyzed == frozenset()
    assert [(e.kind, e.path) for e in result.errors] == [("target", "a.java")]


def test_list_type_error_located_only_by_its_type_spans():
    raw = {"code": 3, "level": "warn", "type": ["PartialParsing", [{"path": "a.java"}]], "message": "m"}
    result = interpret_output(document([raw], scanned=("a.java", "b.java")), 0, ["a.java", "b.java"],
                              frozenset(), "/snap")
    assert result.analyzed == frozenset({"b.java"}) and result.errors[0].path == "a.java"


@pytest.mark.parametrize("raw", [
    {"code": 2, "level": "error", "type": "SemgrepError", "message": "Invalid scanning root: x"},
    {"code": 3, "level": "warn", "type": "Syntax error", "path": "/tmp/not-a-target.json", "message": "m"},
    {"code": 9, "level": "error", "type": ["SomethingNew", []], "message": "m"},
    {"code": 9, "level": "error", "message": "no type at all"},
    {"code": [5], "level": "error", "type": "SemgrepError", "message": "unhashable code"},
])
def test_unrecognized_or_non_target_errors_are_scan_failed(raw):
    result = interpret_output(document([raw]), 0, ["a.java"], frozenset(), "/snap")
    assert (result.status, result.reason_code) == (ScanStatus.FAILED, SCAN_FAILED)
    assert [e.kind for e in result.errors] == ["other"]


@pytest.mark.parametrize("code", [4, 5, 7, 8])
def test_semgrep_error_config_codes(code):
    raw = {"code": code, "level": "error", "type": "SemgrepError", "message": "invalid configuration file found"}
    result = interpret_output(document([raw]), 0, ["a.java"], frozenset(), "/snap")
    assert (result.status, result.reason_code) == (ScanStatus.CONFIG_ERROR, RULE_PACK_INVALID)


def test_mixed_config_errors_are_rule_pack_invalid():
    errors = [{"code": 8, "level": "error", "type": "UnknownLanguageError", "message": "m"},
              {"code": 4, "level": "error", "type": "InvalidRuleSchemaError", "message": "m"}]
    result = interpret_output(document(errors), 8, ["a.java"], frozenset(), "/snap")
    assert result.reason_code == RULE_PACK_INVALID


def test_clean_exit_zero_with_no_findings_is_complete_only_when_confirmed():
    result = interpret_output(document([]), 0, ["a.java"], frozenset(), "/snap")
    assert (result.status, result.findings, result.analyzed) == (ScanStatus.COMPLETE, (), frozenset({"a.java"}))
    skipped = interpret_output(document([], skipped=[{"path": "a.java", "reason": "too_big"}]), 0, ["a.java"],
                               frozenset(), "/snap")
    assert skipped.status is ScanStatus.INCOMPLETE and skipped.skipped == {"a.java": "too_big"}


def test_timeout(workspace, snapshot, tmp_path):
    process = FakeProcess(ProcessResult(returncode=-1, stdout='{"partial', stderr="", timeout=True))
    result = make_adapter(workspace, process).scan(
        request(snapshot, tmp_path, case_targets("findings_exit0"), timeout=9))
    assert (result.status, result.reason_code) == (ScanStatus.TIMEOUT, SCAN_TIMEOUT)
    assert result.findings == () and result.raw_output == '{"partial'


def test_truncated_capture_is_malformed(workspace, snapshot, tmp_path):
    process = FakeProcess(ProcessResult(returncode=0, stdout=output("findings_exit0"), stderr="", timeout=False,
                                        stdout_truncated=True))
    result = make_adapter(workspace, process).scan(
        request(snapshot, tmp_path, case_targets("findings_exit0")))
    assert (result.status, result.reason_code) == (ScanStatus.MALFORMED_OUTPUT, SCAN_OUTPUT_MALFORMED)


def _without(key):
    data = json.loads(output("findings_exit0"))
    del data[key]
    return json.dumps(data)


def _without_skipped():
    data = json.loads(output("findings_exit0"))
    del data["paths"]["skipped"]
    return json.dumps(data)


def _bad_result():
    data = json.loads(output("findings_exit0"))
    del data["results"][0]["start"]
    return json.dumps(data)


@pytest.mark.parametrize("stdout", [
    "", "not json", "[]", '{"results": []}', _without("results"), _without("paths"), _without("errors"),
    _without_skipped(), _bad_result(), document(["not-an-object"]),
])
def test_malformed_output(stdout):
    result = interpret_output(stdout, 0, ["a.java"], frozenset(), "/snap")
    assert (result.status, result.reason_code) == (ScanStatus.MALFORMED_OUTPUT, SCAN_OUTPUT_MALFORMED)
    assert result.findings == () and result.raw_output == stdout


# --- normalization -----------------------------------------------------------

@pytest.mark.parametrize("severity,metadata,expected", [
    ("INFO", {"security-severity": "Critical"}, Severity.CRITICAL),
    ("ERROR", {"security-severity": "critical", "impact": "LOW"}, Severity.CRITICAL),
    ("INFO", {"impact": "HIGH"}, Severity.HIGH),
    ("ERROR", {"impact": "MEDIUM"}, Severity.MEDIUM),
    ("ERROR", {"impact": "LOW"}, Severity.LOW),
    ("WARNING", {"security-severity": "High"}, Severity.MEDIUM),
    ("ERROR", {}, Severity.HIGH),
    ("WARNING", {}, Severity.MEDIUM),
    ("INFO", {}, Severity.LOW),
    # SEVERITY_MAP_VERSION 2: the newer values the pinned scanner reports verbatim
    # (MEASURED 2026-10-08, evidence: backend-readiness-004/defects/semgrep-severity-v2/).
    ("CRITICAL", {}, Severity.CRITICAL),
    ("HIGH", {}, Severity.HIGH),
    ("MEDIUM", {}, Severity.MEDIUM),
    ("LOW", {}, Severity.LOW),
    ("HIGH", {"impact": "LOW"}, Severity.LOW),  # metadata impact still outranks the rule severity
    ("INVENTORY", {}, Severity.UNKNOWN),
    ("error", {}, Severity.UNKNOWN),
    (None, {"impact": "SEVERE"}, Severity.UNKNOWN),
])
def test_severity_map(severity, metadata, expected):
    assert map_severity(severity, metadata) is expected


def test_rule_id_prefix_stripping():
    ids = frozenset({"eval", "python.lang.eval", "java-weak-hash"})
    assert normalize_rule_id("__SCRATCH__.rules.java-weak-hash", ids) == "semgrep:java-weak-hash"
    assert normalize_rule_id("kriya.rules.0.java-weak-hash", ids) == "semgrep:java-weak-hash"
    assert normalize_rule_id("x.python.lang.eval", ids) == "semgrep:python.lang.eval"  # longest match
    assert normalize_rule_id("x.other.eval", ids) == "semgrep:eval"
    assert normalize_rule_id("eval", ids) == "semgrep:eval"
    assert normalize_rule_id("x.noteval", ids) == "semgrep:x.noteval"  # unmatched: kept whole


def test_message_is_control_stripped_and_bounded():
    result = {"check_id": "r", "path": "a.java", "start": {"line": 1, "col": 1}, "end": {"line": 1, "col": 2},
              "extra": {"severity": "ERROR", "message": "a\x1b[31mb\x00c\n" + "x" * 5000}}
    finding = interpret_output(document([], results=[result]), 0, ["a.java"], frozenset({"r"}), "/snap").findings[0]
    assert "\x1b" not in finding.message and "\x00" not in finding.message and "\n" not in finding.message
    assert len(finding.message) == 2048 and finding.message.startswith("a [31mb c")


def test_absolute_finding_path_is_made_root_relative():
    result = {"check_id": "r", "path": "/snap/src/a.java", "start": {"line": 1}, "end": {"line": 2},
              "extra": {"severity": "ERROR", "message": "m"}}
    doc = document([], scanned=["src/a.java"], results=[result])
    assert interpret_output(doc, 0, ["src/a.java"], frozenset({"r"}), "/snap").findings[0].path == "src/a.java"


def test_prerequisites_and_build_graph_are_empty(workspace):
    adapter = make_adapter(workspace, FakeProcess(VERSION_OK))
    assert tuple(adapter.check_prerequisites(adapter.probe().capability, ["java"], str(workspace))) == ()
    assert tuple(adapter.build_graph_roots(str(workspace), ["a.java"])) == ()


# --- identity: effective options and runtime fingerprint (§10.4, §16 test 36) ---

def test_effective_options_digest_tracks_settings(workspace):
    base = make_adapter(workspace, FakeProcess(VERSION_OK)).probe().identity
    same = make_adapter(workspace, FakeProcess(VERSION_OK)).probe().identity
    slower = make_adapter(workspace, FakeProcess(VERSION_OK), per_file_timeout_seconds=6).probe().identity
    assert base.effective_options_digest == same.effective_options_digest
    assert base.identity_digest == same.identity_digest
    assert slower.effective_options_digest != base.effective_options_digest


def test_runtime_fingerprint_is_stable_and_never_runs_semgrep(workspace):
    first, second = FakeProcess(), FakeProcess()
    assert make_adapter(workspace, first).runtime_fingerprint() == \
        make_adapter(workspace, second).runtime_fingerprint()
    assert first.calls == second.calls == []


def test_runtime_fingerprint_changes_with_each_input(workspace, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(RULES, pack)

    def fingerprint(**overrides):
        return make_adapter(workspace, FakeProcess(), rule_packs=[str(pack)], **overrides) \
            .runtime_fingerprint()

    base = fingerprint()
    assert fingerprint(per_file_timeout_seconds=6) != base
    assert fingerprint(timeout_threshold=4) != base
    (pack / "python.yml").write_bytes((pack / "python.yml").read_bytes() + b"\n")
    after_rule = fingerprint()
    assert after_rule != base


def test_runtime_fingerprint_contained_binds_the_image(workspace):
    backend = DummyContainmentBackend()
    base = contained_adapter(workspace, FakeProcess(), backend=backend).runtime_fingerprint()
    other = contained_adapter(workspace, FakeProcess(), backend=backend,
                              image="semgrep/semgrep@sha256:" + "1" * 64).runtime_fingerprint()
    assert base != other


def test_runtime_fingerprint_raises_on_a_missing_pack_or_a_missing_image(workspace, tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree(RULES, pack)
    adapter = make_adapter(workspace, FakeProcess(), rule_packs=[str(pack)])
    adapter.runtime_fingerprint()
    shutil.rmtree(pack)
    with pytest.raises((OSError, ValueError)):
        adapter.runtime_fingerprint()
    with pytest.raises(ValueError):       # contained execution without a pinned image has no runtime to fingerprint
        contained_adapter(workspace, FakeProcess(), backend=DummyContainmentBackend(), image=None).runtime_fingerprint()
