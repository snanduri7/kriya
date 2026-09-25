"""PRD-021: plan-time grounded existing-responsibility ownership findings.

``file_resolution.prefer_existing_artifact_owners`` deterministically sends a
planned file back to an existing owner when the evidence is unambiguous (an
exact basename, strict name-token containment, or a clear name-plus-goal
score). A differently named new owner of an existing responsibility slips
past it: with ``order_validator.py`` in the repository, a plan creating
``order_rules.py``, ``order_validation.py`` or ``stock_checker.py`` for an
order-validation goal is accepted as new.

This module surfaces that suspicion as evidence, never as a verdict:

- ``grounded_owner_candidates``: existing files whose responsibility the
  goal names, from the same bounded, goal-ranked candidate set and the same
  in-memory ``DependencyGraph`` parser that authoritative planning already
  uses (never a second index). A candidate needs the goal to name its
  *responsibility* - the role word of its name (validator, checker) or a
  member verb (validate) - plus one more shared term. A shared domain noun
  ("order") alone never qualifies. They are shown to the Planner (and the
  Architect) before planning.
- ``find_ownership_findings``: for every planned file that is still new after
  the deterministic owner rules, a GROUNDED finding against each candidate it
  overlaps (a shared name term, or the file's subtask naming the candidate's
  responsibility). The match basis (role, domain, member, graph) and a
  confidence class are recorded.
- ``settle_findings``: a finding starts UNRESOLVED. A Planner/Architect
  ``ownership_justification`` moves it only to ACKNOWLEDGED. It becomes
  SATISFIED only through explicit goal intent (the request asks for a new
  artifact), deterministic repository evidence (the plan removes the
  candidate owner, i.e. a migration), or a human approval (no autonomous
  producer exists). A finding never denies or repairs a plan by itself.
- Findings are ``GROUNDED_OWNERSHIP`` obligations (GROUNDED authority, never
  terminal) and carry a stable id and ``to_dict`` for review context.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)

UNRESOLVED = "unresolved"
ACKNOWLEDGED = "acknowledged"
SATISFIED = "satisfied"

_STATUS = {UNRESOLVED: ObligationStatus.PENDING, ACKNOWLEDGED: ObligationStatus.INDETERMINATE,
           SATISFIED: ObligationStatus.SATISFIED}

# Words that describe the change, not a responsibility; never evidence.
_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "from", "into", "that",
    "this", "it", "its", "be", "is", "are", "as", "at", "when", "whose", "which", "all", "any", "each",
    "add", "create", "new", "make", "use", "using", "update", "implement", "change", "support", "ensure",
    "file", "class", "module", "function", "method", "code", "test", "tests", "src", "main", "java", "py",
    "init", "util", "utils", "helper", "helpers", "impl", "base", "common", "core", "app",
})
_SUFFIXES = ("ations", "ation", "ators", "ator", "ating", "ates", "ated", "ate", "ions", "ion", "ings", "ing",
             "ers", "er", "ors", "or", "ies", "es", "ed", "s")
_SOURCE_EXTENSIONS = frozenset({".py", ".java", ".rb", ".kt", ".scala", ".go", ".ts", ".js", ".cs"})


def _stem(token: str) -> str:
    token = token.lower()
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _words(text: str) -> List[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", expanded) if len(w) > 1]


def _stems(text: str) -> set:
    return {_stem(w) for w in _words(text) if w not in _STOP}


def _name_words(path: str) -> List[str]:
    return [w for w in _words(os.path.basename(path).rsplit(".", 1)[0]) if w not in _STOP]


@dataclass(frozen=True)
class OwnerCandidate:
    """An existing file whose responsibility the goal names."""

    path: str
    role: str  # the responsibility term the goal names (stem)
    shared: Tuple[str, ...]  # every shared stem, role included
    members: Tuple[str, ...]  # symbols whose names carry the responsibility
    referenced_by: Tuple[str, ...]  # candidate files that reference it (graph)

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "shared": list(self.shared), "members": list(self.members),
                "referenced_by": list(self.referenced_by)}


@dataclass(frozen=True)
class OwnershipFinding:
    planned_path: str
    candidate_owner: str
    subtask_id: Optional[str]
    symbols: Tuple[str, ...]
    relationships: Tuple[str, ...]
    match_basis: Tuple[str, ...]
    confidence: str  # "high" | "medium"
    status: str = UNRESOLVED
    justification: str = ""
    satisfied_by: str = ""
    classification: str = "GROUNDED"  # suspicion, never DETERMINISTIC equivalence

    @property
    def id(self) -> str:
        return f"ownership.{self.planned_path}::{self.candidate_owner}"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("symbols", "relationships", "match_basis"):
            data[key] = list(data[key])
        data["id"] = self.id
        return data


def _is_source(path: str) -> bool:
    from kriya.workflow.file_resolution import is_runnable_test_file

    return os.path.splitext(path)[1].lower() in _SOURCE_EXTENSIONS and not is_runnable_test_file(path)


def grounded_owner_candidates(
    workspace_path: str, goal: str, *, candidate_paths: Optional[Sequence[str]] = None,
    resolved_edges: Optional[Mapping[str, Sequence[str]]] = None, limit: int = 5,
) -> List[OwnerCandidate]:
    """Existing source files whose responsibility ``goal`` names. Uses the
    bounded goal-ranked candidate set and an in-memory DependencyGraph over
    exactly those files (the planning structural-evidence machinery)."""
    from kriya.analyzer.graph import DependencyGraph

    if candidate_paths is None:
        from kriya.workflow.workflow_controller import _authoritative_planner_extension_candidates
        candidate_paths = _authoritative_planner_extension_candidates(workspace_path, goal=goal)
    paths = [p for p in candidate_paths if _is_source(p) and os.path.isfile(os.path.join(workspace_path, p))]
    if resolved_edges is None:
        from kriya.workflow.workflow_controller import build_planning_structural_evidence
        _, resolved_edges = build_planning_structural_evidence(workspace_path, paths)
    referenced_by: Dict[str, List[str]] = {}
    for source, targets in (resolved_edges or {}).items():
        for target in targets:
            referenced_by.setdefault(target, []).append(source)
    goal_stems = _stems(goal)
    candidates: List[Tuple[int, OwnerCandidate]] = []
    graph = DependencyGraph(":memory:")
    try:
        for path in paths:
            full = os.path.join(workspace_path, path)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
                graph.index_file(path, content, os.path.getmtime(full))
            except OSError:
                continue
            words = _name_words(path)
            if not words:
                continue
            name_stems = {_stem(w) for w in words}
            role = _stem(words[-1])  # OrderValidator -> valid, stock_checker -> check
            symbols = {s.rsplit(".", 1)[-1] for s in graph.get_symbols_for_file(path)}
            # A member carries responsibility evidence when the goal names the
            # role it serves or a verb beyond the file's own name
            # (validate(), check_quantity()); the class restating the file
            # name is not extra evidence.
            member_hits = sorted(
                s for s in symbols
                if _stems(s) != name_stems and _stems(s) & goal_stems & ({role} | (_stems(s) - name_stems))
            )
            if role not in goal_stems and not member_hits:
                continue  # the goal never names what this file is responsible for
            shared = sorted((name_stems | {t for s in member_hits for t in _stems(s)}) & goal_stems)
            if len(shared) < 2:
                continue  # a responsibility word alone, or a domain noun alone, is not enough
            candidates.append((len(shared) + len(member_hits), OwnerCandidate(
                path=path, role=role, shared=tuple(shared), members=tuple(member_hits),
                referenced_by=tuple(sorted(referenced_by.get(path, ()))),
            )))
    finally:
        graph.close()
    candidates.sort(key=lambda item: (-item[0], item[1].path))
    return [candidate for _, candidate in candidates[:limit]]


def find_ownership_findings(
    planned_new_paths: Iterable[Tuple[str, Optional[str], str]], candidates: Sequence[OwnerCandidate], *,
    touched_paths: Iterable[str] = (),
) -> List[OwnershipFinding]:
    """Findings for planned files that are still NEW after the deterministic
    owner rules. ``planned_new_paths`` is (path, subtask id or None, the
    text describing that file's work). A finding needs the same extension and
    either a shared name term with the candidate or the work text naming the
    candidate's responsibility."""
    touched = set(touched_paths)
    findings: List[OwnershipFinding] = []
    for planned, subtask_id, work_text in planned_new_paths:
        if not _is_source(planned):
            continue
        planned_stems = {_stem(w) for w in _name_words(planned)}
        work_stems = _stems(work_text)
        extension = os.path.splitext(planned)[1].lower()
        for candidate in candidates:
            if candidate.path == planned or os.path.splitext(candidate.path)[1].lower() != extension:
                continue
            basis: List[str] = []
            name_shared = sorted(planned_stems & set(candidate.shared))
            if name_shared:
                basis.append("name:" + ",".join(name_shared))
            if candidate.role in planned_stems or candidate.role in work_stems:
                basis.append(f"role:{candidate.role}")
            if not basis:
                continue
            if candidate.members:
                basis.append("members:" + ",".join(candidate.members))
            relationships = tuple(f"{source} references {candidate.path}" for source in candidate.referenced_by)
            if set(candidate.referenced_by) & touched:
                basis.append("graph:referenced_by_planned_file")
            strong = sum(1 for b in basis if b.startswith(("role:", "members:", "graph:")))
            findings.append(OwnershipFinding(
                planned_path=planned, candidate_owner=candidate.path, subtask_id=subtask_id,
                symbols=candidate.members, relationships=relationships, match_basis=tuple(basis),
                confidence="high" if strong >= 2 else "medium",
            ))
    return findings


def settle_findings(
    findings: Sequence[OwnershipFinding], *, goal: str,
    justifications: Mapping[str, str] = {}, removed_paths: Iterable[str] = (),
    human_approved: Iterable[str] = (),
) -> List[OwnershipFinding]:
    """Apply the only ways a finding's status may change. ``justifications``
    maps a planned path to the Planner/Architect's stated reason."""
    from kriya.workflow.file_resolution import _explicitly_requests_new_artifact

    removed = set(removed_paths)
    approved = set(human_approved)
    settled: List[OwnershipFinding] = []
    for finding in findings:
        if _explicitly_requests_new_artifact(finding.planned_path, goal):
            settled.append(replace(finding, status=SATISFIED, satisfied_by="goal_intent"))
        elif finding.candidate_owner in removed:
            settled.append(replace(finding, status=SATISFIED, satisfied_by="plan_removes_owner"))
        elif finding.id in approved:
            settled.append(replace(finding, status=SATISFIED, satisfied_by="human_approval"))
        elif (justifications.get(finding.planned_path) or "").strip():
            settled.append(replace(finding, status=ACKNOWLEDGED,
                                   justification=justifications[finding.planned_path].strip()))
        else:
            settled.append(finding)
    return settled


def record_findings(ledger: ObligationLedger, findings: Sequence[OwnershipFinding], *, revision: Any,
                    source: str) -> None:
    """Persist each finding as a GROUNDED, non-terminal obligation."""
    for finding in findings:
        ledger.record(ObligationRecord(
            id=finding.id, kind=ObligationKind.GROUNDED_OWNERSHIP, status=_STATUS[finding.status],
            authority=ObligationAuthority.GROUNDED,
            description=f"{finding.planned_path} may duplicate the responsibility of {finding.candidate_owner}",
            source=source, revision=revision, evidence=finding.to_dict(),
            owner_subtask_id=finding.subtask_id, terminal_required=False,
            repair_scope=(finding.planned_path, finding.candidate_owner),
        ))


def owner_candidates_prompt_block(candidates: Sequence[OwnerCandidate], *, where: str = "") -> str:
    """Grounded candidates for the Planner/Architect, before planning.
    ``where`` says where the structured ownership_justification goes."""
    if not candidates:
        return ""
    lines = []
    for c in candidates:
        extra = f"; defines {', '.join(c.members)}" if c.members else ""
        refs = f"; used by {', '.join(c.referenced_by)}" if c.referenced_by else ""
        lines.append(f"- {c.path} (responsibility: {c.role}{extra}{refs})")
    return (
        "=== Existing owners this request may belong to (grounded in the repository; a suspicion, not a rule) ===\n"
        + "\n".join(lines)
        + "\nPrefer extending the existing owner of a responsibility. If you still create a new file for it, "
          "state why in ownership_justification (a JSON object mapping that planned path to the reason"
        + (f", {where}" if where else "") + ").\n"
    )


def findings_prompt_block(findings: Sequence[OwnershipFinding]) -> str:
    """Unsettled findings as advisory context (Developer, Reviewer)."""
    open_findings = [f for f in findings if f.status != SATISFIED]
    if not open_findings:
        return ""
    lines = [
        f"- {f.planned_path} may duplicate {f.candidate_owner} ({', '.join(f.match_basis)}; {f.status}"
        + (f": {f.justification}" if f.justification else "") + ")"
        for f in open_findings
    ]
    return ("=== Grounded ownership findings (advisory evidence, not a verdict) ===\n" + "\n".join(lines)
            + "\nKeep the new file from re-implementing what the existing owner already does; reuse or "
              "delegate to it.\n")


def ownership_review_evidence(ledger: Optional[ObligationLedger], files: Iterable[str]) -> str:
    """PRD-022: the run's grounded ownership findings that concern ``files``,
    for the Reviewer and a human approver: advisory evidence only. Read from
    the obligation ledger (where every producer records them), so it is the
    same on the direct, milestone and enforce paths. Nothing a reviewer says
    changes a finding: only ``settle_findings`` does."""
    if ledger is None:
        return ""
    wanted = set(files)
    lines = []
    for record in ledger.current_by_kind(ObligationKind.GROUNDED_OWNERSHIP):
        evidence = record.evidence or {}
        if evidence.get("planned_path") not in wanted or evidence.get("status") == SATISFIED:
            continue
        reason = f": {evidence['justification']}" if evidence.get("justification") else ""
        lines.append(
            f"- {evidence['planned_path']} may duplicate existing {evidence['candidate_owner']} "
            f"(basis {', '.join(evidence.get('match_basis') or [])}; confidence {evidence.get('confidence')}; "
            f"{evidence.get('status')}{reason})"
        )
    if not lines:
        return ""
    return (
        "\n=== Grounded ownership findings (advisory, GROUNDED suspicion - not a verified defect) ===\n"
        "Kriya found these possible duplicate owners from repository evidence. Weigh them as context: "
        "say whether the new code re-implements the existing owner's responsibility, but do not "
        "present a suspicion as an established fact and do not reject for it alone.\n"
        + "\n".join(lines) + "\n"
    )


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_ownership_justifications(text: str) -> Dict[str, str]:
    """The ``ownership_justification`` object of the last fenced JSON block
    of a design (the Architect's file-list block); {} when absent."""
    blocks = _FENCED_JSON.findall(text or "")
    if not blocks:
        return {}
    try:
        parsed = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return {}
    raw = parsed.get("ownership_justification") if isinstance(parsed, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}
