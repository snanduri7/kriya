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

A3-BOUND ONLY, BY DESIGN: `authorized_regions` must be supplied explicitly by
the caller (in practice, derived from an approved `ProposedModification`,
kriya/workflow/review_context.py - not yet wired, a future slice). There is
no goal-text-derived general authorization here - general CORR-018 (deriving
authority from an arbitrary hand-typed goal) remains NEEDS_IMPLEMENTATION and
is explicitly out of scope. An empty/absent `authorized_regions` sequence for
a given file means this module performs NO checks on that file at all -
today's unchanged behavior for every existing caller, opted into per-file by
the caller supplying a region there, never opted OUT of per-file by this
module itself.

V1 SCOPE (Java only): top-level class methods/constructors and imports.
Fields, nested/inner class members, record compact constructors, generated/
Lombok/annotation-processor source, and arbitrary class-level restructuring
are explicitly OUT of member-level scope - any material change there is
caught only as an undifferentiated "residual region changed" and rejected by
default (no WHOLE_FILE escape hatch in this slice - see RegionType's own
docstring). This is an honest, stated restriction, not a silent gap.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from kriya.analyzer.java_members import JavaMember, extract_java_members

REASON_REGION_UNAUTHORIZED = "SEMANTIC_REGION_UNAUTHORIZED"
REASON_MEMBER_DELETED = "SEMANTIC_MEMBER_DELETED"
REASON_MEMBER_ADDED_UNAUTHORIZED = "SEMANTIC_MEMBER_ADDED_UNAUTHORIZED"
REASON_SIGNATURE_UNAUTHORIZED = "SEMANTIC_SIGNATURE_UNAUTHORIZED"
REASON_RESIDUAL_REGION_CHANGED = "SEMANTIC_RESIDUAL_REGION_CHANGED"
REASON_IMPORT_UNAUTHORIZED = "SEMANTIC_IMPORT_UNAUTHORIZED"
REASON_SCAN_AMBIGUOUS = "SEMANTIC_SCAN_AMBIGUOUS"


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


_BODY_REGIONS = frozenset({RegionType.METHOD_BODY, RegionType.CONSTRUCTOR_BODY})
_SIGNATURE_REGIONS = frozenset({RegionType.METHOD_SIGNATURE, RegionType.CONSTRUCTOR_SIGNATURE})
_ADD_REGIONS = frozenset({
    RegionType.METHOD_ADD, RegionType.CONSTRUCTOR_ADD, RegionType.TEST_METHOD_ADD,
})


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
class FileSnapshot:
    relpath: str
    members: Dict[str, MemberSnapshot] = field(default_factory=dict)
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


def _residual_text(content: str, members: Sequence[JavaMember]) -> str:
    """Every line NOT part of an import statement and NOT part of any
    extracted member's own [start_line, end_line] span - fields, class-level
    annotations/modifiers, nested types, initializers, anything this
    module's member scanner doesn't individually understand. Blanked (not
    deleted) so import/member text remains excluded without shifting what
    "material" means for the rest of the file."""
    lines = content.splitlines()
    protected = [False] * (len(lines) + 1)  # 1-indexed
    for m in members:
        for ln in range(m.start_line, m.end_line + 1):
            if 0 < ln <= len(lines):
                protected[ln] = True
    kept = []
    for idx, line in enumerate(lines, 1):
        if protected[idx] or _IMPORT_LINE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


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

    residual_hash = hashlib.sha256(
        _normalize_body_text(_residual_text(content, members)).encode("utf-8")
    ).hexdigest()
    return FileSnapshot(
        relpath=relpath, members=member_snapshots,
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

    violations.extend(_check_imports(relpath, baseline, candidate, base_snap, cand_snap, regions))

    if base_snap.residual_hash != cand_snap.residual_hash:
        violations.append(SemanticAuthorityViolation(
            REASON_RESIDUAL_REGION_CHANGED, relpath, None,
            "content outside every recognized member/import region changed "
            "(fields, class-level annotations/modifiers, nested types, or other "
            "unsupported constructs) - not authorizable in this slice",
        ))

    return violations


def _member_raw_text(content: str, start_line: int, end_line: int) -> str:
    lines = content.splitlines()
    return "\n".join(lines[start_line - 1: end_line]) if start_line >= 1 else ""


def _authorized_region_text(content: str, snap: FileSnapshot, keys: "set[str]") -> str:
    parts = []
    for key in keys:
        m = snap.members.get(key)
        if m:
            parts.append(_member_raw_text(content, m.start_line, m.end_line))
    return "\n".join(parts)


def _non_authorized_baseline_text(baseline: str, base_snap: FileSnapshot, authorized_keys: "set[str]") -> str:
    """Baseline content with only the AUTHORIZED members' own line ranges
    blanked out - everything else (unauthorized members, residual region,
    import lines) remains, so an import's simple name found here proves it
    has a use the authorized change does NOT make obsolete."""
    lines = baseline.splitlines()
    protected = [False] * (len(lines) + 1)
    for key, m in base_snap.members.items():
        if key in authorized_keys:
            for ln in range(m.start_line, m.end_line + 1):
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
        if r.region_type in (_BODY_REGIONS | _SIGNATURE_REGIONS | _ADD_REGIONS) and r.member_key
    }
    base_authorized_keys = {
        r.member_key for r in regions if r.region_type in (_BODY_REGIONS | _SIGNATURE_REGIONS) and r.member_key
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
) -> List[SemanticAuthorityViolation]:
    """The orchestration entry point - the CORR-018-P1 peer to
    `find_brownfield_public_api_changes()` (kriya/workflow/file_resolution.py),
    same `(original_contents, final_contents)` inputs (already in memory at
    both existing call sites, zero new I/O), same "list of violations, empty
    means clean" shape, but a SEPARATE function/module/reason-code space -
    never merged into that one (Part 15/19 of the CORR-018 A3-D2 design).

    Only checks files that appear as a `relpath` in `authorized_regions` -
    this is the A3-bound slice's own scope boundary: it protects exactly the
    file(s) an approved proposal named, never every file a candidate
    happens to touch (that would be general CORR-018, explicitly deferred).
    An empty `authorized_regions` is a full no-op, by construction."""
    if not authorized_regions:
        return []
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
    return violations
