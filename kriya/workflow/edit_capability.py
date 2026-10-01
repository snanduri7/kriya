"""CONTEXT-EDIT-PROTOCOL-001: the mutation operations a Developer invocation
may be offered, decided once from the authoritative context it is given.

Invariant: Kriya never offers a mutation operation that is infeasible under
the authoritative context available to that Developer invocation. One
EditCapability per existing target is computed at the Developer choke point
and read by BOTH the operation contract the model is shown and the
response-side validators, so an operation can never be offered under one
authority predicate and rejected under another.

- FULL_FILE_REPLACEMENT is feasible only with authoritative full source (the
  D1 predicate, _has_authoritative_full_source, or the deterministic restore
  phase) - decided by the caller and passed in as ``full_file``.
- ANCHORED_EDIT is feasible only when byte-exact current source is available
  for the edit: exact spans (a member_exact package entry, or windows this
  module derives) that cover every located edit locus. Skeleton text, whose
  bodies are elided, never counts.
- Neither feasible: the caller stops before inference
  (CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE).

Loci come only from deterministic evidence: code fragments quoted in the
user's goal and found verbatim in the target, the lines a failure names, a
rejected SEARCH block's real match (or its closest real lines), and anchors
the validator found outside the authoritative context. Span granularity
per locus: its enclosing member, else enclosing indentation block, when that
fits an equal share of the budget; otherwise a local window, every window of
one radius - the largest that fits, up to one that doubles with each anchor
failure on that file. Every span is bound to the revision of the
source it was cut from; a span of an older revision authorizes nothing."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from kriya.workflow.context_source import member_boundaries_for
from kriya.workflow.edit_safety import content_revision, normalize_whitespace
from kriya.workflow.file_integrity import ANCHOR_NOT_IN_FILE as ANCHOR_NOT_IN_FILE

ANCHORED_EDIT = "anchored_edit"
FULL_FILE_REPLACEMENT = "full_file_replacement"

CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE = "CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE"
ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT = "ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT"
ANCHOR_CONTEXT_NOT_ESCALATED = "ANCHOR_CONTEXT_NOT_ESCALATED"

# Share of the Developer allocation window reserved for exact windows; the
# windows are mandatory text sized before the request is fitted.
EXACT_WINDOW_SHARE = 0.12
BASE_WINDOW_RADIUS = 8


def exact_window_reserve(prompt_window: int) -> int:
    """Allocator units (estimate_tokens) held back for the exact windows of
    a Developer request: deducted from the source package sized before it
    (the known-target and retry member packages), so the windows never push
    the request past its capacity."""
    return int(prompt_window * EXACT_WINDOW_SHARE)
# A fragment that matches more lines than this localizes nothing.
MAX_LOCUS_MATCHES = 3
MIN_FRAGMENT_CHARS = 8
_FUZZY_MIN_OVERLAP = 0.6
_CODE_PUNCTUATION = re.compile(r"[=().\[\]{}:;<>,+*/-]")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BACKTICK_SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_CLOSING_LINE = re.compile(r"^\s*[\]})]")


@dataclass(frozen=True)
class ExactSpan:
    """A byte-exact slice of one revision of a file: lines start..end
    (1-based, inclusive). ``unit`` says how it was chosen."""

    path: str
    start_line: int
    end_line: int
    unit: str
    text: str
    revision: str


@dataclass(frozen=True)
class EditCapability:
    path: str
    revision: str
    full_file: bool
    spans: Tuple[ExactSpan, ...] = ()
    loci: Tuple[int, ...] = ()
    uncovered_loci: Tuple[int, ...] = ()
    level: int = 0
    _normalized_spans: Tuple[str, ...] = field(default=(), compare=False, repr=False)

    @property
    def anchored(self) -> bool:
        return bool(self.spans) and not self.uncovered_loci

    @property
    def operations(self) -> Tuple[str, ...]:
        return tuple(op for op, ok in ((ANCHORED_EDIT, self.anchored), (FULL_FILE_REPLACEMENT, self.full_file)) if ok)

    @property
    def feasible(self) -> bool:
        return bool(self.operations)

    @property
    def digest(self) -> str:
        """What the model can rely on: revision, the operations and each
        exact span's position and bytes."""
        payload = {
            "revision": self.revision, "operations": list(self.operations),
            "spans": [[s.start_line, s.end_line, s.unit, content_revision(s.text)] for s in self.spans],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def anchor_status(self, search: str, current_content: str) -> Optional[str]:
        """None when ``search`` may be relied on under this capability for
        ``current_content``; otherwise the typed reason it may not."""
        norm_search = normalize_whitespace(search)
        if not norm_search:
            return None
        in_file = norm_search in normalize_whitespace(current_content)
        if not in_file:
            return ANCHOR_NOT_IN_FILE
        if content_revision(current_content) != self.revision:
            return ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT
        if self.full_file:
            return None
        normalized = self._normalized_spans or tuple(normalize_whitespace(s.text) for s in self.spans)
        if any(norm_search in span for span in normalized):
            return None
        return ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT

    def summary(self) -> dict:
        return {
            "path": self.path, "revision": self.revision, "full_file": self.full_file,
            "operations": list(self.operations), "level": self.level,
            "spans": [{"start_line": s.start_line, "end_line": s.end_line, "unit": s.unit} for s in self.spans],
            "loci": list(self.loci), "uncovered_loci": list(self.uncovered_loci), "digest": self.digest,
        }


# --- localization --------------------------------------------------------------------
def _normalize_line(text: str) -> str:
    return " ".join(text.split())


def goal_code_fragments(goal: str) -> List[str]:
    """Code the user's goal quotes: inline `...` spans and fenced lines, kept
    only when long enough and code-shaped (punctuation), so a bare word
    never localizes anything."""
    candidates = [m.group(1) for m in _BACKTICK_SPAN.finditer(goal or "")]
    for block in _FENCE.finditer(goal or ""):
        candidates.extend(block.group(1).splitlines())
    fragments: List[str] = []
    for candidate in candidates:
        normalized = _normalize_line(candidate)
        if len(normalized) >= MIN_FRAGMENT_CHARS and _CODE_PUNCTUATION.search(normalized) and normalized not in fragments:
            fragments.append(normalized)
    return fragments


def locate_fragments(lines: Sequence[str], fragments: Iterable[str]) -> List[int]:
    """1-based lines containing a fragment verbatim (whitespace-normalized);
    a fragment matching more than MAX_LOCUS_MATCHES lines is ignored."""
    normalized_lines = [_normalize_line(line) for line in lines]
    loci: List[int] = []
    for fragment in fragments:
        hits = [i + 1 for i, line in enumerate(normalized_lines) if fragment in line]
        if 0 < len(hits) <= MAX_LOCUS_MATCHES:
            loci.extend(hits)
    return loci


def locate_search_text(lines: Sequence[str], search: str) -> List[int]:
    """Where a SEARCH block that did not apply points in the real source:
    every line it quotes verbatim, else, per quoted line, the unique real
    line sharing most of its identifiers."""
    normalized_lines = [_normalize_line(line) for line in lines]
    token_sets = [set(_IDENTIFIER.findall(line)) for line in lines]
    loci: List[int] = []
    for raw in (search or "").splitlines():
        wanted = _normalize_line(raw)
        if len(wanted) < MIN_FRAGMENT_CHARS:
            continue
        exact = [i + 1 for i, line in enumerate(normalized_lines) if line == wanted]
        if 0 < len(exact) <= MAX_LOCUS_MATCHES:
            loci.extend(exact)
            continue
        tokens = set(_IDENTIFIER.findall(raw))
        if len(tokens) < 2:
            continue
        scores = [len(tokens & line_tokens) / len(tokens) for line_tokens in token_sets]
        best = max(scores, default=0.0)
        best_lines = [i + 1 for i, score in enumerate(scores) if score == best]
        if best >= _FUZZY_MIN_OVERLAP and len(best_lines) == 1:
            loci.extend(best_lines)
    return loci


# --- spans ---------------------------------------------------------------------------
def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _enclosing_block(lines: Sequence[str], locus: int) -> Optional[Tuple[int, int]]:
    """The innermost indentation block holding ``locus``: its header (the
    nearest earlier line indented less) through the last line indented
    deeper than the header (plus a closing bracket line at the header's
    indentation)."""
    index = locus - 1
    if not lines[index].strip():
        return None
    own = _indent(lines[index])
    header = next((i for i in range(index - 1, -1, -1) if lines[i].strip() and _indent(lines[i]) < own), None)
    if header is None:
        return None
    header_indent = _indent(lines[header])
    end = header
    for i in range(header + 1, len(lines)):
        if not lines[i].strip():
            continue
        if _indent(lines[i]) > header_indent:
            end = i
            continue
        if _indent(lines[i]) == header_indent and _CLOSING_LINE.match(lines[i]):
            end = i
        break
    return header + 1, end + 1


def _enclosing_member(members, locus: int) -> Optional[Tuple[int, int]]:
    holding = [m for m in members or () if m.start_line <= locus <= m.end_line]
    if not holding:
        return None
    inner = min(holding, key=lambda m: m.end_line - m.start_line)
    return inner.start_line, inner.end_line


def _slice(lines_keepends: Sequence[str], start: int, end: int) -> str:
    return "".join(lines_keepends[start - 1:end])


def _span_chars(lines_keepends, span) -> int:
    return len(_slice(lines_keepends, span[1], span[2]))


def _allocate(lines, lines_keepends, members, pending: List[int], level: int, budget_chars: int):
    """Spans for ``pending`` loci within ``budget_chars``: (merged spans,
    uncovered loci). A locus whose enclosing member, else block, fits an
    equal share of the budget is shown as that unit; every other locus gets
    a local window, all of ONE radius - the largest, up to BASE*2**level,
    whose merged spans fit - so a new locus never leaves another with a
    smaller window than itself. When not even one-line windows all fit,
    loci are kept in line order while they fit and the rest are uncovered."""
    share = budget_chars // len(pending)
    structural = {}
    for locus in pending:
        for unit, found in (("member", _enclosing_member(members, locus)), ("block", _enclosing_block(lines, locus))):
            if found and _span_chars(lines_keepends, (unit, *found)) <= share:
                structural[locus] = (unit, *found)
                break
    radius = BASE_WINDOW_RADIUS * (2 ** level)
    while radius >= 0:
        chosen = [structural.get(locus) or ("window", max(1, locus - radius), min(len(lines), locus + radius))
                  for locus in pending]
        merged = _merge(chosen)
        if sum(_span_chars(lines_keepends, span) for span in merged) <= budget_chars:
            return merged, []
        radius = radius // 2 if radius > 0 else -1
    kept, uncovered, used = [], [], 0
    for locus in pending:
        span = structural.get(locus) or ("window", locus, locus)
        if used + _span_chars(lines_keepends, span) <= budget_chars:
            kept.append(span)
            used += _span_chars(lines_keepends, span)
        else:
            uncovered.append(locus)
    return _merge(kept), uncovered


# Units already in the prompt through the known-target/retry package.
SHOWN_UNITS = frozenset({"member_exact", "excerpt", "shown_full"})


def shown_exact_texts(tier: str, text: str) -> List[Tuple[str, str]]:
    """The byte-exact pieces a recorded context item shows, as (unit, text):
    a full or member_exact item whole; an implementation excerpt's head and tail,
    cut back to complete lines. Any other tier (skeleton, signatures)
    shows no authoritative anchor text."""
    from kriya.workflow.context_projection import EXCERPT_OMISSION_MARKER

    if not text:
        return []
    if tier == "full":
        return [("shown_full", text)]
    if tier == "member_exact":
        return [("member_exact", text)]
    if tier == "implementation_excerpt" and EXCERPT_OMISSION_MARKER in text:
        head, tail = text.split(EXCERPT_OMISSION_MARKER, 1)
        head = head[:head.rfind("\n") + 1]
        tail = tail[tail.find("\n") + 1:] if "\n" in tail else ""
        return [("excerpt", piece) for piece in (head, tail) if piece.strip()]
    return []


def _shown_span(path, content, lines_keepends, unit: str, text: str, revision: str):
    """The line range a shown exact piece covers in the current source (None
    when it is not there verbatim - then it authorizes nothing)."""
    offset = content.find(text)
    if offset < 0:
        return None
    start = content.count("\n", 0, offset) + 1
    end = start + text.rstrip("\n").count("\n")
    return ExactSpan(path, start, end, unit, _slice(lines_keepends, start, end), revision)


def build_edit_capability(
    path: str, content: str, *, full_file: bool, loci: Iterable[int], budget_chars: int,
    level: int = 0, shown: Iterable[Tuple[str, str]] = (),
) -> EditCapability:
    """The capability for one existing target of one Developer invocation.
    ``shown`` is the exact pieces already in the prompt (shown_exact_texts)."""
    revision = content_revision(content)
    lines_keepends = content.splitlines(keepends=True)
    lines = [line.rstrip("\r\n") for line in lines_keepends]
    ordered = sorted({locus for locus in loci if 1 <= locus <= len(lines)})
    spans: List[ExactSpan] = []
    for unit, text in shown:
        span = _shown_span(path, content, lines_keepends, unit, text, revision)
        if span is not None:
            spans.append(span)
    pending = [locus for locus in ordered if not any(s.start_line <= locus <= s.end_line for s in spans)]
    uncovered: List[int] = []
    if pending:
        merged, uncovered = _allocate(lines, lines_keepends, member_boundaries_for(path, content), pending, level,
                                      budget_chars)
        for unit, start, end in merged:
            spans.append(ExactSpan(path, start, end, unit, _slice(lines_keepends, start, end), revision))
    spans.sort(key=lambda s: s.start_line)
    return EditCapability(
        path=path, revision=revision, full_file=full_file, spans=tuple(spans), loci=tuple(ordered),
        uncovered_loci=tuple(uncovered), level=level,
        _normalized_spans=tuple(normalize_whitespace(s.text) for s in spans),
    )


def _merge(chosen: List[Tuple[str, int, int]]) -> List[Tuple[str, int, int]]:
    merged: List[Tuple[str, int, int]] = []
    for unit, start, end in sorted(chosen, key=lambda item: item[1]):
        if merged and start <= merged[-1][2] + 1:
            prev_unit, prev_start, prev_end = merged[-1]
            merged[-1] = (prev_unit if prev_unit == unit else "merged", prev_start, max(prev_end, end))
        else:
            merged.append((unit, start, end))
    return merged


EXACT_SOURCE_HEADER = "=== EXACT CURRENT SOURCE (authoritative, byte-exact - copy SEARCH text only from here)"


def render_exact_spans(capability: EditCapability) -> str:
    """The derived windows as prompt text (shown units are already in the
    context). The body is the exact bytes; the markers are on their own
    lines."""
    parts = []
    for span in capability.spans:
        if span.unit in SHOWN_UNITS:
            continue
        body = span.text if span.text.endswith("\n") else span.text + "\n"
        parts.append(
            f"\n\n{EXACT_SOURCE_HEADER}: {span.path} lines {span.start_line}-{span.end_line} "
            f"[{span.unit}] ===\n{body}=== END EXACT SOURCE {span.path} lines {span.start_line}-{span.end_line} ===\n"
        )
    return "".join(parts)
