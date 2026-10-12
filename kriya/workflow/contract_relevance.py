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
  or constructs ``new Type(`` (CONSTRUCTS) - the call being the WHOLE returned expression, matching the callee's arity, on a
  project type (not shadowed by an explicit import) that is declared in a file the unit touched. A subclass/implementor is NOT a
  relation: every compiled example form calls the named type itself (``Type.m(..)`` / ``new Type(..).m(..)``), so dispatch never
  reaches a subtype;
- UNKNOWN: a needed fact cannot be read (target file not unique/unparsable, overloaded factory, ambiguous callee type). UNKNOWN
  is never promoted: the example waits for the terminal gate;
- NOT_RELEVANT: every checked relation is absent.

Model text never takes part. Terminal acceptance does not consult this module."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.code_intel.delegation import last_return_expression
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
PROVENANCE = "tree-sitter-structural; delegate reader (code_intel.delegation)"


@dataclass(frozen=True)
class Relevance:
    status: str
    reason: str = ""
    target: str = ""  # Type.method the example calls
    relationship: str = ""  # DELEGATES_TO | CONSTRUCTS for a dependency
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
        self._types: Dict[str, Tuple[Optional[str], str, Optional[str]]] = {}  # simple name -> (qualified, why, path)

    def resolve(self, simple: str) -> Tuple[Optional[str], str, Optional[str]]:
        """(qualified name, why, main file) of the project type a simple name denotes; one tree walk per name per assessment."""
        if simple not in self._types:
            qualified, why = resolve_java_type(self.root, simple)
            self._types[simple] = (qualified, why, main_source_path(self.root, qualified) if qualified else None)
        return self._types[simple]

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
    found = _dependency(example, touched, structures, declaring, target)
    return found if found is not None else _no_relation(example, structures, declaring, target)


def _no_relation(example: Any, structures: _Structures, declaring: str, target: str) -> Relevance:
    reasons = _unknown_reasons(example, structures, declaring)
    if reasons:
        return Relevance(UNKNOWN, "; ".join(reasons), target=target, declaring_path=declaring)
    return Relevance(NOT_RELEVANT, "no touched file is the target's direct delegate/construct callee or an overriding type",
                     target=target, declaring_path=declaring)


def _unknown_reasons(example: Any, structures: _Structures, declaring: str) -> List[str]:
    if not example.method:
        return []
    parsed = structures.get(declaring)
    if parsed is None:
        return [f"{declaring} has no complete structural parse"]
    if len(_factory_members(parsed, example)) != 1:
        return [f"{example.qualified_type}.{example.method} is not declared exactly once in {declaring}"]
    return [f"callee type {simple} is ambiguous" for _relationship, simple, _method, _arity in _reached(parsed, example)
            if "ambiguous" in structures.resolve(simple)[1]]


def _factory_members(parsed: Tuple[bytes, FileStructure], example: Any) -> List[Any]:
    _data, structure = parsed
    return [s for s in structure.symbols if s.is_callable and s.lookup_key == f"{example.qualified_type}.{example.method}"]


_DELEGATE_EXPR = re.compile(r"^\s*(?:[a-z]\w*\.)*([A-Z]\w*)\.([a-z_]\w*)\s*\(")
_NEW_EXPR = re.compile(r"^\s*new\s+([A-Z]\w*)\s*(?:<[^()]*>)?\s*\(")


def _whole_call_arity(expression: str, opening: int) -> Optional[int]:
    """Number of top-level arguments of the call whose '(' is at ``opening`` when that call is the WHOLE expression (its ')'
    ends it), else None. Literals are already blanked; a generic comma inside an argument can only over-count, which makes the
    arity check fail closed (no relation)."""
    depth, count, seen = 0, 0, False
    for index in range(opening, len(expression)):
        ch = expression[index]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return (count + 1 if seen else 0) if not expression[index + 1:].strip() else None
        elif depth == 1 and ch == ",":
            count += 1
        elif depth == 1 and not ch.isspace():
            seen = True
    return None


def _reached(parsed: Tuple[bytes, FileStructure], example: Any) -> List[Tuple[str, str, Optional[str], int]]:
    """(relationship, simple callee type, callee method, argument count) of the target method's last ``return`` when that
    expression IS a single static call or a single construction of a type: one hop, no traversal, nothing nested."""
    members = _factory_members(parsed, example)
    if len(members) != 1:
        return []
    text = parsed[0][members[0].declaration.start_byte:members[0].declaration.end_byte].decode("utf-8", errors="replace")
    expression = last_return_expression(text)
    if expression is None:
        return []
    out: List[Tuple[str, str, Optional[str], int]] = []
    call, built = _DELEGATE_EXPR.match(expression), _NEW_EXPR.match(expression)
    if call is not None and (arity := _whole_call_arity(expression, call.end() - 1)) is not None:
        out.append((DELEGATES_TO, call.group(1), call.group(2), arity))
    if built is not None and (arity := _whole_call_arity(expression, built.end() - 1)) is not None:
        out.append((CONSTRUCTS, built.group(1), None, arity))
    return out


def _dependency(example: Any, touched: Sequence[str], structures: _Structures, declaring: str, target: str) -> Optional[Relevance]:
    if not example.method:
        return None
    parsed = structures.get(declaring)
    for relationship, simple, method, arity in _reached(parsed, example) if parsed else []:
        hit = _callee_touched(relationship, simple, method, arity, touched, structures, declaring)
        if hit is not None:
            path, qualified = hit
            return Relevance(RELEVANT_DIRECT_DEPENDENCY, f"{target} {relationship} {qualified}, declared in a file the unit touched",
                             target=target, relationship=relationship, related_type=qualified, declaring_path=declaring,
                             touched_path=path, provenance=PROVENANCE)
    return None


def _accepts(callables: List[Any], arity: int) -> bool:
    """Whether some callable takes ``arity`` arguments (a varargs callable takes its fixed count or more). A type that declares
    no constructor has the implicit no-argument one."""
    return any((len(c.parameter_types) == arity) or (c.parameter_types and c.parameter_types[-1].endswith("...")
                                                     and arity >= len(c.parameter_types) - 1) for c in callables)


def _callee_touched(relationship: str, simple: str, method: Optional[str], arity: int, touched: Sequence[str],
                    structures: _Structures, declaring: str) -> Optional[Tuple[str, str]]:
    """(touched path, qualified type) when ``simple`` names exactly one project main type - not shadowed by an explicit import
    of another type of that name in the target's file - declared in a touched file (the target's own file was already decided
    by the direct rule), that declares a callable of the called name and arity (DELEGATES_TO) or a constructor of the called
    arity (CONSTRUCTS)."""
    qualified, _why, path = structures.resolve(simple)
    if qualified is None or path is None or path not in touched:
        return None
    own = structures.get(declaring)
    if own is not None and any(i != qualified and i.rsplit(".", 1)[-1] == simple for i in own[1].imports):
        return None
    parsed = structures.get(path)
    if parsed is None:
        return None
    structure = parsed[1]
    owners = {s.symbol_id for s in structure.symbols if s.is_type and s.name == simple}
    if not owners:
        return None
    members = [s for s in structure.symbols if s.is_callable and s.parent_id in owners
               and (s.name == method if relationship == DELEGATES_TO else s.kind == "constructor")]
    if not members:
        return (path, qualified) if relationship == CONSTRUCTS and arity == 0 else None
    return (path, qualified) if _accepts(members, arity) else None


def assess_all(examples: Sequence[Any], touched_paths: Sequence[str], candidate_root: Optional[str]) -> Tuple[Relevance, ...]:
    structures = _Structures(candidate_root) if candidate_root else None
    return tuple(assess(example, touched_paths, candidate_root, structures) for example in examples)
