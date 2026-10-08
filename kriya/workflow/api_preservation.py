"""VERIFICATION-CONTRACT-003: the public-API preservation predicate (Python).

"Do not change the public API", "no public signature may change": a static
repository predicate decided from the base revision and the candidate, never
from a model. For every public Python module of the project (no path
component starting with ``_`` except ``__init__``, no test-side file), the
public surface is every module-level function, class, class method (plus
``__init__``), every public name bound by a module-level import (a
re-export), every public module-level assignment (a constant: its literal
value when it is one) and ``__all__`` entry whose name does not start with
``_``, rendered as a signature string (parameters with their kinds, default
presence and values, annotations). Compared base versus candidate:

- a public symbol absent from the candidate, or present with another
  signature, is deterministic counter-evidence (the API_PRESERVATION claim is
  VIOLATED, whatever any verifier said);
- every base symbol present and unchanged closes the claim (SATISFIED); added
  symbols are recorded, never a violation (an addition does not change what
  existed);
- a file that does not parse on either side, or an unreadable base, is
  UNAVAILABLE: the claim stays open (fail closed).

Java (JAVA-API-PRESERVATION-PREDICATE-001, BACKEND-READINESS-004): the same
predicate from the code-intelligence structural parser (tree-sitter stays in
kriya/code_intel/parsing.py): for every main-side ``.java`` compilation unit,
the public surface is every ``public``/``protected`` type reachable through
public/protected enclosing types (interface and annotation members are public
unless ``private``), with its kind, API-relevant modifiers (``final``,
``abstract``, ``static``, ``sealed``...), supertypes and, per member, the
return type, parameter types (overloads are distinct keys), ``throws`` clause
and field type. Parameter NAMES are not surface (Java has no named
arguments); annotations are not surface. A narrowed visibility, a removed
member, a changed signature, a class made ``final``/``abstract`` or a changed
hierarchy is VIOLATED; a file the parser cannot parse cleanly on either side
is UNAVAILABLE. Other languages stay authority-required.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from kriya.workflow.named_test_oracle import BaseTree, _is_test_side
from kriya.workflow.obligations import ObligationLedger, ObligationStatus
from kriya.workflow.requirements import (
    API_PRESERVATION,
    API_PRESERVATION_METHOD,
    RequirementSet,
    record_requirement_claim,
    requirement_claim_record,
    requirement_obligation_id,
)

API_PRESERVATION_VERSION = 1
API_PRESERVED = "API_PRESERVED"
API_CHANGED = "API_CHANGED"
API_EVIDENCE_UNAVAILABLE = "API_EVIDENCE_UNAVAILABLE"


def _public_module(path: str) -> bool:
    if not path.endswith(".py") or _is_test_side(path):
        return False
    parts = path[:-3].split("/")
    return all(not part.startswith("_") or part == "__init__" for part in parts)


def _module_name(path: str) -> str:
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__root__"


# ------------------------------------------------------------ Java (code-intelligence structural model)

_JAVA_API_MODIFIERS = frozenset({"public", "protected", "private", "static", "final", "abstract", "default",
                                 "sealed", "non-sealed"})
_JAVA_IMPLICIT_PUBLIC_OWNERS = frozenset({"interface", "annotation"})
_JAVA_TYPE_KINDS = frozenset({"class", "interface", "enum", "record", "annotation"})
_THROWS = re.compile(r"\bthrows\s+([^{;]+)$")


def _public_java_unit(path: str) -> bool:
    return path.endswith(".java") and not _is_test_side(path) and not path.endswith(("/package-info.java", "/module-info.java"))


def _java_visible(symbol: Any, owner: Optional[Any]) -> bool:
    """public/protected by modifier, or an implicitly public interface/annotation member, or an enum constant."""
    modifiers = set(symbol.modifiers)
    if "private" in modifiers:
        return False
    if "public" in modifiers or "protected" in modifiers:
        return True
    if symbol.kind == "enum_constant" or "record_component" in modifiers:
        return True
    return owner is not None and owner.kind in _JAVA_IMPLICIT_PUBLIC_OWNERS and symbol.kind != "field"


def _java_signature(symbol: Any) -> str:
    modifiers = " ".join(sorted(m for m in symbol.modifiers if m in _JAVA_API_MODIFIERS))
    if symbol.kind in _JAVA_TYPE_KINDS:
        # declaration order of supertypes is not surface
        return (f"{symbol.kind}[{modifiers}] extends({', '.join(sorted(symbol.extends))}) "
                f"implements({', '.join(sorted(symbol.implements))})")
    if symbol.is_callable:
        throws = _THROWS.search(symbol.signature_text or "")
        clause = ", ".join(sorted(t.strip() for t in throws.group(1).split(","))) if throws else ""
        return f"{symbol.kind}[{modifiers}] {symbol.return_type or ''} ({', '.join(symbol.parameter_types)}) throws({clause})"
    return f"{symbol.kind}[{modifiers}] {symbol.return_type or ''}"


def java_public_signatures(source: bytes, path: str) -> Dict[str, str]:
    """``package.Type[.Member(params)]`` -> signature for the public/protected
    surface of one compilation unit. Raises SyntaxError when the structural
    parser cannot parse the file cleanly (fail closed, like a Python file
    that does not parse)."""
    from kriya.code_intel.model import ParseState
    from kriya.code_intel.parsing import parse_file

    structure = parse_file(path, source)
    if structure.state is not ParseState.PARSED:
        raise SyntaxError(f"{path}: {structure.state.value} {structure.detail}".strip())
    by_id = {symbol.symbol_id: symbol for symbol in structure.symbols}
    visible: Dict[str, bool] = {}

    def reachable(symbol: Any) -> bool:
        if symbol.symbol_id in visible:
            return visible[symbol.symbol_id]
        owner = by_id.get(symbol.parent_id) if symbol.parent_id else None
        result = _java_visible(symbol, owner) and (owner is None or reachable(owner))
        visible[symbol.symbol_id] = result
        return result

    surface: Dict[str, str] = {}
    for symbol in structure.symbols:
        if symbol.kind not in _JAVA_TYPE_KINDS and not symbol.is_callable and symbol.kind not in ("field", "enum_constant"):
            continue
        if not reachable(symbol):
            continue
        key = symbol.lookup_key + (f"({', '.join(symbol.parameter_types)})" if symbol.is_callable else "")
        surface[key] = _java_signature(symbol)
    return surface


def _render_default(node: Optional[ast.AST]) -> str:
    return "" if node is None else "=" + ast.unparse(node)


def _render_annotation(node: Optional[ast.AST]) -> str:
    return "" if node is None else ":" + ast.unparse(node)


def render_signature(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A stable text of the function's parameter list and return annotation."""
    args = function.args
    rendered: List[str] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults: List[Optional[ast.AST]] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for index, argument in enumerate(positional):
        rendered.append(argument.arg + _render_annotation(argument.annotation) + _render_default(defaults[index]))
        if index + 1 == len(args.posonlyargs):
            rendered.append("/")
    if args.vararg is not None:
        rendered.append("*" + args.vararg.arg + _render_annotation(args.vararg.annotation))
    elif args.kwonlyargs:
        rendered.append("*")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        rendered.append(argument.arg + _render_annotation(argument.annotation) + _render_default(default))
    if args.kwarg is not None:
        rendered.append("**" + args.kwarg.arg + _render_annotation(args.kwarg.annotation))
    prefix = "async def" if isinstance(function, ast.AsyncFunctionDef) else "def"
    return f"{prefix}({', '.join(rendered)})" + (" -> " + ast.unparse(function.returns) if function.returns else "")


