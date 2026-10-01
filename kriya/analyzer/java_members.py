r"""A1 (Java Repository-Aware Code Review, 2026-09-09): deterministic Java
constructor/method inventory extraction.

Code Intelligence R1: the inventory now comes from the tree-sitter
structural model (``kriya/code_intel``) - the regex/brace-depth scanner this
module used to carry is gone. The output contract (``JavaMember``, source
order, the primary type's own direct members, lexical visibility, ``@Name``
annotations, parameter types without names/annotations/``final``) is
unchanged, so every consumer (member boundaries, review context, semantic
region authority, proposal binding) keeps its behavior while gaining what
the scanner missed: record compact constructors, comment/text-block-safe
parsing and real spans for any declaration shape.

``_split_top_level_commas``/``_param_type_only`` stay: semantic region
authority uses them on its own declaration text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_MODIFIER_WORDS = (
    "public", "protected", "private", "static", "final",
    "abstract", "synchronized", "native", "strictfp", "default",
)
_THROWS_RE = re.compile(r"\bthrows\s+(.+)$")


@dataclass(frozen=True)
class JavaMember:
    """One deterministically-scanned constructor or method declaration.
    `signature` is a human-readable normalized form for display/prompting
    (e.g. "public DriverDO find(Long)" or "public DefaultDriverService(
    DriverRepository)") - NOT the same identity key
    `_normalized_public_signatures()` (file_resolution.py) uses for
    brownfield diffing; the two modules serve different purposes and are
    deliberately not unified."""

    kind: str  # "constructor" | "method"
    name: str
    visibility: str  # "public" | "protected" | "private" | "package-private"
    is_static: bool
    is_final: bool
    is_abstract: bool
    return_type: Optional[str]  # None for a constructor
    parameter_types: Tuple[str, ...]
    annotations: Tuple[str, ...]
    throws: Tuple[str, ...]
    signature: str
    start_line: int  # 1-indexed, the declaration's own first line
    end_line: int  # 1-indexed; == start_line for a body-less (interface) declaration
    enclosing_type: str
    has_body: bool


def _split_top_level_commas(raw: str) -> List[str]:
    """Splits on "," only at bracket depth 0 - unlike
    `_normalize_java_type_list()`'s own naive `raw.split(",")`
    (file_resolution.py), this does not break a generic parameter type
    like `Map<String, Integer> data` into two spurious pieces. Kept local
    to this module rather than changing that shared helper, which two
    existing, unrelated call sites already depend on for exactly its
    current (simpler, sufficient-for-them) behavior."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in raw:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p.strip() for p in parts if p.strip()]


def _param_type_only(param: str) -> str:
    """One already-top-level-comma-split parameter -> its bare type,
    stripping leading annotations, `final`, and the trailing parameter
    name. Varargs (`String... args`) is normalized to `String...` (kept
    distinguishable from a plain array parameter, `String[]`, which stays
    `String[]`)."""
    p = re.sub(r"@[A-Za-z_$][\w$.]*(?:\([^()]*\))?\s*", "", param).strip()
    p = re.sub(r"\bfinal\s+", "", p).strip()
    varargs = p.endswith("...")
    if varargs:
        p = p[:-3].strip()
    # Strip the trailing identifier (the parameter's own name), leaving
    # only the type - the same normalization rule
    # `_normalize_java_type_list()` already applies, just depth-aware.
    p = re.sub(r"\s+[A-Za-z_$][\w$]*(?:\s*\[\s*\])*$", "", p).strip()
    return p + "..." if varargs else p


def extract_java_members(
    source: str, enclosing_type: Optional[str] = None,
) -> List[JavaMember]:
    """Source-order constructor/method inventory of ONE Java compilation
    unit's PRIMARY (first-declared) top-level type: its own direct members
    (enum body declarations included), never a nested type's or a second
    top-level type's. ``enclosing_type`` overrides the reported type name.

    Visibility is LEXICAL: an interface member without a modifier reports
    "package-private" although the JLS makes it public. A body-less
    declaration (interface/abstract/native) has ``end_line == start_line``.
    ``start_line`` is the declaration's first non-annotation token (its
    annotations are reported in ``annotations``). A file the parser cannot
    read at all yields no members - never a guess."""
    from kriya.code_intel.model import ParseState
    from kriya.code_intel.parsing import parse_text

    structure = parse_text("Member.java", source)
    if structure.state is ParseState.PARSE_FAILED:
        return []
    primary = next((s for s in structure.symbols if s.is_type and s.parent_id is None), None)
    if primary is None:
        return []
    owner = enclosing_type if enclosing_type is not None else primary.name
    members: List[JavaMember] = []
    for symbol in structure.symbols:
        if symbol.parent_id != primary.symbol_id or symbol.kind not in ("method", "constructor"):
            continue
        modifiers = {m for m in symbol.modifiers if m in _MODIFIER_WORDS}
        if "private" in modifiers:
            visibility = "private"
        elif "protected" in modifiers:
            visibility = "protected"
        elif "public" in modifiers:
            visibility = "public"
        else:
            visibility = "package-private"
        throws_match = _THROWS_RE.search(symbol.signature_text)
        throws = tuple(_split_top_level_commas(throws_match.group(1))) if throws_match else ()
        has_body = symbol.body is not None
        start_line = symbol.signature.start_line
        mod_prefix = " ".join(sorted(modifiers, key=_MODIFIER_WORDS.index)) if modifiers else (
            visibility if visibility != "package-private" else "")
        sig_mods = (mod_prefix + " ") if mod_prefix else ""
        params = ", ".join(symbol.parameter_types)
        if symbol.kind == "constructor":
            signature = f"{sig_mods}{symbol.name}({params})".strip()
        else:
            signature = f"{sig_mods}{symbol.return_type} {symbol.name}({params})".strip()
        members.append(JavaMember(
            kind=symbol.kind,
            name=symbol.name,
            visibility=visibility,
            is_static="static" in modifiers,
            is_final="final" in modifiers,
            is_abstract=(not has_body) or "abstract" in modifiers,
            return_type=symbol.return_type if symbol.kind == "method" else None,
            parameter_types=symbol.parameter_types,
            annotations=tuple("@" + a for a in symbol.annotations),
            throws=throws,
            signature=signature,
            start_line=start_line,
            end_line=symbol.body.end_line if has_body else start_line,
            enclosing_type=owner,
            has_body=has_body,
        ))
    members.sort(key=lambda m: m.start_line)
    return members
