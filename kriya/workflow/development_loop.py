"""Stage 4 of a work unit: the Developer + Quality Gates loop (full modularization W4, E5).

Moved verbatim from ``WorkflowEngine.run_generation_workflow`` (``kriya/workflow/workflow.py``):

- ``prepare_development``: the prelude - the Developer's standing prompt blocks (generation manifest, ecosystem /
  resource-lifecycle / verification-contract blocks, existing Maven dependencies), the VAL-001/PRD-024 brownfield
  validation baselines and their ``baseline_indeterminate`` stop, the isolated sandbox worktree and its
  FILE-INTEGRITY-CONTRACT-001 sync refusal, the run's obligation ledger (resume restore, requirement seeds, contract
  non-claims and dispositions, ownership findings), its deterministic-failure diagnostics store and the
  loop-invariant ``AttemptContext``;
- ``run_attempt_loop``: the retry loop - its head's typed ``AttemptPlan`` (``attempt_lifecycle.plan_attempt``:
  ``retry_policy.decide_for_state`` decides every continue/stop) and one ``attempt_lifecycle.run_iteration`` per
  admitted attempt (the per-candidate resets, the LR-R1-M1 attempt-evidence iteration scope, one attempt - Best-of-N on
  the first one when configured -, the candidate's acceptance, and a failed attempt's routing: ``RecoveryPhaseAdvanced``,
  a Best-of-N recorded failure, any other failure to ``retry_strategy.handle_attempt_failure``);
- ``accept_candidate``: the attempt's candidate through the pre-apply success boundary up to the terminal apply and
  the lessons (the review, approval/apply and lesson bodies themselves are ``stage_review`` / ``stage_apply``, E4);
- ``run_terminal_regression``: the terminal gate (full regression with the baselines' attribution, the future-owner
  deferral, the brownfield ownership / public-API / semantic-region checks, the success outcome).

The engine keeps every name tests patch (``run_attempt``, ``handle_attempt_failure``, ``create_git_worktree``,
``capture_brownfield_baselines``, ``compute_workspace_content_hash``, the closer forwarders, ...) and hands them in
through ``StageDeps`` (``WorkflowEngine._development_deps``, resolved through ``kriya.workflow.workflow`` when stage 4
begins), so a patch on the facade keeps intercepting the moved loop: ``deps.run_attempt`` IS
``kriya.workflow.workflow.run_attempt`` (a patch of ``kriya.workflow.attempt.run_attempt`` still reaches only the
Best-of-N path). Names from the kernel's all-import cycle (``attempt``, ``best_of_n``, ``baseline_policy``,
``model_transition``, ``state``, ``resume_fingerprints``, ``suite_attribution``, ``retry_prompts``,
``retry_strategy``, ``ownership_findings``, ``checkpoint``) arrive the same way, the function-local imports of three
of them as loaders called where the loop imported them. This module never imports ``kriya.workflow.workflow``,
``attempt``, ``workflow_controller`` or ``kriya.cli``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

from kriya.control.run_coordinator import mark_run_stage
from kriya.control.run_record import RunLifecycle
from kriya.policy.filesystem import WriteScopeMode
from kriya.repository.git import RepositoryContentFilterRefused
from kriya.tools.validate import PolymorphicValidator, execution_evidence
from kriya.workflow.acceptance import goal_requires_runtime_behavior
from kriya.workflow.architectural_choice import architecture_choice_invalidated_message
from kriya.workflow.attempt_lifecycle import IterationEnd, plan_attempt, run_iteration
from kriya.workflow.banners import log_quality_gate_banner
from kriya.workflow.compile_regression import attribute_compile_regression
from kriya.workflow.deterministic_failure_diagnostic import DeterministicFailureDiagnosticStore
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import _build_quality_gate_failure
from kriya.workflow.file_resolution import find_brownfield_public_api_changes, find_brownfield_test_redirections
from kriya.workflow.migration import resolve_migration_resolution
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.plan_schema import BUILTIN_QUALITY_GATE_VERIFIERS
from kriya.workflow.pytest_stability import classify_with_baseline_stability, record_regression_decision
from kriya.workflow.pytest_stability import decision_summary as pytest_decision_summary
from kriya.workflow.requirement_closers import SUITE_BASELINE_FROM_RUN
from kriya.workflow.requirements import REQUIREMENTS_UNRESOLVED, blocking_requirements, seed_requirement_obligations
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.run_lifecycle import persist_early_exit_trace, stage_run_trace
from kriya.workflow.semantic_region_authority import find_unauthorized_semantic_changes
from kriya.workflow.stage_apply import apply_terminal_candidate, approval_gate, extract_lessons, prepare_lessons
from kriya.workflow.stage_context import StageContext, StageDeps
from kriya.workflow.toolchain import (
    _check_java_toolchain_mismatch,
    _resolve_java_home_override,
    toolchain_declaration_mutable,
)
from kriya.workflow.validation_baseline import (
    DeltaClassification,
    build_validation_outcome,
    regression_unattributed_diagnosis,
    render_blocking_regression_evidence,
)
from kriya.workflow.verification_coordinator import is_verification_only_unit
from kriya.workflow.worktree import WorktreeSyncError

RunState = Any   # kriya.workflow.state.GenerationState (kriya/workflow/stage_context.py says why it is not imported)


@dataclass(frozen=True)
class DevelopmentRun:
    """What the prelude hands the loop: the loop-invariant attempt context (``kriya.workflow.attempt.AttemptContext``)
    and the names the loop and the candidate acceptance read under their run_generation_workflow names."""

    attempt_ctx: Any
    worktree_path: str
    chain: List[Any]
    max_retries: int
    targeted_max_retries: int
    baseline_suite_run: Callable[..., Any]


async def run_development(ctx: StageContext, deps: StageDeps) -> Optional[Dict[str, Any]]:
    """Stage 4 of the work unit: the prelude, then the retry loop. A dict is the run's result (a prelude stop or the
    approval gate's abort); None = the loop ended and the engine goes on to its trace checkpoint and the final review."""
    prepared = await prepare_development(ctx, deps)
    if isinstance(prepared, dict):
        return prepared
    return await run_attempt_loop(ctx, deps, prepared)


