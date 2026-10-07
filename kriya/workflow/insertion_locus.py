"""P3-D: structural new-member insertion authority.

A goal can ask for a brand-new member of an existing type ("Add `public static int clamp(...)` to NumberUtils"). A new
member has no existing text to anchor on, and a large owner file never fits the Developer's context, so before P3-D the
Edit capability (CONTEXT-EDIT-PROTOCOL-001) had no feasible operation and the run stopped before inference (live A3:
NumberUtils.java, 1,696 lines; localization grounded the owner class only).

A StructuralInsertionLocus is zero-width authority - "the candidate may ADD bytes here" - decided deterministically from
the repository's structure at the exact current revision, never by the model:

- Need: the user's goal names, in quoted code, a member (``name(``) the owner does not declare (``planned_new_members``).
- Owner: one type of the target file, established by the Code Intelligence structural model (tree-sitter; never a
  second parser): the localized owner when exactly one is grounded, else a type the goal names, else the file's public
  type named after the file; anything else is ambiguous and refused. V1 owners: class, interface, record (an enum's
  constants and an annotation type's elements are a different grammar - typed unsupported).
- Boundary (tier): ``after_grounded_member`` (just after a grounded member of the owner, up to its next sibling), else
  ``owner_closing_delimiter`` (between the owner's last member and the closing brace the parser reports - the body
  span's last byte, verified to be ``}``; never a brace search).
- Carrier: the smallest run of whole lines ending at the boundary that occurs exactly once in the file (at least the
  boundary line and the one before it), shown byte-exact as an ``insertion_locus`` span. It is how the model addresses
  the boundary with the ordinary anchored edit; it grants no rewrite of those lines.

The existing anchored-edit protocol carries the insertion (SEARCH the carrier, REPLACE with the carrier plus the new
member); no new operation or schema exists. What the candidate actually changed is then checked on the bytes
(``verify_insertion``): the file is still the locus's revision (else STALE_INSERTION_LOCUS), the change is a pure
insertion whose possible offsets meet the boundary gap (else INSERTION_OUTSIDE_LOCUS - neighbouring code, imports or any
other place are not authorized by it), the result still parses with the owner and every original symbol intact and every
new symbol inside the owner (else INSERTION_STRUCTURE_CHANGED), and the carrier was in the request actually sent
(P3-A; else INSERTION_LOCUS_NOT_SENT). A locus is never persisted or re-resolved: each Developer invocation decides its
own from the bytes it reads.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

STRUCTURAL_INSERTION_VERSION = 1
INSERTION_UNIT = "insertion_locus"
TIER_AFTER_GROUNDED_MEMBER = "after_grounded_member"
TIER_OWNER_CLOSING_DELIMITER = "owner_closing_delimiter"

STALE_INSERTION_LOCUS = "STALE_INSERTION_LOCUS"
INSERTION_OUTSIDE_LOCUS = "INSERTION_OUTSIDE_LOCUS"
INSERTION_STRUCTURE_CHANGED = "INSERTION_STRUCTURE_CHANGED"
INSERTION_LOCUS_NOT_SENT = "INSERTION_LOCUS_NOT_SENT"

SUPPORTED_OWNER_KINDS = frozenset({"class", "interface", "record"})
MIN_CARRIER_LINES = 2
MAX_CARRIER_LINES = 12

_CODE_SPAN = re.compile(r"`([^`\n]+)`|```[^\n]*\n(.*?)```", re.DOTALL)
_CALLED_NAME = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_JAVA_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "return", "new", "throw", "synchronized", "super", "this", "assert",
    "try", "do", "else", "case", "default", "instanceof",
})


@dataclass(frozen=True)
class InsertionLocus:
    """Zero-width insertion authority in one revision of one file."""

    path: str
    revision: str
    language: str
    owner_symbol_id: str
    owner_lookup_key: str
    tier: str
    gap_start: int           # character offsets into the revision's text: insertion is allowed anywhere in [start, end]
    gap_end: int
    start_line: int          # the carrier's whole lines (1-based, inclusive)
    end_line: int
    carrier: str
    planned_members: Tuple[str, ...]
    provenance: str

    @property
    def digest(self) -> str:
        payload = "|".join(map(str, (STRUCTURAL_INSERTION_VERSION, self.path, self.revision, self.owner_symbol_id,
                                     self.tier, self.gap_start, self.gap_end, self.start_line, self.end_line,
                                     hashlib.sha256(self.carrier.encode("utf-8")).hexdigest(), self.provenance)))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def summary(self) -> dict:
        return {"version": STRUCTURAL_INSERTION_VERSION, "owner": self.owner_lookup_key, "tier": self.tier,
                "start_line": self.start_line, "end_line": self.end_line, "planned_members": list(self.planned_members),
                "revision": self.revision, "digest": self.digest}


def _code_fragments(goal: str) -> List[str]:
    return [single or block for single, block in _CODE_SPAN.findall(goal or "")]


def planned_new_members(goal: str, existing: Iterable[str]) -> List[str]:
    """Members the user's goal names in quoted code (``name(``) that the
    owner does not declare - the only evidence that a new member is
    planned. Never the plan or a model's words."""
    known = set(existing)
    found: List[str] = []
    for fragment in _code_fragments(goal):
        for name in _CALLED_NAME.findall(fragment):
            if name not in known and name not in _JAVA_KEYWORDS and name not in found:
                found.append(name)
    return found


