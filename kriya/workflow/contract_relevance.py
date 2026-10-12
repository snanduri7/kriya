"""BACKEND-READINESS-V2 (owner decision 2026-10-12): which goal examples are BEHAVIORALLY relevant to the files a unit touched.

An intermediate contract check (``contract_feedback``) may feed a failing example to a unit's repair loop only when the unit
could have caused the failure. The first rule - "the unit touched the file declaring the example's target type" - missed the
facade case measured live (handover/BACKEND-READINESS-V2/23): the examples call a factory method on a facade type that only ends
in ``return Impl.method(..)``; the unit edits the callee's file, so the violation never reached repair. Co-location (same module, same package) proves nothing about behavior and is NOT a relation here.

The relation, deterministic and ONE hop (no transitive traversal), read from the repository's own source with the structural
parser and the delegate reader the Planner and the Developer context already share (``code_intel.delegation``):
- RELEVANT_DIRECT_TARGET: the unit touched the main file declaring the example's target type;
- RELEVANT_DIRECT_DEPENDENCY, keyed on the example's FACTORY METHOD (never on its type - a facade type has hundreds of
  methods): the target type declares that method exactly once, and its last ``return`` either calls ``Type.method(`` (DELEGATES_TO)
  or constructs ``new Type(`` (CONSTRUCTS) on a project type that is declared in a file the unit touched; or a touched type
  directly extends/implements the target type AND declares a method of the same name (OVERRIDES);
- UNKNOWN: a needed fact cannot be read (target file not unique/unparsable, overloaded factory, ambiguous callee type). UNKNOWN
  is never promoted: the example waits for the terminal gate;
- NOT_RELEVANT: every checked relation is absent.

Model text never takes part. Terminal acceptance does not consult this module."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.code_intel.delegation import constructed_type, delegate_call
from kriya.code_intel.model import FileStructure, ParseState
from kriya.code_intel.parsing import parse_file
from kriya.workflow.example_oracle_java import main_source_path, resolve_java_type

RELEVANT_DIRECT_TARGET = "RELEVANT_DIRECT_TARGET"
RELEVANT_DIRECT_DEPENDENCY = "RELEVANT_DIRECT_DEPENDENCY"
NOT_RELEVANT = "NOT_RELEVANT"
UNKNOWN = "UNKNOWN"
RELEVANT_STATUSES = frozenset({RELEVANT_DIRECT_TARGET, RELEVANT_DIRECT_DEPENDENCY})

DELEGATES_TO = "DELEGATES_TO"
CONSTRUCTS = "CONSTRUCTS"
OVERRIDES = "OVERRIDES"
PROVENANCE = "tree-sitter-structural; delegate reader (code_intel.delegation)"


@dataclass(frozen=True)
class Relevance:
    status: str
    reason: str = ""
    target: str = ""  # Type.method the example calls
    relationship: str = ""  # DELEGATES_TO | CONSTRUCTS | OVERRIDES for a dependency
    related_type: str = ""  # the type the target reaches (or the touched type that overrides it)
    declaring_path: str = ""  # main file declaring the target type
    touched_path: str = ""  # the touched file that makes the example relevant
    provenance: str = ""

    @property
    def relevant(self) -> bool:
        return self.status in RELEVANT_STATUSES

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in (
            ("status", self.status), ("reason", self.reason), ("target", self.target), ("relationship", self.relationship),
            ("related_type", self.related_type), ("declaring_path", self.declaring_path),
            ("touched_path", self.touched_path), ("provenance", self.provenance)) if value}


def _declaration_suffix(qualified_type: str) -> str:
    package, _, simple = qualified_type.rpartition(".")
    return "/".join([*package.split("."), f"{simple}.java"]) if package else f"{simple}.java"


def _matching_touched(touched: Sequence[str], qualified_type: str) -> Optional[str]:
    suffix = _declaration_suffix(qualified_type)
    return next((t for t in touched if t == f"src/main/java/{suffix}" or t.endswith(f"/src/main/java/{suffix}")), None)


class _Structures:
    """Parses each file at most once per assessment; ``None`` when the file cannot be read or has no complete structure."""

    def __init__(self, candidate_root: str) -> None:
        self.root = candidate_root
        self._cache: Dict[str, Optional[Tuple[bytes, FileStructure]]] = {}

    def get(self, path: str) -> Optional[Tuple[bytes, FileStructure]]:
        if path not in self._cache:
            try:
                with open(os.path.join(self.root, path), "rb") as handle:
                    data = handle.read()
            except OSError:
                self._cache[path] = None
            else:
                structure = parse_file(path, data)
                self._cache[path] = (data, structure) if structure.state in (ParseState.PARSED, ParseState.PARTIALLY_PARSED) else None
        return self._cache[path]


def _written_names_type(written: str, qualified: str, structure: FileStructure) -> bool:
    """Whether a supertype as written in ``structure`` names ``qualified``: the qualified name itself, or the simple name when
    the file is in the type's package or imports it (explicitly or by its package wildcard)."""
    if written == qualified:
        return True
    package, _, simple = qualified.rpartition(".")
    if written != simple or any(i != qualified and i.rsplit(".", 1)[-1] == simple for i in structure.imports):
        return False  # another type of that simple name is imported explicitly: it shadows the same-package one
    return structure.namespace == package or qualified in structure.imports or f"{package}.*" in structure.imports


