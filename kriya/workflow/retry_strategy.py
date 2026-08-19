"""Failure-handling for the Developer + Quality Gates retry loop - the other
half of the attempt/failure cycle kriya/workflow/attempt.py's run_attempt()
covers. Extracted verbatim from kriya/workflow/workflow.py's
run_generation_workflow() (2026-08-11, Opportunity 2 Slice 3).

Deliberately NOT split into a smaller "pure decision" function plus several
inline leftovers, despite that being the original plan for this slice: the
real code interleaves classification (Failure/attempt_mode/fail_type),
error-triggered live lookup (network + human-approval-gated), LSP grounding
(a persistent client's lifecycle), retry-budget accounting, and the final
terminal/continue decision into one continuous sequence with real data
dependencies flowing through - state.last_attempt_mode set before this even
runs, current_failure_signature feeding both the live-lookup gate AND the
budget's last_failure_signature field. Carving out only the "pure" parts and
leaving the rest inline would have meant re-deriving values already computed
here, exactly the class of bug that made Slice 2 non-trivial - moving it as
one cohesive, thoroughly-tested unit was the safer call once the actual
shape of the code was read in full, not assumed from an earlier line-range
estimate.
"""
import logging
import os

from kriya.workflow.attribution import _detect_missing_build_manifest, attribute_failure, read_worktree_file
from kriya.workflow.failure import Failure
from kriya.workflow.failure_grounding import (
    _build_error_source_context,
    _normalize_error_for_repeat_detection,
    classify_environment_failure,
    extract_error_search_terms,
)
from kriya.workflow.file_resolution import IncompleteGenerationError
from kriya.workflow.live_lookup import _augment_error_with_live_lookup
from kriya.workflow.lsp_integration import _build_lsp_diagnostics_context, _get_or_start_jdtls_client
from kriya.workflow.state import GenerationState
from kriya.workflow.worktree import remove_git_worktree
from kriya.workflow.retry_policy import RetryAction, decide_for_state

logger = logging.getLogger(__name__)