def public_signatures(source: bytes, module: str) -> Dict[str, str]:
    """``module.qualname`` -> signature for the public surface of one module.
    Raises SyntaxError for a file that does not parse."""
    tree = ast.parse(source.decode("utf-8"))
    surface: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            surface[f"{module}.{node.name}"] = render_signature(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            surface[f"{module}.{node.name}"] = f"class({bases})"
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not member.name.startswith("_") or member.name == "__init__"):
                    surface[f"{module}.{node.name}.{member.name}"] = render_signature(member)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            try:
                names = ast.literal_eval(node.value)
            except ValueError:
                names = None
            if isinstance(names, (list, tuple)):
                surface[f"{module}.__all__"] = "[" + ", ".join(sorted(str(name) for name in names)) + "]"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # Review VC3-R5: a public module-level name (a constant, a default) is API surface.
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    try:
                        value = "value=" + repr(ast.literal_eval(node.value)) if node.value is not None else "unbound"
                    except ValueError:
                        value = "non-literal"
                    surface[f"{module}.{target.id}"] = f"name({value})"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Review VC3-R5: a public name bound by a module-level import is a re-export.
            for alias in node.names:
                bound = (alias.asname or alias.name).split(".")[0]
                if bound != "*" and not bound.startswith("_"):
                    origin = (f"from {'.' * node.level}{node.module or ''} import {alias.name}"
                              if isinstance(node, ast.ImportFrom) else f"import {alias.name}")
                    surface[f"{module}.{bound}"] = f"reexport({origin})"
    return surface


@dataclass(frozen=True)
class ApiComparison:
    available: bool
    reason: Optional[str] = None
    removed: Tuple[str, ...] = ()
    changed: Tuple[str, ...] = ()
    added: Tuple[str, ...] = ()
    compared_files: Tuple[str, ...] = ()
    base_symbols: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def preserved(self) -> bool:
        return self.available and not self.removed and not self.changed

    def to_dict(self) -> Dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "removed": list(self.removed),
                "changed": list(self.changed), "added": list(self.added), "compared_files": list(self.compared_files),
                "base_symbols": self.base_symbols, "predicate_version": API_PRESERVATION_VERSION, **self.detail}