def _overriding_type(structures: _Structures, path: str, qualified: str, method: str) -> Optional[str]:
    """Qualified name of a type declared in ``path`` that DIRECTLY extends/implements ``qualified`` and declares ``method``."""
    parsed = structures.get(path)
    if parsed is None:
        return None
    _data, structure = parsed
    for symbol in structure.symbols:
        if symbol.is_type and any(_written_names_type(w, qualified, structure) for w in (*symbol.extends, *symbol.implements)):
            if any(s.is_callable and s.parent_id == symbol.symbol_id and s.name == method for s in structure.symbols):
                return symbol.lookup_key
    return None


def _declares(structures: _Structures, path: str, simple_type: str, method: Optional[str]) -> bool:
    parsed = structures.get(path)
    if parsed is None:
        return False
    _data, structure = parsed
    type_symbols = [s for s in structure.symbols if s.is_type and s.name == simple_type]
    if not type_symbols:
        return False
    return method is None or any(s.is_callable and s.name == method and s.parent_id in {t.symbol_id for t in type_symbols}
                                 for s in structure.symbols)


def assess(example: Any, touched_paths: Sequence[str], candidate_root: Optional[str],
           structures: Optional[_Structures] = None) -> Relevance:
    """The relevance of one example (a ``contract_feedback.ContractExample``) to the unit's touched files."""
    target = f"{example.qualified_type}.{example.method}" if example.method else example.qualified_type
    if not example.qualified_type:
        return Relevance(NOT_RELEVANT, "the example names no project type", target=target)
    touched = [path.replace("\\", "/").removeprefix("./") for path in touched_paths]
    direct = _matching_touched(touched, example.qualified_type)
    if direct is not None:
        return Relevance(RELEVANT_DIRECT_TARGET, "the unit touched the file declaring the target type", target=target,
                         declaring_path=direct, touched_path=direct, provenance="path of the declared type")
    if candidate_root is None or not touched:
        return Relevance(NOT_RELEVANT, "the unit touched no file that could relate to the target", target=target)
    structures = structures or _Structures(candidate_root)
    declaring = example.source_path or main_source_path(candidate_root, example.qualified_type)
    if declaring is None:
        return Relevance(UNKNOWN, "the declaring main file of the target type is not exactly one file", target=target)
    found = _dependency(example, touched, candidate_root, structures, declaring, target)
    return found if found is not None else _no_relation(example, candidate_root, structures, declaring, target)


def _no_relation(example: Any, candidate_root: str, structures: _Structures, declaring: str, target: str) -> Relevance:
    reasons = _unknown_reasons(example, candidate_root, structures, declaring)
    if reasons:
        return Relevance(UNKNOWN, "; ".join(reasons), target=target, declaring_path=declaring)
    return Relevance(NOT_RELEVANT, "no touched file is the target's direct delegate/construct callee or an overriding type",
                     target=target, declaring_path=declaring)


