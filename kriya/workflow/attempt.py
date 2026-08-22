"""One retry-iteration's happy-path body for the Developer + Quality Gates
loop - Developer invocation, file writes, the completeness check, compile/
test gates, and the Runtime Verification gate. Extracted verbatim from
kriya/workflow/workflow.py's run_generation_workflow() (2026-08-11,
Opportunity 2 Slice 2): mutates the passed-in GenerationState in place and
either returns normally (Quality Gates passed) or raises the same exceptions
the inline code always did - QualityGateFailure or IncompleteGenerationError
- letting them propagate to the caller's own except block exactly as before.
Everything after Quality Gates pass (checkpoint save, human approval, apply-
to-workspace, lesson extraction, the full regression suite) deliberately
stays in workflow.py - out of scope for this slice.
"""
import asyncio
import logging
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from kriya.agents.agent import DeveloperAgent
from kriya.core.kernel import Kernel
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    apply_anchored_edits,
    commit_revision_grounded_batch,
    content_revision,
    find_cross_file_type_conflict,
    find_structural_corruption,
    read_file_revision,
)
from kriya.workflow.dependency_invalidation import (
    dependent_closure,
    invalidate_validated_revisions,
)
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import _build_quality_gate_failure, _capture_failed_content, build_cross_package_mismatch_message, find_cross_package_symbol_mismatch, find_locator_files_outside_known_scope
from kriya.workflow.file_resolution import IncompleteGenerationError, _resolve_run_command, correct_exec_main_class_property, downgrade_ungrounded_goal_explicit_commands, ensure_maven_covers_nonconventional_java_files, extract_jvm_module_flags, extract_planner_code_blocks, extract_target_test, find_missing_expected_files, find_runnable_test_files, ground_java_entrypoint_in_no_build_file_projects, normalize_written_filepath, strip_package_declaration_matching_source_root
from kriya.workflow.context_budget import (
    _reserve_graph_context_budget,
    _reserve_sibling_content_budget,
    build_code_context,
)
from kriya.workflow.retry_prompts import _build_full_set_retry_prompt, _build_missing_files_retry_prompt, _build_targeted_retry_prompt
from kriya.workflow.retry_package import RetryPackage, build_retry_package
from kriya.workflow.retry_policy import RetryAction, decide_retry_action
from kriya.workflow.skill_extraction import _skill_verification_context
from kriya.workflow.state import GenerationState
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.operations import (
    CodeOperation,
    all_results_are_no_change,
    operation_for_attempt,
    operation_for_file,
    validate_operation_result,
)
from kriya.workflow.static_checks import run_static_checks
from kriya.workflow.attribution import extract_self_diagnosed_files, find_edits_ignoring_own_diagnosis, find_edits_ignoring_reported_line, find_misdirected_edit_target, find_whole_response_no_op, resolve_fallback_model
from kriya.workflow.banners import log_quality_gate_banner
from kriya.workflow.acceptance import (
    goal_explicitly_requires_tests,
    output_confirms_nonzero_test_execution,
    run_command_targets_missing_entrypoint,
)
from kriya.workflow.toolchain import _check_java_toolchain_mismatch, _pin_exec_plugin_executable_to_resolved_jdk, _resolve_java_home_override, _strip_jdk_incompatible_jvm_flags
from kriya.workflow.verification_contract import extract_contract_verdict, pass_verdict_is_grounded
from kriya.workflow.worktree import clean_untracked_files_since, snapshot_untracked_files

logger = logging.getLogger(__name__)


def _target_exists(ctx: "AttemptContext", filepath: str) -> bool:
    return os.path.exists(os.path.join(ctx.worktree_path, filepath)) or os.path.exists(
        os.path.join(ctx.workspace_path, filepath)
    )


def _operation_map(
    ctx: "AttemptContext", filepaths: List[str], attempt_operation: CodeOperation,
) -> Dict[str, CodeOperation]:
    return {
        filepath: operation_for_file(
            attempt_operation, file_exists=_target_exists(ctx, filepath),
        )
        for filepath in filepaths
    }


def _preserved_authoritative_locator_files(
    state: GenerationState, target_files: List[str], self_diagnosed: List[str],
) -> List[str]:
    """Return current targets backed by the preceding authoritative locator.

    Developer analysis is advisory and cannot erase deterministic evidence.
    A different file explicitly named by current-turn analysis is allowed to
    redirect (root cause and surfacing location may differ), so preservation
    applies only when there is no such alternate.  Intersecting with this
    attempt's actual targets prevents stale locator evidence from widening or
    redirecting a later, unrelated repair.
    """
    prior_attribution = state.last_attribution
    prior_failure = state.last_failure
    if (
        self_diagnosed
        or state.last_attempt_mode not in ("targeted", "fallback_targeted")
        or prior_attribution is None
        or getattr(prior_attribution, "tier", None) != "locator"
        or prior_failure is None
        or getattr(prior_failure, "authority", None) != "authoritative"
    ):
        return []
    return [
        filepath for filepath in getattr(prior_attribution, "files", [])
        if filepath in target_files
    ]


def _request_fallback_for_rejected_authoritative_target(
    state: GenerationState, ctx: "AttemptContext", preserved_files: List[str],
) -> None:
    if (
        preserved_files
        and state.last_attempt_mode == "targeted"
        and ctx.chain
        and not state.budgets.fallback_targeted_attempted
    ):
        state.budgets.fallback_targeted_requested = True


def _preserved_attribution_diagnostics(preserved_files: List[str]) -> Optional[dict]:
    if not preserved_files:
        return None
    return {
        "preserved_prior_attribution": {
            "tier": "locator",
            "files": list(preserved_files),
        }
    }


def _retry_package_for_attempt(
    state: GenerationState,
    ctx: "AttemptContext",
    *,
    target_files: Optional[List[str]],
    context_window: int,
) -> Optional[RetryPackage]:
    if state.last_failure is None:
        return None
    # Reserve at most a bounded fraction of the model window for retry source
    # evidence.  Four characters/token is only an estimate; 1.5 chars per
    # advertised token deliberately leaves ample room for goal, plan, design,
    # skills, instructions, and output on local models with smaller windows.
    max_chars = max(6000, min(48000, int(context_window * 1.5)))
    return build_retry_package(
        failure=state.last_failure,
        worktree_path=ctx.worktree_path,
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a retry package's content candidates should include files earlier
        # milestones wrote too, not just this attempt's own writes.
        all_files=sorted(set(state.all_files_written) | set(ctx.established_files)),
        target_files=target_files,
        source_context=state.last_error_source_context,
        max_chars=max_chars,
        # A file's entry here is only trustworthy while unchanged since the
        # post-compile snapshot that produced it - true here, since nothing
        # between that snapshot and a later gate's failure writes to the
        # worktree. Files outside that snapshot (e.g. genuinely still-missing
        # ones) simply fall back to a fresh hash inside project_implementation_source.
        known_revisions=state.validated_file_revisions,
        advisory_context=(
            state.error_context[
                state.error_context.index("=== Reference material found"):
            ]
            if "\n\n=== Reference material found" in state.error_context
            else ""
        ),
    )


def _estimated_generation_seconds(
    state: GenerationState, *, file_count: int, configured_per_file: int,
    active_model: Optional[str] = None,
) -> float:
    observed = [
        timing["duration_seconds"] / max(1, timing["file_count"])
        for timing in state.generation_timings
        if timing.get("duration_seconds", 0) > 0 and timing.get("file_count", 0) > 0
        and (active_model is None or timing.get("model") == active_model)
    ]
    per_file = statistics.median(observed) if observed else configured_per_file
    return per_file * max(1, file_count)


def _ensure_generation_time_budget(
    state: GenerationState, ctx: "AttemptContext", *, file_count: int,
    active_model: Optional[str] = None,
) -> None:
    autonomy = ctx.kernel.config.autonomy
    budget = autonomy.generation_time_budget_seconds
    if budget is None:
        return
    elapsed = time.monotonic() - state.generation_started_monotonic
    remaining = max(0.0, budget - elapsed)
    estimate = _estimated_generation_seconds(
        state,
        file_count=file_count,
        configured_per_file=autonomy.generation_seconds_per_file_estimate,
        active_model=active_model,
    )
    required = estimate + autonomy.generation_gate_reserve_seconds
    if remaining < required:
        failure = Failure(
            type="time_budget_exhausted",
            source="orchestrator",
            message=(
                "GENERATION TIME BUDGET EXHAUSTED: refusing to start a "
                f"{file_count}-file generation pass with {remaining:.1f}s remaining; "
                f"estimated generation plus gate reserve requires {required:.1f}s."
            ),
            raw_output=(
                f"remaining_seconds={remaining:.1f}; estimated_generation_seconds="
                f"{estimate:.1f}; gate_reserve_seconds="
                f"{autonomy.generation_gate_reserve_seconds}"
            ),
            attempt=state.attempt_number,
        )
        raise QualityGateFailure(failure)


async def _run_developer_generation(
    state: GenerationState, ctx: "AttemptContext", **kwargs,
) -> List[Dict[str, str]]:
    targets = kwargs.get("known_target_files")
    file_count = len(targets or ctx.expected_files_upfront or state.all_files_written or [None])
    active_model = kwargs.get("model_override") or ctx.kernel.config.llm.model
    _ensure_generation_time_budget(
        state, ctx, file_count=file_count, active_model=active_model,
    )
    started = time.monotonic()
    succeeded = False
    try:
        result = await ctx.developer.run_generation(**kwargs)
        succeeded = True
        return result
    finally:
        duration = time.monotonic() - started
        state.generation_timings.append({
            "duration_seconds": duration,
            "file_count": file_count,
            "succeeded": succeeded,
            "model": active_model,
        })
        state.record_event(RunEvent(
            kind="generation.completed" if succeeded else "generation.failed",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Developer generation {'completed' if succeeded else 'failed'} "
                f"for {file_count} file(s) in {duration:.2f}s."
            ),
            details={
                "duration_seconds": duration,
                "file_count": file_count,
                "model": active_model,
            },
        ))


@dataclass
class AttemptContext:
    """The retry loop's read-only, loop-invariant closure captures - built
    once before the while loop starts (nothing in this range is reassigned
    across iterations), passed into run_attempt() (this module) and
    kriya.workflow.retry_strategy.handle_attempt_failure() alike - both
    operate on the same attempt/failure cycle, just its two different
    halves, so they share one context object rather than each inventing
    their own overlapping one."""

    goal: str
    plan: str
    design: str
    workspace_path: str
    worktree_path: str
    architect_files: List[str]
    resume_state: Optional[Dict[str, Any]]
    run_id: str
    skills_prompt: str
    learned_rag_context: str
    matched_files: Any
    related_files: Any
    ecosystem_invariant_block: str
    resource_lifecycle_block: str
    verification_contract_block: str
    required_files_prompt_block: str
    required_dependencies_prompt_block: str
    expected_files_upfront: List[str]
    architect_basename_to_path: Dict[str, str]
    chain: list
    targeted_max_retries: int
    stream_callback: Optional[Callable[[str, str], None]]
    approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]]
    active_skills: List[str]
    active_skill_rules_snapshot: Dict[str, Any]
    developer: DeveloperAgent
    run_verifier: Any
    spec_compliance: Any
    skill_engine: Any
    kernel: Kernel
    # web_lookup_query_callback/approve_web_lookup are only read by
    # handle_attempt_failure(), not run_attempt() itself; max_retries is read
    # by both (run_attempt()'s own decide_retry_action() mode-selection call,
    # and handle_attempt_failure()'s decide_for_state() continue/stop check) -
    # kept on the same context object anyway (see the class docstring) rather
    # than a second, mostly-overlapping dataclass.
    max_retries: int
    web_lookup_query_callback: Optional[Callable[[List[str], str], Any]]
    # A bound method (WorkflowEngine._approve_web_lookup), not a free
    # function - already carries its own `self` reference, so it's just
    # another callable from this module's perspective.
    approve_web_lookup: Callable[..., Any]
    # path -> direct manifest dependencies, in generation order. Default keeps
    # isolated tests and old checkpoints backward compatible.
    generation_dependencies: Dict[str, List[str]] = field(default_factory=dict)
    # Files known to exist from OUTSIDE this attempt's own generation - for a
    # milestone run, every file an earlier, already-completed milestone wrote
    # (kriya/workflow/milestones.py's MilestoneRunState.established_file_context
    # keys). Deliberately NOT merged into state.all_files_written itself - that
    # field is read by ~30 call sites across this module/workflow.py (compile/
    # test scope, file-count budgeting, final "files written" reporting,
    # missing-files detection...) where "written by THIS attempt" is the
    # correct, load-bearing meaning; widening it would be wrong for most of
    # those. This field exists ONLY to widen the "known files" candidate set
    # self-diagnosis/attribution matching uses (see its two read sites: this
    # module's extract_self_diagnosed_files() call, and retry_strategy.py's
    # attribute_failure() call) - found live, 2026-08-21 (ignite_qpid_protocol,
    # milestone 2/4): the Developer's own FIX ANALYSIS correctly, repeatedly
    # said "the fix requires adding public getter methods to the Protocol
    # class" (milestone 1's file), but Protocol.java was never a candidate for
    # redirect at all, since milestone 2's OWN state.all_files_written only
    # ever contains files ITS OWN attempts wrote (ProtocolParser.java) - a
    # correct diagnosis had structurally nowhere to go, and the retry loop
    # burned its full budget regenerating only ProtocolParser.java, 8 attempts
    # straight. Default empty list keeps plain (non-milestone) generate/fix
    # calls, and any old checkpoint, unaffected.
    established_files: List[str] = field(default_factory=list)


def _extract_grounded_contract_verdict(
    output: str, worktree_path: str, files_written: List[str]
) -> Optional[Dict[str, Any]]:
    """Wraps extract_contract_verdict() with the independent grounding check
    from verification_contract.py::pass_verdict_is_grounded() - see that
    function's own docstring for the full reasoning (independent brutal
    review finding #4, 2026-08-15: a PASS marker is self-reported by the
    same model that wrote the implementation, with nothing else checking it
    really branches on anything). A single shared helper, not three copies
    of the same logic - used identically at all three of run_attempt()'s
    run_res-outcome branches (clean run / timed out / plain nonzero exit) so
    a PASS verdict's trust is checked consistently everywhere, not just
    wherever someone happened to add it first."""
    verdict = extract_contract_verdict(output)
    if verdict is None or not verdict["passed"]:
        return verdict
    files_content = []
    for filepath in files_written:
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                files_content.append(f.read())
        except Exception:
            continue
    if not pass_verdict_is_grounded(files_content):
        logger.info(
            "Runtime verification: a deterministic '[VERIFICATION] PASS' marker was found, but "
            "none of the written files contain a '[VERIFICATION] FAIL' string anywhere - the "
            "check doesn't look like it actually branches on anything. Not trusting it; falling "
            "back to LLM grading instead."
        )
        return None
    return verdict


