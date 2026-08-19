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
from typing import Any, Dict, List, Optional


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
    "attribution_rejected" (a targeted/fallback-targeted response explicitly
    reported NO CHANGE NEEDED for every scoped file; its likely_files contains
    any different known file named by the same response's FIX ANALYSIS, or is
    empty to force a full-set widening without rerunning an unchanged target),
    "unaddressed_error_location" (an edit applied cleanly - no anchor-match
    failure - but its own search block spanned the exact line a prior
    compile error reported, then left that line byte-identical in its
    replace text; see find_edits_ignoring_reported_line() in
    kriya/workflow/workflow.py), "structural_corruption" (the edit/content
    applied cleanly but the resulting file is obviously, mechanically broken -
    unbalanced Java braces or malformed XML; see find_structural_corruption()
    in kriya/workflow/workflow.py), "static_rule_violation" (the generated
    code matches a known-bad pattern already documented in an active skill's
    rules - e.g. mixing Apache Ignite's two startup mechanisms - caught by a
    deterministic, no-LLM scan before the compile gate rather than after a
    live failure; see run_static_checks() in kriya/workflow/static_checks.py),
    "diagnosis_mismatch" (an edit/content applied cleanly and (for an edit)
    didn't leave the compiler's reported line unchanged either - unlike
    unaddressed_error_location above, this is about a DIFFERENT, legitimate
    line: the model's own FIX ANALYSIS text quoted specific code it said the
    fix required, but none of that quoted content is actually new anywhere in
    the resulting file - see find_edits_ignoring_own_diagnosis() in
    kriya/workflow/edit_safety.py), "misdirected_edit" (an anchored edit's
    search block matched 0 times against the file it was scoped to, but that
    exact text was found instead inside a DIFFERENT already-written file -
    strong evidence the edit's fix was correct but aimed at the wrong target,
    e.g. a targeted retry scoped by a locator that could only ever name the
    file where a runtime check threw, not the different file whose method
    silently computed the wrong value; see find_misdirected_edit_target() in
    kriya/workflow/edit_safety.py), "pom_semantic_validation" (pom.xml is
    well-formed XML - structural_corruption's own check already passed - but
    `mvn validate` still rejects it as an invalid POM, e.g. the wrong root
    element; caught immediately after pom.xml is written, before any other
    file in the batch is generated, since nothing else can compile without a
    usable POM anyway; see PolymorphicValidator.run_pom_validate() in
    kriya/tools/validate.py), or "general_error" (fallback for a bare,
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
    # Empty for every failure type other than anchored_edit and its sibling
    # misdirected_edit (same underlying anchor-match failure, redirected to a
    # different file once find_misdirected_edit_target() finds where the
    # search block actually belongs).
    attempted_edits: List[Dict[str, str]] = field(default_factory=list)
    attempt: int = 0
    mode: Optional[str] = None
    # Set only on a "compile" failure where autonomy.self_correction_loop_enabled
    # was on and the loop actually ran but did NOT resolve it within its turn
    # budget (kriya/workflow/self_correction.py) - {"turns_used", "transcript",
    # "final_compile_output"}. None whenever the loop never ran at all (flag off,
    # or a non-compile failure type). Closes the same forensics gap
    # attempted_edits closed for anchored edits above: without this, a live run
    # (2026-08-12 eval harness batch) showed the loop making its full 4 turns of
    # genuine diagnostic tool calls, then silently discarding all of it the
    # moment it fell through to the ordinary QualityGateFailure path - exactly
    # the "captured in memory but never persisted" gap this file's own history
    # already names as a repeat mistake worth avoiding.
    self_correction_attempt: Optional[Dict[str, Any]] = None
    # Set by kriya/workflow/retry_strategy.py right after attribute_failure()
    # (kriya/workflow/attribution.py) runs, before this Failure's
    # to_gate_outcome() is called - which of that module's tiers
    # ("locator"/"judge"/"triage"/"full_set") actually produced likely_files
    # for this failure, and how confident it was. None only for a Failure
    # that never went through attribute_failure() at all (shouldn't happen
    # in the normal retry path as of 2026-08-13, kept Optional defensively
    # like self_correction_attempt above). Persisted into gate_outcomes/
    # traces.db for the same reason graded_by was: so a live run can be
    # audited for which attribution tier actually fired, not just trusted.
    attribution_tier: Optional[str] = None
    attribution_confidence: Optional[str] = None
    attribution_reasoning: Optional[str] = None

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
            "self_correction_attempt": self.self_correction_attempt,
            "attribution_tier": self.attribution_tier,
            "attribution_confidence": self.attribution_confidence,
            "attribution_reasoning": self.attribution_reasoning,
        }


class QualityGateFailure(Exception):
    """Raised by every Quality Gate check instead of a bare ValueError, so
    the retry loop's except block can consume a real Failure object
    directly instead of re-deriving one from str(e)."""

    def __init__(self, failure: Failure) -> None:
        self.failure = failure
        super().__init__(failure.message)