async def prepare_development(ctx: StageContext, deps: StageDeps) -> Union[Dict[str, Any], DevelopmentRun]:
    """Stage 4's prelude: the Developer's standing prompt blocks, the brownfield validation baselines (and the
    ``baseline_indeterminate`` stop), the isolated sandbox (and the worktree-sync refusal), the run's obligation ledger
    and diagnostics store, and the loop-invariant ``AttemptContext``. A dict is the run's result (it stops here)."""
    logger = deps.logger
    state, goal, workspace_path, run_id = ctx.state, ctx.goal, ctx.workspace_path, ctx.run_id
    trace_id, resume_state, milestone_group_id = ctx.trace_id, ctx.resume_state, ctx.milestone_group_id
    stream_callback, approval_callback = ctx.stream_callback, ctx.approval_callback
    web_lookup_query_callback, requirement_set = ctx.web_lookup_query_callback, ctx.requirement_set
    verification_contract, resume_plan = ctx.verification_contract, ctx.resume_plan
    resume_restored_ledger, protected_relpath = ctx.resume_restored_ledger, ctx.protected_relpath
    planned_source_files, recovery_contract_block = ctx.planned_source_files, ctx.recovery_contract_block
    grounded_locations, established_files = ctx.grounded_locations, ctx.established_files
    allowed_write_relpaths, authorized_semantic_regions = ctx.allowed_write_relpaths, ctx.authorized_semantic_regions
    write_scope_mode, required_verification = ctx.write_scope_mode, ctx.required_verification
    runtime_verification_required, strict_spec_compliance = ctx.runtime_verification_required, ctx.strict_spec_compliance
    execution_scope, reopened_owner, grounding_goal = ctx.execution_scope, ctx.reopened_owner, ctx.grounding_goal
    migration_resolution, structured_plan = ctx.migration_resolution, ctx.structured_plan
    current_subtask_id, obligation_ledger = ctx.current_subtask_id, ctx.obligation_ledger
    completed_subtask_ids, work_unit = ctx.completed_subtask_ids, ctx.work_unit
    deterministic_failure_diagnostics = ctx.deterministic_failure_diagnostics
    # What the prelude reads from the design stages, under their names.
    engineering_route, repo_model, se = ctx.engineering_route, ctx.repo_model, ctx.se
    active_skills, skills_prompt = ctx.active_skills, ctx.skills_prompt
    matched_files, related_files = ctx.matched_files, ctx.related_files
    retrieval_member_hints, retrieval_symbol_loci = ctx.retrieval_member_hints, ctx.retrieval_symbol_loci
    learned_rag_context, plan = ctx.learned_rag_context, ctx.plan
    design, architect_files, ownership_findings = ctx.design, ctx.architect_files, ctx.ownership_findings
    active_skill_rules_snapshot = ctx.active_skill_rules_snapshot

    # 4. Developer & Quality Gates (Auto-debugging loop)
    deps._log_phase_banner("DEVELOPMENT & QUALITY GATES")
    logger.info("Developer Agent implementing source files...")
    chain = deps.config.llm_chain
    max_retries = max(4, 1 + len(chain)) if chain else 4
    # See GenerationState/RetryBudgets in kriya/workflow/state.py for the
    # rationale behind each of these counters/flags/caches - state itself
    # was already constructed at the top of this method.
    TARGETED_MAX_RETRIES = 3

    # Rendered once (design does not change across retries) and appended to the
    # full-set task description on every attempt, so the Developer sees an
    # explicit, unambiguous checklist of what the design requires BEFORE
    # generating - not just a punitive check afterward. This is the prevention
    # half of the completeness fix; the missing-file recovery retry below is the
    # cheaper, targeted recovery half for when prevention still doesn't work.
    from kriya.workflow.generation_manifest import build_generation_manifest

    generation_manifest = build_generation_manifest(architect_files)
    required_files_prompt_block = generation_manifest.render_prompt()
    _expected_files_upfront = generation_manifest.ordered_paths
    # basename -> full path, built once from the already-resolved architect_files
    # list (see the Architect call above) so a missing-file recovery retry (below)
    # can resolve a bare basename back to its real path via a simple lookup instead
    # of re-scanning the design's prose with a regex. First occurrence wins on a
    # basename collision across two different directories - a deterministic,
    # rare-edge-case tie-break, not expected to matter in practice.
    _architect_basename_to_path: Dict[str, str] = {}
    for _f in architect_files:
        _architect_basename_to_path.setdefault(os.path.basename(_f), _f)
    # Same prevention-over-punishment pattern as required_files_prompt_block
    # above, for a different completeness failure: a full-set regeneration of
    # pom.xml naturally rewrites it to match the current goal, and can
    # silently drop an existing, goal-irrelevant dependency from an earlier
    # milestone in the process. The reactive "Dependency regression" compile
    # check (PolymorphicValidator.run_compile_check) already catches this
    # after the fact, and _build_full_set_retry_prompt already shows the
    # model its own prior attempt's file content as reference - confirmed
    # live that neither of those was sufficient on their own: the tug-of-war
    # recurred and even worsened across repeated full-set retries in the
    # golden Ignite+Qpid validation (M2 attempts 2 and 4). Computed once from
    # workspace_path's pom.xml (the same stable "original" reference the
    # regression check itself compares against, via original_workspace_path)
    # rather than the worktree's possibly-already-regressed prior attempt.
    # Computed once, before the loop, and threaded into every prompt builder
    # (generation and all three retry modes alike, since they all funnel
    # through the same task_desc string) - a standing invariant, not a
    # reactive one, unlike required_dependencies_prompt_block below which
    # only has something concrete to preserve once a project exists.
    ecosystem_invariant_block = deps._build_ecosystem_invariant_block(repo_model)

    # Stack-agnostic generalization of skills/ignite-java17/rules.txt's own
    # start-once/reuse/close-once rule, which only ever fires for an
    # Ignite-tagged goal. A goal using any other stateful resource (a JDBC
    # connection, a broker client, a thread pool) gets no equivalent
    # instruction cold-start, with no skill yet to learn it from. Threaded
    # through every prompt builder the same way ecosystem_invariant_block is -
    # a standing invariant present on every attempt, not a reactive one.
    resource_lifecycle_block = deps.RESOURCE_LIFECYCLE_HEADER

    # Threaded through every prompt builder alongside ecosystem_invariant_block/
    # resource_lifecycle_block - see VERIFICATION_CONTRACT_HEADER's own rationale
    # comment in kriya/workflow/retry_prompts.py for why this exists.
    verification_contract_block = deps.VERIFICATION_CONTRACT_HEADER

    required_dependencies_prompt_block = ""
    _original_pom_path = os.path.join(workspace_path, "pom.xml")
    if os.path.exists(_original_pom_path):
        from kriya.tools.validate import get_pom_dependencies
        _existing_dependencies = get_pom_dependencies(_original_pom_path)
        if _existing_dependencies:
            required_dependencies_prompt_block = (
                "\n\nExisting Maven dependencies (already present in pom.xml before this goal - "
                "you must preserve ALL of these even if the current goal doesn't need them; "
                "do not silently drop any while editing pom.xml):\n"
                + "\n".join(f"- {d}" for d in _existing_dependencies)
                + "\n\nBefore adding ANY new dependency, check whether the package/class you need "
                "is already used successfully in the Existing Code Base Context shown below - if an "
                "import already appears there and that code is known-working, it is ALREADY resolvable "
                "through one of the dependencies listed above (often transitively) and you must NOT add "
                "a new, separate dependency for it. Confirmed live as a real bug: a retry added an "
                "explicit javax.jms:jms:1.1 dependency (which doesn't exist on Maven Central) for a "
                "'javax.jms.*' import that was already compiling successfully via the existing "
                "qpid-jms-client dependency alone - the addition was both wrong and unnecessary."
            )

    # VAL-001 brownfield validation baselining (2026-09-18,
    # kriya/workflow/validation_baseline.py) - deliberately BEFORE the
    # sandbox worktree is created a few lines below and before any
    # Developer call anywhere in this method: the ONE point in this
    # method where `workspace_path` is still guaranteed pristine
    # (candidate writes only ever reach `worktree_path`, confirmed
    # untouched at this line - see this same ordering guarantee already
    # documented a few hundred lines above for checkpoint fingerprinting).
    # Opt-in only (autonomy.brownfield_baseline_target_test /
    # brownfield_full_regression_baseline_policy, both default to a
    # complete no-op) - a run that doesn't configure either behaves
    # byte-identically to before this package existed (proven by
    # capture_brownfield_baselines() itself invoking neither injected
    # callable at all when both are unset). brownfield_baseline_
    # target_test may be a single string or an ordered list (VAL-001
    # G1-R3: several specific targets at once) - capture_brownfield_
    # baselines() normalizes it once; this call site passes it straight
    # through unmodified either way.
    autonomy_baseline_cfg = deps.config.autonomy
    # PRD-024 (kriya/workflow/baseline_policy.py): `auto` is decided here,
    # deterministically, from the route, the planned files and the
    # repository's tests - before the first Developer mutation. A
    # triggered `auto` is as binding as `required`.
    baseline_environment_identity, decide_auto_baseline, effective_baseline_policy = deps.load_baseline_policy()
    configured_baseline_policy = autonomy_baseline_cfg.brownfield_full_regression_baseline_policy
    auto_baseline_decision = None
    if configured_baseline_policy == "auto":
        try:
            auto_baseline_decision = await asyncio.to_thread(
                decide_auto_baseline, engineering_route, architect_files or [], workspace_path,
                workspace_revision=deps.compute_workspace_content_hash(workspace_path),
            )
        except Exception as exc:
            logger.warning(f"Brownfield baseline auto policy could not be evaluated: {exc}")
    full_regression_policy = effective_baseline_policy(configured_baseline_policy, auto_baseline_decision)
    state.validation_baseline_policy = full_regression_policy
    state.record_event(RunEvent(
        kind="validation_baseline.policy", attempt=0, source="workflow.run_generation_workflow",
        authority=EventAuthority.AUXILIARY,
        message=f"brownfield full-regression baseline: {configured_baseline_policy} -> {full_regression_policy}",
        details={"configured": configured_baseline_policy, "effective": full_regression_policy,
                 "auto_decision": auto_baseline_decision.to_dict() if auto_baseline_decision else None},
    ))
    baseline_environment = None
    baseline_runner = None
    if full_regression_policy == "required" or autonomy_baseline_cfg.brownfield_baseline_target_test:
        try:
            baseline_environment = baseline_environment_identity(
                workspace_path, autonomy_baseline_cfg, goal=goal)
        except Exception as exc:
            logger.warning(f"Baseline environment identity unavailable: {exc}")
        # REG-R1: a pytest baseline must carry per-test evidence; one that
        # does not (an older checkpoint) is re-established, never reused.
        baseline_runner = PolymorphicValidator(
            workspace_path, original_workspace_path=workspace_path, autonomy_cfg=autonomy_baseline_cfg,
        ).test_runner()
    # The baseline's verification call. REG-R1: the same call replays the
    # untouched baseline for stability, so it runs in the same context.
    def baseline_suite_run(target_test):
        return PolymorphicValidator(
            workspace_path, original_workspace_path=workspace_path, autonomy_cfg=autonomy_baseline_cfg,
        ).run_tests(target_test=target_test)

    baseline_capture = deps.capture_brownfield_baselines(
        run_id=run_id,
        target_test=autonomy_baseline_cfg.brownfield_baseline_target_test,
        full_regression_policy=full_regression_policy,
        environment_identity=baseline_environment,
        run_validator=baseline_suite_run,
        compute_revision=lambda: deps.compute_workspace_content_hash(workspace_path),
        resume_baseline_targeted=(resume_state or {}).get("validation_baseline_targeted"),
        resume_baseline_full_regression=(resume_state or {}).get("validation_baseline_full_regression"),
        prior_full_regression=deps.prior_full_suite_evidence(),
        require_pytest_evidence=baseline_runner == "pytest",
    )
    for _rejection in baseline_capture.reuse_rejections:
        state.record_event(RunEvent(
            kind="validation_baseline.reuse_rejected", attempt=0,
            source="workflow.run_generation_workflow", authority=EventAuthority.AUXILIARY,
            message=f"baseline not reused ({_rejection}); re-established by a fresh capture",
            details={"reason": _rejection},
        ))
    _resumed_stability = (resume_state or {}).get("validation_baseline_pytest_stability")
    if isinstance(_resumed_stability, dict):
        # Each entry is keyed by its own binding; a stale one simply never matches.
        state.pytest_stability_cache.update(_resumed_stability)
    if baseline_capture.full_regression_source is not None:
        state.record_event(RunEvent(
            kind="validation_baseline.full_regression_source", attempt=0,
            source="workflow.run_generation_workflow", authority=EventAuthority.AUXILIARY,
            message=f"full-regression baseline: {baseline_capture.full_regression_source}",
            details={"source": baseline_capture.full_regression_source,
                     "baseline_run_id": getattr(baseline_capture.full_regression, "run_id", None),
                     "workspace_revision": getattr(
                         baseline_capture.full_regression, "workspace_revision", None)},
        ))
    state.validation_baseline_targeted = baseline_capture.targeted
    state.validation_baseline_full_regression = baseline_capture.full_regression
    if baseline_capture.targeted is not None:
        if baseline_capture.targeted.status == "captured":
            logger.info(
                "Brownfield PRE-mutation targeted validation baseline captured "
                f"(success={baseline_capture.targeted.outcome.success})."
            )
        else:
            logger.warning(
                "Brownfield PRE-mutation targeted validation baseline is INDETERMINATE: "
                f"{baseline_capture.targeted.indeterminate_reason}"
            )
    if baseline_capture.hard_stop_reason is not None and auto_baseline_decision is not None:
        # PRD-024: say which deterministic signals made the baseline required.
        baseline_capture = dataclasses.replace(baseline_capture, hard_stop_reason=(
            "the 'auto' baseline policy required a pristine PRE baseline for this change ("
            + "; ".join(auto_baseline_decision.reasons) + ") - "
            + baseline_capture.hard_stop_reason
        ))
    if baseline_capture.hard_stop_reason is not None:
        # FAILURE_BEHAVIOR: never silently proceed assuming a green
        # baseline - an explicit, distinct terminal status, same shape
        # as the existing knowledge_gap/planner_output_incomplete early
        # returns just above in this same method.
        logger.error(f"Brownfield validation baseline REQUIRED but indeterminate: {baseline_capture.hard_stop_reason}")
        persist_early_exit_trace(deps, state, stage_run_trace(ctx), status="baseline_indeterminate",
                                 failure_category="baseline_indeterminate")
        return {
            "status": "baseline_indeterminate",
            "reason": baseline_capture.hard_stop_reason,
            "plan": plan,
            "design": design,
            "goal": goal,
            "workspace_path": workspace_path,
            "run_id": trace_id,
            "quality_gates_passed": False,
        }

    # Create isolated git worktree sandbox
    worktree_path = workspace_path
    try:
        worktree_path = deps.create_git_worktree(workspace_path)
        if os.path.realpath(worktree_path) == os.path.realpath(workspace_path):
            # BACKEND-READINESS-V2 (D0): the invariant create_git_worktree already keeps (a sandbox path or an error),
            # asserted where the direct path depends on it. In-place generation could not even commit (the terminal
            # revision guard sees its own writes as a conflict) and left unsafe bytes behind on a refused run.
            raise RuntimeError("the isolated generation sandbox resolves to the application workspace itself")
        logger.info(f"Isolated sandbox worktree created at: {worktree_path}")
        mark_run_stage(workspace_path, RunLifecycle.CANDIDATE)
    except WorktreeSyncError as sync_error:
        # FILE-INTEGRITY-CONTRACT-001: a sandbox that does not hold the
        # workspace's exact bytes is a typed deterministic stop, never a
        # generic sandbox error and never verified against.
        logger.error(f"Worktree sync refused: {sync_error}")
        category = sync_error.reason_code.lower()
        persist_early_exit_trace(deps, state, stage_run_trace(ctx), status="failure", failure_category=category)
        return {
            "status": "failure", "failure_category": category, "reason_codes": [sync_error.reason_code],
            "error": str(sync_error), "plan": plan, "design": design, "goal": goal,
            "workspace_path": workspace_path, "run_id": trace_id, "files": [],
            "quality_gates_passed": False,
        }
    except RepositoryContentFilterRefused:
        raise                       # S19 Q1: the typed refusal reaches the run's caller as itself
    except Exception as e:
        raise RuntimeError(
            f"Failed to create an isolated generation sandbox: {e}. "
            "Refusing to generate directly in the application workspace."
        ) from e

    # P2: a plan-scope re-invocation keeps the grounded loci of the
    # failure that widened its scope (revision-bound; a stale one is dropped).
    if grounded_locations:
        deps._seed_grounded_loci(state, worktree_path, grounded_locations)

    # Resolved ONCE, here, against workspace_path BEFORE any Developer
    # write happens (worktree_path above is an isolated copy - writes
    # never touch workspace_path directly until the approval/copy-back
    # step at the very end of this call) - mirrors write_scope_mode's own
    # "resolved once, read everywhere" pattern below. A caller that
    # already resolved this identity once for the whole run (e.g.
    # WorkflowController, across several bounded-subtask calls) passes
    # migration_resolution directly so every subtask's own attempt reuses
    # the SAME resolution rather than each one re-deriving its own from
    # its own (already-progressed) plan_workspace_path. See kriya/
    # workflow/migration.py's own docstring (PRV-05 run 6, 2026-08-28)
    # for why re-resolving per call site is itself the defect this
    # closes.
    resolved_migration_resolution = (
        migration_resolution if migration_resolution is not None
        else resolve_migration_resolution(goal, workspace_path)
    )
    # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
    # A caller that already created one per-run ledger (WorkflowController,
    # across several bounded-subtask calls) passes it through so every
    # subtask's obligations accumulate into the SAME instance; a plain
    # Legacy call gets its own fresh one, mirroring migration_resolution's
    # own "resolved once, reused, or created fresh for this call" pattern.
    resolved_obligation_ledger = obligation_ledger if obligation_ledger is not None else ObligationLedger()
    # PRD-008: a reused candidate comes with the effective ledger it was
    # verified against (its fingerprint matched the checkpoint's). The
    # input ledger matched too, so this only adds what that run recorded;
    # restored in place because callers share one ledger object.
    if resume_plan is not None and resume_plan.reuse_candidate and resume_restored_ledger is not None:
        resolved_obligation_ledger.restore_from(resume_restored_ledger)
    if requirement_set is not None:
        # PRD-020: every original requirement is tracked (PENDING until the
        # verifier records an outcome); idempotent over a restored ledger.
        seed_requirement_obligations(resolved_obligation_ledger, requirement_set)
        if verification_contract is not None:
            # VERIFICATION-CONTRACT-003 (D1): the contract's structural
            # non-claims need no closure; recorded once, visible everywhere.
            from kriya.workflow.contract_compilation import record_contract_dispositions, record_contract_non_claims

            record_contract_non_claims(resolved_obligation_ledger, requirement_set, verification_contract,
                                       source="workflow.verification_contract")
            # BACKEND-READINESS-004 (D3): the operator's sealed dispositions, recorded once as audit.
            record_contract_dispositions(resolved_obligation_ledger, requirement_set, verification_contract,
                                         source="workflow.verification_contract")
    if ownership_findings:
        deps.record_findings(resolved_obligation_ledger, ownership_findings, revision=0,
                        source="workflow.architect_file_resolution")
    # PRV-17 (2026-09-08, P7 efficiency finding) - kriya/workflow/
    # deterministic_failure_diagnostic.py. Same "resolved once, reused
    # across every bounded-subtask call, or created fresh for a plain
    # Legacy call" pattern as resolved_obligation_ledger immediately
    # above - deliberately a separate store, not folded into the
    # ObligationLedger itself (see that module's own docstring for why).
    resolved_deterministic_failure_diagnostics = (
        deterministic_failure_diagnostics if deterministic_failure_diagnostics is not None
        else DeterministicFailureDiagnosticStore()
    )

    # Loop-invariant - nothing in this object is reassigned across retry
    # attempts, so it's built once here rather than reconstructed per
    # iteration. See kriya/workflow/attempt.py for what actually happens
    # inside a single attempt.
    attempt_ctx = deps.AttemptContext(
        goal=goal,
        plan=plan,
        design=design,
        workspace_path=workspace_path,
        worktree_path=worktree_path,
        architect_files=architect_files,
        resume_state=resume_state,
        run_id=run_id,
        skills_prompt=skills_prompt,
        learned_rag_context=learned_rag_context,
        matched_files=matched_files,
        related_files=related_files,
        planned_source_files=tuple(planned_source_files or ()),
        ecosystem_invariant_block=ecosystem_invariant_block,
        resource_lifecycle_block=resource_lifecycle_block,
        verification_contract_block=verification_contract_block,
        recovery_contract_block=recovery_contract_block,
        required_files_prompt_block=required_files_prompt_block,
        required_dependencies_prompt_block=required_dependencies_prompt_block,
        expected_files_upfront=_expected_files_upfront,
        architect_basename_to_path=_architect_basename_to_path,
        chain=chain,
        targeted_max_retries=TARGETED_MAX_RETRIES,
        stream_callback=stream_callback,
        approval_callback=approval_callback,
        defer_execution_until_source_review_enabled=True,
        active_skills=active_skills,
        active_skill_rules_snapshot=active_skill_rules_snapshot,
        developer=deps.developer,
        run_verifier=deps.run_verifier,
        spec_compliance=deps.spec_compliance,
        skill_engine=se,
        kernel=deps.kernel,
        max_retries=max_retries,
        web_lookup_query_callback=web_lookup_query_callback,
        approve_web_lookup=deps.approve_web_lookup,
        generation_dependencies={
            entry.path: list(entry.depends_on)
            for entry in generation_manifest.entries
        },
        established_files=established_files or [],
        retrieval_member_hints=retrieval_member_hints,
        retrieval_symbol_loci=retrieval_symbol_loci,
        protected_relpath=protected_relpath,
        allowed_write_relpaths=list(allowed_write_relpaths or []),
        authorized_semantic_regions=list(authorized_semantic_regions or []),
        required_verification=list(required_verification or []),
        contract_feedback=deps.build_contract_feedback(
            deps.engine, worktree_path, workspace_path, deps.config.autonomy),
        # CANDIDATE-GATE-BASELINE-POLICY-001: the baseline's own
        # verification call, for the candidate gates' attribution.
        baseline_suite_replay=baseline_suite_run,
        # Review F1 / MILESTONE-ZERO-COMMIT-COMPLETION-001: a milestone's zero-change result is decided by the
        # milestone driver (kriya/workflow/milestones.py _complete_milestone: a deterministic no-change proof
        # completes it, a refusal fails it typed); the integration pass is decided by the plan-level original-
        # requirement verification and its gates (PRD-020 - a VIOLATED requirement always blocks; an unverified
        # one blocks under the "block" requirement policies the production profile seals or, whatever the policy,
        # when the verifier reported it missing - GR-R0; INTEGRATION-PASS-REQUIREMENT-POLICY-001). A direct goal has no
        # such decider: the typed stop below is its own.
        no_change_verifier_downstream=milestone_group_id is not None,
        # Resolved ONCE, here, mirroring AuthorizedFileWriter's own
        # backward-compatible inference (kriya/policy/filesystem.py) so
        # every write-gate call site downstream (kriya/workflow/
        # attempt.py) reads the SAME unambiguous value instead of each
        # re-deriving it from allowed_write_relpaths' truthiness - see
        # WriteScopeMode's own docstring for the PRV-05 incident this
        # closes. A caller that already knows its scope (e.g.
        # WorkflowController's bounded-subtask execution, for a
        # verification-role subtask) passes write_scope_mode directly;
        # every other caller keeps today's behavior exactly.
        write_scope_mode=(
            write_scope_mode if write_scope_mode is not None
            else (WriteScopeMode.ALLOWLIST if allowed_write_relpaths else WriteScopeMode.UNRESTRICTED)
        ),
        runtime_verification_required=(
            goal_requires_runtime_behavior(goal)
            if runtime_verification_required is None
            else runtime_verification_required
        ),
        strict_spec_compliance=strict_spec_compliance,
        execution_scope=execution_scope,
        reopened_owner=reopened_owner,
        grounding_goal=grounding_goal,
        exit_authority_goal=(
            (work_unit.authoritative_goal if work_unit is not None else None) or grounding_goal or ""
        ),
        migration_resolution=resolved_migration_resolution,
        structured_plan=structured_plan,
        current_subtask_id=current_subtask_id,
        obligation_ledger=resolved_obligation_ledger,
        completed_subtask_ids=completed_subtask_ids or frozenset(),
        deterministic_failure_diagnostics=resolved_deterministic_failure_diagnostics,
        resume_plan=resume_plan,
        requirement_set=requirement_set,
    )

    # What the review, approval/apply and lesson stages (E4) read besides the design stages' outputs (the run's
    # grounding goal, work unit and trace identity are on the StageContext from its construction).
    ctx.worktree_path, ctx.resolved_obligation_ledger = worktree_path, resolved_obligation_ledger
    return DevelopmentRun(attempt_ctx=attempt_ctx, worktree_path=worktree_path, chain=chain, max_retries=max_retries,
                          targeted_max_retries=TARGETED_MAX_RETRIES, baseline_suite_run=baseline_suite_run)


