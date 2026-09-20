"""SEC-009 P2: explicit, durable, digest-bound approval for security-
authority configuration.

Core invariant this module enforces: approval grants authority to an EXACT
security-relevant effective configuration, never blanket trust to a
repository, a config file, a directory, or any future field/value. Changing
an approved security field invalidates approval; adding another security
field also requires new authority (Option A - exact approved security-field
SET digest, not per-field grants - see build_security_subset()'s docstring
for why).

Reuses this codebase's own established primitives rather than inventing new
ones: the tmp-file + os.replace() atomic-write idiom and the
json.dumps(sort_keys=True) canonicalization idiom (both from
kriya/workflow/checkpoint.py, also used verbatim by
kriya/workflow/proposal_store.py - re-implemented here as a small local
copy rather than imported across the config->workflow layer boundary,
since kriya/config/ is a lower layer than kriya/workflow/ and nothing else
in kriya/config/ imports from kriya/workflow/); kriya/control/
workspace_identity.py::workspace_identity() for workspace binding, unchanged;
kriya/cli.py::_redact_secrets()'s "keep key names, hide values" convention
for env-var display, mirrored (not imported, to avoid a config->cli
reverse-layer import) as _redact_display_value() below.

STORAGE LOCATION - the load-bearing security property of this module:
the local approval store lives OUTSIDE the workspace entirely (by default
under ~/.kriya/authority/<workspace_id>.json, override via
KRIYA_AUTHORITY_HOME), keyed by workspace_identity(). This is deliberate,
not incidental: kriya/config/config.py's own SEC-009 P1 threat model
already establishes that a git clone/checkout can carry arbitrary content
INTO the workspace directory an attacker controls - if the approval
artifact were ever read from inside the workspace (e.g. .kriya/authority/
approval.json), a malicious repository could ship both a hostile config
AND a self-consistent, digest-matching "approval" for it in the same
commit, and Kriya would honor an approval no real operator ever granted.
Anchoring the store outside the workspace makes this structurally
impossible: a checkout never writes outside its own target directory, so
repository content can never populate this location. The same rule applies
to an explicitly-supplied CI trust-file path (KRIYA_TRUST_FILE / --trust-file):
validate_trust_path_outside_workspace() rejects any trust-file path whose
realpath resolves inside the workspace root, closing the same recursion for
the CI case (an attacker-modifiable .github/workflows/ci.yml naming an
in-repo trust-file path is exactly this attack, spelled differently).

Deliberately NOT done here (would be forbidden self-authorization, not a
missing feature): there is no "CI runs `kriya authority approve`
non-interactively during pipeline setup" path. `approve` always computes
and persists a digest over what THIS invocation resolved - it never
accepts a caller-supplied digest to trust. A CI pipeline obtains a valid
trust artifact only by an operator running `approve` somewhere authority
already exists (their own machine, a bootstrap step outside the checked-out
branch's control) and shipping the resulting file via their own external
channel - Kriya never automates that hand-off.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kriya.config.authority import ConfigAuthorityViolation, get_field_value
from kriya.control.workspace_identity import workspace_identity

AUTHORITY_SCHEMA_VERSION = 1

ENV_HOME_OVERRIDE = "KRIYA_AUTHORITY_HOME"
ENV_TRUST_FILE = "KRIYA_TRUST_FILE"

_REDACTED = "***REDACTED***"


class TrustPathInsideWorkspaceError(ValueError):
    """A trust-artifact path (local store or explicit --trust-file/
    KRIYA_TRUST_FILE) resolved to somewhere inside the workspace root.
    Refusing this is the load-bearing guard against a repository shipping
    its own self-authorizing approval - see module docstring."""


class ApprovalArtifactError(ValueError):
    """Structural failure loading/parsing an approval artifact (bad schema,
    missing field, not valid JSON) - distinct from a substantive tamper/
    drift finding, which is reported via ApprovalCheckResult instead of
    raising (matches proposal_store.py's own ProposalStoreError-vs-
    structured-result split)."""


def _canonical_json_bytes(value: Any) -> bytes:
    """Deterministic bytes for identical semantics - the same
    sort_keys=True/no-whitespace-variance/ascii-safe idiom
    kriya/workflow/checkpoint.py::compute_config_fingerprint() and
    kriya/workflow/proposal_store.py::canonical_json_bytes() both already
    use, kept as a small local copy here rather than importing across the
    config->workflow layer boundary (see module docstring)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- Security subset: canonicalizing values without persisting secrets -----