async def handle_attempt_failure(state: GenerationState, ctx, e: Exception) -> bool:
    """Called from workflow.py's `except Exception as e:` after a failed
    run_attempt() call. Mutates state (error_context, last_implicated_files/
    last_missing_files, last_error_source_context, jdtls_client/
    jdtls_unavailable/lsp_warning, budgets, gate_outcomes, environment_failure,
    final_attempt_contents) and returns whether the retry loop should
    explicitly `break` now (True only for an environment/toolchain failure -
    genuine budget exhaustion is left to the `while` loop's own condition
    going False on its next check, matching the original inline code's exact
    control flow rather than introducing a new signal for it)."""
    raw_error_context = str(e)
    # "full_set" -> "full-set" to match this log line's original
    # wording exactly; the other three modes were already hyphen-free.
    attempt_mode = "full-set" if state.last_attempt_mode in (None, "full_set") else state.last_attempt_mode
    logger.warning(
        f"Quality Gates FAILED (Attempt {state.attempt_number}, "
        f"{attempt_mode}, full-set {state.budgets.retry_count}/{ctx.max_retries} + "
        f"targeted {state.budgets.targeted_retry_count}/{ctx.targeted_max_retries}): {e}"
    )

    # Every failure source now raises with a real Failure object attached
    # (QualityGateFailure.failure directly, or IncompleteGenerationError.failure
    # for backward compat - see kriya/workflow/failure.py) instead of a bare
    # message string later re-sniffed for its type by prefix-matching. A bare
    # Exception (shouldn't normally happen - defensive only) is wrapped the same
    # way so everything downstream always reads one shape.
    failure: Failure = getattr(e, "failure", None) or Failure(
        type="general_error", message=raw_error_context, raw_output=raw_error_context,
        source="orchestrator",
    )
    failure.attempt = state.attempt_number
    failure.mode = attempt_mode
    state.record_failure(failure, operation=attempt_mode)
    fail_type = failure.type
    is_incomplete_generation = isinstance(e, IncompleteGenerationError)

    # Error-triggered live lookup: only for a REPEATED failure (same
    # fail_type + same extracted tool/library terms, or identical raw
    # text if none were extracted) - a first-time failure resolves
    # normally most of the time and doesn't need it. Scoped to
    # compile/run_verification failures, since those are the ones most
    # likely to be a generic tooling/config gap a search can actually
    # resolve (a test-assertion failure is usually application-logic-
    # specific, not something external docs fix). Terms are extracted
    # via a hard, code-enforced regex restricted to Maven/Gradle-style
    # groupId:artifactId coordinates found IN the error text, plus
    # (separately, safety-bounded via the worktree's own declared
    # dependencies below) a wrong-import-path shape - the same
    # query-safety boundary as the existing goal/design-stage live
    # lookup: never the raw error/stack-trace text itself, which can
    # contain project-specific class/variable names. When neither
    # matches (e.g. a plain Java stack trace), the raw text itself
    # is the fallback signature - normalized first to strip Maven's
    # own always-different build-timing lines, or two occurrences
    # of the exact same failure would never compare equal.
    state.environment_failure = classify_environment_failure(raw_error_context)

    # Read fresh from the worktree's CURRENT pom.xml each attempt,
    # not cached once before the loop - the project's own
    # groupId:artifactId can legitimately change mid-run (e.g. the
    # Developer renaming the artifact while extending the project),
    # and a stale cached value would fail to exclude Maven's own
    # build banner for whatever the CURRENT attempt actually named
    # the project.
    own_project_coordinate = None
    worktree_dependency_coordinates = None
    try:
        from kriya.tools.validate import get_pom_dependencies, get_pom_own_coordinate
        worktree_pom_path = os.path.join(ctx.worktree_path, "pom.xml")
        own_project_coordinate = get_pom_own_coordinate(worktree_pom_path)
        worktree_dependency_coordinates = get_pom_dependencies(worktree_pom_path) or None
    except Exception as ex:
        logger.debug(f"Failed to resolve project's own pom.xml coordinate/dependencies: {ex}")
    error_terms = extract_error_search_terms(
        raw_error_context,
        exclude_coordinates=[own_project_coordinate] if own_project_coordinate else None,
        dependency_coordinates=worktree_dependency_coordinates,
    )
    current_failure_signature = (
        fail_type,
        tuple(sorted(error_terms)) if error_terms else _normalize_error_for_repeat_detection(raw_error_context),
    )
    state.error_context = raw_error_context
    if (
        current_failure_signature == state.budgets.last_failure_signature
        # unaddressed_error_location included alongside compile: it's a
        # cheaper, earlier-firing variant of the same signal (the model's
        # own edit didn't address the reported location) - repeating it
        # is just as strong a "this model is stuck" signal as a repeated
        # compile failure, and today's repeat-based gate is the only
        # thing standing between a first occurrence and live lookup, so
        # it should fire on the same schedule, not a slower one.
        and fail_type in ("compile", "run_verification", "run_verification_hung", "unaddressed_error_location")
        and ctx.kernel.config.autonomy.web_lookup_enabled
        and ctx.kernel.config.search.base_url
        and error_terms
        and await ctx.approve_web_lookup(error_terms, ctx.kernel.config.search.base_url, ctx.web_lookup_query_callback)
    ):
        state.error_context = await _augment_error_with_live_lookup(
            raw_error_context, error_terms,
            ctx.kernel.config.search.base_url, ctx.kernel.config.search.top_k
        )
    state.budgets.last_failure_signature = current_failure_signature

    # Re-evaluate which file(s) THIS failure implicates/is missing -
    # independent of whether this attempt was itself targeted, missing-
    # files, or full-set, so any attempt's failure can still kick off
    # (or redirect) a scoped retry afterward. An IncompleteGenerationError
    # sets last_missing_files and clears last_implicated_files (the
    # missing file, by definition, was never written, so it can never
    # appear in all_files_written for extract_implicated_files to find);
    # any other failure does the reverse - the two trackers are mutually
    # exclusive per attempt, matching that they route to different,
    # differently-built retry prompts.
    if is_incomplete_generation:
        state.last_missing_files = e.missing_files
        state.last_implicated_files = None
    else:
        # attribute_failure() (kriya/workflow/attribution.py) is the single
        # decision point for "which file(s) is this failure about" - replaces
        # the old inline "failure.likely_files or extract_implicated_files(...)"
        # fallback with a tiered ladder that also covers the case neither of
        # those two sources ever handled: a deterministic verification-
        # contract FAIL, which deliberately carries no locator at all (see
        # extract_contract_verdict()'s own docstring) and used to fall
        # straight through to a blind full-set walk. Reuses
        # ctx.developer.llm (the same configured LLMClient generation
        # already uses) for its triage tier, and state.budgets.retry_count/
        # ctx.chain so that tier rides the exact same model-escalation
        # ladder the current attempt is already on.
        # Only trust a self-diagnosis (kriya/workflow/attribution.py's
        # extract_self_diagnosed_files(), captured in attempt.py right after
        # the attempt that produced it) when THIS failure is a CONFIRMED
        # repeat of the exact failure that diagnosis was responding to -
        # current_failure_signature was just computed fresh above, before
        # state.budgets.last_failure_signature gets overwritten to it at
        # line 142. A stale diagnosis from an unrelated, different failure
        # must never override a fresh locator.
        self_diagnosed_files = None
        if state.last_self_diagnosis and state.last_self_diagnosis[0] == current_failure_signature:
            self_diagnosed_files = state.last_self_diagnosis[1]

        attribution = await attribute_failure(
            failure,
            sorted(state.all_files_written),
            state.budgets.retry_count,
            ctx.chain,
            ctx.developer.llm,
            lambda fp: read_worktree_file(ctx.worktree_path, fp),
            self_diagnosed_files=self_diagnosed_files,
        )
        implicated = attribution.files
        state.last_attribution = attribution
        # failure.likely_files must be overwritten with the FINAL attribution
        # result, not left at whatever _build_quality_gate_failure() computed
        # at construction time (its own internal extract_implicated_files()
        # call, before this module's self_diagnosis/triage tiers ever ran) -
        # found via a live test failure, not assumed: the actual retry
        # targeting already correctly used the local `implicated` var above,
        # but the PERSISTED gate_outcome's likely_files silently stayed
        # stale, same class of bug as the attribution_tier fix below.
        failure.likely_files = implicated
        failure.attribution_tier = attribution.tier
        failure.attribution_confidence = attribution.confidence
        failure.attribution_reasoning = attribution.reasoning
        # For a QualityGateFailure type that appends its own gate_outcome at
        # the RAISE SITE (compile/test/regression_test/run_verification/
        # anchored_edit, all inside attempt.py) - that append already
        # happened, with a to_gate_outcome() snapshot taken BEFORE this
        # attribution ever ran, so likely_files/attribution_tier/confidence/
        # reasoning would silently stay stale/None in the persisted
        # gate_outcome despite being set on the Failure object right above.
        # Confirmed via a live test failure, not assumed: patch the
        # already-appended entry for THIS attempt in place rather than
        # relying on to_gate_outcome() being called again - the
        # de-dup-guarded append further below already handles the other
        # case (general_error, which never appends at a raise site)
        # correctly on its own.
        for outcome in state.gate_outcomes:
            if outcome.get("attempt") == state.attempt_number and outcome.get("type") == fail_type:
                outcome["likely_files"] = failure.likely_files
                outcome["attribution_tier"] = failure.attribution_tier
                outcome["attribution_confidence"] = failure.attribution_confidence
                outcome["attribution_reasoning"] = failure.attribution_reasoning
        # A missing build manifest the Architect never asked for at all
        # (see _detect_missing_build_manifest) takes priority over normal
        # implication scoping - extract_implicated_files() can never name
        # a file that was never written and never mentioned in the error
        # text, so without this check a "package X does not exist" error
        # would keep re-targeting the files that DO exist (which already
        # correctly declined to fix a dependency problem outside their
        # own scope) forever, rather than ever generating the one file
        # that would actually fix it.
        missing_manifest = (
            _detect_missing_build_manifest(ctx.worktree_path, raw_error_context)
            if fail_type == "compile" else None
        )
        if missing_manifest:
            state.last_missing_files = [missing_manifest]
            state.last_implicated_files = None
        else:
            state.last_implicated_files = implicated if implicated else None
            state.last_missing_files = None

    # Generic across ANY compile error shape (see
    # extract_error_source_locations) - the exact broken source
    # line(s), read fresh from the worktree, keyed by file so the
    # next retry's per-file prompt shows this only to the file(s)
    # actually implicated, not broadcast to every file in a
    # full-set batch (same scoping fix as prior_error_context
    # below).
    state.last_error_source_context = _build_error_source_context(
        ctx.worktree_path, raw_error_context, state.all_files_written
    )

    # LSP grounding (Java only, silently skipped if jdtls isn't
    # installed or this isn't a Maven project) - deterministic,
    # real-classpath ground truth for the same implicated file(s),
    # merged directly into the existing error_source_context dict
    # so it reaches the retry prompt through the same, already-
    # scoped injection point rather than needing new plumbing.
    if not state.jdtls_unavailable and os.path.exists(os.path.join(ctx.worktree_path, "pom.xml")):
        state.jdtls_client = await _get_or_start_jdtls_client(state.jdtls_client, ctx.worktree_path)
        if state.jdtls_client is None:
            state.jdtls_unavailable = True
            if state.lsp_warning is None:
                from kriya.tools.lsp import find_jdtls as _find_jdtls_for_warning
                if _find_jdtls_for_warning():
                    state.lsp_warning = (
                        "jdtls was found on PATH but failed to start - LSP grounding was "
                        "unavailable for the rest of this run (see logs for the startup error)."
                    )
                    logger.warning(f"LSP preflight: {state.lsp_warning}")
        else:
            lsp_context = await _build_lsp_diagnostics_context(
                state.jdtls_client, ctx.worktree_path,
                state.last_implicated_files if state.last_implicated_files else state.all_files_written,
            )
            for lsp_filepath, lsp_text in lsp_context.items():
                state.last_error_source_context[lsp_filepath] = (
                    state.last_error_source_context.get(lsp_filepath, "") + lsp_text
                )

    if state.last_attempt_mode in ("targeted", "missing_files"):
        state.budgets.targeted_retry_count += 1
    elif state.last_attempt_mode == "fallback_targeted":
        # Deliberately counts against NEITHER budget - it's a genuinely
        # separate, one-shot step (fallback_targeted_attempted, already
        # set True at the branch entry above, is what prevents this from
        # ever firing twice), not a full-set attempt or an extension of
        # the primary-model-only targeted budget.
        pass
    else:
        state.budgets.retry_count += 1

    # De-dup fallback: a QualityGateFailure-sourced failure already appended
    # its own gate_outcome at the raise site (via failure.to_gate_outcome()),
    # so this only ever fires for a source that never reaches a try-block
    # append - chiefly IncompleteGenerationError, plus the general_error
    # defensive path.
    if not any(o.get("attempt") == state.attempt_number and o.get("type") == fail_type for o in state.gate_outcomes):
        state.gate_outcomes.append(failure.to_gate_outcome())

    retry_decision = decide_for_state(
        state, max_retries=ctx.max_retries,
        targeted_max_retries=ctx.targeted_max_retries,
        has_fallback_model=bool(ctx.chain),
    )
    budgets_exhausted = not retry_decision.should_continue
    if budgets_exhausted:
        if retry_decision.action is RetryAction.STOP_ENVIRONMENT:
            logger.error(f"Quality Gates stopped early - {state.environment_failure}")
        else:
            logger.error("Quality Gates exceeded maximum debug retries (full-set and targeted). Continuing to review with errors.")
        if ctx.worktree_path != ctx.workspace_path:
            for filepath in state.all_files_written:
                worktree_file = os.path.join(ctx.worktree_path, filepath)
                try:
                    with open(worktree_file, "r", encoding="utf-8", errors="replace") as fh:
                        state.final_attempt_contents[filepath] = fh.read()
                except Exception as e:
                    logger.debug(f"Failed to capture final content of '{worktree_file}' before worktree cleanup: {e}")
            remove_git_worktree(ctx.workspace_path, ctx.worktree_path)
        # An environment/toolchain failure needs an explicit break -
        # unlike genuine budget exhaustion (which naturally coincides
        # with the `while` loop's own condition going False on its next
        # check), this can fire on the very first attempt, well before
        # retry_count reaches max_retries, and the loop would otherwise
        # continue straight into another pointless Developer retry.
        if retry_decision.action is RetryAction.STOP_ENVIRONMENT:
            return True
    return False