async def run_attempt_loop(ctx: StageContext, deps: StageDeps, run: DevelopmentRun) -> Optional[Dict[str, Any]]:
    """Stage 4's retry loop: while the loop head's ``AttemptPlan`` (``attempt_lifecycle.plan_attempt``: the
    ``retry_policy.decide_for_state`` decision, typed) admits another attempt, one iteration
    (``attempt_lifecycle.run_iteration``): the planned Developer + Quality Gates attempt (Best-of-N on the first one when
    configured) and, when it passes, ``accept_candidate``; a failed attempt goes to
    ``retry_strategy.handle_attempt_failure``. A dict is the run's result (the approval gate's abort); None = the loop
    ended (applied, or every budget spent)."""
    logger = deps.logger
    state, workspace_path = ctx.state, ctx.workspace_path
    attempt_ctx, worktree_path, chain = run.attempt_ctx, run.worktree_path, run.chain
    max_retries, TARGETED_MAX_RETRIES = run.max_retries, run.targeted_max_retries
    fallback_routing_for_state = deps.load_fallback_routing()
    while (plan := plan_attempt(
        state, max_retries=max_retries,
        targeted_max_retries=TARGETED_MAX_RETRIES,
        has_fallback_model=fallback_routing_for_state(state, deps.config, chain, worktree_path).available,
        verification_only=is_verification_only_unit(attempt_ctx.write_scope_mode, attempt_ctx.required_verification),
        best_of_n=deps.config.autonomy.best_of_n_first_attempt, sandboxed=worktree_path != workspace_path,
    )).should_continue:
        outcome = await run_iteration(
            plan, state, attempt_ctx, run_attempt=deps.run_attempt, load_best_of_n=deps.load_best_of_n,
            # Stages 4.x-7 on the candidate this attempt produced: None = applied, the loop ends; a dict = the run
            # stops here with that result (the approval gate's abort).
            accept=lambda: accept_candidate(ctx, deps, run),
            handle_attempt_failure=deps.handle_attempt_failure, phase_advanced=deps.RecoveryPhaseAdvanced,
            best_of_n_recorded=deps.BestOfNFailureRecorded, logger=logger,
        )
        if outcome.end is IterationEnd.RESULT:
            return outcome.result
        if outcome.end is not IterationEnd.RETRY:
            break
    return None


