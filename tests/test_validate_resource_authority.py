"""SEC-007 / OBS-005 (2026-09-12): deterministic coverage for the
acquisition/target resource-authority split and acquisition failure
evidence - kriya/tools/validate.py's `build_containment_profile_and_backend`
and kriya/tools/dependency_execution.py's `log_acquisition_outcome`/
`_acquisition_profile`. No Docker required - real Docker integration
coverage for the actual SEC-007 incident (Maven succeeding under a
separate, sufficient acquisition memory cap while the target cap stays
low) lives in tests/test_validate_oci.py."""
import logging

from kriya.config.config import AutonomyConfig
from kriya.tools.dependency_execution import _acquisition_profile, log_acquisition_outcome
from kriya.tools.validate import PolymorphicValidator


def _cfg(**overrides) -> AutonomyConfig:
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci", **overrides)


def test_acquisition_call_receives_acquisition_resource_limits(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=_cfg(acquisition_cpu_seconds=111, acquisition_memory_mb=222),
    )
    profile, _backend = validator.build_containment_profile_and_backend(acquisition=True)
    assert profile.cpu_seconds == 111
    assert profile.memory_mb == 222


def test_target_call_receives_target_resource_limits(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=_cfg(sandbox_cpu_seconds=33, sandbox_memory_mb=44),
    )
    profile, _backend = validator.build_containment_profile_and_backend(acquisition=False)
    assert profile.cpu_seconds == 33
    assert profile.memory_mb == 44


def test_changing_acquisition_memory_does_not_change_target_memory(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=_cfg(sandbox_memory_mb=128, acquisition_memory_mb=4096),
    )
    target_profile, _ = validator.build_containment_profile_and_backend(acquisition=False)
    assert target_profile.memory_mb == 128


def test_changing_target_memory_does_not_change_acquisition_memory(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=_cfg(sandbox_memory_mb=16, acquisition_memory_mb=2048),
    )
    acquisition_profile, _ = validator.build_containment_profile_and_backend(acquisition=True)
    assert acquisition_profile.memory_mb == 2048


def test_default_acquisition_limits_are_more_generous_than_a_deliberately_tight_target_cap(tmp_path):
    """The exact real-world shape of the SEC-007 incident: a target cap
    tightened to bound a hostile application (128MB) must not also bound
    acquisition - the PACKAGED DEFAULT acquisition_memory_mb must already
    exceed a plausible "very strict" target value."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_cfg(sandbox_memory_mb=128))
    acquisition_profile, _ = validator.build_containment_profile_and_backend(acquisition=True)
    target_profile, _ = validator.build_containment_profile_and_backend(acquisition=False)
    assert acquisition_profile.memory_mb > target_profile.memory_mb
    assert target_profile.memory_mb == 128


def test_python_acquisition_profile_uses_acquisition_resource_limits():
    profile = _acquisition_profile("/tmp/fake-workspace", "/tmp/fake-cache", [], registry_hosts=["pypi.org"])
    assert profile.cpu_seconds == 300
    assert profile.memory_mb == 2048


def test_python_acquisition_profile_resource_limits_are_overridable():
    profile = _acquisition_profile(
        "/tmp/fake-workspace", "/tmp/fake-cache", [], cpu_seconds=9, memory_mb=17, registry_hosts=["pypi.org"],
    )
    assert profile.cpu_seconds == 9
    assert profile.memory_mb == 17


# --- SEC-006: registry-scoped acquisition authority ---

def test_acquisition_profile_uses_dependency_registry_only_network():
    from kriya.tools.containment import NetworkAuthority
    profile = _acquisition_profile(
        "/tmp/fake-workspace", "/tmp/fake-cache", [], registry_hosts=["pypi.org", "files.pythonhosted.org"],
    )
    assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
    assert profile.network_destinations == ("files.pythonhosted.org", "pypi.org")


# --- OBS-005: acquisition outcome evidence ---

def test_acquisition_success_is_logged_at_info(caplog):
    with caplog.at_level(logging.INFO, logger="kriya.tools.dependency_execution"):
        log_acquisition_outcome("maven", "mvn test", returncode=0, timed_out=False)
    assert any("succeeded" in r.message for r in caplog.records)


def test_acquisition_nonzero_exit_is_logged_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="kriya.tools.dependency_execution"):
        log_acquisition_outcome("maven", "mvn test", returncode=1, timed_out=False)
    assert any("exited 1" in r.message for r in caplog.records)


def test_acquisition_timeout_is_logged_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="kriya.tools.dependency_execution"):
        log_acquisition_outcome("maven", "mvn test", returncode=None, timed_out=True)
    assert any("timed out" in r.message for r in caplog.records)


def test_acquisition_resource_killed_signature_is_distinguished(caplog):
    """returncode=137 (128 + SIGKILL) - the exact real value observed in
    the SEC-007 incident - is called out specifically, not just reported
    as an opaque nonzero exit."""
    with caplog.at_level(logging.WARNING, logger="kriya.tools.dependency_execution"):
        log_acquisition_outcome("maven", "mvn test", returncode=137, timed_out=False)
    assert any("likely terminated by a signal" in r.message for r in caplog.records)


def test_acquisition_ordinary_nonzero_exit_is_not_misreported_as_resource_killed(caplog):
    """A plain tool-reported failure (e.g. exit 1) must NOT be
    misclassified as a likely resource termination - only the POSIX
    128+signal range is called out that way."""
    with caplog.at_level(logging.WARNING, logger="kriya.tools.dependency_execution"):
        log_acquisition_outcome("maven", "mvn test", returncode=1, timed_out=False)
    assert not any("likely terminated by a signal" in r.message for r in caplog.records)


def test_acquisition_evidence_never_logs_secrets_or_environment():
    """Deterministic code-level check: log_acquisition_outcome's own
    signature has no way to receive stdout/stderr/env at all - it
    physically cannot log content it was never given."""
    import inspect
    params = inspect.signature(log_acquisition_outcome).parameters
    assert set(params) == {"purpose", "goal_desc", "returncode", "timed_out"}
