"""Deterministic sanity checks applied to an anchored edit or full-file write before it reaches disk - whitespace-tolerant anchor matching, the locator-touch pre-flight check (Layer 1), and structural corruption detection. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization)."""

import asyncio
import difflib
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from kriya.workflow.failure_grounding import extract_error_source_locations

logger = logging.getLogger(__name__)


def normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def apply_anchored_edits(original_content: str, edits: List[Dict[str, str]], shown_context: str) -> str:
    current_content = original_content
    for idx, edit in enumerate(edits, 1):
        search_block = edit.get("search", "")
        replace_block = edit.get("replace", "")

        if not search_block:
            continue

        norm_search = normalize_whitespace(search_block)

        if shown_context:
            norm_shown = normalize_whitespace(shown_context)
            if norm_search not in norm_shown:
                raise ValueError(
                    f"Anchor matching failed for edit #{idx}: The search block contains code segments "
                    f"that were elided in the skeletonized context and not shown to the model."
                )

        exact_count = current_content.count(search_block)
        if exact_count >= 1:
            # An exact (unnormalized) match exists - the strictest, most-
            # preferred match shape, so its own count is the uniqueness
            # signal here, not a whitespace-normalized count over the whole
            # file (see the window branch below for why that can disagree
            # with what's actually being matched).
            if exact_count > 1:
                raise ValueError(
                    f"Anchor matching failed for edit #{idx}: The search block matched {exact_count} times (must match exactly once). "
                    f"Provide more context surrounding the search block."
                )
            current_content = current_content.replace(search_block, replace_block, 1)
            continue

        # No exact match - fall back to a whitespace-tolerant search that
        # also tolerates a DIFFERENT number of blank lines between content
        # and search block, not just different indentation - consistent with
        # normalize_whitespace's own blank-line-discarding philosophy used
        # everywhere else in this function (the shown_context check above,
        # for instance). A prior version used a FIXED-size raw-line window
        # (exactly len(search_block.splitlines()) raw lines) for both the
        # uniqueness check and the actual splice - found live, 2026-08-11
        # (kriya-oneshot-protocol-ignite-qpid audit): a search block with one
        # blank line between two statements, matched against content with
        # TWO blank lines at the same location, made the OLD whole-file
        # blank-line-collapsed uniqueness check report "exactly 1 match" (it
        # discards all blank lines before counting) while the fixed-size
        # window could never actually find it (the real match needs one more
        # raw line than the search block has) - the check said "found,
        # unique" while application then failed with "could not find match",
        # a self-contradictory outcome that burned a retry on Kriya's own
        # matching inconsistency, not a real problem with the edit.
        #
        # Matches the search block's own non-blank, stripped lines as a
        # contiguous subsequence against the content's non-blank, stripped
        # lines - the count of subsequence matches IS the uniqueness check
        # (no separate, disagreeing mechanism), and the actual RAW splice
        # range spans from the first to the last matched non-blank line's
        # real index, so any blank lines interspersed between them in the
        # original file are naturally included in (and replaced by) the
        # spliced-in replace_block, regardless of how many there are.
        search_norm_lines = [ln.strip() for ln in search_block.splitlines() if ln.strip()]
        content_lines = current_content.splitlines()
        content_nonblank = [(i, ln.strip()) for i, ln in enumerate(content_lines) if ln.strip()]
        content_norm_lines = [ln for _, ln in content_nonblank]

        n = len(search_norm_lines)
        matched_starts = (
            [i for i in range(len(content_norm_lines) - n + 1) if content_norm_lines[i:i + n] == search_norm_lines]
            if n > 0 else []
        )

        if not matched_starts:
            raise ValueError(
                f"Anchor matching failed for edit #{idx}: The search block matched 0 times. "
                f"Please ensure whitespace and contents match exactly."
            )
        elif len(matched_starts) > 1:
            raise ValueError(
                f"Anchor matching failed for edit #{idx}: The search block matched {len(matched_starts)} times (must match exactly once). "
                f"Provide more context surrounding the search block."
            )

        match_pos = matched_starts[0]
        raw_start = content_nonblank[match_pos][0]
        raw_end = content_nonblank[match_pos + n - 1][0] + 1
        content_lines[raw_start:raw_end] = replace_block.splitlines()
        current_content = "\n".join(content_lines)

    return current_content