def _redact_env_values(value: Any, salt: str, field_path: str) -> Any:
    """Mirrors kriya/cli.py::_redact_secrets()'s "keep key names, hide
    values" convention for mcp.*.env, but for DIGEST input rather than
    display: each env value is replaced by a salted, field-and-key-scoped
    digest of itself (not a fixed "***REDACTED***" placeholder) so a
    rotated secret still changes the resulting value_digest - an approval
    must become invalid if a secret changes, even though the secret itself
    is never persisted in plaintext anywhere in the artifact."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    env = result.get("env")
    if isinstance(env, dict):
        result["env"] = {
            k: _sha256_hex(f"{salt}\x00{field_path}.env.{k}\x00".encode("utf-8") + _canonical_json_bytes(v))
            for k, v in env.items()
        }
    return result


def compute_value_digest(salt: str, field_path: str, value: Any) -> str:
    """Domain-separated (salt + field_path prefix) so the same value at a
    different field path, or a different artifact's salt, never collides.
    mcp.*.env values are pre-digested individually (see
    _redact_env_values()) before the whole structure is hashed, so no
    secret value ever appears in the digest input in recoverable form -
    only other secret-shaped values would need the same treatment if a
    future field carries one; today only mcp.*.env does (grep-verified
    against kriya/config/authority.py's classification tables)."""
    safe_value = _redact_env_values(value, salt, field_path)
    blob = f"{salt}\x00{field_path}\x00".encode("utf-8") + _canonical_json_bytes(safe_value)
    return _sha256_hex(blob)


@dataclass(frozen=True)
class SecurityFieldRecord:
    field_path: str
    classification: str
    source: str
    value_digest: str
    env_keys: Tuple[str, ...] = ()  # names only (never values) - present only for mcp.<name> entries with an env dict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_path": self.field_path,
            "classification": self.classification,
            "source": self.source,
            "value_digest": self.value_digest,
            "env_keys": list(self.env_keys),
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "SecurityFieldRecord":
        return SecurityFieldRecord(
            field_path=d["field_path"], classification=d["classification"], source=d["source"],
            value_digest=d["value_digest"], env_keys=tuple(d.get("env_keys") or ()),
        )


def _env_keys_for(value: Any) -> Tuple[str, ...]:
    if isinstance(value, dict) and isinstance(value.get("env"), dict):
        return tuple(sorted(value["env"].keys()))
    return ()


def build_security_field_records(
    violations: List[ConfigAuthorityViolation], config_dict: Dict[str, Any], salt: str,
) -> Tuple[SecurityFieldRecord, ...]:
    """One record per violation, sorted by field_path for determinism. The
    EFFECTIVE (post-merge, post-runtime_profile-expansion) value is what
    gets digested - the same config_dict authority resolution itself
    inspects, per the P2 requirement to bind the expanded effective
    configuration, not raw source YAML."""
    records = []
    for v in violations:
        if "." in v.field_path:
            top_key, leaf_key = v.field_path.split(".", 1)
        else:
            top_key, leaf_key = None, v.field_path
        value = get_field_value(config_dict, top_key, leaf_key)
        records.append(SecurityFieldRecord(
            field_path=v.field_path, classification=v.classification.value, source=v.source.value,
            value_digest=compute_value_digest(salt, v.field_path, value),
            env_keys=_env_keys_for(value),
        ))
    return tuple(sorted(records, key=lambda r: r.field_path))


def compute_set_digest(records: Tuple[SecurityFieldRecord, ...]) -> str:
    """Digest over the COMPLETE sorted set of security field records - not
    a per-field digest. This is the Option A choice (exact approved
    security-field SET digest, not individually digest-bound grants): an
    approval is valid only when the CURRENT full set of security-relevant
    fields, in full, matches exactly what was approved. Adding a field
    (mcp.serverB alongside an approved mcp.serverA), removing one, or
    changing any one value all change this digest and invalidate the
    WHOLE approval, not just the affected entry. Simpler than per-field
    partial-coverage tracking, and structurally guarantees the required
    invariant ("addition/change/removal affecting security authority
    cannot silently widen authority") rather than relying on a matching
    algorithm to get every edge case right - the cost is that a legitimate
    operator adding a second, unrelated MCP server must re-approve the
    whole set, which is the core invariant working as intended, not a
    limitation to work around."""
    return _sha256_hex(_canonical_json_bytes([r.to_dict() for r in records]))


# --- Approval artifact -------------------------------------------------------

@dataclass(frozen=True)
class ApprovalArtifact:
    schema_version: int
    authority_schema_version: int
    workspace_id: str
    salt: str
    records: Tuple[SecurityFieldRecord, ...]
    set_digest: str
    artifact_digest: str
    approved_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority_schema_version": self.authority_schema_version,
            "workspace_id": self.workspace_id,
            "salt": self.salt,
            "security_subset": {
                "records": [r.to_dict() for r in self.records],
                "set_digest": self.set_digest,
            },
            "artifact_digest": self.artifact_digest,
            "approved_at": self.approved_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ApprovalArtifact":
        try:
            subset = d["security_subset"]
            records = tuple(SecurityFieldRecord.from_dict(r) for r in subset["records"])
            return ApprovalArtifact(
                schema_version=d["schema_version"], authority_schema_version=d["authority_schema_version"],
                workspace_id=d["workspace_id"], salt=d["salt"], records=records,
                set_digest=subset["set_digest"], artifact_digest=d["artifact_digest"],
                approved_at=d["approved_at"],
            )
        except (KeyError, TypeError) as e:
            raise ApprovalArtifactError(f"malformed approval artifact: missing/invalid field {e}") from e


def _artifact_digest_input(artifact_without_digest: Dict[str, Any]) -> bytes:
    return _canonical_json_bytes(artifact_without_digest)


def _compute_artifact_digest(
    schema_version: int, authority_schema_version: int, workspace_id: str, salt: str,
    records: Tuple[SecurityFieldRecord, ...], set_digest: str, approved_at: str,
) -> str:
    payload = {
        "schema_version": schema_version, "authority_schema_version": authority_schema_version,
        "workspace_id": workspace_id, "salt": salt,
        "security_subset": {"records": [r.to_dict() for r in records], "set_digest": set_digest},
        "approved_at": approved_at,
    }
    return _sha256_hex(_artifact_digest_input(payload))


def build_approval_artifact(
    violations: List[ConfigAuthorityViolation], config_dict: Dict[str, Any], workspace_root: str,
) -> ApprovalArtifact:
    """Builds (but does not persist) a fresh approval artifact for exactly
    the violations passed in, computed against config_dict's effective
    values. A new random salt every time - two approvals of the identical
    logical config never share a salt, so value_digests can't be
    cross-correlated across separately-approved workspaces/artifacts."""
    salt = secrets.token_hex(16)
    records = build_security_field_records(violations, config_dict, salt)
    set_digest = compute_set_digest(records)
    approved_at = _utc_now_iso()
    ws_id = workspace_identity(workspace_root)
    artifact_digest = _compute_artifact_digest(
        AUTHORITY_SCHEMA_VERSION, AUTHORITY_SCHEMA_VERSION, ws_id, salt, records, set_digest, approved_at,
    )
    return ApprovalArtifact(
        schema_version=AUTHORITY_SCHEMA_VERSION, authority_schema_version=AUTHORITY_SCHEMA_VERSION,
        workspace_id=ws_id, salt=salt, records=records, set_digest=set_digest,
        artifact_digest=artifact_digest, approved_at=approved_at,
    )


def verify_artifact_integrity(artifact: ApprovalArtifact) -> bool:
    """Tamper check only - recomputes artifact_digest from the artifact's
    own stored fields and compares. Does NOT check whether it still
    matches the current live configuration (see is_approval_current_for()
    for that) or whether it belongs to this workspace (see
    load_local_approval())."""
    recomputed = _compute_artifact_digest(
        artifact.schema_version, artifact.authority_schema_version, artifact.workspace_id,
        artifact.salt, artifact.records, artifact.set_digest, artifact.approved_at,
    )
    return recomputed == artifact.artifact_digest


def is_approval_current_for(
    artifact: ApprovalArtifact, violations: List[ConfigAuthorityViolation], config_dict: Dict[str, Any],
    workspace_root: str,
) -> bool:
    """The live drift check: recomputes the CURRENT security field records
    (using the artifact's own stored salt, so the same values reproduce
    the same digests) and compares the resulting set_digest against what
    was approved. Any difference at all - a value changed, a field was
    added, a field was removed, provenance changed - invalidates the whole
    approval (Option A, see compute_set_digest()'s docstring). Also
    requires workspace_id to match (workspace_identity() already uses
    realpath, so a symlink substitution changes it) and both schema
    versions to match exactly."""
    if artifact.schema_version != AUTHORITY_SCHEMA_VERSION or artifact.authority_schema_version != AUTHORITY_SCHEMA_VERSION:
        return False
    if artifact.workspace_id != workspace_identity(workspace_root):
        return False
    if not verify_artifact_integrity(artifact):
        return False
    current_records = build_security_field_records(violations, config_dict, artifact.salt)
    current_set_digest = compute_set_digest(current_records)
    return current_set_digest == artifact.set_digest


# --- Storage: local (home-dir, outside workspace) and explicit trust file --

def _authority_home_dir() -> str:
    override = os.environ.get(ENV_HOME_OVERRIDE)
    base = override if override else os.path.join(os.path.expanduser("~"), ".kriya", "authority")
    return os.path.realpath(base)


def validate_trust_path_outside_workspace(trust_path: str, workspace_root: str) -> None:
    """The load-bearing guard (see module docstring): refuses any trust-
    artifact path - the default local store OR an explicit --trust-file/
    KRIYA_TRUST_FILE - whose real target resolves inside the workspace
    root. Checked with realpath so a symlink can't be used to smuggle an
    in-workspace path past an apparent outside-workspace location either."""
    real_trust_dir = os.path.realpath(os.path.dirname(trust_path) or ".")
    real_ws = os.path.realpath(workspace_root)
    if real_trust_dir == real_ws or real_trust_dir.startswith(real_ws + os.sep):
        raise TrustPathInsideWorkspaceError(
            f"trust-artifact path {trust_path!r} resolves inside the workspace root {workspace_root!r} - "
            "a trust artifact must live outside the workspace a repository/checkout can populate; "
            "see kriya/config/authority_approval.py's module docstring for why this is refused, not just discouraged."
        )


def default_local_approval_path(workspace_root: str) -> str:
    home = _authority_home_dir()
    validate_trust_path_outside_workspace(os.path.join(home, "x.json"), workspace_root)
    return os.path.join(home, f"{workspace_identity(workspace_root)}.json")


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def save_approval_artifact(path: str, artifact: ApprovalArtifact) -> None:
    _atomic_write_json(path, artifact.to_dict())


def load_approval_artifact(path: str) -> Optional[ApprovalArtifact]:
    """None if the file simply doesn't exist (no approval - the normal,
    expected P1-equivalent state). Raises ApprovalArtifactError for a file
    that exists but is malformed/unparseable/wrong-schema (fail closed -
    the caller treats any raise here as "no valid approval", never as
    approved) - see resolve_with_approval()."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ApprovalArtifactError(f"could not parse approval artifact at {path!r}: {e}") from e
    if not isinstance(raw, dict):
        raise ApprovalArtifactError(f"approval artifact at {path!r} is not a JSON object")
    if raw.get("schema_version") != AUTHORITY_SCHEMA_VERSION:
        raise ApprovalArtifactError(
            f"unsupported approval schema_version {raw.get('schema_version')!r} at {path!r} "
            f"(expected {AUTHORITY_SCHEMA_VERSION})"
        )
    return ApprovalArtifact.from_dict(raw)


def revoke_local_approval(workspace_root: str) -> bool:
    """Deletes the local approval artifact for this workspace, if any.
    Returns True if a file was actually removed, False if there was
    nothing to revoke (idempotent - calling revoke twice is not an
    error)."""
    path = default_local_approval_path(workspace_root)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# --- Resolution: the function load_config() calls ---------------------------

def _load_artifact_fail_closed(path: Optional[str]) -> Optional[ApprovalArtifact]:
    if not path:
        return None
    try:
        return load_approval_artifact(path)
    except ApprovalArtifactError:
        # Present but malformed/tampered-at-the-schema-level/wrong-schema -
        # fail closed exactly like "no approval", never raise out of
        # load_config()'s normal fail-closed-to-denial path.
        return None


def resolve_with_approval(
    violations: List[ConfigAuthorityViolation], config_dict: Dict[str, Any], workspace_root: str,
    trust_file: Optional[str] = None,
) -> List[ConfigAuthorityViolation]:
    """Returns the violations that remain DENIED after checking for a valid
    covering approval. Empty violations in -> empty out, trivially (no
    lookup needed - matches P1 exactly when there's nothing to approve).
    Checks, in order: an explicit trust_file (CI/operator-supplied,
    already validated to be outside the workspace by the caller - see
    kriya/config/config.py), then the local per-workspace store. Either
    one, if present, valid, untampered, and currently matching the exact
    live security-field set, covers ALL current violations (Option A - the
    whole set or nothing). A present-but-invalid/stale/tampered artifact
    at either location does NOT get silently skipped in favor of the
    other - if trust_file was supplied it is authoritative (CI has no
    interactive fallback), only falling through to the local store when no
    trust_file was supplied at all."""
    if not violations:
        return []

    if trust_file:
        artifact = _load_artifact_fail_closed(trust_file)
        if artifact and is_approval_current_for(artifact, violations, config_dict, workspace_root):
            return []
        return violations

    local_path = default_local_approval_path(workspace_root)
    artifact = _load_artifact_fail_closed(local_path)
    if artifact and is_approval_current_for(artifact, violations, config_dict, workspace_root):
        return []
    return violations


# --- Display: safe, redacted representation for inspect/approve CLI --------

def _redact_display_value(field_path: str, value: Any) -> Any:
    """Mirrors kriya/cli.py::_redact_secrets() - env values become
    "***REDACTED***" (key names kept), api_key values too. Kept as a local
    copy (see module docstring) rather than an import across layers."""
    if isinstance(value, dict):
        redacted = {}
        for k, v in value.items():
            if k == "api_key" and isinstance(v, str) and v:
                redacted[k] = _REDACTED
            elif k == "env" and isinstance(v, dict):
                redacted[k] = {ek: (_REDACTED if ev else ev) for ek, ev in v.items()}
            else:
                redacted[k] = _redact_display_value(field_path, v)
        return redacted
    if isinstance(value, list):
        return [_redact_display_value(field_path, item) for item in value]
    return value


@dataclass(frozen=True)
class PendingFieldDisplay:
    field_path: str
    classification: str
    source: str
    redacted_value: Any


def describe_pending(violations: List[ConfigAuthorityViolation], config_dict: Dict[str, Any]) -> List[PendingFieldDisplay]:
    """Human-facing, secret-safe view of the current violation set - used
    by both `kriya authority inspect` (display only) and `kriya authority
    approve` (display, then re-resolved fresh immediately before
    persisting - never reuses inspect's own output as approve's input,
    closing the TOCTOU window by construction: approve always recomputes)."""
    out = []
    for v in sorted(violations, key=lambda x: x.field_path):
        if "." in v.field_path:
            top_key, leaf_key = v.field_path.split(".", 1)
        else:
            top_key, leaf_key = None, v.field_path
        value = get_field_value(config_dict, top_key, leaf_key)
        out.append(PendingFieldDisplay(
            field_path=v.field_path, classification=v.classification.value, source=v.source.value,
            redacted_value=_redact_display_value(v.field_path, value),
        ))
    return out
