"""Deterministic sanity checks applied to an anchored edit or full-file write before it reaches disk - whitespace-tolerant anchor matching and structural corruption detection. Extracted from kriya/workflow/workflow.py (2026-08-11 modularization). The "which file does this edit concern" checks that used to live here (find_misdirected_edit_target, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line) moved to kriya/workflow/attribution.py on 2026-08-14 - see that module's own docstring taxonomy for why. What's left here is purely mechanical edit-safety: does the edit apply cleanly, and does the resulting file look structurally sound - never "which file"."""

import logging
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

logger = logging.getLogger(__name__)

# MA4.5 (control-plane implementation plan) - audit-only, module-level
# since this file has no class/instance to hold it (unlike kriya/core/llm.py's
# LLMClient or kriya/tools/validate.py's PolymorphicValidator). See
# _audit_write_file below.
_execution_policy = ExecutionPolicy()


def _audit_write_file(full_path: str) -> None:
    """MA4.5 - audit-only ExecutionPolicy consultation, mirroring
    kriya/core/llm.py's _audit_llm_network_access (MA4.3) and kriya/tools/
    validate.py's _audit_run_command (MA4.4) exactly: can never affect
    whether atomic_write_file actually writes - any exception raised here is
    caught and logged, never propagated, and the decision is only logged,
    never branched on.

    No workspace_path is available at THIS call site - atomic_write_file()
    remains a pure path-in/bytes-in primitive with no concept of "which
    repo/worktree root is this write happening under", deliberately kept
    that way (see kriya/policy/filesystem.py's module docstring). That
    means kriya/policy/execution.py's ExecutionPolicy._check_filesystem's
    workspace-containment rule still cannot run from HERE - only its
    context-free sensitive-path rule can, so this remains audit-only
    telemetry, not enforcement.

    MA4.16 update: this is no longer the only policy consultation Kriya's
    two real content-write call sites (kriya/workflow/attempt.py,
    kriya/workflow/self_correction.py) go through. Both now call
    kriya/policy/filesystem.py's AuthorizedFileWriter FIRST, with the real
    worktree_path they've always had in scope - that layer REALLY enforces
    containment and a narrow sensitive-path check (raises PolicyDeniedError,
    nothing reaches this function at all on a denial) before
    commit_revision_grounded_file/batch are ever called. This audit-only
    call therefore now only fires for writes that already passed real
    enforcement upstream, plus any other/future caller of
    atomic_write_file directly - it's a second, defense-in-depth signal,
    not the only one anymore."""
    try:
        result = _execution_policy.evaluate(
            ActionRequest(action_type=ActionType.WRITE_FILE, target=full_path)
        )
        logger.debug(
            "MA4 policy audit (not enforced): WRITE_FILE '%s' -> %s (%s)",
            full_path, result.decision.value, result.reason_code,
        )
    except Exception as e:
        logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)


class FileRevisionConflict(ValueError):
    """The file changed after Kriya read it and before the staged write."""


class BatchCommitError(RuntimeError):
    """A staged batch could not be committed or completely rolled back."""


@dataclass(frozen=True)
class StagedFileWrite:
    """One fully materialized candidate file and the revision it was based on.

    ``base_path`` can differ from ``target_path`` when a sandbox file has not
    been materialized yet and generation read the corresponding workspace file.
    The source revision is still guarded before the candidate is committed.
    """

    target_path: str
    content: str
    base_path: str
    expected_base_revision: str


def content_revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read_file_revision(full_path: str) -> str:
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            return content_revision(fh.read())
    except FileNotFoundError:
        return content_revision("")


def commit_revision_grounded_file(full_path: str, content: str, expected_revision: str) -> str:
    """Atomically commit a fully staged file only if its base is unchanged."""
    actual_revision = read_file_revision(full_path)
    if actual_revision != expected_revision:
        raise FileRevisionConflict(
            f"Refusing stale write to '{full_path}': expected revision "
            f"{expected_revision[:12]}, found {actual_revision[:12]}. Re-read the file and retry."
        )
    atomic_write_file(full_path, content)
    return content_revision(content)


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
    _audit_write_file(full_path)
    tmp_path = f"{full_path}.kriya-tmp-{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, full_path)