def _diagnosis_mismatch_bypass_reason(
    state: GenerationState, ctx: "AttemptContext", filepath: str, candidate_content: str,
) -> Optional[str]:
    """Deterministic-validation-first override for the diagnosis-mismatch
    pre-flight check (find_edits_ignoring_own_diagnosis, kriya/workflow/
    attribution.py) - added 2026-08-17 after 5 independent false-positive
    prose shapes were found in that one check across a single night (cast-
    insertion, "instead of" ambiguity, self-referential filepath,
    verification-marker wrap, plus the original 2026-08-13 incident that
    motivated the check at all). Five bespoke signals in one function is a
    symptom of an unbounded problem, not a finite list of missing cases -
    verifying analysis PROSE against diff CONTENT via backtick-quote
    matching fights an inherently fuzzy correspondence that a new model
    response shape can always break differently. Rather than adding a 6th/
    7th/Nth signal for the next prose shape, this asks the real underlying
    question directly, two different ways depending on what's cheaply
    available:

    1. If this retry is responding to a static_rule_violation (a CHEAP,
       already-registered deterministic checker - kriya/workflow/
       static_checks.py), just re-run that SAME check against the proposed
       content. If the specific violation it originally flagged is gone,
       that's authoritative - no prose-matching needed at all. Confirmed
       live, 2026-08-17 (ignite_qpid_person, run b-10m): a correct fix
       wrapping a bare `[VERIFICATION] PASS` line in println(...) was
       rejected 3 times because the marker text was a substring of a
       larger search block, not signal (b)'s required whole-block match -
       re-running BareVerificationMarkerCheck against the candidate would
       have accepted it on the first try, with zero new signal needed.
    2. If this retry is responding to a `compile` failure, there is no
       equally-cheap re-check available - the only authoritative answer is
       the real compiler, which runs immediately after this check anyway.
       Rather than keep guessing from prose, this check is bypassed
       entirely for compile-triggered retries and the real compile gate
       decides. This is an evidence-backed tradeoff, not a guess: 3 of the
       5 confirmed false positives above (cast-insertion, "instead of",
       self-referential filepath) were ALL compile-triggered, each
       rejecting an edit that would have compiled successfully; by
       contrast, this check's own ORIGINAL motivating incident (a compile
       error recurring verbatim across 3 retries because an edit never
       implemented its own analysis, at ANY line -
       attribution-fix-validation-3, 2026-08-13) would cost at most one
       extra wasted compile cycle if this check were entirely absent - the
       SAME repeated-failure signal this retry loop already detects and
       escalates from independently (state.budgets.last_failure_signature),
       with no dependency on prose-matching at all. Given real compile
       subprocess cost against the confirmed cost of wrongly rejecting a
       correct fix (3-6 wasted retries EACH, tonight), the evidence favors
       letting the compiler decide.

    pom_semantic_validation joined the compile bypass 2026-08-17 (see the
    inline comment on that branch below) - a real compile gate always runs
    right after this check regardless, so it's the same tradeoff, not a
    separate case. Every OTHER failure type (misdirected_edit,
    run_verification, general_error, etc.) is left exactly as-is - no
    evidence yet that this check misfires for them, and this is deliberately
    not a blanket "disable the check" change. (unaddressed_error_location -
    find_edits_ignoring_reported_line, the "Layer 1" check just above this
    one's call site - got its OWN, separately-scoped deterministic-
    validation-first bypass the same night this docstring line was found to
    be stale; see that check's own inline comment in run_attempt() for why it
    didn't need this function's signature-based dispatch at all.)

    Returns a human-readable bypass reason (for logging/observability - the
    diagnosis text stays useful there, just never a blocking gate for these
    two cases) if the check should be skipped for this edit; None if the
    existing diagnosis-mismatch check should run and decide as before."""
    # Bounded-veto policy, checked before anything else and independent of
    # fail_type: this check can reject a given FILE'S edit at most once per
    # run. Found live, 2026-08-17 (ignite_qpid_person, run b-10o): the
    # pom_semantic_validation false positive above rejected the SAME correct
    # fix 4 CONSECUTIVE times before the model finally gave up and regenerated
    # from scratch on a fallback model - by which point the run had already
    # burned most of its wall-clock budget on one heuristic's repeated wrong
    # call. Every fail_type this check has EVER been extended to cover
    # (compile, static_rule_violation, pom_semantic_validation) was found
    # this same way: a real incident, after the fact. This is the safety net
    # for every fail_type NOT yet on that list, and for the next false-
    # positive shape this check will inevitably have that nobody has found
    # yet - a single wrongful rejection costs one retry; catching this
    # heuristic in a 4x-in-a-row loop costs the whole run.
    if state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) >= 1:
        return (
            "this check already rejected an edit to this file once this run - "
            "per the bounded-veto policy, it never rejects the same file twice "
            "regardless of failure type; the real downstream gate decides instead"
        )
    sig = state.budgets.last_failure_signature
    if not sig:
        return None
    fail_type = sig[0]
    if fail_type in ("compile", "pom_semantic_validation", "targeted_test"):
        # targeted_test added 2026-08-17: run_tests(target_test=...) (kriya/
        # tools/validate.py) already scopes a targeted-test rerun to the ONE
        # test file extract_target_test() identified - not the whole
        # regression suite - and that real rerun happens immediately after
        # this per-file write loop regardless, in the SAME attempt (see the
        # targeted_test gate a few hundred lines below this check's own call
        # site). Same tradeoff as compile/pom_semantic_validation: bypassing
        # here costs nothing beyond what would happen anyway, and avoids
        # prose-matching a correct fix out from under a test that would have
        # passed.
        # pom_semantic_validation added 2026-08-17 after a live false positive
        # (ignite_qpid_person, run b-10o): a correct edit wrapped a bare
        # <dependency> fragment in a full, valid <project> POM structure -
        # exactly what its own analysis said - but the analysis quoted the
        # tag as `<project>` (no attributes) while the actual fix wrote
        # `<project xmlns="...">` (real Maven POMs always declare a
        # namespace), so a literal substring check for the bare quoted tag
        # never matched. Same tradeoff as compile, for the same reason: `mvn
        # validate` (the check that raised pom_semantic_validation) is a
        # cheap PRE-check specifically so a broken POM fails before the
        # expensive full `mvn compile` - but that real compile gate still
        # runs immediately after this per-file loop regardless, and would
        # catch a genuinely still-broken POM just as reliably, one step
        # later. No new prose-matching signal needed (the underlying gap is
        # XML-attribute-vs-bare-tag-name, not really about pom.xml
        # specifically - a 6th signal here would just be the next incident-
        # specific patch this function's whole redesign was meant to stop).
        return "this retry responds to a compile/POM-validation/targeted-test failure - deferring to the real gate instead of prose-matching"
    if fail_type == "static_rule_violation":
        from kriya.workflow.static_checks import run_static_checks
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a static rule can span an established file plus the one just edited.
        still_violates = run_static_checks(
            ctx.worktree_path, sorted(set(state.all_files_written) | set(ctx.established_files)),
            overrides={filepath: candidate_content},
        )
        if not still_violates:
            return "the static check that originally flagged this file no longer flags the proposed content"
    return None


def _dependency_graph_db_path(ctx: "AttemptContext") -> str:
    return os.path.join(ctx.kernel.config.paths.memory, "dependency_graph.db")


