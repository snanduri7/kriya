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


def _search_block_matches(content: str, search_block: str) -> bool:
    """Whitespace-tolerant "does this search block exist anywhere in this
    content at all" check - shares apply_anchored_edits()'s own two-tier
    matching philosophy (an exact substring match first, then a blank-line-
    tolerant, stripped-line subsequence match) but answers a simpler
    question: containment, not uniqueness or splicing (no position is ever
    needed by this function's only caller, find_misdirected_edit_target()
    below - it only needs a yes/no)."""
    if not search_block:
        return False
    if search_block in content:
        return True
    search_norm_lines = [ln.strip() for ln in search_block.splitlines() if ln.strip()]
    if not search_norm_lines:
        return False
    content_norm_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    n = len(search_norm_lines)
    return any(
        content_norm_lines[i:i + n] == search_norm_lines
        for i in range(len(content_norm_lines) - n + 1)
    )


def find_misdirected_edit_target(
    edits: List[Dict[str, str]], orig_text: str, other_files: Dict[str, str]
) -> Optional[str]:
    """Called from attempt.py's `except ValueError as anchor_ex:` handler, right
    after apply_anchored_edits() has ALREADY raised a "matched 0 times" failure -
    this function asks one more question before accepting that failure at face
    value: does the search block that failed to match its intended file actually
    exist, verbatim or near-verbatim, inside a DIFFERENT known file instead? If
    so, that's direct, ground-truth evidence the model's edit was aimed at the
    wrong file, not that its content was simply wrong.

    Found live, 2026-08-14 (spikes/eval_harness/runs/a-3, ignite_qpid_protocol):
    a runtime verification failure's stack trace -
    `at com.example.ProtocolApp.testProtocolLayer(ProtocolApp.java:73)` - has
    exactly one locatable frame, because the failure is an EXPLICIT
    `if (!protocol.equals(decoded)) throw new RuntimeException(...)` check
    written directly in ProtocolApp.java (the exact pattern Kriya's own
    VERIFICATION_CONTRACT_HEADER prompts every goal to write). extract_implicated_
    files() correctly scoped the resulting targeted retry to ProtocolApp.java -
    the only file with a locator - but the REAL bug was silently-wrong data
    returned from ProtocolParser.encode() (a local `dataLength` variable
    computed from body.length that was never written back to the encoded
    header the same way on every call path), a method that doesn't itself
    throw and so never appears in the stack trace at all. The model's own FIX
    ANALYSIS text was textbook-correct ("...in the encode method, we're not
    using the protocol.dataLength field - instead we're using body.length
    directly...") but, constrained to editing only ProtocolApp.java, it wrote a
    SEARCH block that was actually ProtocolParser.encode()'s own body - which
    can never match ProtocolApp.java's real content, guaranteeing "matched 0
    times" and burning the whole retry attempt (confirmed directly from the
    real captured kriya.log and worktree file contents, not inferred).

    This is a different trigger of the same general shape the 2026-08-10
    _NO_CHANGE_NEEDED_RE incident (kriya/agents/agent.py) closed for a stack
    trace that names TWO files (one needing no change) - here the stack trace
    only ever names ONE file, because an explicit-throw runtime check
    structurally can't produce a locator for whatever file's logic actually
    computed the wrong value. No amount of improving extract_implicated_files()
    itself can fix this: the file that needs the fix is never named anywhere in
    the error text, precisely because it fails silently rather than throwing.
    The one piece of ground truth Kriya already has at this exact moment - the
    edit's own search-block bytes, and the real on-disk content of every OTHER
    file already written this run - is what this function checks instead,
    sidestepping the need to name the right file from error text at all.

    Deliberately generic: no Java/Ignite/protocol-specific logic anywhere here,
    just whitespace-tolerant text containment across two known texts, reusing
    apply_anchored_edits()'s own tolerant-match philosophy via
    _search_block_matches() above. Only considers an edit whose search block did
    NOT already match its own intended file (orig_text) - an edit that matched
    fine is never the culprit, regardless of what else it might coincidentally
    also match elsewhere. Returns the first other file whose content contains a
    failing edit's search block, or None if no failing edit's search block is
    found anywhere else either (the caller falls through to the existing,
    unchanged generic "matched 0 times" failure in that case - this is purely
    additive, never a regression on the prior behavior)."""
    for edit in edits:
        search_block = edit.get("search") or ""
        if not search_block or _search_block_matches(orig_text, search_block):
            continue
        for other_path, other_content in other_files.items():
            if _search_block_matches(other_content, search_block):
                return other_path
    return None


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


_ANALYSIS_QUOTED_SPAN_RE = re.compile(r"`([^`]+)`")