def _atomic_write_bytes(full_path: str, content: bytes) -> None:
    tmp_path = f"{full_path}.kriya-rollback-{os.getpid()}"
    with open(tmp_path, "wb") as fh:
        fh.write(content)
    os.replace(tmp_path, full_path)


def commit_revision_grounded_batch(writes: Iterable[StagedFileWrite]) -> Dict[str, str]:
    """Commit a candidate set only after every source revision is still current.

    The function performs a full preflight before the first write, repeats each
    guard immediately before its write to narrow race windows, and restores every
    already-written target if a later write fails.  Callers therefore never expose
    a deliberately half-committed model response to compilation or test gates.
    """
    staged = list(writes)
    targets = [item.target_path for item in staged]
    if len(set(targets)) != len(targets):
        raise BatchCommitError("A candidate batch contains duplicate target paths.")

    for item in staged:
        actual = read_file_revision(item.base_path)
        if actual != item.expected_base_revision:
            raise FileRevisionConflict(
                f"Refusing stale batch write to '{item.target_path}': base "
                f"'{item.base_path}' expected revision "
                f"{item.expected_base_revision[:12]}, found {actual[:12]}."
            )

    snapshots: Dict[str, Optional[bytes]] = {}
    committed: List[str] = []
    try:
        for item in staged:
            actual = read_file_revision(item.base_path)
            if actual != item.expected_base_revision:
                raise FileRevisionConflict(
                    f"Refusing stale batch write to '{item.target_path}': base "
                    f"changed during commit."
                )
            try:
                with open(item.target_path, "rb") as fh:
                    snapshots[item.target_path] = fh.read()
            except FileNotFoundError:
                snapshots[item.target_path] = None
            os.makedirs(os.path.dirname(item.target_path), exist_ok=True)
            atomic_write_file(item.target_path, item.content)
            committed.append(item.target_path)
    except Exception as commit_error:
        rollback_errors = []
        for target_path in reversed(committed):
            try:
                original = snapshots[target_path]
                if original is None:
                    os.unlink(target_path)
                else:
                    _atomic_write_bytes(target_path, original)
            except Exception as rollback_error:  # pragma: no cover - rare OS failure
                rollback_errors.append(f"{target_path}: {rollback_error}")
        if rollback_errors:
            raise BatchCommitError(
                f"Candidate commit failed ({commit_error}); rollback also failed for: "
                + "; ".join(rollback_errors)
            ) from commit_error
        raise

    return {
        item.target_path: content_revision(item.content)
        for item in staged
    }


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

        # Found live, 2026-08-17, digging into a corpus-wide survey of
        # eval-harness runs: 14 "elided in the skeletonized context"
        # failures across the whole run history, several from a genuinely
        # legitimate shape this check never accounted for. shown_context is
        # a fixed snapshot, passed in once and never updated across loop
        # iterations - but current_content DOES evolve as earlier edits in
        # this SAME response get applied (the .replace() call below).
        # Reproduced directly: a two-step chained edit (edit #1 adds a
        # `helper();` call, edit #2 wants to comment on that exact new
        # line) is completely valid and internally consistent, but edit
        # #2's search text was never part of the ORIGINAL file the model
        # was shown - only of what edit #1 itself just introduced - so the
        # old check (comparing only against the static shown_context)
        # wrongly rejected it as "not shown to the model," when the model
        # in fact introduced that exact text itself, one edit earlier in
        # the same response. Grounding a search block against EITHER the
        # original shown context OR the file's current (possibly
        # already-edited) state closes this gap while still rejecting a
        # genuinely fabricated/hallucinated search block, which by
        # definition matches neither.
        if shown_context:
            norm_shown = normalize_whitespace(shown_context)
            norm_current = normalize_whitespace(current_content)
            if norm_search not in norm_shown and norm_search not in norm_current:
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
