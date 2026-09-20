"""TOOL-002 P2: durable, explicit, operator-facing MCP invocation approval.

Deliberately a SEPARATE artifact/mechanism from SEC-009's own
`kriya/config/authority_approval.py` (Invariant 12: SEC-009 config approval
!= TOOL-002 invocation approval) - reuses that module's PROVEN PATTERNS
(outside-workspace storage, atomic write, workspace-identity binding,
digest-bound records, corrupt/unknown-format fail-closed) without reusing
its artifact, its storage location, or its CLI verbs. Modeled closely on
it; never delegates to it.

Core invariant this module enforces:

    a durable approval authorizes invocation ONLY while the complete
    binding - workspace identity + exact server identity + exact tool
    name + exact input-schema digest + exact TOOL-003 capability-profile
    digest - still matches exactly. ANY drift in ANY of those five
    fields invalidates the approval; there is no partial-match/best-effort
    mode.

STORAGE LOCATION - the load-bearing security property, identical reasoning
to authority_approval.py's own: the local approval store lives OUTSIDE the
workspace (`~/.kriya/mcp_approvals/<workspace_id>.json` by default,
override via `KRIYA_MCP_APPROVAL_HOME`) - never inside it. A malicious
repository can ship arbitrary content into the workspace it controls; if
this store lived inside the workspace, that repository could ship both a
hostile MCP server config AND a self-consistent "approval" for it in the
same commit, and Kriya would honor an approval no real operator ever
granted. Anchoring outside the workspace makes this structurally
impossible.

REVALIDATION - resolve_mcp_invocation_approval() returns a callable that
reads the artifact FRESH from disk on every single call, with no caching
layer anywhere in this module. This is load-bearing, not an incidental
choice: Task 7 requires revalidating immediately before every `tools/call`
(never trusting identity captured at earlier discovery time), and Task 11
requires revocation to take effect without restarting Kriya - both fail if
this module ever memoizes a load.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from kriya.control.workspace_identity import workspace_identity
from kriya.policy.model import MCPCapabilityProfileIdentity, MCPToolIdentity

SCHEMA_VERSION = 1
ENV_HOME_OVERRIDE = "KRIYA_MCP_APPROVAL_HOME"


class MCPApprovalArtifactError(ValueError):
    """Structural failure loading/parsing an approval artifact (bad schema,
    missing field, not valid JSON) - never raised out of the resolver used
    by `_check_mcp_invocation` (see `_load_fail_closed()` below), only from
    the lower-level `load_approval_artifact()` a CLI command can use to
    report "present but invalid" the same way `kriya authority inspect`
    does for its own store."""


class MCPTrustPathInsideWorkspaceError(ValueError):
    """A store path (the default local store, or an explicit override)
    resolved to somewhere inside the workspace root - refused, matching
    kriya/config/authority_approval.py's own load-bearing guard for the
    identical reason (see module docstring)."""


def _canonical_json_bytes(value: Any) -> bytes:
    """Local copy of this codebase's own established canonicalization
    idiom (kriya/workflow/checkpoint.py, kriya/workflow/proposal_store.py,
    kriya/config/authority_approval.py all keep their own copy rather than
    importing across layers - see authority_approval.py's own docstring
    for why) - sort_keys, no whitespace variance, ASCII-escaped."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_invocation_approval_binding_digest(
    workspace_id: str, tool_identity: MCPToolIdentity, capability_profile_digest: str,
) -> str:
    """TASK 2 - the canonical, deterministic binding: workspace identity +
    exact server identity + exact tool name + exact schema digest + exact
    TOOL-003 capability-profile digest. Description, annotations,
    arguments, result data, and the flattened registry display name are
    never inputs here (Invariant 5/14) - only `MCPToolIdentity`'s own three
    structural fields (never a server's advertised text) and the
    capability-profile digest (itself already description-free - see
    MCPCapabilityProfile's own canonicalization). Equivalent identity ->
    same digest; any material authority change -> a different digest."""
    payload = {
        "workspace_id": workspace_id,
        "server_identity": tool_identity.server_identity,
        "tool_name": tool_identity.tool_name,
        "schema_digest": tool_identity.schema_digest,
        "capability_profile_digest": capability_profile_digest,
    }
    return _sha256_hex(_canonical_json_bytes(payload))


