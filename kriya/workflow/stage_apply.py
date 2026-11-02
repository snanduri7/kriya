"""Stages 6-7 of a work unit and the pre-apply gates: the approval gate, the terminal apply and the lessons (full
modularization W4, E4).

Moved verbatim from ``kriya/workflow/workflow.py``:

- ``run_static_analysis_gate`` / ``run_semantic_safety_gate``: the PRD-031A static-analysis gate and the
  BACKEND-READINESS-V2 exception-masking rule on the exact batch the terminal apply commits (the facade keeps
  ``_run_static_analysis_gate`` / ``_run_semantic_safety_gate`` as forwarders that pass its own collaborators, so a
  patch of ``kriya.workflow.workflow._direct_terminal_writes`` still reaches them);
- ``approval_gate``: 4.5, the Pre-Apply Human Approval Gate (the change set, the human-in-the-loop / sensitive-path /
  diff-size / process-profile triggers, the pre-approval review (``stage_review``), the abort that never applies,
  the approval request);
- ``prepare_lessons``: the dependency-skill auto-accrual and the hard-won-knowledge trigger, where the attempt
  computed them (before the terminal regression);
- ``apply_terminal_candidate``: the terminal apply through the shared commit seam, the attempt's success record and the
  sandbox release;
- ``extract_lessons``: the advisory live-failure lesson extraction after authoritative success.

The Developer loop that calls them (stage 4: candidate gates, the held executing gates, the terminal regression)
stays in the engine until E5 and hands its outputs in through ``StageContext`` and the arguments. Collaborators
arrive in ``StageDeps`` (``kriya/workflow/stage_context.py``); this module never imports ``kriya.workflow.workflow``.
"""
from __future__ import annotations

import difflib
import functools
import os
import re
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from kriya.config.authority import is_human_in_the_loop
from kriya.control.run_coordinator import annotate_run
from kriya.policy.approval import ApprovalDecision, resolve_approval
from kriya.repository.atomic_write import atomic_write_file
from kriya.repository.commit import CandidateMaterializationError
from kriya.workflow.acceptance import output_confirms_nonzero_test_execution
from kriya.workflow.banners import log_gate_banner
from kriya.workflow.contract_lifecycle import CONTRACT_REGISTRY_STOP_REASON_CODES
from kriya.workflow.exception_masking import find_masking_in_batch
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.outcome import failure_report_dicts
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_lifecycle import TerminalTrace, TraceDetail, persist_run_trace
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.stage_review import pre_approval_review
from kriya.workflow.worktree import remove_git_worktree

RunState = Any   # kriya.workflow.state.GenerationState (kriya/workflow/stage_context.py says why it is not imported)
# The approval.decision event's `outcome` vocabulary (PRD-033; recorded traces keep it): a decline is spelled "rejected" there.
_APPROVAL_EVENT_OUTCOME = {ApprovalDecision.APPROVED: "approved", ApprovalDecision.DECLINED: "rejected", ApprovalDecision.UNAVAILABLE: "unavailable"}


