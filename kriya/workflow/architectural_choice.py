"""MA8: Bounded Architecture-Choice Invalidation (spec §35-38).

Deliberately the narrowest possible slice of the spec's own description.
The spec asks for a mechanism that recognizes when a candidate's own
architectural choice - not any single file's content - is the actual
defect, and can abandon that choice in favor of a grounded existing owner,
rather than patching the same wrong choice indefinitely. It explicitly
warns against two things this module avoids: building a generic
"redesign the repository" capability, and inventing new semantic-
duplication detection from scratch (§35: "Do not let the LLM redesign the
repository arbitrarily"; §61: "control must remain deterministic").

This module does NOT detect duplication itself - kriya/workflow/
file_resolution.py's find_brownfield_test_redirections/
find_brownfield_public_api_changes are the only live-confirmed,
already-tested detectors of "a new candidate file competes with an
existing grounded owner" in this codebase (find_brownfield_test_
redirections is exactly the PRV-05 run 5 "new JsonUtil.java instead of
migrating JsonService.java" failure family, scoped to the case where an
established test's ownership reference gets redirected). Building a
second, more general "does this new file duplicate that existing file's
responsibility" detector from nothing - without a live incident to
validate it against - would be exactly the kind of speculative,
unvalidated heuristic this codebase's own established discipline avoids
(see this repo's memory: "Trigger to implement: recurrence in another
PRV, or evidence it materially affects completion rate - not before").

What this module DOES add: a bounded trigger and typed record for turning
a RECURRING brownfield/duplicate-ownership violation (the SAME candidate
file flagged more than once by an EXISTING detector) into an explicit
ARCHITECTURE_CHOICE_INVALIDATED diagnostic, rather than silently retrying
the same doomed repair indefinitely and hoping normal failure attribution
eventually redirects to the grounded owner on its own."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from kriya.workflow.file_resolution import _explicitly_requests_new_artifact

# A single occurrence is treated as this attempt's ordinary quality-gate
# failure - normal likely_files-targeted attribution/retry already points
# the next attempt at the grounded owner (see workflow.py's own
# ownership_violations handling). Only once the SAME candidate file has
# been flagged again DESPITE that redirect is the underlying architectural
# choice - not just this one attempt's output - confirmed wrong.
INVALIDATION_REPEAT_THRESHOLD = 2


@dataclass(frozen=True)
class CandidateArchitecturalChange:
    """One occurrence of a candidate-introduced file conflicting with a
    grounded existing owner. `change_id` is stable across occurrences
    (derived from `files`, never from free-text error content) so
    repeated occurrences of the SAME conflict can be counted."""

    change_id: str
    kind: str
    files: Tuple[str, ...]
    introduced_attempt: int
    originating_subtask: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


def candidate_architectural_change_id(kind: str, files: Tuple[str, ...]) -> str:
    return f"{kind}:{'|'.join(sorted(files))}"


def should_invalidate_architectural_choice(
    occurrences: List[CandidateArchitecturalChange],
) -> bool:
    """Bounded trigger (spec §36): fires once the SAME candidate-introduced
    architectural choice has been flagged at least
    INVALIDATION_REPEAT_THRESHOLD times this run - not on first occurrence."""
    return len(occurrences) >= INVALIDATION_REPEAT_THRESHOLD


def classify_ownership_violations(
    ownership_violations: List[Dict[str, str]],
    goal: str,
    attempt_number: int,
    prior_changes: List[CandidateArchitecturalChange],
) -> Tuple[List[CandidateArchitecturalChange], Optional[Dict[str, Any]]]:
    """Given this attempt's freshly-detected brownfield/duplicate-ownership
    violations (find_brownfield_test_redirections' own return shape) plus
    every CandidateArchitecturalChange already recorded on prior attempts
    THIS run, decide whether the spec §36 bounded trigger fires for any of
    them.

    Returns (new_changes_to_append, invalidation_diagnostics). The caller
    is responsible for appending new_changes_to_append onto its own
    running list (this function is pure - it does not mutate
    prior_changes) and for merging invalidation_diagnostics into the
    Failure it raises when not None.

    A violation whose candidate file the goal explicitly requested by name
    (_explicitly_requests_new_artifact) is excluded entirely - never
    invalidate an architecture the goal itself asked for (spec §36's own
    "goal did not explicitly require the candidate architecture" clause).
    When invalidation fires for more than one violation in the same call,
    diagnostics describes only the first - one clear, actionable target
    beats a bundled list for what the next repair attempt should do."""
    new_changes: List[CandidateArchitecturalChange] = []
    diagnostics: Optional[Dict[str, Any]] = None
    for item in ownership_violations:
        candidate = item["new_candidate"]
        if _explicitly_requests_new_artifact(candidate, goal):
            continue
        change_id = candidate_architectural_change_id(
            "brownfield_ownership_redirect", (candidate,),
        )
        change = CandidateArchitecturalChange(
            change_id=change_id, kind="brownfield_ownership_redirect",
            files=(candidate,), introduced_attempt=attempt_number,
            evidence={
                "existing_owner": item["existing_owner"],
                "redirected_test": item["redirected_test"],
            },
        )
        new_changes.append(change)
        occurrences = [
            c for c in prior_changes + new_changes if c.change_id == change_id
        ]
        if diagnostics is None and should_invalidate_architectural_choice(occurrences):
            diagnostics = {
                "reason_code": "ARCHITECTURE_CHOICE_INVALIDATED",
                "invalidated_candidate": candidate,
                "grounded_owner": item["existing_owner"],
                "occurrences": len(occurrences),
            }
    return new_changes, diagnostics


def architecture_choice_invalidated_message(diagnostics: Dict[str, Any]) -> str:
    return (
        f"ARCHITECTURE CHOICE INVALIDATED: {diagnostics['invalidated_candidate']!r} has "
        f"repeatedly ({diagnostics['occurrences']}x) competed with the existing grounded "
        f"owner {diagnostics['grounded_owner']!r} for the same behavioral responsibility, "
        "and the goal never explicitly requested this new file. Abandon "
        f"{diagnostics['invalidated_candidate']!r} entirely - do not create or reference it. "
        f"Implement the change in {diagnostics['grounded_owner']!r} instead."
    )
