"""SEC-001 foundation: kriya/tools/containment.py's backend-independent
contract - composition and fail-closed semantics, per the SEC-001
implementation work package's own explicit test requirements."""
import pytest

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    NullContainmentBackend,
    ResourceLimitSetupError,
    DummyContainmentBackend,
    TrustClass,
    resolve_containment_backend,
)


def test_trusted_infrastructure_profile_does_not_require_a_backend():
    """Trust must be explicit, never inferred from command text (Invariant) -
    this checks the ONE place trust is actually declared: the caller's own
    explicit TrustClass choice on the profile."""
    profile = ContainmentProfile(trust_class=TrustClass.TRUSTED_KRIYA_INFRASTRUCTURE, workspace_path="/tmp")
    assert profile.backend_required is False


def test_untrusted_execution_profile_requires_a_backend():
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp")
    assert profile.backend_required is True


def test_profile_never_names_a_specific_mechanism():
    """Scope item 5: 'Do not bake Docker, sandbox-exec or macOS-specific
    concepts into the profile.' - checked structurally: the dataclass's own
    field names must not mention a specific backend."""
    field_names = set(ContainmentProfile.__dataclass_fields__.keys())
    for forbidden in ("docker", "sandbox_exec", "oci", "container_image"):
        assert not any(forbidden in f.lower() for f in field_names)


def test_null_backend_prepare_returns_env_allowlist_and_no_rlimit_by_default():
    backend = NullContainmentBackend()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp", env_allowlist=["PATH"],
    )
    prepared = backend.prepare(profile)
    assert prepared.backend_name == "none"
    assert prepared.env is not None and "PATH" in prepared.env
    assert prepared.preexec_fn is None  # no cpu_seconds/memory_mb requested


def test_null_backend_prepare_builds_rlimit_preexec_when_requested():
    backend = NullContainmentBackend()
    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp",
        cpu_seconds=60, memory_mb=256,
    )
    prepared = backend.prepare(profile)
    assert prepared.preexec_fn is not None


def test_test_backend_configured_to_fail_raises_backend_unavailable():
    """Scope item 5's own explicit allowance: a test/dummy backend proving
    fail-closed semantics."""
    backend = DummyContainmentBackend(should_fail=True, failure_message="simulated outage")
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp")
    with pytest.raises(BackendUnavailableError, match="simulated outage"):
        backend.prepare(profile)
    assert backend.prepared_profiles == []  # never recorded as prepared


def test_test_backend_configured_to_succeed_records_the_profile():
    backend = DummyContainmentBackend(should_fail=False)
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/tmp")
    prepared = backend.prepare(profile)
    assert prepared.backend_name == "test"
    assert backend.prepared_profiles == [profile]


def test_resolve_containment_backend_none_returns_null_backend():
    backend = resolve_containment_backend("none")
    assert isinstance(backend, NullContainmentBackend)


def test_resolve_containment_backend_unknown_name_fails_closed():
    """Never silently falls back to NullContainmentBackend for a typo'd or
    removed backend name - that would be exactly the kind of
    silent-downgrade-to-uncontained-execution Invariant 9 forbids."""
    with pytest.raises(BackendUnavailableError, match="Unknown containment backend"):
        resolve_containment_backend("docker")


def test_resolve_containment_backend_test_name_is_not_production_selectable():
    """'test' is deliberately excluded from the production registry - it
    exists for tests to construct DummyContainmentBackend(...) directly,
    never for production config to name."""
    with pytest.raises(BackendUnavailableError):
        resolve_containment_backend("test")


def test_resource_limit_setup_error_is_a_containment_setup_error():
    assert issubclass(ResourceLimitSetupError, ContainmentSetupError)


def test_backend_unavailable_error_is_a_containment_setup_error():
    assert issubclass(BackendUnavailableError, ContainmentSetupError)


def test_network_authority_has_no_boolean_only_values():
    """Task 3's own authority-model requirement: network authority must not
    collapse to a single true/false switch - dependency-acquisition and
    arbitrary-execution network need to be independently expressible."""
    values = {member.value for member in NetworkAuthority}
    assert "dependency_registry_only" in values
    assert len(values) >= 3
