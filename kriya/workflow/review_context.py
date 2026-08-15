"""Budget-aware review-prompt batching, shared between the standalone `kriya review`
CLI command and the generation workflow's own Reviewer stage(s) (kriya/workflow/workflow.py).

Extracted from kriya/cli.py's `review` command (2026-08-15 SME review, stage 6, Finding 2)
so both callers get the same protection: with no size control at all, a file (or file
set) exceeding the model's context window gets silently truncated from the FRONT by the
backend, cutting off every "=== File: ... ===" framing marker along with it - the model
receives an unlabeled fragment of raw code with no indication it's even being asked to
review anything, produces a confused non-review response, and the caller has no signal
anything went wrong. Confirmed live as a real, severe bug for the CLI path; the
workflow.py Reviewer stage(s) had no equivalent protection at all until this fix.
"""
from typing import List, Tuple

from kriya.analyzer.analyzer import chunk_file_syntactically


def build_review_batches(files: List[Tuple[str, str]], budget: int) -> Tuple[List[str], List[str]]:
    """Chunks and greedily batches (relpath, content) pairs into review-prompt blobs that
    each fit within `budget` tokens (same context_window * 0.75 convention used throughout
    workflow.py, via the caller's own estimate_tokens() heuristic - not duplicated here to
    avoid an import cycle with kriya.workflow.workflow).

    A single oversized file is truncated in place (kept chunks + an explicit "TRUNCATED"
    marker) rather than ever spanning batches - a file's own review always sees a
    contiguous prefix of itself, never a scattered/reordered view. The common case (a
    handful of small/medium files) produces exactly ONE batch: one combined call, full
    cross-file architectural context for the reviewer. Only degrades to multiple separate,
    independently-reviewed batches (no shared context between them) when the combined
    content genuinely wouldn't fit.

    Returns (batches, truncated_relpaths) - the caller decides how to surface a truncation
    warning (CLI: click.secho; workflow: logger.warning), kept UI-agnostic here.
    """
    from kriya.workflow.workflow import estimate_tokens

    file_blobs: List[Tuple[str, str, int]] = []  # (rel, blob_text, token_estimate)
    truncated_relpaths: List[str] = []
    for rel, content in files:
        chunks = chunk_file_syntactically(content, max_lines=150, overlap=15)
        blob = ""
        for c_idx, chunk_data in enumerate(chunks, 1):
            suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
            blob += f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"

        if estimate_tokens(blob) > budget:
            truncated_relpaths.append(rel)
            kept = ""
            for c_idx, chunk_data in enumerate(chunks, 1):
                suffix = f" (Part {c_idx})" if len(chunks) > 1 else ""
                candidate = kept + f"\n=== File: {rel}{suffix} ===\n{chunk_data['text']}\n"
                if estimate_tokens(candidate) > budget:
                    break
                kept = candidate
            blob = kept + f"\n=== File: {rel} - TRUNCATED: remainder omitted, file exceeds the review token budget ===\n"

        file_blobs.append((rel, blob, estimate_tokens(blob)))

    batches: List[str] = []
    current_batch = ""
    current_tokens = 0
    for _rel, blob, tokens in file_blobs:
        if current_batch and current_tokens + tokens > budget:
            batches.append(current_batch)
            current_batch = ""
            current_tokens = 0
        current_batch += blob
        current_tokens += tokens
    if current_batch:
        batches.append(current_batch)

    return batches, truncated_relpaths
