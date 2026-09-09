r"""A1 (Java Repository-Aware Code Review, 2026-09-09): deterministic Java
constructor/method inventory extraction.

Why this exists, and why it is a NEW, dedicated module rather than an
extension of the existing `JAVA_METHOD_SIGNATURE_CORE` regex
(`kriya/analyzer/analyzer.py`): that regex is deliberately narrow -
built for RAG-chunk boundary detection, reused as-is by context-budget
skeletonization and attribution line-matching. It structurally requires a
"modifiers* returnType name(" shape, which real Java syntax never gives a
constructor (no return type at all) and which package-private methods
often fail to satisfy in practice once the caller has already
`.strip()`-ped the line (confirmed empirically: `_normalized_public_
signatures()`/the RAG chunker's own line-stripping removes the leading
indentation that is otherwise the ONLY thing that could satisfy that
regex's `\s` modifier alternative for an unmodified declaration).
Widening that one regex in place would risk the three existing call
sites' own established behavior for no benefit to them - A1 needs a
DIFFERENT, complete answer (constructors, every visibility, annotations,
throws, precise line span), not a wider chunk boundary. This module is
purely additive; nothing existing is changed or removed.

Reuses, rather than reimplements, two already-shipped utilities:
`_strip_java_comments_and_strings()` (kriya/workflow/edit_safety.py) for
comment/string-literal-safe scanning (line positions preserved - content
is blanked, not deleted) and `_normalize_java_type_list()`
(kriya/workflow/file_resolution.py) is NOT reused directly for parameter
splitting here (that helper's naive comma-split breaks on a generic
parameter type like `Map<String, Integer>`, a case this module's own
test matrix explicitly covers) - `_split_top_level_commas()` below is a
small, purpose-built, bracket-depth-aware alternative kept local to this
module rather than changing that shared helper's own established
behavior for its two existing callers.

Deliberately NOT a full Java grammar parser (see docstring on
`extract_java_members()` for the explicit, tested boundary of what this
scans). No new third-party dependency was introduced - see the module's
own implementation notes in the A1-P1 handoff for why a bounded,
brace-depth-aware regex scanner (the same structural pattern this
codebase already uses for `_find_duplicate_top_level_type()` and the RAG
chunker) was judged sufficient for one target file at a time, and a full
parser dependency was not."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from kriya.workflow.edit_safety import _strip_java_comments_and_strings

_TOP_LEVEL_TYPE_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")

_MODIFIER_WORDS = (
    "public", "protected", "private", "static", "final",
    "abstract", "synchronized", "native", "strictfp", "default",
)
_MODIFIER_ALT = "|".join(_MODIFIER_WORDS)

# Deliberately excludes any bare "name(...) {"/"name(...) ;" shape whose
# name is a Java control-flow keyword, not a real declaration - a
# package-private constructor ("Foo(int x) {") and a control-flow
# statement ("if (x) {") are structurally identical to a token-level
# scanner (neither has a preceding return type), so this exclusion is not
# optional. The depth==1 filter in extract_java_members() already
# excludes the overwhelming majority of these (real control flow lives
# inside a method body, at depth >= 2) - this is the remaining,
# inexpensive belt-and-suspenders check, the same discipline already
# applied to the existing RAG chunker's own method_name exclusion set.
_NOT_A_DECLARATION_NAME = frozenset({
    "if", "for", "while", "switch", "catch", "do", "try", "return",
    "class", "interface", "enum", "record", "new", "throw", "synchronized",
})

_DECL_RE = re.compile(
    r"(?P<modifiers>(?:\b(?:" + _MODIFIER_ALT + r")\b\s+)*)"
    r"(?:<[^>{}]+>\s*)?"
    r"(?:(?P<rettype>[A-Za-z_$][\w$.]*(?:\s*<[^>{}]*>)?(?:\s*\[\s*\])*(?:\s*\.\.\.)?)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*"
    r"(?:throws\s+(?P<throws>[\w\s,.<>\[\]$]+?))?\s*"
    r"(?P<term>[{;])"
)

_ANNOTATION_LINE_RE = re.compile(
    r"^\s*@([A-Za-z_$][\w$.]*)(\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*$"
)
_LEADING_ANNOTATION_RE = re.compile(
    r"^\s*@[A-Za-z_$][\w$.]*(?:\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*"
)


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


def _annotations_for(lines: List[str], decl_start_line: int, decl_line_text: str) -> Tuple[str, ...]:
    """decl_start_line is 1-indexed. Collects annotations either on their
    own immediately-preceding lines, or inline at the very start of the
    declaration's own line (e.g. "@Override public void foo() {")."""
    found: List[str] = []
    leading = decl_line_text
    m = _LEADING_ANNOTATION_RE.match(leading)
    while m:
        found.append(m.group(0).strip())
        leading = leading[m.end():]
        m = _LEADING_ANNOTATION_RE.match(leading)

    idx = decl_start_line - 2  # 0-indexed line immediately above
    while idx >= 0:
        candidate = lines[idx]
        m2 = _ANNOTATION_LINE_RE.match(candidate)
        if not m2:
            break
        found.insert(0, candidate.strip())
        idx -= 1
    # Annotation NAMES only (bare, e.g. "@Override"/"@RequestMapping") for
    # a stable, comparison-friendly value - the full text (with any
    # arguments) is still available by re-reading the source at
    # start_line for a caller that needs it.
    names = []
    for a in found:
        nm = re.match(r"@([A-Za-z_$][\w$.]*)", a)
        if nm:
            names.append("@" + nm.group(1))
    return tuple(names)