API_PREDICATE_LANGUAGES = frozenset({"python", "java"})


def _surface(language: str, path: str, data: bytes) -> Dict[str, str]:
    if language == "java":
        return java_public_signatures(data, path)
    return public_signatures(data, _module_name(path))


def compare_public_api(
    candidate_root: str, base_revision: Optional[str], *, candidate_files: Iterable[str],
    read_candidate: Any, language: str = "python",
) -> ApiComparison:
    """Base (git revision, read without checkout) versus candidate (bytes via
    ``read_candidate(path)``, None when absent) over every public unit of
    ``language`` (Python module / Java compilation unit) tracked at the base
    or present in the candidate."""
    if language not in API_PREDICATE_LANGUAGES:
        return ApiComparison(False, f"no public-API predicate for language {language!r}")
    if not base_revision:
        return ApiComparison(False, "no authorized base revision")
    try:
        base = BaseTree(candidate_root, base_revision)
    except Exception as error:
        return ApiComparison(False, f"base revision unreadable: {type(error).__name__}")
    public = _public_java_unit if language == "java" else _public_module
    paths = sorted({p for p in base.paths if public(p)} | {p for p in candidate_files if public(p)})
    base_bytes = base.read_many(paths)
    before: Dict[str, str] = {}
    after: Dict[str, str] = {}
    for path in paths:
        try:
            if base_bytes.get(path) is not None:
                before.update(_surface(language, path, base_bytes[path]))
            current = read_candidate(path)
            if current is not None:
                after.update(_surface(language, path, current))
        except (SyntaxError, UnicodeDecodeError) as error:
            return ApiComparison(False, f"{path} does not parse: {type(error).__name__}", compared_files=tuple(paths))
    removed = tuple(sorted(symbol for symbol in before if symbol not in after))
    changed = tuple(sorted(symbol for symbol in before if symbol in after and after[symbol] != before[symbol]))
    added = tuple(sorted(symbol for symbol in after if symbol not in before))
    return ApiComparison(True, None, removed, changed, added, tuple(paths), len(before),
                         detail={"language": language,
                                 "changed_signatures": {symbol: {"base": before[symbol], "candidate": after[symbol]}
                                                        for symbol in changed}})


def close_api_preservation_requirements(
    ledger: ObligationLedger, requirements: RequirementSet, *, contract_claims: Mapping[str, Sequence[str]],
    comparison: ApiComparison, source: str, revision: Any,
) -> List[Dict[str, Any]]:
    """Record the API_PRESERVATION claim of every requirement the contract
    binds to the predicate, from one comparison: SATISFIED when preserved,
    VIOLATED when a public symbol was removed or changed, INDETERMINATE when
    the evidence is unavailable. The record carries the contract's required
    claims so the requirement closes only when every claim is closed."""
    attempts: List[Dict[str, Any]] = []
    for requirement in requirements.requirements:
        claims = tuple(contract_claims.get(requirement.id) or ())
        if API_PRESERVATION not in claims:
            continue
        record = ledger.current(requirement_obligation_id(requirement.id))
        evidence_id = (record.evidence or {}).get("evidence_id") if record is not None else None
        entry: Dict[str, Any] = {"requirement": requirement.id, "kind": API_PRESERVATION_METHOD, "closed": False,
                                 **comparison.to_dict()}
        if not evidence_id:
            entry["reason"] = "the verdict has no evidence id to bind to"
            attempts.append(entry)
            continue
        if not comparison.available:
            status, code = ObligationStatus.INDETERMINATE, API_EVIDENCE_UNAVAILABLE
            entry["reason"] = str(comparison.reason)
        elif comparison.preserved:
            status, code = ObligationStatus.SATISFIED, API_PRESERVED
        else:
            status, code = ObligationStatus.VIOLATED, API_CHANGED
            entry["reason"] = ("public API changed: removed " + ", ".join(comparison.removed) + "; changed "
                               + ", ".join(comparison.changed)).replace("removed ; ", "")
            entry["violated"] = True
        record_requirement_claim(ledger, requirements, requirement.id, API_PRESERVATION, evidence_id=evidence_id,
                                 method=API_PRESERVATION_METHOD,
                                 detail={**comparison.to_dict(), "reason_code": code, "required_claims": list(claims)},
                                 source=source, revision=revision, status=status)
        entry["reason_code"] = code
        current = requirement_claim_record(ledger, requirement.id, API_PRESERVATION, evidence_id)
        entry["claim_status"] = current.status.value if current is not None else None
        attempts.append(entry)
    return attempts
