"""Deterministic sanity checks applied to an anchored edit or full-file write before it reaches disk - whitespace-tolerant anchor matching and structural corruption detection. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization). The "which file does this edit concern" checks that used to live here (find_misdirected_edit_target, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line) moved to kriya/workflow/attribution.py on 2026-08-14 - see that module's own docstring taxonomy for why. What's left here is purely mechanical edit-safety: does the edit apply cleanly, and does the resulting file look structurally sound - never "which file"."""

# M1 (full modularization): the file write/commit mechanism - atomic_write_file, commit_revision_grounded_file/_batch,
# StagedFileWrite, the commit evidence and the error classes - is owned by kriya/repository/{atomic_write,commit}.py.
# This module keeps the GENERATION edit engine and re-exports the moved public names for the old import path
# (PARTIAL_FACADES, tests/architecture/test_facades.py); Removed in P1. `os` and `tempfile` stay imported only as the
# documented patch points `patch("kriya.workflow.edit_safety.os.replace" / ".os.unlink")` and
# `edit_safety.tempfile.mkstemp`: they patch the shared module objects the moved mechanism uses.
import os  # noqa: F401 - patch point kriya.workflow.edit_safety.os.replace/os.unlink (7 test patches, shared os module)
import re
import tempfile  # noqa: F401 - patch point kriya.workflow.edit_safety.tempfile.mkstemp (test_prd032_chaos_runtime)
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from kriya.repository.atomic_write import _execution_policy as _execution_policy
from kriya.repository.atomic_write import atomic_write_file as atomic_write_file
from kriya.repository.commit import BatchCommitError as BatchCommitError
from kriya.repository.commit import BatchCommitResult as BatchCommitResult
from kriya.repository.commit import CandidateMaterializationError as CandidateMaterializationError
from kriya.repository.commit import CommitEvidence as CommitEvidence
from kriya.repository.commit import CommitState as CommitState
from kriya.repository.commit import FileRevisionConflict as FileRevisionConflict
from kriya.repository.commit import StagedFileWrite as StagedFileWrite
from kriya.repository.commit import UncertainCommitError as UncertainCommitError
from kriya.repository.commit import candidate_digest as candidate_digest
from kriya.repository.commit import candidate_digest_of_entries as candidate_digest_of_entries
from kriya.repository.commit import commit_evidence_dir as commit_evidence_dir
from kriya.repository.commit import commit_revision_grounded_batch as commit_revision_grounded_batch
from kriya.repository.commit import commit_revision_grounded_file as commit_revision_grounded_file
from kriya.repository.commit import commit_state_for_transaction as commit_state_for_transaction
from kriya.repository.commit import content_revision as content_revision
from kriya.repository.commit import find_uncertain_commit_evidence as find_uncertain_commit_evidence
from kriya.repository.commit import list_commit_evidence as list_commit_evidence
from kriya.repository.commit import load_commit_evidence as load_commit_evidence
from kriya.repository.commit import read_file_revision as read_file_revision
from kriya.repository.commit import settle_recovered_commit_evidence as settle_recovered_commit_evidence
from kriya.repository.commit import stage_file_prefix as stage_file_prefix
from kriya.workflow.file_integrity import (
    ANCHOR_NOT_IN_FILE,
    FileIntegrityError,
    anchored_replaces,
    apply_line_block_edits,
)


def normalize_whitespace(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def apply_anchored_edits(original_content: str, edits: List[Dict[str, str]], shown_context: str) -> str:
    """Apply the Developer's SEARCH/REPLACE edits to ``original_content``
    (LF-normalized text) through the one edit engine
    (kriya/workflow/file_integrity.py): complete-line anchors, located in the
    same source, exactly one match each, no overlap, empty SEARCH refused.

    ``shown_context`` (the source shown to the model) additionally refuses a
    SEARCH block that occurs neither there nor in the file
    (ANCHOR_NOT_IN_FILE: fabricated or stale)."""
    if shown_context:
        norm_shown = normalize_whitespace(shown_context)
        norm_current = normalize_whitespace(original_content)
        for index, edit in enumerate(edits, 1):
            norm_search = normalize_whitespace(edit.get("search") or "")
            if norm_search and norm_search not in norm_shown and norm_search not in norm_current:
                raise FileIntegrityError(
                    ANCHOR_NOT_IN_FILE,
                    f"Anchor matching failed for edit #{index}: the search block occurs neither in the "
                    "source shown to the model nor in the current file (fabricated or stale).",
                )
    return apply_line_block_edits(original_content, anchored_replaces(edits)).text


def strip_java_comments_and_strings(code: str) -> str:
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


_strip_java_comments_and_strings = strip_java_comments_and_strings  # private alias kept for existing callers


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
        # A complete compilation unit followed by model commentary is often
        # brace-balanced and therefore invisible to the checks above.  At
        # least one top-level type means the last top-level closing brace is
        # the end of Java source; non-whitespace afterward is payload
        # contamination, not valid source. Comments/strings have already been
        # blanked by the language adapter, avoiding marker-word heuristics.
        if _TOP_LEVEL_TYPE_RE.search(stripped):
            last_close = stripped.rfind("}")
            trailing = stripped[last_close + 1:]
            # Java permits empty top-level declarations (`;`) between/after
            # type declarations; they are source, not model commentary.
            if last_close >= 0 and trailing.replace(";", "").strip():
                return "non-source payload appears after the final top-level type declaration."
    elif filepath.endswith(".xml"):
        try:
            ET.fromstring(content)
        except ET.ParseError as ex:
            return f"malformed XML: {ex}"
    return None


def find_cross_file_type_conflict(
    filepath: str,
    candidate_type_names: List[str],
    type_index: Dict[str, List[str]],
) -> Optional[Tuple[str, List[str]]]:
    """The cross-file sibling of _find_duplicate_top_level_type above - that
    one catches two declarations of the same type WITHIN one file; this one
    catches a new file about to be written whose declared type already
    exists somewhere ELSE in the workspace, found live 2026-08-21
    (protocol_encoder_java): three separate, incompatible `Protocol.java`
    files ended up coexisting in different packages, each missing different
    pieces of the intended API, because nothing noticed a "new" file was
    actually redeclaring an existing type under a different path.

    Deliberately pure data in/out (no DependencyGraph/DB coupling) so it's
    trivially unit-testable with hand-built inputs, matching
    find_whole_response_no_op(edits)'s own style - the caller
    (kriya/workflow/attempt.py) is responsible for building `type_index`
    (kriya/analyzer/graph.py::DependencyGraph.get_class_symbol_locations(),
    layered with anything written earlier in the same still-in-progress
    attempt) and extracting `candidate_type_names`
    (DependencyGraph.extract_class_names()) before calling this.

    Scoped to genuinely NEW files by the caller, never a REPAIR of a file
    that already legitimately owns `filepath` - `filepath` itself is
    excluded from the conflict set here defensively (a file redeclaring its
    OWN class is never a conflict), but the caller should not even reach
    this for an existing-path write in the first place.

    Returns (type_name, [other_paths]) for the FIRST candidate name also
    declared elsewhere, or None if none conflict."""
    for name in candidate_type_names:
        others = [p for p in type_index.get(name, []) if p != filepath]
        if others:
            return name, others
    return None
