"""BACKEND-READINESS-004 (owner decision D3): the operator's sealed requirement disposition.

A goal may state something no deterministic authority can ever prove or that should not be an obligation at all: a
false premise ("make EXCEL skip empty lines" when the documented, tested behaviour is the opposite), a historical
remark ("both cases worked in the version we used before"), an informational aside, a clause outside the task's
scope. The contract compiler cannot know that; only the operator may say so, and never silently: a disposition is an
operator-authored JSON artifact OUTSIDE the workspace (SEC-009's rule for operator authority), bound to the exact goal,
requirement set, requirement text, base revision and - optionally - one claim of the statement, with a reason, the
declared operator identity and the evidence the operator relied on. It is read, validated and stored content-
addressed under ``<state>/requirement-dispositions/`` before any model call, bound to the engine by the CLI only
(``--requirement-disposition``); its digest joins the verification contract, so a disposition added or changed after
a candidate exists is another contract (both resume identities fail closed: VERIFICATION_CONTRACT_CHANGED).

What a disposition does: a whole-statement disposition removes the statement from the mandatory set and reports it
``DISPOSITIONED`` with its kind and reason (never "satisfied", never "verified"); a claim-level disposition removes
exactly that claim from the statement's obligation, every other claim keeps its closer. The statement itself stays in
the requirement set, in lineage, in the Developer's requirements block and in every report: nothing is deleted.

What it never does: no model output can produce one (there is no API from model text to this artifact; a verdict
that merely says "dispositioned" is UNKNOWN, see ``requirement_outcomes``), no run can add one after sealing without
becoming another run, and no disposition is ever applied to words other than the ones its text digest names.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from kriya.workflow.requirements import CLAIM_KINDS, RequirementSet, goal_identity

DISPOSITION_FORMAT = "kriya.requirement_disposition/1"
DISPOSITION_STORE_DIR = "requirement-dispositions"

REJECTED_FALSE_PREMISE = "REJECTED_FALSE_PREMISE"
HISTORICAL_CONTEXT = "HISTORICAL_CONTEXT"
INFORMATIONAL_CONTEXT = "INFORMATIONAL_CONTEXT"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
DISPOSITION_KINDS = frozenset({REJECTED_FALSE_PREMISE, HISTORICAL_CONTEXT, INFORMATIONAL_CONTEXT, OUT_OF_SCOPE})

DISPOSITION_INVALID = "DISPOSITION_INVALID"
DISPOSITION_GOAL_MISMATCH = "DISPOSITION_GOAL_MISMATCH"
DISPOSITION_REQUIREMENT_SET_MISMATCH = "DISPOSITION_REQUIREMENT_SET_MISMATCH"
DISPOSITION_BASE_MISMATCH = "DISPOSITION_BASE_MISMATCH"
DISPOSITION_TEXT_MISMATCH = "DISPOSITION_TEXT_MISMATCH"

_DOCUMENT_FIELDS = frozenset({"format", "operator", "dispositions"})
_OPERATOR_FIELDS = frozenset({"identity", "issued_at"})
_ENTRY_FIELDS = frozenset({"requirement_id", "requirement_text_sha256", "goal_sha256", "requirement_set_sha256",
                           "base_revision", "claim", "disposition", "reason", "evidence"})
_REQ_ID = re.compile(r"^REQ-C?\d+$")


class RequirementDispositionError(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


def _refuse(code: str, message: str) -> RequirementDispositionError:
    return RequirementDispositionError(code, message)


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DispositionEntry:
    requirement_id: str
    requirement_text_sha256: str
    claim: Optional[str]  # None: the whole statement; else exactly one of CLAIM_KINDS
    disposition: str
    reason: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement_id": self.requirement_id, "requirement_text_sha256": self.requirement_text_sha256,
                "claim": self.claim, "disposition": self.disposition, "reason": self.reason,
                "evidence": list(self.evidence)}


@dataclass(frozen=True)
class RequirementDispositions:
    digest: str
    stored_path: str
    source_name: str
    operator: Mapping[str, str]
    goal_sha256: str
    requirement_set_sha256: str
    base_revision: str
    entries: Mapping[str, Tuple[DispositionEntry, ...]]  # requirement id -> its entries

    def whole(self, requirement_id: str) -> Optional[DispositionEntry]:
        return next((e for e in self.entries.get(requirement_id, ()) if e.claim is None), None)

    def for_claim(self, requirement_id: str, claim: str) -> Optional[DispositionEntry]:
        return next((e for e in self.entries.get(requirement_id, ()) if e.claim == claim), None)

    def binds(self, requirement_id: str, text: str) -> bool:
        """Re-checked at read time, like a B3 approval: the disposition is for exactly these words."""
        entries = self.entries.get(requirement_id, ())
        return bool(entries) and all(e.requirement_text_sha256 == text_sha256(text) for e in entries)

    def identity(self) -> Dict[str, Any]:
        """What the contract digest sees: the content digest and every binding, never where the store lives."""
        return {"format": DISPOSITION_FORMAT, "digest": self.digest, "operator": dict(self.operator),
                "goal_sha256": self.goal_sha256, "requirement_set_sha256": self.requirement_set_sha256,
                "base_revision": self.base_revision,
                "entries": {rid: [e.to_dict() for e in entries] for rid, entries in sorted(self.entries.items())}}

    def to_dict(self) -> Dict[str, Any]:
        return {**self.identity(), "stored_path": self.stored_path, "source_name": self.source_name}


def load_requirement_dispositions(
    path: str, requirement_set: RequirementSet, goal: str, *, state_root: str, workspace: str,
    base_revision: Optional[str],
) -> RequirementDispositions:
    """Read, validate, store and bind the operator's disposition file before
    any model call. Typed refusal for anything that does not bind exactly."""
    from kriya.platform.filesystem_semantics import PathRelation, path_relation

    real = os.path.realpath(path)
    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:
        raise _refuse(DISPOSITION_INVALID, "the disposition file must live outside the workspace (a repository can "
                                           "never ship its own operator authority)")
    try:
        with open(real, "rb") as handle:
            data = handle.read()
        document = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _refuse(DISPOSITION_INVALID, f"unreadable disposition file: {error}") from error
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS or document.get("format") != DISPOSITION_FORMAT:
        raise _refuse(DISPOSITION_INVALID, f"expected exactly the fields {sorted(_DOCUMENT_FIELDS)} with format "
                                           f"{DISPOSITION_FORMAT!r}")
    operator = document["operator"]
    if (not isinstance(operator, dict) or set(operator) != _OPERATOR_FIELDS
            or not all(isinstance(operator[k], str) and operator[k].strip() for k in _OPERATOR_FIELDS)):
        raise _refuse(DISPOSITION_INVALID, "operator must be {identity: <non-empty>, issued_at: <non-empty>}")
    raw_entries = document["dispositions"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise _refuse(DISPOSITION_INVALID, "dispositions must list at least one entry")
    if not base_revision:
        raise _refuse(DISPOSITION_BASE_MISMATCH, "the workspace has no base revision to bind the disposition to")
    goal_digest = goal_identity(goal)
    entries: Dict[str, List[DispositionEntry]] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != _ENTRY_FIELDS:
            raise _refuse(DISPOSITION_INVALID, f"every disposition has exactly the fields {sorted(_ENTRY_FIELDS)}")
        rid = raw["requirement_id"]
        if not isinstance(rid, str) or not _REQ_ID.match(rid):
            raise _refuse(DISPOSITION_INVALID, f"requirement_id must be one REQ id, never a pattern: {rid!r}")
        requirement = requirement_set.get(rid)
        if requirement is None:
            raise _refuse(DISPOSITION_INVALID, f"{rid} is not a requirement of this goal ({', '.join(requirement_set.ids)})")
        if raw["goal_sha256"] != goal_digest or raw["goal_sha256"] != requirement_set.goal_digest:
            raise _refuse(DISPOSITION_GOAL_MISMATCH, f"{rid}: the disposition was made for another goal")
        if raw["requirement_set_sha256"] != requirement_set.digest:
            raise _refuse(DISPOSITION_REQUIREMENT_SET_MISMATCH, f"{rid}: the disposition was made for another requirement set")
        if raw["requirement_text_sha256"] != text_sha256(requirement.text):
            raise _refuse(DISPOSITION_TEXT_MISMATCH, f"{rid}: requirement_text_sha256 does not match the requirement's exact text")
        if raw["base_revision"] != base_revision:
            raise _refuse(DISPOSITION_BASE_MISMATCH, f"{rid}: the disposition binds base revision {raw['base_revision']!r}, "
                                                     f"the workspace is at {base_revision!r}")
        claim = raw["claim"]
        if claim is not None and claim not in CLAIM_KINDS:
            raise _refuse(DISPOSITION_INVALID, f"{rid}: claim must be null (the whole statement) or one of {sorted(CLAIM_KINDS)}")
        if raw["disposition"] not in DISPOSITION_KINDS:
            raise _refuse(DISPOSITION_INVALID, f"{rid}: disposition must be one of {sorted(DISPOSITION_KINDS)}")
        if not isinstance(raw["reason"], str) or not raw["reason"].strip():
            raise _refuse(DISPOSITION_INVALID, f"{rid}: reason must say why the operator dispositions this statement")
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
            raise _refuse(DISPOSITION_INVALID, f"{rid}: evidence must be a list of strings (paths, digests, references)")
        existing = entries.get(rid, [])
        if any(e.claim == claim for e in existing) or (existing and (claim is None or any(e.claim is None for e in existing))):
            raise _refuse(DISPOSITION_INVALID, f"{rid}: one disposition per statement or one per claim, never both and never twice")
        entries.setdefault(rid, []).append(DispositionEntry(rid, raw["requirement_text_sha256"], claim, raw["disposition"],
                                                            raw["reason"].strip(), tuple(evidence)))
    digest = hashlib.sha256(data).hexdigest()
    store = os.path.join(state_root, DISPOSITION_STORE_DIR)
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}.json")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, stored)
    return RequirementDispositions(
        digest=digest, stored_path=stored, source_name=os.path.basename(path),
        operator={"identity": operator["identity"].strip(), "issued_at": operator["issued_at"].strip()},
        goal_sha256=goal_digest, requirement_set_sha256=requirement_set.digest, base_revision=base_revision,
        entries={rid: tuple(items) for rid, items in entries.items()},
    )


def bound_dispositions(engine: Any) -> Optional[RequirementDispositions]:
    """The dispositions bound to ``engine`` for this run (the CLI sets
    ``WorkflowEngine.requirement_dispositions``), or None - never anything
    that merely looks like one."""
    bound = getattr(engine, "requirement_dispositions", None)
    return bound if isinstance(bound, RequirementDispositions) else None


def disposition_template(
    requirement_set: RequirementSet, goal: str, targets: Iterable[Tuple[str, Optional[str]]], *, base_revision: str,
    operator_identity: str = "", issued_at: str = "",
) -> Dict[str, Any]:
    """An UNSEALED document for the operator to complete: every binding filled
    in, ``disposition`` and ``reason`` empty - only the operator writes them."""
    return {"format": DISPOSITION_FORMAT, "operator": {"identity": operator_identity, "issued_at": issued_at},
            "dispositions": [{"requirement_id": rid, "requirement_text_sha256": text_sha256(requirement_set.get(rid).text),
                              "goal_sha256": goal_identity(goal), "requirement_set_sha256": requirement_set.digest,
                              "base_revision": base_revision, "claim": claim, "disposition": "", "reason": "",
                              "evidence": []} for rid, claim in targets]}
