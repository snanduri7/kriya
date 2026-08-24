"""Approved-source promotion - MA4.11 of the control-plane implementation
plan (see kriya/policy/__init__.py for MA4's overall principle;
kriya/policy/trust.py for MA4.10's TrustLevel/TrustedContent this module
builds on).

Priority 2 of the trust ladder ("Approved project / organization agent
policy") sits above ordinary REPOSITORY content but below an actual user
instruction or platform policy - it exists for repository-resident content
(a vetted policy file, an approved skill, a reviewed script) that a human
has explicitly signed off on, not content that earns elevated trust just by
existing in the repo. This module is the mechanism for that sign-off: a
per-workspace manifest at `.kriya/policy/approved-sources.json` records
(relative path -> SHA-256 of its approved bytes). Content resolves to
APPROVED_PROJECT_POLICY only when its CURRENT bytes hash to exactly what was
recorded at approval time; if the path is still listed but the hash no
longer matches - the file changed since it was approved - elevated trust is
lost outright and resolution falls back to the caller-supplied default
level. There is no partial credit and no "close enough": one byte of drift
is a full loss of the elevated rung, by design (the same content shape a
prompt-injection edit would produce).

promote_source() is the only way an entry enters the manifest, and it is a
deliberate, explicit action - nothing in this module or in resolve_trust_
level() ever writes an approval on content's own say-so. There is no real
caller wired up yet (mirrors MA4.10's own scope): nothing in today's Kriya
pipeline constructs TrustedContent objects from real content sources, so
there is nothing for resolve_trust_level() to be called from yet. That
wiring, and any `kriya policy approve <path>` style CLI surface for
promote_source(), is later MA4 work.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

from kriya.policy.trust import TrustLevel, outranks

logger = logging.getLogger(__name__)

_MANIFEST_RELATIVE_PATH = os.path.join(".kriya", "policy", "approved-sources.json")


@dataclass(frozen=True)
class ApprovedSourceEntry:
    """One manifest record. `path` is always stored normalized (forward
    slashes, no leading "./") so the same logical path can't accidentally
    create two entries because of platform or caller formatting
    differences."""

    path: str
    sha256: str
    note: Optional[str] = None


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _normalize_relative_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _manifest_path(workspace_path: str) -> str:
    return os.path.join(workspace_path, _MANIFEST_RELATIVE_PATH)


def load_manifest(workspace_path: str) -> Dict[str, ApprovedSourceEntry]:
    """Never raises. A missing manifest is the normal, expected state for a
    workspace with no approvals yet - an empty dict, not an error. A
    present-but-malformed manifest fails CLOSED (also an empty dict, logged)
    rather than risking a partially-parsed structure granting elevated trust
    it was never actually given."""

    path = _manifest_path(workspace_path)
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {
            entry_path: ApprovedSourceEntry(path=entry_path, sha256=fields["sha256"], note=fields.get("note"))
            for entry_path, fields in raw.items()
        }
    except Exception:
        logger.warning("Failed to parse approved-sources manifest at %s - treating as empty", path, exc_info=True)
        return {}


def save_manifest(workspace_path: str, entries: Dict[str, ApprovedSourceEntry]) -> None:
    path = _manifest_path(workspace_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    serializable = {entry.path: {"sha256": entry.sha256, "note": entry.note} for entry in entries.values()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, sort_keys=True)
        f.write("\n")


def promote_source(
    workspace_path: str, relative_path: str, content: bytes, note: Optional[str] = None
) -> ApprovedSourceEntry:
    """Explicitly approves `content` at `relative_path`, recording its
    current hash. Overwrites any prior entry for the same path - approval is
    always of the CURRENT bytes, never cumulative."""

    normalized_path = _normalize_relative_path(relative_path)
    entry = ApprovedSourceEntry(path=normalized_path, sha256=compute_sha256(content), note=note)

    entries = load_manifest(workspace_path)
    entries[normalized_path] = entry
    save_manifest(workspace_path, entries)
    return entry


def resolve_trust_level(
    workspace_path: str, relative_path: str, content: bytes, default_level: TrustLevel
) -> TrustLevel:
    """Read-only: never writes to the manifest, never promotes anything
    itself. Raises the result to APPROVED_PROJECT_POLICY only when the path
    is a recorded approval AND its hash matches `content` exactly; a path
    that's merely listed with a stale hash gets no credit at all - it falls
    all the way back to `default_level`, not some intermediate level.

    Never LOWERS trust: if `default_level` already outranks
    APPROVED_PROJECT_POLICY (i.e. it's PLATFORM or USER_INSTRUCTION - actual
    platform policy or a real user instruction, never repository content),
    this function is a no-op and returns it unchanged. Promotion only ever
    raises repository-origin content up to Priority 2; it can never be used
    to talk a higher-trust source down to it."""

    if outranks(default_level, TrustLevel.APPROVED_PROJECT_POLICY):
        return default_level

    normalized_path = _normalize_relative_path(relative_path)
    entries = load_manifest(workspace_path)
    entry = entries.get(normalized_path)
    if entry is None:
        return default_level

    if entry.sha256 != compute_sha256(content):
        return default_level

    return TrustLevel.APPROVED_PROJECT_POLICY