@dataclass(frozen=True)
class MCPInvocationApprovalRecord:
    server_identity: str
    tool_name: str
    schema_digest: str
    capability_profile_digest: str
    binding_digest: str
    approved_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_identity": self.server_identity, "tool_name": self.tool_name,
            "schema_digest": self.schema_digest, "capability_profile_digest": self.capability_profile_digest,
            "binding_digest": self.binding_digest, "approved_at": self.approved_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MCPInvocationApprovalRecord":
        return MCPInvocationApprovalRecord(
            server_identity=d["server_identity"], tool_name=d["tool_name"],
            schema_digest=d["schema_digest"], capability_profile_digest=d["capability_profile_digest"],
            binding_digest=d["binding_digest"], approved_at=d["approved_at"],
        )


@dataclass(frozen=True)
class MCPInvocationApprovalArtifact:
    schema_version: int
    workspace_id: str
    records: Tuple[MCPInvocationApprovalRecord, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "records": [r.to_dict() for r in sorted(self.records, key=lambda r: r.binding_digest)],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MCPInvocationApprovalArtifact":
        try:
            records = tuple(MCPInvocationApprovalRecord.from_dict(r) for r in d["records"])
            return MCPInvocationApprovalArtifact(
                schema_version=d["schema_version"], workspace_id=d["workspace_id"], records=records,
            )
        except (KeyError, TypeError) as e:
            raise MCPApprovalArtifactError(f"malformed MCP invocation approval artifact: missing/invalid field {e}") from e


def empty_artifact(workspace_id: str) -> MCPInvocationApprovalArtifact:
    return MCPInvocationApprovalArtifact(schema_version=SCHEMA_VERSION, workspace_id=workspace_id, records=())


def add_approval(
    artifact: MCPInvocationApprovalArtifact, tool_identity: MCPToolIdentity, capability_profile_digest: str,
) -> MCPInvocationApprovalArtifact:
    """Adds (or replaces, if the exact same binding already exists) one
    approval record. Never mutates - returns a new artifact."""
    binding_digest = compute_invocation_approval_binding_digest(artifact.workspace_id, tool_identity, capability_profile_digest)
    remaining = tuple(r for r in artifact.records if r.binding_digest != binding_digest)
    new_record = MCPInvocationApprovalRecord(
        server_identity=tool_identity.server_identity, tool_name=tool_identity.tool_name,
        schema_digest=tool_identity.schema_digest, capability_profile_digest=capability_profile_digest,
        binding_digest=binding_digest, approved_at=_utc_now_iso(),
    )
    return MCPInvocationApprovalArtifact(
        schema_version=artifact.schema_version, workspace_id=artifact.workspace_id,
        records=remaining + (new_record,),
    )


def remove_approvals_for_identity(
    artifact: MCPInvocationApprovalArtifact, tool_identity: MCPToolIdentity,
) -> Tuple[MCPInvocationApprovalArtifact, int]:
    """TASK 11 - revocation matches on (server_identity, tool_name) alone,
    NOT on the full binding digest - an operator must be able to revoke an
    approval whose schema/capability-profile has already drifted (its
    binding digest no longer matches anything current), not just an
    exact-match record. Returns (new_artifact, count_removed)."""
    remaining = tuple(
        r for r in artifact.records
        if not (r.server_identity == tool_identity.server_identity and r.tool_name == tool_identity.tool_name)
    )
    removed = len(artifact.records) - len(remaining)
    return MCPInvocationApprovalArtifact(
        schema_version=artifact.schema_version, workspace_id=artifact.workspace_id, records=remaining,
    ), removed


def is_tool_approved(
    artifact: MCPInvocationApprovalArtifact, workspace_id: str,
    tool_identity: MCPToolIdentity, capability_profile_digest: str,
) -> bool:
    """The revalidation check (Task 7) - recomputes the binding digest
    fresh from the CURRENT identity/digest/workspace, exact match only
    against the artifact's own stored records. No partial match, no
    fuzzy/best-effort fallback - any one of the five bound fields
    differing produces a different digest and therefore denies."""
    if artifact.workspace_id != workspace_id:
        return False
    binding_digest = compute_invocation_approval_binding_digest(workspace_id, tool_identity, capability_profile_digest)
    return any(r.binding_digest == binding_digest for r in artifact.records)


# --- Storage: local (home-dir, outside workspace) ---------------------------

def _approval_home_dir() -> str:
    override = os.environ.get(ENV_HOME_OVERRIDE)
    base = override if override else os.path.join(os.path.expanduser("~"), ".kriya", "mcp_approvals")
    return os.path.realpath(base)


def validate_store_path_outside_workspace(store_path: str, workspace_root: str) -> None:
    real_dir = os.path.realpath(os.path.dirname(store_path) or ".")
    real_ws = os.path.realpath(workspace_root)
    if real_dir == real_ws or real_dir.startswith(real_ws + os.sep):
        raise MCPTrustPathInsideWorkspaceError(
            f"MCP invocation-approval store path {store_path!r} resolves inside the workspace "
            f"root {workspace_root!r} - this store must live outside the workspace a repository/"
            "checkout can populate; see kriya/mcp/invocation_approval.py's module docstring."
        )


def default_local_approval_path(workspace_root: str) -> str:
    home = _approval_home_dir()
    path = os.path.join(home, f"{workspace_identity(workspace_root)}.json")
    validate_store_path_outside_workspace(path, workspace_root)
    return path


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def save_approval_artifact(path: str, artifact: MCPInvocationApprovalArtifact) -> None:
    _atomic_write_json(path, artifact.to_dict())


def load_approval_artifact(path: str) -> Optional[MCPInvocationApprovalArtifact]:
    """None if the file doesn't exist (no approvals - the normal default
    state). Raises `MCPApprovalArtifactError` for a present-but-malformed/
    unparseable/wrong-schema file (fail closed - a caller in the
    invocation-decision path must treat any raise here as "no valid
    approval", never as approved; see `_load_fail_closed()` below)."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise MCPApprovalArtifactError(f"could not parse MCP invocation approval store at {path!r}: {e}") from e
    if not isinstance(raw, dict):
        raise MCPApprovalArtifactError(f"MCP invocation approval store at {path!r} is not a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise MCPApprovalArtifactError(
            f"unsupported MCP invocation approval schema_version {raw.get('schema_version')!r} "
            f"at {path!r} (expected {SCHEMA_VERSION}) - refusing to interpret an unknown/future format"
        )
    return MCPInvocationApprovalArtifact.from_dict(raw)


def _load_fail_closed(path: str) -> Optional[MCPInvocationApprovalArtifact]:
    try:
        return load_approval_artifact(path)
    except MCPApprovalArtifactError:
        # Present but corrupt/unknown-format - fail closed exactly like
        # "no approval", never raise into the invocation decision path
        # (TASK 15's own "corrupt artifact denied" requirement - the ONE
        # place this module ever swallows an error, and only into a
        # deterministic False, never a silent True).
        return None


def resolve_mcp_invocation_approval(
    workspace_root: str,
) -> Callable[[MCPToolIdentity, Optional[MCPCapabilityProfileIdentity]], bool]:
    """Returns a resolver closed over `workspace_root`, suitable for
    `ExecutionPolicy(mcp_invocation_approval_resolver=...)`. Reads the
    approval artifact FRESH from disk on every call - no caching anywhere
    in this function or the ones it calls (see module docstring's
    REVALIDATION section for why this is load-bearing, not incidental).
    Any exception raised here (a filesystem error mid-read, a bug) is
    deliberately NOT caught - it propagates through
    `ExecutionPolicy.evaluate()` to `MCPTool._run()`'s own existing
    fail-closed try/except (Task 15: "approval-store exception fails
    closed" - already satisfied by reusing that existing boundary, no new
    one needed here)."""
    workspace_id = workspace_identity(workspace_root)

    def _resolver(tool_identity: MCPToolIdentity, capability_identity: Optional[MCPCapabilityProfileIdentity]) -> bool:
        if capability_identity is None:
            # No capability-profile identity means the profile digest this
            # approval must be bound to cannot even be determined - never
            # approve a call that cannot be revalidated against a real
            # digest.
            return False
        path = default_local_approval_path(workspace_root)
        artifact = _load_fail_closed(path)
        if artifact is None:
            return False
        return is_tool_approved(artifact, workspace_id, tool_identity, capability_identity.profile_digest)

    return _resolver
