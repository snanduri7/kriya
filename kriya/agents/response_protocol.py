"""FILE-INTEGRITY-CONTRACT-001: the Developer response protocol boundary.

A model response is protocol plus payload. This module parses the protocol
and hands the payload on untouched: it may remove a recognized wrapper that
encloses the WHOLE payload, never edit, trim or "repair" anything inside it.
A response that does not match its protocol exactly is refused with a typed
reason code, and the attempt asks the model again; Kriya never guesses which
part of a malformed response was meant as source.

Two protocols emit the same ``DeveloperResponse``:

* ``legacy_strict`` - the historical markers, recognized only as exact
  full lines at column 0 (trailing spaces/tabs/CR on a marker line are
  tolerated): ``FIX ANALYSIS:`` (text may follow on the line), then exactly
  one outcome - ``SEARCH:``/``REPLACE:`` pairs, one ``FILE CONTENT:``, or
  ``NO CHANGE NEEDED`` (text may follow). A marker line inside a payload is
  ambiguous and refused.
* ``structured`` (``kriya_sentinel_v1``, the production protocol) - the
  grammar is: an optional free-text analysis section, then outcome blocks,
  then nothing but blank lines. Every block is opened and closed by exact
  sentinel lines naming the target path::

      <<<KRIYA:FILE path="p">>>        ... <<<KRIYA:END_FILE>>>
                                          (or <<<KRIYA:END_FILE no_final_newline>>>)
      <<<KRIYA:EDIT path="p">>>        <<<KRIYA:SEARCH>>> ... <<<KRIYA:REPLACE>>> ...
                                       (pairs repeat)  <<<KRIYA:END_EDIT>>>
      <<<KRIYA:NO_CHANGE path="p">>>

  A payload ends only at its own end marker; any text after the last block
  is INVALID_EDIT_PROTOCOL and never reaches a file. Payload lines are
  opaque; a payload line that itself starts with the sentinel prefix is
  refused. Newline semantics are explicit: every FILE payload line is
  newline-terminated, and ``no_final_newline`` on the end marker is the one
  way to state that the file has no final newline. For an existing file the
  Existing File Convention Policy (file_integrity.keep_final_newline_state,
  FileSnapshot.encode) then keeps that file's BOM, line endings and final
  newline state. ``path`` is metadata: it is normalized as a safe
  repository-relative POSIX path (normalize_protocol_path) and must equal
  the requested target; an absolute, escaping or malformed path is refused.
  The whole response may be wrapped in one outer fence (a grammar rule, not
  a payload rewrite). There is never an automatic fallback to another
  protocol: one invocation has exactly one protocol.

``legacy_strict`` (``strict_legacy_v1``) is compatibility-only: the legacy
markers have no payload terminator, so prose after a REPLACE or FILE
CONTENT block cannot be told apart from payload, a final newline cannot be
expressed, and a non-Markdown file with a column-0 fence cannot be carried.
It is not production-equivalent to the structured protocol.

Raw content (a first-pass file with no markers) accepts one legacy wrapper:
the whole response is a single fenced block. Anything else containing a
column-0 fence line is ambiguous, except for Markdown-family targets, whose
fences are content.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

LEGACY_STRICT = "legacy_strict"
STRUCTURED = "structured"
RESPONSE_PROTOCOLS = (LEGACY_STRICT, STRUCTURED)
# The versioned, model-facing identity of each protocol: bound into
# qualification (model_qualification.policy_digest_for) - a record qualified
# under one protocol is STALE under another.
PROTOCOL_IDENTITIES = {LEGACY_STRICT: "strict_legacy_v1", STRUCTURED: "kriya_sentinel_v1"}
# Must equal AutonomyConfig.developer_response_protocol's default (tested).
DEFAULT_RESPONSE_PROTOCOL = STRUCTURED

AMBIGUOUS_FILE_RESPONSE_PROTOCOL = "AMBIGUOUS_FILE_RESPONSE_PROTOCOL"
INVALID_EDIT_PROTOCOL = "INVALID_EDIT_PROTOCOL"
CONFLICTING_DEVELOPER_RESPONSE = "CONFLICTING_DEVELOPER_RESPONSE"
MODEL_EDIT_PROTOCOL_INVALID = "MODEL_EDIT_PROTOCOL_INVALID"
PROTOCOL_REASON_CODES = frozenset({
    AMBIGUOUS_FILE_RESPONSE_PROTOCOL, INVALID_EDIT_PROTOCOL,
    CONFLICTING_DEVELOPER_RESPONSE, MODEL_EDIT_PROTOCOL_INVALID,
})

FILE, EDITS, NO_CHANGE, INVALID = "file", "edits", "no_change", "invalid"

# Targets whose own format uses fenced blocks as content.
_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc"})
_MARKDOWN_FENCE_LANGUAGES = frozenset({"markdown", "md"})
_FENCE_OPEN_RE = re.compile(r"^```[A-Za-z0-9_+.-]*$")

_ANALYSIS = "FIX ANALYSIS:"
_SEARCH = "SEARCH:"
_REPLACE = "REPLACE:"
_FILE_CONTENT = "FILE CONTENT:"
_NO_CHANGE = "NO CHANGE NEEDED"

SENTINEL_PREFIX = "<<<KRIYA:"
_STRUCTURED_OPEN_RE = re.compile(r'^<<<KRIYA:(FILE|EDIT|NO_CHANGE) path="([^"]+)">>>$')
_STRUCTURED_SEARCH = "<<<KRIYA:SEARCH>>>"
_STRUCTURED_REPLACE = "<<<KRIYA:REPLACE>>>"
_STRUCTURED_END = {"FILE": "<<<KRIYA:END_FILE>>>", "EDIT": "<<<KRIYA:END_EDIT>>>"}
_END_FILE_NO_FINAL_NEWLINE = "<<<KRIYA:END_FILE no_final_newline>>>"


@dataclass(frozen=True)
class DeveloperResponse:
    """The typed mutation intent of one per-file Developer response."""

    kind: str
    protocol: str
    analysis: Optional[str] = None
    content: Optional[str] = None
    edits: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    reason_code: Optional[str] = None
    detail: Optional[str] = None
    # Structured FILE only: the payload's declared final-newline state.
    final_newline: Optional[bool] = None

    @property
    def error(self) -> Optional[str]:
        return f"{self.reason_code}: {self.detail}" if self.kind == INVALID else None

    def edit_dicts(self) -> List[Dict[str, str]]:
        return [{"search": search, "replace": replace} for search, replace in self.edits]


def _invalid(protocol: str, code: str, detail: str, analysis: Optional[str] = None) -> DeveloperResponse:
    return DeveloperResponse(kind=INVALID, protocol=protocol, analysis=analysis, reason_code=code, detail=detail)


def developer_response_protocol(config) -> str:
    """The configured Developer response protocol (autonomy.developer_response_protocol);
    the default protocol when the config names none. An unknown value is refused
    by config validation, never mapped to another protocol."""
    autonomy = getattr(config, "autonomy", None)
    value = getattr(autonomy, "developer_response_protocol", DEFAULT_RESPONSE_PROTOCOL)
    if value not in RESPONSE_PROTOCOLS:
        raise ValueError(f"unknown developer response protocol {value!r}")
    return value


def response_protocol_identity(config) -> str:
    """The versioned protocol identity (``kriya_sentinel_v1``...) of ``config``."""
    return PROTOCOL_IDENTITIES[developer_response_protocol(config)]


def normalize_protocol_path(path: str) -> Optional[str]:
    """A protocol ``path=`` value as a safe repository-relative POSIX path
    (``./a//b.py`` -> ``a/b.py``), or None when it is empty, absolute, holds
    a NUL or backslash, or leaves the repository root after normalization.
    Normalization never widens scope: the result must still equal the
    requested target, which is authorized separately."""
    if not path or "\x00" in path or "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return None
    normalized = posixpath.normpath(path)
    if normalized in (".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def is_markdown_target(filepath: Optional[str]) -> bool:
    return bool(filepath) and os.path.splitext(filepath)[1].lower() in _MARKDOWN_EXTENSIONS


def _is_fence_line(line: str) -> bool:
    return line.rstrip(" \t\r").startswith("```")


def _outer_fence(lines: Sequence[str]) -> Optional[Tuple[str, List[str]]]:
    """(language, inner lines) when the non-blank lines are exactly one
    fenced block with no fence line inside it; else None."""
    body = [line.rstrip("\r") for line in lines]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if len(body) < 2 or not _FENCE_OPEN_RE.match(body[0].rstrip(" \t")) or body[-1].strip() != "```":
        return None
    inner = body[1:-1]
    if any(_is_fence_line(line) for line in inner):
        return None
    return body[0].rstrip(" \t")[3:].lower(), inner


def parse_raw_payload(text: str, filepath: Optional[str]) -> DeveloperResponse:
    """A response whose whole text is the file (no protocol markers)."""
    lines = text.split("\n")
    fenced = _outer_fence(lines)
    markdown = is_markdown_target(filepath)
    if fenced is not None and (not markdown or fenced[0] in _MARKDOWN_FENCE_LANGUAGES):
        inner = fenced[1]
        # The newline before the closing fence is the fence's own framing.
        return DeveloperResponse(kind=FILE, protocol=LEGACY_STRICT, content="\n".join(inner))
    if not markdown and any(_is_fence_line(line) and not line.startswith((" ", "\t")) for line in lines):
        return _invalid(
            LEGACY_STRICT, AMBIGUOUS_FILE_RESPONSE_PROTOCOL,
            f"the response for '{filepath}' contains a fenced block but is not exactly one fenced "
            "block; Kriya does not guess which part is the file. Return only the file content",
        )
    return DeveloperResponse(kind=FILE, protocol=LEGACY_STRICT, content=text)


def _marker(line: str) -> Optional[str]:
    stripped = line.rstrip(" \t\r")
    if stripped in (_SEARCH, _REPLACE, _FILE_CONTENT):
        return stripped
    if stripped.startswith(_NO_CHANGE):
        return _NO_CHANGE
    if stripped.startswith(_ANALYSIS):
        return _ANALYSIS
    return None


def _frame(lines: List[str], filepath: Optional[str]) -> Optional[str]:
    """A SEARCH/REPLACE payload between marker lines: the empty separator
    lines at its two edges are framing; a single fence enclosing the whole
    block is a wrapper. Nothing inside is changed. None when the block holds
    a column-0 fence that is not that one enclosing wrapper (prose and a
    fenced dump after the edit, say) - ambiguous, never truncated."""
    start, end = 0, len(lines)
    while start < end and lines[start] == "":
        start += 1
    while end > start and lines[end - 1] == "":
        end -= 1
    block = lines[start:end]
    fenced = _outer_fence(block)
    if fenced is not None:
        return "\n".join(fenced[1])
    if not is_markdown_target(filepath) and any(_is_fence_line(line) and not line.startswith((" ", "\t"))
                                                for line in block):
        return None
    return "\n".join(block)


def _framed_pair(search: List[str], replace: List[str], filepath: Optional[str]) -> Optional[Tuple[str, str]]:
    framed_search, framed_replace = _frame(search, filepath), _frame(replace, filepath)
    if framed_search is None or framed_replace is None:
        return None
    return framed_search, framed_replace


def _ambiguous_block(analysis: Optional[str]) -> DeveloperResponse:
    return _invalid(
        LEGACY_STRICT, AMBIGUOUS_FILE_RESPONSE_PROTOCOL,
        "a SEARCH/REPLACE block contains a fenced block that does not enclose the whole block "
        "(prose or a second file after the edit?); Kriya does not guess where the edit ends",
        analysis,
    )


def parse_legacy_repair(text: str, filepath: Optional[str], *, patch_allowed: bool) -> DeveloperResponse:
    """The strict legacy repair grammar (see the module docstring)."""
    lines = text.split("\n")
    analysis: List[str] = []
    edits: List[Tuple[str, str]] = []
    search: Optional[List[str]] = None
    replace: Optional[List[str]] = None
    state = "analysis"

    def _analysis() -> Optional[str]:
        joined = "\n".join(analysis).strip()
        return joined or None

    for index, line in enumerate(lines):
        marker = _marker(line)
        if state == "analysis":
            if marker in (None, _ANALYSIS):
                analysis.append(line[len(_ANALYSIS):] if marker == _ANALYSIS else line)
                continue
            if marker == _REPLACE:
                return _invalid(LEGACY_STRICT, INVALID_EDIT_PROTOCOL, "REPLACE: before any SEARCH:", _analysis())
            if marker == _FILE_CONTENT:
                payload = lines[index + 1:]
                if any(_marker(rest) not in (None, _ANALYSIS) for rest in payload):
                    return _invalid(
                        LEGACY_STRICT, CONFLICTING_DEVELOPER_RESPONSE,
                        "the FILE CONTENT payload contains another outcome marker line (SEARCH:/REPLACE:/"
                        "FILE CONTENT:/NO CHANGE NEEDED); exactly one outcome is allowed", _analysis(),
                    )
                # The legacy FILE CONTENT section has no terminator: the blank
                # lines at its two edges are framing, never content. The file's
                # own final-newline state is restored at the write (it is the
                # file's convention, not the response's).
                parsed = parse_raw_payload("\n".join(payload).strip("\n"), filepath)
                if parsed.kind == INVALID:
                    return _invalid(LEGACY_STRICT, parsed.reason_code or AMBIGUOUS_FILE_RESPONSE_PROTOCOL,
                                    parsed.detail or "", _analysis())
                return DeveloperResponse(kind=FILE, protocol=LEGACY_STRICT, analysis=_analysis(),
                                         content=parsed.content or "")
            if marker == _NO_CHANGE:
                if any(_marker(rest) in (_SEARCH, _REPLACE, _FILE_CONTENT) for rest in lines[index + 1:]):
                    return _invalid(
                        LEGACY_STRICT, CONFLICTING_DEVELOPER_RESPONSE,
                        "NO CHANGE NEEDED together with a mutation outcome", _analysis(),
                    )
                reason = "\n".join([line.rstrip(" \t\r")[len(_NO_CHANGE):].lstrip(": ")] + lines[index + 1:]).strip()
                analysis_text = _analysis()
                return DeveloperResponse(
                    kind=NO_CHANGE, protocol=LEGACY_STRICT,
                    analysis=analysis_text or reason or None,
                )
            state, search = "search", []
            continue
        if state == "search":
            if marker == _REPLACE:
                state, replace = "replace", []
                continue
            if marker is not None and marker != _ANALYSIS:
                return _invalid(LEGACY_STRICT, INVALID_EDIT_PROTOCOL,
                                f"'{marker}' inside a SEARCH block (a SEARCH needs its REPLACE first)", _analysis())
            search.append(line)
            continue
        # state == "replace"
        if marker == _SEARCH:
            pair = _framed_pair(search, replace, filepath)
            if pair is None:
                return _ambiguous_block(_analysis())
            edits.append(pair)
            state, search, replace = "search", [], None
            continue
        if marker in (_FILE_CONTENT, _NO_CHANGE):
            return _invalid(LEGACY_STRICT, CONFLICTING_DEVELOPER_RESPONSE,
                            f"'{marker}' after a SEARCH/REPLACE edit; exactly one outcome is allowed", _analysis())
        if marker == _REPLACE:
            return _invalid(LEGACY_STRICT, INVALID_EDIT_PROTOCOL, "two REPLACE: markers for one SEARCH:", _analysis())
        replace.append(line)
    if state == "analysis":
        return _invalid(
            LEGACY_STRICT, MODEL_EDIT_PROTOCOL_INVALID,
            "missing repair outcome marker: expected SEARCH/REPLACE, FILE CONTENT, or NO CHANGE NEEDED "
            "as an exact line", _analysis(),
        )
    if state == "search":
        return _invalid(LEGACY_STRICT, INVALID_EDIT_PROTOCOL, "a SEARCH: block has no REPLACE:", _analysis())
    pair = _framed_pair(search, replace, filepath)
    if pair is None:
        return _ambiguous_block(_analysis())
    edits.append(pair)
    if not patch_allowed:
        return _invalid(LEGACY_STRICT, INVALID_EDIT_PROTOCOL,
                        "SEARCH/REPLACE edits were not offered for this file; use FILE CONTENT", _analysis())
    return DeveloperResponse(kind=EDITS, protocol=LEGACY_STRICT, analysis=_analysis(), edits=tuple(edits))


def parse_structured(text: str, filepath: str, *, patch_allowed: bool = True,
                     file_allowed: bool = True) -> DeveloperResponse:
    """The sentinel protocol (see STRUCTURED_* and the module docstring)."""
    lines = text.split("\n")
    fenced = _outer_fence(lines)
    if fenced is not None and any(line.startswith(SENTINEL_PREFIX) for line in fenced[1]):
        lines = fenced[1]
    analysis: List[str] = []
    outcome: Optional[str] = None
    content: Optional[str] = None
    final_file_newline: Optional[bool] = None
    edits: List[Tuple[str, str]] = []
    index = 0

    def _fail(code: str, detail: str) -> DeveloperResponse:
        return _invalid(STRUCTURED, code, detail, "\n".join(analysis).strip() or None)

    while index < len(lines):
        line = lines[index].rstrip(" \t\r")
        if not line.startswith(SENTINEL_PREFIX):
            if outcome is None:
                analysis.append(lines[index])
            elif line.strip():
                return _fail(INVALID_EDIT_PROTOCOL, "text after the last protocol block")
            index += 1
            continue
        opened = _STRUCTURED_OPEN_RE.match(line)
        if not opened:
            # PROTOCOL-FEEDBACK-EDIT-OPENING-001 (BACKEND-FINAL-CLOSURE-005, P5-T2: six of eight attempts began
            # with a SEARCH line, once carrying a path, and the message named only the symptom): the
            # diagnostic names the missing frame. The parse outcome is unchanged (INVALID), the wire
            # protocol is unchanged - this is the repair prompt's text only.
            if line.startswith(("<<<KRIYA:SEARCH", "<<<KRIYA:REPLACE", "<<<KRIYA:END_EDIT")):
                return _fail(INVALID_EDIT_PROTOCOL,
                             f"{line!r} appeared outside an EDIT block - an edit begins with "
                             f"<<<KRIYA:EDIT path=\"{filepath}\">>> on its own line (SEARCH and REPLACE lines carry "
                             "no path), its SEARCH/REPLACE pairs follow, and <<<KRIYA:END_EDIT>>> closes it")
            return _fail(INVALID_EDIT_PROTOCOL, f"unexpected protocol line {line!r}")
        kind, path = opened.group(1), opened.group(2)
        normalized = normalize_protocol_path(path)
        if normalized is None:
            return _fail(INVALID_EDIT_PROTOCOL, f"block path {path!r} is not a safe repository-relative path")
        if normalized != normalize_protocol_path(filepath):
            return _fail(INVALID_EDIT_PROTOCOL, f"block names '{path}', expected '{filepath}'")
        new_outcome = {"FILE": FILE, "EDIT": EDITS, "NO_CHANGE": NO_CHANGE}[kind]
        if outcome is not None and (outcome != new_outcome or outcome != EDITS):
            return _fail(CONFLICTING_DEVELOPER_RESPONSE, "more than one outcome block")
        outcome = new_outcome
        index += 1
        if kind == "NO_CHANGE":
            continue
        end = _STRUCTURED_END[kind]
        ends = (end, _END_FILE_NO_FINAL_NEWLINE) if kind == "FILE" else (end,)
        body: List[str] = []
        while index < len(lines) and lines[index].rstrip(" \t\r") not in ends:
            body.append(lines[index])
            index += 1
        if index >= len(lines):
            return _fail(INVALID_EDIT_PROTOCOL, f"missing {end}")
        final_newline = lines[index].rstrip(" \t\r") != _END_FILE_NO_FINAL_NEWLINE
        index += 1
        if kind == "FILE":
            if any(item.startswith(SENTINEL_PREFIX) for item in body):
                return _fail(INVALID_EDIT_PROTOCOL, "a sentinel line inside a FILE payload")
            # Explicit grammar: each payload line is newline-terminated, unless
            # the end marker declares no_final_newline.
            content = "\n".join(body) + ("\n" if body and final_newline else "")
            final_file_newline = final_newline
            continue
        parsed = _structured_edit_pairs(body)
        if isinstance(parsed, str):
            return _fail(INVALID_EDIT_PROTOCOL, parsed)
        edits.extend(parsed)
    analysis_text = "\n".join(analysis).strip() or None
    if outcome is None:
        return _fail(MODEL_EDIT_PROTOCOL_INVALID, "no <<<KRIYA:...>>> outcome block")
    if outcome == EDITS and not patch_allowed:
        return _fail(INVALID_EDIT_PROTOCOL, "EDIT blocks were not offered for this file")
    if outcome == FILE and not file_allowed:
        return _fail(INVALID_EDIT_PROTOCOL, "a whole-file replacement was not offered for this file")
    return DeveloperResponse(kind=outcome, protocol=STRUCTURED, analysis=analysis_text,
                             content=content, edits=tuple(edits),
                             final_newline=final_file_newline if outcome == FILE else None)


def _structured_edit_pairs(body: List[str]):
    pairs: List[Tuple[str, str]] = []
    search: Optional[List[str]] = None
    replace: Optional[List[str]] = None
    for raw in body:
        line = raw.rstrip(" \t\r")
        if line == _STRUCTURED_SEARCH:
            if search is not None and replace is None:
                return "a SEARCH section has no REPLACE"
            if search is not None:
                pairs.append(("\n".join(search), "\n".join(replace or [])))
            search, replace = [], None
        elif line == _STRUCTURED_REPLACE:
            if search is None or replace is not None:
                return "REPLACE without its own SEARCH"
            replace = []
        elif line.startswith(SENTINEL_PREFIX):
            return f"unexpected protocol line {line!r} inside an EDIT block"
        elif replace is not None:
            replace.append(raw)
        elif search is not None:
            search.append(raw)
        elif raw.strip():
            return "text inside an EDIT block before its first SEARCH"
    if search is None:
        return "an EDIT block with no SEARCH section"
    if replace is None:
        return "a SEARCH section has no REPLACE"
    pairs.append(("\n".join(search), "\n".join(replace)))
    return pairs


def structured_contract(filepath: str, *, analysis_required: bool, allow_edit: bool, allow_file: bool,
                        allow_no_change: bool) -> str:
    """The prompt text that defines the structured protocol for one file."""
    parts = [
        "RESPONSE PROTOCOL (exact lines, each on its own line, at column 0):",
    ]
    if analysis_required:
        parts.append("First write 1-3 sentences of analysis as plain text. Then write exactly one outcome:")
    else:
        parts.append("Write exactly one outcome and nothing else:")
    if allow_edit:
        parts.append(
            f'<<<KRIYA:EDIT path="{filepath}">>>\n<<<KRIYA:SEARCH>>>\n<exact existing lines, copied verbatim>\n'
            "<<<KRIYA:REPLACE>>>\n<the replacement lines>\n<<<KRIYA:END_EDIT>>>\n"
            "(repeat SEARCH/REPLACE pairs inside one EDIT block for several changes; each SEARCH must be "
            "complete lines that occur exactly once in the file and must not overlap another SEARCH)"
        )
    if allow_file:
        parts.append(f'<<<KRIYA:FILE path="{filepath}">>>\n<the complete file content>\n<<<KRIYA:END_FILE>>>\n'
                     "(every line you write ends with a newline in the file)")
    if allow_no_change:
        parts.append(f'<<<KRIYA:NO_CHANGE path="{filepath}">>>   (only if this file needs no change)')
    parts.append(
        "Everything between the protocol lines is copied into the file byte for byte - no markdown "
        "fences, no line numbers. Never combine outcomes. Nothing may follow the last protocol line."
    )
    return "\n".join(parts) + "\n"
