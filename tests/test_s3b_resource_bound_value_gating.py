"""S3b (OI-132): a repository-controlled kriya.yaml cannot RAISE the containment resource bounds of the sandbox it runs in
without the human authority path (SEC-009 P1 classification, P2 durable approval); a LOWER (or equal) value stays
repository-safe.

Measured defect: sandbox_cpu_seconds / sandbox_memory_mb / acquisition_cpu_seconds / acquisition_memory_mb /
shell_command_timeout_seconds were REPOSITORY_SAFE (every mcp_lifecycle bound is SECURITY_AUTHORITY): a workspace
kriya.yaml raising each one x100 loaded with 0 pending violations, and so did -1 (on macOS setrlimit(-1) makes the
spawn fail; on Linux RLIM_INFINITY is -1, i.e. unlimited CPU). The sibling audit adds run_verification_timeout_seconds
(the wall-clock bound of running the built application, same class as the shell timeout; the OCI backend honours
cpu_seconds only as a rate cap, so the wall-clock timeout IS the time containment).

Mechanism: the S3 value gate (``authority.value_gated_field_classification``) generalized from "exactly the restricting
bool" to "a positive int at most the packaged ceiling" for these fields; the static class is SECURITY_AUTHORITY so a
skipped override fails closed. There is no trusted user-level configuration source in SEC-009 (an explicit --config
outside the workspace is repository-equivalent): the operator positive control is the durable approval / trust file.

Characterization FIRST (S1 method): pins of governed behaviour (green before and after) and EXPECTED-TO-FLIP pins
strict-xfail (AssertionError only) at the characterization commit, all failing on the unfixed bytes with --runxfail; the
correction removes the marks.
"""
import subprocess

import pytest
import yaml

from kriya.config import authority_approval as aa
from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import AppConfig, load_config, resolve_config_state

# The packaged ceiling of each bound (AutonomyConfig defaults; default_config.yaml does not set them - pinned below).
CEILINGS = {"sandbox_cpu_seconds": 240, "sandbox_memory_mb": 4096, "acquisition_cpu_seconds": 300,
            "acquisition_memory_mb": 2048, "shell_command_timeout_seconds": 300, "run_verification_timeout_seconds": 90}
MEASURED = [f for f in CEILINGS if f != "run_verification_timeout_seconds"]


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)


def _workspace(tmp_path, monkeypatch, autonomy, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": autonomy}))
    monkeypatch.chdir(ws)
    return ws


def _pending():
    return sorted(v.field_path for v in resolve_config_state().violations)


def _denied(config_path=None, trust_file=None):
    try:
        load_config(config_path, trust_file=trust_file)
    except ConfigAuthorityError as error:
        return sorted(v.field_path for v in error.violations)
    return None


def _approve():
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    aa.save_approval_artifact(aa.default_local_approval_path(state.workspace_root), artifact)


# =========================================================================== governed behaviour that must NOT change

def test_the_ceilings_are_the_packaged_defaults():
    defaults = AppConfig().autonomy
    assert {f: getattr(defaults, f) for f in CEILINGS} == CEILINGS
    import os

    import kriya.config as config_package

    packaged = yaml.safe_load(open(os.path.join(os.path.dirname(config_package.__file__), "default_config.yaml"), encoding="utf-8"))
    assert not set(CEILINGS) & set((packaged.get("autonomy") or {}))           # the schema default IS the packaged value


@pytest.mark.parametrize("field", list(CEILINGS))
@pytest.mark.parametrize("factor", ["lower", "equal", "one"])
def test_a_lower_or_equal_bound_from_the_repository_needs_no_approval(tmp_path, monkeypatch, field, factor):
    value = {"lower": CEILINGS[field] // 2, "equal": CEILINGS[field], "one": 1}[factor]
    _workspace(tmp_path, monkeypatch, {field: value})
    assert _pending() == [] and _denied() is None
    assert getattr(load_config().autonomy, field) == value


def test_the_sec_live_validation_shapes_still_load(tmp_path, monkeypatch):
    """Migration: the only kinds of value the 8,969 user YAMLs under ~/kriya-* set (scratch s3b/before/migration*.txt):
    a tightened hostile-code cap and the defaults restated."""
    _workspace(tmp_path, monkeypatch, {"sandbox_cpu_seconds": 120, "sandbox_memory_mb": 128, "acquisition_cpu_seconds": 300,
                                       "acquisition_memory_mb": 2048, "run_verification_timeout_seconds": 90})
    assert _pending() == [] and _denied() is None


@pytest.mark.parametrize("field", list(CEILINGS))
def test_the_durable_approval_makes_a_raise_effective(tmp_path, monkeypatch, field):
    """Positive control: the operator path (kriya authority approve) - the same value the repository cannot grant itself."""
    _workspace(tmp_path, monkeypatch, {field: CEILINGS[field] * 4})
    _approve()
    assert _denied() is None and getattr(load_config().autonomy, field) == CEILINGS[field] * 4


def test_a_trust_file_outside_the_workspace_makes_a_raise_effective(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"sandbox_memory_mb": 8192, "shell_command_timeout_seconds": 900})
    state = resolve_config_state()
    trust = tmp_path / "ci-trust.json"
    aa.save_approval_artifact(str(trust), aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root))
    cfg = load_config(trust_file=str(trust))
    assert cfg.autonomy.sandbox_memory_mb == 8192 and cfg.autonomy.shell_command_timeout_seconds == 900


