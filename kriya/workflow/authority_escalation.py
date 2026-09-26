"""PRD-028: evidence-based, revision-bound member authority expansion.

When the Developer lacks the exact source of a member it must change, Kriya
may resolve that member read-only and expand the Developer's context with
it. The sources are failure evidence, the model's own rejected SEARCH text,
and DEV-INV investigation. The deterministic control plane, not the model,
decides whether the expansion is granted. Every decision is an
``AuthorityExpansion`` record carrying:

- the exact member, and the language adapter/capability that resolved it;
- ``source_revision``: the content the member was cut from;
- ``source_origin``: PRISTINE when that content is byte-identical to the
  immutable baseline (the workspace file), CANDIDATE when it is this run's
  own candidate in the worktree;
- ``pristine_revision`` and ``member_in_pristine``: computed ONLY from the
  baseline, so any claim about what the repository contains comes from
  pristine source, never from candidate text;
- the evidence the request rests on;
- GRANTED or INDETERMINATE, with a reason code;
- the mutation boundary it stays inside (it must already be write-authorized).

Two separations hold:
- **Context/source expansion is not mutation-scope expansion.** A member
  outside the already-authorized write scope is INDETERMINATE, and the scope
  object is never touched.
- **Edit-target source versus pristine source.** A retry edits the
  candidate in the worktree, so member boundaries come from the current
  (worktree-first) content, the bytes an anchored edit must match. Whether
  that content is the pristine repository is recorded, not assumed.

An unsupported language or an ambiguous member is INDETERMINATE and never
becomes whole-file authority. A grant is revalidated wherever it is consumed
(``expansion_is_current``): a changed source or baseline revision voids it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

from kriya.workflow.edit_safety import content_revision
from kriya.workflow.language_adapters import Capability, CapabilityStatus, adapter_for

GRANTED = "GRANTED"
INDETERMINATE = "INDETERMINATE"

ORIGIN_PRISTINE = "PRISTINE"
ORIGIN_CANDIDATE = "CANDIDATE"

REASON_GRANTED = "MEMBER_AUTHORITY_GRANTED"
REASON_OUT_OF_SCOPE = "OUT_OF_MUTATION_SCOPE"
REASON_UNSUPPORTED_LANGUAGE = "LANGUAGE_CAPABILITY_UNSUPPORTED"
REASON_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
REASON_MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
REASON_MEMBER_AMBIGUOUS = "MEMBER_AMBIGUOUS"
REASON_STALE = "SOURCE_REVISION_CHANGED"

MUTATION_BOUNDARY = "authorized_write_scope"


@dataclass(frozen=True)
class AuthorityExpansion:
    path: str
    member_id: str
    outcome: str
    reason_code: str
    language: Optional[str]
    capability: str
    source_origin: Optional[str]
    source_revision: str
    pristine_revision: Optional[str]
    member_in_pristine: bool
    in_write_scope: bool
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def granted(self) -> bool:
        return self.outcome == GRANTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "member_id": self.member_id, "outcome": self.outcome,
            "reason_code": self.reason_code, "language": self.language, "capability": self.capability,
            "source_origin": self.source_origin, "source_revision": self.source_revision,
            "pristine_revision": self.pristine_revision, "member_in_pristine": self.member_in_pristine,
            "mutation_boundary": MUTATION_BOUNDARY, "in_write_scope": self.in_write_scope,
            "evidence": dict(self.evidence),
        }


def _read(root: Optional[str], path: str) -> Optional[str]:
    if not root:
        return None
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _current_source(workspace_path: str, worktree_path: Optional[str], path: str) -> Optional[str]:
    """The edit target: the worktree candidate when present, else the
    workspace (CurrentSourceResolver's own worktree-first rule)."""
    current = _read(worktree_path, path)
    return current if current is not None else _read(workspace_path, path)


def _member_matches(path: str, content: str, member_id: str) -> Optional[int]:
    from kriya.workflow.context_source import boundaries_matching_member_id, member_boundaries_for

    boundaries = member_boundaries_for(path, content)
    if boundaries is None:
        return None
    return len(boundaries_matching_member_id(boundaries, member_id))


def request_member_authority(
    *, path: str, member_id: str, workspace_path: str, worktree_path: Optional[str],
    authorized_paths: Iterable[str], evidence: Optional[Dict[str, Any]] = None,
) -> AuthorityExpansion:
    """Decide one read-only member-authority expansion. Pure decision: it
    never writes, and never changes ``authorized_paths``."""
    authorized = path in set(authorized_paths)
    adapter = adapter_for(path)
    language = adapter.language if adapter is not None else None
    pristine = _read(workspace_path, path)
    pristine_revision = content_revision(pristine) if pristine is not None else None
    member_in_pristine = bool(pristine is not None and (_member_matches(path, pristine, member_id) or 0) == 1)

    def decide(outcome: str, reason: str, *, origin: Optional[str] = None, revision: str = "") -> AuthorityExpansion:
        return AuthorityExpansion(
            path=path, member_id=member_id, outcome=outcome, reason_code=reason, language=language,
            capability=Capability.MEMBER_BOUNDARIES.value, source_origin=origin, source_revision=revision,
            pristine_revision=pristine_revision, member_in_pristine=member_in_pristine,
            in_write_scope=authorized, evidence=dict(evidence or {}),
        )

    if not authorized:
        return decide(INDETERMINATE, REASON_OUT_OF_SCOPE)
    if adapter is None or adapter.status(Capability.MEMBER_BOUNDARIES) is CapabilityStatus.UNSUPPORTED:
        return decide(INDETERMINATE, REASON_UNSUPPORTED_LANGUAGE)
    current = _current_source(workspace_path, worktree_path, path)
    if current is None:
        return decide(INDETERMINATE, REASON_SOURCE_UNAVAILABLE)
    revision = content_revision(current)
    origin = ORIGIN_PRISTINE if revision == pristine_revision else ORIGIN_CANDIDATE
    matches = _member_matches(path, current, member_id)
    if not matches:
        return decide(INDETERMINATE, REASON_MEMBER_NOT_FOUND, origin=origin, revision=revision)
    if matches > 1:
        return decide(INDETERMINATE, REASON_MEMBER_AMBIGUOUS, origin=origin, revision=revision)
    return decide(GRANTED, REASON_GRANTED, origin=origin, revision=revision)


def expansion_is_current(expansion: AuthorityExpansion, workspace_path: str, worktree_path: Optional[str]) -> bool:
    """A grant is usable only while the source it was cut from and the
    baseline it was compared with are both unchanged."""
    if not expansion.granted:
        return False
    current = _current_source(workspace_path, worktree_path, expansion.path)
    pristine = _read(workspace_path, expansion.path)
    return (
        current is not None
        and content_revision(current) == expansion.source_revision
        and (content_revision(pristine) if pristine is not None else None) == expansion.pristine_revision
    )


def grant_member_hints(
    member_hints: Dict[str, Iterable[str]], *, workspace_path: str, worktree_path: Optional[str],
    authorized_paths: Iterable[str], evidence: Dict[str, Any],
) -> Tuple[Dict[str, list], Tuple[AuthorityExpansion, ...]]:
    """Run every hinted member through ``request_member_authority``. Returns
    the hints restricted to GRANTED members, plus every decision record."""
    authorized = tuple(authorized_paths)
    granted: Dict[str, list] = {}
    records = []
    for path in sorted(member_hints):
        for member_id in sorted(set(member_hints[path])):
            record = request_member_authority(
                path=path, member_id=member_id, workspace_path=workspace_path, worktree_path=worktree_path,
                authorized_paths=authorized, evidence=evidence,
            )
            records.append(record)
            if record.granted:
                granted.setdefault(path, []).append(member_id)
    return granted, tuple(records)