def find_edits_ignoring_own_diagnosis(
    analysis: Optional[str],
    edits: Optional[List[Dict[str, str]]],
    content: Optional[str],
    orig_text: str,
) -> Optional[str]:
    """Layer 2 pre-flight check, sibling to find_edits_ignoring_reported_line() above -
    that check catches an edit that left the COMPILER's own reported line unchanged,
    but deliberately does NOT flag an edit at a different line, since its own docstring
    correctly treats that as a legitimate alternative fix. This one closes the gap that
    leaves open: found live, 2026-08-13 (spikes/eval_harness/runs/
    attribution-fix-validation-3) - a real compile error (`incompatible types:
    java.lang.Object cannot be converted to com.example.Protocol`, from an untyped
    `var cache = ignite.getOrCreateCache(...)` declaration) recurred VERBATIM across 3
    consecutive targeted retries. Each retry's own FIX ANALYSIS text was textbook-correct
    every time ("...requires explicitly declaring the cache with proper generic type
    parameters `IgniteCache<Integer, Protocol>` instead of using `var`"), confirmed via
    kriya.log - yet the worktree file still showed the identical untyped declaration
    unchanged after all 3 rounds, confirmed directly against the file. No anchor-match
    failure occurred (edits applied cleanly) and no worktree reset happened between
    retries - the edit itself simply never implemented what its own analysis said, at
    ANY line, not just the compiler-reported ones.

    Detection, deliberately avoiding an old-vs-new quote ambiguity: a diagnosis often
    quotes BOTH the broken pattern and the prescribed fix in the same sentence (as
    above - `IgniteCache<Integer, Protocol>` the fix, `var` the problem, both
    backtick-quoted). Naively checking "does any quoted span appear anywhere in the new
    code" is ambiguous - the OLD quoted pattern trivially still appears in old code too.
    Resolved precisely: a quoted span counts as evidence the fix was made if EITHER (a)
    it appears in the NEW content but did NOT already appear in the OLD content (the
    original signal - a quote describing the problem, already present in the old code,
    is naturally excluded without needing to guess which quoted span is "the fix" from
    sentence structure), OR (b) it appears in the new content AND the quote IS the
    entire old (search) text, not merely present somewhere within a larger old text (a
    WRAP/EXTEND edit, e.g. `X` -> `print(X)` - the quoted span legitimately stays
    byte-identical because the fix adds context AROUND the whole thing being replaced,
    so signal (a) alone always misses this shape). Found live, 2026-08-13
    (python_greeter, reproduced across two separate eval-harness runs): this check
    itself was flagging a genuinely correct fix ("wrap the bare `[VERIFICATION] PASS`
    marker in a print() call") as a mismatch on every single retry, for both
    qwen3-coder:30b and glm-4.7-flash, because the quoted marker text is unavoidably
    identical before and after a correct wrap - not a model execution failure at all,
    this check's own false positive was blocking a fix that was likely already correct.

    Signal (b) is deliberately scoped to `q == old_text.strip()`, not the broader "the
    whole old_text survives as a substring of new_text" - that broader version was
    tried first and found to reintroduce a real regression: an edit that appends an
    unrelated trailing comment to an unfixed line (`X;` -> `X; // unchanged`) trivially
    makes the ENTIRE old line a substring of the new one, which would incorrectly clear
    every quote in the analysis, not just one actually being wrapped - confirmed via
    test_find_edits_ignoring_own_diagnosis_regression_validation_3, which still expects
    that shape to be flagged. Requiring the quote to equal the whole search block ties
    the signal specifically to "this edit's target WAS just this quoted text," which
    the append-a-comment case doesn't satisfy (its search block is a full statement, not
    the bare quoted fragment) but the print-wrap case does exactly.

    Returns a descriptive mismatch reason (naming the specific quoted content that never
    appeared) when analysis has at least one backtick-quoted span and NONE of them are
    new (or a wrapped whole-search-block) in the resulting content; None when analysis
    has no quoted spans at all (nothing specific enough to check against - never flag a
    prose-only diagnosis), or when at least one quoted span satisfies either signal."""
    if not analysis:
        return None
    quoted = [q for q in _ANALYSIS_QUOTED_SPAN_RE.findall(analysis) if len(q.strip()) >= 2]
    if not quoted:
        return None

    if edits:
        old_text = "\n".join(e.get("search") or "" for e in edits)
        new_text = "\n".join(e.get("replace") or "" for e in edits)
    else:
        old_text = orig_text
        new_text = content or ""

    for q in quoted:
        # Signal (b) is deliberately scoped to "the quote IS the entire old
        # text" (not just present somewhere within a larger old_text) - a
        # broader "old_text survives anywhere inside new_text" version was
        # tried and found to also let a genuine mismatch through: appending
        # an unrelated trailing comment to an unfixed line trivially makes
        # the whole old line a substring of the new one too, for every
        # quote in the analysis, not just the one actually being wrapped.
        # Requiring q == old_text.strip() ties the signal to the specific
        # quote whose surrounding statement was JUST that quote, which is
        # exactly the real wrap shape (search: "X", replace: "print(X)")
        # and excludes a multi-token old_text merely containing q somewhere.
        if q in new_text and (q not in old_text or q == old_text.strip()):
            return None

    quoted_desc = ", ".join(f"`{q}`" for q in quoted)
    return (
        f"your analysis said \"{analysis.strip()}\" - specifically naming {quoted_desc} - "
        f"but none of that appears anywhere new in your proposed change"
    )


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