def find_edits_ignoring_reported_line(
    original_content: str, edits: List[Dict[str, str]], filepath: str, error_context: str
) -> List[int]:
    """Layer 1 pre-flight check, added 2026-08-07 in direct response to a real
    live failure (ignite_qpid_person): a targeted retry's own SEARCH block
    spanned the exact line the compiler reported (javac's universal
    file:[line,col] locator - the same source extract_error_source_locations()
    already uses to build _build_error_source_context()'s prompt), yet the
    REPLACE text at that same relative position was byte-identical to SEARCH.
    The edit applied cleanly - no anchor-match failure, Kriya's own plumbing
    worked - but it never actually changed the line the compiler pointed at,
    so the IDENTICAL compile error recurred verbatim on the very next attempt,
    burning a full, expensive compile-and-fail cycle to discover something
    checkable for free beforehand.

    Deliberately narrower than "did the file change anywhere": only flags a
    reported line when SOME edit's own search block already claimed
    responsibility for it (by including it in what it chose to match
    against). An edit whose search block doesn't span the reported line at
    all is a legitimate alternative fix (e.g. correcting a variable's
    generic-typed declaration several lines above instead of its usage site,
    which needs no change at the usage line itself) and is never flagged -
    this catches the model contradicting its OWN stated scope, not every
    possible way of fixing the underlying bug.

    Locates each edit's absolute line offset against the ORIGINAL, pre-edit
    content (not the progressively-edited content apply_anchored_edits()
    itself walks through as it applies edits in sequence), so line numbers
    always line up with what the compiler actually reported regardless of how
    many edits precede this one or whether an earlier edit shifted the line
    count. Returns the reported line number(s) found unaddressed, empty if
    none (including the common case where the error has no locatable line at
    all, or this file isn't the one it names)."""
    locations = extract_error_source_locations(error_context)
    reported_lines = {line for fname, line in locations if fname == os.path.basename(filepath)}
    if not reported_lines:
        return []

    orig_lines = original_content.splitlines()
    ignored: List[int] = []
    for edit in edits:
        search_block = edit.get("search") or ""
        search_lines = search_block.splitlines()
        if not search_lines:
            continue

        norm_search = normalize_whitespace(search_block)
        matched_start = -1
        for i in range(len(orig_lines) - len(search_lines) + 1):
            window = orig_lines[i:i + len(search_lines)]
            if normalize_whitespace("\n".join(window)) == norm_search:
                matched_start = i
                break
        if matched_start == -1:
            # Not this check's job to explain - apply_anchored_edits() itself
            # already raises a precise "matched 0 times" failure for an edit
            # whose search block can't be located at all.
            continue

        replace_lines = (edit.get("replace") or "").splitlines()
        for line_no in reported_lines:
            offset = line_no - 1 - matched_start
            if not (0 <= offset < len(search_lines)):
                continue
            old_line = search_lines[offset].strip()
            new_line = replace_lines[offset].strip() if offset < len(replace_lines) else None
            # Skip trivially short lines (a lone brace, a blank line) - too
            # easy to coincidentally "reappear" unchanged to be a meaningful
            # signal on their own.
            if len(old_line) >= 8 and old_line == new_line and line_no not in ignored:
                ignored.append(line_no)
    return ignored


def _strip_java_comments_and_strings(code: str) -> str:
    """Best-effort removal of Java string/char literals and // and /* */
    comments, replacing each with equal-length whitespace (blank, not deleted,
    so a caller relying on absolute character positions for anything else
    isn't affected). Deliberately NOT a real lexer - doesn't understand Java
    17 text blocks (\"\"\"...\"\"\") or unicode escapes. Good enough for a
    cheap, best-effort structural pre-check (find_structural_corruption
    below); a false negative here just means that check degrades to "found
    nothing wrong" the same as if the check didn't exist, never a false
    rejection of valid code."""
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
            out.append("".join(ch if ch == "\n" else " " for ch in code[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def find_structural_corruption(filepath: str, content: str) -> Optional[str]:
    """Cheap, deterministic, best-effort structural sanity check on a file
    about to be written to the sandbox - catches the exact corruption class
    found live TWICE this session: a duplicated package/class declaration
    from a swallowed FILE CONTENT: block (2026-08-04, see
    _split_fix_analysis_edit's own docstring) and a 23-error "illegal start
    of expression"/"class, interface, enum, or record expected" cascade from
    a fallback model's full-file rewrite (2026-08-08, ignite_qpid_protocol) -
    BEFORE the expensive compile gate spends a full Maven invocation
    discovering the same thing. Neither prior incident was caught by Layer 1
    (find_edits_ignoring_reported_line) or any scaffold, since both checks
    something about a REPORTED error's location/shape - a corrupted file from
    a clean-looking edit or a fresh full-file generation has no prior error
    to check against at all.

    Deliberately NOT a real parser - a full AST/tree-sitter integration would
    catch more, but at real added complexity and a new dependency; this
    targets specifically the cheap, unambiguous "obviously broken" shape a
    human would spot on sight (unbalanced braces, malformed XML), the same
    shape both real incidents actually were. Returns a human-readable
    description of the problem, or None if the file looks structurally sound
    - never a guarantee of correctness (a real compiler remains the source of
    truth for that), only a cheap, low-false-positive earlier tripwire for
    the specific way these two real corruptions were shaped.

    Scoped to .java (brace balance, comment/string-aware so a stray brace
    inside a string literal or comment doesn't produce a false positive) and
    .xml (real well-formedness via the stdlib's own XML parser, not a
    heuristic - zero false positives by construction). Every other extension
    returns None unconditionally - deliberately not extended to Python/Ruby
    (indentation/block-keyword-based, not brace-delimited - a brace count is
    much less informative there) without a second real incident to justify it."""
    if filepath.endswith(".java"):
        stripped = _strip_java_comments_and_strings(content)
        balance = stripped.count("{") - stripped.count("}")
        if balance > 0:
            return f"{balance} unclosed '{{' brace(s) - more opening braces than closing ones."
        if balance < 0:
            return f"{-balance} extra closing '}}' brace(s) - more closing braces than opening ones."
    elif filepath.endswith(".xml"):
        try:
            ET.fromstring(content)
        except ET.ParseError as ex:
            return f"malformed XML: {ex}"
    return None
