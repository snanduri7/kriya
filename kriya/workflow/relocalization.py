"""PLAN-TARGET-LOCALIZATION-MISMATCH-001 (BACKEND-READINESS-004): deterministic relocalization when the planned
target cannot carry an edit.

Batch 001 T2 (jsoup): the Planner targeted the facade the goal names (Element.java, large: skeleton tier, no exact
locus), the defect lived one call away (Node.absUrl), and the edit protocol correctly refused to invoke the Developer
(CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE) - a fail-closed stop on an unrelated editing error, with the right file never
considered. Here, at that stop, Kriya asks the repository's own structural index where the symbols the GOAL names are
DEFINED (``extract_signals`` qualified names such as ``Element.absUrl`` -> the member ``absUrl``; code intelligence,
never a model): every unique main-side definition outside the approved write scope is a grounded relocalization
candidate, recorded as a plan-scope conflict (``PLAN_TARGET_RELOCALIZATION_REQUIRED``) with grounded locations
(file, declaration line, file revision). The existing plan-scope recovery then revises the plan to own that file and
re-invokes the unit with the loci seeded (``_seed_grounded_loci``), so the Developer is shown the exact definition.
Nothing here widens write authority by itself: the conflict is evidence for the plan revision mechanism, which keeps
its own ownership rules. An ambiguous name (several definitions), a test-side definition or a definition already
inside the approved scope is never a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from kriya.code_intel.locate import extract_signals, is_test_path

PLAN_TARGET_RELOCALIZATION_REQUIRED = "PLAN_TARGET_RELOCALIZATION_REQUIRED"
ATTRIBUTION_TIER = "code_intelligence_definition"
MAX_CANDIDATE_FILES = 3


@dataclass(frozen=True)
class Definition:
    name: str  # the goal's name that resolved
    symbol_id: str
    lookup_key: str
    kind: str
    path: str
    line: int

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "symbol_id": self.symbol_id, "lookup_key": self.lookup_key, "kind": self.kind,
                "path": self.path, "line": self.line}


def _definition_names(goal: str) -> List[str]:
    """The goal's qualified names (``Element.absUrl``), each followed by its member part when it has one."""
    names: List[str] = []
    for qualified in extract_signals(goal).qualified:
        if qualified.rsplit(".", 1)[-1][:1].islower() and "." in qualified:  # Type.member, not a hostname or a file
            for name in (qualified, qualified.rsplit(".", 1)[-1]):
                if name not in names:
                    names.append(name)
    return names


def goal_symbol_definitions(service: Any, goal: str) -> Tuple[List[Definition], List[Dict[str, Any]]]:
    """(unique definitions of the goal's named members, ambiguous names) from
    the structural index: a qualified name first, its member name only when
    the qualified form resolves nothing; main-side callables and types only;
    a name with several definitions is ambiguous and never used."""
    definitions: List[Definition] = []
    ambiguous: List[Dict[str, Any]] = []
    seen_names: set = set()
    for name in _definition_names(goal):
        base = name.rsplit(".", 1)[0] if "." in name else None
        if base in seen_names:  # the qualified form already resolved; its bare member is not retried
            continue
        try:
            found = [s for s in service.find_symbol(name) if (s.is_callable or getattr(s, "is_type", False))
                     and not is_test_path(s.path)]
        except Exception:  # read-only enrichment: an index error never decides anything
            found = []
        if not found:
            continue
        if len({s.symbol_id for s in found}) > 1:
            ambiguous.append({"name": name, "definitions": sorted(f"{s.path}:{s.declaration.start_line}" for s in found)})
            continue
        symbol = found[0]
        seen_names.add(name)
        definitions.append(Definition(name, symbol.symbol_id, symbol.lookup_key, symbol.kind, symbol.path,
                                      symbol.declaration.start_line))
    return definitions, ambiguous


def relocalization_conflict(
    service: Any, goal: str, *, infeasible_paths: Sequence[str], allowed_paths: Iterable[str],
    read_revision: Callable[[str], Optional[str]], failure_type: str = "context_edit_protocol_unsatisfiable",
) -> Optional[Dict[str, Any]]:
    """The plan-scope conflict a deterministic relocalization establishes, or
    None: goal-named definitions in files outside the approved write scope
    (never the infeasible planned files themselves - those get loci, not a
    plan revision). ``read_revision(path)``: the current revision of a
    workspace file, binding each grounded location to the bytes it was read from."""
    if service is None:
        return None
    definitions, ambiguous = goal_symbol_definitions(service, goal)
    allowed = set(allowed_paths) | set(infeasible_paths)
    outside = [d for d in definitions if d.path not in allowed]
    if not outside:
        return None
    files: List[str] = []
    for definition in outside:
        if definition.path not in files:
            files.append(definition.path)
    files = files[:MAX_CANDIDATE_FILES]
    locations = [{"filepath": d.path, "line": d.line, "revision": read_revision(d.path), "symbol": d.lookup_key}
                 for d in outside if d.path in files]
    return {
        "classification": "PLAN_SCOPE_DEFECT",
        "reason_code": PLAN_TARGET_RELOCALIZATION_REQUIRED,
        "failure_type": failure_type,
        "required_files": files,
        "allowed_files": sorted(set(allowed_paths)),
        "grounded_owner_files": files,
        "grounded_locations": locations,
        "attribution_tier": ATTRIBUTION_TIER,
        "reason": ("the planned target(s) " + ", ".join(infeasible_paths) + " cannot carry an edit under the "
                   "authoritative context, and the repository's structural index defines the symbol(s) the goal "
                   "names in " + ", ".join(files) + " (" + ", ".join(d.lookup_key for d in outside if d.path in files)
                   + "), outside the approved write scope: the plan must own that file before the Developer can edit it"),
        "relocalization": {"definitions": [d.to_dict() for d in outside], "ambiguous": ambiguous,
                           "infeasible_paths": list(infeasible_paths)},
    }