def _extract_class_names_best_effort(db_path: str, filepath: str, content: str) -> List[str]:
    """Short-lived DependencyGraph open/query/close for one file - cheap
    (existing WAL-mode SQLite file, a handful of milliseconds) and avoids
    threading a long-lived connection through the entire per-file write loop
    below just to reuse extract_class_names(), which is an instance method,
    not static. Best-effort: any error (including a missing db_path, checked
    by the caller) degrades to no names extracted, never a false rejection."""
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            return graph.extract_class_names(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        logger.debug(f"Skipping duplicate-type check for {filepath}: {e}")
        return []


def _find_java_main_class_best_effort(db_path: str, filepath: str, content: str) -> Optional[str]:
    """Same short-lived DependencyGraph open/query/close shape as
    _extract_class_names_best_effort() above, for
    DependencyGraph.find_java_main_class() instead - see that method's own
    docstring for what it detects and why (deterministic Java entrypoint
    resolution for a no-pom.xml project, ground_java_entrypoint_in_no_build_
    file_projects()'s call site below)."""
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            return graph.find_java_main_class(filepath, content)
        finally:
            graph.close()
    except Exception as e:
        logger.debug(f"Skipping Java entrypoint detection for {filepath}: {e}")
        return None


def _build_java_main_class_map(java_files: List[str], ctx: "AttemptContext") -> Dict[str, str]:
    """{filepath: entrypoint_class} for every .java file (already the
    established_files-inclusive union the caller passes in) that has a real
    `public static void main` - see find_java_main_class()'s own docstring.
    Reads each file's CURRENT content fresh (worktree first, workspace
    fallback - same lookup order used throughout this module), never cached
    across attempts unlike judge()'s own judgment: a retry can edit a file's
    content, and this determination must reflect what's actually on disk
    right now, not a stale snapshot from an earlier attempt."""
    db_path = _dependency_graph_db_path(ctx)
    result: Dict[str, str] = {}
    for filepath in java_files:
        full_path = os.path.join(ctx.worktree_path, filepath)
        if not os.path.exists(full_path):
            full_path = os.path.join(ctx.workspace_path, filepath)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            logger.debug(f"Java entrypoint detection: couldn't read {filepath}, skipping it: {e}")
            continue
        entrypoint_class = _find_java_main_class_best_effort(db_path, filepath, content)
        if entrypoint_class:
            result[filepath] = entrypoint_class
    return result


_JAVA_PACKAGE_DECL_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _build_java_package_map(java_files: List[str], ctx: "AttemptContext") -> Dict[str, Optional[str]]:
    """{filepath: package_or_None} for every .java file given, read fresh
    (worktree first, workspace fallback, same lookup order as
    _build_java_main_class_map() above) - the I/O half of
    find_cross_package_symbol_mismatch()'s own deliberately pure/testable
    design (kriya/workflow/failure_grounding.py). None means the file has no
    package declaration (Java's default/unnamed package), which is a real,
    meaningful value here, not "unknown" - a file that can't be read at all
    is simply omitted from the returned dict rather than guessed at."""
    result: Dict[str, Optional[str]] = {}
    for filepath in java_files:
        full_path = os.path.join(ctx.worktree_path, filepath)
        if not os.path.exists(full_path):
            full_path = os.path.join(ctx.workspace_path, filepath)
        if not os.path.exists(full_path):
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            logger.debug(f"Java package detection: couldn't read {filepath}, skipping it: {e}")
            continue
        pkg_match = _JAVA_PACKAGE_DECL_RE.search(content)
        result[filepath] = pkg_match.group(1) if pkg_match else None
    return result


def _build_workspace_type_index(state: GenerationState, ctx: "AttemptContext") -> Dict[str, List[str]]:
    """Workspace-wide simple-class-name -> [filepaths] index for the
    duplicate-type-across-files pre-flight check below and
    find_cross_package_symbol_mismatch() (see their own docstrings for the
    live incidents this exists to prevent). Two layers:

    1. DependencyGraph.get_class_symbol_locations() - the persisted baseline,
       covering pre-existing repo content and every earlier-completed
       milestone's already-applied output, PROVIDED `kriya analyze` has ever
       actually been run against this workspace. Found live, 2026-08-22
       (ignite_qpid_protocol): this baseline is NOT populated automatically
       by run_generation_workflow() at all - index_repository() (the only
       thing that ever writes real rows into dependency_graph.db's symbols
       table) is called exclusively from the explicit `kriya analyze`/
       `kriya analyze --vectors` CLI path (kriya/cli.py) - a milestone-
       decomposition project that never had that command run against it has
       a genuinely EMPTY persisted baseline for its entire lifetime, however
       many milestones have completed. Best-effort by construction regardless
       (no dependency_graph.db yet, or any DB error, degrades to an empty
       baseline for this layer) - this is exactly why layer 2 below is not
       optional supplementary coverage, it is THE primary coverage for any
       project that has never been explicitly `kriya analyze`-d.
    2. Every file in state.all_files_written UNION ctx.established_files,
       read fresh (worktree first, workspace fallback - same lookup order
       used throughout this module) and parsed via
       DependencyGraph.extract_class_names() (no DB write). Found live,
       2026-08-22, the SAME "established_files blind spot" class of bug
       already fixed three times this session at other call sites
       (RunVerifierAgent.judge()'s files_written, self-diagnosis
       attribution): this layer previously covered ONLY state.
       all_files_written (files THIS attempt itself wrote), so an earlier,
       already-completed milestone's file was invisible to both the
       duplicate-type gate and find_cross_package_symbol_mismatch() unless
       `kriya analyze` happened to have indexed it into layer 1 - for a
       project that never had that command run, EVERY earlier milestone's
       file was invisible to this whole index, silently, for this session's
       entire live-validation effort."""
    db_path = _dependency_graph_db_path(ctx)
    try:
        from kriya.analyzer.graph import DependencyGraph
        graph = DependencyGraph(db_path)
        try:
            index = graph.get_class_symbol_locations()
        finally:
            graph.close()
        for written_path in sorted(set(state.all_files_written) | set(ctx.established_files)):
            full_path = os.path.join(ctx.worktree_path, written_path)
            if not os.path.exists(full_path):
                full_path = os.path.join(ctx.workspace_path, written_path)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                written_content = fh.read()
            for name in _extract_class_names_best_effort(db_path, written_path, written_content):
                paths = index.setdefault(name, [])
                if written_path not in paths:
                    paths.append(written_path)
        return index
    except Exception as e:
        logger.debug(f"_build_workspace_type_index: skipping duplicate-type check for this attempt: {e}")
        return {}


async def run_attempt(state: GenerationState, ctx: AttemptContext) -> None:
    """Runs one Developer + Quality Gates attempt. Mutates state in place
    (files_written, gate_outcomes, model_hops, run_verification_*, etc.).
    Raises QualityGateFailure or IncompleteGenerationError on any gate
    failure; returns normally when Quality Gates (including Runtime
    Verification) pass."""
    state.attempt_number += 1
    # Mode selection delegates to retry_policy.decide_retry_action() - the
    # same pure decision function the outer while loop (workflow.py) and the
    # post-failure budget bookkeeping (retry_strategy.py) already call to
    # decide whether to continue at all, so "targeted beats missing_files
    # beats fallback_targeted beats full_set" is encoded in exactly one
    # place instead of being independently hand-rolled here too (found by
    # code review as unintentional duplication - the two copies happened to
    # agree, but nothing enforced that). environment_failure/attempt_number
    # are deliberately left at their None defaults: this call is scoped to
    # MODE selection only - the STOP_ENVIRONMENT/STOP_EXHAUSTED stop
    # conditions those two params drive are already handled one level up, by
    # the while loop's own decide_for_state() check before run_attempt() is
    # ever invoked for this iteration, so they must not fire a second time
    # here with a since-incremented attempt_number.
    retry_decision = decide_retry_action(
        retry_count=state.budgets.retry_count,
        max_retries=ctx.max_retries,
        targeted_retry_count=state.budgets.targeted_retry_count,
        targeted_max_retries=ctx.targeted_max_retries,
        has_implicated_files=bool(state.last_implicated_files),
        has_missing_files=bool(state.last_missing_files),
        has_fallback_model=bool(ctx.chain),
        fallback_targeted_attempted=state.budgets.fallback_targeted_attempted,
        environment_failure=None,
        fallback_targeted_requested=state.budgets.fallback_targeted_requested,
    )
    # Recorded now, not derived by the caller afterward - see the field's own
    # docstring in kriya/workflow/state.py for why that would be unsafe.
    # RetryAction.STOP_EXHAUSTED can't actually occur here (see the None
    # defaults above), but falls back to "full_set" rather than raising, to
    # stay inert if that ever changes.
    state.last_attempt_mode = (
        retry_decision.action.value
        if retry_decision.action in (
            RetryAction.TARGETED, RetryAction.MISSING_FILES, RetryAction.FALLBACK_TARGETED,
        )
        else "full_set"
    )
    # Downstream branches below key off these booleans (not the mode string
    # directly) since that predates this function's decide_retry_action()
    # consolidation - derived from the single state.last_attempt_mode value
    # above rather than re-testing the same conditions a second time.
    use_targeted = state.last_attempt_mode == "targeted"
    use_missing_files = state.last_attempt_mode == "missing_files"
    use_fallback_targeted = state.last_attempt_mode == "fallback_targeted"
    attempt_operation = operation_for_attempt(
        state.last_attempt_mode, has_prior_failure=bool(state.error_context),
    )
    state.record_event(RunEvent(
        kind="attempt.started",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.ADVISORY,
        operation=attempt_operation.value,
        details={"mode": state.last_attempt_mode},
    ))
    # Needed unconditionally below (both the normal compile/test gate
    # path and the always-run full regression check use it) - imported
    # here rather than only inside the skippable gate block so a
    # resumed "developer_success" checkpoint iteration (which skips
    # that block entirely) still has it in scope.
    from kriya.tools.validate import PolymorphicValidator

    # A "developer_success" checkpoint means Developer generation + all
    # Quality Gates already passed once, before this process was
    # interrupted - only usable on the very first iteration of a resumed
    # run; any retry after that needs a real, fresh generation attempt.
    resuming_developer_stage = bool(
        ctx.resume_state and ctx.resume_state.get("stage") == "developer_success" and state.attempt_number == 1
    )

    if resuming_developer_stage:
        logger.info(f"Resuming checkpoint '{ctx.run_id}': using saved Developer output, skipping generation + Quality Gates.")
        files = [
            {"filepath": fp, "content": content}
            for fp, content in ctx.resume_state.get("final_files", {}).items()
        ]
        state.gate_outcomes = ctx.resume_state.get("gate_outcomes", state.gate_outcomes)
        state.model_hops = ctx.resume_state.get("model_hops", state.model_hops)
        model_override = None
        base_url_override = None
        api_key_override = None
    elif use_targeted:
        # Targeted retry: always the primary model, never escalated
        # (see the budget comment above) - so the context budget is
        # always the primary model's own window, not a fallback's.
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        retry_package = _retry_package_for_attempt(
            state, ctx,
            target_files=state.last_implicated_files,
            context_window=ctx.kernel.config.llm.context_window,
        )
        retry_error_context = (
            retry_package.authoritative_error if retry_package else state.error_context
        )
        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
        )
        logger.info(f"Targeted retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(ctx.kernel.config.llm.model)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=state.last_implicated_files,
            prior_error_context=retry_error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
            operation_by_file=_operation_map(
                ctx, state.last_implicated_files, attempt_operation,
            ),
            default_operation=attempt_operation,
        )
    elif use_fallback_targeted:
        # One-shot targeted fix on the first fallback model (see
        # fallback_targeted_attempted's own docstring above) - same
        # narrow scope as a primary-model targeted retry (just the
        # implicated files, fix-analysis/anchored-edit preference),
        # just on a different model, before paying for a full-set
        # regeneration. Set the one-shot flag immediately, not after
        # a result is known, so a crash/exception mid-attempt can
        # never cause this to be retried in a loop.
        state.budgets.fallback_targeted_attempted = True
        state.budgets.fallback_targeted_requested = False
        fallback = ctx.chain[0]
        current_limit = _reserve_graph_context_budget(
            fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = fallback.model
        base_url_override = fallback.base_url
        api_key_override = fallback.api_key
        logger.info(
            f"Trying ONE targeted fix on fallback model {model_override} before "
            "falling back to full-set regeneration."
        )

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        retry_package = _retry_package_for_attempt(
            state, ctx,
            target_files=state.last_implicated_files,
            context_window=fallback.context_window,
        )
        retry_error_context = (
            retry_package.authoritative_error if retry_package else state.error_context
        )
        task_desc, active_code_context = _build_targeted_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, state.last_implicated_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
        )
        logger.info(f"Fallback-targeted retry: focusing on {', '.join(state.last_implicated_files)}.")

        state.model_hops.append(model_override)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=state.last_implicated_files,
            prior_error_context=retry_error_context or None,
            implicated_files=state.last_implicated_files,
            error_source_context=state.last_error_source_context or None,
            retry_temperature=ctx.kernel.config.llm.retry_temperature,
            extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
            files_with_current_content=state.all_files_written,
            sibling_content_budget=_reserve_sibling_content_budget(fallback.context_window),
            operation_by_file=_operation_map(
                ctx, state.last_implicated_files, attempt_operation,
            ),
            default_operation=attempt_operation,
        )
    elif use_missing_files:
        # Missing-file recovery: same primary-model-only, non-escalating
        # budget as a targeted retry (see the comment on
        # last_missing_files above) - asks for exactly the file(s) the
        # completeness check found missing, instead of re-describing an
        # error or regenerating the whole file set.
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        base_code_context = ctx.skills_prompt
        if current_graph_context:
            base_code_context += current_graph_context
        if ctx.learned_rag_context:
            base_code_context += ctx.learned_rag_context

        # last_missing_files (from find_missing_expected_files) is always
        # bare basenames (compared against written files by basename).
        # Resolve each to a real path via architect_basename_to_path (built
        # once from the Architect's already-resolved file list above) -
        # falls back to the bare basename itself for root-level files like
        # pom.xml, or if a basename genuinely isn't in the map. This lets
        # known_target_files be used safely: confirmed live (Qpid+Ignite
        # validation) that leaving this to the model's own file-list call -
        # even when explicitly told exactly which 1-4 files are missing -
        # reliably returns only ONE of them, silently dropping the rest and
        # burning the whole retry budget without ever recovering them.
        resolved_missing_files = [
            ctx.architect_basename_to_path.get(basename, basename) for basename in state.last_missing_files
        ]

        task_desc, active_code_context = _build_missing_files_retry_prompt(
            ctx.goal, ctx.plan, ctx.design, resolved_missing_files,
            state.all_files_written, ctx.worktree_path, base_code_context,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
        )
        logger.info(f"Missing-file recovery retry {state.budgets.targeted_retry_count + 1}/{ctx.targeted_max_retries}: adding {', '.join(resolved_missing_files)}.")

        state.model_hops.append(ctx.kernel.config.llm.model)

        dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
        files = await _run_developer_generation(
            state, ctx,
            task_description=task_desc,
            design_context=ctx.design,
            existing_code_context=active_code_context,
            stream_callback=dev_stream,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            known_target_files=resolved_missing_files,
            sibling_content_budget=_reserve_sibling_content_budget(ctx.kernel.config.llm.context_window),
            operation_by_file=_operation_map(
                ctx, resolved_missing_files, attempt_operation,
            ),
            default_operation=attempt_operation,
        )
    else:
        # Re-run context budget allocator dynamically for escalated model context window size
        current_limit = _reserve_graph_context_budget(
            ctx.kernel.config.llm.context_window, ctx.skills_prompt, ctx.learned_rag_context
        )
        model_override = None
        base_url_override = None
        api_key_override = None

        active_context_window = ctx.kernel.config.llm.context_window
        fallback = resolve_fallback_model(state.budgets.retry_count, ctx.chain)
        if fallback is not None:
            model_override = fallback.model
            base_url_override = fallback.base_url
            api_key_override = fallback.api_key
            active_context_window = fallback.context_window
            current_limit = _reserve_graph_context_budget(
                fallback.context_window, ctx.skills_prompt, ctx.learned_rag_context
            )
            logger.info(f"Escalating compilation attempt to fallback model: {model_override} (Limit: {current_limit} tokens)")

        current_graph_context = build_code_context(ctx.matched_files, ctx.related_files, ctx.workspace_path, current_limit)
        active_code_context = ctx.skills_prompt
        if current_graph_context:
            active_code_context += current_graph_context
        if ctx.learned_rag_context:
            active_code_context += ctx.learned_rag_context

        retry_package = _retry_package_for_attempt(
            state, ctx,
            target_files=state.last_implicated_files,
            context_window=active_context_window,
        )
        retry_error_context = (
            retry_package.authoritative_error if retry_package else state.error_context
        )
        task_desc, active_code_context = _build_full_set_retry_prompt(
            ctx.goal, ctx.plan, state.error_context, ctx.required_files_prompt_block,
            state.all_files_written, ctx.worktree_path, active_code_context,
            ctx.required_dependencies_prompt_block,
            ecosystem_invariant_block=ctx.ecosystem_invariant_block,
            resource_lifecycle_block=ctx.resource_lifecycle_block,
            verification_contract_block=ctx.verification_contract_block,
            retry_package=retry_package,
        )

        # Track model hops
        state.model_hops.append(model_override or ctx.kernel.config.llm.model)

        # On the very first attempt only (never a full-set retry, which
        # already escalates through the fallback chain above and is
        # regenerating in response to a real compile/test/runtime error,
        # not a clean slate) - if the Architect's design yielded a
        # deterministic file manifest, use it directly instead of asking
        # the model to independently re-derive the same list. Confirmed
        # live: "INCOMPLETE GENERATION" (the design called for N files,
        # fewer were written) was one of the most common first-attempt
        # failures observed this session, each one costing a full extra
        # missing-file-recovery retry cycle - this prevents that failure
        # category outright on attempt 1 instead of only recovering from
        # it after the fact. Falls back to today's ask-the-model-for-a-
        # list behavior when the design didn't yield a usable list.
        # expected_files_upfront is already resolved to real paths by
        # this point (architect_files comes pre-resolved either from the
        # Architect's own structured JSON file list, or, in the fallback
        # case above, from _resolve_file_paths_from_design already) - no
        # separate resolution step needed here anymore.
        known_target_files = None
        active_failure_signature = state.budgets.last_failure_signature
        if state.budgets.retry_count == 0 and ctx.expected_files_upfront and active_failure_signature is None:
            known_target_files = ctx.expected_files_upfront
        elif (
            active_failure_signature is not None
            and state.last_implicated_files
            and state.budgets.scoped_full_set_failure_signature != active_failure_signature
        ):
            # Each distinct, grounded validator failure gets exactly one
            # dependency-closure repair before broad regeneration, independent
            # of global retry_count consumed by earlier, unrelated failures.
            # Repetition of the SAME signature broadens after this one shot.
            known_target_files = dependent_closure(
                state.last_implicated_files, ctx.generation_dependencies,
            )
            state.budgets.scoped_full_set_failure_signature = active_failure_signature
            logger.info(
                f"First full-set attempt for this failure family has grounded implicated "
                f"file(s) - scoping to their dependency closure "
                f"{', '.join(known_target_files)} instead of the full file set."
            )

        # PlannerAgent's own prompt never asks for full code, but models
        # routinely over-deliver it anyway in fenced blocks inside the plan
        # text - Architect explicitly discards it, and Developer previously
        # always regenerated every file from scratch regardless, paying a
        # full completion per file for work already done. On attempt 1 only
        # (never a retry - a plan that already led to a failure isn't a
        # trustworthy source for a fresh attempt), if the Planner's own text
        # already has usable code for EVERY expected file, use it directly
        # instead of asking Developer to redo it - still subject to the
        # exact same compile/test/Runtime-Verification gates as any other
        # attempt, so a wrong or incomplete Planner draft costs at most one
        # gate cycle before falling through to a real Developer generation
        # on the next attempt, the same downside a bad first Developer
        # attempt would already have. Deliberately all-or-nothing: a partial
        # match (some but not all expected files present) is NOT reused, to
        # avoid a third, harder-to-verify code path that mixes Planner and
        # Developer output for the same attempt.
        reused_files = None
        if state.budgets.retry_count == 0 and ctx.expected_files_upfront:
            planner_blocks = extract_planner_code_blocks(ctx.plan, ctx.expected_files_upfront)
            if set(planner_blocks.keys()) == set(ctx.expected_files_upfront):
                reused_files = [{"filepath": fp, "content": content} for fp, content in planner_blocks.items()]
                logger.info(
                    f"Planner's own plan already contains complete code for all "
                    f"{len(reused_files)} expected file(s) - reusing it directly instead of "
                    "a fresh Developer generation call, subject to the same Quality Gates "
                    "as any other attempt."
                )

        # Logged symmetrically on BOTH branches (previously only the reused
        # branch logged anything) so a run's log alone - via the same grep-
        # based analysis this session has used all day - can already answer
        # "did attempt 1 use Planner-reused content or fresh Developer
        # generation" without needing new tooling. See
        # state.planner_reuse_used_attempt1's own docstring for why this is
        # worth tracking at all: an external review raised, and two of the
        # same day's live incidents supported, the hypothesis that reused
        # Planner content correlates with more first-attempt failures than
        # fresh Developer generation - this makes that measurable from
        # ordinary run logs instead of argued from a handful of anecdotes.
        if state.budgets.retry_count == 0:
            state.planner_reuse_used_attempt1 = reused_files is not None
            if reused_files is None:
                logger.info(
                    "Attempt 1: Planner's plan did not contain usable code for every expected "
                    "file (or none was expected upfront) - using a fresh Developer generation "
                    "call for all files."
                )

        if reused_files is not None:
            files = reused_files
        else:
            # Generate code files
            dev_stream = (lambda token: ctx.stream_callback("Code Generation", token)) if ctx.stream_callback else None
            files = await _run_developer_generation(
                state, ctx,
                task_description=task_desc,
                design_context=ctx.design,
                existing_code_context=active_code_context,
                stream_callback=dev_stream,
                model_override=model_override,
                base_url_override=base_url_override,
                api_key_override=api_key_override,
                known_target_files=known_target_files,
                prior_error_context=retry_error_context or None,
                implicated_files=state.last_implicated_files,
                error_source_context=state.last_error_source_context or None,
                retry_temperature=ctx.kernel.config.llm.retry_temperature,
                extra_fix_instruction=DeveloperAgent.SELF_CONSISTENCY_NUDGE,
                files_with_current_content=state.all_files_written,
                sibling_content_budget=_reserve_sibling_content_budget(active_context_window),
                operation_by_file=(
                    _operation_map(ctx, known_target_files, attempt_operation)
                    if known_target_files else None
                ),
                default_operation=attempt_operation,
            )

    # Recorded now, not derived by the caller afterward - see the fields'
    # own docstring in kriya/workflow/state.py. Every branch above sets all
    # three of these (to None for the primary model, or a fallback's values).
    state.last_model_override = model_override
    state.last_base_url_override = base_url_override
    state.last_api_key_override = api_key_override

    # Normalize filepaths before anything downstream uses them - the
    # Developer Agent occasionally returns an absolute path instead of a
    # relative one, which os.path.join(base, filepath) would silently
    # resolve to just `filepath` (discarding `base`) in every loop below.
    normalized_files = []
    for file_obj in files:
        raw_filepath = file_obj.get("filepath", "")
        normalized = normalize_written_filepath(raw_filepath, ctx.workspace_path)
        if normalized is None:
            logger.warning(f"Developer Agent returned an unusable filepath '{raw_filepath}' (absolute path outside the workspace, or empty) - skipping this file.")
            continue
        if normalized != raw_filepath:
            logger.info(f"Normalized Developer Agent filepath '{raw_filepath}' -> '{normalized}'.")
        file_obj["filepath"] = normalized
        normalized_files.append(file_obj)
    files = normalized_files

    # Enforce the selected response contract before attribution heuristics or
    # writes.  Classification is based on the target's real existence, so the
    # same full-content shape is CREATE for an absent file and REPAIR for an
    # existing one.  Repair fallbacks are explicit and observable; creation has
    # no permissive fallback that could turn a missing file into a silent no-op.
    for file_obj in files:
        filepath = file_obj["filepath"]
        file_exists = _target_exists(ctx, filepath)
        expected_operation = operation_for_file(
            attempt_operation, file_exists=file_exists,
        )
        actual_operation, contract_error = validate_operation_result(
            file_obj,
            expected=expected_operation,
            file_exists=file_exists,
        )
        if contract_error:
            failure = Failure(
                type="operation_contract",
                message=(
                    f"OPERATION CONTRACT FAILURE in {filepath}: {contract_error}. "
                    f"Return exactly the {expected_operation.value} response shape requested."
                ),
                raw_output=contract_error,
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                attempted_edits=file_obj.get("edits") or [],
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if actual_operation is not expected_operation:
            state.record_event(RunEvent(
                kind="operation.fallback",
                attempt=state.attempt_number,
                source="developer",
                authority=EventAuthority.ADVISORY,
                operation=actual_operation.value,
                message=(
                    f"{filepath}: accepted safe fallback from "
                    f"{expected_operation.value} to {actual_operation.value}."
                ),
                details={
                    "filepath": filepath,
                    "requested": expected_operation.value,
                    "returned": actual_operation.value,
                },
            ))

    # Captured here (before any write can raise) rather than after the write
    # loop below, so a self-diagnosis is never lost to an anchored-edit
    # exception on an unrelated file later in the same batch. Paired with
    # the failure signature THIS attempt was responding to - state.budgets.
    # last_failure_signature is still the PREVIOUS failure's signature at
    # this point (retry_strategy.py only overwrites it after the NEXT
    # failure is classified) - see kriya/workflow/attribution.py's
    # extract_self_diagnosed_files() and retry_strategy.py's signature-gated
    # consumption of this field.
    # Only overwritten when THIS attempt actually produced a fresh self-
    # diagnosis - left untouched otherwise, not reset to None. A stale value
    # here is safe by construction: retry_strategy.py's consumption below
    # only ever reuses it when its stored signature exactly matches the
    # CURRENT failure's signature, so an unrelated earlier diagnosis can
    # never wrongly redirect a different failure. Resetting to None on every
    # attempt with no fresh diagnosis would instead lose a still-valid
    # diagnosis the moment one intervening attempt didn't happen to repeat
    # its own FIX ANALYSIS - e.g. attempt N correctly redirects to a
    # different file via self-diagnosis, attempt N+1 (now targeting that
    # file) returns no analysis, and the SAME original failure signature
    # recurs at attempt N+2: the diagnosis must still be there to redirect
    # correctly again.
    # Unioned with ctx.established_files (see that field's own docstring) so a
    # correct diagnosis naming an EARLIER milestone's file - one THIS attempt
    # never wrote itself - is still a valid redirect candidate, not silently
    # unmatchable.
    self_diagnosed = extract_self_diagnosed_files(
        files, sorted(set(state.all_files_written) | set(ctx.established_files)),
    )
    if self_diagnosed:
        state.last_self_diagnosis = (state.budgets.last_failure_signature, self_diagnosed)

    # "NO CHANGE NEEDED" is useful negative attribution evidence, not a
    # successful repair. In a targeted attempt, rerunning compile/tests/runtime
    # after every returned target was explicitly left untouched wastes an
    # expensive gate and routes the same failure back to the same file. Turn it
    # into a retry signal before any write or gate. If FIX ANALYSIS names a
    # different known file, redirect there. Otherwise preserve a preceding
    # deterministic locator; only genuinely ungrounded scope widens.
    all_targets_rejected = all_results_are_no_change(files)
    if state.last_attempt_mode in ("targeted", "fallback_targeted") and all_targets_rejected:
        state.record_event(RunEvent(
            kind="operation.no_change",
            attempt=state.attempt_number,
            source="developer",
            authority=EventAuthority.ADVISORY,
            operation="no_change_assessment",
            message="Every targeted result rejected the selected file scope.",
        ))
        returned_targets = [f.get("filepath", "") for f in files if f.get("filepath")]
        # An advisory NO CHANGE response may redirect a weak/judgment-based
        # attribution, but it must not erase an authoritative deterministic
        # locator.  Preserve only the files that were both locator-backed and
        # actually in this response's target set; this cannot pull unrelated
        # stale evidence forward.  A grounded alternate named by the response
        # still wins because it is explicit current-turn root-cause evidence.
        preserved_locator_files = _preserved_authoritative_locator_files(
            state, returned_targets, self_diagnosed,
        )
        likely_files = list(self_diagnosed or preserved_locator_files)
        _request_fallback_for_rejected_authoritative_target(
            state, ctx, preserved_locator_files,
        )
        evidence = "\n\n".join(
            f"{f.get('filepath', '(unknown file)')}: "
            f"{f.get('analysis') or '(no FIX ANALYSIS supplied)'}" for f in files
        )
        if self_diagnosed:
            attribution_message = (
                f"Its own analysis instead names: {', '.join(self_diagnosed)}."
            )
        elif preserved_locator_files:
            attribution_message = (
                "The advisory response supplied no grounded alternate; retaining the "
                "preceding authoritative locator for: "
                f"{', '.join(preserved_locator_files)}."
            )
        else:
            attribution_message = (
                "No grounded alternate file or authoritative locator was available; "
                "widen the next attempt to the full file set."
            )
        failure = Failure(
            type="attribution_rejected",
            message=(
                "TARGET ATTRIBUTION REJECTED: the Developer reported NO CHANGE NEEDED "
                f"for every targeted file ({', '.join(returned_targets)}). "
                + attribution_message
            ),
            raw_output=evidence,
            source="developer",
            authority="advisory",
            file_locations=[FileLocation(filepath=f) for f in likely_files],
            likely_files=likely_files,
            diagnostics=_preserved_attribution_diagnostics(preserved_locator_files),
            attempt=state.attempt_number,
        )
        state.gate_outcomes.append(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    # Read original file contents before overwriting (crucial for fallback mode diffs)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        if not filepath:
            continue
        if filepath not in state.all_original_contents:
            actual_file = os.path.join(ctx.workspace_path, filepath)
            if os.path.exists(actual_file):
                with open(actual_file, "r", encoding="utf-8", errors="replace") as fh:
                    state.all_original_contents[filepath] = fh.read()
            else:
                state.all_original_contents[filepath] = ""

    # Write files to worktree sandbox
    state.files_written = []
    staged_writes: List[StagedFileWrite] = []
    # Built once per attempt, not once per file - see _build_workspace_type_index's
    # own docstring. Updated in place below as each new file is accepted, so two
    # files in the SAME batch that collide with each other are caught too.
    workspace_type_index = _build_workspace_type_index(state, ctx)
    for file_obj in files:
        filepath = file_obj.get("filepath", "")
        content = file_obj.get("content", "")
        edits = file_obj.get("edits", [])
        analysis = file_obj.get("analysis")

        if not filepath:
            continue

        # Single choke point every content path (batch JSON, iterative
        # per-file, a full-set retry) converges through before a byte
        # reaches disk - closes a real gap the per-path fixes upstream
        # (DeveloperAgent.sanitize_generated_content) don't: a batch JSON
        # response's content/edits fields are consumed directly from
        # parsed JSON and never passed through any sanitization at all
        # before this point. Idempotent/harmless to re-apply to content
        # that already went through it upstream.
        if edits:
            edits = [
                {
                    **e,
                    "search": DeveloperAgent.sanitize_generated_content(e.get("search", "")),
                    "replace": DeveloperAgent.sanitize_generated_content(e.get("replace", "")),
                }
                for e in edits
            ]
        elif content is not None:
            content = DeveloperAgent.sanitize_generated_content(content)

        full_path = os.path.join(ctx.worktree_path, filepath)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        if edits:
            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                current_file_path = os.path.join(ctx.workspace_path, filepath)

            orig_text = ""
            if os.path.exists(current_file_path):
                with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                    orig_text = fh.read()

            try:
                new_content = apply_anchored_edits(orig_text, edits, active_code_context)
            except ValueError as anchor_ex:
                # apply_anchored_edits() itself never receives a filepath, so its
                # raised ValueError never named one either - this failure class
                # always fell through to a blind full-set retry, unlike a compile
                # error (which self-names its file). filepath IS known right here,
                # in the caller's loop scope - capture it now instead of losing it.
                # orig_text (the real pre-edit content the SEARCH block was
                # supposed to match against) is already in memory - exactly what's
                # needed to debug an anchor mismatch, no disk re-read needed.
                # edits (the actual search/replace text that was attempted, already
                # sanitized) is captured alongside it as attempted_edits - together
                # both halves of "why didn't this match" are now persisted, not just
                # the generic "matched 0 times" message.
                #
                # Before accepting that generic explanation, check whether the
                # failed search block actually belongs to a DIFFERENT known file -
                # see find_misdirected_edit_target()'s own docstring for the real
                # incident (a-3, ignite_qpid_protocol, 2026-08-14) this closes: a
                # retry scoped to one file by a locator that could only ever name
                # the file where an explicit runtime check THREW, not the
                # different file whose method silently returned the wrong value,
                # produced a textbook-correct diagnosis whose edit could never
                # match the file it was constrained to. Reads every OTHER
                # already-written file straight from the worktree (nothing keeps
                # their content in memory this late in the per-file write loop).
                other_files: Dict[str, str] = {}
                # Unioned with ctx.established_files (see that field's own
                # docstring) - a misdirected edit could legitimately belong to
                # an established file from an earlier milestone, not just
                # another file this attempt itself wrote.
                for other_filepath in set(state.all_files_written) | set(ctx.established_files):
                    if other_filepath == filepath:
                        continue
                    other_full_path = os.path.join(ctx.worktree_path, other_filepath)
                    try:
                        with open(other_full_path, "r", encoding="utf-8", errors="replace") as fh:
                            other_files[other_filepath] = fh.read()
                    except OSError:
                        continue

                misdirected_target = find_misdirected_edit_target(edits, orig_text, other_files)
                if misdirected_target:
                    # raw_output (not just message) carries both filenames - it's
                    # what to_gate_outcome() persists as "output" (raw_output or
                    # message) and what a future post-mortem reads from traces.db,
                    # same forensics goal as failed_content/attempted_edits below.
                    misdirected_explanation = (
                        f"the search block for {filepath} matched 0 times against "
                        f"{filepath}'s content, but was found instead inside {misdirected_target}"
                    )
                    failure = Failure(
                        type="misdirected_edit",
                        message=(
                            f"MISDIRECTED EDIT: {misdirected_explanation}. The fix you "
                            f"diagnosed likely belongs in {misdirected_target}, not {filepath} - "
                            f"target {misdirected_target} in your next edit (and {filepath} too, "
                            f"only if it genuinely also needs its own companion change)."
                        ),
                        raw_output=misdirected_explanation,
                        file_locations=[
                            FileLocation(filepath=filepath),
                            FileLocation(filepath=misdirected_target),
                        ],
                        likely_files=[filepath, misdirected_target],
                        failed_content={filepath: orig_text, misdirected_target: other_files[misdirected_target]},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure) from anchor_ex

                failure = Failure(
                    type="anchored_edit",
                    message=f"ANCHORED EDIT FAILURE in {filepath}: {anchor_ex}",
                    raw_output=str(anchor_ex),
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure) from anchor_ex

            # Layer 0 pre-flight check (see find_whole_response_no_op's own
            # docstring): purely structural, no analysis text or fail_type
            # dispatch needed - unlike Layers 1/2 below, a response whose
            # every edit is a byte-identical no-op is objectively useless
            # regardless of what it claims, so this is never bypassed and
            # never counts against the Layer 2 bounded veto (a different
            # check, a different question). apply_anchored_edits() above
            # already confirmed every search block matched real content -
            # this only fires when the edit(s) that matched changed nothing.
            if find_whole_response_no_op(edits):
                preserved_locator_files = _preserved_authoritative_locator_files(
                    state, [filepath], self_diagnosed,
                )
                retry_files = list(self_diagnosed or preserved_locator_files) or [filepath]
                _request_fallback_for_rejected_authoritative_target(
                    state, ctx, preserved_locator_files,
                )
                failure = Failure(
                    type="no_op_edit",
                    message=(
                        f"NO-OP EDIT in {filepath}: every SEARCH/REPLACE pair in your response "
                        f"is byte-identical - this response changes nothing. If this file "
                        f"genuinely needs no change, write \"NO CHANGE NEEDED:\" instead of a "
                        f"SEARCH/REPLACE block; otherwise your REPLACE text must actually differ "
                        f"from your SEARCH text."
                    ),
                    raw_output="every edit in the response was a no-op (search == replace)",
                    source="developer",
                    authority="advisory",
                    file_locations=[FileLocation(filepath=f) for f in retry_files],
                    likely_files=retry_files,
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    diagnostics=_preserved_attribution_diagnostics(preserved_locator_files),
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 1 pre-flight check (see find_edits_ignoring_reported_line's
            # own docstring): only meaningful when this attempt is itself
            # responding to a real prior error with a locatable line for this
            # file - error_context is "" on a clean first attempt, where
            # extract_error_source_locations() would find nothing anyway, but
            # the explicit guard avoids the line-matching work entirely there.
            #
            # Deterministic-validation-first (same principle as
            # _diagnosis_mismatch_bypass_reason, 2026-08-17): this check ONLY
            # ever fires when extract_error_source_locations() found a real
            # javac-style file:[line,col] locator for THIS file - i.e. it is
            # compile-scoped by construction, every single time, not just
            # sometimes (unlike diagnosis_mismatch, which fires for every
            # failure type). A real compile gate runs immediately after this
            # per-file write loop regardless, so it is always the cheaper,
            # authoritative answer to "does this edit actually still leave
            # the reported error in place" - no heuristic needed at all.
            # Confirmed both ways live via the corpus survey's replay of the
            # 2 DEBUG-capturable unaddressed_error_location occurrences
            # (b-10f, b-10n): b-10f was a FALSE positive - a companion edit
            # earlier in the same response fixed the bad import, which
            # transitively resolves the reported line's "cannot find symbol"
            # error too, but this check has no visibility into OTHER edits in
            # the same response and flagged the untouched (but now-harmless)
            # usage-site line anyway. b-10n was a genuine TRUE positive - the
            # model's own analysis said it would add a missing import and
            # never did - and bypassing here costs nothing there: the real
            # compile gate catches the exact same missing-import error one
            # step later, through the normal compile-failure retry path this
            # loop already handles well. Given every occurrence of this
            # check's own reported-line recurred identically across many
            # historical runs (10x/9x/6x for single line numbers - the same
            # small number of Ignite/Qpid goals hitting it over and over),
            # the false-positive cost of hard-blocking here was likely the
            # dominant driver of this failure category, not genuine misses.
            if state.error_context:
                ignored_lines = find_edits_ignoring_reported_line(
                    orig_text, edits, filepath, state.error_context
                )
                if ignored_lines:
                    lines_desc = ", ".join(str(n) for n in sorted(ignored_lines))
                    logger.info(
                        f"UNADDRESSED ERROR LOCATION pre-flight check bypassed for {filepath} "
                        f"(line(s) {lines_desc} of the reported error left unchanged): deferring "
                        f"to the real compiler instead of this heuristic, which cannot see whether "
                        f"a companion edit elsewhere in the same response already resolved it."
                    )

            # Structural corruption pre-flight check (see
            # find_structural_corruption's own docstring) - a cheap,
            # deterministic tripwire for the "obviously broken" shape
            # BOTH real corruption incidents this session actually had
            # (unbalanced braces from a folded-in duplicate class),
            # before the expensive compile gate spends itself
            # discovering the same thing.
            structural_problem = find_structural_corruption(filepath, new_content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=(
                        f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem} "
                        f"This usually means an edit's replace text accidentally folded in "
                        f"extra, unrelated content (e.g. a redundant full-file dump appended "
                        f"after the intended change). Re-check your SEARCH/REPLACE blocks - "
                        f"the replace text for each pair should contain ONLY the corrected "
                        f"version of that pair's search text, nothing else."
                    ),
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: orig_text},
                    attempted_edits=edits,
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Layer 2 pre-flight check (see find_edits_ignoring_own_diagnosis's own
            # docstring): the Layer 1 check above only catches an edit that left the
            # COMPILER's own reported line unchanged - it deliberately allows a fix
            # at a different, legitimate line (e.g. a declaration above the reported
            # use site). This catches the gap that leaves open: does the edit's own
            # content actually implement what its own FIX ANALYSIS just said, at ANY
            # line - found live, 2026-08-13, a real compile error recurred verbatim
            # across 3 targeted retries despite a textbook-correct analysis every time.
            diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, edits, None, orig_text)
            if diagnosis_mismatch:
                bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, new_content)
                if bypass_reason:
                    logger.info(
                        f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                    )
                else:
                    # Counted here, at the actual rejection - see the bounded-
                    # veto policy's own comment in _diagnosis_mismatch_bypass_
                    # reason for why this exists.
                    state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                        state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                    )
                    failure = Failure(
                        type="diagnosis_mismatch",
                        message=(
                            f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                            f"Make the exact change your own analysis described - not a "
                            f"different or partial change."
                        ),
                        raw_output=diagnosis_mismatch,
                        file_locations=[FileLocation(filepath=filepath)],
                        likely_files=[filepath],
                        failed_content={filepath: orig_text},
                        attempted_edits=edits,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            else:
                # This file's edit looked consistent with its own analysis -
                # a clean slate, so an unrelated mismatch found later in the
                # run for this same file still gets its own bounded veto.
                state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=new_content,
                base_path=current_file_path,
                expected_base_revision=content_revision(orig_text),
            ))
        else:
            if content is None:
                continue

            current_file_path = os.path.join(ctx.worktree_path, filepath)
            if not os.path.exists(current_file_path):
                workspace_file_path = os.path.join(ctx.workspace_path, filepath)
                if os.path.exists(workspace_file_path):
                    current_file_path = workspace_file_path
            file_is_new = not os.path.exists(current_file_path)
            prior_content = ""
            if os.path.exists(current_file_path):
                with open(current_file_path, "r", encoding="utf-8", errors="replace") as fh:
                    prior_content = fh.read()

            structural_problem = find_structural_corruption(filepath, content)
            if structural_problem:
                failure = Failure(
                    type="structural_corruption",
                    message=f"STRUCTURAL CORRUPTION in {filepath}: {structural_problem}",
                    raw_output=structural_problem,
                    file_locations=[FileLocation(filepath=filepath)],
                    likely_files=[filepath],
                    failed_content={filepath: content},
                    attempt=state.attempt_number,
                )
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

            # Cross-file duplicate-type pre-flight check - only for a
            # genuinely NEW file (a REPAIR of a file that already legitimately
            # owns `filepath` is never a conflict, even if it re-declares its
            # own class). See find_cross_file_type_conflict's own docstring
            # for the live incident this prevents.
            candidate_type_names: List[str] = []
            if file_is_new and workspace_type_index:
                candidate_type_names = _extract_class_names_best_effort(
                    _dependency_graph_db_path(ctx), filepath, content,
                )
            if candidate_type_names:
                conflict = find_cross_file_type_conflict(filepath, candidate_type_names, workspace_type_index)
                if conflict:
                    # conflict[0] is an "ext:name" key (see extract_class_names'
                    # own docstring for why the extension is folded in) - strip
                    # it back to a plain class name for the human-facing message.
                    type_name = conflict[0].split(":", 1)[-1]
                    other_paths = conflict[1]
                    conflict_content: Dict[str, str] = {filepath: content}
                    for other_path in other_paths:
                        for base in (ctx.worktree_path, ctx.workspace_path):
                            other_full = os.path.join(base, other_path)
                            if os.path.exists(other_full):
                                with open(other_full, "r", encoding="utf-8", errors="replace") as fh:
                                    conflict_content[other_path] = fh.read()
                                break
                    failure = Failure(
                        type="duplicate_type_across_files",
                        message=(
                            f"DUPLICATE TYPE in {filepath}: '{type_name}' is already declared in "
                            f"{', '.join(other_paths)} (shown below). This is almost always a sign "
                            f"the existing file was never found or edited - target "
                            f"{other_paths[0]} directly instead of creating a new file. Only if "
                            f"'{type_name}' in {filepath} is genuinely a different, deliberately "
                            f"separate concept (rare) should you instead rename it to something "
                            f"unambiguous."
                        ),
                        raw_output=f"'{type_name}' declared in both {filepath} and {', '.join(other_paths)}",
                        file_locations=[FileLocation(filepath=filepath)] + [FileLocation(filepath=p) for p in other_paths],
                        likely_files=[filepath] + other_paths,
                        failed_content=conflict_content,
                        attempt=state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                for name in candidate_type_names:
                    paths = workspace_type_index.setdefault(name, [])
                    if filepath not in paths:
                        paths.append(filepath)

            # Layer 2 pre-flight check - same rationale as the anchored-edit branch
            # above, applied to a full-file regeneration instead. Only reads the
            # file's current on-disk content when there's actually an analysis to
            # check against, to avoid the extra I/O on the common (no prior error)
            # case.
            if analysis:
                diagnosis_mismatch = find_edits_ignoring_own_diagnosis(analysis, None, content, prior_content)
                if diagnosis_mismatch:
                    bypass_reason = _diagnosis_mismatch_bypass_reason(state, ctx, filepath, content)
                    if bypass_reason:
                        logger.info(
                            f"DIAGNOSIS MISMATCH pre-flight check bypassed for {filepath}: {bypass_reason}."
                        )
                    else:
                        state.budgets.diagnosis_mismatch_veto_counts[filepath] = (
                            state.budgets.diagnosis_mismatch_veto_counts.get(filepath, 0) + 1
                        )
                        failure = Failure(
                            type="diagnosis_mismatch",
                            message=(
                                f"DIAGNOSIS MISMATCH in {filepath}: {diagnosis_mismatch}. "
                                f"Make the exact change your own analysis described - not a "
                                f"different or partial change."
                            ),
                            raw_output=diagnosis_mismatch,
                            file_locations=[FileLocation(filepath=filepath)],
                            likely_files=[filepath],
                            failed_content={filepath: prior_content},
                            attempt=state.attempt_number,
                        )
                        state.gate_outcomes.append(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                else:
                    state.budgets.diagnosis_mismatch_veto_counts.pop(filepath, None)

            staged_writes.append(StagedFileWrite(
                target_path=full_path,
                content=content,
                base_path=current_file_path,
                expected_base_revision=content_revision(prior_content),
            ))

    # Nothing reaches the sandbox until every candidate has passed its cheap
    # deterministic checks.  The batch commit re-checks all source revisions
    # before the first write and rolls back already-written targets if an OS
    # error or last-moment revision conflict interrupts the commit.
    commit_revision_grounded_batch(staged_writes)
    changed_files = [
        os.path.relpath(staged.target_path, ctx.worktree_path)
        for staged in staged_writes
    ]
    invalidated = invalidate_validated_revisions(
        state.validated_file_revisions,
        changed_files,
        ctx.generation_dependencies,
    )
    if invalidated:
        state.record_event(RunEvent(
            kind="validation.invalidated",
            attempt=state.attempt_number,
            source="workflow",
            authority=EventAuthority.ADVISORY,
            message=(
                "Candidate changes invalidated compiled revisions for: "
                + ", ".join(invalidated)
            ),
            details={"changed_files": changed_files, "invalidated_files": invalidated},
        ))
    for staged in staged_writes:
        filepath = os.path.relpath(staged.target_path, ctx.worktree_path)
        state.files_written.append(filepath)
        state.all_files_written.add(filepath)
        logger.info(f"Committed generated/edited candidate to sandbox: {filepath}")

    # A POM has no dependency on sibling source files, but it is now validated
    # after the atomic candidate batch so no quality gate can observe a partial
    # model response.  This remains much cheaper than dependency resolution and
    # compilation, and a failed attempt is discarded by the worktree lifecycle.
    pom_files = [
        filepath for filepath in state.files_written
        if os.path.basename(filepath) == "pom.xml"
    ]
    if pom_files:
        pom_validator = PolymorphicValidator(
            ctx.worktree_path, autonomy_cfg=ctx.kernel.config.autonomy,
        )
        pom_validate_res = pom_validator.run_pom_validate()
        if not pom_validate_res["success"]:
            filepath = pom_files[0]
            full_path = os.path.join(ctx.worktree_path, filepath)
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                failed_pom_content = fh.read()
            failure = Failure(
                type="pom_semantic_validation",
                message=f"POM VALIDATION FAILED for {filepath}: {pom_validate_res['output']}",
                raw_output=pom_validate_res["output"],
                file_locations=[FileLocation(filepath=filepath)],
                likely_files=[filepath],
                failed_content={filepath: failed_pom_content},
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    if not resuming_developer_stage:
        # Completeness Check: catch the Developer Agent silently under-delivering
        # (e.g. only writing pom.xml when the Architect's design called for 7 files).
        # A trivially-passing compile on a near-empty sandbox would otherwise report
        # PASSED and get applied to the workspace despite the goal not being met.
        # Sourced from architect_files (the structured file list, or its heuristic
        # fallback - see the Architect call above) rather than re-deriving via a
        # second independent regex pass over the design's prose.
        expected_files = {os.path.basename(f) for f in ctx.architect_files}
        missing_files = find_missing_expected_files(expected_files, state.all_files_written, goal=ctx.goal)
        if missing_files:
            raise IncompleteGenerationError(
                missing_files,
                "INCOMPLETE GENERATION: The design called for the following files, but "
                f"they were never written: {', '.join(missing_files)}. "
                f"You must generate ALL files listed in the Architect Design Guidelines, "
                f"not just a subset."
            )

        # Static pre-check: deterministic, no-LLM scan for known anti-patterns already
        # documented in active skill rules (e.g. mixing Ignite's two startup mechanisms,
        # an unclosed Ignition.start()) - catches a mistake the model already had the
        # rule for, before the expensive compile+run cycle rather than after it.
        # Unioned with ctx.established_files (see that field's own docstring) -
        # a static rule violation (e.g. mixing Ignite's two startup mechanisms)
        # can span an established file plus one this attempt just wrote.
        static_check_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
        static_violation = run_static_checks(ctx.worktree_path, static_check_known_files)
        if static_violation:
            # _build_quality_gate_failure() (not a bare Failure(...)), matching the
            # SAME construction every other Quality Gate type already uses (compile/
            # test/run_verification/regression_test) - its extract_implicated_files()
            # call scopes likely_files to whichever known file(s) the violation TEXT
            # actually names, instead of defaulting to state.all_files_written (every
            # file the run has ever written). Found live, 2026-08-13
            # (ignite_qpid_protocol): every StaticCheck's own violation message
            # already names its implicated file(s) by exact known relative path (see
            # static_checks.py) - broadcasting to the whole file set discarded that
            # precision, sending a targeted retry's per-file "explain the fix"
            # instruction to every unrelated file too. Confirmed as a real, non-
            # theoretical cost, not just a wasted-call inefficiency: one of those
            # broadcast files applied a genuinely unrelated edit (an unprompted
            # config section) that had nothing to do with the actual violation. A
            # violation whose text names no known file (a hypothetical future check
            # with no per-file locality) falls through to extract_implicated_files()'s
            # own empty-list fallback, same honest degrade-to-full-set behavior every
            # other gate type already has - never a regression from today's blind
            # broadcast, just no longer the ONLY behavior available.
            failure = _build_quality_gate_failure(
                type_="static_rule_violation",
                message=f"STATIC RULE VIOLATION: {static_violation}",
                raw_output=static_violation,
                worktree_path=ctx.worktree_path,
                known_files=static_check_known_files,
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # Quality Gates: Polymorphic compile & test checks inside sandbox
        logger.info("Quality Gates: Running polymorphic compiler and test checks...")
        validator = PolymorphicValidator(
            ctx.worktree_path, original_workspace_path=ctx.workspace_path,
            autonomy_cfg=ctx.kernel.config.autonomy,
        )

        if not state.toolchain_checked:
            state.toolchain_checked = True
            state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
            if state.toolchain_warning:
                logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
            if validator.stack == "java":
                state.java_home_override = _resolve_java_home_override(ctx.goal)
                if state.java_home_override:
                    logger.warning(
                        "JVM toolchain enforcement: forcing Maven subprocess calls to "
                        f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                        "version doesn't match what 'mvn' resolves to by default here."
                    )
        # Constructed fresh above (a new validator every attempt) - re-apply
        # the one-time-resolved override every time, not just when it was
        # just computed.
        validator.java_home_override = state.java_home_override

        # Unioned with ctx.established_files (see that field's own docstring) -
        # for a no-build-file Java project, this list is extended DIRECTLY into
        # the raw javac fallback's command line (kriya/tools/validate.py); an
        # established file not passed explicitly is only found via javac's
        # fragile implicit sourcepath auto-discovery, which silently breaks
        # whenever the established file's real location doesn't mirror its
        # package path relative to the workspace root - exactly the layout
        # mismatch this session's live incident already proved can happen.
        compile_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))

        # Deterministic pom.xml sourceDirectory correction - found live,
        # 2026-08-22 (ignite_qpid_protocol milestone 3/4): see
        # ensure_maven_covers_nonconventional_java_files()'s own docstring
        # (kriya/workflow/file_resolution.py) for the full incident. Runs
        # every attempt, unconditionally, whenever a pom.xml exists in the
        # worktree - cheap to detect "already customized" and no-op, and
        # correctly re-applies if a retry rewrites pom.xml back to a plain
        # Maven-convention shape.
        pom_path = os.path.join(ctx.worktree_path, "pom.xml")
        if os.path.exists(pom_path):
            try:
                with open(pom_path, "r", encoding="utf-8", errors="replace") as fh:
                    pom_content = fh.read()
            except OSError:
                pom_content = None
            if pom_content is not None:
                skills_relpath = os.path.relpath(ctx.kernel.config.paths.skills, ctx.workspace_path)
                corrected_pom = ensure_maven_covers_nonconventional_java_files(
                    pom_content, compile_known_files, skills_relpath,
                )
                if corrected_pom is not None:
                    logger.info(
                        "pom.xml doesn't cover the actual location of known .java files under "
                        "Maven's default sourceDirectory - deterministically widening it to the "
                        "workspace root (excluding the skills and .kriya directories)."
                    )
                    pom_content = corrected_pom
                    with open(pom_path, "w", encoding="utf-8") as fh:
                        fh.write(corrected_pom)

                # Deterministic exec.mainClass property correction - found live,
                # 2026-08-22 (ignite_qpid_protocol): see
                # correct_exec_main_class_property()'s own docstring
                # (kriya/workflow/file_resolution.py) for the full incident -
                # every active skill's own example pom.xml sets a plausible-
                # looking but arbitrary default (com.example.App and siblings)
                # for this property, which the Developer can copy verbatim
                # even though the real class it generated lives in the
                # default package. compile succeeds either way (this property
                # has no bearing on what compiles) - `mvn exec:exec` only
                # fails at RUNTIME with "Could not find or load main class
                # App", the exact failure this closes.
                compile_java_files = [f for f in compile_known_files if f.endswith(".java")]
                corrected_exec_main_class = correct_exec_main_class_property(
                    pom_content, _build_java_main_class_map(compile_java_files, ctx),
                )
                if corrected_exec_main_class is not None:
                    logger.info(
                        "pom.xml's <exec.mainClass> property doesn't match the real class Kriya "
                        "actually generated (likely copied verbatim from a skill's example) - "
                        "deterministically correcting it."
                    )
                    with open(pom_path, "w", encoding="utf-8") as fh:
                        fh.write(corrected_exec_main_class)

        # Deterministic package-declaration correction - found live,
        # 2026-08-22 (ignite_qpid_protocol milestone 3/4): see
        # strip_package_declaration_matching_source_root()'s own docstring
        # (kriya/workflow/file_resolution.py) for the full incident. Runs
        # every attempt, unconditionally, over every known .java file - cheap
        # to no-op for the (overwhelmingly common) case where a file either
        # isn't directly under src/main/java/ or already has no package.
        for java_relpath in (f for f in compile_known_files if f.endswith(".java")):
            java_abs_path = os.path.join(ctx.worktree_path, java_relpath)
            if not os.path.exists(java_abs_path):
                continue
            try:
                with open(java_abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    java_content = fh.read()
            except OSError:
                continue
            corrected_java = strip_package_declaration_matching_source_root(java_relpath, java_content)
            if corrected_java is not None:
                logger.info(
                    f"{java_relpath} sits directly under src/main/java/ with no subdirectory "
                    "nesting, so its package declaration is unconditionally invalid per Java's "
                    "own rules - deterministically stripping it to the default package."
                )
                with open(java_abs_path, "w", encoding="utf-8") as fh:
                    fh.write(corrected_java)

        compile_res = validator.run_compile_check(compile_known_files)
        if not compile_res["success"]:
            self_correction_result = None
            if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                from kriya.workflow.self_correction import run_self_correction_loop
                logger.info(
                    "Compile gate failed - attempting bounded self-correction "
                    "micro-loop before raising QualityGateFailure."
                )
                self_correction_result = await run_self_correction_loop(
                    llm=ctx.developer.llm,
                    worktree_path=ctx.worktree_path,
                    validator=validator,
                    files_in_scope=compile_known_files,
                    compile_error_output=compile_res["output"],
                    active_code_context=active_code_context,
                    max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                )
                for incident in getattr(self_correction_result, "incidents", []):
                    state.record_event(RunEvent(
                        kind="auxiliary.failed",
                        attempt=state.attempt_number,
                        source=incident["source"],
                        authority=EventAuthority.AUXILIARY,
                        message=incident["message"],
                        failure_type=incident["type"],
                        operation="repair_with_patch",
                    ))

            if self_correction_result and self_correction_result.resolved:
                logger.info(
                    "Self-correction micro-loop resolved the compile failure in "
                    f"{self_correction_result.turns_used} turn(s)."
                )
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "compile",
                    "success": True,
                    "output": self_correction_result.final_compile_output,
                    "self_corrected": True,
                    "self_correction_turns": self_correction_result.turns_used,
                    "self_correction_transcript": self_correction_result.transcript,
                })
            else:
                # Deterministic cross-milestone Java package-mismatch check -
                # found live, 2026-08-22 (ignite_qpid_protocol milestone
                # 3/4): a fresh milestone's Architect chose a Maven-
                # conventional package for its own new file while an earlier
                # milestone's established file stayed in the default
                # package - a `cannot find symbol` error that recurred
                # BYTE-FOR-BYTE IDENTICAL across 3+ retries, because a class
                # in one named package can never reference a class in a
                # different (or default) package under any circumstances -
                # not a missing import, a genuine language-level
                # incompatibility no amount of prose-level retrying can
                # resolve. See find_cross_package_symbol_mismatch()'s own
                # docstring (kriya/workflow/failure_grounding.py) for the
                # full incident, including how the Developer's own reasoning
                # actually diagnosed this correctly, more than once, then
                # talked itself out of fixing it every time. Checked only
                # after self-correction's own micro-loop (if enabled)
                # already had its chance and didn't resolve it, so this
                # never competes with or short-circuits that separate
                # recovery path.
                cross_package_failure = None
                java_files_for_mismatch = sorted(
                    f for f in set(state.all_files_written) | set(ctx.established_files)
                    if f.endswith(".java")
                )
                if java_files_for_mismatch:
                    mismatch = find_cross_package_symbol_mismatch(
                        compile_res.get("output", ""),
                        _build_workspace_type_index(state, ctx),
                        _build_java_package_map(java_files_for_mismatch, ctx),
                    )
                    if mismatch:
                        missing_symbol, referencing_path, candidate_path = mismatch
                        java_packages_for_message = _build_java_package_map(
                            [referencing_path, candidate_path], ctx
                        )
                        message = build_cross_package_mismatch_message(
                            missing_symbol, referencing_path,
                            java_packages_for_message.get(referencing_path),
                            candidate_path, java_packages_for_message.get(candidate_path),
                        )
                        cross_package_failure = Failure(
                            type="cross_package_symbol_mismatch",
                            message=message,
                            raw_output=compile_res.get("output", ""),
                            file_locations=[FileLocation(filepath=referencing_path), FileLocation(filepath=candidate_path)],
                            likely_files=[referencing_path, candidate_path],
                            failed_content=_capture_failed_content(
                                ctx.worktree_path, [referencing_path, candidate_path]
                            ),
                            attempt=state.attempt_number,
                        )
                compile_message = f"COMPILATION FAILURE:\n{compile_res['output']}"
                if not cross_package_failure:
                    # See find_locator_files_outside_known_scope()'s own docstring
                    # for the real live incident this closes, 2026-08-22
                    # (ignite_qpid_protocol): a workspace reused across two
                    # unrelated runs left stale files on disk that the compiler's
                    # own precise locator named but this run's tracking never
                    # heard of - surfacing that plainly here, rather than letting
                    # it silently degrade to "no known file implicated," turns 8
                    # wasted retries into one clear, actionable diagnostic.
                    unrecognized = find_locator_files_outside_known_scope(
                        compile_res.get("output", ""), compile_known_files,
                    )
                    if unrecognized:
                        compile_message += (
                            "\n\nNOTE: this error also references file(s) not tracked by this "
                            f"run at all: {', '.join(unrecognized)}. These are likely stale/"
                            "leftover content from an earlier, unrelated run or attempt still "
                            "sitting in the workspace - not something the current milestone's "
                            "own files can fix. If they don't belong, they should be removed "
                            "from the workspace rather than repeatedly retried against."
                        )
                failure = cross_package_failure or _build_quality_gate_failure(
                    "compile", compile_message,
                    compile_res.get("output", ""), ctx.worktree_path, compile_known_files, state.attempt_number,
                )
                if self_correction_result is not None:
                    # The loop ran but didn't resolve it within budget - persist
                    # what it tried (its transcript) instead of silently
                    # discarding it, so a real exhaustion is diagnosable from
                    # gate_outcomes/traces.db afterward rather than only visible
                    # in the process's own (possibly-rotated) log file.
                    failure.self_correction_attempt = {
                        "turns_used": self_correction_result.turns_used,
                        "transcript": self_correction_result.transcript,
                        "final_compile_output": self_correction_result.final_compile_output,
                    }
                state.gate_outcomes.append(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
        else:
            state.gate_outcomes.append({
                "attempt": state.attempt_number,
                "type": "compile",
                "success": True,
                "output": compile_res.get("output", "")
            })

        # A successful real compile is the authority for cross-file signature,
        # import, classpath, and build consistency. Record exact revisions only
        # after that gate; heuristics never mark a file validated.
        state.validated_file_revisions = {
            filepath: read_file_revision(os.path.join(ctx.worktree_path, filepath))
            for filepath in state.all_files_written
        }

        accepted_test_output: Optional[str] = None
        runnable_test_files = find_runnable_test_files(state.all_files_written)
        target_test = extract_target_test(state.error_context, list(state.all_files_written))
        if target_test:
            logger.info(f"Quality Gates: Running targeted tests: {target_test}")
            test_repair_result = None
            test_res = validator.run_tests(target_test=target_test)
            if not output_confirms_nonzero_test_execution(test_res.get("output", "")):
                # A targeted command that collected zero tests says the selector
                # did not identify an executable test; it says nothing about the
                # generated application.  Do not route this orchestration error to
                # the Developer as a source repair.  Retry once with the runner's
                # native suite discovery, which is the authoritative fallback.
                logger.warning(
                    "Quality Gates: Targeted test selection collected zero tests for "
                    f"{target_test}; retrying the full suite before attributing a code failure."
                )
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test_selection",
                    "success": False,
                    "output": test_res.get("output", ""),
                    "target_test": target_test,
                    "recovered_by": "full_suite",
                })
                target_test = None
                test_res = validator.run_tests()
                if not test_res["success"]:
                    failure = _build_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path,
                        state.all_files_written, state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", ""),
                    "selection_fallback": True,
                })
                accepted_test_output = test_res.get("output", "")

            if target_test and not test_res["success"]:
                if ctx.kernel.config.autonomy.self_correction_loop_enabled:
                    from kriya.workflow.self_correction import run_repair_loop
                    test_repair_result = await run_repair_loop(
                        llm=ctx.developer.llm,
                        worktree_path=ctx.worktree_path,
                        validator=validator,
                        files_in_scope=list(state.all_files_written),
                        compile_error_output=test_res["output"],
                        active_code_context=active_code_context,
                        max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                        failure_type="targeted_test",
                        target_test=target_test,
                    )
                    for incident in getattr(test_repair_result, "incidents", []):
                        state.record_event(RunEvent(
                            kind="auxiliary.failed", attempt=state.attempt_number,
                            source=incident["source"], authority=EventAuthority.AUXILIARY,
                            message=incident["message"], failure_type=incident["type"],
                            operation="repair_with_patch",
                        ))
                if test_repair_result and test_repair_result.resolved:
                    state.gate_outcomes.append({
                        "attempt": state.attempt_number,
                        "type": "targeted_test",
                        "success": True,
                        "output": test_repair_result.final_compile_output,
                        "self_corrected": True,
                        "self_correction_turns": test_repair_result.turns_used,
                        "self_correction_transcript": test_repair_result.transcript,
                    })
                    test_res = {"success": True, "output": test_repair_result.final_compile_output}
                else:
                    failure = _build_quality_gate_failure(
                        "targeted_test", f"TARGETED TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if test_repair_result is not None:
                        failure.self_correction_attempt = {
                            "turns_used": test_repair_result.turns_used,
                            "transcript": test_repair_result.transcript,
                            "final_validation_output": test_repair_result.final_compile_output,
                        }
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            if target_test and not (test_repair_result and test_repair_result.resolved):
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "targeted_test",
                    "success": True,
                    "output": test_res.get("output", "")
                })
            if target_test:
                accepted_test_output = test_res.get("output", "")
        else:
            if runnable_test_files:
                logger.info(f"Quality Gates: Executing tests for {validator.stack} stack...")
                test_res = validator.run_tests()
                if not test_res["success"]:
                    failure = _build_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    state.gate_outcomes.append(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
                state.gate_outcomes.append({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", "")
                })
                accepted_test_output = test_res.get("output", "")

        if accepted_test_output is None and goal_explicitly_requires_tests(ctx.goal):
            failure = Failure(
                type="test_acceptance",
                message=(
                    "TEST ACCEPTANCE FAILURE: the goal explicitly requires tests, "
                    "but no runnable test module was generated. Package initializers, "
                    "test-runner configuration, fixtures, and support files do not count as tests."
                ),
                raw_output="runnable_test_files=[]",
                likely_files=sorted(state.all_files_written),
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # The same acceptance rule applies whether extract_target_test() chose
        # one test or the validator ran the suite. Test-selection strategy must
        # never change an explicit user contract.
        if (
            accepted_test_output is not None
            and goal_explicitly_requires_tests(ctx.goal)
            and not output_confirms_nonzero_test_execution(accepted_test_output)
        ):
            failure = Failure(
                type="test_acceptance",
                message=(
                    "TEST ACCEPTANCE FAILURE: the goal explicitly requires tests, "
                    "but the configured test runner reported that zero tests executed."
                ),
                raw_output=accepted_test_output,
                likely_files=[
                    path for path in state.all_files_written
                    if "test" in path.lower() or "spec" in path.lower()
                ],
                attempt=state.attempt_number,
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # Quality Gates: Runtime Verification. Compiling and passing whatever tests
        # exist only proves the code is valid - it says nothing about whether it does
        # what the goal actually asked for, which matters most for goals with no test
        # suite at all. Judgment decides per-attempt whether this goal describes
        # self-terminating runtime behavior worth actually running and checking.
        autonomy_cfg_rv = ctx.kernel.config.autonomy
        if autonomy_cfg_rv.run_verification_enabled and not state.run_verification_declined:
            if state.cached_run_verification_judgment is None:
                pom_content_for_judge = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_judge = f.read()
                except Exception as e:
                    logger.debug(f"No pom.xml available for run-verification judgment: {e}")
                raw_judgment = await ctx.run_verifier.judge(
                    goal=ctx.goal,
                    design=ctx.design,
                    # Merge in ctx.established_files (see its own docstring) -
                    # same "known_files should span the whole workspace, not
                    # just this attempt's own writes" gap as the self-
                    # diagnosis attribution fix above, just at a different
                    # call site. Found live, 2026-08-21 (ignite_qpid_protocol
                    # milestone 3/4): App.java (this milestone's own file)
                    # imports Protocol and needs ProtocolParser, both written
                    # by EARLIER milestones - judge() only ever saw
                    # state.all_files_written (this milestone's own writes:
                    # applicationContext.xml, App.java), so it had no way to
                    # know those two files existed at all, and inferred a
                    # bare `java -cp . App` assuming pre-compiled classes
                    # (or, on a pom.xml-less project, a Maven-classpath
                    # command that also doesn't apply) instead of a real
                    # `javac App.java Protocol.java ProtocolParser.java &&
                    # java App` - 6 attempts straight rewrote perfectly
                    # correct application code chasing a ClassNotFoundException
                    # that was never a code bug, until the run's time budget
                    # was exhausted.
                    files_written=sorted(set(state.all_files_written) | set(ctx.established_files)),
                    build_file_content=pom_content_for_judge,
                )
                # Independent brutal review finding #2 (2026-08-15): don't trust
                # command_source="goal_explicit" as self-reported - verify it's
                # actually grounded in the goal text before it gets cached (and
                # before the approval-gate check below ever sees it), same
                # "never trust an LLM claim without an independent check" pattern
                # already applied to grade()'s likely_files. See
                # downgrade_ungrounded_goal_explicit_commands()'s own docstring
                # for the full reasoning.
                state.cached_run_verification_judgment = downgrade_ungrounded_goal_explicit_commands(
                    raw_judgment, ctx.goal
                )
            else:
                logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
            judgment = state.cached_run_verification_judgment
            # Deterministic Java entrypoint resolution - applied fresh every
            # attempt (never cached alongside judge()'s own should_run/
            # success_criteria decision above, unlike everything else on
            # this dict): a retry can edit file content between attempts, so
            # which file has the real entrypoint must be re-checked against
            # what's actually on disk right now, not a stale snapshot. See
            # ground_java_entrypoint_in_no_build_file_projects()'s own
            # docstring (kriya/workflow/file_resolution.py) for the full
            # incident and design - this replaces a free-form LLM guess with
            # a real javac+java sequence for a Java project with no pom.xml/
            # build.gradle, the one shape PolymorphicValidator's stack
            # detection has zero deterministic compile capability for today.
            # Applied here, BEFORE the human-in-the-loop approval display
            # below builds commands_desc from judgment["run_commands"] -
            # never correct AFTER a human already approved a different,
            # uncorrected command than what would actually execute.
            if judgment["should_run"] and judgment.get("run_commands"):
                pom_content_for_correction = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_correction = f.read()
                except Exception:
                    pass
                entrypoint_known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
                java_files = [f for f in entrypoint_known_files if f.endswith(".java")]
                if java_files and not pom_content_for_correction:
                    corrected_commands = ground_java_entrypoint_in_no_build_file_projects(
                        judgment["run_commands"],
                        judgment["command_source"],
                        entrypoint_known_files,
                        _build_java_main_class_map(java_files, ctx),
                        extract_jvm_module_flags(ctx.skills_prompt),
                        pom_content_for_correction,
                    )
                    if corrected_commands is None:
                        # ground_java_entrypoint_in_no_build_file_projects() returns None
                        # (distinct from "unchanged") only when it's already checked and
                        # confirmed zero of files_written has a real main() method, yet the
                        # judged command still tries to `java <SomeClass>` anyway - that
                        # class name is provably fabricated, not merely unverified, so
                        # trusting it would just repeat the exact live incident (a
                        # hallucinated "ProtocolParserTest" class) that motivated this
                        # fix. Force should_run False rather than execute a command known
                        # in advance to fail - matches judge()'s own system-prompt rule
                        # for a pure-library milestone with no runnable entrypoint at all.
                        logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and no known .java file has a main() method - overriding "
                            "should_run to False instead of executing a command that targets "
                            "a nonexistent entrypoint class."
                        )
                        judgment = dict(judgment)
                        judgment["should_run"] = False
                        judgment["run_commands"] = None
                    elif corrected_commands != judgment["run_commands"]:
                        logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and exactly one real entrypoint was detected - overriding the "
                            f"inferred run command(s) with {corrected_commands} instead of "
                            "trusting the model's own guess."
                        )
                        judgment = dict(judgment)
                        judgment["run_commands"] = corrected_commands
            if judgment["should_run"]:
                proceed_with_run = True
                if judgment["command_source"] == "inferred" and not state.run_verification_confirmed:
                    if autonomy_cfg_rv.mode == "human-in-the-loop":
                        commands_desc = "\n".join(
                            f"    {i}. {' '.join(cmd)}" for i, cmd in enumerate(judgment["run_commands"], 1)
                        )
                        confirm_reason = (
                            "Kriya judged that this goal describes runtime behavior compile/test "
                            "checks can't verify, and wants to actually run the generated app:\n"
                            f"  Command(s):\n{commands_desc}\n"
                            f"  Looking for: {judgment['success_criteria']}\n"
                            "Allow Kriya to execute these command(s) inside the sandboxed worktree?"
                        )
                        if ctx.approval_callback:
                            approved = ctx.approval_callback([], confirm_reason)
                            if asyncio.iscoroutine(approved):
                                approved = await approved
                            proceed_with_run = bool(approved)
                        else:
                            logger.warning("Runtime verification warrants human approval but no approval_callback is available. Proceeding under default policy.")
                    if not proceed_with_run:
                        state.run_verification_declined = True
                if proceed_with_run:
                    state.run_verification_confirmed = True
                    resolved_run_commands = [_resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
                    if resolved_run_commands != judgment["run_commands"]:
                        logger.info(
                            "One or more inferred run commands aren't resolvable as given here - "
                            "substituted Kriya's own interpreter/PATH-resolved equivalents."
                        )
                    jvm_flag_correction = _strip_jdk_incompatible_jvm_flags(ctx.worktree_path, state.java_home_override)
                    if jvm_flag_correction:
                        logger.warning(f"JVM flag preflight: {jvm_flag_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {jvm_flag_correction}"
                            if state.toolchain_warning else jvm_flag_correction
                        )
                    exec_pin_correction = _pin_exec_plugin_executable_to_resolved_jdk(ctx.worktree_path, state.java_home_override)
                    if exec_pin_correction:
                        logger.warning(f"JVM executable preflight: {exec_pin_correction}")
                        state.toolchain_warning = (
                            f"{state.toolchain_warning} {exec_pin_correction}"
                            if state.toolchain_warning else exec_pin_correction
                        )
                    logger.info(
                        "Quality Gates: Running runtime verification: "
                        + " && ".join(" ".join(cmd) for cmd in resolved_run_commands)
                    )
                    # Snapshot/clean around the actual execution, not just once
                    # per attempt - see clean_untracked_files_since()'s own
                    # docstring for the real incident (python_task_tracker,
                    # b-6/b-7) this closes: the worktree is deliberately reused
                    # across retry attempts (compile caches), but nothing ever
                    # cleaned up runtime state a PRIOR attempt's own verification
                    # run wrote to disk (a JSON store, a database) - so attempt
                    # N's run started from attempt N-1's leftover state instead
                    # of a fresh one, producing task-ID drift and "not found"
                    # failures that had nothing to do with the generated code.
                    pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
                    run_res = validator.run_app_sequence(
                        resolved_run_commands,
                        timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                    )
                    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
                    # Invalidate a stale cached judgment BEFORE grading/raising below,
                    # so a retry decision made from this failure already has a fresh
                    # judge() call queued for the NEXT attempt - not one attempt later.
                    # See run_command_targets_missing_entrypoint()'s own docstring for
                    # the live incident (a cached command kept targeting a test class
                    # that was never generated, unchanged across 6 straight attempts,
                    # even after the file layout changed specifically to try to
                    # satisfy it).
                    if run_command_targets_missing_entrypoint(run_res["output"]):
                        logger.warning(
                            "Runtime verification's command referenced a class/module "
                            "that doesn't exist - invalidating the cached judgment so "
                            "the next attempt re-infers a fresh one against the current "
                            "file layout instead of repeating this exact command."
                        )
                        state.cached_run_verification_judgment = None
                    gate_type = "run_verification"
                    # Set unconditionally (only ever reassigned in the "plain
                    # nonzero exit, no hang" branch below - see its own
                    # comment for why the other two branches are deliberately
                    # NOT self-corrected) so the shared failure-raising block
                    # further down can attach it to the Failure regardless of
                    # which branch actually ran, mirroring the compile gate's
                    # own self_correction_attempt pattern above.
                    self_correction_result = None
                    if run_res["timed_out"]:
                        # _run_cmd_with_timeout still reaps and captures whatever
                        # stdout/stderr the process produced before being killed (see
                        # kriya/tools/validate.py) - a forced kill does NOT mean nothing
                        # happened. Grading that captured output, same as a clean run,
                        # instead of short-circuiting straight to a flat "timed out"
                        # message, is what lets a genuinely-non-binary outcome surface.
                        # Confirmed live, 2026-08-04: a real Ignite/Qpid run printed its
                        # correct final "[RESULT]" output, then hung (an unclosed Ignite
                        # node's background threads kept the JVM alive) - the old flat
                        # message gave the retry loop zero signal, so every attempt kept
                        # trying timeout-tuning fixes that could never fix a genuine
                        # resource leak, burning the whole retry budget on the wrong
                        # class of change.
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (timed-out run)."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                                timed_out=True,
                            )
                        timeout_s = autonomy_cfg_rv.run_verification_timeout_seconds
                        if grade["passed"]:
                            # The goal's described behavior WAS genuinely produced -
                            # this is a categorically different defect than "wrong
                            # behavior": a self-terminating entrypoint that doesn't
                            # terminate is still a real bug (still fails this gate,
                            # still needs a retry), but the fix is almost always the
                            # resource lifecycle (see RESOURCE_LIFECYCLE_HEADER above),
                            # not the application logic that already produced the
                            # correct result - pointing the retry there directly,
                            # rather than at a generic timeout message, is the entire
                            # point of grading the captured output instead of skipping
                            # straight to a synthetic failure.
                            gate_type = "run_verification_hung"
                            grade["reasoning"] = (
                                f"The goal's described output WAS produced correctly, but the "
                                f"process never exited on its own and had to be killed after "
                                f"{timeout_s}s. This is still a real defect, not a false alarm - "
                                "almost always an unclosed resource (a connection, broker client, "
                                "executor, or similar) keeping the process alive after all "
                                "application logic already finished. Fix the resource lifecycle "
                                f"(see Resource Lifecycle above), not the application logic, which "
                                f"already works. Grader's evidence: {grade['reasoning']}"
                            )
                        else:
                            grade["reasoning"] = (
                                f"Run timed out after {timeout_s}s, and the output captured before "
                                f"the forced kill does not show the goal was achieved either: "
                                f"{grade['reasoning']}"
                            )
                        # A hang is always disqualifying regardless of what the
                        # captured-output grade concluded - only the message/gate_type
                        # above differ based on it.
                        grade["passed"] = False
                    elif not run_res["success"]:
                        # A non-final step failing can still leave the LAST step's
                        # returncode at 0 (every command runs regardless of an
                        # earlier step's exit code) - success reflects the whole
                        # sequence, not just the last command, so check that instead.
                        #
                        # Independent-review finding (2026-08-15): this branch used to
                        # short-circuit straight to a synthetic "one or more steps
                        # failed" message, never checking extract_contract_verdict()
                        # or calling grade() at all - the ONLY one of the three outcome
                        # branches (clean run / timed out / plain nonzero exit) that
                        # skipped both. A plain nonzero exit with no hang is plausibly
                        # the single most common real failure shape, and it's exactly
                        # the case VERIFICATION_CONTRACT_HEADER's marker convention is
                        # for (an entrypoint that detects its own failure and exits
                        # nonzero after printing "[VERIFICATION] FAIL: <reason>") - that
                        # rich, deterministic diagnosis was being silently discarded in
                        # favor of a generic "exit code 1", and likely_files was always
                        # empty here, giving the retry loop zero file-attribution signal
                        # for this failure class specifically (the exact gap grade()
                        # exists to close - see its own docstring). Fixed by mirroring
                        # the clean-run branch below exactly: check the deterministic
                        # marker first, only fall back to the LLM grader if the
                        # generated program didn't comply with the contract.
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading (non-zero exit, no hang)."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )

                        # Bounded self-correction, widened 2026-08-22 from
                        # compile-only to also cover THIS specific run-
                        # verification shape - found live (ignite_qpid_protocol):
                        # a plain nonzero exit with no hang ("Could not find or
                        # load main class App") is very often an
                        # infrastructure/classpath/build-layout problem, not
                        # application logic - exactly the class of thing
                        # inspect_class/list_dependencies/list_compiled_output
                        # (kriya/workflow/self_correction.py) exist to ground a
                        # fix in, instead of the Developer chasing phantom
                        # import/package theories across several full-set
                        # retries (5+ attempts, observed live). Deliberately
                        # NOT applied to the timed-out branch (a hang is a
                        # resource-lifecycle bug in application logic, not
                        # something inspect_class/list_dependencies can help
                        # diagnose) or the clean-run branch (exit 0 but wrong
                        # output is almost always an application-logic defect
                        # too) - see run_self_correction_loop()'s own
                        # docstring for the same scoping rationale.
                        if not grade["passed"] and ctx.kernel.config.autonomy.self_correction_loop_enabled:
                            from kriya.workflow.self_correction import run_self_correction_loop
                            logger.info(
                                "Runtime verification failed with a plain nonzero exit (no hang) - "
                                "attempting bounded self-correction micro-loop before raising "
                                "QualityGateFailure."
                            )
                            run_verification_known_files = sorted(
                                set(state.all_files_written) | set(ctx.established_files)
                            )
                            self_correction_result = await run_self_correction_loop(
                                llm=ctx.developer.llm,
                                worktree_path=ctx.worktree_path,
                                validator=validator,
                                files_in_scope=run_verification_known_files,
                                compile_error_output=(
                                    f"RUNTIME VERIFICATION FAILURE (plain nonzero exit): {grade['reasoning']}"
                                    f"\n\nCaptured output:\n{run_res['output']}"
                                ),
                                active_code_context=active_code_context,
                                max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                                failure_type="run_verification",
                            )
                            for incident in getattr(self_correction_result, "incidents", []):
                                state.record_event(RunEvent(
                                    kind="auxiliary.failed",
                                    attempt=state.attempt_number,
                                    source=incident["source"],
                                    authority=EventAuthority.AUXILIARY,
                                    message=incident["message"],
                                    failure_type=incident["type"],
                                    operation="repair_run_verification",
                                ))
                            if self_correction_result.resolved:
                                # Self-correction only ever validates via
                                # recompile (did the INFRASTRUCTURE issue get
                                # fixed) - it never re-runs the generated
                                # application itself (see the module's own
                                # docstring). Re-run the real run-verification
                                # sequence exactly once here to confirm actual
                                # behavior, reassigning run_res/grade/
                                # contract_verdict so the SHARED pass/fail
                                # handling below (and the success bookkeeping
                                # further down, reached only when grade
                                # ["passed"] is True) needs no duplication.
                                logger.info(
                                    "Self-correction micro-loop resolved the run-verification "
                                    f"infrastructure issue in {self_correction_result.turns_used} "
                                    "turn(s) - re-running the actual application to confirm."
                                )
                                pre_run_untracked_after_repair = snapshot_untracked_files(ctx.worktree_path)
                                run_res = validator.run_app_sequence(
                                    resolved_run_commands,
                                    timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                                )
                                clean_untracked_files_since(ctx.worktree_path, pre_run_untracked_after_repair)
                                contract_verdict = _extract_grounded_contract_verdict(
                                    run_res["output"], ctx.worktree_path, list(state.all_files_written),
                                )
                                if contract_verdict is not None:
                                    grade = contract_verdict
                                else:
                                    grade = await ctx.run_verifier.grade(
                                        goal=ctx.goal,
                                        success_criteria=judgment["success_criteria"],
                                        output=run_res["output"],
                                        returncode=run_res["returncode"],
                                        files_written=list(state.all_files_written),
                                    )
                    else:
                        contract_verdict = _extract_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                        if contract_verdict is not None:
                            logger.info(
                                "Runtime verification: using deterministic verification-contract "
                                "marker instead of LLM grading."
                            )
                            grade = contract_verdict
                        else:
                            grade = await ctx.run_verifier.grade(
                                goal=ctx.goal,
                                success_criteria=judgment["success_criteria"],
                                output=run_res["output"],
                                returncode=run_res["returncode"],
                                files_written=list(state.all_files_written),
                            )
                    if not grade["passed"]:
                        # A compile error always names its own broken file
                        # (file:[line,col]) - a runtime failure's captured
                        # output (broker banners, SLF4J lines with no .java
                        # suffix) structurally never does. RunVerifierAgent.grade()'s
                        # already-validated likely_files (grade.get("likely_files"),
                        # absent on the two synthetic timed-out/step-failed grades
                        # built above, only present from a real grade() call) is
                        # passed straight into Failure.likely_files as
                        # extra_likely_files - no more stringify-into-the-message-
                        # then-re-derive-via-regex round-trip.
                        message = (
                            f"RUNTIME VERIFICATION FAILURE: {grade['reasoning']}"
                            f"\n\nCaptured output:\n{run_res['output']}"
                        )
                        # Append the grader's reasoning to what gets PERSISTED
                        # (Failure.raw_output -> to_gate_outcome()'s "output"
                        # field), mirroring the PASSED path a few lines below
                        # (which already does this) - found live, 2026-08-11
                        # (kriya-oneshot-protocol-ignite-qpid audit): without
                        # this, to_gate_outcome() prefers raw_output over
                        # message whenever raw_output is non-empty, so the
                        # grader's diagnosis (embedded in message, used only
                        # for the in-memory retry prompt) was silently absent
                        # from every persisted FAILED run_verification
                        # gate_outcome/traces.db record, even though it was
                        # computed and even though the identical PASSED case
                        # persists it - a real debugging/forensics asymmetry,
                        # confirmed directly against a real trace.
                        enriched_output = run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}"
                        failure = _build_quality_gate_failure(
                            gate_type, message, enriched_output,
                            ctx.worktree_path, state.all_files_written, state.attempt_number,
                            extra_likely_files=grade.get("likely_files") or [],
                        )
                        if self_correction_result is not None:
                            # The loop ran (only ever possible from the plain-
                            # nonzero-exit branch above) but didn't leave the
                            # gate passing - either it never resolved, or it
                            # resolved the infrastructure issue but the
                            # re-run still failed for a genuine application-
                            # logic reason. Persist what it tried either way,
                            # same forensics reasoning as the compile gate's
                            # identical pattern above.
                            failure.self_correction_attempt = {
                                "turns_used": self_correction_result.turns_used,
                                "transcript": self_correction_result.transcript,
                                "final_compile_output": self_correction_result.final_compile_output,
                            }
                        state.gate_outcomes.append(failure.to_gate_outcome())
                        raise QualityGateFailure(failure)
                    run_verification_outcome = {
                        "attempt": state.attempt_number,
                        "type": "run_verification",
                        "success": True,
                        "output": run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}",
                        # Reachable via the clean-run branch above, or (since
                        # 2026-08-22) the plain-nonzero-exit branch's own
                        # self-correction re-verification - the timed-out
                        # branch always forces grade["passed"] = False, so it
                        # can never reach here - contract_verdict is
                        # guaranteed in scope either way. Makes deterministic-
                        # contract-vs-LLM-grader compliance queryable directly
                        # from traces.db instead of grepping raw stdout logs
                        # by hand, which is what diagnosing the underlying
                        # reliability gap required this session, repeatedly.
                        "graded_by": "contract" if contract_verdict is not None else "llm",
                    }
                    if self_correction_result is not None and self_correction_result.resolved:
                        # Same markers the compile gate's own self-correction
                        # success path already records - lets Pillar 3's
                        # lesson-extraction trigger (kriya/workflow/workflow.py)
                        # find this outcome and feed the real transcript
                        # (diagnosis + before/after + verification) into
                        # LiveFailureChannel.extract() as richer evidence than
                        # bare error_context/file_contents.
                        run_verification_outcome["self_corrected"] = True
                        run_verification_outcome["self_correction_turns"] = self_correction_result.turns_used
                        run_verification_outcome["self_correction_transcript"] = self_correction_result.transcript
                    state.gate_outcomes.append(run_verification_outcome)
                    logger.info(f"Quality Gates: Runtime verification PASSED: {grade['reasoning']}")
                    # A passing real-world run is exactly the proof the
                    # skill-verification gap check is looking for - mark every
                    # skill that contributed to this generation as verified so
                    # future runs stop asking about it.
                    for active_skill_name in ctx.active_skills:
                        try:
                            active_skill_obj = ctx.skill_engine.get_skill(active_skill_name)
                            context = _skill_verification_context(active_skill_obj, ctx.goal)
                            ctx.skill_engine.mark_verified(active_skill_name, context=context)
                            # Also flip per-rule provenance for exactly the
                            # rules that were part of this skill when this
                            # run's context was built (the pre-retry-loop
                            # snapshot) - not whatever rules.txt contains now.
                            if active_skill_obj.source_path and active_skill_name in ctx.active_skill_rules_snapshot:
                                from kriya.skills.skill import mark_rules_verified
                                mark_rules_verified(active_skill_obj.source_path, ctx.active_skill_rules_snapshot[active_skill_name])
                        except Exception as ex:
                            logger.debug(f"Failed to mark skill '{active_skill_name}' verified: {ex}")

    # Quality Gates: Goal Spec Compliance. Compiling, passing tests, and (when
    # applicable) runtime verification above all structurally can't catch a goal's
    # LITERALLY named requirement (an exact field/method/class name, an exact type,
    # an exact constant) going unimplemented - the generated code can be perfectly
    # valid and even behave correctly while still being a different shape than what
    # was actually asked for. Runs once, here, only after every other gate has
    # already passed, so an attempt doomed for another reason never pays for it.
    # See SpecComplianceAgent's own docstring (kriya/agents/agent.py) for the live
    # incident (ignite_qpid_protocol milestone 1, 2026-08-21) this closes.
    if ctx.kernel.config.autonomy.spec_compliance_enabled:
        spec_check_files = sorted(state.all_files_written)
        spec_file_contents: Dict[str, str] = {}
        for spec_path in spec_check_files:
            spec_full_path = os.path.join(ctx.worktree_path, spec_path)
            if not os.path.exists(spec_full_path):
                spec_full_path = os.path.join(ctx.workspace_path, spec_path)
            if not os.path.exists(spec_full_path):
                continue
            try:
                with open(spec_full_path, "r", encoding="utf-8", errors="replace") as fh:
                    spec_file_contents[spec_path] = fh.read()
            except Exception as e:
                logger.debug(f"Spec compliance check: couldn't read {spec_path}, skipping it: {e}")
        spec_result = await ctx.spec_compliance.check(
            goal=ctx.goal, files_written=spec_check_files, file_contents=spec_file_contents,
        )
        if not spec_result["compliant"]:
            missing_desc = "; ".join(spec_result["missing_requirements"]) or spec_result["reasoning"]
            message = (
                "GOAL SPEC COMPLIANCE FAILURE: the goal names concrete requirements the "
                f"generated code doesn't satisfy: {missing_desc}\n\n{spec_result['reasoning']}"
            )
            failure = _build_quality_gate_failure(
                "goal_spec_compliance", message, message,
                ctx.worktree_path, state.all_files_written, state.attempt_number,
                extra_likely_files=spec_result.get("likely_files") or [],
            )
            state.gate_outcomes.append(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        state.gate_outcomes.append({
            "attempt": state.attempt_number,
            "type": "goal_spec_compliance",
            "success": True,
            "output": spec_result["reasoning"],
        })
        logger.info(f"Quality Gates: Goal spec compliance PASSED: {spec_result['reasoning']}")

    # If we made it here, Quality Gates passed successfully!
    log_quality_gate_banner("PASSED", state.attempt_number)