async def accept_candidate(ctx: StageContext, deps: StageDeps, run: DevelopmentRun) -> Optional[Dict[str, Any]]:
    """The candidate an attempt produced, through the pre-apply success boundary: its verified-bytes binding, the
    required verification, the post-generation ownership findings, the PRD-020 requirement closers, the static-analysis
    and semantic-safety gates, the candidate checkpoint, the approval gate (stage_apply), the held executing gates
    (Gap B), the lesson preparation, the terminal regression (``run_terminal_regression``), the developer_success
    checkpoint, the terminal apply and the lessons. Raises ``QualityGateFailure`` (the loop's failure handling) on a
    failed gate; returns the approval gate's abort result, or None once the candidate is applied."""
    logger = deps.logger
    state, goal, workspace_path, run_id = ctx.state, ctx.goal, ctx.workspace_path, ctx.run_id
    plan, design, owner_candidates = ctx.plan, ctx.design, ctx.owner_candidates
    approval_callback, required_verification = ctx.approval_callback, ctx.required_verification
    requirement_set, verification_contract = ctx.requirement_set, ctx.verification_contract
    allowed_write_relpaths, write_scope_mode = ctx.allowed_write_relpaths, ctx.write_scope_mode
    structured_plan, current_subtask_id, work_unit = ctx.structured_plan, ctx.current_subtask_id, ctx.work_unit
    resolved_obligation_ledger, _save_stage_checkpoint = ctx.resolved_obligation_ledger, ctx.save_stage_checkpoint
    attempt_ctx, worktree_path, baseline_suite_run = run.attempt_ctx, run.worktree_path, run.baseline_suite_run
    # CANDIDATE-VERIFIED-DIGEST-BINDING-001: the candidate gates
    # passed on exactly these bytes. Everything after this
    # (requirements, static analysis, approval, terminal
    # regression) judges the same candidate, and the terminal
    # commit refuses any batch that differs from this binding.
    if state.verification_tree_binding is not None:
        state.verification_tree_binding.check("terminal_binding")
    state.verified_candidate_binding = deps._bind_direct_candidate(worktree_path, workspace_path, state)

    # Authoritative subtask verification is part of the pre-apply
    # success boundary.  A skipped/unavailable check or unresolved
    # judgment must never reach approval or copy sandbox files into
    # the real workspace merely because the legacy aggregate gate
    # boolean remained true.
    def _raise_if_required_verification_unresolved(execution_pending: bool = False) -> None:
        # execution_pending: the executing gates are held until the source is approved (Gap B), so only the requirements that those gates resolve (the builtin
        # compile/test tools, a judgment that needs runtime execution) wait for them; every other unresolved requirement still stops the run before any approval.
        checkable = [
            item for item in required_verification or []
            if not (execution_pending and (
                (item.get("type") == "tool" and item.get("tool_name") in BUILTIN_QUALITY_GATE_VERIFIERS)
                or (item.get("type") == "judgment" and item.get("requires_runtime_execution") is True)))
        ]
        required_evidence = deps.required_verification_evidence(
            checkable,
            quality_gates_passed=True,
            gate_outcomes=state.gate_outcomes,
        )
        unresolved_requirements = [
            item.get("description", "") for item in required_evidence
            if item.get("passed") is not True
        ]
        if unresolved_requirements:
            message = (
                "REQUIRED VERIFICATION UNRESOLVED: no authoritative passing evidence "
                f"was produced for {unresolved_requirements!r}; refusing pre-apply success."
            )
            failure = Failure(
                type="verification_infrastructure_failure",
                message=message,
                raw_output=message,
                attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

    # Gap B: when the runtime execution is held until the person has approved the source (human-in-the-loop), the requirements that need it cannot be judged yet;
    # the complete check runs again right after that deferred step (below), before anything is applied.
    _raise_if_required_verification_unresolved(execution_pending=attempt_ctx.deferred_executing_gates is not None)

    # PRD-022: near-duplicates the candidate actually created,
    # against the grounded owners PRD-021 found before planning.
    # Advisory evidence for review and approval - never a gate.
    if owner_candidates:
        try:
            deps._record_post_generation_ownership_findings(
                state, owner_candidates, resolved_obligation_ledger, worktree_path, goal,
                current_subtask_id, deps.parse_ownership_justifications(str(design or "")),
            )
        except Exception as exc:
            logger.warning(f"Post-generation ownership findings unavailable: {exc}")

    async def _close_and_check_requirements() -> None:
        """PRD-020 closers + the blocking decision. They EXECUTE the candidate's tests (named tests, acceptance, suite preservation), so in human-in-the-loop mode they run after
                    the source approval together with the other executing gates (Gap B); otherwise at their original place."""
        # PRD-020: the user's original requirements are part of the
        # pre-apply success boundary. Only the verifier's recorded
        # outcomes count (never plan/design/review prose); what a
        # non-satisfied outcome does is the requirement policy's call.
        if requirement_set is not None:
            autonomy_policy = deps.config.autonomy
            # "Do not modify any other file": decided from what the run
            # actually changed (this candidate plus the run's committed
            # history) against the files the goal itself names.
            try:
                scope_closures = await asyncio.to_thread(
                    deps.close_requirements_by_mutation_scope, resolved_obligation_ledger, requirement_set,
                    worktree_path, workspace_path, candidate_paths=state.all_files_written,
                    revision=state.attempt_number,
                )
            except Exception as exc:
                scope_closures = []
                logger.warning(f"Requirement mutation-scope evidence unavailable: {exc}")
            # FS-1C2 B2-a: the operator's acceptance file judges the
            # behaviour claims it covers on this candidate.
            try:
                acceptance_closures = await asyncio.to_thread(
                    deps.close_requirements_with_acceptance_tests, deps.config.autonomy,
                    resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                    acceptance=deps.bound_acceptance(deps.engine), approval=deps.bound_approval(deps.engine),
                    modified=state.all_files_written, revision=state.attempt_number,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan,
                    ),
                    contract=verification_contract,
                )
            except Exception as exc:
                acceptance_closures = []
                logger.warning(f"Requirement acceptance evidence unavailable: {exc}")
            # VERIFICATION-CONTRACT-003: the goal's compiled examples, the
            # API predicate, the test-addition record and the documentation
            # referent - each closer bound by the sealed contract.
            contract_closures: List[Dict[str, Any]] = []
            for closer_name, closer_call in (
                ("external authority", lambda: deps.close_requirements_with_authority_bundle_evidence(
                    deps.config.autonomy, resolved_obligation_ledger, requirement_set,
                    verification_contract, worktree_path, workspace_path, engine=deps.engine,
                    revision=state.attempt_number,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan))),
                ("derived examples", lambda: deps.close_requirements_with_derived_examples(
                    deps.config.autonomy, resolved_obligation_ledger, requirement_set, worktree_path,
                    workspace_path, engine=deps.engine, modified=state.all_files_written,
                    revision=state.attempt_number, contract=verification_contract,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan))),
                ("API preservation", lambda: deps.close_requirements_by_api_preservation(
                    resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                    workspace_path, candidate_paths=state.all_files_written, revision=state.attempt_number)),
                ("test addition", lambda: deps.close_requirements_by_test_addition(
                    resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                    workspace_path, revision=state.attempt_number)),
                ("documentation", lambda: deps.close_requirements_by_documentation(
                    resolved_obligation_ledger, requirement_set, verification_contract, worktree_path,
                    revision=state.attempt_number)),
            ):
                try:
                    contract_closures += await asyncio.to_thread(closer_call)
                except Exception as exc:
                    logger.warning(f"Requirement {closer_name} evidence unavailable: {exc}")
            # An UNVERIFIED (cannot confirm from code) requirement whose
            # own text names existing tests is closed only by running
            # exactly those tests on this candidate.
            try:
                closures = await asyncio.to_thread(
                    deps.close_requirements_with_named_tests, deps.config.autonomy,
                    resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                    modified=state.all_files_written, revision=state.attempt_number,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan,
                    ),
                    java_home_override=state.java_home_override, contract=verification_contract,
                )
            except Exception as exc:
                closures = []
                logger.warning(f"Requirement closure by named tests unavailable: {exc}")
            # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: the run's mutation
            # record decides "do not change any existing test"; the
            # candidate's own complete, green full suite closes
            # "every existing test must keep passing".
            try:
                immutability_closures = await asyncio.to_thread(
                    deps.close_requirements_by_test_immutability, resolved_obligation_ledger, requirement_set,
                    worktree_path, workspace_path, candidate_paths=state.all_files_written,
                    revision=state.attempt_number, contract=verification_contract,
                )
            except Exception as exc:
                immutability_closures = []
                logger.warning(f"Requirement test-immutability evidence unavailable: {exc}")
            # OD-3: the files the goal froze by name, judged from the same mutation record.
            try:
                frozen_closures = await asyncio.to_thread(
                    deps.close_requirements_by_file_immutability, resolved_obligation_ledger, requirement_set,
                    worktree_path, workspace_path, candidate_paths=state.all_files_written,
                    revision=state.attempt_number, contract=verification_contract,
                )
            except Exception as exc:
                frozen_closures = []
                logger.warning(f"Requirement file-immutability evidence unavailable: {exc}")
            try:
                suite_closures = await asyncio.to_thread(
                    deps.close_requirements_by_suite_preservation, deps.config.autonomy,
                    resolved_obligation_ledger, requirement_set, worktree_path, workspace_path,
                    revision=state.attempt_number, candidate_paths=state.all_files_written,
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        write_scope_mode, allowed_write_relpaths, structured_plan,
                    ),
                    java_home_override=state.java_home_override, contract=verification_contract,
                    # SUITE-PRESERVATION-CLOSURE-BASELINE-ATTRIBUTION-001: the run's own captured
                    # baseline, stability cache and replay - no second baseline run on this path.
                    goal=goal, suite_baseline=state.validation_baseline_full_regression,
                    stability_cache=state.pytest_stability_cache, baseline_suite_run=baseline_suite_run,
                    baseline_source=SUITE_BASELINE_FROM_RUN,
                    run_baseline_policy=state.validation_baseline_policy,
                )
            except Exception as exc:
                suite_closures = []
                logger.warning(f"Requirement suite-preservation evidence unavailable: {exc}")
            closures = (scope_closures + acceptance_closures + contract_closures + closures
                        + immutability_closures + frozen_closures + suite_closures)
            if closures:
                state.record_event(RunEvent(
                    kind="requirement.closure", attempt=state.attempt_number,
                    source="workflow.requirement_closure", authority=EventAuthority.AUTHORITATIVE,
                    message="unverified requirement closure: " + ", ".join(
                        f"{c['requirement']}={'closed' if c['closed'] else 'open'}" for c in closures),
                    details={"requirement_set_digest": requirement_set.digest, "closures": closures},
                ))
            blocking = blocking_requirements(
                resolved_obligation_ledger, requirement_set,
                unknown_policy=autonomy_policy.requirement_unknown_policy,
                unverified_policy=autonomy_policy.requirement_unverified_policy,
            )
            if blocking:
                detail = "; ".join(
                    f"{req.id} ({outcome.value}): {req.text}" for req, outcome in blocking)
                message = (
                    f"{REQUIREMENTS_UNRESOLVED}: original requirement(s) without accepted "
                    f"evidence - {detail}"
                )
                failure = Failure(
                    type="requirements_unresolved", message=message, raw_output=message,
                    source="orchestrator", attempt=state.attempt_number,
                    diagnostics={
                        "reason_code": REQUIREMENTS_UNRESOLVED,
                        "requirement_set_digest": requirement_set.digest,
                        "blocking": {req.id: outcome.value for req, outcome in blocking},
                    },
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)

    if attempt_ctx.deferred_executing_gates is None:
        await _close_and_check_requirements()


    # PRD-031A: the static-analysis gate, on the exact batch the
    # terminal apply below commits (it re-materializes it, and the
    # commit guard proves the two are byte-identical). Before the
    # approval gate, so a human sees the outcome. A non-permitting
    # result is a deterministic stop, never a Developer retry, and
    # its message carries no scanner text.
    deps._run_static_analysis_gate(
        deps.config, state, worktree_path=worktree_path, workspace_path=workspace_path,
        run_id=run_id,
        unit_id=f"{work_unit.work_unit_id if work_unit is not None else 'direct'}-attempt{state.attempt_number}",
    )

    # BACKEND-READINESS-V2: the exception-masking rule on the same final batch.
    deps._run_semantic_safety_gate(state, worktree_path=worktree_path, workspace_path=workspace_path)

    # Candidate-only checkpoint: generation and inner gates passed, but
    # terminal regression and application have not. Its name and payload
    # preserve that distinction for resume and external inspection.
    # PRD-008: the candidate is reusable only if every written
    # file was captured exactly (strict UTF-8, no newline
    # translation); otherwise no digest is stored and a resume
    # regenerates it instead of rebuilding a partial candidate.
    final_files_for_checkpoint = {}
    candidate_snapshot_complete = True
    for filepath in state.all_files_written:
        try:
            with open(os.path.join(worktree_path, filepath), "rb") as fh:
                final_files_for_checkpoint[filepath] = fh.read().decode("utf-8")
        except Exception as ex:
            candidate_snapshot_complete = False
            logger.warning(
                f"Candidate file '{filepath}' could not be captured exactly for the "
                f"checkpoint ({type(ex).__name__}: {ex}); a resume will regenerate the candidate."
            )
    candidate_checkpoint_fields = {
        deps.CANDIDATE_HASH_KEY: (
            deps.candidate_snapshot_digest(final_files_for_checkpoint)
            if candidate_snapshot_complete else None
        ),
        deps.EFFECTIVE_LEDGER_KEY: resolved_obligation_ledger.to_snapshot(),
    }
    def _save_candidate_gates_checkpoint(checkpoint_fields: Dict[str, Any], checkpoint_files: Dict[str, str]) -> None:
        _save_stage_checkpoint(
            "candidate_gates_passed",
            effective_obligation_ledger=resolved_obligation_ledger,
            **checkpoint_fields,
            plan=plan,
            design=design,
            final_files=checkpoint_files,
            original_files=state.all_original_contents,
            gate_outcomes=state.gate_outcomes,
            model_hops=state.model_hops,
            retry_count=state.budgets.retry_count,
            targeted_retry_count=state.budgets.targeted_retry_count,
            candidate_gates_passed=True,
            terminal_regression_passed=False,
            overall_attempt_passed=False,
        )

    # Gap B: "candidate gates passed" is only true once the held runtime verification has also run, so a resume can never skip it. Saved after that step when held.
    if attempt_ctx.deferred_executing_gates is None:
        _save_candidate_gates_checkpoint(candidate_checkpoint_fields, final_files_for_checkpoint)

    # 4.5. Pre-Apply Human Approval Gate (kriya/workflow/stage_apply.py::approval_gate, full modularization E4):
    # the change set, the approval triggers, the pre-approval review and the request; an abort (the person
    # rejected the change, or approval was required and nobody can be asked) is this method's result.
    approval_stop, need_human_approval = await approval_gate(
        ctx, deps.late_stage_deps(), attempt_ctx=attempt_ctx,
    )
    if approval_stop is not None:
        return approval_stop

    # Gap B (owner decision 2026-10-15, stronger form): in human-in-the-loop mode every gate that EXECUTES the candidate (POM validation, build, tests, runtime
    # verification) and the requirement closers that run its tests were held back by run_attempt until the person approved the source (a source approval here means
    # "this exact candidate may keep being verified"; nothing is applied to the real workspace until the terminal gate). They run now, against the same bytes: the
    # byte binding taken when the candidate gates were reached must still hold before each execution boundary and after the last, the self-correction micro-loops
    # are off, and a failure re-enters the ordinary retry loop whose new candidate is reviewed again.
    deferred_runtime = attempt_ctx.deferred_executing_gates
    attempt_ctx.deferred_executing_gates = None
    if deferred_runtime is not None:
        if not (need_human_approval and approval_callback):
            message = (
                "EXECUTION_DEFERRED_WITHOUT_SOURCE_REVIEW: verification was held for a source review that was not asked. "
                "Nothing was run and nothing was applied."
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message, raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        reviewed_binding = state.verified_candidate_binding

        def _candidate_is_the_reviewed_one(reviewed: Any = reviewed_binding) -> bool:
            current_binding = deps._bind_direct_candidate(worktree_path, workspace_path, state)
            return reviewed is not None and current_binding is not None and current_binding.digest == reviewed.digest

        attempt_ctx.post_source_review_guard = _candidate_is_the_reviewed_one
        try:
            deps.assert_candidate_unchanged_since_source_review(state, attempt_ctx)
            await deferred_runtime()
            deps.assert_candidate_unchanged_since_source_review(state, attempt_ctx)
            _raise_if_required_verification_unresolved()
            await _close_and_check_requirements()      # it runs the candidate's tests: still under the guard, checked again after it
            deps.assert_candidate_unchanged_since_source_review(state, attempt_ctx)
        except Exception:
            # The review shown with the final failure must describe what finally failed, not the pre-build review the person approved from: drop it so the rejected-
            # candidate diagnostic review (Demo-01 Finding 3) is produced, exactly as for a failure inside run_attempt.
            state.pre_approval_review = None
            raise
        finally:
            attempt_ctx.post_source_review_guard = None
        _save_candidate_gates_checkpoint(candidate_checkpoint_fields, final_files_for_checkpoint)

    # Worktree cleanup deliberately does NOT happen here anymore - see the
    # real live incident this fix closes, 2026-08-22 (ignite_qpid_protocol
    # milestone 2/3): compile+run-verification passing is not the same as
    # "done" - the regression test suite below still has to pass too, and
    # a regression failure sends the loop back for another Developer/
    # Quality-Gates retry that needs a working worktree. Resetting the
    # worktree here (git checkout -f HEAD + git clean -fd) wiped every
    # established-but-never-committed file (pom.xml, and milestone 1's own
    # Protocol.java/ProtocolParser.java/ProtocolTest.java) the instant a
    # regression failure occurred, with no re-sync step before the next
    # attempt - only files a subsequent retry happened to rewrite (e.g.
    # App.java) ever reappeared. A file nobody touched again (pom.xml
    # itself, once a targeted retry answered "NO CHANGE NEEDED" for it)
    # stayed missing, so `run_compile_check()`'s `os.path.exists(pom.xml)`
    # silently went False and fell through to the raw-javac fallback,
    # which then failed with a confusing "file not found: Protocol.java" -
    # a build-lifecycle bug that looked exactly like a code/context defect.
    # Moved to fire only once the regression suite has ALSO passed, right
    # before quality_gates_succeeded is actually set - the worktree now
    # stays alive for as long as any retry can still need it, matching how
    # every other retry in this loop already treats worktree lifetime
    # (kept until final success or final budget exhaustion via
    # retry_strategy.py's own remove_git_worktree call).

    # Stage 7, before the terminal regression (kriya/workflow/stage_apply.py::prepare_lessons): the
    # dependency-skill auto-accrual and the hard-won-knowledge trigger.
    self_corrected_outcome, should_extract_hard_won_knowledge = prepare_lessons(
        ctx, deps.late_stage_deps(),
    )

    final_candidate_contents = run_terminal_regression(ctx, deps, run)

    # A semantically final-success checkpoint is legal only after
    # every required terminal gate has passed.
    candidate_checkpoint_fields[deps.EFFECTIVE_LEDGER_KEY] = resolved_obligation_ledger.to_snapshot()
    _save_stage_checkpoint(
        "developer_success",
        effective_obligation_ledger=resolved_obligation_ledger,
        **candidate_checkpoint_fields,
        plan=plan,
        design=design,
        final_files=final_files_for_checkpoint,
        original_files=state.all_original_contents,
        gate_outcomes=state.gate_outcomes,
        model_hops=state.model_hops,
        retry_count=state.budgets.retry_count,
        targeted_retry_count=state.budgets.targeted_retry_count,
        candidate_gates_passed=True,
        terminal_regression_passed=True,
        overall_attempt_passed=False,
    )

    # Stage 6, terminal apply (kriya/workflow/stage_apply.py::apply_terminal_candidate): the verified candidate
    # as one revision-grounded batch through the shared commit seam, the attempt's success record, and the
    # sandbox released before any optional learning call.
    apply_terminal_candidate(
        ctx, deps.late_stage_deps(), attempt_ctx=attempt_ctx,
        final_candidate_contents=final_candidate_contents,
    )

    # Stage 7 (kriya/workflow/stage_apply.py::extract_lessons): advisory learning after authoritative success.
    await extract_lessons(
        ctx, deps.late_stage_deps(), should_extract_hard_won_knowledge=should_extract_hard_won_knowledge,
        self_corrected_outcome=self_corrected_outcome,
    )

    return None


def run_terminal_regression(ctx: StageContext, deps: StageDeps, run: DevelopmentRun) -> Dict[str, str]:
    """The terminal gate on the still-isolated candidate: the full regression suite (with the brownfield baselines'
    PRE/POST attribution and the targeted replay), the future-owner deferral, then the brownfield ownership, public-API
    and semantic-region checks on the final content, and the success outcome. Raises ``QualityGateFailure`` on a
    blocking result; returns the final candidate contents the terminal apply commits."""
    logger = deps.logger
    state, goal, workspace_path = ctx.state, ctx.goal, ctx.workspace_path
    allowed_write_relpaths, write_scope_mode = ctx.allowed_write_relpaths, ctx.write_scope_mode
    structured_plan, current_subtask_id = ctx.structured_plan, ctx.current_subtask_id
    completed_subtask_ids, obligation_ledger = ctx.completed_subtask_ids, ctx.obligation_ledger
    grounding_goal, authorized_semantic_regions = ctx.grounding_goal, ctx.authorized_semantic_regions
    attempt_ctx, worktree_path, baseline_suite_run = run.attempt_ctx, run.worktree_path, run.baseline_suite_run
    # If targeted gates passed, run the full regression suite against
    # the still-isolated candidate. The real application workspace is
    # deliberately untouched until this terminal gate passes.
    logger.info("Quality Gates: Running full test suite regression check...")
    validator = PolymorphicValidator(
        worktree_path, original_workspace_path=workspace_path,
        autonomy_cfg=deps.config.autonomy,
        toolchain_declaration_mutable=toolchain_declaration_mutable(
            write_scope_mode, allowed_write_relpaths, structured_plan,
        ),
    )
    validator.tree_binding = state.verification_tree_binding

    if not state.toolchain_checked:
        state.toolchain_checked = True
        state.toolchain_warning = _check_java_toolchain_mismatch(validator.stack)
        if state.toolchain_warning:
            logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
        if validator.stack == "java":
            state.java_home_override = _resolve_java_home_override(goal)
            if state.java_home_override:
                logger.warning(
                    "JVM toolchain enforcement: forcing Maven subprocess calls to "
                    f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                    "version doesn't match what 'mvn' resolves to by default here."
                )
    validator.java_home_override = state.java_home_override

    full_test_res = validator.run_tests()
    deps._stop_on_environment_gate_result(state, full_test_res, "regression_test")
    state.terminal_full_suite_result = full_test_res

    # VAL-001 brownfield validation baselining - ONLY changes
    # the blocking decision when a full-regression baseline was
    # actually captured (autonomy.brownfield_full_regression_
    # baseline_policy == "required"); otherwise
    # `_regression_should_block` is byte-identical to the
    # pre-existing `not full_test_res["success"]` check, so a
    # run that never opted in behaves exactly as before this
    # package existed. A captured baseline lets a genuinely
    # PRE-EXISTING failure (present before Kriya ever touched
    # this brownfield repository) pass through without
    # blocking candidate acceptance - invariant 4 - while
    # NEW_FAILURE/CHANGED_FAILURE/an unexplained disappearance
    # of previously-executed validation still blocks exactly
    # as strictly as before (invariants 5/"fail conservatively").
    _regression_should_block = not full_test_res["success"]
    _baseline_delta_result = None
    # CANDIDATE-GATE-BASELINE-POLICY-001: the one attribution
    # owner (kriya/workflow/suite_attribution.py) - the same
    # decision the candidate gates now make.
    _full_baseline = deps.captured_baseline(state.validation_baseline_full_regression)
    if _full_baseline is not None:
        _full_attribution = deps.attribute_suite_result(
            scope=deps.SCOPE_FULL_REGRESSION, baseline=_full_baseline, result=full_test_res,
            post_environment=deps.post_environment_identity(
                baseline=_full_baseline, workspace_path=workspace_path, worktree_path=worktree_path,
                autonomy_cfg=deps.config.autonomy, goal=goal, written_files=state.all_files_written),
            stability_cache=state.pytest_stability_cache, replay=baseline_suite_run,
            workspace_path=workspace_path,
        )
        _baseline_delta_result = _full_attribution.delta
        _regression_should_block = _baseline_delta_result.blocking
        state.record_event(RunEvent(
            kind="validation_baseline.full_regression_delta",
            attempt=state.attempt_number,
            source="workflow.run_generation_workflow",
            authority=EventAuthority.ADVISORY,
            message=f"Full-regression {_full_attribution.message()}",
            details=_full_attribution.event_details(),
        ))

    # VAL-001 G1-R3 (2026-09-18): the targeted baseline's own
    # POST comparison - separate from, and additive to, the
    # full-regression one immediately above. A no-op (zero extra
    # subprocess calls, _targeted_regression_should_block stays
    # False) whenever brownfield_baseline_target_test was never
    # set, matching this package's own established "opt-in,
    # complete no-op otherwise" precedent exactly.
    #
    # POST_REPLAY invariant: the target(s) re-run here are read
    # from `state.validation_baseline_targeted.invocation.
    # target_test` - the frozen, structural selection PRE
    # capture itself used - NEVER re-read from
    # `self.kernel.config.autonomy.brownfield_baseline_target_
    # test` fresh. The two are the same value for an ordinary,
    # non-resumed run, but a resumed run could in principle carry
    # a checkpointed PRE baseline captured under a DIFFERENT
    # config than the one active now - replaying the frozen
    # invocation, not re-deriving from current config/workspace
    # state, is what makes "PRE and POST validation command/
    # selection identities are frozen and equivalent" true by
    # construction rather than by coincidence.
    _targeted_test_res = None
    _targeted_regression_should_block = False
    _targeted_baseline_delta_result = None
    if (
        state.validation_baseline_targeted is not None
        and state.validation_baseline_targeted.status == "captured"
    ):
        _frozen_targets = state.validation_baseline_targeted.invocation.target_test
        _targeted_test_res = validator.run_tests(target_test=_frozen_targets)
        deps._stop_on_environment_gate_result(state, _targeted_test_res, "targeted_test")
        _post_targeted_outcome = build_validation_outcome(_targeted_test_res)
        _targeted_baseline_delta_result, _targeted_stability = classify_with_baseline_stability(
            baseline=state.validation_baseline_targeted, post=_post_targeted_outcome,
            post_environment=None, cache=state.pytest_stability_cache,
            replay=baseline_suite_run,
            current_revision=lambda: deps.compute_workspace_content_hash(workspace_path),
            working_directory=os.path.realpath(workspace_path),
        )
        record_regression_decision("targeted", state.validation_baseline_targeted,
                                   _post_targeted_outcome, _targeted_baseline_delta_result,
                                   _targeted_stability)
        _targeted_regression_should_block = _targeted_baseline_delta_result.blocking
        state.record_event(RunEvent(
            kind="validation_baseline.targeted_delta",
            attempt=state.attempt_number,
            source="workflow.run_generation_workflow",
            authority=EventAuthority.ADVISORY,
            message=(
                f"Targeted PRE/POST delta ({_frozen_targets}): "
                f"level1={_targeted_baseline_delta_result.level1.classification.value} "
                f"blocking={_targeted_regression_should_block}"
            ),
            details={
                "target_test": list(_frozen_targets) if _frozen_targets else None,
                "level1_classification": _targeted_baseline_delta_result.level1.classification.value,
                "level2": {k: v.value for k, v in _targeted_baseline_delta_result.level2.items()},
                "aggregate_drop_detected": _targeted_baseline_delta_result.aggregate_drop_detected,
                "blocking": _targeted_regression_should_block,
                "blocking_reasons": list(_targeted_baseline_delta_result.blocking_reasons),
                **pytest_decision_summary(_targeted_baseline_delta_result, _targeted_stability),
            },
        ))
        if not _targeted_regression_should_block and not _targeted_test_res["success"]:
            logger.info(
                "Targeted baseline suite failed, but every failure is classified "
                "PRE_EXISTING_FAILURE relative to the captured PRE-mutation baseline - "
                "not attributed to this candidate, not blocking."
            )

    _regression_should_block = _regression_should_block or _targeted_regression_should_block

    # Combined failure evidence: the full-regression output is
    # always included (unchanged from before this package
    # existed); the targeted suite's own output is appended,
    # clearly labeled, whenever it was actually run AND is
    # itself a genuine cause (failed outright, or its own delta
    # blocked) - never omitted when it's the reason blocking
    # fired, never included as noise when it isn't.
    _regression_failure_output = full_test_res.get("output", "")
    if _targeted_test_res is not None and (
        not _targeted_test_res["success"] or _targeted_regression_should_block
    ):
        _regression_failure_output = (
            f"{_regression_failure_output}\n\n"
            f"=== TARGETED BASELINE SUITE ({state.validation_baseline_targeted.invocation.target_test}) ===\n"
            f"{_targeted_test_res.get('output', '')}"
        )

    # VAL-001 G1-DEVINV2 (2026-09-20): a captured full-regression
    # baseline means classify_baseline_delta() already computed,
    # per test, whether each failure is PRE_EXISTING_FAILURE
    # (never candidate-caused) or NEW_FAILURE/CHANGED_FAILURE
    # (blocking, and attributable). Before this fix, the
    # Developer-facing failure text above ignored that structure
    # entirely and used the RAW full-suite output - in a real
    # brownfield repo with substantial pre-existing test debt
    # (live-confirmed: 140 pre-existing failures alongside 0-1
    # genuinely new ones), that floods the repair prompt with
    # noise the classification above already had the information
    # to exclude, and was the proximate cause of a real G1 run
    # (qwen3.8:27b, 2026-09-19/20) discarding an independently-
    # verified CORRECT Attempt-1 candidate after 6 further
    # attempts of cascading malformed repair responses across
    # both the primary and fallback model. This ONLY changes
    # what evidence is shown for an ALREADY-blocking regression -
    # `_regression_should_block` above (the actual accept/reject
    # decision) is completely untouched, preserving "full
    # regression remains the final acceptance gate" exactly.
    # Byte-identical to before this fix whenever a full-
    # regression baseline was never captured at all (the
    # overwhelming majority of runs, which never opted into
    # `brownfield_full_regression_baseline_policy: "required"`).
    _confirmed_regression_ids: List[str] = []
    # True only when NOTHING attributable was found anywhere -
    # neither the full baseline (after ambiguous-entry replay
    # resolution) nor the targeted baseline (a small, already-
    # trustworthy 76-test signal with no flooding concern, whose
    # own independent block is never second-guessed here). When
    # the targeted baseline itself blocks, this stays False and
    # the pre-existing raw-output-plus-targeted-section text
    # built above is used completely unchanged - this fix only
    # ever narrows what's shown for the FULL-suite signal, never
    # touches the targeted one.
    _full_regression_unattributed = False
    # P2: files the compiler's errors name, when a test-compilation
    # failure is attributed to the candidate (compile_regression.py).
    _regression_known_files: List[str] = list(state.all_files_written)
    if _regression_should_block and _baseline_delta_result is not None:
        from kriya.workflow.regression_attribution import confirm_ambiguous_regressions
        _resolved_level2, _replay_evidence = confirm_ambiguous_regressions(
            _baseline_delta_result.level2,
            pristine_workspace_path=workspace_path,
            candidate_workspace_path=worktree_path,
            autonomy_cfg=deps.config.autonomy,
        )
        if _replay_evidence:
            state.record_event(RunEvent(
                kind="validation_baseline.ambiguous_regression_replay",
                attempt=state.attempt_number,
                source="workflow.run_generation_workflow",
                authority=EventAuthority.ADVISORY,
                message=(
                    f"Isolated-pristine replay resolved {len(_replay_evidence)} "
                    "ambiguous (NOT_COMPARABLE) full-regression test(s)."
                ),
                details={"replay_evidence": _replay_evidence},
            ))
        _confirmed_regression_ids = sorted(
            test_id for test_id, cls in _resolved_level2.items()
            if cls in (DeltaClassification.NEW_FAILURE, DeltaClassification.CHANGED_FAILURE)
        )
        if _confirmed_regression_ids:
            _regression_failure_output = render_blocking_regression_evidence(
                baseline=state.validation_baseline_full_regression,
                confirmed_test_ids=_confirmed_regression_ids,
                raw_output=full_test_res.get("output", ""),
                failure_details=deps.test_execution.failure_details(full_test_res),
            )
            if _targeted_test_res is not None and (
                not _targeted_test_res["success"] or _targeted_regression_should_block
            ):
                _regression_failure_output = (
                    f"{_regression_failure_output}\n\n"
                    f"=== TARGETED BASELINE SUITE ({state.validation_baseline_targeted.invocation.target_test}) ===\n"
                    f"{_targeted_test_res.get('output', '')}"
                )
        elif not _targeted_regression_should_block:
            # P2: no per-test evidence at all (a test-compilation
            # failure runs no test) is not "every failure is
            # pre-existing": under a green, comparable PRE the
            # compiler's own diagnostics attribute it.
            _compile_regression = attribute_compile_regression(
                _baseline_delta_result, full_test_res.get("output", ""), worktree_path,
            )
            if _compile_regression is None:
                _full_regression_unattributed = True
            else:
                _regression_known_files = sorted(
                    set(state.all_files_written) | set(_compile_regression.files))
                state.record_event(RunEvent(
                    kind="validation_baseline.compile_regression_attributed",
                    attempt=state.attempt_number,
                    source="workflow.run_generation_workflow",
                    authority=EventAuthority.AUTHORITATIVE,
                    message=("Full-regression compilation failure attributed to the candidate "
                             "from compiler diagnostics: " + ", ".join(_compile_regression.files)),
                    details=_compile_regression.as_dict(),
                ))

    if _regression_should_block:
        # PRV-11 (2026-08-30): before treating this as an ordinary
        # regression failure for the CURRENTLY executing subtask,
        # check whether the approved plan's own provides/requires
        # graph already proves this exact evidence is covered by
        # unfinished, approved work - see ObligationKind.FUTURE_
        # OWNER_VERIFICATION's own docstring (obligations.py) and
        # resolve_future_owner_verification_deferral's own
        # docstring (attribution.py) for the full live incident
        # and fail-closed resolution rules. None whenever plan/
        # subtask/ledger context is absent (every pre-MA6 caller)
        # or any link in the chain is ambiguous - falls through
        # to the unchanged ordinary path below.
        deferral = (
            deps.resolve_future_owner_verification_deferral(
                structured_plan, current_subtask_id,
                full_test_res.get("output", ""), completed_subtask_ids or frozenset(),
            )
            if structured_plan is not None and current_subtask_id and obligation_ledger is not None
            else None
        )
        if deferral is None:
            if obligation_ledger is not None and current_subtask_id:
                # This subtask was itself the future owner some
                # earlier subtask deferred to, and its own real
                # regression check still fails - settle VIOLATED
                # for observability only. Control flow is
                # unaffected: the raise below already routes
                # this through the subtask's own ordinary retry/
                # attribution path, which IS "recover against
                # the now-responsible owner."
                deps._settle_future_owner_verification_obligations(
                    obligation_ledger, current_subtask_id, satisfied=False,
                )
            if _full_regression_unattributed:
                # VAL-001 G1-DEVINV2 (2026-09-20): the full-
                # regression gate still BLOCKS (safety unchanged
                # - `_regression_should_block` above is never
                # weakened by this branch), but nothing could be
                # confirmed candidate-attributable even after
                # isolated-pristine replay of every ambiguous
                # entry - only a LEVEL1-only aggregate delta with
                # zero attributable per-test evidence. Feeding
                # this into the ordinary Developer repair retry
                # loop (either the raw full-suite dump, or an
                # empty/uninformative filtered context) is
                # exactly what produced a cascading malformed-
                # response failure across both the primary and
                # fallback model in a real live G1 run - not
                # developer-fixable, so this reuses the SAME
                # state.environment_failure/STOP_ENVIRONMENT
                # mechanism containment_setup_failed/internal_
                # framework_error/time_budget_exhausted already
                # use for "no amount of Developer regeneration
                # can fix this" (kriya/workflow/retry_strategy.py),
                # rather than a bare "regression_test" failure
                # that would re-enter the ordinary repair loop.
                # REGRESSION-UNATTRIBUTED-PROSE-001: the stop
                # reason states what the delta established
                # (incomplete evidence, an aggregate change, or
                # unattributable per-test entries) from its own
                # blocking reasons - never an aggregate change
                # the fingerprints do not show.
                _attribution, _unattributed_message = regression_unattributed_diagnosis(
                    _baseline_delta_result)
                failure = Failure(
                    type="regression_unattributed",
                    message=_unattributed_message,
                    raw_output=full_test_res.get("output", ""),
                    source="orchestrator", attempt=state.attempt_number,
                    diagnostics={
                        # the typed code itself is set by its
                        # producer (retry_strategy, LR-R1-M1 D7)
                        # from this message's prefix
                        "reason_code": "REGRESSION_UNATTRIBUTED",
                        "attribution": _attribution,
                        "blocking_reasons": list(_baseline_delta_result.blocking_reasons),
                        "pytest_evidence_status": _baseline_delta_result.pytest_evidence_status,
                    },
                )
            else:
                failure = _build_quality_gate_failure(
                    "regression_test", f"REGRESSION TEST SUITE FAILURE:\n{_regression_failure_output}",
                    _regression_failure_output, worktree_path,
                    _regression_known_files, state.attempt_number,
                )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        deps._record_future_owner_verification_deferred(
            obligation_ledger, deferral,
            observed_by_subtask_id=current_subtask_id,
            raw_output=full_test_res.get("output", ""),
        )
        logger.info(
            "FUTURE_OWNER_VERIFICATION: regression evidence in %s (owned by "
            "verification subtask %s) requires capability %s from not-yet-executed "
            "subtask %s - deferring rather than retrying %s; continuing plan "
            "execution.",
            deferral.evidence_path, deferral.verification_subtask_id,
            deferral.required_capability, deferral.future_owner_id, current_subtask_id,
        )
        state.record_gate_outcome({
            "attempt": state.attempt_number,
            "type": "regression_test",
            "success": True,
            "output": full_test_res.get("output", ""),
            "deferred_to_future_owner": deferral.future_owner_id,
            **execution_evidence(full_test_res),
        })
        # Falls through to the same post-regression checks and
        # success path below, exactly as a genuinely passing
        # full_test_res would - this IS "allow the current
        # subtask to complete."

    final_candidate_contents = {}
    for filepath in sorted(state.all_files_written):
        with open(
            os.path.join(worktree_path, filepath),
            "r", encoding="utf-8", errors="replace",
        ) as fh:
            final_candidate_contents[filepath] = fh.read()
    ownership_violations = find_brownfield_test_redirections(
        workspace_path,
        state.all_original_contents,
        final_candidate_contents,
    )
    if ownership_violations:
        evidence = "; ".join(
            f"owner={item['existing_owner']}, candidate={item['new_candidate']}, "
            f"test={item['redirected_test']}"
            for item in ownership_violations
        )
        message = (
            "BROWNFIELD OWNERSHIP REJECTED: existing behavioral ownership "
            "cannot be transferred to a newly created parallel implementation "
            "by redirecting established tests. Modify the existing owner and "
            "retain its API; update tests only to extend expectations or coverage."
        )
        # MA8 (spec §35-38, kriya/workflow/architectural_choice.py):
        # a single occurrence is ordinary quality-gate failure -
        # likely_files already redirects the next attempt at the
        # grounded owner below. Only once the SAME candidate file
        # recurs despite that redirect, AND the goal never
        # explicitly asked for a new artifact by that name, is the
        # underlying architectural choice (not just this attempt)
        # confirmed wrong.
        new_architectural_changes, architecture_diagnostics = deps.classify_ownership_violations(
            ownership_violations, goal, state.attempt_number, state.architectural_changes,
        )
        state.architectural_changes.extend(new_architectural_changes)
        diagnostics = dict(architecture_diagnostics or {})
        if architecture_diagnostics:
            message = architecture_choice_invalidated_message(architecture_diagnostics)
        failure = Failure(
            type="brownfield_ownership_redirect",
            message=message,
            raw_output=evidence,
            source="ownership_gate",
            authority="deterministic",
            file_locations=[
                FileLocation(filepath=item["redirected_test"])
                for item in ownership_violations
            ],
            likely_files=sorted({
                item["existing_owner"] for item in ownership_violations
            }),
            attempt=state.attempt_number,
        )
        if diagnostics:
            failure.diagnostics = {**(failure.diagnostics or {}), **diagnostics}
        # PRD-022: the redirected tests are evidence and the
        # parallel file is abandoned; the next attempt restores
        # both deterministically (attempt.py) so the retry only
        # has to change the grounded owner it is scoped to.
        for key, values in (
            ("redirected_tests", [item["redirected_test"] for item in ownership_violations]),
            ("abandoned_candidates", [item["new_candidate"] for item in ownership_violations]),
            ("owners", [item["existing_owner"] for item in ownership_violations]),
        ):
            merged = state.ownership_redirect_recovery.setdefault(key, [])
            merged.extend(v for v in values if v not in merged)
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    # CORR-016 (P9/PRV-08, 2026-09-08, DIRECT-only) - same
    # authorization the per-attempt pre-write gate in
    # run_attempt() already applies (kriya/workflow/attempt.py),
    # computed identically here so a candidate that passed that
    # gate is never re-rejected at this terminal regression
    # check. [] for every plain Legacy run (structured_plan is
    # None there).
    terminal_direct_authorizations = deps._terminal_contract_authorizations(
        grounding_goal, structured_plan, current_subtask_id, state,
    )
    api_violations = find_brownfield_public_api_changes(
        workspace_path,
        state.all_original_contents,
        final_candidate_contents,
        goal,
        terminal_direct_authorizations,
    )
    if api_violations:
        evidence = "; ".join(
            f"owner={item['owner']}, removed={item['removed_signature']}, "
            f"references={item['evidence_files']}"
            for item in api_violations
        )
        failure = Failure(
            type="brownfield_public_api_changed",
            message=(
                "BROWNFIELD PUBLIC API REJECTED: an established public signature "
                "referenced by existing tests or call sites was removed or renamed. "
                "Preserve the public API and repair private/internal implementation "
                "unless the goal explicitly requests an API migration."
            ),
            raw_output=evidence,
            source="ownership_gate",
            authority="deterministic",
            file_locations=[
                FileLocation(filepath=item["owner"])
                for item in api_violations
            ],
            likely_files=sorted({item["owner"] for item in api_violations}),
            diagnostics={
                "api_contract_recovery": {"violations": api_violations},
            },
            attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    # CORR-018-P1 (A3-bound slice, 2026-09-09) - same terminal
    # re-check discipline as the brownfield-API gate immediately
    # above (never re-rejects a candidate that already passed the
    # per-attempt gate in attempt.py, computed identically here),
    # applied to a SEPARATE, deterministic question - see
    # semantic_region_authority.py's own module docstring and
    # attempt.py's early gate for the full rationale. No-op unless
    # a caller actually populated authorized_semantic_regions.
    terminal_semantic_violations = find_unauthorized_semantic_changes(
        state.all_original_contents, final_candidate_contents, authorized_semantic_regions or [],
        strict_existing_java_files=deps.config.autonomy.semantic_region_enforcement_required,
    )
    if terminal_semantic_violations:
        evidence = "; ".join(
            f"file={v.relpath}, member={v.member_key}, reason={v.reason_code}: {v.detail}"
            for v in terminal_semantic_violations
        )
        failure = Failure(
            type="semantic_region_unauthorized",
            message=(
                "SEMANTIC REGION REJECTED: the candidate changes source outside the "
                "explicitly authorized region(s) for this run."
            ),
            raw_output=evidence,
            source="semantic_authority_gate", authority="deterministic",
            file_locations=[FileLocation(filepath=v.relpath) for v in terminal_semantic_violations],
            likely_files=sorted({v.relpath for v in terminal_semantic_violations}),
            diagnostics={
                "semantic_region_authority": {
                    "violations": [
                        {"relpath": v.relpath, "member_key": v.member_key,
                         "reason_code": v.reason_code, "detail": v.detail}
                        for v in terminal_semantic_violations
                    ],
                },
            },
            attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    state.record_gate_outcome({
        "attempt": state.attempt_number,
        "type": "regression_test",
        "success": True,
        "output": full_test_res.get("output", ""),
        **execution_evidence(full_test_res),
    })
    if state.no_change_proposal:
        deps._settle_no_change_proposal(state, structured_plan, current_subtask_id, worktree_path,
                                   reopened_owner=attempt_ctx.reopened_owner)
    elif (state.identical_rewrites and not state.all_files_written
          and not attempt_ctx.no_change_verifier_downstream):
        deps._stop_unverified_no_change(state)
    state.terminal_regression_succeeded = True
    if obligation_ledger is not None and current_subtask_id:
        # This subtask's own full regression genuinely passed
        # (deferred or not) - settle SATISFIED any still-PENDING
        # FUTURE_OWNER_VERIFICATION obligation this subtask
        # itself owns (i.e. an earlier subtask deferred to it,
        # and it just proved the deferred requirement for real).
        # A no-op when none exist.
        deps._settle_future_owner_verification_obligations(
            obligation_ledger, current_subtask_id, satisfied=True,
        )
    if state.api_contract_recovery:
        state.api_contract_recovery.terminal_succeeded()
        logger.info(
            "API_CONTRACT_RECOVERY complete: deterministic contract checks, "
            "candidate gates, and terminal regression all passed; transitions=%s",
            state.api_contract_recovery.transition_history,
        )
        state.api_contract_recovery = None
    state.record_event(RunEvent(
        kind="terminal_regression.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "terminal": True},
    ))
    log_quality_gate_banner(
        "PASSED", state.attempt_number,
        scope=attempt_ctx.execution_scope,
    )
    return final_candidate_contents


__all__ = ["DevelopmentRun", "accept_candidate", "prepare_development", "run_attempt_loop", "run_development",
           "run_terminal_regression"]
