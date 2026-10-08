"""FS-1C2 B3: human-bound acceptance authority for GENERAL behaviour claims.

B2 (kriya/workflow/acceptance_oracle.py, B2-COV) refuses to let finite passing cases prove a GENERAL rule ("raises
ValueError for any other string"). B3 is a different authority, never a proof: an operator explicitly approves one
exact acceptance suite as SUFFICIENT acceptance evidence for one exact requirement. A requirement closed this way is
reported ``HUMAN_ACCEPTED`` (closure method ``human_bound_acceptance``), never CLOSED_BY_EVIDENCE, never "verified".

Approval artifact (JSON, operator-written, passed with ``kriya generate --acceptance ... --acceptance-approval <file>``):

    {"format": "kriya.acceptance_approval/1",
     "approvals": [{"requirement_id": "REQ-1",
                    "requirement_text_sha256": "...",   # sha256 of the requirement's exact text
                    "goal_sha256": "...",               # RequirementSet.goal_digest
                    "acceptance_sha256": "...",         # the acceptance artifact's digest
                    "expected_cases": ["..."],          # exactly the cases the artifact binds to this requirement
                    "runner_contract_sha256": "...",    # the acceptance runner contract of its language
                    "base_revision": "...",             # the repository revision the run starts from
                    "accept_suite_as_sufficient": true}]}

Every field is required and must equal what the run binds: one entry per requirement, no wildcard, no duplicate, an id
of the derived requirement set (never a scope-only requirement), the suite's exact case list. The file must live
OUTSIDE the workspace (SEC-009's rule for operator authority: a repository can never ship its own approval); it is
read, validated and stored content-addressed in Kriya's state directory before any model call. Bound at run start
only - its digest joins the goal-side resume identity (direct path) and the enforce ControlState, so an approval added
or changed after a candidate was generated never elevates that candidate: the resumed run regenerates.

At the closure (``approval_problem``) the entry is re-checked against the requirement's current text, the goal, the
acceptance digest and cases, the runner contract and the run's base revision. B3 only ever upgrades a GENERAL claim
whose approved suite executed and fully passed under every B2 integrity check; a contradicting case is VIOLATED with
or without approval, and every INDETERMINATE outcome stays INDETERMINATE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

APPROVAL_FORMAT = "kriya.acceptance_approval/1"
# GR-R1A: /2 adds requirement_set_sha256, the authoritative requirement set's
# digest. Required for a run bound to an explicit requirement contract (an
# approval made against another set - including the one derived from the
# goal - never applies to it); /1 stays valid for derived requirement sets.
APPROVAL_FORMAT_V2 = "kriya.acceptance_approval/2"
HUMAN_ACCEPTANCE_METHOD = "human_bound_acceptance"
ACCEPTANCE_HUMAN_ACCEPTED = "ACCEPTANCE_HUMAN_ACCEPTED"
ACCEPTANCE_APPROVAL_INVALID = "ACCEPTANCE_APPROVAL_INVALID"
ACCEPTANCE_APPROVAL_MISMATCH = "ACCEPTANCE_APPROVAL_MISMATCH"

_FIELDS = ("requirement_id", "requirement_text_sha256", "goal_sha256", "acceptance_sha256", "expected_cases",
           "runner_contract_sha256", "base_revision", "accept_suite_as_sufficient")
_FIELDS_V2 = _FIELDS + ("requirement_set_sha256",)
_REQ_ID = re.compile(r"^REQ-C?\d+$")


@dataclass(frozen=True)
class ApprovalEntry:
    requirement_id: str
    requirement_text_sha256: str
    goal_sha256: str
    acceptance_sha256: str
    expected_cases: Tuple[str, ...]
    runner_contract_sha256: str
    base_revision: str
    requirement_set_sha256: Optional[str] = None  # GR-R1A, format /2

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement_id": self.requirement_id, "requirement_text_sha256": self.requirement_text_sha256,
                "goal_sha256": self.goal_sha256, "acceptance_sha256": self.acceptance_sha256,
                "expected_cases": list(self.expected_cases), "runner_contract_sha256": self.runner_contract_sha256,
                "base_revision": self.base_revision, "accept_suite_as_sufficient": True,
                **({"requirement_set_sha256": self.requirement_set_sha256}
                   if self.requirement_set_sha256 is not None else {})}


@dataclass(frozen=True)
class AcceptanceApproval:
    digest: str
    stored_path: str
    source_name: str
    entries: Mapping[str, ApprovalEntry]


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def runner_contract_digest(acceptance: Any, workspace: Optional[str] = None) -> str:
    """The runner contract the acceptance artifact runs under: Python's, or
    for a Java artifact the JVM runner detected at ``workspace`` (Maven /
    Gradle; GRADLE-JVM-ACCEPTANCE-001) - Maven when no workspace is given."""
    if getattr(acceptance, "language", "python") == "java":
        from kriya.workflow.acceptance_jvm import JVM_RUNNER, detect_jvm_runner, runner_source_digest

        runner = (detect_jvm_runner(workspace) if workspace else None) or JVM_RUNNER
        return runner_source_digest(runner)
    from kriya.workflow.acceptance_oracle import RUNNER_CONTRACT_DIGEST

    return RUNNER_CONTRACT_DIGEST


def _refuse(message: str) -> Exception:
    from kriya.workflow.acceptance_oracle import AcceptanceError

    return AcceptanceError(ACCEPTANCE_APPROVAL_INVALID, message)


def load_approval(
    path: str, requirements: Any, acceptance: Any, *, state_root: str, workspace: str, base_revision: Optional[str],
) -> AcceptanceApproval:
    """Read, validate and bind the operator's approval file before any model
    call; refused (``ACCEPTANCE_APPROVAL_INVALID``) unless every entry binds
    exactly this goal, requirement, acceptance artifact, case list, runner
    contract and base revision."""
    from kriya.platform.filesystem_semantics import PathRelation, path_relation
    from kriya.workflow.requirements import is_mutation_scope_requirement

    if acceptance is None:
        raise _refuse("an approval needs the acceptance file it approves (--acceptance)")
    real = os.path.realpath(path)
    if path_relation(os.path.realpath(workspace), real) is not PathRelation.OUTSIDE:
        raise _refuse("the approval file must live outside the workspace (a repository can never ship its own "
                      "approval)")
    try:
        with open(real, "rb") as handle:
            data = handle.read()
        document = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise _refuse(f"unreadable approval file: {error}") from error
    if not isinstance(document, dict) or document.get("format") not in (APPROVAL_FORMAT, APPROVAL_FORMAT_V2) or set(
            document) != {"format", "approvals"} or not isinstance(document.get("approvals"), list) or not document[
            "approvals"]:
        raise _refuse(f"expected {{'format': '{APPROVAL_FORMAT_V2}', 'approvals': [...]}} with at least one "
                      "approval")
    fields = _FIELDS_V2 if document["format"] == APPROVAL_FORMAT_V2 else _FIELDS
    if getattr(requirements, "contract_digest", None) is not None and fields is not _FIELDS_V2:
        raise _refuse(f"an explicit requirement contract needs an approval in {APPROVAL_FORMAT_V2}, bound to its "
                      "requirement set (requirement_set_sha256)")
    if not base_revision:
        raise _refuse("the workspace has no base revision to bind the approval to")
    entries: Dict[str, ApprovalEntry] = {}
    runner = runner_contract_digest(acceptance, workspace)
    for raw in document["approvals"]:
        if not isinstance(raw, dict) or set(raw) != set(fields):
            raise _refuse("each approval has exactly these fields: " + ", ".join(fields))
        rid = raw["requirement_id"]
        if not isinstance(rid, str) or not _REQ_ID.match(rid):
            raise _refuse(f"requirement_id must be one REQ id, never a pattern: {rid!r}")
        if rid in entries:
            raise _refuse(f"duplicate approval for {rid}")
        requirement = requirements.get(rid)
        if requirement is None:
            raise _refuse(f"{rid} is not a requirement of this goal ({', '.join(requirements.ids)})")
        if is_mutation_scope_requirement(requirement.text):
            raise _refuse(f"{rid} is a mutation-scope requirement; acceptance never decides it")
        if raw["accept_suite_as_sufficient"] is not True:
            raise _refuse(f"{rid}: accept_suite_as_sufficient must be the literal true")
        cases = raw["expected_cases"]
        if not isinstance(cases, list) or not all(isinstance(case, str) for case in cases):
            raise _refuse(f"{rid}: expected_cases must be a list of case identities")
        entry = ApprovalEntry(rid, raw["requirement_text_sha256"], raw["goal_sha256"], raw["acceptance_sha256"],
                              tuple(sorted(cases)), raw["runner_contract_sha256"], raw["base_revision"],
                              raw.get("requirement_set_sha256"))
        mismatch = _entry_mismatch(entry, requirement, requirements, acceptance, runner, base_revision)
        if mismatch:
            raise _refuse(f"{rid}: {mismatch}")
        entries[rid] = entry
    digest = hashlib.sha256(data).hexdigest()
    store = os.path.join(state_root, "acceptance-approvals")
    os.makedirs(store, exist_ok=True)
    stored = os.path.join(store, f"{digest}.json")
    if not os.path.isfile(stored):
        temporary = f"{stored}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, stored)
    return AcceptanceApproval(digest=digest, stored_path=stored, source_name=os.path.basename(path), entries=entries)


def _entry_mismatch(
    entry: ApprovalEntry, requirement: Any, requirements: Any, acceptance: Any, runner: str, base_revision: Optional[str],
) -> Optional[str]:
    checks = (
        (entry.requirement_id == requirement.id, "the approval is for another requirement"),
        (entry.requirement_text_sha256 == text_sha256(requirement.text), "the requirement text differs"),
        (entry.goal_sha256 == requirements.goal_digest, "the goal differs"),
        (entry.acceptance_sha256 == acceptance.digest, "the acceptance artifact differs"),
        (list(entry.expected_cases) == sorted(acceptance.identities_for(entry.requirement_id)),
         "the approved cases are not exactly the acceptance cases for this requirement"),
        (entry.runner_contract_sha256 == runner, "the acceptance runner contract differs"),
        (bool(base_revision) and entry.base_revision == base_revision, "the base revision differs"),
        # GR-R1A: an approval bound to a requirement set binds exactly that one;
        # an explicit contract's set accepts no approval without the binding.
        (entry.requirement_set_sha256 == requirements.digest if entry.requirement_set_sha256 is not None
         else getattr(requirements, "contract_digest", None) is None,
         "the approval is bound to another requirement set"),
    )
    problems = [message for ok, message in checks if not ok]
    return "; ".join(problems) if problems else None


def approval_problem(
    approval: Optional[AcceptanceApproval], requirement: Any, requirements: Any, acceptance: Any,
    base_revision: Optional[str], runner_digest: Optional[str] = None,
) -> Optional[str]:
    """Why ``approval`` does not make the suite sufficient for ``requirement``
    at this closure (None: it does). ``runner_digest``: the contract the run
    actually judged under (its evidence), never re-detected here."""
    if approval is None:
        return "no human approval"
    entry = approval.entries.get(requirement.id)
    if entry is None:
        return f"the approval does not cover {requirement.id}"
    return _entry_mismatch(entry, requirement, requirements, acceptance,
                           runner_digest or runner_contract_digest(acceptance), base_revision)


def bound_approval(engine: Any) -> Optional[AcceptanceApproval]:
    approval = getattr(engine, "acceptance_approval", None)
    return approval if isinstance(approval, AcceptanceApproval) else None


def approval_template(requirements: Any, acceptance: Any, requirement_ids: List[str], base_revision: str,
                      workspace: Optional[str] = None) -> Dict[str, Any]:
    """An UNAPPROVED approval document for the operator to review: every
    binding filled in, ``accept_suite_as_sufficient`` false - only the
    operator flips it."""
    explicit = getattr(requirements, "contract_digest", None) is not None
    entries = []
    for rid in requirement_ids:
        requirement = requirements.get(rid)
        entries.append({**ApprovalEntry(rid, text_sha256(requirement.text), requirements.goal_digest,
                                        acceptance.digest, tuple(sorted(acceptance.identities_for(rid))),
                                        runner_contract_digest(acceptance, workspace), base_revision,
                                        requirements.digest if explicit else None).to_dict(),
                        "accept_suite_as_sufficient": False})
    return {"format": APPROVAL_FORMAT_V2 if explicit else APPROVAL_FORMAT, "approvals": entries}
