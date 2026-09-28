"""HostIdentityPort and the one SEC-008 writer-identity rule
(ARCH-PLATFORM-001, PLAT-HOST-IDENTITY-001).

The OCI backend and TOOL-003's MCP adapter used to carry two copies of the
rule (never root; every writable mount owned by the Kriya process's own
uid). Both now call containment.host_writer_identity over the host's
identity provider and keep their own typed errors and messages; a host
without a uid/gid provider is refused, never mapped to a guessed identity.
"""
import dataclasses
import os

import pytest

from kriya.mcp.containment_adapter import resolve_mcp_container_identity
from kriya.platform import services
from kriya.platform.capabilities import CapabilityReport, CapabilityStatus, PlatformCapability
from kriya.platform.host_identity import HostIdentity
from kriya.tools.containment import BackendUnavailableError, HostWriterIdentityRefused, host_writer_identity
from kriya.tools.containment_oci import resolve_host_writer_identity

posix_only = pytest.mark.skipif(services.host_family() != services.POSIX, reason="POSIX provider contract")


class _Identity:
    """A strict test provider: a fixed identity and a fixed owner per path."""

    name = "test-identity"

    def __init__(self, uid, gid, owners):
        self._identity, self._owners = HostIdentity(uid, gid), owners

    def capability(self):
        return CapabilityReport(PlatformCapability.UID_GID_IDENTITY, CapabilityStatus.ENFORCED, self.name)

    def current(self):
        return self._identity

    def owner_uid(self, path):
        return self._owners[path]


def _with_identity(provider):
    return services.override(dataclasses.replace(services.compose(services.POSIX), host_identity=provider))


@posix_only
def test_the_posix_provider_reports_the_process_identity_and_path_owner(tmp_path):
    provider = services.compose(services.POSIX).host_identity
    assert provider.current() == HostIdentity(os.getuid(), os.getgid())
    assert provider.owner_uid(str(tmp_path)) == os.stat(tmp_path).st_uid
    assert provider.capability().status is CapabilityStatus.ENFORCED


def test_the_rule_returns_the_trusted_identity_when_every_mount_is_owned():
    with _with_identity(_Identity(1001, 118, {"/ws": 1001, "/cache": 1001})):
        assert host_writer_identity(["/ws", "/cache"]) == (1001, 118)
        assert resolve_host_writer_identity(["/ws"], purpose="p") == (1001, 118)
        assert resolve_mcp_container_identity(["/ws"]) == (1001, 118)


@pytest.mark.parametrize("provider,kind", [
    (_Identity(0, 0, {"/ws": 0}), HostWriterIdentityRefused.PRIVILEGED),
    (_Identity(1001, 118, {"/ws": 1001, "/other": 4242}), HostWriterIdentityRefused.OWNER_MISMATCH),
])
def test_each_refusal_reaches_both_callers_as_their_own_typed_error(provider, kind):
    with _with_identity(provider):
        with pytest.raises(HostWriterIdentityRefused) as refused:
            host_writer_identity(["/ws", "/other"] if kind == HostWriterIdentityRefused.OWNER_MISMATCH else ["/ws"])
        assert refused.value.kind == kind
        paths = ["/ws", "/other"] if kind == HostWriterIdentityRefused.OWNER_MISMATCH else ["/ws"]
        with pytest.raises(BackendUnavailableError, match="root|owned by uid 4242"):
            resolve_host_writer_identity(paths, purpose="Contained execution")
        with pytest.raises(PermissionError, match="root|owned by uid 4242"):
            resolve_mcp_container_identity(paths)


def test_a_host_without_an_identity_provider_is_refused_by_both_callers():
    with services.override(services.compose(services.WINDOWS)):
        with pytest.raises(HostWriterIdentityRefused) as refused:
            host_writer_identity(["/ws"])
        assert refused.value.kind == HostWriterIdentityRefused.UNAVAILABLE
        with pytest.raises(BackendUnavailableError, match="no trusted host identity"):
            resolve_host_writer_identity(["/ws"], purpose="Contained execution")
        with pytest.raises(PermissionError, match="no trusted host identity"):
            resolve_mcp_container_identity(["/ws"])
        # No write authority needs no host identity at all (the fixed nobody uid).
        assert resolve_mcp_container_identity([]) == (65534, 65534)