def _unknown_reasons(example: Any, candidate_root: str, structures: _Structures, declaring: str) -> List[str]:
    if not example.method:
        return []
    parsed = structures.get(declaring)
    if parsed is None:
        return [f"{declaring} has no complete structural parse"]
    if len(_factory_members(parsed, example)) != 1:
        return [f"{example.qualified_type}.{example.method} is not declared exactly once in {declaring}"]
    return [f"callee type {simple} is ambiguous" for _relationship, simple, _method in _reached(parsed, example)
            if "ambiguous" in resolve_java_type(candidate_root, simple)[1]]


def _factory_members(parsed: Tuple[bytes, FileStructure], example: Any) -> List[Any]:
    _data, structure = parsed
    return [s for s in structure.symbols if s.is_callable and s.lookup_key == f"{example.qualified_type}.{example.method}"]


def _reached(parsed: Tuple[bytes, FileStructure], example: Any) -> List[Tuple[str, str, Optional[str]]]:
    """(relationship, simple callee type, callee method) of the target method's last ``return``: one hop, no traversal."""
    members = _factory_members(parsed, example)
    if len(members) != 1:
        return []
    text = parsed[0][members[0].declaration.start_byte:members[0].declaration.end_byte].decode("utf-8", errors="replace")
    out: List[Tuple[str, str, Optional[str]]] = []
    call = delegate_call(text)
    if call is not None:
        out.append((DELEGATES_TO, call[0], call[1]))
    built = constructed_type(text)
    if built is not None:
        out.append((CONSTRUCTS, built, None))
    return out


def _dependency(example: Any, touched: Sequence[str], candidate_root: str, structures: _Structures, declaring: str,
                target: str) -> Optional[Relevance]:
    if not example.method:
        return None
    parsed = structures.get(declaring)
    for relationship, simple, method in _reached(parsed, example) if parsed else []:
        hit = _callee_touched(relationship, simple, method, touched, candidate_root, structures)
        if hit is not None:
            path, qualified = hit
            return Relevance(RELEVANT_DIRECT_DEPENDENCY, f"{target} {relationship} {qualified}, declared in a file the unit touched",
                             target=target, relationship=relationship, related_type=qualified, declaring_path=declaring,
                             touched_path=path, provenance=PROVENANCE)
    for path in touched:  # a touched type directly overrides the target's method
        if path.endswith(".java") and "src/main/java/" in f"/{path}" and path != declaring:
            overriding = _overriding_type(structures, path, example.qualified_type, example.method)
            if overriding is not None:
                return Relevance(RELEVANT_DIRECT_DEPENDENCY,
                                 f"{overriding} directly extends/implements {example.qualified_type} and declares {example.method}",
                                 target=target, relationship=OVERRIDES, related_type=overriding, declaring_path=declaring,
                                 touched_path=path, provenance=PROVENANCE)
    return None


def _callee_touched(relationship: str, simple: Optional[str], method: Optional[str], touched: Sequence[str], candidate_root: str,
                    structures: _Structures) -> Optional[Tuple[str, str]]:
    """(touched path, qualified type) when ``simple`` names exactly one project main type, declared in a touched file
    (the target's own file was already decided by the direct rule), that declares the called method (DELEGATES_TO) or the type itself (CONSTRUCTS)."""
    if not simple:
        return None
    qualified, _why = resolve_java_type(candidate_root, simple)
    if qualified is None:
        return None
    path = main_source_path(candidate_root, qualified)
    if path is None or path not in touched:
        return None
    return (path, qualified) if _declares(structures, path, simple, method if relationship == DELEGATES_TO else None) else None


def assess_all(examples: Sequence[Any], touched_paths: Sequence[str], candidate_root: Optional[str]) -> Tuple[Relevance, ...]:
    structures = _Structures(candidate_root) if candidate_root else None
    return tuple(assess(example, touched_paths, candidate_root, structures) for example in examples)
