"""Deterministic sanity checks applied to an anchored edit or full-file write before it reaches disk - whitespace-tolerant anchor matching and structural corruption detection. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization). The "which file does this edit concern" checks that used to live here (find_misdirected_edit_target, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line) moved to kriya/workflow/attribution.py on 2026-08-14 - see that module's own docstring taxonomy for why. What's left here is purely mechanical edit-safety: does the edit apply cleanly, and does the resulting file look structurally sound - never "which file"."""

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def atomic_write_file(full_path: str, content: str) -> None:
    """Writes `content` to `full_path` atomically - via a temp file in the SAME
    directory, then os.replace() (atomic on both POSIX and Windows NTFS) - so a
    process killed mid-write can never leave `full_path` truncated/corrupted at
    0 bytes with whatever content was previously there already destroyed.

    Confirmed live, 2026-08-16: a real eval-harness run's `--timeout-per-goal`
    fired mid-write on a full-set fallback-model regeneration - `subprocess.run`'s
    own timeout handling calls Popen.kill() (SIGKILL on POSIX), which is
    uncatchable, so no signal handler or cleanup code could ever run regardless
    of how this were structured. The one thing that CAN survive an uncatchable
    kill is making each individual write itself atomic: plain `open(path, "w")`
    truncates the file to empty BEFORE any new content is written, so a kill in
    that window leaves 0 bytes on disk - exactly what was found post-mortem,
    destroying the one piece of evidence (the file's real content at the moment
    of a still-unexplained recurring compile failure) that would have settled
    root cause. With this, the file on disk is always EITHER the complete old
    content OR the complete new content, never an in-between state, regardless
    of when the kill lands.

    Used by both kriya/workflow/attempt.py (the normal per-attempt write path)
    and kriya/workflow/self_correction.py (the micro-loop's own tool-driven
    patch application) - lives here, not in either caller, since both already
    import from this module and neither should import from the other (attempt.py
    only imports self_correction.py locally, deferred inside a function, to
    avoid exactly that).

    The temp file lives in the same directory as the target (not a shared
    system tmp dir) so os.replace() stays within one filesystem - crossing
    filesystems silently degrades to a non-atomic copy+delete on some
    platforms, defeating the whole point."""
    tmp_path = f"{full_path}.kriya-tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, full_path)


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
    (find_edits_ignoring_reported_line now lives in
    kriya/workflow/attribution.py, moved there 2026-08-14 alongside the
    rest of the "which file" checks - this reference is to the check
    itself, not its current file location.)

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
