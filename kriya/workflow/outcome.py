"""Run outcome construction of one ``run_generation_workflow`` invocation (full modularization W4, E2).

Moved out of ``kriya/workflow/workflow.py`` (bodies verbatim; ``tests/test_outcome.py`` pins every terminal path
against the E1 goldens and, before the move, against the inline code):

- ``terminal_failure_category``: WHY a run that did not pass ended (the typed ``environment_failure`` prefixes,
  the final-review refusal, the plan-scope conflict, the PRD-026 no-progress terminal, exhausted gates);
- ``failure_report_dicts``: WHAT kind of thing kept failing across the attempts (MA7.6);
- ``final_review_refusal_payload``: the PROMPT-BUDGET-FIT-001C refusal with what had already happened;
- ``completed_result``: the result dict of a run that reached its terminal stage (the controller wraps it as
  ``WorkflowResult.legacy_result``).

The early-exit result dicts (verification-contract refusals, knowledge gap, planner/architect rejections, baseline
and sandbox stops, approval rejection) stay with their stages for E3/E4. This module never imports the engine
facade, the controller or the CLI; the engine's collaborators arrive in ``StageDeps``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from kriya.workflow.contract_lifecycle import CONTRACT_REGISTRY_STOP_REASON_CODES
from kriya.workflow.failure_reporting import build_failure_report_entry
from kriya.workflow.file_integrity import DETERMINISTIC_FILE_INTEGRITY_STOPS, VERIFICATION_TREE_STOP_CODES
from kriya.workflow.requirements import (
    REQUIREMENTS_UNRESOLVED,
    requirement_evidence,
    requirement_outcomes,
    requirement_verdict_details,
)
from kriya.workflow.stage_context import RunState, StageDeps

# PRD-032: the environment_failure prefix of a terminal commit that did not
# commit (failure_category ``workspace_commit_failed``).
WORKSPACE_COMMIT_NOT_COMPLETED = "WORKSPACE_COMMIT_NOT_COMPLETED"


def terminal_failure_category(state: RunState) -> str:
    """A stable, short string identifying WHY this run failed, so a caller
    (human or script) can always ask "why did this fail" the same way
    rather than having to know which of several differently-shaped
    fields to check. Called only for a run whose terminal quality did not
    pass (a passing run's failure_category is None). The other two terminal
    states Kriya can end a run in - knowledge-gap and human-rejected-approval -
    are separate early-return dicts that log their own failure_category
    directly at their own return site."""
    # PRV-17 preflight correction (2026-09-03), extended for recovery
    # admission (2026-09-03): checked BEFORE the generic
    # "environment_failure" branch below, even though both cases also
    # set state.environment_failure to reuse its STOP_ENVIRONMENT
    # mechanism (see retry_strategy.py's own comments on that reuse) -
    # an unauthorized/unrecoverable generation target, or a grounded
    # failure with no authorized repair target, is a plan/scope
    # defect, never a real machine/toolchain problem, and must not be
    # reported or traced as one (kriya/cli.py prints the generic
    # branch below as "[ENVIRONMENT/TOOLCHAIN ISSUE]" with
    # toolchain-fix advice). Checked by message prefix, not a single
    # counter, since two distinct retry_strategy.py call sites now
    # produce this same category (state.unrecoverable_scope_
    # denial_count's write-time PolicyDeniedError case, and the
    # pre-call "NO_AUTHORIZED_REPAIR_TARGET" recovery-admission case)
    # - both are scope defects, never environment ones.
    is_scope_defect_stop = bool(state.environment_failure) and state.environment_failure.startswith((
        "UNAUTHORIZED_GENERATION_TARGET:", "NO_AUTHORIZED_REPAIR_TARGET:",
    ))
    # PRV-17 (2026-09-08, P7 efficiency finding): same message-prefix
    # convention as is_scope_defect_stop above - a candidate-
    # independent deterministic validator/build-configuration defect
    # (kriya/workflow/deterministic_failure_diagnostic.py) is neither
    # a machine/toolchain environment problem nor a plan/scope
    # defect, and must not be reported or traced as either.
    is_candidate_independent_deterministic_failure = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE:")
    )
    # Demo-01 Finding 4 (2026-09-11): GENERATION TIME BUDGET EXHAUSTED
    # (kriya/workflow/attempt.py::_ensure_generation_time_budget)
    # reuses state.environment_failure/STOP_ENVIRONMENT purely as its
    # stop mechanism, same as the two categories above - but it is a
    # TERMINAL STOP CONDITION (the retry loop itself ran out of
    # configured time), never an ENVIRONMENT/TOOLCHAIN failure, and
    # must not be reported or traced as one (kriya/cli.py's generic
    # branch prints "[ENVIRONMENT/TOOLCHAIN ISSUE]" with `kriya
    # doctor` advice, which cannot help a budget problem). Checked by
    # the exact message prefix _ensure_generation_time_budget's own
    # Failure.message always starts with, same convention as the two
    # categories above - no new state field, no retry/budget
    # behavior change.
    is_generation_budget_exhausted_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("GENERATION TIME BUDGET EXHAUSTED:")
    )
    # SEC-001 (2026-09-11): same message-prefix convention as the
    # three categories above - kriya/tools/containment.py's
    # ContainmentSetupError means ProcessController refused to run
    # a command uncontained, which is neither an environment/
    # toolchain problem (kriya doctor cannot fix a missing/
    # misconfigured containment backend the same way it diagnoses
    # Java/Maven) nor an ordinary retryable code defect.
    is_containment_setup_failed_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("CONTAINMENT_SETUP_FAILED:")
    )
    # VAL-001 G1-DEVINV2 (2026-09-20): same message-prefix convention
    # as the four categories above - a full-regression block with no
    # candidate-attributable evidence (kriya/workflow/workflow.py's
    # own _full_regression_unattributed branch) is neither an
    # environment/toolchain problem nor an ordinary retryable code
    # defect; `kriya doctor` cannot fix an aggregate-level test-suite
    # delta with no attributable test.
    is_regression_unattributed_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("REGRESSION_UNATTRIBUTED:")
    )
    # PRD-017: same message-prefix convention - a fallback model that
    # cannot serve the attempt (kriya/workflow/model_transition.py).
    is_fallback_incompatible_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("FALLBACK_MODEL_INCOMPATIBLE:")
    )
    # CONTEXT-EDIT-PROTOCOL-001: same convention - no feasible mutation
    # operation under the authoritative context (edit_capability.py).
    is_edit_protocol_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE:")
    )
    # PROVIDER-CONTRACT-001: same convention - a provider contract
    # violation (kriya/core/provider_contract.py reason codes).
    from kriya.core.provider_contract import PROVIDER_CONTRACT_REASON_CODES

    is_provider_contract_stop = bool(state.environment_failure) and any(
        state.environment_failure.startswith(f"{code}:") for code in PROVIDER_CONTRACT_REASON_CODES)
    # MODEL-EVIDENCE-HARDENING-001: same convention - a production
    # retry identity that is not qualified (attempt.py).
    is_retry_identity_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith("RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED:")
    )
    # PRD-020: same convention - an original requirement without
    # accepted evidence under a blocking policy (requirements.py).
    is_requirements_unresolved_stop = (
        bool(state.environment_failure)
        and state.environment_failure.startswith(f"{REQUIREMENTS_UNRESOLVED}:")
    )
    # PRD-029: same convention - a ContractRegistry transition that
    # could not be made exactly (contract_lifecycle.py).
    is_contract_registry_stop = bool(state.environment_failure) and any(
        state.environment_failure.startswith(f"{code}:") or state.environment_failure == code
        for code in CONTRACT_REGISTRY_STOP_REASON_CODES
    )
    # PRD-032: same convention - the verified candidate's terminal
    # commit did not commit (_raise_terminal_commit_stop).
    is_workspace_commit_stop = bool(state.environment_failure) and (
        state.environment_failure.startswith(f"{WORKSPACE_COMMIT_NOT_COMPLETED}:")
    )
    # FILE-INTEGRITY-CONTRACT-001: same convention - a target the edit
    # engine cannot mutate byte-exactly (file_integrity.py reason codes).
    is_file_integrity_stop = bool(state.environment_failure) and any(
        state.environment_failure.startswith(f"{code}:") for code in DETERMINISTIC_FILE_INTEGRITY_STOPS)
    is_verification_tree_stop = bool(state.environment_failure) and any(
        state.environment_failure.startswith(f"{code}:") for code in VERIFICATION_TREE_STOP_CODES)
    # PRD-031A: a static-analysis gate stop (StaticAnalysisGateResult.gap).
    static_analysis_stop = next(
        (
            f"static_analysis_{outcome.lower()}" for outcome in ("BLOCKED", "UNKNOWN", "UNAVAILABLE")
            if (state.environment_failure or "").startswith(f"STATIC_ANALYSIS_{outcome}:")
        ),
        None,
    )
    return (
        "final_review_refused" if state.final_review_refusal is not None
        else "plan_scope_revision_required" if state.plan_scope_conflict
        else "unauthorized_generation_target" if is_scope_defect_stop
        else "candidate_independent_deterministic_failure" if is_candidate_independent_deterministic_failure
        else "generation_budget_exhausted" if is_generation_budget_exhausted_stop
        else "containment_setup_failed" if is_containment_setup_failed_stop
        else "regression_unattributed" if is_regression_unattributed_stop
        else "fallback_model_incompatible" if is_fallback_incompatible_stop
        else "context_edit_protocol_unsatisfiable" if is_edit_protocol_stop
        else "provider_contract_violation" if is_provider_contract_stop
        else "retry_identity_not_qualified" if is_retry_identity_stop
        else "requirements_unresolved" if is_requirements_unresolved_stop
        else "contract_registry_blocked" if is_contract_registry_stop
        else static_analysis_stop if static_analysis_stop is not None
        else "workspace_commit_failed" if is_workspace_commit_stop
        else "file_integrity_unsupported" if is_file_integrity_stop
        else "verification_tree_mutated" if is_verification_tree_stop
        else "environment_failure" if state.environment_failure
        # PRD-026: the retry-progress invariant ended the run.
        else "no_progress" if state.no_progress_terminated
        else "quality_gates_exhausted"
    )

def failure_report_dicts(state: RunState) -> List[Dict[str, Any]]:
    """MA7.6 (kriya/workflow/failure_reporting.py) - additive, NOT a
    replacement for failure_category: that field answers "why did the retry
    loop stop"; this answers "what KIND of thing kept failing across the
    attempts" - every real gate_outcome (one per failed attempt,
    kriya/workflow/failure.py's own Failure.to_gate_outcome()) through
    categorize_failure()/build_failure_report_entry(). Empty list on a clean
    success (state.gate_outcomes only ever holds failures) or a run that never
    got past a single passing attempt."""
    failure_report = [
        build_failure_report_entry(outcome.get("type", ""), outcome.get("attribution_tier"))
        for outcome in state.gate_outcomes
    ]
    return [
        {"failure_type": e.failure_type, "category": e.category.value, "attribution_tier": e.attribution_tier}
        for e in failure_report
    ]


def final_review_refusal_payload(state: RunState, workspace_path: str, *, logger: Any) -> Dict[str, Any]:
    """PROMPT-BUDGET-FIT-001C: the refusal with what had already
    happened. ``candidate_applied`` is whether this run's candidate reached
    ``workspace_path``; ``committed_work_units`` comes from the owning
    RunRecord's commit cycles (None when unavailable). Nothing is rolled
    back."""
    from kriya.control.run_coordinator import owning_run_committed_work_units

    try:
        committed = owning_run_committed_work_units(workspace_path)
    except Exception as error:  # the refusal is still reported
        logger.warning(f"Committed work units unavailable: {error}")
        committed = None
    return {
        **state.final_review_refusal,
        "candidate_applied": bool(state.quality_gates_succeeded),
        "committed_work_units": committed,
        "rolled_back": False,
    }


def completed_result(
    deps: StageDeps, state: RunState, *, quality_passed: bool, failure_category: Optional[str],
    failure_report_dicts: List[Dict[str, Any]], plan: Any, design: Any, review: Any, workspace_path: str,
    run_id: str, required_verification: Optional[List[Dict[str, Any]]], unresolved_skill_gap_names: List[str],
    skill_staleness_warnings: List[str], active_skill_manifest: Any, requirement_set: Any,
    resolved_obligation_ledger: Any,
) -> Dict[str, Any]:
    """The result dict of a run that reached its terminal stage, every field in the order the run has always
    built it (``generation_metrics`` is sampled here, after the terminal trace row sampled its own)."""
    return {
        "plan": plan,
        "design": design,
        "files": list(state.all_files_written),
        # ENFORCE-IDENTICAL-WRITE-COMPLETION-001: planned files the candidate returned byte-identical to the
        # baseline - not a mutation (never applied), but examined, verified-unchanged source a later unit may
        # treat as established (the milestone driver's established file context).
        "unchanged_files": sorted(state.identical_rewrites) if quality_passed else [],
        "quality_gates_passed": quality_passed,
        "candidate_gates_passed": state.candidate_gates_succeeded,
        "terminal_regression_passed": state.terminal_regression_succeeded,
        "overall_attempt_passed": state.overall_attempt_succeeded,
        "verification_results": deps.required_verification_evidence(
            required_verification, quality_passed, gate_outcomes=state.gate_outcomes,
        ),
        "deterministic_gate_evidence": deps.deterministic_gate_evidence(
            state.gate_outcomes, state.attempt_number,
        ),
        # ENFORCE-VERIFIED-NO-CHANGE-001: "VERIFIED_NO_CHANGE" when the
        # unit completed without a mutation on deterministic evidence.
        "completion_kind": state.completion_kind if quality_passed else None,
        "environment_failure": state.environment_failure if not quality_passed else None,
        "failure_category": failure_category,
        # PRD-026 / LR-R1-P4: which no-progress terminal ended the run (None otherwise) - the enforce
        # controller keys its one bounded upstream-owner reopen on VERIFICATION_RETRY_NO_CHANGE_POSSIBLE.
        "no_progress_reason": state.no_progress_reason if not quality_passed else None,
        # The unit's own last deterministic failure (type, message, the gate's output, bounded) - the evidence
        # a reopened upstream owner receives. Never model text: Failure.raw_output is the validator's output.
        "last_failure": (
            {"type": state.last_failure.type, "message": str(state.last_failure.message or "")[-2000:],
             "raw_output": str(state.last_failure.raw_output or "")[-4000:]}
            if state.last_failure is not None and not quality_passed else None
        ),
        "retry_progress": state.retry_progress_summary(),
        # PRD-029: the ContractRegistry transition committed with this
        # run's source (None when no contract changed).
        "contract_registry": state.contract_registry_transition,
        # PRD-032: why the verified candidate was not committed (None
        # unless its terminal commit did not commit).
        "workspace_commit_failure": state.terminal_commit_failure,
        **({"post_commit_persistence_errors": list(state.post_commit_persistence_errors)} if state.post_commit_persistence_errors else {}),
        "failure_report": failure_report_dicts,
        "plan_scope_conflict": state.plan_scope_conflict,
        "toolchain_warning": state.toolchain_warning,
        "lsp_warning": state.lsp_warning,
        "unresolved_skill_gaps": sorted(set(unresolved_skill_gap_names)) or None,
        "skill_staleness_warnings": sorted(set(skill_staleness_warnings)) or None,
        "active_skill_manifest": active_skill_manifest,
        "generation_metrics": state.generation_metrics(
                total_wall_seconds=deps.monotonic() - state.generation_started_monotonic,
            ),
        "review": review,
        "review_included_in_approval": state.pre_approval_review is not None,
        # PRD-031A: the static-analysis outcome (ACCEPTED_RISK is never PASS).
        **deps.static_analysis_fields(state.static_analysis_result),
        **({"final_review_refusal": final_review_refusal_payload(state, workspace_path, logger=deps.logger)}
           if state.final_review_refusal is not None else {}),
        "run_id": run_id,
        # PRD-021: grounded ownership findings (advisory evidence).
        **({"ownership_findings": [f.to_dict() for f in state.ownership_findings]}
           if state.ownership_findings else {}),
        # PRD-020: the original requirements and each one's authoritative
        # outcome (the verifier's; PENDING when no verdict was recorded).
        **({"requirements": {
            **requirement_set.to_dict(),
            "outcomes": {rid: outcome.value for rid, outcome in
                         requirement_outcomes(resolved_obligation_ledger, requirement_set).items()},
            "evidence": requirement_evidence(resolved_obligation_ledger, requirement_set),
            # MODEL-EVIDENCE-HARDENING-001: each verdict with its reason
            # code, evidence, verifier identity and judged candidate.
            "verdicts": requirement_verdict_details(resolved_obligation_ledger, requirement_set),
        }} if requirement_set is not None else {}),
    }


__all__ = ["WORKSPACE_COMMIT_NOT_COMPLETED", "completed_result", "failure_report_dicts", "final_review_refusal_payload",
           "terminal_failure_category"]
