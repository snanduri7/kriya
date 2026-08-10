"""Unified failure-grounding shape for the Developer + Quality Gates retry
loop in kriya/workflow/workflow.py.

Before this module existed, each failure source (compile error, test
failure, Runtime Verification grade, anchored-edit anchor-match failure) had
its own hand-built retry-scoping path with asymmetric capabilities. Every
source funneled through one shared `except Exception as e:` block, but only
after being collapsed to `str(e)` - so structure that existed at the moment
of failure (RunVerifierAgent.grade()'s already-validated `likely_files`, an
anchored-edit's known filepath, a compiler's real file:line coordinates) was
either re-derived afterward by regex, or - for the anchored-edit case -
never captured at all. `IncompleteGenerationError` was the one exception
that already did this right (a real `missing_files` attribute, no string
round-trip); `Failure`/`QualityGateFailure` generalize that pattern to every
other source instead of inventing a new one.

Kept as its own module (not folded into workflow.py, already long/stateful
by design) since this is a pure data shape, trivial to unit-test in
isolation from the retry loop itself.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FileLocation:
    filepath: str
    line: Optional[int] = None
    col: Optional[int] = None


@dataclass
class Failure:
    """One shape every failure source in the retry loop populates. `type` is
    a single canonical vocabulary (replaces the two inconsistent taxonomies
    found in gate_outcomes' try-block dict literals and the except block's
    separate fail_type derivation): "compile", "test", "run_verification",
    "run_verification_hung" (a self-terminating entrypoint whose captured
    output shows the goal WAS achieved, but the process never exited on its
    own and had to be killed after timing out - a resource-lifecycle defect,
    not a wrong-behavior one; see the non-binary grading in
    kriya/workflow/workflow.py's run-verification timeout branch),
    "regression_test", "incomplete_generation", "anchored_edit",
    "unaddressed_error_location" (an edit applied cleanly - no anchor-match
    failure - but its own search block spanned the exact line a prior
    compile error reported, then left that line byte-identical in its
    replace text; see find_edits_ignoring_reported_line() in
    kriya/workflow/workflow.py), "structural_corruption" (the edit/content
    applied cleanly but the resulting file is obviously, mechanically broken -
    unbalanced Java braces or malformed XML; see find_structural_corruption()
    in kriya/workflow/workflow.py), or "general_error" (fallback for a bare,
    non-QualityGateFailure Exception).
    """
    type: str
    message: str
    raw_output: str = ""
    file_locations: List[FileLocation] = field(default_factory=list)
    likely_files: List[str] = field(default_factory=list)
    diagnostics: Optional[dict] = None
    # Full content of every implicated file, captured from the worktree at
    # the moment of failure (still on disk - the next attempt hasn't
    # overwritten it yet). Closes a real forensics gap found live
    # (2026-08-04 eval harness batch): a failed attempt's actual generated
    # content was otherwise never persisted anywhere, only the tool's error
    # text - impossible to root-cause after the fact. Now persisted into
    # gate_outcomes/traces.db by to_gate_outcome() below (2026-08-07) - a
    # real anchor-match failure (kriya-protocol-parser-app) turned out to
    # be undiagnosable after the fact precisely because this was captured
    # in memory but silently dropped before persistence, the exact gap
    # this field's own docstring used to flag as a deferred follow-up.
    failed_content: Dict[str, str] = field(default_factory=dict)
    # The exact search/replace text an anchored edit attempted, captured at
    # the anchored_edit raise site alongside failed_content (the original
    # content the search block was supposed to match against) - together
    # they're exactly what's needed to see WHY a "matched 0 times" anchor
    # failure happened (whitespace drift, a gutter/fence artifact that
    # slipped past sanitize_generated_content, a stale search target)
    # without needing to reproduce the failure with debug logging enabled.
    # Empty for every failure type other than anchored_edit.
    attempted_edits: List[Dict[str, str]] = field(default_factory=list)
    attempt: int = 0
    mode: Optional[str] = None

    def to_gate_outcome(self) -> dict:
        """The shape kriya/workflow/workflow.py's gate_outcomes list expects -
        one shared constructor instead of 5 independently hand-typed dict
        literals (compile/targeted_test/test/run_verification/regression_test),
        each of which previously used a different type vocabulary than the
        except block's own fail_type derivation."""
        return {
            "attempt": self.attempt,
            "type": self.type,
            "success": False,
            "output": self.raw_output or self.message,
            "mode": self.mode,
            "likely_files": list(self.likely_files),
            "file_locations": [
                {"filepath": loc.filepath, "line": loc.line, "col": loc.col}
                for loc in self.file_locations
            ],
            "failed_content": dict(self.failed_content),
            "attempted_edits": list(self.attempted_edits),
        }


class QualityGateFailure(Exception):
    """Raised by every Quality Gate check instead of a bare ValueError, so
    the retry loop's except block can consume a real Failure object
    directly instead of re-deriving one from str(e)."""

    def __init__(self, failure: Failure) -> None:
        self.failure = failure
        super().__init__(failure.message)
