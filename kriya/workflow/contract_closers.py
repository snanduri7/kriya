"""VERIFICATION-CONTRACT-003: deterministic closers the compiled contract binds
beside the existing ones (named tests, full regression, test immutability,
mutation scope, migration gate, B2 acceptance, B3 approval, the API
predicate in kriya/workflow/api_preservation.py, the external authority in
kriya/workflow/authority_bundle.py).

- ``test_addition`` ("add a test for it"): closed by the candidate's own
  mutation record and structured test evidence - a runnable test file the
  base did not have, or a test identity in the candidate's complete suite
  report that the base's report does not carry. Never closed by a model
  statement, a changed test file alone, or an incomplete report.
- ``documentation_not_applicable`` ("document it in the README's function
  list if there is one"): closed when the conditional referent is absent
  from the candidate as well as the base (nothing to document). A present
  referent is a content claim and stays open.

Each closer records its claim with the contract's ``required_claims`` so a
compound statement closes only when every claim it makes is closed.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from kriya.workflow.contract_compilation import (
    CLOSER_DOCUMENTATION_LIST_ENTRIES,
    CLOSER_DOCUMENTATION_NOT_APPLICABLE,
    CLOSER_TEST_ADDITION,
    VerificationContract,
    _documentation_list_present,
    documentation_entries_present,
    documentation_sections,
)
from kriya.workflow.obligations import ObligationLedger, ObligationStatus
from kriya.workflow.requirements import (
    DOCUMENTATION_CLAIM,
    DOCUMENTATION_ENTRIES_METHOD,
    DOCUMENTATION_METHOD,
    TEST_ADDITION_CLAIM,
    TEST_ADDITION_METHOD,
    RequirementSet,
    record_requirement_claim,
    requirement_obligation_id,
)

TEST_ADDED = "TEST_ADDED"
TEST_ADDITION_UNPROVEN = "TEST_ADDITION_UNPROVEN"
DOCUMENTATION_NOT_APPLICABLE = "DOCUMENTATION_NOT_APPLICABLE"
DOCUMENTATION_REFERENT_PRESENT = "DOCUMENTATION_REFERENT_PRESENT"
# BACKEND-READINESS-004 (owner decision 2): the sealed list-entries predicate on the candidate.
DOCUMENTATION_ENTRIES_PRESENT = "DOCUMENTATION_ENTRIES_PRESENT"
DOCUMENTATION_ENTRIES_MISSING = "DOCUMENTATION_ENTRIES_MISSING"
DOCUMENTATION_LIST_REMOVED = "DOCUMENTATION_LIST_REMOVED"


_JVM_TEST_ANNOTATION = re.compile(r"@(?:org\.junit\.(?:jupiter\.api\.)?)?(?:Test|ParameterizedTest|RepeatedTest)\b")


def file_contains_a_test(path: str, data: Optional[bytes]) -> bool:
    """Review VC3-R7: a file whose NAME follows a test convention is not a
    test until it CONTAINS one - a Python ``test*`` function (module level or
    in a ``Test*`` class) or a JVM ``@Test`` annotation. Unreadable or
    unparseable: no."""
    if data is None:
        return False
    if path.endswith(".py"):
        try:
            tree = ast.parse(data.decode("utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            return False
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                return True
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test") and any(
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test") for m in node.body):
                return True
        return False
    if path.endswith((".java", ".kt")):
        return bool(_JVM_TEST_ANNOTATION.search(data.decode("utf-8", errors="replace")))
    return False


def _evidence_id(ledger: ObligationLedger, requirement_id: str) -> Optional[str]:
    record = ledger.current(requirement_obligation_id(requirement_id))
    return (record.evidence or {}).get("evidence_id") if record is not None else None


def close_test_addition_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, contract: VerificationContract, *,
    reference_test_files: Optional[Sequence[str]], candidate_test_files: Iterable[str],
    base_test_identities: Optional[Iterable[str]], candidate_test_identities: Optional[Iterable[str]],
    source: str, revision: Any, read_candidate: Optional[Callable[[str], Optional[bytes]]] = None,
) -> List[Dict[str, Any]]:
    """Record the TEST_ADDITION claim of every requirement the contract binds
    to ``test_addition``: SATISFIED when a test file exists in the candidate
    that the reference (pre-run) set lacks AND contains a test
    (``file_contains_a_test``; review VC3-R7: an empty file named like a test
    is not one), or when the candidate's complete suite report carries a test
    identity the base's does not; otherwise the claim stays open."""
    attempts: List[Dict[str, Any]] = []
    present = sorted(set(candidate_test_files))
    added_files = (sorted(set(present) - set(reference_test_files)) if reference_test_files is not None else None)
    if added_files:
        added_files = [path for path in added_files
                       if read_candidate is not None and file_contains_a_test(path, read_candidate(path))]
    new_identities = (sorted(set(candidate_test_identities) - set(base_test_identities))
                      if base_test_identities is not None and candidate_test_identities is not None else None)
    for requirement_id, binding in contract.binding_closers(CLOSER_TEST_ADDITION):
        requirement = requirements.get(requirement_id)
        if requirement is None:
            continue
        evidence_id = _evidence_id(ledger, requirement_id)
        entry: Dict[str, Any] = {"requirement": requirement_id, "kind": TEST_ADDITION_METHOD, "closed": False,
                                 "added_test_files": added_files, "new_test_identities": new_identities}
        if not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
        elif added_files or new_identities:
            record_requirement_claim(
                ledger, requirements, requirement_id, TEST_ADDITION_CLAIM, evidence_id=evidence_id,
                method=TEST_ADDITION_METHOD,
                detail={"reason_code": TEST_ADDED, "added_test_files": added_files or [],
                        "new_test_identities": new_identities or [], "required_claims": list(binding.detail.get(
                            "required_claims") or contract.required_claims_by_requirement()[requirement_id])},
                source=source, revision=revision, status=ObligationStatus.SATISFIED)
            entry.update({"closed": True, "reason_code": TEST_ADDED})
        else:
            entry["reason_code"] = TEST_ADDITION_UNPROVEN
            entry["reason"] = ("no runnable test file was added and no new test identity is in the candidate's "
                               "complete report" if added_files is not None or new_identities is not None else
                               "the pre-run test files and the base test identities are unknown")
        attempts.append(entry)
    return attempts