_TOP_LEVEL_TYPE_RE = re.compile(r'\b(?:class|interface|enum)\s+(\w+)')


def _find_duplicate_top_level_type(stripped: str) -> Optional[str]:
    """Scans (already comment/string-stripped) Java source for two top-level
    class/interface/enum declarations sharing the same name - the variant a
    plain brace-balance count can never catch, since a *complete*, self-
    closed duplicate type is brace-balanced by construction (2026-08-12 SME
    review; see find_structural_corruption's own docstring for the real
    incident this closes). "Top-level" is determined by brace depth at the
    keyword's position (0 = not nested inside another type's body) so a
    legitimate, differently-named inner/nested class is never flagged -
    only a second declaration of a name already seen at depth 0 is."""
    seen = set()
    for m in _TOP_LEVEL_TYPE_RE.finditer(stripped):
        depth = stripped.count("{", 0, m.start()) - stripped.count("}", 0, m.start())
        if depth != 0:
            continue
        name = m.group(1)
        if name in seen:
            return name
        seen.add(name)
    return None


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

    Deliberately NOT a full AST/tree-sitter integration for every language -
    that would catch more but at real added complexity and (for Java/Ruby) a
    new dependency Kriya has repeatedly avoided elsewhere (e.g. the hand-
    rolled LSP client). This targets specifically the cheap, unambiguous
    "obviously broken" shape a human would spot on sight (unbalanced braces,
    a duplicated declaration, malformed XML), the same shape every real
    corruption incident actually was. Returns a human-readable description
    of the problem, or None if the file looks structurally sound - never a
    guarantee of correctness (a real compiler/interpreter remains the source
    of truth for that), only a cheap, low-false-positive earlier tripwire.

    Scoped to .java (brace balance, comment/string-aware so a stray brace
    inside a string literal or comment doesn't produce a false positive,
    PLUS a duplicate-top-level-type check for the one documented gap a brace
    count alone can't catch - see _find_duplicate_top_level_type) and .xml
    (real well-formedness via the stdlib's own XML parser, not a heuristic -
    zero false positives by construction). Every other extension returns
    None unconditionally.

    Deliberately NOT extended to .py, despite a real stdlib ast.parse()
    check being zero-cost and zero-new-dependency (tried during the
    2026-08-12 SME review, reverted after live test-writing surfaced why):
    unlike Java (where this function's brace check only intercepts ONE
    narrow shape, leaving most javac syntax errors to reach the real compile
    gate - which DOES show the model its previous broken content in the
    retry prompt, since that gate only runs on already-written files),
    Python's own "compile check" (kriya/tools/validate.py) is JUST
    `compile(source, f, "exec")` - a pure syntax check with 100% overlap
    with what ast.parse() would catch. Adding it here would silently
    redirect EVERY Python syntax error away from the compile gate's richer,
    content-shown targeted retry (this function runs pre-write, so a
    rejected file is deliberately never added to all_files_written, and
    _build_targeted_retry_prompt only shows previous content for files it
    finds on disk) into this function's leaner error-text-only failure -
    for zero cost savings, since compile() is already just as free as
    ast.parse(), with no expensive gate being avoided the way a real Maven
    invocation is for Java. Not extended to Ruby either, for the original
    reason: indentation/block-keyword-based, not brace-delimited, and no
    stdlib parser available - a brace count is much less informative there."""
    if filepath.endswith(".java"):
        stripped = _strip_java_comments_and_strings(content)
        balance = stripped.count("{") - stripped.count("}")
        if balance > 0:
            return f"{balance} unclosed '{{' brace(s) - more opening braces than closing ones."
        if balance < 0:
            return f"{-balance} extra closing '}}' brace(s) - more closing braces than opening ones."
        duplicate = _find_duplicate_top_level_type(stripped)
        if duplicate:
            return f"duplicate top-level type declaration: '{duplicate}' is declared more than once."
    elif filepath.endswith(".xml"):
        try:
            ET.fromstring(content)
        except ET.ParseError as ex:
            return f"malformed XML: {ex}"
    return None
