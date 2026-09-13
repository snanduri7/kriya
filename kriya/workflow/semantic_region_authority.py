"""CORR-018-P1 (A3-bound slice, 2026-09-09): deterministic Java semantic-region
authority enforcement.

FILE WRITE AUTHORITY != SEMANTIC CHANGE AUTHORITY. `AuthorizedFileWriter`
(kriya/policy/filesystem.py) decides which FILES a candidate may write to - it
has zero content awareness by design (path-scope only) and this module does
not change that. This module decides, for a file already inside write scope,
which SOURCE REGIONS may materially change. A file being writable has never
meant every byte inside it is semantically authorized to change - this closes
that gap for the narrow case where an authority has been made explicit
up front (see CORR-018 A3-D2 investigation memory for the full architecture
trace and the P10 production incident this addresses).

Deliberately separate from, and run AFTER (never merged into),
find_brownfield_public_api_changes() (CORR-016, kriya/workflow/file_resolution.py).
That function asks "did a protected public signature change without
authority?" - this module asks "did anything structurally material change
outside explicitly authorized regions?", including inside a signature-
preserving method body (exactly the P10 gap CORR-016 cannot see, since a
body-only change never touches a public signature at all). A legitimately
authorized signature change is represented here as a METHOD_SIGNATURE/
CONSTRUCTOR_SIGNATURE region - CORR-016 still independently decides whether
the signature delta itself is authorized; this module never re-litigates
that, it only decides whether the region shape is covered.

`authorized_regions` must always be supplied explicitly by the caller -
never inferred from candidate content, Planner text, or file-write scope
(Invariant: file authority != semantic authority). Two independent sources
of `authorized_regions` exist in production: (1) an approved
`ProposedModification` (A3's own proposal-promotion path,
kriya/workflow/proposal_binding.py - unchanged by the CORR-018 general-case
closure below), and (2) `kriya/workflow/semantic_scope_derivation.py`
(CORR-018 general-case closure, 2026-09-13) - grounding_goal-derived and
deterministic-repository-relationship-derived regions for ordinary
generate/fix runs, gated behind `autonomy.semantic_region_enforcement_
required` (default False - see that module's own docstring for why an
unconditional default was rejected). An empty/absent `authorized_regions`
sequence for a given file means this module performs NO PER-REGION checks
on that file - `find_unauthorized_semantic_changes`'s own
`strict_existing_java_files` parameter (default False, opt-in, wired only
when the flag above is True) is the ONLY thing that turns an unlisted
EXISTING .java file's own change into a rejection instead of a silent
no-op; every existing caller that does not pass it keeps byte-identical
behavior.

V1 SCOPE (Java only): top-level class/interface/record methods,
constructors, field declarations, record components, and imports.
Nested/inner class members, record compact constructors, multi-line or
comma-separated (`int a, b;`) field declarations, generated/Lombok/
annotation-processor source, and arbitrary class-level restructuring are
explicitly OUT of scope - any material change there is caught only as an
undifferentiated "residual region changed" and rejected by default (no
WHOLE_FILE escape hatch anywhere in this module - see RegionType's own
docstring). This is an honest, stated restriction, not a silent gap: a
construct this module cannot recognize can only ever be MORE protected
(falls to the residual catch-all), never less.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from kriya.analyzer.java_members import (
    JavaMember,
    _param_type_only,
    _split_top_level_commas,
    extract_java_members,
)
from kriya.workflow.edit_safety import _strip_java_comments_and_strings

REASON_REGION_UNAUTHORIZED = "SEMANTIC_REGION_UNAUTHORIZED"
REASON_MEMBER_DELETED = "SEMANTIC_MEMBER_DELETED"
REASON_MEMBER_ADDED_UNAUTHORIZED = "SEMANTIC_MEMBER_ADDED_UNAUTHORIZED"
REASON_SIGNATURE_UNAUTHORIZED = "SEMANTIC_SIGNATURE_UNAUTHORIZED"
REASON_RESIDUAL_REGION_CHANGED = "SEMANTIC_RESIDUAL_REGION_CHANGED"
REASON_IMPORT_UNAUTHORIZED = "SEMANTIC_IMPORT_UNAUTHORIZED"
REASON_SCAN_AMBIGUOUS = "SEMANTIC_SCAN_AMBIGUOUS"
# CORR-018 general-case closure (2026-09-13): declaration-shaped peers of
# the member reason codes above, reused verbatim in spirit (deleted/added/
# changed-without-authority) for FIELD_DECLARATION/RECORD_COMPONENT - kept
# as distinct string values (never the SAME code as a method/constructor
# violation) so a caller's diagnostics can always tell which construct
# kind was actually rejected, without inventing a second violation shape.
REASON_DECLARATION_DELETED = "SEMANTIC_DECLARATION_DELETED"
REASON_DECLARATION_ADDED_UNAUTHORIZED = "SEMANTIC_DECLARATION_ADDED_UNAUTHORIZED"
REASON_DECLARATION_UNAUTHORIZED = "SEMANTIC_DECLARATION_UNAUTHORIZED"
# Strict mode only (find_unauthorized_semantic_changes(...,
# strict_existing_java_files=True)) - an EXISTING .java file whose content
# actually changed but that has ZERO entries anywhere in authorized_regions
# at all (today's non-strict behavior treats this as "outside this run's
# authority scope, not this function's concern" and is silently a no-op for
# that file - strict mode makes that silence a rejection instead).
REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY = "SEMANTIC_FILE_HAS_NO_AUTHORITY"


class RegionType(str, Enum):
    """Deliberately NOT including WHOLE_FILE as a normal, grantable region -
    per explicit instruction, a proposal that needs whole-file semantic
    authority is too broad for A3's surgical mode and must go through an
    ordinary, broader user goal instead. Every region here is either a
    specific existing member (BODY/SIGNATURE) or a specific new one
    (ADD/TEST_*), never "everything in this file"."""
    METHOD_BODY = "METHOD_BODY"
    CONSTRUCTOR_BODY = "CONSTRUCTOR_BODY"
    METHOD_SIGNATURE = "METHOD_SIGNATURE"
    CONSTRUCTOR_SIGNATURE = "CONSTRUCTOR_SIGNATURE"
    METHOD_ADD = "METHOD_ADD"
    CONSTRUCTOR_ADD = "CONSTRUCTOR_ADD"
    IMPORTS = "IMPORTS"
    TEST_METHOD_ADD = "TEST_METHOD_ADD"
    TEST_FILE_ADD = "TEST_FILE_ADD"
    # CORR-018 general-case closure (2026-09-13): ordinary class/interface
    # field declarations (top-level members of the primary type only - see
    # _extract_field_declarations()'s own docstring for the exact scanned
    # shape) and Java record components (the record header's own
    # parenthesized component list - a distinct construct, never modeled
    # as METHOD_SIGNATURE/CONSTRUCTOR_SIGNATURE/FIELD_DECLARATION even
    # though a record's accessor methods and canonical constructor are
    # implicitly derived from its components - this region authorizes ONLY
    # the component-list text itself, never any accessor/constructor body,
    # per the closure task's own explicit instruction).
    FIELD_DECLARATION = "FIELD_DECLARATION"
    RECORD_COMPONENT = "RECORD_COMPONENT"


_BODY_REGIONS = frozenset({RegionType.METHOD_BODY, RegionType.CONSTRUCTOR_BODY})
_SIGNATURE_REGIONS = frozenset({RegionType.METHOD_SIGNATURE, RegionType.CONSTRUCTOR_SIGNATURE})
_ADD_REGIONS = frozenset({
    RegionType.METHOD_ADD, RegionType.CONSTRUCTOR_ADD, RegionType.TEST_METHOD_ADD,
})
# A field/record-component has no body/signature split - one declaration,
# one authorization. Kept as its own frozenset (not folded into
# _SIGNATURE_REGIONS) so a *_SIGNATURE authorization can never accidentally
# be read as covering a field/record-component change, or vice versa.
_DECLARATION_REGIONS = frozenset({RegionType.FIELD_DECLARATION, RegionType.RECORD_COMPONENT})


@dataclass(frozen=True)
class AuthorizedSemanticRegion:
    """One explicitly granted authority. `member_key` is required for every
    region type except IMPORTS/TEST_FILE_ADD (a whole-file grant with no
    single member). `successor_key`, only for a *_SIGNATURE region, names
    the member key the candidate is expected to rename/re-type the baseline
    member INTO - required so a legitimate signature change (baseline key
    vanishes, a new key appears) is matched to its authorized successor
    instead of being seen as an unrelated delete+add (Part 7 of the CORR-018
    A3-D2 investigation). `source` is audit-only provenance (e.g.
    "a3_proposal:P1") - never derived from Planner text or candidate
    content; nothing in this module reads it for a decision."""
    relpath: str
    region_type: RegionType
    member_key: Optional[str] = None
    successor_key: Optional[str] = None
    source: str = "a3_proposal"


@dataclass(frozen=True)
class MemberSnapshot:
    stable_key: str
    kind: str  # "constructor" | "method", mirrors JavaMember.kind
    name: str
    normalized_signature: str
    body_hash: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DeclarationSnapshot:
    """A single field declaration or record component - simpler than
    MemberSnapshot by design: there is no body/signature split (one
    declaration, one hash), matching this construct's own single-region
    authorization model (FIELD_DECLARATION/RECORD_COMPONENT)."""
    stable_key: str
    kind: str  # "field" | "record_component"
    name: str
    declaration_hash: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FileSnapshot:
    relpath: str
    members: Dict[str, MemberSnapshot] = field(default_factory=dict)
    fields: Dict[str, DeclarationSnapshot] = field(default_factory=dict)
    record_components: Dict[str, DeclarationSnapshot] = field(default_factory=dict)
    imports: FrozenSet[str] = frozenset()
    residual_hash: str = ""
    ambiguous: bool = False
    ambiguous_reason: str = ""


@dataclass(frozen=True)
class SemanticAuthorityViolation:
    reason_code: str
    relpath: str
    member_key: Optional[str]
    detail: str


def _normalize_type(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def stable_member_key(
    relpath: str, enclosing_type: str, kind: str, name: str, parameter_types: Sequence[str],
) -> str:
    """Durable, invocation-independent identity - deliberately NOT source
    line numbers (a reformat shifts lines without changing identity) and
    NOT A1's own invocation-local M#/R# ids (meaningless across separate
    review/generation calls - see the A3-D1/D2 investigations' own explicit
    finding that those must never be treated as durable). Overloads are
    disambiguated by the (lexically) normalized parameter type list, the
    same fidelity limitation `_normalized_public_signatures()`
    (file_resolution.py) already lives with for the same reason: this
    codebase has no full type-resolution pass, only a regex scanner."""
    kind_token = "CONSTRUCTOR" if kind == "constructor" else "METHOD"
    normalized_params = ", ".join(_normalize_type(p) for p in parameter_types)
    return f"{relpath}::{enclosing_type}::{kind_token}::{name}::({normalized_params})"


def stable_field_key(relpath: str, enclosing_type: str, field_name: str) -> str:
    """Field identity: owning type + field name (Java forbids two fields of
    the same name in the same type, so name alone disambiguates within one
    type - the ``FIELD`` kind token plus enclosing_type is what keeps
    ``Customer.region`` distinct from ``OtherCustomer.region``, and a
    field's own key namespace distinct from a method/constructor sharing
    the same name, matching stable_member_key()'s own collision-avoidance
    reasoning)."""
    return f"{relpath}::{enclosing_type}::FIELD::{field_name}"


def stable_record_component_key(relpath: str, record_name: str, component_name: str) -> str:
    """Component identity: owning record + component name - deliberately
    its own kind token/namespace (RECORD_COMPONENT), never collapsed into
    stable_field_key()'s FIELD namespace even for the same textual name,
    since the two constructs have different authorization semantics
    (RegionType.RECORD_COMPONENT vs RegionType.FIELD_DECLARATION) and this
    module's own instruction is explicit: never model a record component as
    a field declaration."""
    return f"{relpath}::{record_name}::RECORD_COMPONENT::{component_name}"


def _strip_comments_only(code: str) -> str:
    """A local, purpose-built variant of edit_safety.py's
    `_strip_java_comments_and_strings()` - same character-scanning
    algorithm, but string/char literal CONTENT is preserved verbatim
    instead of being blanked. Kept local rather than widening that shared
    helper (matches this codebase's own established convention - see
    java_members.py's `_split_top_level_commas()` docstring for the exact
    same rationale): that helper's callers need strings blanked (safe
    structural scanning); CORR-018's body-hash normalization needs the
    opposite - comments are non-material (Part 6, ignored), but a changed
    string literal is exactly the kind of material change this guard must
    never treat as a no-op."""
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in code[i:j]))
            i = j
        elif c in ("\"", "'"):
            quote = c
            j = i + 1
            while j < n and code[j] != quote:
                j += 2 if code[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(code[i:j])  # preserved verbatim - the one deliberate difference
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _normalize_body_text(raw: str) -> str:
    """Ignore only non-material churn (Part 6): comments stripped,
    whitespace/line-endings collapsed. String literals, operators, control
    flow, calls, exceptions, modifiers, annotations, initializers all
    survive untouched inside the comment-stripped text."""
    stripped = _strip_comments_only(raw)
    return re.sub(r"\s+", " ", stripped).strip()


def _body_hash(raw: str) -> str:
    return hashlib.sha256(_normalize_body_text(raw).encode("utf-8")).hexdigest()


_IMPORT_LINE_RE = re.compile(r"^\s*import\s+(static\s+)?([\w.]+(?:\.\*)?)\s*;\s*$", re.MULTILINE)


def _extract_imports(content: str) -> FrozenSet[str]:
    """Raw import target strings (e.g. "java.util.Objects",
    "static java.util.Objects.requireNonNull", "com.foo.*") - a plain set,
    so reordering is ignored for free (Part 8/9)."""
    out = set()
    for is_static, target in _IMPORT_LINE_RE.findall(content or ""):
        prefix = "static " if is_static else ""
        out.add(f"{prefix}{target}")
    return frozenset(out)


def _import_simple_name(import_target: str) -> Optional[str]:
    """None for a wildcard (its scope can never be proven narrowly, so it's
    never auto-authorized - Part 9). Otherwise the last dotted segment (the
    class name for an ordinary import, the member name for a static one)."""
    text = import_target[len("static "):] if import_target.startswith("static ") else import_target
    if text.endswith(".*"):
        return None
    return text.rsplit(".", 1)[-1] if "." in text else text


_IDENTIFIER_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _references_token(text: str, token: str) -> bool:
    pattern = _IDENTIFIER_RE_CACHE.get(token)
    if pattern is None:
        pattern = re.compile(r"(?<!\w)" + re.escape(token) + r"(?!\w)")
        _IDENTIFIER_RE_CACHE[token] = pattern
    return bool(pattern.search(text))


def _residual_text(
    content: str, members: Sequence[JavaMember],
    extra_protected_spans: Sequence[Tuple[int, int]] = (),
) -> str:
    """Every line NOT part of an import statement, NOT part of any
    extracted member's own [start_line, end_line] span, and NOT part of any
    `extra_protected_spans` entry (recognized field declarations / a
    record's own component-list header, both now explicitly represented -
    see `_extract_field_declarations`/`_extract_record_components`) -
    class-level annotations/modifiers, nested types, initializers, and
    anything else this module's scanners don't individually understand
    still fall here. Blanked (not deleted) so protected text remains
    excluded without shifting what "material" means for the rest of the
    file."""
    lines = content.splitlines()
    protected = [False] * (len(lines) + 1)  # 1-indexed
    for m in members:
        for ln in range(m.start_line, m.end_line + 1):
            if 0 < ln <= len(lines):
                protected[ln] = True
    for start, end in extra_protected_spans:
        for ln in range(start, end + 1):
            if 0 < ln <= len(lines):
                protected[ln] = True
    kept = []
    for idx, line in enumerate(lines, 1):
        if protected[idx] or _IMPORT_LINE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


_FIELD_DECL_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|transient|volatile)\s+)*"
    r"(?P<type>[A-Za-z_$][\w$]*(?:\s*<[^<>{}]*>)?(?:\s*\[\s*\])*)\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:=[^;]*)?;\s*$"
)
_LEADING_FIELD_ANNOTATION_RE = re.compile(r"^\s*@[A-Za-z_$][\w$.]*(?:\s*\([^()]*\))?\s*")


def _extract_field_declarations(
    relpath: str, content: str, enclosing_type: str, members: Sequence[JavaMember],
) -> Dict[str, DeclarationSnapshot]:
    """Ordinary class/interface field declarations of the PRIMARY top-level
    type only (mirrors extract_java_members()'s own "one target file, one
    primary type, depth 1 relative to it" scope). Depth is computed by a
    plain running brace count over the comment/string-stripped text (safe:
    a string literal's own '{'/'}' characters are blanked first, exactly
    the same reuse `_matching_close_brace_line()` in java_members.py relies
    on) - a line is a field candidate only while depth-before-that-line is
    exactly 1 (directly inside the primary type's own body, never inside a
    method/initializer block, which would already be depth >= 2) and it is
    not already claimed by an extracted member's own line span.

    Explicit V1 scope, honest rather than silently wrong: SINGLE PHYSICAL
    LINE declarations only - no multi-line generic types, no multi-line
    lambda initializers, no comma-separated multi-declarators (`int a,
    b;`). None of these become false NEGATIVES that leak protection: a
    field shape this function does not recognize simply never enters
    `fields` and stays covered by the existing residual-region catch-all
    (REASON_RESIDUAL_REGION_CHANGED) exactly as before this change - this
    function can only ever narrow what "residual" means, never widen what
    is silently permitted."""
    try:
        stripped = _strip_java_comments_and_strings(content)
    except Exception:
        return {}
    stripped_lines = stripped.splitlines()
    real_lines = content.splitlines()
    depths_before: List[int] = []
    depth = 0
    for line in stripped_lines:
        depths_before.append(depth)
        depth += line.count("{") - line.count("}")

    member_protected = [False] * (len(real_lines) + 1)
    for m in members:
        for ln in range(m.start_line, m.end_line + 1):
            if 0 < ln <= len(real_lines):
                member_protected[ln] = True

    out: Dict[str, DeclarationSnapshot] = {}
    for idx, stripped_line in enumerate(stripped_lines):
        line_no = idx + 1
        if depths_before[idx] != 1 or member_protected[line_no]:
            continue
        candidate = stripped_line
        prev = None
        while prev != candidate:
            prev = candidate
            candidate = _LEADING_FIELD_ANNOTATION_RE.sub("", candidate)
        match = _FIELD_DECL_RE.match(candidate)
        if not match:
            continue
        name = match.group("name")
        key = stable_field_key(relpath, enclosing_type, name)
        if key in out:
            # A duplicate field name in the same type is invalid Java and
            # should not occur for real source - treated defensively as
            # "not safely identifiable" (removed, not arbitrarily picked)
            # rather than risking a wrong identity; still protected by the
            # residual catch-all either way.
            del out[key]
            continue
        raw_text = real_lines[line_no - 1] if 0 < line_no <= len(real_lines) else stripped_line
        declaration_hash = hashlib.sha256(_normalize_body_text(raw_text).encode("utf-8")).hexdigest()
        out[key] = DeclarationSnapshot(
            stable_key=key, kind="field", name=name,
            declaration_hash=declaration_hash, start_line=line_no, end_line=line_no,
        )
    return out


_RECORD_HEADER_RE = re.compile(r"\brecord\s+([A-Za-z_$][\w$]*)\s*(?:<[^<>{}]*>)?\s*\(")
# Fallback enclosing-type name for a file whose primary type has zero
# extracted methods/constructors (e.g. a plain data class/record with only
# fields/components) - mirrors java_members.py's own private
# _TOP_LEVEL_TYPE_RE exactly (kept local rather than importing a
# leading-underscore symbol from that module for a second purpose).
_TOP_LEVEL_TYPE_FALLBACK_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")


def _extract_record_components(
    relpath: str, content: str,
) -> Tuple[Dict[str, DeclarationSnapshot], Optional[Tuple[int, int]]]:
    """The PRIMARY top-level record's own component list only (same "one
    target file, one primary type" bound extract_java_members() already
    uses) - a SECOND record declared in the same file is not scanned, an
    honest V1 restriction. Returns (components, header_span) - header_span
    (1-indexed, inclusive) is the record header's own line range, needed by
    build_file_snapshot() to exclude it from the residual region; None if
    no record declaration was found (or it was malformed/unmatched, which
    fails closed - the header stays in the residual region, unauthorizable,
    rather than silently guessing a component list)."""
    try:
        stripped = _strip_java_comments_and_strings(content)
    except Exception:
        return {}, None
    match = _RECORD_HEADER_RE.search(stripped)
    if not match:
        return {}, None
    record_name = match.group(1)
    open_paren = match.end() - 1
    depth = 0
    close_paren = None
    for i in range(open_paren, len(stripped)):
        ch = stripped[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                close_paren = i
                break
    if close_paren is None:
        return {}, None

    component_text = stripped[open_paren + 1: close_paren]
    start_line = stripped.count("\n", 0, open_paren) + 1
    end_line = stripped.count("\n", 0, close_paren) + 1

    out: Dict[str, DeclarationSnapshot] = {}
    for piece in _split_top_level_commas(component_text):
        piece = piece.strip()
        if not piece:
            continue
        name_match = re.search(r"([A-Za-z_$][\w$]*)\s*$", piece)
        if not name_match:
            continue
        name = name_match.group(1)
        comp_type = _param_type_only(piece)
        key = stable_record_component_key(relpath, record_name, name)
        if key in out:
            del out[key]
            continue
        declaration_hash = hashlib.sha256(
            _normalize_body_text(f"{comp_type} {name}").encode("utf-8")
        ).hexdigest()
        out[key] = DeclarationSnapshot(
            stable_key=key, kind="record_component", name=name,
            declaration_hash=declaration_hash, start_line=start_line, end_line=end_line,
        )
    return out, (start_line, end_line)


def build_file_snapshot(relpath: str, content: str) -> FileSnapshot:
    """Pure, deterministic. Never raises - a scan failure (exception, or a
    duplicate stable key from two members that lexically collide) produces
    an `ambiguous=True` snapshot instead, so the comparator can fail closed
    (Part 14/15) rather than the caller having to guard every call site."""
    try:
        members = extract_java_members(content)
    except Exception as e:
        return FileSnapshot(relpath=relpath, ambiguous=True, ambiguous_reason=f"scan failed: {e}")

    lines = content.splitlines()
    member_snapshots: Dict[str, MemberSnapshot] = {}
    for m in members:
        key = stable_member_key(relpath, m.enclosing_type, m.kind, m.name, m.parameter_types)
        if key in member_snapshots:
            return FileSnapshot(
                relpath=relpath, ambiguous=True,
                ambiguous_reason=f"duplicate stable member key: {key!r}",
            )
        raw_body = "\n".join(lines[m.start_line - 1: m.end_line]) if m.start_line >= 1 else ""
        throws_suffix = f" throws {','.join(sorted(_normalize_type(t) for t in m.throws))}" if m.throws else ""
        member_snapshots[key] = MemberSnapshot(
            stable_key=key, kind=m.kind, name=m.name,
            normalized_signature=f"{m.signature}{throws_suffix}",
            body_hash=_body_hash(raw_body),
            start_line=m.start_line, end_line=m.end_line,
        )

    if members:
        enclosing_type = members[0].enclosing_type
    else:
        type_match = _TOP_LEVEL_TYPE_FALLBACK_RE.search(content)
        enclosing_type = type_match.group(1) if type_match else ""
    field_snapshots = _extract_field_declarations(relpath, content, enclosing_type, members)
    record_component_snapshots, record_header_span = _extract_record_components(relpath, content)
    if field_snapshots.keys() & record_component_snapshots.keys():
        return FileSnapshot(
            relpath=relpath, ambiguous=True,
            ambiguous_reason="a field and a record component resolved to the same stable key",
        )

    extra_protected_spans = [(d.start_line, d.end_line) for d in field_snapshots.values()]
    if record_header_span:
        extra_protected_spans.append(record_header_span)
    residual_hash = hashlib.sha256(
        _normalize_body_text(_residual_text(content, members, extra_protected_spans)).encode("utf-8")
    ).hexdigest()
    return FileSnapshot(
        relpath=relpath, members=member_snapshots,
        fields=field_snapshots, record_components=record_component_snapshots,
        imports=_extract_imports(content), residual_hash=residual_hash,
    )


def _authorized_body_or_signature(
    key: str, regions: Sequence[AuthorizedSemanticRegion],
) -> Tuple[bool, bool]:
    """Returns (body_authorized, signature_authorized) for this exact key -
    a SIGNATURE authorization implicitly covers a body change to the SAME
    member too (Scenario 8: an authorized signature change routinely needs
    a matching body edit), but a BODY-only authorization never covers a
    signature change (Scenario 9 - strictly the smaller grant)."""
    body = any(r.member_key == key and r.region_type in _BODY_REGIONS for r in regions)
    sig = any(r.member_key == key and r.region_type in _SIGNATURE_REGIONS for r in regions)
    return body, sig


def _compare_declarations(
    relpath: str,
    base_decls: Dict[str, DeclarationSnapshot],
    cand_decls: Dict[str, DeclarationSnapshot],
    regions: Sequence[AuthorizedSemanticRegion],
    region_type: RegionType,
) -> List[SemanticAuthorityViolation]:
    """Field/record-component comparison - deliberately simpler than the
    member comparator below: one declaration, one hash, no body/signature
    split, and NO successor-matching for a rename (Task 3's own explicit
    instruction: a rename is indistinguishable from, and must be
    authorized as, an independent delete + add - never a special-cased
    "same identity, new name" grant, unlike a *_SIGNATURE region's own
    successor_key mechanism for methods/constructors)."""
    violations: List[SemanticAuthorityViolation] = []
    # One grant, per exact key, covers all three operations for that key:
    # modify, remove, or (for a key absent from the baseline) add - Task 2/3
    # both explicitly require field/record-component REMOVAL to be an
    # authorizable operation (unlike the method/constructor comparator
    # above, where deletion is never authorized via a BODY/SIGNATURE grant
    # at all, only via an explicit *_SIGNATURE successor_key rename - a
    # deliberately different, simpler rule for this simpler construct kind,
    # per this closure task's own explicit instruction that a
    # field/record-component rename is an independent delete + add, never
    # a special-cased "same identity" match).
    authorized_keys = {r.member_key for r in regions if r.region_type == region_type and r.member_key}
    for base_key, base_decl in base_decls.items():
        cand_decl = cand_decls.get(base_key)
        if cand_decl is None:
            if base_key in authorized_keys:
                continue  # authorized removal
            violations.append(SemanticAuthorityViolation(
                REASON_DECLARATION_DELETED, relpath, base_key,
                f"{base_decl.kind} present in baseline is missing from candidate",
            ))
            continue
        if base_decl.declaration_hash != cand_decl.declaration_hash and base_key not in authorized_keys:
            violations.append(SemanticAuthorityViolation(
                REASON_DECLARATION_UNAUTHORIZED, relpath, base_key,
                f"{base_decl.kind} declaration changed without an authorized {region_type.value} region",
            ))
    for cand_key, cand_decl in cand_decls.items():
        if cand_key in base_decls or cand_key in authorized_keys:
            continue
        violations.append(SemanticAuthorityViolation(
            REASON_DECLARATION_ADDED_UNAUTHORIZED, relpath, cand_key,
            f"new {cand_decl.kind} has no authorized {region_type.value} region",
        ))
    return violations


def _compare_one_file(
    relpath: str, baseline: str, candidate: str, regions: Sequence[AuthorizedSemanticRegion],
) -> List[SemanticAuthorityViolation]:
    base_snap = build_file_snapshot(relpath, baseline)
    cand_snap = build_file_snapshot(relpath, candidate)
    if base_snap.ambiguous or cand_snap.ambiguous:
        reason = cand_snap.ambiguous_reason or base_snap.ambiguous_reason
        return [SemanticAuthorityViolation(REASON_SCAN_AMBIGUOUS, relpath, None, reason)]

    violations: List[SemanticAuthorityViolation] = []
    consumed_successor_keys = set()

    # Signature-authorized successor matching must run FIRST, so a
    # legitimate rename/re-type's new key is marked consumed before the
    # "unmatched candidate member" pass below ever sees it (Part 7).
    sig_regions_by_base_key = {
        r.member_key: r for r in regions if r.region_type in _SIGNATURE_REGIONS and r.successor_key
    }
    for base_key, region in sig_regions_by_base_key.items():
        if base_key in base_snap.members and base_key not in cand_snap.members:
            if region.successor_key in cand_snap.members:
                consumed_successor_keys.add(region.successor_key)
            else:
                violations.append(SemanticAuthorityViolation(
                    REASON_MEMBER_DELETED, relpath, base_key,
                    f"authorized signature change expected successor {region.successor_key!r}, "
                    "which never appeared in the candidate",
                ))

    for base_key, base_member in base_snap.members.items():
        if base_key in sig_regions_by_base_key:
            continue  # already handled above (matched-successor or reported)
        cand_member = cand_snap.members.get(base_key)
        if cand_member is None:
            violations.append(SemanticAuthorityViolation(
                REASON_MEMBER_DELETED, relpath, base_key, "member present in baseline is missing from candidate",
            ))
            continue
        sig_changed = base_member.normalized_signature != cand_member.normalized_signature
        body_changed = base_member.body_hash != cand_member.body_hash
        if not sig_changed and not body_changed:
            continue
        body_ok, sig_ok = _authorized_body_or_signature(base_key, regions)
        if sig_changed:
            if not sig_ok:
                violations.append(SemanticAuthorityViolation(
                    REASON_SIGNATURE_UNAUTHORIZED, relpath, base_key,
                    "signature changed without an authorized *_SIGNATURE region",
                ))
        elif body_changed and not body_ok:
            violations.append(SemanticAuthorityViolation(
                REASON_REGION_UNAUTHORIZED, relpath, base_key,
                "body changed without an authorized *_BODY region",
            ))

    for cand_key in cand_snap.members:
        if cand_key in base_snap.members or cand_key in consumed_successor_keys:
            continue
        if any(r.member_key == cand_key and r.region_type in _ADD_REGIONS for r in regions):
            continue
        violations.append(SemanticAuthorityViolation(
            REASON_MEMBER_ADDED_UNAUTHORIZED, relpath, cand_key,
            "new member has no authorized *_ADD region",
        ))

    violations.extend(_compare_declarations(
        relpath, base_snap.fields, cand_snap.fields, regions, RegionType.FIELD_DECLARATION,
    ))
    violations.extend(_compare_declarations(
        relpath, base_snap.record_components, cand_snap.record_components, regions, RegionType.RECORD_COMPONENT,
    ))

    violations.extend(_check_imports(relpath, baseline, candidate, base_snap, cand_snap, regions))

    if base_snap.residual_hash != cand_snap.residual_hash:
        violations.append(SemanticAuthorityViolation(
            REASON_RESIDUAL_REGION_CHANGED, relpath, None,
            "content outside every recognized member/field/record-component/import "
            "region changed (class-level annotations/modifiers, nested types, "
            "multi-line/comma-separated field declarations, or other unsupported "
            "constructs) - not authorizable in this slice",
        ))

    return violations


def _member_raw_text(content: str, start_line: int, end_line: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[start_line - 1: end_line]) if start_line >= 1 else ""


def _lookup_span(snap: FileSnapshot, key: str) -> Optional[Tuple[int, int]]:
    """A key may resolve to a member, a field, or a record component -
    exactly one dict will ever hold it (the three key namespaces never
    collide by construction, see stable_member_key/stable_field_key/
    stable_record_component_key's own distinct kind tokens)."""
    m = snap.members.get(key)
    if m:
        return m.start_line, m.end_line
    d = snap.fields.get(key) or snap.record_components.get(key)
    if d:
        return d.start_line, d.end_line
    return None


def _authorized_region_text(content: str, snap: FileSnapshot, keys: "set[str]") -> str:
    parts = []
    for key in keys:
        span = _lookup_span(snap, key)
        if span:
            parts.append(_member_raw_text(content, span[0], span[1]))
    return "\n".join(parts)


def _non_authorized_baseline_text(baseline: str, base_snap: FileSnapshot, authorized_keys: "set[str]") -> str:
    """Baseline content with only the AUTHORIZED members'/fields'/record
    components' own line ranges blanked out - everything else (unauthorized
    members, residual region, import lines) remains, so an import's simple
    name found here proves it has a use the authorized change does NOT
    make obsolete."""
    lines = baseline.splitlines()
    protected = [False] * (len(lines) + 1)
    all_items = list(base_snap.members.items()) + list(base_snap.fields.items()) + list(base_snap.record_components.items())
    for key, entry in all_items:
        if key in authorized_keys:
            for ln in range(entry.start_line, entry.end_line + 1):
                if 0 < ln <= len(lines):
                    protected[ln] = True
    kept = [line for idx, line in enumerate(lines, 1) if not protected[idx] and not _IMPORT_LINE_RE.match(line)]
    return "\n".join(kept)


def _check_imports(
    relpath: str, baseline: str, candidate: str,
    base_snap: FileSnapshot, cand_snap: FileSnapshot,
    regions: Sequence[AuthorizedSemanticRegion],
) -> List[SemanticAuthorityViolation]:
    imports_authorized = any(r.region_type == RegionType.IMPORTS for r in regions)
    added = cand_snap.imports - base_snap.imports
    removed = base_snap.imports - cand_snap.imports
    violations: List[SemanticAuthorityViolation] = []
    if not added and not removed:
        return violations

    cand_authorized_keys = {
        r.member_key for r in regions
        if r.region_type in (_BODY_REGIONS | _SIGNATURE_REGIONS | _ADD_REGIONS | _DECLARATION_REGIONS)
        and r.member_key
    }
    base_authorized_keys = {
        r.member_key for r in regions
        if r.region_type in (_BODY_REGIONS | _SIGNATURE_REGIONS | _DECLARATION_REGIONS) and r.member_key
    }
    authorized_candidate_text = _authorized_region_text(candidate, cand_snap, cand_authorized_keys)
    authorized_baseline_text = _authorized_region_text(baseline, base_snap, base_authorized_keys)
    rest_of_baseline_text = _non_authorized_baseline_text(baseline, base_snap, base_authorized_keys)
    candidate_content_without_imports = "\n".join(
        line for line in candidate.splitlines() if not _IMPORT_LINE_RE.match(line)
    )

    for target in sorted(added):
        simple_name = _import_simple_name(target)
        if simple_name is None:
            violations.append(SemanticAuthorityViolation(
                REASON_IMPORT_UNAUTHORIZED, relpath, None,
                f"added wildcard import {target!r} is never auto-authorized (v1)",
            ))
            continue
        if not imports_authorized or not _references_token(authorized_candidate_text, simple_name):
            violations.append(SemanticAuthorityViolation(
                REASON_IMPORT_UNAUTHORIZED, relpath, None,
                f"added import {target!r} is not referenced inside any authorized region",
            ))

    for target in sorted(removed):
        simple_name = _import_simple_name(target)
        if simple_name is None or not imports_authorized:
            violations.append(SemanticAuthorityViolation(
                REASON_IMPORT_UNAUTHORIZED, relpath, None,
                f"removed import {target!r} is not deterministically provable as safe to remove",
            ))
            continue
        exclusively_authorized_baseline_use = (
            _references_token(authorized_baseline_text, simple_name)
            and not _references_token(rest_of_baseline_text, simple_name)
        )
        still_referenced = _references_token(candidate_content_without_imports, simple_name)
        if not exclusively_authorized_baseline_use or still_referenced:
            violations.append(SemanticAuthorityViolation(
                REASON_IMPORT_UNAUTHORIZED, relpath, None,
                f"removed import {target!r} was not exclusively used by an authorized region, "
                "or remains referenced",
            ))
    return violations


def find_unauthorized_semantic_changes(
    original_contents: Dict[str, str],
    final_contents: Dict[str, str],
    authorized_regions: Sequence[AuthorizedSemanticRegion],
    strict_existing_java_files: bool = False,
) -> List[SemanticAuthorityViolation]:
    """The orchestration entry point - the CORR-018-P1 peer to
    `find_brownfield_public_api_changes()` (kriya/workflow/file_resolution.py),
    same `(original_contents, final_contents)` inputs (already in memory at
    both existing call sites, zero new I/O), same "list of violations, empty
    means clean" shape, but a SEPARATE function/module/reason-code space -
    never merged into that one (Part 15/19 of the CORR-018 A3-D2 design).

    Per-region checking covers only files that appear as a `relpath` in
    `authorized_regions` - unchanged since A3-P1. `strict_existing_java_files`
    (new, 2026-09-13, default False - every existing caller keeps
    byte-identical behavior without passing it) is additive: when True, ANY
    EXISTING `.java` file present in `final_contents` with real content
    change but ZERO entries anywhere in `authorized_regions` is rejected
    outright (REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY) instead of silently
    skipped - this is what makes "every existing Java file requires
    semantic-region authority, not merely file-write authority" a real,
    fail-closed guarantee rather than a per-file opt-in. A brand-new file
    (absent from `original_contents`) is never flagged by this branch -
    CORR-018's own required closure target is existing-file mutation; new
    files remain governed by file-write/change-contract authority and other
    existing validation (Task 19)."""
    regions_by_relpath: Dict[str, List[AuthorizedSemanticRegion]] = {}
    for r in authorized_regions:
        regions_by_relpath.setdefault(r.relpath, []).append(r)

    violations: List[SemanticAuthorityViolation] = []
    for relpath in sorted(regions_by_relpath):
        regions = regions_by_relpath[relpath]
        candidate_content = final_contents.get(relpath)
        if candidate_content is None:
            continue  # this candidate never touched this file at all
        baseline_content = original_contents.get(relpath)
        if baseline_content is None:
            if any(r.region_type == RegionType.TEST_FILE_ADD for r in regions):
                continue
            violations.append(SemanticAuthorityViolation(
                REASON_MEMBER_ADDED_UNAUTHORIZED, relpath, None,
                f"'{relpath}' is a new file with no TEST_FILE_ADD (or other) authority granted",
            ))
            continue
        if baseline_content == candidate_content:
            continue
        violations.extend(_compare_one_file(relpath, baseline_content, candidate_content, regions))

    if strict_existing_java_files:
        already_checked = set(regions_by_relpath.keys())
        for relpath in sorted(final_contents.keys()):
            if relpath in already_checked or not relpath.endswith(".java"):
                continue
            candidate_content = final_contents.get(relpath)
            baseline_content = original_contents.get(relpath)
            if baseline_content is None or candidate_content is None:
                continue  # new file - not this function's concern (Task 19)
            if baseline_content == candidate_content:
                continue
            violations.append(SemanticAuthorityViolation(
                REASON_FILE_HAS_NO_SEMANTIC_AUTHORITY, relpath, None,
                f"'{relpath}' is an existing Java file with a real content change but "
                "zero authorized_regions entries at all - strict_existing_java_files "
                "requires every existing-Java-file mutation to carry requirement-grounded "
                "semantic-region authority, never merely file-write authority",
            ))
    return violations