def close_documentation_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, contract: VerificationContract, *,
    candidate_tracked_paths: Iterable[str], read_candidate: Callable[[str], Optional[bytes]],
    source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Record the DOCUMENTATION claim of every requirement the contract bound
    to ``documentation_not_applicable``, re-checked on the candidate: the
    referent (or the named list inside it) still absent -> SATISFIED; present
    now -> open, with why (a content claim)."""
    attempts: List[Dict[str, Any]] = []
    tracked = sorted(set(candidate_tracked_paths))
    for requirement_id, binding in contract.binding_closers(CLOSER_DOCUMENTATION_LIST_ENTRIES):
        requirement = requirements.get(requirement_id)
        if requirement is None:
            continue
        attempts.append(_close_documentation_entries(ledger, requirements, requirement, binding, tracked, read_candidate,
                                                     evidence_id=_evidence_id(ledger, requirement_id),
                                                     required_claims=contract.required_claims_by_requirement()[requirement_id],
                                                     source=source, revision=revision))
    for requirement_id, _binding in contract.binding_closers(CLOSER_DOCUMENTATION_NOT_APPLICABLE):
        entry_contract = contract.entry(requirement_id)
        requirement = requirements.get(requirement_id)
        if requirement is None or entry_contract is None or not entry_contract.scope.documentation:
            continue
        evidence_id = _evidence_id(ledger, requirement_id)
        present, evidence = _documentation_list_present(entry_contract.scope.documentation, tracked, read_candidate)
        entry: Dict[str, Any] = {"requirement": requirement_id, "kind": DOCUMENTATION_METHOD, "closed": False,
                                 **evidence}
        if not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
        elif present is False:
            record_requirement_claim(
                ledger, requirements, requirement_id, DOCUMENTATION_CLAIM, evidence_id=evidence_id,
                method=DOCUMENTATION_METHOD,
                detail={**evidence, "reason_code": DOCUMENTATION_NOT_APPLICABLE,
                        "required_claims": list(entry_contract.required_claims)},
                source=source, revision=revision, status=ObligationStatus.SATISFIED)
            entry.update({"closed": True, "reason_code": DOCUMENTATION_NOT_APPLICABLE})
        else:
            entry["reason_code"] = DOCUMENTATION_REFERENT_PRESENT
            entry["reason"] = ("the documentation referent exists in the candidate; documenting it correctly is a "
                               "content claim Kriya cannot verify deterministically" if present else
                               f"the referent could not be inspected: {evidence.get('reason')}")
        attempts.append(entry)
    return attempts


def _close_documentation_entries(
    ledger: ObligationLedger, requirements: RequirementSet, requirement: Any, binding: Any, tracked: Sequence[str],
    read_candidate: Callable[[str], Optional[bytes]], *, evidence_id: Optional[str], required_claims: Sequence[str],
    source: str, revision: Any,
) -> Dict[str, Any]:
    """The sealed list-entries predicate re-evaluated on the candidate: the
    referent's named list section still exists and names every subject -
    SATISFIED; a missing entry or a removed section leaves the claim open
    (never VIOLATED: the operator reads which names are missing)."""
    predicate = dict(binding.detail)
    subjects = list(predicate.get("subjects") or ())
    noun = str(predicate.get("list_noun") or "")
    entry: Dict[str, Any] = {"requirement": requirement.id, "kind": DOCUMENTATION_ENTRIES_METHOD, "closed": False,
                             "subjects": subjects, "paths": list(predicate.get("paths") or ())}
    if not evidence_id:
        entry["reason"] = "the verdict has no evidence id to bind to"
        return entry
    present: Dict[str, List[str]] = {subject: [] for subject in subjects}
    headings: List[str] = []
    unreadable: List[str] = []
    for path in predicate.get("paths") or ():
        if path not in tracked:
            continue
        data = read_candidate(path)
        if data is None:
            unreadable.append(path)
            continue
        sections = documentation_sections(data, noun)
        headings += [f"{path}: {heading}" for heading in sections]
        for subject, found in documentation_entries_present(sections, subjects).items():
            present[subject] += [f"{path}: {line}" for line in found]
    missing = [subject for subject in subjects if not present[subject]]
    entry.update({"headings": headings, "present": {s: lines for s, lines in present.items() if lines}, "missing": missing})
    if unreadable and missing:
        entry.update({"reason_code": DOCUMENTATION_LIST_REMOVED, "reason": f"referent unreadable in the candidate: {unreadable}"})
    elif not headings:
        entry.update({"reason_code": DOCUMENTATION_LIST_REMOVED,
                      "reason": f"the {noun} list section sealed from the baseline no longer exists in the candidate"})
    elif missing:
        entry.update({"reason_code": DOCUMENTATION_ENTRIES_MISSING,
                      "reason": f"no entry of the {noun} list names: {', '.join(missing)}"})
    else:
        record_requirement_claim(
            ledger, requirements, requirement.id, DOCUMENTATION_CLAIM, evidence_id=evidence_id,
            method=DOCUMENTATION_ENTRIES_METHOD,
            detail={"reason_code": DOCUMENTATION_ENTRIES_PRESENT, "subjects": subjects, "headings": headings,
                    "present": entry["present"], "source_requirements": list(predicate.get("source_requirements") or ()),
                    "required_claims": list(required_claims)},
            source=source, revision=revision, status=ObligationStatus.SATISFIED)
        entry.update({"closed": True, "reason_code": DOCUMENTATION_ENTRIES_PRESENT})
    return entry


def contract_claims_map(contract: Optional[VerificationContract]) -> Mapping[str, Sequence[str]]:
    """Requirement id -> the contract's required claims; empty without a contract."""
    return contract.required_claims_by_requirement() if contract is not None else {}