def run_static_analysis_gate(
    cfg: Any, state: RunState, *, worktree_path: str, workspace_path: str, run_id: str, unit_id: str,
    StaticAnalysisService: Any, StaticAnalysisCandidate: Any, banner: Callable[[Any], str],
    _direct_terminal_writes: Callable[..., Any], _captured_originals: Callable[[RunState], Dict[str, Optional[bytes]]],
) -> None:
    """PRD-031A at the direct/milestone pre-apply boundary: the same StaticAnalysisService the enforce
    TerminalGateService uses. The keyword collaborators are the facade's names, passed by its forwarder."""
    in_place = worktree_path == workspace_path
    result = StaticAnalysisService(cfg).evaluate_candidate(StaticAnalysisCandidate(
        materialize=lambda: _direct_terminal_writes(worktree_path, workspace_path, state),
        workspace_path=workspace_path, run_id=run_id, unit_id=unit_id,
        in_place=in_place,
        # STATIC-ANALYSIS-INPLACE-BASELINE-001: the exact pre-write bytes the attempt captured are the PRE side.
        originals=_captured_originals(state) if in_place else None,
    ))
    state.static_analysis_result = result
    state.record_event(RunEvent(
        kind="static_analysis.result", attempt=state.attempt_number, source="static_analysis_gate",
        authority=EventAuthority.AUTHORITATIVE, message=banner(result), details=result.summary(),
    ))
    if result.permits_commit:
        return
    message = result.gap or f"STATIC_ANALYSIS_{result.outcome.value}"
    state.environment_failure = message
    failure = Failure(
        type=f"static_analysis_{result.outcome.value.lower()}", message=message, raw_output=message,
        source="static_analysis_gate", authority="deterministic", attempt=state.attempt_number,
        diagnostics={
            "reason_code": f"STATIC_ANALYSIS_{result.outcome.value}",
            "reason_codes": list(result.reason_codes), "evidence_digest": result.evidence_digest,
        },
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def run_semantic_safety_gate(
    state: RunState, *, worktree_path: str, workspace_path: str, _direct_terminal_writes: Callable[..., Any],
) -> None:
    """BACKEND-READINESS-V2 at the direct/milestone pre-apply boundary: the exception-masking rule on the exact batch the
    terminal apply commits (the enforce terminal's ``semantic_safety`` gate, same decision), against the bytes the run
    first read. It judges the final state, so a masking result is refused whichever route left it in the candidate. A
    finding is a deterministic stop of the same kind as the staging guard's: fix the cause, do not widen a catch."""
    try:
        writes = _direct_terminal_writes(worktree_path, workspace_path, state)
    except CandidateMaterializationError:
        return  # no bindable candidate: the terminal apply refuses it (VERIFIED_CANDIDATE_EVIDENCE_MISSING)

    def run_start_text(write: Any) -> Optional[str]:
        return state.all_original_contents.get(os.path.relpath(write.target_path, workspace_path))

    findings = find_masking_in_batch(writes, run_start_text)
    if not findings:
        return
    message = "SEMANTIC SAFETY VIOLATED (final candidate): " + "; ".join(
        f"{os.path.relpath(target, workspace_path)}: {found}" for target, found in findings)
    failure = Failure(
        type="unsafe_exception_masking", message=message, raw_output=message,
        file_locations=[FileLocation(filepath=os.path.relpath(target, workspace_path)) for target, _ in findings],
        likely_files=[os.path.relpath(target, workspace_path) for target, _ in findings],
        attempt=state.attempt_number, source="semantic_safety_gate", authority="deterministic",
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


ARTIFACT_REGISTRY_CANDIDATE_DERIVATION_FAILED = "ARTIFACT_REGISTRY_CANDIDATE_DERIVATION_FAILED"


def milestone_registry_unit(work_unit: Any) -> Optional[str]:
    """The milestone id whose artifact registry this unit must be able to publish, else None. Only a MILESTONE unit that is
    not the plan-level integration unit has one; an enforce subtask keeps the TerminalGateService's own artifact_registry
    gate, and a direct run has no registry."""
    from kriya.workflow.execution_plan import PlanSourceKind

    if work_unit is None or work_unit.source_kind is not PlanSourceKind.MILESTONE or work_unit.requirement_goal is not None:
        return None
    return work_unit.work_unit_id


def run_artifact_registry_gate(state: RunState, *, worktree_path: str, work_unit: Any, workspace_path: Optional[str] = None) -> None:
    """Gate F (owner ruling 2026-11-01), at the milestone pre-apply boundary: the canonical
    ``ArtifactRegistry.derive_from_workspace`` runs on the CANDIDATE before the terminal apply, exactly as the enforce
    terminal derives from its candidate before its commit. A failure is a deterministic stop (an I/O-class exception is an environment fault; an
    indeterminate present manifest is a bounded Developer retry, never a commit) and the real workspace is untouched.
    Validation only: nothing is persisted here; the registry becomes authoritative only through
    ``milestones.settle_artifact_registry`` after the source commit, bound to it."""
    milestone_id = milestone_registry_unit(work_unit)
    if milestone_id is None:
        return
    from kriya.control.artifacts import ArtifactDerivationIndeterminate, ArtifactRegistry, registry_digest
    from kriya.control.persistence import load_artifact_registry

    try:
        # The REAL workspace's recorded registry says which artifacts are expected (an artifact recorded earlier whose descriptor
        # the candidate leaves present but underivable is indeterminate); a missing store is an empty registry, an unreadable one fails closed.
        registry = load_artifact_registry(workspace_path) if workspace_path is not None else ArtifactRegistry()
        derived = registry.derive_from_workspace(worktree_path, milestone_id)
    except Exception as error:
        # ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001: a present canonical manifest whose artifact cannot be derived is NOT
        # an environment fault: the candidate (or the repository it extends) carries the unusable manifest, so the Developer is
        # told exactly which one and the ordinary bounded retry applies. Any other exception is an environment fault.
        indeterminate = isinstance(error, ArtifactDerivationIndeterminate)
        if indeterminate:
            message, reason_code = str(error), error.reason_code
        else:
            message, reason_code = f"{ARTIFACT_REGISTRY_CANDIDATE_DERIVATION_FAILED}: {type(error).__name__}: {error}", ARTIFACT_REGISTRY_CANDIDATE_DERIVATION_FAILED
            state.environment_failure = message
        failure = Failure(
            type="artifact_registry", message=message, raw_output=message, source="artifact_registry_gate",
            authority="deterministic", attempt=state.attempt_number,
            diagnostics={"reason_code": reason_code, "milestone_id": milestone_id,
                         **({"problems": list(error.problems)} if indeterminate else {})},
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure) from error
    state.candidate_artifact_registry_digest = registry_digest(derived)
    state.record_event(RunEvent(
        kind="artifact_registry.candidate_validated", attempt=state.attempt_number, source="artifact_registry_gate",
        authority=EventAuthority.AUTHORITATIVE, message=f"the candidate derives {len(derived)} artifact record(s)",
        details={"milestone_id": milestone_id, "records": len(derived), "digest": state.candidate_artifact_registry_digest},
    ))


async def approval_gate(
    ctx: StageContext, deps: StageDeps, *, attempt_ctx: Any,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """4.5, the Pre-Apply Human Approval Gate: (the abort result when nothing may be applied - the person rejected the
    change, or approval was required and no approval callback exists - else None, whether approval was required)."""
    # The stage's run-local names (StageContext) and injected collaborators (StageDeps), bound under the names the
    # body used inside run_generation_workflow; the body below is that method's code, moved verbatim.
    state, workspace_path, worktree_path = ctx.state, ctx.workspace_path, ctx.worktree_path
    plan, design, run_id, control = ctx.plan, ctx.design, ctx.run_id, ctx.control
    approval_callback, resolved_obligation_ledger = ctx.approval_callback, ctx.resolved_obligation_ledger
    retrieved_chunks, active_skills, plan_prompt = ctx.retrieved_chunks, ctx.active_skills, ctx.plan_prompt
    logger, banner, ownership_review_evidence = deps.logger, deps.banner, deps.ownership_review_evidence
    static_analysis_result_fields, delete_checkpoint = deps.static_analysis_fields, deps.delete_checkpoint
    _record_approval_decision, _restore_original_bytes = deps._record_approval_decision, deps._restore_original_bytes
    _audit_approval_rules = deps.audit_approval_rules

    # 4.5. Pre-Apply Human Approval Gate
    # (state.pre_approval_review is reset once per loop iteration, above -
    # before run_attempt() runs, not here - see that comment for why.)
    diffs_to_show = []
    # Also kept for the pre-approval Reviewer call just below - same
    # worktree read, no second file-open per file.
    worktree_file_contents: Dict[str, str] = {}
    for filepath in sorted(state.all_files_written):
        worktree_file = os.path.join(worktree_path, filepath)
        actual_content = state.all_original_contents.get(filepath, "")
        with open(worktree_file, "r", encoding="utf-8", errors="replace") as fh:
            new_content = fh.read()
        worktree_file_contents[filepath] = new_content

        file_diff = "".join(difflib.unified_diff(
            actual_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{filepath}",
            tofile=f"b/{filepath}"
        ))
        diffs_to_show.append({"filepath": filepath, "content": file_diff})

    total_diff_lines = sum(len(d["content"].splitlines()) for d in diffs_to_show)
    autonomy_cfg = deps.config.autonomy

    # Check sensitive paths matches
    sensitive_match = False
    sensitive_reason = ""
    for filepath in state.all_files_written:
        for pattern in autonomy_cfg.sensitive_paths:
            try:
                if re.match(pattern, filepath, re.IGNORECASE):
                    sensitive_match = True
                    sensitive_reason = f"Sensitive path matched: {filepath} ({pattern})"
                    break
            except Exception as e:
                logger.warning(f"Invalid sensitive_paths regex '{pattern}' - this pattern is not being enforced: {e}")
        if sensitive_match:
            break

    # MA2.5 - process_profile.human_review_required is OR'd in as a new,
    # independent trigger, alongside (never replacing) the three that
    # already existed. This is what actually makes HEAVY (and STANDARD)
    # profiles require approval: autonomy_cfg.mode == "human-in-the-loop"
    # is only ONE clause of this OR expression, not a gate the whole
    # expression sits behind - a project configured for a more permissive
    # mode still hits approval here whenever ANY clause fires, process
    # profile included, once process_profiles.enabled/enforce_approval
    # are both explicitly turned on (kriya/config/config.py::
    # ProcessProfilesConfig - "safe incremental activation," off by
    # default). `control` is also None-safe on its own (engineering_
    # triage.enabled may be False, or MA1.3/MA2.4's classification may
    # have failed and been caught/logged) - either gate missing means
    # this new clause contributes nothing, same as before MA2.5 existed.
    # MA4.9 - audit-only, does not affect need_human_approval below.
    _audit_approval_rules(state.all_files_written, workspace_path, control)

    process_profiles_cfg = deps.config.process_profiles
    process_profile_requires_review = bool(
        process_profiles_cfg.enabled
        and process_profiles_cfg.enforce_approval
        and control is not None
        and control.process_profile.human_review_required
    )
    # NO SOURCE MUTATION -> NO SOURCE-CHANGE APPROVAL. This gate approves the exact change set (diffs_to_show) before it is applied; a unit that wrote nothing (a
    # verification-only unit: no planned files, DENY_ALL) has no subject to approve. Before this, the human-in-the-loop clause alone fired with an empty set: a
    # desktop launch declined it (other_approval -> human_rejected, FAILURE) and a terminal user was asked to approve nothing (owner run 20261015T075602-2b5a0ad7, s4).
    # Every non-empty change is approved exactly as before; the runtime-command approvals (attempt.py, approval_callback([], ...)) are a different subject and are
    # not touched here.
    has_source_mutation = bool(state.all_files_written)
    # OI-135 (S18): the mode through the one fail-closed reader (a value outside the vocabulary raises, typed).
    human_in_the_loop = is_human_in_the_loop(autonomy_cfg.mode)
    need_human_approval = has_source_mutation and (
        human_in_the_loop or
        sensitive_match or
        total_diff_lines > autonomy_cfg.risk_threshold_lines or
        process_profile_requires_review
    )

    # PRD-033: why approval was required, for the approval.decision event.
    approval_triggers = [name for name, fired in (
        ("human_in_the_loop", human_in_the_loop),
        ("sensitive_path", bool(sensitive_match)),
        ("diff_size", total_diff_lines > autonomy_cfg.risk_threshold_lines),
        ("process_profile", process_profile_requires_review),
    ) if fired]

    escalation_reason = "Human-in-the-loop review policy"
    if sensitive_match:
        escalation_reason = sensitive_reason
    elif total_diff_lines > autonomy_cfg.risk_threshold_lines:
        escalation_reason = f"Risk threshold exceeded ({total_diff_lines} lines > {autonomy_cfg.risk_threshold_lines})"
    elif process_profile_requires_review:
        escalation_reason = (
            f"Engineering process profile requires review (execution_weight="
            f"{control.process_profile.execution_weight.value}, kind="
            f"{control.engineering_route.kind.value}, risk="
            f"{control.engineering_route.max_observed_risk_class.name})"
        )

    if need_human_approval and attempt_ctx.deferred_executing_gates is not None:
        # Gap B: nothing has been built, tested or run yet - say exactly that, never a pending check as a success. In the policy note (before the reviewer's section),
        # so a display that splits the reason at the review header keeps it with the policy, not with the automated review.
        escalation_reason += (
            "\nPre-checks passed: expected files present, static rules, stack contract. Nothing has been built, tested or run yet."
            "\nBuild and tests: will run after you approve."
            "\nIf the application needs to be run, you will be asked separately before any command runs."
        )

    # Stage 6 SME review, 2026-08-15, Finding 1: the Reviewer used to run
    # AFTER this gate - by the time its verdict existed, a human had
    # already approved based on nothing but a truncated raw diff, and the
    # files were already copied into the real workspace with the worktree
    # torn down. Even in strict human-in-the-loop mode, a Reviewer
    # "REJECTED - critical issue" verdict changed nothing: quality_gates_passed
    # stayed True, the files were already on disk. Only worth doing when a
    # human is actually going to see it (need_human_approval AND a real
    # callback) - the common autonomous-mode path pays zero extra latency/
    # cost, Reviewer still runs at its usual later point for the final
    # report. Deliberately folded into escalation_reason (a plain string
    # on_approval already prints verbatim) rather than changing
    # approval_callback's signature - that callback is shared, unmodified,
    # across two OTHER, differently-shaped approval decisions elsewhere
    # (Stage 2A knowledge-gap approval, Runtime-Verification command
    # approval) and any external/REPL/MCP caller supplying their own
    # callback - a signature change would risk breaking all of them for a
    # fix scoped to just this one gate.
    if need_human_approval and approval_callback:
        escalation_reason = await pre_approval_review(
            ctx, deps, worktree_file_contents=worktree_file_contents, escalation_reason=escalation_reason,
        )
    if need_human_approval and approval_callback:
        # PRD-022: the approver sees open ownership findings as
        # evidence; they never decide the approval themselves.
        escalation_reason += ownership_review_evidence(
            resolved_obligation_ledger, state.all_files_written)
        # PRD-031A: the approver sees the static-analysis outcome,
        # including accepted risks; approving the diff creates no waiver.
        if state.static_analysis_result is not None:
            escalation_reason += f"\n\n=== Static Analysis ===\n{banner(state.static_analysis_result)}"

    def _abort_without_applying(status: str, review_text: str) -> Dict[str, Any]:
        """Shared cleanup for both 'a human said no' and 'approval was
        required but there was no way to even ask' - never applies
        worktree changes to the real workspace, restoring/removing
        exactly as the original human-rejection path already did.

        Trace-persistence contract (2026-09-18, VAL-001 G1-R2
        post-mortem): this path runs strictly AFTER the full
        Developer + Quality Gates loop has already populated
        `state` with real gate_outcomes/model_hops/run_events/
        evidence_records - unlike the knowledge_gap/planner_
        output_incomplete early-abort paths above (which
        correctly log the sparse shape, since nothing has
        happened yet at that point in the workflow). A real
        G1-R2 run (trace 8cc2018a) proved this distinction
        matters: the previous, sparse log_run() call here
        persisted `model_hops=[]`/`attempts=0 events`/zero gate
        outcomes for a run that had made 9 real Developer calls,
        making the whole approval-diff incident forensically
        unreconstructable after the fact. This call site now
        mirrors the SAME full contract the terminal success/
        failure path uses (this method's own trailing
        trace_logger.log_run() call) - not a blind duplication,
        the exact fields that call already treats as the
        canonical "a real Developer loop ran" shape."""
        if worktree_path != workspace_path:
            remove_git_worktree(workspace_path, worktree_path)
        else:
            for filepath, orig_content in state.all_original_contents.items():
                actual_file = os.path.join(workspace_path, filepath)
                # FILE-INTEGRITY-CONTRACT-001: restore the exact original
                # bytes, never a decoded copy of them.
                orig_raw = state.all_original_raw.get(filepath)
                if orig_raw is not None:
                    _restore_original_bytes(actual_file, orig_raw)
                elif filepath in state.all_original_raw:
                    if os.path.exists(actual_file):
                        os.remove(actual_file)
                elif orig_content:
                    # Atomic, not plain open(...,"w") - this restores the
                    # user's REAL project file directly (no worktree
                    # isolation on this path), so a kill mid-write here would
                    # corrupt the user's actual pre-existing source, not just
                    # a scratch sandbox file.
                    # MA4.16 migration classification: (4) explicitly
                    # documented safe/internal-only, not routed through
                    # AuthorizedFileWriter. `orig_content` is content Kriya
                    # itself already read from `actual_file` earlier THIS
                    # run (state.all_original_contents), and `actual_file`
                    # is that exact same real path - a rollback restoring a
                    # file to its own prior real content, not new/model-
                    # influenced content reaching a new/uncertain path. No
                    # containment or sensitive-path question this call site
                    # could meaningfully still fail.
                    atomic_write_file(actual_file, orig_content)
                elif os.path.exists(actual_file):
                    os.remove(actual_file)
        delete_checkpoint(workspace_path, run_id)
        # OI-31/OI-32 (E4): the full row through the one trace writer (kriya/workflow/run_lifecycle.py), with the
        # failure report the terminal row uses (kriya/workflow/outcome.py) - the same kwargs in the same order, the
        # store opened first, a failure logged and never raised, as the inline write did.
        persist_run_trace(
            deps, state, ctx.run_trace, TraceDetail(retrieved_chunks, active_skills, plan_prompt), status=status,
            terminal=TerminalTrace(status, failure_report_dicts(state)),
        )
        return {
            "plan": plan,
            "design": design,
            "files": [],
            "quality_gates_passed": False,
            # W3-6 (KERNEL-HUMAN-REJECTED-NO-CATEGORY-001): the category the trace row above records (human_rejected / approval_required)
            "failure_category": status,
            "review": review_text,
            "review_included_in_approval": state.pre_approval_review is not None,
            "run_id": run_id,
            **static_analysis_result_fields(state.static_analysis_result),
        }

    if need_human_approval and approval_callback:
        review_note = (
            " (automated code review attached to approval context)"
            if state.pre_approval_review is not None else ""
        )
        # The full report remains in the local result and approval
        # context; duplicating it in the INFO log adds no new evidence.
        logger.info(
            "Escalating changes to human approval gate: "
            f"{escalation_reason.splitlines()[0]}{review_note}"
        )
        outcome = await resolve_approval(approval_callback, diffs_to_show, escalation_reason)
        _record_approval_decision(state, _APPROVAL_EVENT_OUTCOME[outcome.decision], approval_triggers, reason_code=outcome.reason_code if outcome.unavailable else None)
        if outcome.declined:
            logger.info("Human rejected changes. Aborting workflow.")
            return _abort_without_applying(
                "human_rejected", "Rejected by user during approval gate review."
            ), need_human_approval
        if outcome.unavailable:
            # Nobody was asked or nobody decided (a desktop launch that cannot put this decision to a person, a review that expired): that is "approval required and not
            # available", the same stop as a missing callback - never a human rejection.
            logger.warning(f"Approval required ({escalation_reason}) but no approval decision could be obtained ({outcome.reason_code}) - nothing applied.")
            return _abort_without_applying(
                "approval_required",
                f"Not applied: human approval was required ({escalation_reason}) but no approval decision could be obtained ({outcome.reason_code}); "
                "nothing was approved and nobody declined.",
            ), need_human_approval
    elif need_human_approval and not approval_callback:
        # Independent adversarial review, 2026-08-16, Finding 2: this gate used
        # to fail OPEN - if policy required approval but no callback was wired
        # (any direct run_generation_workflow() caller that doesn't supply one,
        # e.g. a library/MCP integration), execution silently fell through to
        # the unconditional "apply to workspace" step below with no approval
        # ever having been requested. A sensitive-path match or a large diff
        # would be applied to the real workspace with zero human involvement,
        # in the one mode whose entire purpose is preventing exactly that.
        # Fixed the same way the existing knowledge_gap/human_rejected paths
        # already handle "can't proceed automatically" - a distinct early-return
        # status, never silently applying, so a caller missing a callback gets
        # an unambiguous signal instead of an unreviewed change landing on disk.
        logger.warning(
            f"Approval required ({escalation_reason}) but no approval_callback was "
            "provided - refusing to apply changes rather than proceeding unreviewed."
        )
        _record_approval_decision(state, "unavailable", approval_triggers)
        return _abort_without_applying(
            "approval_required",
            f"Not applied: human approval was required ({escalation_reason}) but no "
            "approval_callback was provided to run_generation_workflow().",
        ), need_human_approval

    return None, need_human_approval


def prepare_lessons(ctx: StageContext, deps: StageDeps) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Stage 7, before the terminal regression: the dependency-skill auto-accrual and the hard-won-knowledge trigger;
    (the self-corrected outcome, whether the lessons are extracted after an authoritative success)."""
    state, worktree_path, guard, logger = ctx.state, ctx.worktree_path, ctx.guard, deps.logger

    # Phase 3: Auto-generate skill templates for solved dependencies. A
    # coordinate merely appearing in a resolver.py suggestion during some
    # retry attempt is not evidence it was ever actually used - confirmed
    # live as a real bug: a wrong Maven-Central match for a generic
    # missing-symbol name (the real problem was a bare wrong import path,
    # not a genuinely missing dependency) got auto-accrued into a
    # permanent, git-committed skill scaffold that then polluted an
    # unrelated later goal's skill-gap detection. Only accrue a
    # coordinate that actually appears in the FINAL applied file content -
    # real evidence it was used, not just suggested at some point.
    # best_of_n_candidates_tried also counts: a discarded independent
    # candidate's own gate_outcomes can carry the same real dependency-
    # resolution evidence retry_count alone would've gated on.
    if state.budgets.retry_count > 0 or state.budgets.best_of_n_candidates_tried > 0:
        final_contents_combined = ""
        for filepath in state.all_files_written:
            try:
                with open(os.path.join(worktree_path, filepath), "r", encoding="utf-8", errors="replace") as fh:
                    final_contents_combined += fh.read()
            except Exception as e:
                logger.debug(f"Failed to read '{filepath}' for auto-accrual verification: {e}")
        for outcome in state.gate_outcomes:
            output_str = outcome.get("output", "")
            if output_str and "=== KRIYA PLATFORM DEPENDENCY SUGGESTIONS ===" in output_str:
                deps = re.findall(
                    r"<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*<version>([^<]+)</version>",
                    output_str
                )
                for g, a, v in deps:
                    artifact_id = a.strip()
                    if artifact_id not in final_contents_combined:
                        logger.debug(
                            f"Skipping auto-accrual for {g.strip()}:{artifact_id} - not found in the "
                            "final applied files, likely an irrelevant suggestion never actually used."
                        )
                        continue
                    try:
                        coord = f"{g.strip()}:{artifact_id}"
                        ver = v.strip()
                        logger.info(f"Auto-accrual: Automatically scaffolding verified skill for resolved dependency {coord}:{ver}")
                        guard.generate_skill_template(coord, ver)
                    except Exception as ex:
                        logger.warning(f"Failed to auto-accrue skill for dependency: {ex}")

    # Autonomous Skill Accrual / Lesson extraction. Originally gated on
    # `state.last_model_override and chain` - fired ONLY for a fallback-
    # model escalation rescue, never for a primary-model attempt that
    # self-corrected, and never at all for a project with no llm_chain
    # configured. Found live, 2026-08-11 (right after the JAVA_HOME-
    # durability fix above, same session): this is exactly the gap the
    # "durable verified project facts" backlog item was asking to close -
    # project-specific lessons learned the hard way ("X resolves
    # transitively, don't add it explicitly") most often come from an
    # ordinary primary-model retry converging, not specifically an
    # escalated fallback model, and plenty of real projects never
    # configure a fallback chain at all.
    #
    # Broadened to ALSO fire when the PRIMARY model needed a genuinely
    # significant amount of full-set regeneration to converge -
    # `retry_count >= 2` (at least two full FULL-SET rewrites, not just
    # one targeted patch) - rather than any retry at all. A first attempt
    # at this used bare `state.error_context` truthiness (set on ANY
    # failure, never cleared) as the trigger, reasoning it precisely
    # captures "this successful attempt was actually informed by a real
    # failure" - correct in principle, but it fires on essentially EVERY
    # retry-then-succeed run, including a single trivial one-line
    # syntax-typo fix (confirmed by the existing test suite: ~20
    # unrelated tests broke, each hitting an unplanned extra LLM call for
    # a routine single-retry recovery that has nothing to do with a
    # durable, project-specific "fact"). That's also the wrong real-world
    # tradeoff, not just a test inconvenience - it would flood
    # staged_rules.txt with noise from routine retries instead of
    # capturing genuinely hard-won lessons, and pay an extra completion
    # call on nearly every imperfect-but-recoverable run. `retry_count >=
    # 2` mirrors why fallback-model escalation was originally a decent
    # proxy in the first place: reaching a SECOND full-set attempt is a
    # real signal the first fix didn't fully work, not a proxy for
    # "informed by a real failure" that's true almost always.
    # best_of_n_candidates_tried >= 2 mirrors the same "genuinely hard-won"
    # bar for two DISCARDED independent candidates, not just two full-set
    # retries within one candidate - equally real signal, just reset out of
    # retry_count between candidates (see kriya/workflow/best_of_n.py).
    # A resolved self-correction micro-loop (kriya/workflow/self_correction.py)
    # is added 2026-08-22 as its own, independent trigger, alongside the
    # existing fallback-model-escalation/multi-retry ones above - it happens
    # within a single attempt (touches neither last_model_override nor
    # retry_count), so without this it was completely invisible to lesson
    # extraction despite producing the richest evidence this pipeline
    # generates all day (explicit tool-grounded diagnosis, a real before/
    # after edit, a real verification call). This is the actual mechanism
    # meant to close the gap between "an incident got tool-corrected" and
    # "a future generation is warned about it" without a human needing to
    # notice the pattern recurring and hand-write a deterministic check.
    self_corrected_outcome = next(
        (o for o in state.gate_outcomes if o.get("self_corrected")), None,
    )
    should_extract_hard_won_knowledge = bool(
        state.last_model_override
        or state.budgets.retry_count >= 2
        or state.budgets.best_of_n_candidates_tried >= 2
        or self_corrected_outcome is not None
    )

    return self_corrected_outcome, should_extract_hard_won_knowledge


def apply_terminal_candidate(
    ctx: StageContext, deps: StageDeps, *, attempt_ctx: Any, final_candidate_contents: Dict[str, str],
) -> None:
    """Stage 6, terminal apply: the complete verified candidate as one revision-grounded batch, the attempt's success
    record, and the sandbox released. A commit that did not commit is a deterministic stop (raises)."""
    state, goal, workspace_path, worktree_path = ctx.state, ctx.goal, ctx.workspace_path, ctx.worktree_path
    run_id, grounding_goal, structured_plan = ctx.run_id, ctx.grounding_goal, ctx.structured_plan
    current_subtask_id, work_unit, logger = ctx.current_subtask_id, ctx.work_unit, deps.logger
    _direct_terminal_writes, _terminal_contract_authorizations = (
        deps._direct_terminal_writes, deps._terminal_contract_authorizations)
    commit_terminal_candidate, commit_guard = deps.commit_terminal_candidate, deps.commit_guard
    derive_contract_transition, developer = deps.derive_contract_transition, deps.developer
    _raise_contract_registry_stop, _raise_terminal_commit_stop = (
        deps._raise_contract_registry_stop, deps._raise_terminal_commit_stop)

    # Terminal apply: the complete verified candidate as one
    # revision-grounded batch through the shared commit seam
    # (kriya/workflow/terminal_commit.py) - exact bytes and mode,
    # durable RunRecord intent before the first workspace byte,
    # and a settled commit result. Every real-workspace base
    # revision must still match what this run originally read; a
    # partial write is rolled back.
    final_writes = _direct_terminal_writes(worktree_path, workspace_path, state)
    annotate_run(
        workspace_path,
        retry_counters={
            "retry_count": state.budgets.retry_count,
            "targeted_retry_count": state.budgets.targeted_retry_count,
        },
        retry_state_reference=f"checkpoint:{run_id}",
    )
    # Unique per cycle: a resumed run reuses run_id.
    terminal_transaction_id = uuid.uuid4().hex
    # PRD-029: the ContractRegistry transition this candidate
    # implies commits in the same transaction as its bytes.
    contract_authorizations = _terminal_contract_authorizations(
        grounding_goal, structured_plan, current_subtask_id, state,
    )
    downstream_verified = bool(
        state.terminal_regression_succeeded
        and state.terminal_full_suite_result is not None
        and output_confirms_nonzero_test_execution(
            str(state.terminal_full_suite_result.get("output") or ""),
        )
    )

    commit_outcome = commit_terminal_candidate(
        final_writes, workspace_path=workspace_path,
        static_analysis=commit_guard(deps.config, state.static_analysis_result),
        verified_candidate=state.verified_candidate_binding,
        transaction_id=terminal_transaction_id,
        evidence={
            "verification_evidence_ids": [
                f"commit:{terminal_transaction_id}",
            ],
        },
        contract_transition=functools.partial(
            derive_contract_transition,
            workspace_path=workspace_path, registry=None,
            original_contents=dict(state.all_original_contents),
            final_contents=dict(final_candidate_contents),
            authorizations=tuple(contract_authorizations),
            transaction_id=terminal_transaction_id,
            downstream_verified=downstream_verified,
            # PRD-029: a milestone unit establishes its provided
            # capabilities in this same transaction.
            capability_contracts=(
                work_unit.provided_capabilities if work_unit is not None else ()
            ),
        ),
    )
    if not commit_outcome.committed:
        if commit_outcome.reason_code in CONTRACT_REGISTRY_STOP_REASON_CODES:
            _raise_contract_registry_stop(state, commit_outcome)
        _raise_terminal_commit_stop(state, commit_outcome)
    state.contract_registry_transition = commit_outcome.contract_registry
    state.post_commit_persistence_errors.extend(commit_outcome.record_errors)
    for filepath in sorted(state.all_files_written):
        logger.info(
            "Applied terminally verified sandbox change to workspace: %s", filepath,
        )
    # PRD-024: the suite just ran against exactly what is now in
    # the workspace; the next run starting here may reuse it.
    if state.terminal_full_suite_result is not None:
        try:
            full_suite_evidence_for_reuse = deps.load_full_suite_evidence_for_reuse()
            deps.keep_full_suite_evidence(full_suite_evidence_for_reuse(
                workspace_path, deps.config.autonomy, goal=goal, run_id=run_id,
                raw_result=state.terminal_full_suite_result,
            ))
        except Exception as exc:
            deps.keep_full_suite_evidence(None)
            logger.warning(f"Full-suite evidence not kept for reuse: {exc}")

    state.overall_attempt_succeeded = True
    state.record_developer_attempt_outcome(developer.llm, passed=True)
    state.record_event(RunEvent(
        kind="attempt.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "applied": True},
    ))
    log_gate_banner(
        "OVERALL ATTEMPT", "PASSED", state.attempt_number,
        scope=attempt_ctx.execution_scope,
    )
    state.quality_gates_succeeded = True

    # Authoritative work is durable now; release the sandbox
    # before any optional learning call can stall or fail.
    if worktree_path != workspace_path:
        remove_git_worktree(workspace_path, worktree_path)


async def extract_lessons(
    ctx: StageContext, deps: StageDeps, *, should_extract_hard_won_knowledge: bool,
    self_corrected_outcome: Optional[Dict[str, Any]],
) -> None:
    """Stage 7, after authoritative success: the advisory live-failure lesson extraction (never fails the run)."""
    state, workspace_path, repo_slug = ctx.state, ctx.workspace_path, ctx.repo_slug
    logger, llm = deps.logger, deps.llm

    # Advisory learning may consume a slow model call. It is legal
    # only after terminal regression and atomic application have
    # established authoritative success, never between candidate
    # gates and the terminal regression boundary.
    if should_extract_hard_won_knowledge:
        try:
            logger.info(
                "Terminal success established - extracting structured "
                "knowledge facts from the hard-won fix..."
            )
            knowledge_staging, LiveFailureChannel, LiveFailureContext = deps.load_lesson_extraction()

            file_contents = {}
            for filepath in state.all_files_written:
                full_path = os.path.join(workspace_path, filepath)
                try:
                    with open(full_path, "r", encoding="utf-8") as fh:
                        file_contents[filepath] = fh.read()
                except Exception as e:
                    logger.debug(f"Failed to read '{full_path}' for lesson extraction: {e}")

            channel = LiveFailureChannel(llm)
            facts = await channel.extract(LiveFailureContext(
                error_context=state.error_context,
                file_contents=file_contents,
                model_override=state.last_model_override,
                base_url_override=state.last_base_url_override,
                api_key_override=state.last_api_key_override,
                extra_body_override=state.last_extra_body_override,
                transcript=(
                    self_corrected_outcome.get("self_correction_transcript")
                    if self_corrected_outcome is not None else None
                ),
            ))
            if facts:
                skills_dir = ctx.skills_dir or deps.config.paths.skills        # ctx.skills_dir: the engine's effective root
                skill_folder = os.path.join(skills_dir, f"auto-{repo_slug}")
                written = knowledge_staging.stage_facts(skill_folder, facts)
                if written:
                    logger.info(f"Staged {len(written)} structured knowledge fact(s) to {skill_folder}")
        except Exception as ex:
            logger.warning(f"Failed to extract lesson or update skills: {ex}")


__all__ = ["apply_terminal_candidate", "approval_gate", "extract_lessons", "prepare_lessons",
           "run_semantic_safety_gate", "run_static_analysis_gate"]
