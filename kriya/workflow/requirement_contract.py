"""GR-R1A: the operator's explicit requirement contract.

By default the requirements a run must close come from the goal's own statements
(``derive_requirements``). A goal written as an issue report also carries headings, reproducers, version notes
and sentences that describe the defect itself; derived, every one becomes a terminal obligation. With an explicit
contract the operator names the obligations instead:

    kriya generate -f <goal-or-issue-file> --requirements <contract.json>

    {"format": "kriya.requirements/1",
     "requirements": [{"id": "REQ-1", "text": "...", "kind": "requirement"}, ...]}

The contract is a CLOSED set: it is the complete authoritative requirement set of the run, never merged with
requirements derived from the goal. The goal itself is unchanged and stays the planning, localization and Developer
context and the goal identity: the set binds the goal's digest, so another goal never reuses it.

Authority (SEC-009's rule for operator input): the file must live outside the workspace, is read and validated
before any model call, and is stored content-addressed in Kriya's state directory. Its digest joins the requirement
set's digest, so everything that binds that digest - verifier verdicts, acceptance artifacts, B3 approvals
(``kriya.acceptance_approval/2``) and the resume identity - is bound to the exact contract. Nothing a candidate or
a model writes can add, remove or reword a requirement: the set is fixed in memory at run start.

Refused before any model call (``REQUIREMENT_CONTRACT_INVALID``): an unreadable file, another format, an empty set,
an entry without exactly ``id``/``text``/``kind``, an id that is not ``REQ-<n>``, a duplicate id, an empty text, or
a kind the requirement engine does not know (``requirement``/``constraint``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from kriya.workflow.requirements import (
    REQUIREMENT_DERIVATION_VERSION,
    Requirement,
    RequirementSet,
    derive_requirements,
    goal_identity,
)

CONTRACT_FORMAT = "kriya.requirements/1"
CONTRACT_SOURCE = "operator_contract"
REQUIREMENT_CONTRACT_INVALID = "REQUIREMENT_CONTRACT_INVALID"
REQUIREMENT_CONTRACT_GOAL_MISMATCH = "REQUIREMENT_CONTRACT_GOAL_MISMATCH"
REQUIREMENT_KINDS = ("requirement", "constraint")
_ENTRY_FIELDS = frozenset({"id", "text", "kind"})
_ID = re.compile(r"^REQ-[1-9]\d*$")


class RequirementContractError(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code


@dataclass(frozen=True)
class RequirementContract:
    digest: str
    stored_path: str
    source_name: str
    requirement_set: RequirementSet


def _refuse(message: str) -> RequirementContractError:
    return RequirementContractError(REQUIREMENT_CONTRACT_INVALID, message)


def load_requirement_contract(path: str, goal: str, *, state_root: str, workspace: str) -> RequirementContract:
    """Read, validate, store and bind the operator's contract to ``goal``
    before any model call. Raises RequirementContractError."""
    from kriya.platform.filesystem_semantics import PathRelation, path_relation

    real = os.path.realpath(path)
    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:
        raise _refuse("the requirement contract must live outside the workspace (a repository can never ship "
                      "its own requirements)")
    try:
        with open(real, "rb") as handle:
            data = handle.read()
        document = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _refuse(f"unreadable requirement contract: {error}") from error
    if (not isinstance(document, dict) or set(document) != {"format", "requirements"}
            or document.get("format") != CONTRACT_FORMAT or not isinstance(document.get("requirements"), list)):
        raise _refuse(f"expected {{'format': '{CONTRACT_FORMAT}', 'requirements': [...]}}")
    if not document["requirements"]:
        raise _refuse("the requirement set is empty")
    digest = hashlib.sha256(data).hexdigest()
    requirements = []
    seen = set()
    for ordinal, raw in enumerate(document["requirements"], start=1):
        if not isinstance(raw, dict) or set(raw) != _ENTRY_FIELDS:
            raise _refuse(f"requirement #{ordinal} must have exactly the fields id, text, kind")
        rid, text, kind = raw["id"], raw["text"], raw["kind"]
        if not isinstance(rid, str) or not _ID.match(rid):
            raise _refuse(f"requirement #{ordinal}: id must be REQ-<n>, got {rid!r}")
        if rid in seen:
            raise _refuse(f"duplicate requirement id {rid}")
        seen.add(rid)
        if not isinstance(text, str) or not text.strip():
            raise _refuse(f"{rid}: text must be a non-empty string")
        if kind not in REQUIREMENT_KINDS:
            raise _refuse(f"{rid}: unsupported kind {kind!r} (supported: {', '.join(REQUIREMENT_KINDS)})")
        requirements.append(Requirement(id=rid, text=text, kind=kind, source=CONTRACT_SOURCE, ordinal=ordinal))
    requirement_set = RequirementSet(goal_digest=goal_identity(goal), version=REQUIREMENT_DERIVATION_VERSION,
                                     requirements=tuple(requirements), contract_digest=digest)
    store = os.path.join(state_root, "requirement-contracts")
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}.json")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, stored)
    return RequirementContract(digest=digest, stored_path=stored, source_name=os.path.basename(path),
                               requirement_set=requirement_set)


def bound_requirement_contract(engine: Any) -> Optional[RequirementContract]:
    contract = getattr(engine, "requirement_contract", None)
    return contract if isinstance(contract, RequirementContract) else None


def requirement_set_for(goal: str, contract: Optional[RequirementContract]) -> RequirementSet:
    """The one authoritative requirement set for ``goal``: the operator's
    closed contract when one is bound (refused when it was bound to another
    goal), else the set derived from the goal (unchanged auto mode)."""
    if contract is None:
        return derive_requirements(goal)
    if contract.requirement_set.goal_digest != goal_identity(goal):
        raise RequirementContractError(REQUIREMENT_CONTRACT_GOAL_MISMATCH,
                                       "the requirement contract was bound to another goal")
    return contract.requirement_set