def _char_offset(data: bytes, byte_offset: int) -> int:
    return len(data[:byte_offset].decode("utf-8", errors="strict"))


def resolve_insertion_locus(
    path: str, content: str, *, goal: str, revision: str, grounded_keys: Sequence[str] = (),
) -> Tuple[Optional[InsertionLocus], str]:
    """The insertion locus for a planned new member of ``path`` at this exact
    ``revision`` of ``content``, or (None, why not) - fail closed.
    ``grounded_keys`` are the namespace-relative keys (``Type`` or
    ``Type.member``, the member-boundary convention) localization grounded."""
    from kriya.code_intel.model import ParseState
    from kriya.code_intel.parsing import parse_text
    from kriya.workflow.language_adapters import Capability, CapabilityStatus, capability_status

    if capability_status(path, Capability.STRUCTURAL_INSERTION) is CapabilityStatus.UNSUPPORTED:
        return None, "structural insertion is not supported for this language"
    structure = parse_text(path, content)
    if structure.state is not ParseState.PARSED:
        return None, f"the file does not parse cleanly ({structure.state.value})"
    prefix = f"{structure.namespace}." if structure.namespace else ""
    grounded_set = set(grounded_keys)

    def is_grounded(symbol) -> bool:
        key = symbol.lookup_key[len(prefix):] if symbol.lookup_key.startswith(prefix) else symbol.lookup_key
        return key in grounded_set

    types = [symbol for symbol in structure.symbols if symbol.is_type]
    grounded = [symbol for symbol in types if is_grounded(symbol)]
    named = [symbol for symbol in types if re.search(rf"(?<![\w$]){re.escape(symbol.name)}(?![\w$])", goal or "")]
    stem = os.path.splitext(os.path.basename(path))[0]
    by_file = [symbol for symbol in types if symbol.parent_id is None and symbol.name == stem]
    owner = None
    for candidates in (grounded, named, by_file):
        if len(candidates) == 1:
            owner = candidates[0]
            break
        if len(candidates) > 1:
            return None, "the owner type is ambiguous: " + ", ".join(sorted(s.lookup_key for s in candidates))
    if owner is None:
        return None, "no owner type can be established"
    if owner.kind not in SUPPORTED_OWNER_KINDS or owner.body is None:
        return None, f"owner kind {owner.kind} is not supported for structural insertion (V1: class, interface, record)"
    members = [symbol for symbol in structure.symbols if symbol.parent_id == owner.symbol_id]
    planned = planned_new_members(goal, [symbol.name for symbol in members])
    if not planned:
        return None, "the goal names no new member of the owner"
    data = content.encode("utf-8")
    closing = owner.body.end_byte - 1
    if data[closing:closing + 1] != b"}":
        return None, "the owner's closing delimiter is not where the parser reports it"
    grounded_members = sorted((symbol for symbol in members if is_grounded(symbol)),
                              key=lambda symbol: symbol.declaration.end_byte)
    if grounded_members:
        anchor = grounded_members[-1]
        later = [symbol.declaration.start_byte for symbol in members
                 if symbol.declaration.start_byte >= anchor.declaration.end_byte]
        start_byte, end_byte, tier = anchor.declaration.end_byte, min(later + [closing]), TIER_AFTER_GROUNDED_MEMBER
    else:
        before = [symbol.declaration.end_byte for symbol in members if symbol.declaration.end_byte <= closing]
        start_byte, end_byte, tier = max(before + [owner.body.start_byte + 1]), closing, TIER_OWNER_CLOSING_DELIMITER
    # The gap is the whitespace right after the anchor member (or the
    # owner's opening brace): never inside a comment, a neighbour or past the
    # closing delimiter.
    gap_start = _char_offset(data, start_byte)
    gap_end = gap_start
    limit = _char_offset(data, end_byte)
    while gap_end < limit and content[gap_end].isspace():
        gap_end += 1
    lines = content.splitlines(keepends=True)
    first_gap_line = content.count("\n", 0, gap_start) + 1
    last = content.count("\n", 0, gap_end) + 1
    first = min(first_gap_line, last - MIN_CARRIER_LINES + 1)
    while first >= 1 and last - first < MAX_CARRIER_LINES:
        carrier = "".join(lines[first - 1:last])
        if content.count(carrier) == 1:
            return InsertionLocus(
                path=path, revision=revision, language=structure.language, owner_symbol_id=owner.symbol_id,
                owner_lookup_key=owner.lookup_key, tier=tier, gap_start=gap_start, gap_end=gap_end,
                start_line=first, end_line=last, carrier=carrier, planned_members=tuple(planned),
                provenance=f"{owner.provenance}/{structure.parser_digest[:16]}"), ""
        first -= 1
    return None, "no unique exact carrier ends at the insertion boundary"


