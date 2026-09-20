"""TOOL-003 P1: MCP capability authority - definition and governance only.

Core invariant: for every configured MCP server,

    effective runtime capability <= operator-authorized capability profile

MCP-provided metadata (tool name, description, JSON schema, annotations,
argument values, result content, anything the server itself says or an LLM
says about it) is NEVER an authority source for this profile - the ONLY
input is `kriya.config.config.MCPCapabilityConfig`, itself SEC-009-governed
(the whole `mcp.<server>` block is unconditionally SECURITY_AUTHORITY - see
kriya/config/authority.py's `classify_field()`), resolved once per server
into an `MCPCapabilityProfile` here.

SECURITY STATEMENT (state this exactly, no stronger claim): TOOL-003 P1
defines and governs MCP capability authority but does not yet enforce that
authority at the OS/container boundary. Until TOOL-003 P2 containment
lands, an MCP process that starts still has the host capabilities of its
execution environment - resolving a restrictive `MCPCapabilityProfile` here
does not, by itself, stop a server from reading/writing/reaching anything
its real host process could already reach. This module is declarative and
governance-only; it deliberately does not touch `spawn_mcp_process()` or
route through `kriya.tools.containment.ContainmentBackend` at all.

Filesystem path safety (escape rejection for a relative capability path,
anchored to the config file's own directory) already happened once, at
config-load time, in `kriya.config.config.resolve_config_state()` - see
that function's own "MCP capability path resolution" block. By the time
`resolve_mcp_capability_profile()` runs here, every path in an
`MCPCapabilityConfig` is already an absolute, already-safe real path; this
module only classifies each one as WORKSPACE_RELATIVE vs. HOST_PATH for
canonical/audit purposes (a purely descriptive comparison against
`workspace_root`, never a second safety check) - see `resolve_mcp_capability_
profile()`'s own docstring for why re-deriving that classification against
a possibly-different `os.getcwd()` at MCPManager.start_all() time is safe
even though the config-load-time value was anchored to config_dir: the
authorized path VALUE never changes here, only its purely-cosmetic scope
label could, in the rare case an explicit --config lives outside the
workspace it configures.

NETWORK: a dedicated `MCPNetworkAuthority` enum, NOT an added member of
`kriya.tools.containment.NetworkAuthority` - see
`kriya.config.config.MCPCapabilityConfig`'s own docstring for why reusing
that shared, already-in-production enum was evaluated and rejected
(OCIContainmentBackend.prepare() has no exhaustive-match guard on it; a
third member risks a silent full-UNRESTRICTED fallback in the unrelated
target-code sandboxing path). Value strings are deliberately identical to
`NetworkAuthority.DENIED`/`UNRESTRICTED` ("denied"/"unrestricted") so a
future TOOL-003 P2 mapping onto real containment is lossless and trivial;
`EXPLICIT_DESTINATIONS` has no ContainmentProfile analogue at all today
(that enum's own `DEPENDENCY_REGISTRY_ONLY` is a narrower, differently-
sourced concept - see its own docstring - and must never be reused for
arbitrary MCP-server destinations). Localhost is not a distinct authority
tier - an operator wanting an MCP server to reach `localhost`/`127.0.0.1`
lists it as an ordinary `EXPLICIT_DESTINATIONS` host, same as any other.

PROCESS: deliberately no configurable field at all (see
`MCPCapabilityConfig`'s own docstring for why) - every resolved profile
carries the same fixed, honest `PROCESS_AUTHORITY_STATEMENT` constant.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Tuple


class MCPCapabilityConfigError(ValueError):
    """A capability profile could not be resolved - an invalid/unsupported
    network posture, or an internal-consistency failure (e.g.
    EXPLICIT_DESTINATIONS with no hosts). Filesystem path escape is NOT
    raised here - it fails closed earlier, at config-load time, in
    kriya.config.config.resolve_config_state() (see module docstring)."""


class MCPPathScope(str, Enum):
    """Purely descriptive, for canonical/audit output and future OCI mount
    binding - never a second authority check (the path was already
    validated safe at config-load time regardless of which scope it ends
    up labeled)."""

    WORKSPACE_RELATIVE = "workspace_relative"
    HOST_PATH = "host_path"


class MCPNetworkAuthority(Enum):
    DENIED = "denied"
    EXPLICIT_DESTINATIONS = "explicit_destinations"
    UNRESTRICTED = "unrestricted"


# Fixed, honest constant every resolved profile carries for its `process`
# authority - never a per-server configurable value (see module docstring).
# Changing this string in a future pass (e.g. once TOOL-003 P2 lands real
# OCI process containment) will shift every existing profile's digest -
# that is intended: a real capability change must always be a real
# identity change, exactly like `compute_mcp_schema_digest`'s treatment of
# a schema change for MCPToolIdentity.
PROCESS_AUTHORITY_STATEMENT = "unconstrained_pending_oci_containment"


@dataclass(frozen=True)
class MCPResolvedPath:
    scope: MCPPathScope
    real_path: str
    writable: bool

    def canonical_key(self) -> str:
        return f"{self.scope.value}:{'rw' if self.writable else 'ro'}:{self.real_path}"


@dataclass(frozen=True)
class MCPCapabilityProfile:
    """The resolved, canonical maximum authority one configured MCP server
    may ever be declared to possess. Frozen/hashable by construction, like
    `kriya.policy.model.MCPToolIdentity` - constructed once per server, at
    binding time, from the SAME already-validated `MCPCapabilityConfig`
    everywhere it is read from (never re-derived from raw config mid-
    session)."""

    workspace_read: bool
    workspace_write: bool
    temp_read_write: bool
    dependency_cache_read: bool
    additional_read_paths: Tuple[MCPResolvedPath, ...] = ()
    additional_write_paths: Tuple[MCPResolvedPath, ...] = ()
    network: MCPNetworkAuthority = MCPNetworkAuthority.DENIED
    network_hosts: Tuple[str, ...] = ()
    process_authority: str = PROCESS_AUTHORITY_STATEMENT

    def to_canonical_dict(self) -> dict:
        """Stable, order-independent representation - two profiles that
        differ only in the INPUT ORDER of paths/hosts (never in the actual
        authorized set) must produce the same dict, and therefore the same
        digest (see `compute_mcp_capability_profile_digest()`)."""
        return {
            "workspace_read": self.workspace_read,
            "workspace_write": self.workspace_write,
            "temp_read_write": self.temp_read_write,
            "dependency_cache_read": self.dependency_cache_read,
            "additional_read_paths": sorted(p.canonical_key() for p in self.additional_read_paths),
            "additional_write_paths": sorted(p.canonical_key() for p in self.additional_write_paths),
            "network": self.network.value,
            "network_hosts": sorted(set(self.network_hosts)),
            "process_authority": self.process_authority,
        }


def compute_mcp_capability_profile_digest(profile: MCPCapabilityProfile) -> str:
    """SHA-256 over the profile's canonical dict - deliberately a single
    plain hash (matching `compute_mcp_schema_digest`'s own precedent), not
    over-engineered cryptography. This is an IDENTITY/audit digest, a
    DIFFERENT digest from SEC-009 P2's own approval-set digest
    (`kriya.config.authority_approval.compute_set_digest()`) - that one
    binds the raw configured `mcp.<server>` blob for drift detection; this
    one binds the resolved, canonicalized effective profile for TOOL-002
    telemetry, audit evidence, and future OCI containment binding. Do not
    conflate the two - see CLAUDE.md's TOOL-003 P1 section."""
    canonical = json.dumps(profile.to_canonical_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_path_scope(real_path: str, workspace_root: str) -> MCPPathScope:
    real_root = os.path.realpath(workspace_root)
    if real_path == real_root or real_path.startswith(real_root + os.sep):
        return MCPPathScope.WORKSPACE_RELATIVE
    return MCPPathScope.HOST_PATH


def resolve_mcp_capability_profile(capability_config: Any, workspace_root: str) -> MCPCapabilityProfile:
    """Pure, deterministic: the SAME `capability_config` + `workspace_root`
    always resolves to the SAME `MCPCapabilityProfile` (Task 3's "exactly
    one deterministic effective capability profile" requirement) - no
    ambient state, no I/O beyond `os.path.realpath` (idempotent on an
    already-canonical absolute path, safe to call again here even though
    kriya.config.config.resolve_config_state() already canonicalized these
    same strings once at config-load time).

    `capability_config` accepts either a real `MCPCapabilityConfig`
    pydantic instance or an equivalent plain dict (mirrors this
    codebase's own `server_cfg` duck-typing convention in
    kriya/mcp/mcp.py's `MCPManager.start_all()`).

    Filesystem paths are NOT re-validated for escape here - see module
    docstring for why that already happened, once, at config-load time.
    This function only classifies each already-safe absolute path's scope
    (WORKSPACE_RELATIVE vs. HOST_PATH) by comparing it against
    `workspace_root` - a purely descriptive comparison, never a second
    authority gate; even if `workspace_root` here differs from the
    `config_dir` the path was originally anchored/validated against (only
    possible for an explicit --config living outside the workspace it
    configures), the AUTHORIZED VALUE itself never changes, only which
    descriptive label it is reported under."""
    if hasattr(capability_config, "model_dump"):
        cfg = capability_config.model_dump()
    else:
        cfg = dict(capability_config or {})

    network_raw = cfg.get("network", "denied")
    try:
        network = MCPNetworkAuthority(network_raw)
    except ValueError:
        raise MCPCapabilityConfigError(
            f"unsupported MCP network authority {network_raw!r} - must be one of "
            f"{[m.value for m in MCPNetworkAuthority]!r}"
        ) from None

    network_hosts = tuple(sorted({h.strip().lower() for h in cfg.get("network_hosts", []) if h.strip()}))
    if network is MCPNetworkAuthority.EXPLICIT_DESTINATIONS and not network_hosts:
        raise MCPCapabilityConfigError(
            "MCP network authority 'explicit_destinations' requires a non-empty "
            "network_hosts set - refusing to resolve an explicit-destinations "
            "profile naming no destinations."
        )

    def _resolved_paths(field_name: str, writable: bool) -> Tuple[MCPResolvedPath, ...]:
        raw_paths = cfg.get(field_name) or []
        return tuple(
            MCPResolvedPath(
                scope=_resolve_path_scope(p, workspace_root),
                real_path=os.path.realpath(p),
                writable=writable,
            )
            for p in raw_paths
        )

    return MCPCapabilityProfile(
        workspace_read=bool(cfg.get("workspace_read", False)),
        workspace_write=bool(cfg.get("workspace_write", False)),
        temp_read_write=bool(cfg.get("temp_read_write", False)),
        dependency_cache_read=bool(cfg.get("dependency_cache_read", False)),
        additional_read_paths=_resolved_paths("additional_read_paths", writable=False),
        additional_write_paths=_resolved_paths("additional_write_paths", writable=True),
        network=network,
        network_hosts=network_hosts,
    )