def test_the_mcp_lifecycle_bounds_stay_security_authority(tmp_path, monkeypatch):
    from kriya.config import authority

    for leaf in ("cpu_seconds", "memory_mb", "request_timeout_seconds"):
        assert authority.classify_field("mcp_lifecycle", leaf) is authority.FieldClassification.SECURITY_AUTHORITY
        assert authority.value_gated_field_classification("mcp_lifecycle", leaf, 1) is None   # not lowered by the gate


# =========================================================================== EXPECTED TO FLIP (the measured defect)

FLIP = pytest.mark.xfail(strict=True, raises=AssertionError, reason="S3b characterization: the measured defect (flips with the correction)")


@FLIP
@pytest.mark.parametrize("field", list(CEILINGS))
def test_a_repository_kriya_yaml_cannot_raise_a_containment_bound(tmp_path, monkeypatch, field):
    _workspace(tmp_path, monkeypatch, {field: CEILINGS[field] + 1})
    assert _pending() == [f"autonomy.{field}"]
    assert _denied() == [f"autonomy.{field}"]


@FLIP
@pytest.mark.parametrize("value", [-1, 0, True, "100", 100.0, None, [1]], ids=["negative", "zero", "bool", "string", "float", "null", "list"])
@pytest.mark.parametrize("field", MEASURED)
def test_a_value_that_is_not_a_positive_int_within_the_ceiling_is_refused(tmp_path, monkeypatch, field, value):
    """-1 is RLIM_INFINITY on Linux (a raise disguised as a lower number); everything that is not exactly a positive int
    fails closed to the authority path (pydantic would coerce "100" / 100.0 / True)."""
    _workspace(tmp_path, monkeypatch, {field: value})
    assert _pending() == [f"autonomy.{field}"]


@FLIP
def test_authority_inspect_lists_a_raised_bound_as_pending(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main

    _workspace(tmp_path, monkeypatch, {"sandbox_memory_mb": 65536})
    out = CliRunner().invoke(main, ["authority", "inspect"], catch_exceptions=False).output
    assert "autonomy.sandbox_memory_mb" in out and "security_authority" in out


@FLIP
def test_an_explicit_config_outside_the_workspace_is_not_trusted_either(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, {"sandbox_memory_mb": 1024})
    outside = tmp_path / "operator.yaml"
    outside.write_text(yaml.safe_dump({"autonomy": {"sandbox_cpu_seconds": 3600}}))
    assert _denied(str(outside)) == ["autonomy.sandbox_cpu_seconds"]


@FLIP
def test_an_approval_does_not_survive_a_further_raise(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch, {"sandbox_cpu_seconds": 500})
    _approve()
    assert _denied() is None
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"autonomy": {"sandbox_cpu_seconds": 600}}))
    assert _denied() == ["autonomy.sandbox_cpu_seconds"]


@FLIP
def test_the_gate_is_the_s3_value_gate_with_a_ceiling():
    from kriya.config import authority

    gate = authority.value_gated_field_classification
    for field, ceiling in CEILINGS.items():
        assert gate("autonomy", field, ceiling) is authority.FieldClassification.REPOSITORY_SAFE
        assert gate("autonomy", field, 1) is authority.FieldClassification.REPOSITORY_SAFE
        for refused in (ceiling + 1, 0, -1, True, False, str(ceiling), float(ceiling), None):
            assert gate("autonomy", field, refused) is authority.FieldClassification.SECURITY_AUTHORITY, (field, refused)
        assert authority.classify_field("autonomy", field) is authority.FieldClassification.SECURITY_AUTHORITY   # closed fallback
        assert authority.is_known_field("autonomy", field)
