"""TOOL-003 P2: maps a resolved `MCPCapabilityProfile` (TOOL-003 P1,
kriya/mcp/capability.py) onto the existing SEC-001 `ContainmentProfile`
contract (kriya/tools/containment.py) - the ONE place this translation
happens, Kriya-owned and explicit throughout. No input here is ever
server-supplied (tool name/description/schema/annotations/arguments/
result content) - only the already-resolved, already-SEC-009-governed
capability profile and facts Kriya itself computed (the workspace root,
the resolved SEC-003 environment, the lifecycle resource bounds).

Container-side mount points are fixed, Kriya-chosen constants - never
derived from anything the MCP server or its configuration supplies -
so a hostile/misconfigured server can never influence where its own
granted authority is mounted, only whether it can see/write what's
already there.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from kriya.mcp.capability import MCPCapabilityProfile, MCPNetworkAuthority
from kriya.tools.containment import (
    ContainmentProfile,
    ContainmentSetupError,
    MountSpec,
    NetworkAuthority,
    TrustClass,
)

_CONTAINER_EXTRA_RO_PREFIX = "/kriya/mcp/extra/ro"
_CONTAINER_EXTRA_RW_PREFIX = "/kriya/mcp/extra/rw"

# The standard "nobody" UID/GID on every Debian/Alpine-family image this
# backend selects (kriya/tools/containment_oci.py::_select_image_and_cache_mount) -
# used only when an MCP profile grants NO write authority at all, so there
# is no host-owned target whose ownership the container identity could
# ever need to match (see resolve_mcp_container_identity()'s own docstring).
_NO_WRITE_UID = 65534
_NO_WRITE_GID = 65534


class MCPContainmentUnsupportedError(ContainmentSetupError):
    """A resolved MCPCapabilityProfile requires a containment posture
    TOOL-003 P2 does not yet implement (EXPLICIT_DESTINATIONS network
    authority - see module-level NOTE below). Raised INSTEAD OF silently
    mapping to a weaker (UNRESTRICTED) or stronger-looking-but-wrong
    (DENIED) posture - fail closed, never approximate. Subclasses
    `ContainmentSetupError` (Task 10: "reuse existing exception hierarchy
    if sufficient") so it propagates through MCPClient/MCPManager exactly
    like every other containment-setup failure - a distinct, typed
    "containment could not be established" signal, never mistaken for an
    ordinary MCP startup/protocol failure."""


# NOTE on EXPLICIT_DESTINATIONS (STOP, per this package's own explicit
# authorization to do so rather than approximate): kriya/tools/containment_oci.py's
# real per-run-scoped-egress mechanism (SEC-006, NetworkAuthority.DEPENDENCY_REGISTRY_ONLY -
# a dedicated Squid forward proxy container + a fresh per-run Docker network +
# in-container iptables/privilege-drop setup, all torn down at the end of ONE
# finite acquisition command) is the closest existing primitive, and is
# reused for DENIED/UNRESTRICTED above without modification - but adapting
# it to a SESSION-LIFETIME MCP server (the proxy and network would need to
# live for the entire MCP connection, not one finite command, with their own
# health-monitoring and cleanup wired into MCPClient's own close sequence)
# is real, additional infrastructure work, not a bounded reuse. Per this
# package's own STOP condition ("If EXPLICIT_DESTINATIONS cannot be safely
# implemented using existing containment design, STOP before substituting
# UNRESTRICTED"), this pass does not implement it - a server configured
# with `network: explicit_destinations` fails closed here with a typed,
# actionable error, never silently downgraded to DENIED (would look like a
# working configuration that quietly does less than declared) or upgraded
# to UNRESTRICTED (the exact silent-widening the STOP condition exists to
# prevent).


def resolve_mcp_container_identity(write_targets: List[str]) -> Tuple[int, int]:
    """TOOL-003 P2 (Task 7) non-root execution identity. Two cases:

    - `write_targets` empty (the profile grants no write authority at all -
      no workspace_write, no writable additional_mounts): there is no
      host-owned target a container UID could ever need to match, so a
      fixed, well-known non-root identity (`_NO_WRITE_UID`/`_GID`, the
      standard "nobody" UID present on every image this backend selects)
      is always safe and sufficient.
    - `write_targets` non-empty: mirrors kriya/tools/containment_oci.py's
      own SEC-008 `_resolve_acquisition_identity()` reasoning exactly (a
      NEW, MCP-scoped function rather than calling that one directly - it
      is registry-acquisition-specific and this pass leaves that CLOSED
      code path untouched): the container must run as the REAL host UID/
      GID that owns every write target, or a write that succeeds inside
      the container would fail on the host (root-squash-like behavior) or
      require a host permission/ownership change this package's own
      Task 7 explicitly forbids ("never require host chmod/chown
      widening", "preserve host permissions"). Fails closed (raises
      PermissionError) if the invoking Kriya process itself is UID 0, or
      if any write target is owned by a DIFFERENT real UID than the
      invoking process - there is no safe identity to fall back to in
      either case, matching the SEC-008 precedent's own fail-closed
      choice."""
    if not write_targets:
        return _NO_WRITE_UID, _NO_WRITE_GID
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise PermissionError(
            "MCP containment cannot resolve a safe non-root container identity - the "
            "invoking Kriya process itself is running as root (uid 0), and running the "
            "contained MCP server as root too is forbidden (Task 7's non-root invariant). "
            "Run Kriya as a non-root user to grant MCP write authority."
        )
    for path in write_targets:
        st = os.stat(path)
        if st.st_uid != uid:
            raise PermissionError(
                f"MCP containment: write target {path!r} is owned by uid {st.st_uid}, which "
                f"does not match the invoking Kriya process's own uid {uid} - refusing to "
                "start a container whose write mounts could not match a real host owner "
                "without a host chmod/chown widening (forbidden by Task 7)."
            )
    return uid, gid


def map_capability_profile_to_containment(
    profile: MCPCapabilityProfile,
    *,
    workspace_root: str,
    resolved_env: Dict[str, str],
    profile_digest: str,
    cpu_seconds: Optional[int] = None,
    memory_mb: Optional[int] = None,
) -> ContainmentProfile:
    """The Task 2 adapter - deterministic, Kriya-owned, no server-supplied
    input anywhere. Raises `MCPContainmentUnsupportedError` for
    `MCPNetworkAuthority.EXPLICIT_DESTINATIONS` (see module NOTE above)."""
    if profile.network is MCPNetworkAuthority.EXPLICIT_DESTINATIONS:
        raise MCPContainmentUnsupportedError(
            "MCPNetworkAuthority.EXPLICIT_DESTINATIONS is not yet enforceable at the OCI "
            "boundary (TOOL-003 P2 does not implement a session-lifetime scoped-egress "
            "mechanism this pass - see kriya/mcp/containment_adapter.py's own module NOTE) - "
            "refusing to start this MCP server rather than silently substituting DENIED "
            "(looks like a working configuration doing less than declared) or UNRESTRICTED "
            "(the exact silent authority-widening this failure exists to prevent)."
        )

    # profile.temp_read_write: intentionally NOT mapped to
    # ContainmentProfile.temp_path (a HOST directory mount) - the OCI
    # backend already unconditionally mounts a size-bounded, noexec/nosuid
    # container-internal tmpfs (kriya/tools/containment_oci.py's own
    # `--tmpfs` flag) regardless of any profile field, which already IS a
    # scoped, isolated temp read/write area with no host-path exposure at
    # all - strictly safer than a host-mounted temp dir would be. No
    # additional mount is needed to satisfy this capability.
    #
    # profile.dependency_cache_read: intentionally NOT mapped to
    # ContainmentProfile.dependency_cache_paths this pass - TOOL-003 P1
    # never specified WHICH host directory an MCP server's "dependency
    # cache" would even be (unlike autonomy.acquisition_*, which has a
    # concrete, single-purpose host cache directory), so there is no real
    # host path to mount. Declaring this capability today grants nothing
    # extra (a safe under-delivery, never an over-grant) - disclosed here
    # rather than silently ignored.

    # TASK 3 (filesystem): mount_workspace=False (no mount at all) unless
    # either workspace_read or workspace_write is granted; workspace_write
    # controls rw vs ro when a mount does exist.
    mount_workspace = profile.workspace_read or profile.workspace_write
    workspace_write = profile.workspace_write

    additional_mounts: List[MountSpec] = []
    write_targets: List[str] = []
    if mount_workspace and workspace_write:
        write_targets.append(workspace_root)
    ro_index = 0
    for resolved_path in profile.additional_read_paths:
        additional_mounts.append(MountSpec(
            host_path=resolved_path.real_path,
            container_path=f"{_CONTAINER_EXTRA_RO_PREFIX}/{ro_index}",
            writable=False,
        ))
        ro_index += 1
    rw_index = 0
    for resolved_path in profile.additional_write_paths:
        additional_mounts.append(MountSpec(
            host_path=resolved_path.real_path,
            container_path=f"{_CONTAINER_EXTRA_RW_PREFIX}/{rw_index}",
            writable=True,
        ))
        write_targets.append(resolved_path.real_path)
        rw_index += 1

    # TASK 4 (network): DENIED/UNRESTRICTED map onto the SAME enum values
    # kriya/tools/containment.py already defines - reused, never
    # reinterpreted (see kriya/config/config.py::MCPCapabilityConfig's own
    # docstring for why a shared enum value, not a shared TYPE, is reused).
    network = (
        NetworkAuthority.UNRESTRICTED if profile.network is MCPNetworkAuthority.UNRESTRICTED
        else NetworkAuthority.DENIED
    )

    # TASK 7 (non-root): resolved from the ACTUAL write targets computed
    # above - never from a server-supplied hint, never a fixed root UID.
    run_as_uid, run_as_gid = resolve_mcp_container_identity(write_targets)

    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION,
        workspace_path=workspace_root,
        mount_workspace=mount_workspace,
        workspace_write=workspace_write,
        additional_mounts=tuple(additional_mounts),
        network=network,
        resolved_env=resolved_env,
        persistent_stdio=True,
        run_as_uid=run_as_uid,
        run_as_gid=run_as_gid,
        authority_label=profile_digest,
        cpu_seconds=cpu_seconds,
        memory_mb=memory_mb,
    )