def _insertion_offsets(before: str, after: str) -> Optional[Tuple[int, int]]:
    """When ``after`` is ``before`` with one contiguous run of characters
    added, every offset of ``before`` where that addition could have been
    made, as [low, high]; None for any other change."""
    added = len(after) - len(before)
    if added <= 0:
        return None
    prefix = 0
    limit = len(before)
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    if prefix + suffix < len(before):
        return None
    return len(before) - suffix, prefix


def verify_insertion(locus: InsertionLocus, before: str, after: str, sent: Sequence[str]) -> Optional[str]:
    """Why the change ``before`` -> ``after`` is not authorized by ``locus``
    (None: it is a pure insertion at the locus of exactly that revision,
    the owner and every original symbol are intact, and the carrier was in
    the request actually sent)."""
    from kriya.code_intel.model import ParseState
    from kriya.code_intel.parsing import parse_text
    from kriya.workflow.edit_safety import content_revision

    if content_revision(before) != locus.revision:
        return f"{STALE_INSERTION_LOCUS}: {locus.path} is no longer the revision the insertion locus was decided for"
    old = parse_text(locus.path, before)
    owner = next((symbol for symbol in old.symbols if symbol.symbol_id == locus.owner_symbol_id), None)
    data = before.encode("utf-8")
    if (owner is None or owner.body is None or old.state is not ParseState.PARSED
            or not _char_offset(data, owner.body.start_byte) < locus.gap_start <= locus.gap_end
            < _char_offset(data, owner.body.end_byte)):
        return f"{STALE_INSERTION_LOCUS}: the owner {locus.owner_lookup_key} no longer encloses the insertion locus"
    lines = before.splitlines(keepends=True)
    if "".join(lines[locus.start_line - 1:locus.end_line]) != locus.carrier:
        return f"{STALE_INSERTION_LOCUS}: the insertion carrier no longer matches {locus.path}"
    if not any(locus.carrier in prompt for prompt in sent):
        return f"{INSERTION_LOCUS_NOT_SENT}: the insertion carrier was not in the request this invocation sent"
    offsets = _insertion_offsets(before, after)
    if offsets is None or offsets[1] < locus.gap_start or offsets[0] > locus.gap_end:
        return (f"{INSERTION_OUTSIDE_LOCUS}: the change to {locus.path} is not a pure insertion at the authorized "
                f"boundary of {locus.owner_lookup_key} (lines {locus.start_line}-{locus.end_line}); existing source, "
                "imports and every other place stay unchanged")
    new = parse_text(locus.path, after)
    if new.state is not ParseState.PARSED:
        return f"{INSERTION_STRUCTURE_CHANGED}: {locus.path} no longer parses cleanly after the insertion"
    old_keys = {(symbol.kind, symbol.lookup_key) for symbol in old.symbols}
    new_keys = {(symbol.kind, symbol.lookup_key) for symbol in new.symbols}
    owner_prefix = locus.owner_lookup_key + "."
    escaped = sorted(key for kind, key in new_keys - old_keys if not key.startswith(owner_prefix))
    if not old_keys <= new_keys or escaped or not new_keys - old_keys:
        return (f"{INSERTION_STRUCTURE_CHANGED}: the insertion changed declarations outside {locus.owner_lookup_key}"
                + (f" ({', '.join(escaped)})" if escaped else " (an original declaration disappeared, or no new member "
                                                          "was declared)"))
    return None


def structural_insertion_readiness(path: str, required: bool) -> str:
    """Graphify/preflight: YES when the target language supports structural
    new-member insertion, NO when it does not, NOT_REQUIRED when the task
    needs none."""
    from kriya.workflow.language_adapters import Capability, CapabilityStatus, capability_status

    if not required:
        return "NOT_REQUIRED"
    return "NO" if capability_status(path, Capability.STRUCTURAL_INSERTION) is CapabilityStatus.UNSUPPORTED else "YES"