def _matching_close_brace_line(lines: List[str], stripped_lines: List[str], open_line_idx: int) -> int:
    """0-indexed open_line_idx (the line the declaration's own "{"
    appears on) -> 0-indexed line the matching "}" appears on, via simple
    depth counting over the comment/string-stripped text (line-aligned
    with the real source 1:1, since stripping preserves newlines)."""
    depth = 0
    for i in range(open_line_idx, len(lines)):
        depth += stripped_lines[i].count("{") - stripped_lines[i].count("}")
        if depth <= 0 and i >= open_line_idx:
            return i
    return len(lines) - 1


def extract_java_members(
    source: str, enclosing_type: Optional[str] = None,
) -> List[JavaMember]:
    """Deterministic, source-order constructor/method inventory for ONE
    Java compilation unit's PRIMARY (first-declared) top-level
    class/interface/enum/record. `enclosing_type`, if given, overrides
    auto-detection (useful when the caller already knows the class name
    from a filename convention).

    Scope, confirmed by this module's own test suite - explicitly
    supported:
      - constructors and methods of every visibility (public/protected/
        private/package-private), `static`, `final`, `abstract` (body-less,
        `;`-terminated - interface/abstract methods).
      - annotations (own-line or inline-leading), multiple stacked
        annotations, `throws` clauses, generic return types, generic
        parameter types (including embedded commas, e.g. `Map<String,
        Integer>`), array parameters, varargs, multiline declarations,
        overloaded constructors/methods (each overload is its own record).
      - comments (// and /* */) and string/char literal contents
        (including ones that look like a declaration or contain braces)
        are correctly never mistaken for real declarations - verified via
        the reused `_strip_java_comments_and_strings()`.

    Explicit, tested, NOT-supported/out-of-scope syntax (documented, not
    silently mishandled):
      - members of a SECOND top-level type in the same file (only the
        first-declared top-level type's own direct members, at brace
        depth 1 relative to it, are scanned - a deliberate, narrow bound,
        matching this module's own "one target file, one primary type"
        A1 scope).
      - nested/inner classes' own members (depth > 1 relative to the
        primary type is never scanned as a member - by design, this is a
        member inventory for the target type itself, not a transitive
        scan of everything the file happens to contain).
      - Java 17 text blocks (`\"\"\"...\"\"\"`) are not recognized as a
        distinct string form by the reused stripper - inherited from that
        function's own documented limitation, not new here.
      - lambda expressions and anonymous-class bodies are never
        mistaken for named declarations (no "name(params){" shape at
        depth 1 that also carries a plausible modifier/return-type
        prefix), but are not otherwise analyzed.
      - a record's COMPACT canonical constructor (`public Customer { ... }`
        - no parameter list at all, a Java-record-specific short form) is
        NOT detected - this scanner requires a parenthesized parameter
        list for every constructor. A record's ordinary (non-compact,
        explicit-parameter-list) canonical constructor IS detected
        normally, matching this module's own constructor rule. Confirmed
        via direct test, not assumed; out of scope for A1-P1's frozen
        target (an ordinary class, not a record) - documented rather than
        silently mishandled.
      - visibility is LEXICAL, not semantic: an interface member with no
        explicit modifier keyword is reported "package-private" even
        though the JLS makes every such member implicitly `public`
        (`abstract`, for a method with no body). Confirmed via direct
        test against a real interface. A caller reviewing an interface
        specifically should treat an unmodified interface member's
        reported "package-private" visibility as "public" by Java
        semantics, not take the field at face value - out of scope to
        special-case for A1-P1's frozen target (a class, not an
        interface).
    """
    lines = source.splitlines()
    stripped_source = _strip_java_comments_and_strings(source)
    stripped_lines = stripped_source.splitlines()
    if len(stripped_lines) < len(lines):
        stripped_lines.extend([""] * (len(lines) - len(stripped_lines)))

    type_match = _TOP_LEVEL_TYPE_RE.search(stripped_source)
    if enclosing_type is None:
        enclosing_type = type_match.group(1) if type_match else ""

    # Bound scanning to the PRIMARY (first-declared) top-level type's own
    # brace range - global brace depth alone can't distinguish "depth 1
    # inside the primary type" from "depth 1 inside a second, later
    # top-level type in the same file" (both are globally depth 1). Find
    # the primary type's own opening "{" (the first one at or after its
    # name), then its own matching close via the same depth-counting
    # convention used for a member's own body below.
    type_open_pos: Optional[int] = None
    type_close_pos: Optional[int] = None
    if type_match:
        brace_pos = stripped_source.find("{", type_match.end())
        if brace_pos != -1:
            type_open_pos = brace_pos
            depth = 0
            for idx in range(brace_pos, len(stripped_source)):
                ch = stripped_source[idx]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        type_close_pos = idx
                        break

    members: List[JavaMember] = []
    for m in _DECL_RE.finditer(stripped_source):
        name = m.group("name")
        if name in _NOT_A_DECLARATION_NAME:
            continue
        if type_open_pos is not None and not (type_open_pos < m.start() < (type_close_pos or len(stripped_source))):
            continue
        depth = stripped_source.count("{", 0, m.start()) - stripped_source.count("}", 0, m.start())
        if depth != 1:
            continue

        rettype = m.group("rettype")
        is_constructor = rettype is None and name == enclosing_type
        if rettype is None and not is_constructor:
            # A bare "name(params){" at depth 1 whose name doesn't match
            # the enclosing type is not a legal Java member declaration
            # (every real method requires a return type, `void` included)
            # - almost certainly a false positive (e.g. a static/instance
            # initializer block is excluded elsewhere, but stay
            # conservative). Skip rather than misclassify.
            continue

        modifiers = set(m.group("modifiers").split())
        if "private" in modifiers:
            visibility = "private"
        elif "protected" in modifiers:
            visibility = "protected"
        elif "public" in modifiers:
            visibility = "public"
        else:
            visibility = "package-private"

        param_parts = _split_top_level_commas(m.group("params"))
        parameter_types = tuple(_param_type_only(p) for p in param_parts)

        throws_raw = m.group("throws")
        throws = tuple(t.strip() for t in throws_raw.split(",")) if throws_raw else ()

        start_line = stripped_source.count("\n", 0, m.start()) + 1
        decl_line_text = lines[start_line - 1] if start_line - 1 < len(lines) else ""
        annotations = _annotations_for(lines, start_line, decl_line_text)

        term = m.group("term")
        has_body = term == "{"
        if has_body:
            # The declaration's own terminating "{" is the last character
            # matched (m.end() - 1) - the line it's on is where brace-depth
            # counting for the matching "}" must begin.
            open_line_idx = stripped_source.count("\n", 0, m.end() - 1)
            end_line = _matching_close_brace_line(lines, stripped_lines, open_line_idx) + 1
        else:
            end_line = start_line

        ret_display = rettype.strip() if rettype else None
        kind = "constructor" if is_constructor else "method"
        mod_prefix = " ".join(sorted(modifiers, key=_MODIFIER_WORDS.index)) if modifiers else visibility if visibility != "package-private" else ""
        sig_mods = (mod_prefix + " ") if mod_prefix else ""
        if kind == "constructor":
            signature = f"{sig_mods}{name}({', '.join(parameter_types)})".strip()
        else:
            signature = f"{sig_mods}{ret_display} {name}({', '.join(parameter_types)})".strip()

        members.append(JavaMember(
            kind=kind,
            name=name,
            visibility=visibility,
            is_static="static" in modifiers,
            is_final="final" in modifiers,
            is_abstract=(not has_body) or "abstract" in modifiers,
            return_type=ret_display,
            parameter_types=parameter_types,
            annotations=annotations,
            throws=throws,
            signature=signature,
            start_line=start_line,
            end_line=end_line,
            enclosing_type=enclosing_type,
            has_body=has_body,
        ))

    members.sort(key=lambda mm: mm.start_line)
    return members
