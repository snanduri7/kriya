"""The candidate-gate orchestration of one attempt (full modularization W4, E8).

Moved verbatim from ``kriya/workflow/attempt.py``:

- the verification-tree binding taken before the first gate (``_bind_verification_tree``, the staged-bytes check
  ``_require_worktree_matches_candidate`` and the LR-R1-M1 ``candidate.change`` record ``_record_candidate_change``,
  FILE-INTEGRITY-CONTRACT-001);
- the candidate validator and the one-time JVM toolchain preflight (``_new_candidate_validator``,
  ``_resolve_candidate_toolchain``: the java/mvn mismatch warning and the goal-stated JDK's JAVA_HOME override, applied
  to every attempt's new validator), the candidate's own pom.xml corrections (``_apply_candidate_pom_corrections``,
  ``apply_pre_review_candidate_corrections``);
- the POM validation gate (``_run_pom_validation_gate``), the executing candidate gates through
  ``candidate_gates_succeeded`` (``_run_executing_candidate_gates``: compile, contract feedback, tests, the test
  acceptance and test-delta checks, the runtime-verification judgment and its deterministic grounding, migration
  completion, goal spec compliance) and their held half after the source review (``_run_deferred_executing_gates``,
  Gap B);
- the verification-only attempt (``_run_verification_only_attempt``), whose declared verifiers are sequenced by
  ``kriya/workflow/verification_coordinator.py::VerificationCoordinator`` (unchanged);
- the goal-spec gate's advisory statuses (``SPEC_MODEL_REPORTED_MISSING``, ``SPEC_MODEL_INDETERMINATE``,
  ``SPEC_COMPLIANCE_MODEL_ADVISORY``).

Gate ORDER and every gate decision are unchanged. Not here: the gate sequence's caller (``run_attempt``: the binding,
the hold decision, the non-executing completeness/static checks), the runtime-verification execution and the
managed-service verification (``_approve_and_execute_runtime_verification``, ``_execute_managed_service_verification``,
``_execute_runtime_verification_directly``: E9), the pre-apply static-analysis gate and the commit binding
(``development_loop`` / ``stage_apply``), and every helper the gates call that other attempt code also uses.

``kriya/workflow/attempt.py`` keeps every one of these names (a forwarder that builds ``GateDeps`` at call time, or a
re-export) so a test patch on ``kriya.workflow.attempt`` keeps intercepting the moved code. What arrives in
``GateDeps``, resolved through ``kriya.workflow.attempt`` when the forwarder runs: every name the bodies looked up that
attempt.py defines (its helpers, the moved siblings' forwarders), every name tests patch on ``kriya.workflow.attempt``
(``tests/architecture/test_patch_points.py::PATCH_POINTS``: ``extract_target_test``, ``find_runnable_test_files``,
``_check_java_toolchain_mismatch``, ``_resolve_java_home_override``, ``_judge_candidate_suite``, the runtime and
managed-service seams), attempt.py's ``logger`` and ``time``, and the names of the kernel's all-import cycle
(``suite_attribution``'s scopes, ``context_budget.request_capacity``; ``self_correction`` and ``retry_strategy``
through loaders called where the bodies imported them). Names of modules outside that cycle (none of them reaches
this module: ``kriya.tools.validate`` sits in the separate EXECUTION cycle with ``capabilities``) that no test patches
on ``kriya.workflow.attempt`` are imported here directly. ``PolymorphicValidator`` stays a function-local
import of ``kriya.tools.validate`` (tests patch the class there). This module never imports
``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or ``kriya.cli``, nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState`` and ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext``; both are spelled ``Any`` (``kriya/workflow/stage_context.py`` says why the
state module is not imported; AttemptContext lives in attempt.py, OI-46).
"""
import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.policy.filesystem import AuthorizedFileWriter, WriteScopeMode
from kriya.tools.validate import MAVEN_VALIDATE_TIMED_OUT, execution_evidence
from kriya.workflow.acceptance import output_confirms_nonzero_test_execution, subtask_owns_test_obligation
from kriya.workflow.banners import log_gate_banner
from kriya.workflow.edit_safety import StagedFileWrite, read_file_revision
from kriya.workflow.failure import Failure, FileLocation, QualityGateFailure
from kriya.workflow.failure_grounding import (
    _build_quality_gate_failure,
    _build_test_quality_gate_failure,
    _capture_failed_content,
    build_cross_package_mismatch_message,
    find_cross_package_symbol_mismatch,
    find_locator_files_outside_known_scope,
    resolve_repository_locator_files,
)
from kriya.workflow.file_integrity import (
    WORKTREE_CONTENT_MISMATCH,
    FileIntegrityError,
    VerificationTreeBinding,
    file_raw_digest,
    load_snapshot,
    raw_digest,
)
from kriya.workflow.file_resolution import (
    correct_exec_main_class_property,
    downgrade_ungrounded_goal_explicit_commands,
    ensure_maven_covers_nonconventional_java_files,
    extract_jvm_module_flags,
    ground_java_entrypoint_in_no_build_file_projects,
    ground_python_runtime_target,
)
from kriya.workflow.migration import MigrationResolutionStatus, MigrationValidationScope, find_migration_incomplete
from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationRecord, ObligationStatus
from kriya.workflow.repair_contract import RepairContractStatus
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.test_delta import TEST_EXECUTION_EVIDENCE_INDETERMINATE, judge_test_delta
from kriya.workflow.toolchain import _pin_exec_plugin_executable_to_resolved_jdk, _strip_jdk_incompatible_jvm_flags
from kriya.workflow.verification_authority import deterministic_sequence_kind
from kriya.workflow.verification_coordinator import VerificationCoordinator, VerificationRequest
from kriya.workflow.worktree import repository_content_paths

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext

# GR-R1B: the attempt-level Goal Spec Compliance gate's model-only outcomes -
# advisory diagnostic evidence, never an attempt failure, retry or fallback.
SPEC_MODEL_REPORTED_MISSING = "model_reported_missing"
SPEC_MODEL_INDETERMINATE = "model_indeterminate"
SPEC_COMPLIANCE_MODEL_ADVISORY = "SPEC_COMPLIANCE_MODEL_ADVISORY"


@dataclass(frozen=True)
class GateDeps:
    """What the moved gate code may use, resolved through ``kriya.workflow.attempt`` by ``attempt._gate_deps()`` when
    a forwarder runs. Every field is required: a builder that omits one fails at construction, never at the first
    use."""

    # attempt.py's logger and clock (a patch of kriya.workflow.attempt.time reaches the compile timing)
    logger: Any
    time: Any
    # the kernel's all-import cycle: suite_attribution's scopes, context_budget's request capacity, and the loaders of
    # the bodies' function-local self_correction / retry_strategy imports
    SCOPE_CANDIDATE_GATE: str
    SCOPE_VERIFICATION_UNIT: str
    request_capacity: Callable[..., Any]
    load_run_self_correction_loop: Callable[[], Any]
    load_run_repair_loop: Callable[[], Any]
    load_verification_inputs_digest: Callable[[], Any]
    # names tests patch on kriya.workflow.attempt (PATCH_POINTS)
    extract_target_test: Callable[..., Any]
    find_runnable_test_files: Callable[..., Any]
    _check_java_toolchain_mismatch: Callable[..., Any]
    _resolve_java_home_override: Callable[..., Any]
    _judge_candidate_suite: Callable[..., Any]
    _approve_and_execute_runtime_verification: Callable[..., Any]
    _execute_managed_service_verification: Callable[..., Any]
    # attempt.py's own names (E9: the runtime execution; some of them are E14's moved bodies, still bound as attempt.py's
    # forwarders)
    _PROCESS_BOUNDARY_RECURRENCE_ESCALATION: str
    _as_developer: Callable[..., Any]
    _build_java_main_class_map: Callable[..., Any]
    _build_java_package_map: Callable[..., Any]
    _build_python_runtime_grounding: Callable[..., Any]
    _build_workspace_type_index: Callable[..., Any]
    _close_requirements_by_migration_gate: Callable[..., Any]
    _ctx_toolchain_declaration_mutable: Callable[..., Any]
    _declares_test_verification: Callable[..., Any]
    _downgrade_suppressed_requirement_claims: Callable[..., Any]
    _execute_runtime_verification_directly: Callable[..., Any]
    _goal_spec_evidence_fingerprint: Callable[..., Any]
    _goal_spec_requirement_obligation_id: Callable[..., Any]
    _migration_obligations_all_satisfied: Callable[..., Any]
    _raise_if_required_runtime_verification_declined: Callable[..., Any]
    _raise_unexecuted_test_delta: Callable[..., Any]
    _raise_ungrounded_child_process_test_candidate: Callable[..., Any]
    _raise_unsafe_process_boundary_test_candidate: Callable[..., Any]
    _record_original_requirement_verdicts: Callable[..., Any]
    _record_process_boundary_obligation: Callable[..., Any]
    _record_self_correction_scope_conflict: Callable[..., Any]
    _required_runtime_verification_missing_message: Callable[..., Any]
    _resolve_execution_mode: Callable[..., Any]
    _run_contract_feedback_gate: Callable[..., Any]
    _run_verification_basis_hash: Callable[..., Any]
    _runtime_verification_is_advisory_only: Callable[..., Any]
    _settled_goal_spec_requirement: Callable[..., Any]
    _spec_compliance_authoritative_context: Callable[..., Any]
    _spec_requirements_contradicting_authority: Callable[..., Any]
    _spec_requirements_naming_planner_only_identifiers: Callable[..., Any]
    _stage_scoped_spec_compliance_goal: Callable[..., Any]
    _stop_on_environment_gate_result: Callable[..., Any]
    assert_candidate_unchanged_since_source_review: Callable[..., Any]
    # the moved siblings, as attempt.py's forwarders (a patch on any of them is honoured)
    _apply_candidate_pom_corrections: Callable[..., Any]
    _record_candidate_change: Callable[..., Any]
    _require_worktree_matches_candidate: Callable[..., Any]
    _run_pom_validation_gate: Callable[..., Any]
    _new_candidate_validator: Callable[..., Any]
    _resolve_candidate_toolchain: Callable[..., Any]
    _run_executing_candidate_gates: Callable[..., Any]
    # E14: what the bodies moved here in E14 read - the suite_attribution scope and the test_execution module (names
    # attempt.py imports: an all-import-cycle module and an allowlisted EXECUTION edge), the loaders of their
    # function-local kriya.analyzer.graph / kriya.core.role_metrics imports, and attempt.py's helpers and the moved
    # siblings' forwarders
    SCOPE_TEST_DELTA_COVERING_RUN: str
    test_execution: Any
    load_dependency_graph: Callable[[], Any]
    load_model_role: Callable[[], Any]
    _attribute_candidate_suite: Callable[..., Any]
    _changed_test_sources: Callable[..., Any]
    _dependency_graph_db_path: Callable[..., Any]
    _extract_class_names_best_effort: Callable[..., Any]
    _process_boundary_obligation_id: Callable[..., Any]
    _runtime_contract_requirements: Callable[..., Any]
    _sync_active_repair_contract: Callable[..., Any]
    find_in_process_terminating_test_invocations: Callable[..., Any]
    find_ungrounded_java_child_process_tests: Callable[..., Any]


def _apply_candidate_pom_corrections(
    deps: GateDeps, state: RunState, ctx: AttemptCtx, compile_known_files: List[str],
) -> None:
    """Deterministic pom.xml corrections (Maven source-root coverage and
    exec.mainClass), as ordinary candidate mutations (FILE-INTEGRITY-
    CONTRACT-001): only when this candidate itself wrote pom.xml, through the
    authorized staged writer against the pom's raw revision, recorded as a
    run event with both digests - so the change is in the candidate's diff,
    its commit batch and its verification. A pom the candidate did not write
    (the repository's own) is never rewritten behind the gates; the compile
    gate then reports the real layout problem instead."""
    pom_path = os.path.join(ctx.worktree_path, "pom.xml")
    if not os.path.exists(pom_path):
        return
    if "pom.xml" not in state.all_files_written:
        return
    snapshot = load_snapshot(pom_path)
    try:
        pom_content = snapshot.text
    except FileIntegrityError:
        return
    corrected = pom_content
    skills_relpath = os.path.relpath(ctx.kernel.config.paths.skills, ctx.workspace_path)
    corrections = []
    widened = ensure_maven_covers_nonconventional_java_files(corrected, compile_known_files, skills_relpath)
    if widened is not None:
        corrected = widened
        corrections.append("maven_source_root_coverage")
    compile_java_files = [f for f in compile_known_files if f.endswith(".java")]
    main_class = correct_exec_main_class_property(corrected, deps._build_java_main_class_map(compile_java_files, ctx))
    if main_class is not None:
        corrected = main_class
        corrections.append("exec_main_class")
    notes = []
    for name, transform in (("jdk_incompatible_jvm_flags", _strip_jdk_incompatible_jvm_flags),
                            ("exec_executable_pinned_to_resolved_jdk", _pin_exec_plugin_executable_to_resolved_jdk)):
        transformed, note = transform(corrected, state.java_home_override)
        if transformed is not None:
            corrected = transformed
            corrections.append(name)
            notes.append(note)
    if notes:
        joined = " ".join(notes)
        deps.logger.warning(f"JVM preflight: {joined}")
        state.toolchain_warning = f"{state.toolchain_warning} {joined}" if state.toolchain_warning else joined
    if not corrections:
        return
    data = snapshot.encode(corrected)
    AuthorizedFileWriter(
        ctx.worktree_path,
        protected_relpaths=(ctx.protected_relpath,) if ctx.protected_relpath else (),
        allowed_relpaths=ctx.allowed_write_relpaths,
        write_scope_mode=ctx.write_scope_mode,
    ).commit_batch([StagedFileWrite(
        target_path=pom_path, content=corrected, base_path=pom_path,
        expected_base_revision=snapshot.raw_sha256, content_bytes=data, mode=snapshot.file_mode,
    )])
    state.candidate_digests["pom.xml"] = raw_digest(data)
    if state.verification_tree_binding is not None:
        state.verification_tree_binding.authorize("pom.xml")
    state.record_event(RunEvent(
        kind="candidate.deterministic_transformation", attempt=state.attempt_number,
        source="attempt.pom_corrections", authority=EventAuthority.ADVISORY,
        message=f"pom.xml corrected in the candidate ({', '.join(corrections)}) before verification",
        details={"path": "pom.xml", "corrections": corrections,
                 "before_sha256": snapshot.raw_sha256, "after_sha256": raw_digest(data)},
    ))



def _bind_verification_tree(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> VerificationTreeBinding:
    """FILE-INTEGRITY-CONTRACT-001, before the first gate of an attempt:
    prove the sandbox holds the staged candidate, then bind the whole tree
    the gates will verify (repository content + candidate). Every validator
    of this attempt checks it after each command it runs."""
    deps._require_worktree_matches_candidate(state, ctx)
    deps._record_candidate_change(state, ctx)
    binding = VerificationTreeBinding(
        ctx.worktree_path, repository_content_paths(ctx.workspace_path), state.candidate_digests,
    )
    state.verification_tree_binding = binding
    return binding



def _record_candidate_change(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> None:
    """LR-R1-M1 ``candidate.change`` (STAGED): the candidate exactly as it is
    frozen for this attempt's gates - per path the base and candidate raw
    digests (the commit-evidence digests) and, as content, both byte
    versions and their unified diff. Observational."""
    if attempt_evidence_scope.capture_mode() is None:
        return
    import difflib

    for relpath, after_digest in sorted(state.candidate_digests.items()):
        try:
            before = state.all_original_raw.get(relpath)
            path = os.path.join(ctx.worktree_path, relpath)
            after = None
            if after_digest is not None and os.path.isfile(path):
                with open(path, "rb") as handle:
                    after = handle.read()
            try:
                before_text = before.decode("utf-8") if before is not None else ""
                after_text = after.decode("utf-8") if after is not None else ""
            except UnicodeDecodeError:
                before_text = after_text = None   # bytes are recorded; no lossy text diff
            diff = None if before_text is None else "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in difflib.unified_diff(
                    before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                    fromfile="/dev/null" if before is None else f"a/{relpath}",
                    tofile="/dev/null" if after is None else f"b/{relpath}"))
            diff_lines = (diff or "").splitlines()
            added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
            before_digest = raw_digest(before) if before is not None else None
            payload = {"decision": "STAGED", "path": relpath, "before_digest": before_digest,
                       "after_digest": after_digest, "created": before is None, "deleted": after_digest is None,
                       # ENFORCE-IDENTICAL-WRITE-COMPLETION-001: the candidate equals the base - no change.
                       "unchanged": before_digest is not None and before_digest == after_digest,
                       "lines_added": added, "lines_removed": removed, "text_diff": diff is not None}
        except Exception as error:  # observational: never alters the attempt
            deps.logger.warning("Attempt evidence: candidate.change for %s not built (%s)", relpath, error)
            continue
        attempt_evidence_scope.emit("candidate.change", payload,
                                    content={"before": before, "after": after, "diff": diff})



def _require_worktree_matches_candidate(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> None:
    """FILE-INTEGRITY-CONTRACT-001, before any verification gate: every
    candidate file in the sandbox holds exactly the bytes the authorized
    writer staged for it (``state.candidate_digests``). A difference means
    something wrote the candidate outside the staged writer; verification
    never runs on bytes the commit batch would not carry."""
    mismatches = []
    for relpath, expected in sorted(state.candidate_digests.items()):
        path = os.path.join(ctx.worktree_path, relpath)
        actual = file_raw_digest(path) if os.path.lexists(path) else None
        if actual != expected:
            mismatches.append(relpath)
    if not mismatches:
        return
    message = (f"{WORKTREE_CONTENT_MISMATCH}: candidate file(s) {', '.join(mismatches[:10])} no longer hold the "
               "bytes the authorized writer staged; verification is not run on them")
    failure = Failure(
        type="internal_framework_error", message=message, raw_output=message, source="orchestrator",
        authority="deterministic", attempt=state.attempt_number,
        diagnostics={"reason_code": WORKTREE_CONTENT_MISMATCH, "paths": mismatches[:50]},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)



async def _run_verification_only_attempt(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> None:
    """First-class execution path for a verification-only subtask
    (ctx.write_scope_mode == WriteScopeMode.DENY_ALL): executes its own
    declared deterministic verifier(s) directly against the existing
    worktree content - no Developer invocation, no candidate write attempt,
    no retry loop of its own. Raises QualityGateFailure (the same typed
    shape an ordinary compile/test failure already raises) the moment any
    verifier fails, so the EXISTING failure-attribution/recovery machinery
    handles it exactly as it would any other subtask failure - this
    function does not invent a new recovery path.

    Found live, PRV-05 (2026-08-28): before this existed, a verification-
    only subtask (files=[], DENY_ALL) still entered the ordinary Developer
    mutation/retry pipeline. Every attempt's Developer response inevitably
    tried to write SOMETHING (it has no other protocol), DENY_ALL correctly
    rejected each one, and the loop burned 6 attempts (escalating to the
    fallback model) before a runtime-verification side effect happened to
    produce the exact evidence a direct verifier call would have produced
    on attempt 1. Confirmed live evidence, same run: real `mvn -e test`
    exit-0 evidence WAS produced, but under gate_outcome type="run_verification"
    (the runtime-verification path's own type) while the plan's declared
    requirement was type=tool/tool_name=test - workflow.py's
    _build_required_verification_evidence() only matches a tool/test
    requirement against type in {"test","targeted_test","regression_test"}
    outcomes, so the real evidence was never connected to the obligation.
    Running the DECLARED verifier directly here (via the same
    PolymorphicValidator.run_tests()/run_compile_check() every ordinary
    attempt already uses) records the gate_outcome under the SAME type the
    requirement itself expects, closing that mismatch as a side effect of
    fixing the real problem (verification-only subtasks running the wrong
    execution path) rather than patching the symptom (evidence-matching
    logic) directly."""
    from kriya.tools.validate import PolymorphicValidator
    verification_inputs_digest = deps.load_verification_inputs_digest()

    state.attempt_number += 1
    attempt_evidence_scope.attempt_opened({"mode": "verification_only", "operation": None})
    # LR-R1-P4: what this verification verifies, for the retry admission.
    state.verification_only_inputs = verification_inputs_digest(state, ctx)
    state.verification_only_inputs_attempt = state.attempt_number
    state.candidate_gates_succeeded = False
    validator = PolymorphicValidator(
        ctx.worktree_path, original_workspace_path=ctx.workspace_path,
        autonomy_cfg=ctx.kernel.config.autonomy,
        toolchain_declaration_mutable=deps._ctx_toolchain_declaration_mutable(ctx),
    )
    # PRD-031: the declared verifiers are sequenced by VerificationCoordinator.
    await VerificationCoordinator(
        validator, record_gate_outcome=lambda outcome: state.record_gate_outcome(outcome),
        run_runtime_verification=lambda: deps._execute_runtime_verification_directly(state, ctx, validator),
        judge_suite=lambda result: deps._judge_candidate_suite(state, ctx, result, deps.SCOPE_VERIFICATION_UNIT),
    ).verify(VerificationRequest(
        required_verification=ctx.required_verification,
        known_files=sorted(set(ctx.established_files)), attempt_number=state.attempt_number,
    ))

    state.candidate_gates_succeeded = True
    state.record_event(RunEvent(
        kind="candidate_gates.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "terminal": False, "verification_only": True},
    ))
    log_gate_banner(
        "CANDIDATE GATES", "PASSED", state.attempt_number,
        scope=ctx.execution_scope,
    )



def _run_pom_validation_gate(deps: GateDeps, state: RunState, ctx: AttemptCtx, tree_binding: Any) -> None:
    """The POM semantic check (``mvn validate`` on the candidate: it EXECUTES Maven against model-authored build files). Extracted unchanged from run_attempt so human-in-the-loop mode can
    hold it, with the rest of the executing gates, until the source has been approved (``defer_execution_until_source_review``)."""
    from kriya.tools.validate import PolymorphicValidator  # local: tests patch the class on kriya.tools.validate
    pom_files = [
        filepath for filepath in state.files_written
        if os.path.basename(filepath) == "pom.xml"
    ]
    if pom_files:
        pom_validator = PolymorphicValidator(
            ctx.worktree_path, autonomy_cfg=ctx.kernel.config.autonomy,
        )
        pom_validator.tree_binding = tree_binding
        pom_validate_res = pom_validator.run_pom_validate()
        if not pom_validate_res["success"]:
            # F7: Maven could not be run (or returned no verdict): the typed environment code makes this the existing verification_infrastructure_failure stop, not
            # a POM defect - the Developer is never asked to repair a POM that was not judged. Only a verdict from Maven itself is a pom_semantic_validation.
            _stop_on_environment_gate_result(state, pom_validate_res, "pom_validate")
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
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)



def _new_candidate_validator(deps: GateDeps, ctx: AttemptCtx) -> Any:
    """The candidate-gates validator for this attempt (a new one every attempt). Extracted unchanged from the executing tail so the pre-review corrections can use the same one."""
    from kriya.tools.validate import PolymorphicValidator  # local: tests patch the class on kriya.tools.validate
    validator = PolymorphicValidator(
        ctx.worktree_path, original_workspace_path=ctx.workspace_path,
        autonomy_cfg=ctx.kernel.config.autonomy,
        toolchain_declaration_mutable=deps._ctx_toolchain_declaration_mutable(ctx),
        # PRV-05 (2026-08-28, run 5): the dependency-preservation check
        # below used to unconditionally reject ANY pom.xml dependency
        # removal, restoring Gson every time the subtask that owns
        # pom.xml tried to remove it - even though the top-level goal
        # explicitly authorizes replacing it. PRV-05 run 6 (2026-08-28):
        # re-resolving authorization from CURRENT (possibly already-
        # mutated) workspace state per attempt was itself timing-
        # sensitive - read from the run's ONE already-resolved
        # migration_resolution instead (see AttemptContext.migration_
        # resolution's own docstring), never re-derived here.
        authorized_dependency_removals=(
            set(ctx.migration_resolution.obligation.source_artifacts)
            if ctx.migration_resolution is not None
            and ctx.migration_resolution.status == MigrationResolutionStatus.RESOLVED
            and ctx.migration_resolution.obligation is not None
            else set()
        ),
    )
    return validator



def _resolve_candidate_toolchain(deps: GateDeps, state: RunState, ctx: AttemptCtx, validator: Any) -> None:
    """The one-time toolchain preflight and the JAVA_HOME override, applied to this attempt's validator. Extracted unchanged from the executing tail (no candidate code runs here)."""
    if not state.toolchain_checked:
        state.toolchain_checked = True
        state.toolchain_warning = deps._check_java_toolchain_mismatch(validator.stack)
        if state.toolchain_warning:
            deps.logger.warning(f"Toolchain preflight: {state.toolchain_warning}")
        if validator.stack == "java":
            state.java_home_override = deps._resolve_java_home_override(ctx.goal)
            if state.java_home_override:
                deps.logger.warning(
                    "JVM toolchain enforcement: forcing Maven subprocess calls to "
                    f"run under JAVA_HOME={state.java_home_override} - the goal-stated Java "
                    "version doesn't match what 'mvn' resolves to by default here."
                )
    # Constructed fresh above (a new validator every attempt) - re-apply
    # the one-time-resolved override every time, not just when it was
    # just computed.
    validator.java_home_override = state.java_home_override



def apply_pre_review_candidate_corrections(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> None:
    """Deterministic pom.xml corrections are ordinary candidate mutations (they are in the candidate's diff and commit batch). In the held-execution mode they must happen BEFORE the
    binding is taken and the source is shown, or the build would run on bytes the person did not review (independent review B1). Idempotent: the executing tail applies them again as a no-op."""
    validator = deps._new_candidate_validator(ctx)
    deps._resolve_candidate_toolchain(state, ctx, validator)
    deps._apply_candidate_pom_corrections(state, ctx, sorted(set(state.all_files_written) | set(ctx.established_files)))



async def _run_executing_candidate_gates(
    deps: GateDeps, state: RunState, ctx: AttemptCtx, skipping_candidate_gates: bool, tree_binding: Any, active_code_context: str,
) -> None:
    """Every candidate gate that EXECUTES the candidate (compile, tests, the runtime verification) and the gates that judge after them (migration completion, spec compliance),
    through ``candidate_gates_succeeded``. Extracted unchanged from run_attempt: in human-in-the-loop mode with an approver it is held (``ctx.deferred_executing_gates``) and run by
    the caller right after the person approves the source, so no model-authored content is built, tested or run before that review. ``ctx.post_source_review_guard`` (set while
    it runs after approval) is consulted at each execution boundary and the candidate-editing self-correction micro-loops are not entered."""
    if not skipping_candidate_gates:
        # Quality Gates: Polymorphic compile & test checks inside sandbox
        deps.logger.info("Quality Gates: Running polymorphic compiler and test checks...")
        validator = deps._new_candidate_validator(ctx)
        deps._resolve_candidate_toolchain(state, ctx, validator)

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
        deps._apply_candidate_pom_corrections(state, ctx, compile_known_files)
        deps._require_worktree_matches_candidate(state, ctx)
        tree_binding.check("pre_compile")
        validator.tree_binding = tree_binding

        _compile_started = deps.time.monotonic()
        compile_res = validator.run_compile_check(compile_known_files)
        deps._stop_on_environment_gate_result(state, compile_res, "compile")
        # R1 Deliverable 5 - observational only, timing the primary compile
        # gate call (the main full-set/targeted path's own compile check,
        # not every secondary/verification-only call site in this module -
        # see docs/assurance/KRIYA_PERFORMANCE_TELEMETRY.md for the exact
        # coverage boundary). Recorded AFTER the call returns; never reads
        # compile_res beyond what the very next line already does.
        state.validator_timings.append({
            "kind": "compile",
            "duration_seconds": deps.time.monotonic() - _compile_started,
            "success": bool(compile_res.get("success")),
        })
        if not compile_res["success"]:
            self_correction_result = None
            if ctx.kernel.config.autonomy.self_correction_loop_enabled and ctx.post_source_review_guard is None:
                run_self_correction_loop = deps.load_run_self_correction_loop()
                deps.logger.info(
                    "Compile gate failed - attempting bounded self-correction "
                    "micro-loop before raising QualityGateFailure."
                )
                self_correction_result = await deps._as_developer(run_self_correction_loop(
                    llm=ctx.developer.llm,
                    worktree_path=ctx.worktree_path,
                    validator=validator,
                    files_in_scope=compile_known_files,
                    # DENY_ALL means never writable here either - a bug in
                    # this recovery/fallback loop must not be able to bypass
                    # the same invariant AuthorizedFileWriter enforces at the
                    # real write gate below (see WriteScopeMode's own
                    # docstring for why "recovery paths can accidentally
                    # bypass otherwise correct invariants" is a confirmed,
                    # not hypothetical, risk here).
                    writable_files=(
                        [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                        else (ctx.allowed_write_relpaths or compile_known_files)
                    ),
                    compile_error_output=compile_res["output"],
                    active_code_context=active_code_context,
                    max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                    authorized_semantic_regions=ctx.authorized_semantic_regions,
                    strict_existing_java_files=ctx.kernel.config.autonomy.semantic_region_enforcement_required,
                    baseline_contents=state.all_original_contents,
                    # DEVELOPER-AUX-LOOP-PROMPT-FIT-001: the primary Developer
                    # binding's request capacity bounds every turn.
                    request_capacity=deps.request_capacity(ctx.kernel.config),
                ))
                deps._record_self_correction_scope_conflict(
                    state, ctx, self_correction_result, "compile",
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
                deps.logger.info(
                    "Self-correction micro-loop resolved the compile failure in "
                    f"{self_correction_result.turns_used} turn(s)."
                )
                state.record_gate_outcome({
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
                        deps._build_workspace_type_index(state, ctx),
                        deps._build_java_package_map(java_files_for_mismatch, ctx),
                    )
                    if mismatch:
                        missing_symbol, referencing_path, candidate_path = mismatch
                        java_packages_for_message = deps._build_java_package_map(
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
                        # Found live, PRV-03 hardened (2026-08-27): "not
                        # tracked by this run" does NOT mean stale - a real,
                        # currently-existing brownfield sibling (e.g.
                        # CustomerService.java, broken because THIS
                        # candidate changed the Customer record's own
                        # constructor arity) is exactly as "unrecognized"
                        # to compile_known_files as genuine leftover cruft
                        # from an unrelated run would be. The two are only
                        # distinguishable by checking whether the file
                        # actually exists in the real worktree - resolve
                        # that before asserting either story, rather than
                        # defaulting to the "stale" framing that's actively
                        # wrong for the common brownfield case.
                        real_siblings = resolve_repository_locator_files(
                            compile_res.get("output", ""), ctx.worktree_path, compile_known_files,
                        )
                        genuinely_unrecognized = sorted(
                            set(unrecognized) - {os.path.basename(p) for p in real_siblings}
                        )
                        if real_siblings:
                            compile_message += (
                                "\n\nNOTE: this error also references existing repository "
                                f"file(s) this run's own tracking never declared as scope: "
                                f"{', '.join(sorted(real_siblings))}. These are REAL, CURRENT "
                                "files, not stale content - if they only started failing to "
                                "compile because of THIS candidate's own change (e.g. an "
                                "existing type's constructor/signature changed), that is a "
                                "regression this candidate caused. Prefer preserving the "
                                "existing contract over expanding scope to repair every caller "
                                "it breaks."
                            )
                        if genuinely_unrecognized:
                            compile_message += (
                                "\n\nNOTE: this error also references file(s) not tracked by "
                                f"this run at all and not found anywhere in the current "
                                f"workspace either: {', '.join(genuinely_unrecognized)}. These "
                                "are likely stale/leftover content from an earlier, unrelated "
                                "run or attempt - not something the current milestone's own "
                                "files can fix. If they don't belong, they should be removed "
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
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
        else:
            state.record_gate_outcome({
                "attempt": state.attempt_number,
                "type": "compile",
                "success": True,
                "output": compile_res.get("output", ""),
                **execution_evidence(compile_res),
            })

        # A successful real compile is the authority for cross-file signature,
        # import, classpath, and build consistency. Record exact revisions only
        # after that gate; heuristics never mark a file validated.
        state.validated_file_revisions = {
            filepath: read_file_revision(os.path.join(ctx.worktree_path, filepath))
            for filepath in state.all_files_written
        }

        # BACKEND-READINESS-V2 slot 3: deterministic user contracts that target what this unit touched, before any
        # model verifier or test gate (see _run_contract_feedback_gate).
        deps._run_contract_feedback_gate(state, ctx)

        deps.assert_candidate_unchanged_since_source_review(state, ctx)
        accepted_test_output: Optional[str] = None
        accepted_test_result: Optional[Dict[str, Any]] = None
        runnable_test_files = deps.find_runnable_test_files(state.all_files_written)
        deps._raise_unsafe_process_boundary_test_candidate(
            state, ctx, runnable_test_files,
        )
        deps._raise_ungrounded_child_process_test_candidate(
            state, ctx, runnable_test_files,
        )
        target_test = deps.extract_target_test(state.error_context, list(state.all_files_written))
        if target_test:
            deps.logger.info(f"Quality Gates: Running targeted tests: {target_test}")
            test_repair_result = None
            test_res = validator.run_tests(target_test=target_test)
            deps._stop_on_environment_gate_result(state, test_res, "targeted_test")
            if not output_confirms_nonzero_test_execution(test_res.get("output", "")):
                # A targeted command that collected zero tests says the selector
                # did not identify an executable test; it says nothing about the
                # generated application.  Do not route this orchestration error to
                # the Developer as a source repair.  Retry once with the runner's
                # native suite discovery, which is the authoritative fallback.
                deps.logger.warning(
                    "Quality Gates: Targeted test selection collected zero tests for "
                    f"{target_test}; retrying the full suite before attributing a code failure."
                )
                state.record_gate_outcome({
                    "attempt": state.attempt_number,
                    "type": "test_selection",
                    "success": False,
                    "output": test_res.get("output", ""),
                    "target_test": target_test,
                    "recovered_by": "full_suite",
                })
                target_test = None
                test_res = validator.run_tests()
                deps._stop_on_environment_gate_result(state, test_res, "test")
                suite_blocks, suite_evidence, suite_gate_evidence = deps._judge_candidate_suite(
                    state, ctx, test_res, deps.SCOPE_CANDIDATE_GATE)
                if suite_blocks:
                    failure = _build_test_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{suite_evidence}",
                        suite_evidence, ctx.worktree_path,
                        state.all_files_written, state.attempt_number,
                    )
                    if failure.type == "test_process_terminated":
                        if deps._record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "test_selection_fallback", "raw_output": failure.raw_output},
                        ):
                            failure.message += deps._PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    failure.diagnostics = {**(failure.diagnostics or {}), **suite_gate_evidence}
                    state.record_gate_outcome({**failure.to_gate_outcome(), **suite_gate_evidence})
                    raise QualityGateFailure(failure)
                deps._record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.record_gate_outcome({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", ""),
                    "selection_fallback": True,
                    **execution_evidence(test_res),
                    **suite_gate_evidence,
                })
                accepted_test_output = test_res.get("output", "")
                accepted_test_result = test_res

            if target_test and not test_res["success"]:
                if ctx.kernel.config.autonomy.self_correction_loop_enabled and ctx.post_source_review_guard is None:
                    run_repair_loop = deps.load_run_repair_loop()
                    test_repair_result = await run_repair_loop(
                        llm=ctx.developer.llm,
                        worktree_path=ctx.worktree_path,
                        validator=validator,
                        files_in_scope=list(state.all_files_written),
                        # See the compile-gate self-correction call site's own
                        # comment (above, this module) for why DENY_ALL is
                        # checked explicitly here too.
                        writable_files=(
                            [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                            else (ctx.allowed_write_relpaths or list(state.all_files_written))
                        ),
                        compile_error_output=test_res["output"],
                        active_code_context=active_code_context,
                        max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                        failure_type="targeted_test",
                        target_test=target_test,
                    )
                    deps._record_self_correction_scope_conflict(
                        state, ctx, test_repair_result, "targeted_test",
                    )
                    for incident in getattr(test_repair_result, "incidents", []):
                        state.record_event(RunEvent(
                            kind="auxiliary.failed", attempt=state.attempt_number,
                            source=incident["source"], authority=EventAuthority.AUXILIARY,
                            message=incident["message"], failure_type=incident["type"],
                            operation="repair_with_patch",
                        ))
                if test_repair_result and test_repair_result.resolved:
                    deps._record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                    state.record_gate_outcome({
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
                    failure = _build_test_quality_gate_failure(
                        "targeted_test", f"TARGETED TEST FAILURE:\n{test_res['output']}",
                        test_res.get("output", ""), ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if test_repair_result is not None:
                        failure.self_correction_attempt = {
                            "turns_used": test_repair_result.turns_used,
                            "transcript": test_repair_result.transcript,
                            "final_validation_output": test_repair_result.final_compile_output,
                        }
                    if failure.type == "test_process_terminated":
                        if deps._record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "targeted_test", "raw_output": failure.raw_output},
                        ):
                            failure.message += deps._PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
            if target_test and not (test_repair_result and test_repair_result.resolved):
                deps._record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.record_gate_outcome({
                    "attempt": state.attempt_number,
                    "type": "targeted_test",
                    "success": True,
                    "output": test_res.get("output", ""),
                    **execution_evidence(test_res),
                })
            if target_test:
                accepted_test_output = test_res.get("output", "")
                accepted_test_result = test_res
        else:
            # The repository's own suite also runs when the plan declares a
            # test verification for this stage: an edit of existing source
            # writes no test file, and without this run the declared test
            # evidence could never exist (REQUIRED VERIFICATION UNRESOLVED
            # after every other gate passed - live, more-itertools).
            if runnable_test_files or deps._declares_test_verification(ctx.required_verification):
                deps.logger.info(f"Quality Gates: Executing tests for {validator.stack} stack...")
                test_res = validator.run_tests()
                deps._stop_on_environment_gate_result(state, test_res, "test")
                # CANDIDATE-GATE-BASELINE-POLICY-001 (C2-S2_A): judged against
                # the frozen PRE baseline, never raw - a pre-existing failure
                # the terminal check would not attribute to this candidate
                # does not block its candidate gate either.
                suite_blocks, suite_evidence, suite_gate_evidence = deps._judge_candidate_suite(
                    state, ctx, test_res, deps.SCOPE_CANDIDATE_GATE)
                if suite_blocks:
                    failure = _build_test_quality_gate_failure(
                        "test", f"TEST FAILURE:\n{suite_evidence}",
                        suite_evidence, ctx.worktree_path, state.all_files_written, state.attempt_number,
                    )
                    if failure.type == "test_process_terminated":
                        if deps._record_process_boundary_obligation(
                            ctx, state, violated=True,
                            evidence={"detected_via": "full_suite", "raw_output": failure.raw_output},
                        ):
                            failure.message += deps._PROCESS_BOUNDARY_RECURRENCE_ESCALATION
                    failure.diagnostics = {**(failure.diagnostics or {}), **suite_gate_evidence}
                    state.record_gate_outcome({**failure.to_gate_outcome(), **suite_gate_evidence})
                    raise QualityGateFailure(failure)
                deps._record_process_boundary_obligation(ctx, state, violated=False, evidence={})
                state.record_gate_outcome({
                    "attempt": state.attempt_number,
                    "type": "test",
                    "success": True,
                    "output": test_res.get("output", ""),
                    **execution_evidence(test_res),
                    **suite_gate_evidence,
                })
                accepted_test_output = test_res.get("output", "")
                accepted_test_result = test_res

        # PRV-11 (2026-08-30): obligation/ownership-aware, not a blind scan
        # over ctx.goal - see subtask_owns_test_obligation's own docstring
        # for the live incident (a FUTURE_ORDERED test obligation genuinely
        # owned by a LATER subtask was being treated as CURRENT for every
        # OTHER subtask, merely because the full top-level goal - correctly,
        # since the authority-isolation fix - is visible in every subtask's
        # own ctx.goal, not because anything actually reassigned ownership).
        this_subtask_owns_tests = subtask_owns_test_obligation(
            ctx.grounding_goal or ctx.goal, ctx.structured_plan, ctx.current_subtask_id,
        )
        if accepted_test_output is None and this_subtask_owns_tests:
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
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # The same acceptance rule applies whether extract_target_test() chose
        # one test or the validator ran the suite. Test-selection strategy must
        # never change an explicit user contract.
        if (
            accepted_test_output is not None
            and this_subtask_owns_tests
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
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        if accepted_test_output is not None and this_subtask_owns_tests:
            deps._raise_unexecuted_test_delta(state, ctx, validator, accepted_test_result, target_test)

        # Quality Gates: Runtime Verification. Compiling and passing whatever tests
        # exist only proves the code is valid - it says nothing about whether it does
        # what the goal actually asked for, which matters most for goals with no test
        # suite at all. Judgment decides per-attempt whether this goal describes
        # self-terminating runtime behavior worth actually running and checking.
        deps.assert_candidate_unchanged_since_source_review(state, ctx)  # before the runtime-command review can be raised for a candidate the tests may have changed
        autonomy_cfg_rv = ctx.kernel.config.autonomy
        if ctx.runtime_verification_required and not autonomy_cfg_rv.run_verification_enabled:
            message = (
                "REQUIRED_RUNTIME_VERIFICATION_DISABLED: the declared verification contract "
                "requires observable execution, but runtime verification is disabled."
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        deps._raise_if_required_runtime_verification_declined(state, ctx)
        if autonomy_cfg_rv.run_verification_enabled and not state.run_verification_declined:
            current_judgment_basis = deps._run_verification_basis_hash(ctx, state)
            if (
                state.cached_run_verification_judgment is not None
                and state.cached_run_verification_basis_hash != current_judgment_basis
            ):
                deps.logger.info(
                    "Invocation-affecting workspace content changed - invalidating cached "
                    "runtime-verification judgment."
                )
                state.cached_run_verification_judgment = None
            if state.cached_run_verification_judgment is None:
                pom_content_for_judge = None
                try:
                    with open(os.path.join(ctx.worktree_path, "pom.xml"), "r", encoding="utf-8") as f:
                        pom_content_for_judge = f.read()
                except Exception as e:
                    deps.logger.debug(f"No pom.xml available for run-verification judgment: {e}")
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
                if raw_judgment.get("infrastructure_error") and ctx.runtime_verification_required:
                    message = (
                        "VERIFICATION INFRASTRUCTURE FAILURE: runtime behavior is required, but "
                        f"the runtime-verification judge was unavailable: "
                        f"{raw_judgment['infrastructure_error']}"
                    )
                    failure = Failure(
                        type="verification_infrastructure_failure",
                        message=message,
                        raw_output=message,
                        attempt=state.attempt_number,
                    )
                    state.record_gate_outcome(failure.to_gate_outcome())
                    raise QualityGateFailure(failure)
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
                state.cached_run_verification_basis_hash = current_judgment_basis
            else:
                deps.logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
            judgment = state.cached_run_verification_judgment
            if deps._runtime_verification_is_advisory_only(ctx, judgment):
                # PRV-17 (2026-09-03): the SAME authority _execute_runtime_
                # verification_directly() consults for a DENY_ALL
                # verification-only subtask - this is the OTHER, much more
                # common call site (an ordinary ALLOWLIST implementation
                # subtask's own candidate-gates run-verification), which a
                # prior fix missed entirely: patching only the DENY_ALL copy
                # left this one free to still execute a FUTURE-owned
                # endpoint's inferred command for an ordinary scaffold
                # subtask. Forcing should_run False here (rather than an
                # early return) lets every later section of this same
                # attempt - candidate_gates_succeeded, Spec Compliance, etc.
                # - proceed exactly as it already does for judge()'s own
                # genuine should_run=False verdict, matching precedent
                # already established a few lines below for the Java-
                # entrypoint self-heal.
                deps.logger.info(
                    "Run-verification judge inferred should_run=True, but this subtask's own "
                    "approved plan declares no runtime-verification obligation - treating as "
                    "advisory only, not executing (a later subtask that DOES own this "
                    "obligation still runs it for real)."
                )
                judgment = dict(judgment)
                judgment["should_run"] = False
                judgment["run_commands"] = None
            if ctx.runtime_verification_required and not judgment.get("should_run"):
                message = deps._required_runtime_verification_missing_message(judgment)
                failure = Failure(
                    type="verification_infrastructure_failure", message=message,
                    raw_output=message, attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
            execution_mode, execution_mode_error = deps._resolve_execution_mode(judgment)
            if execution_mode_error:
                message = f"MANAGED_SERVICE_CONTRACT_INVALID: {execution_mode_error}"
                failure = Failure(
                    type="verification_infrastructure_failure", message=message,
                    raw_output=message, attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
            if judgment.get("should_run") and execution_mode == "managed_service":
                await deps._execute_managed_service_verification(
                    state, ctx, judgment,
                    sorted(set(state.all_files_written) | set(ctx.established_files)),
                    validator,
                )
                return
            if (
                ctx.runtime_verification_required
                and judgment.get("should_run")
                and deterministic_sequence_kind(judgment.get("run_commands") or []) == "build"
            ):
                message = (
                    "BEHAVIORAL_GOAL_WITH_BUILD_ONLY_VERIFICATION: observable runtime behavior "
                    "is required, but the inferred sequence contains only build commands."
                )
                failure = Failure(
                    type="verification_infrastructure_failure", message=message,
                    raw_output=message, attempt=state.attempt_number,
                )
                state.record_gate_outcome(failure.to_gate_outcome())
                raise QualityGateFailure(failure)
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
                        deps._build_java_main_class_map(java_files, ctx),
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
                        deps.logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and no known .java file has a main() method - overriding "
                            "should_run to False instead of executing a command that targets "
                            "a nonexistent entrypoint class."
                        )
                        judgment = dict(judgment)
                        judgment["should_run"] = False
                        judgment["run_commands"] = None
                    elif corrected_commands != judgment["run_commands"]:
                        deps.logger.info(
                            "Deterministic Java entrypoint resolution: no pom.xml/build.gradle "
                            "found and exactly one real entrypoint was detected - overriding the "
                            f"inferred run command(s) with {corrected_commands} instead of "
                            "trusting the model's own guess."
                        )
                        judgment = dict(judgment)
                        judgment["run_commands"] = corrected_commands

                # VER-005 implementation (2026-09-13): the Python sibling of
                # the Java entrypoint grounding just above - see
                # ground_python_runtime_target()'s own docstring (kriya/
                # workflow/file_resolution.py) for the live E4 defect this
                # closes. Applied fresh every attempt, same as the Java
                # branch above - never cached alongside judge()'s own
                # judgment, since a retry can edit file content between
                # attempts. Independent of the Java branch (mutually
                # exclusive in practice - java_files is empty for a real
                # Python project, and this block's own gate is False for a
                # real Java one), so no interaction between them.
                if any(f.endswith(".py") for f in entrypoint_known_files) or validator.stack == "python":
                    all_python_files, package_dirs, entrypoint_files = deps._build_python_runtime_grounding(
                        validator.workspace_path
                    )
                    corrected_py_commands = ground_python_runtime_target(
                        judgment["run_commands"], judgment["command_source"],
                        all_python_files, package_dirs, entrypoint_files,
                    )
                    if corrected_py_commands is None:
                        deps.logger.info(
                            "Deterministic Python runtime-target grounding: the run-verification "
                            "judge's proposed target is not a valid application runtime target "
                            "(test-shaped, nonexistent, or unguarded), and no unambiguous real "
                            "entrypoint exists elsewhere in the repository to substitute - "
                            "overriding should_run to False instead of executing an invalid target."
                        )
                        judgment = dict(judgment)
                        judgment["should_run"] = False
                        judgment["run_commands"] = None
                    elif corrected_py_commands != judgment["run_commands"]:
                        deps.logger.info(
                            "Deterministic Python runtime-target grounding: the run-verification "
                            "judge's proposed target was not a valid application runtime target - "
                            f"substituting {corrected_py_commands} instead of trusting the model's "
                            "own guess."
                        )
                        judgment = dict(judgment)
                        judgment["run_commands"] = corrected_py_commands
            if judgment["should_run"]:
                await deps._approve_and_execute_runtime_verification(state, ctx, validator, active_code_context, judgment, autonomy_cfg_rv)


    # Quality Gates: Migration Completion. Deliberately runs BEFORE Spec
    # Compliance and is never overridable by it - an explicit, objectively-
    # testable migration obligation (see kriya/workflow/migration.py's own
    # docstring for the PRV-05, 2026-08-28 incident) must not be reopened by
    # a semantic verdict that disagrees.
    #
    # PRV-05 run 6 (2026-08-28): this used to call resolve_migration_
    # obligation() fresh, per attempt, against ctx.workspace_path - re-
    # inferring source/target identity from whatever state the repository
    # happened to be in at that moment, which is exactly the timing-
    # sensitive defect this module's own docstring now documents. Reads the
    # run's ONE already-resolved migration_resolution instead (see
    # AttemptContext.migration_resolution's own docstring) - identity is
    # fixed; only find_migration_incomplete's completion CHECK still runs
    # fresh, against this attempt's own current candidate tree, which is
    # exactly what it's for.
    #
    # Only relevant to THIS attempt when its own scope (architect_files)
    # actually touches the obligation's grounded consumer(s) or the
    # manifest (pom.xml) - an unrelated subtask elsewhere in the same plan
    # must not be failed for a migration it was never responsible for.
    migration_obligation = (
        ctx.migration_resolution.obligation
        if ctx.migration_resolution is not None
        and ctx.migration_resolution.status == MigrationResolutionStatus.RESOLVED
        else None
    )
    subtask_owns_migration_scope = migration_obligation is not None and bool(
        set(ctx.architect_files) & (set(migration_obligation.grounded_consumers) | {"pom.xml"})
    )
    if subtask_owns_migration_scope:
        # validation_scope=CURRENT_SUBTASK (PRV-05 run 7, 2026-08-28): only
        # requirements DUE at this subtask's position in the plan can fail
        # this gate - a requirement whose only implicated file(s) belong to
        # a not-yet-reached, dependency-ordered subtask (e.g. this exact
        # PRV-05 plan's s4, which owns removing the old dependency from
        # pom.xml) is PENDING here, not FAILED, even though s1 legitimately
        # touches the grounded consumer. Degrades to the original always-
        # TERMINAL behavior when ctx.structured_plan/current_subtask_id are
        # None (a non-MA6-structured caller) - see MigrationValidationScope's
        # own docstring.
        migration_gap = find_migration_incomplete(
            migration_obligation, ctx.worktree_path,
            current_subtask_id=ctx.current_subtask_id,
            engineering_plan=ctx.structured_plan,
            validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
            obligation_ledger=ctx.obligation_ledger,
            revision=state.attempt_number, source="migration.attempt_gate",
        )
        if migration_gap:
            message = (
                "MIGRATION INCOMPLETE: the goal explicitly requires replacing "
                f"{migration_gap['source_identity']} with {migration_gap['target_identity']}, but "
                f"{', '.join(migration_gap['reason_codes'])}. Grounded consumer(s) that must use "
                f"{migration_gap['target_identity']}: {', '.join(migration_gap['grounded_consumers']) or 'none'}."
            )
            # Union, not "first non-empty wins": found live, PRV-05
            # (2026-08-28 rerun) - a fully-migrated consumer with the
            # dependency still declared leaves BOTH unmigrated_consumers
            # and source_usage_files empty, so the failure had no
            # likely_files at all and couldn't point the retry/
            # attribution pipeline at the one file that actually needs
            # fixing (typically pom.xml, owned by a different, already-
            # completed subtask - see migration.py's own manifest_files
            # docstring). Already DUE-filtered by find_migration_incomplete
            # above, so this union never includes a future-owned file.
            evidence_files = list(dict.fromkeys(
                migration_gap["unmigrated_consumers"]
                + migration_gap["source_usage_files"]
                + migration_gap["manifest_files"]
            ))
            failure = _build_quality_gate_failure(
                "migration_incomplete", message, message,
                ctx.worktree_path, state.all_files_written, state.attempt_number,
                extra_likely_files=evidence_files,
            )
            # PRV-05 run 7: this evidence is deterministic (parsed from
            # pom.xml/scanned imports), not a text-scan guess - it must
            # outrank the model's own self-diagnosis in attribute_failure()
            # (kriya/workflow/attribution.py), and its "high" confidence
            # lets the existing plan-scope-conflict check (retry_strategy.py)
            # correctly hard-stop rather than call the Developer at all if
            # ever an authoritative target genuinely falls outside this
            # subtask's authorized write scope.
            failure.authoritative_files = evidence_files
            failure.diagnostics = {
                **(failure.diagnostics or {}),
                "reason_code": "MIGRATION_INCOMPLETE",
                "reason_codes": migration_gap["reason_codes"],
                "pending_reason_codes": migration_gap.get("pending_reason_codes", []),
            }
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

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
        compliance_goal, pending_acceptance_ids = deps._stage_scoped_spec_compliance_goal(ctx)
        if ctx.structured_plan is not None and ctx.current_subtask_id:
            deps.logger.info(
                "STAGE_OBLIGATION_SCOPE %s",
                json.dumps({
                    "subtask_id": ctx.current_subtask_id,
                    "decision": "CURRENT_STAGE_ONLY",
                    "pending_acceptance_criteria_ids": pending_acceptance_ids,
                }, sort_keys=True),
            )
        # A bounded consumer is judged against both its own candidate and the
        # already-verified upstream contracts it consumes. Restricting this to
        # files written by the current subtask made compliance incorrectly say
        # an upstream field/type was absent, then reopen a healthy owner.
        spec_check_files = sorted(
            set(state.all_files_written) | set(ctx.established_files)
        )
        spec_file_contents: Dict[str, str] = {}
        # Each file's version before this candidate (None: new), so a file too
        # large to show whole is judged on its changed regions.
        spec_baselines: Dict[str, Optional[str]] = {}
        for spec_path in spec_check_files:
            spec_full_path = os.path.join(ctx.worktree_path, spec_path)
            if not os.path.exists(spec_full_path):
                spec_full_path = os.path.join(ctx.workspace_path, spec_path)
            if not os.path.exists(spec_full_path):
                continue
            try:
                with open(spec_full_path, "r", encoding="utf-8", errors="replace") as fh:
                    spec_file_contents[spec_path] = fh.read()
                baseline_path = os.path.join(ctx.workspace_path, spec_path)
                if os.path.isfile(baseline_path):
                    with open(baseline_path, "r", encoding="utf-8", errors="replace") as fh:
                        spec_baselines[spec_path] = fh.read()
                else:
                    spec_baselines[spec_path] = None
            except Exception as e:
                deps.logger.debug(f"Spec compliance check: couldn't read {spec_path}, skipping it: {e}")
        authoritative_context = deps._spec_compliance_authoritative_context(ctx.obligation_ledger)
        # Correctness Continuity Part A (PRV-06, 2026-08-29): computed BEFORE
        # the call so the fingerprint reflects exactly what's about to be
        # judged, and captured here (not recomputed later) so the settled
        # check and the eventual ledger write always agree on one value.
        goal_spec_obligation_id = (
            deps._goal_spec_requirement_obligation_id(ctx.current_subtask_id)
            if ctx.current_subtask_id else None
        )
        goal_spec_evidence_fingerprint = deps._goal_spec_evidence_fingerprint(compliance_goal, spec_file_contents)
        settled_goal_spec = deps._settled_goal_spec_requirement(
            ctx.obligation_ledger, goal_spec_obligation_id, goal_spec_evidence_fingerprint,
        )
        # PRD-020: the original requirements ride along only when this check
        # is their verifier (never passed otherwise, so existing callers and
        # test doubles see the same call).
        requirement_kwargs = {"requirements": ctx.requirement_set} if ctx.requirement_set is not None else {}
        spec_result = await ctx.spec_compliance.check(
            goal=compliance_goal, files_written=spec_check_files, file_contents=spec_file_contents,
            authoritative_context=authoritative_context, baseline_contents=spec_baselines, **requirement_kwargs,
        )
        if spec_result.get("status") == "indeterminate":
            # SpecComplianceAgent.check() returns this when the model's own
            # verdict is internally contradictory (compliant=false naming no
            # concrete missing requirement) - used to be silently forced to
            # compliant=True (see that method's own docstring for the PRV-05,
            # 2026-08-28 incident this closes: a real migration failure the
            # model's own reasoning had already identified became a
            # fabricated PASS). One bounded re-evaluation, not a retry-budget
            # spend - if it's STILL indeterminate, stop rather than guess
            # either way; trusting a bare compliant=false here would just
            # move the same problem (an unreliable LLM verdict as sole
            # authority) in the opposite direction.
            spec_result = await ctx.spec_compliance.check(
                goal=compliance_goal, files_written=spec_check_files, file_contents=spec_file_contents,
                authoritative_context=authoritative_context, baseline_contents=spec_baselines, **requirement_kwargs,
            )
        if ctx.requirement_set is not None:
            deps._record_original_requirement_verdicts(
                state, ctx, spec_result, goal_spec_evidence_fingerprint,
            )
        if spec_result.get("status") == "indeterminate":
            if deps._migration_obligations_all_satisfied(ctx.obligation_ledger):
                # MA8 follow-up (found live, PRV-05, 2026-08-28): this branch
                # used to raise unconditionally, with ZERO reference to
                # ctx.obligation_ledger, even though the sibling "not
                # compliant, with actual named requirements" branch just
                # below already arbitrates against it. A candidate whose
                # migration was ALREADY fully and correctly complete
                # (confirmed live: the real worktree files, not just the
                # log - pom.xml, JsonService.java, JacksonConfig.java all
                # correct) got destabilized into an 11-attempt budget
                # exhaustion by this exact gap - the model's own repeated
                # verdict ("the code still uses Jackson... does not show
                # replacement of any prior library") is the same direction-
                # hallucination class this whole gate exists to catch, just
                # reached through the indeterminate shape instead of a
                # named-requirement one. The up-front authoritative_context
                # prompt addition alone did not prevent it - confirming the
                # spec's own "do not trust the prompt alone" warning live.
                # Same suppression treatment as the arbitrated-requirements
                # branch below: normalize to compliant so the rest of this
                # function's existing success path handles it, rather than
                # adding a second, parallel success path.
                deps.logger.warning(
                    "Quality Gates: Goal spec compliance returned an internally "
                    "contradictory INDETERMINATE verdict twice in a row while every "
                    "current migration obligation is deterministically SATISFIED - "
                    "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY, suppressed (treated as "
                    "compliant, not used to trigger retry): %s",
                    spec_result.get("reasoning"),
                )
                spec_result = {
                    "compliant": True, "reasoning": spec_result.get("reasoning", ""),
                    "missing_requirements": [], "likely_files": [],
                    "status": "indeterminate_suppressed",
                    "reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                }
            else:
                # GR-R1B: LLM output can authorize nothing, in either
                # direction. A model verdict contradictory twice in a row is
                # still only the model's word: advisory diagnostic evidence,
                # never an attempt failure, a retry or a fallback. Stronger
                # evidence acts on its own (every deterministic gate above,
                # and the terminal requirement gate, which leaves anything
                # without trusted closure UNVERIFIED and blocked).
                spec_result = {
                    "compliant": True, "reasoning": spec_result.get("reasoning", ""),
                    "missing_requirements": [], "likely_files": [],
                    "status": SPEC_MODEL_INDETERMINATE, "reason_code": SPEC_COMPLIANCE_MODEL_ADVISORY,
                }
        if spec_result.get("status") == "unknown" and ctx.strict_spec_compliance:
            message = (
                "SPEC COMPLIANCE INFRASTRUCTURE FAILURE: authoritative execution cannot "
                f"treat an unavailable compliance judgment as satisfied: {spec_result['reasoning']}"
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        if not spec_result["compliant"] and settled_goal_spec is not None:
            # Correctness Continuity Part A (PRV-06, 2026-08-29): the SAME
            # evidence (requirement text + every checked file's content,
            # byte for byte) already satisfied this exact obligation on an
            # earlier attempt for this subtask - e.g. s2's own planned pass,
            # re-judged again during a later owner-recovery attempt with no
            # relevant content change. A later contradictory JUDGMENT-
            # authority verdict against UNCHANGED evidence must never
            # overturn a settled fact (MA8: DETERMINISTIC > GROUNDED >
            # JUDGMENT; here, even within JUDGMENT itself, unchanged
            # evidence cannot be destabilized by a mere re-ask of the same
            # question). Live incident this closes: byte-identical App.java
            # content passed goal_spec_compliance during s2's own pass, then
            # failed the same check type during s2's owner-recovery,
            # inventing a "protocolVersion field" requirement that appeared
            # nowhere in the goal, plan, or either file.
            deps.logger.warning(
                "Quality Gates: Goal spec compliance returned a contradictory verdict against "
                f"UNCHANGED evidence already SATISFIED at attempt {settled_goal_spec.revision} for "
                f"{settled_goal_spec.id!r} - SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY, suppressed "
                f"(not used to trigger retry): {spec_result.get('reasoning')}"
            )
            spec_result = {
                "compliant": True, "reasoning": spec_result.get("reasoning", ""),
                "missing_requirements": [], "likely_files": [],
                "reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
            }
        arbitrated_contradictions: List[str] = []
        if not spec_result["compliant"]:
            # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
            # SpecComplianceAgent is pure LLM judgment (authority=JUDGMENT) -
            # a missing_requirement whose text correlates to a migration
            # identity the DETERMINISTIC migration gate already reports fully
            # SATISFIED must never drive a retry; DETERMINISTIC always
            # outranks JUDGMENT for the same real-world fact. Found live: "the
            # code still uses Jackson... does not show evidence of replacing
            # the old dependency" fired AFTER the migration was actually
            # complete, and drove 3 further wasted, destabilizing attempts.
            # Only ever suppresses requirements when EVERY current migration
            # obligation is SATISFIED (never touches one while the
            # deterministic check itself still reports a violation - that's
            # not a contradiction, judgment and determinism simply agree).
            kept_requirements, arbitrated_contradictions = deps._spec_requirements_contradicting_authority(
                spec_result["missing_requirements"], ctx.obligation_ledger,
            )
            if arbitrated_contradictions:
                deps.logger.warning(
                    "Quality Gates: Goal spec compliance reported requirement(s) that contradict "
                    "an authoritative deterministic SATISFIED obligation - SPEC_COMPLIANCE_"
                    "CONTRADICTS_AUTHORITY, suppressed (not used to trigger retry): %s",
                    arbitrated_contradictions,
                )
            # PRV-11 (2026-08-30): the other half of build_subtask_goal_
            # text()'s own authority-isolation split - a prompt instruction
            # telling SpecComplianceAgent not to treat a Planned-Implementation
            # -only identifier as a requirement is not a deterministic
            # guarantee (same "do not trust the prompt alone" principle the
            # migration arbitration above already applies), and this model
            # was observed reconstructing its own worked counter-example
            # almost verbatim. Deterministically re-checks each surviving
            # requirement's own identifier token(s) against ctx.goal's two
            # labeled sections directly - never a prompt-only fix.
            kept_requirements, planner_only_requirements = deps._spec_requirements_naming_planner_only_identifiers(
                kept_requirements, ctx.goal,
            )
            if planner_only_requirements:
                deps.logger.warning(
                    "Quality Gates: Goal spec compliance reported requirement(s) naming an "
                    "identifier that appears only in the Planned Implementation Strategy "
                    "section of the goal, never in the Authoritative Goal section - "
                    "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY (planner-only identifier), "
                    "suppressed (not used to trigger retry): %s",
                    planner_only_requirements,
                )
        else:
            kept_requirements = []
            arbitrated_contradictions = []
            planner_only_requirements = []
        if ctx.requirement_set is not None and (arbitrated_contradictions or planner_only_requirements):
            deps._downgrade_suppressed_requirement_claims(
                state, ctx, arbitrated_contradictions + planner_only_requirements,
                goal_spec_evidence_fingerprint,
            )
        if ctx.requirement_set is not None:
            deps._close_requirements_by_migration_gate(state, ctx, goal_spec_evidence_fingerprint)
        model_missing_requirements: List[str] = []
        if not spec_result["compliant"] and kept_requirements:
            # GR-R1B: the verifier reports concrete requirements missing from
            # a candidate every deterministic gate passed. That is the model's
            # word alone: recorded as advisory diagnostic evidence (the
            # requirement verdicts above keep it as MODEL_CLAIMED provenance),
            # never an attempt failure, a retry or a fallback. The terminal
            # requirement gate decides from trusted evidence.
            model_missing_requirements = list(kept_requirements)
            spec_result = {**spec_result, "status": SPEC_MODEL_REPORTED_MISSING,
                           "reason_code": SPEC_COMPLIANCE_MODEL_ADVISORY}
        model_advisory = spec_result.get("status") in (SPEC_MODEL_REPORTED_MISSING, SPEC_MODEL_INDETERMINATE)
        if model_advisory:
            deps.logger.warning(
                "Quality Gates: Goal spec compliance - the model reported %s; advisory only (GR-R1B: a model "
                "verdict fails no attempt and spends no retry): %s",
                "; ".join(model_missing_requirements) or "an indeterminate verdict", spec_result.get("reasoning"),
            )
            state.record_event(RunEvent(
                kind="spec_compliance.model_advisory", attempt=state.attempt_number,
                source="attempt.goal_spec_compliance", authority=EventAuthority.ADVISORY,
                message="The verifier's verdict on this candidate is advisory diagnostic evidence only.",
                details={"status": spec_result["status"], "authority": "advisory_only",
                         "missing_requirements": model_missing_requirements,
                         "reasoning": spec_result.get("reasoning", ""),
                         "evidence_fingerprint": goal_spec_evidence_fingerprint},
            ))
        if goal_spec_obligation_id and ctx.obligation_ledger is not None:
            ctx.obligation_ledger.record(ObligationRecord(
                id=goal_spec_obligation_id, kind=ObligationKind.GOAL_SPEC_REQUIREMENT,
                # A model verdict never settles this either way (GR-R1B).
                status=ObligationStatus.INDETERMINATE if model_advisory else ObligationStatus.SATISFIED,
                authority=ObligationAuthority.JUDGMENT,
                description="goal_spec_compliance verdict for this subtask's checked files",
                source="attempt.run_attempt", revision=state.attempt_number,
                evidence={"fingerprint": goal_spec_evidence_fingerprint,
                          **({"model_missing_requirements": model_missing_requirements}
                             if model_missing_requirements else {})},
                owner_subtask_id=ctx.current_subtask_id, terminal_required=False,
            ))
        # Verifier-availability honesty (2026-09-20): `success: True` here
        # has historically meant two structurally different things - a real
        # PASS verdict, and an infrastructure failure (status="unknown",
        # from SpecComplianceAgent.check()'s own call/parse-exception
        # handler) or a suppressed contradictory verdict
        # (status="indeterminate_suppressed", set just above) that this
        # non-strict path deliberately does not block on. `success: True`
        # is kept unchanged for backward structural compatibility (nothing
        # here weakens ctx.strict_spec_compliance's own existing fail-closed
        # escalation a few lines above, which still raises before reaching
        # this point whenever that flag is set) - but `status` now always
        # says explicitly which case this is, so a genuinely unavailable or
        # suppressed check can never be silently read back as real
        # verification evidence downstream (traces.db's own persisted
        # gate_outcomes, or any future consumer). PASS / UNAVAILABLE /
        # SUPPRESSED are the only values; a real compliant verdict carries
        # no `status` key at all (unchanged from before this fix).
        _spec_status = spec_result.get("status")
        state.record_gate_outcome({
            "attempt": state.attempt_number,
            "type": "goal_spec_compliance",
            "success": True,
            "output": spec_result["reasoning"],
            **({"status": _spec_status} if _spec_status else {}),
            **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                "arbitrated_contradictions": arbitrated_contradictions}
               if arbitrated_contradictions else {}),
            **({"reason_code": "SPEC_COMPLIANCE_CONTRADICTS_AUTHORITY",
                "planner_only_requirements": planner_only_requirements}
               if planner_only_requirements else {}),
            **({"reason_code": spec_result["reason_code"]} if spec_result.get("reason_code") else {}),
            **({"model_missing_requirements": model_missing_requirements} if model_missing_requirements else {}),
        })
        if _spec_status == "unknown":
            deps.logger.info(
                "Quality Gates: Goal spec compliance UNAVAILABLE (the check itself could "
                f"not run - advisory only, not evaluated as verification evidence): {spec_result['reasoning']}"
            )
        elif model_advisory:
            pass  # logged above, with the advisory event
        elif _spec_status == "indeterminate_suppressed":
            deps.logger.info(
                "Quality Gates: Goal spec compliance SUPPRESSED (contradictory verdict "
                f"against deterministically-satisfied authority, not evaluated as verification evidence): {spec_result['reasoning']}"
            )
        else:
            deps.logger.info(f"Quality Gates: Goal spec compliance PASSED: {spec_result['reasoning']}")

    # The isolated candidate passed its inner checks. Terminal full regression
    # and application still remain, so this must never claim overall success.
    state.candidate_gates_succeeded = True
    if state.api_contract_recovery:
        state.api_contract_recovery.candidate_gates_passed()
    state.record_event(RunEvent(
        kind="candidate_gates.passed",
        attempt=state.attempt_number,
        source="workflow",
        authority=EventAuthority.AUTHORITATIVE,
        details={"passed": True, "terminal": False},
    ))
    log_gate_banner(
        "CANDIDATE GATES", "PASSED", state.attempt_number,
        scope=ctx.execution_scope,
    )



async def _run_deferred_executing_gates(
    deps: GateDeps, state: RunState, ctx: AttemptCtx, skipping_candidate_gates: bool, tree_binding: Any, active_code_context: str,
) -> None:
    """The held half of run_attempt, after the source approval: POM validation first (it was ahead of the other gates), then the executing gates."""
    deps.assert_candidate_unchanged_since_source_review(state, ctx)
    deps._run_pom_validation_gate(state, ctx, tree_binding)
    await deps._run_executing_candidate_gates(state, ctx, skipping_candidate_gates, tree_binding, active_code_context)


# ----------------------------------------------------------------------------- E14 (OI-54, OI-50): moved verbatim from
# kriya/workflow/attempt.py - the candidate-gate seams the moved gates (and run_attempt) reach through GateDeps: the
# developer-role wrapper, the environment stop, the test-verification declaration, the process-boundary obligation,
# the two process-boundary test-candidate checks, the test-delta integrity check, the workspace type index and the
# suite judgment. attempt.py keeps a forwarder (or a re-export) for every name; GateDeps keeps binding those.

async def _as_developer(deps: GateDeps, awaitable: Any) -> Any:
    """Await ``awaitable`` with its model calls attributed to the developer
    role (PRD-018): the compile/test self-correction loop is the Developer's
    repair, run outside _run_developer_generation."""
    model_role = deps.load_model_role()

    with model_role("developer"):
        return await awaitable


# Environment outcomes of a tool that DID start but returned no verdict (every other code means it could not start).
_ENVIRONMENT_CODES_THAT_STARTED = frozenset({MAVEN_VALIDATE_TIMED_OUT})


def _stop_on_environment_gate_result(state: RunState, result: Any, gate: str) -> None:
    """GRADLE-WRAPPER-CONTAINMENT-001: a gate result whose verification tool
    could not start carries a typed ``environment_reason_code`` (the
    validator sets it from a structured outcome, never from text). It is
    raised as the existing ``verification_infrastructure_failure``
    (retry_strategy: STOP_ENVIRONMENT - no budget reset, no fallback
    escalation, the environment banner), never as a compile/test failure of
    the candidate: nothing about the candidate was observed."""
    code = result.get("environment_reason_code") if isinstance(result, dict) else None
    if not code:
        return
    output = str(result.get("output") or "")
    what = "did not finish" if code in _ENVIRONMENT_CODES_THAT_STARTED else "could not start"
    failure = Failure(
        type="verification_infrastructure_failure",
        message=(f"VERIFICATION_INFRASTRUCTURE_FAILURE: the {gate} gate's verification tool {what} "
                 f"({code}); nothing about the candidate was observed, so this is not a code failure.\n{output}"),
        raw_output=output, source="orchestrator", attempt=state.attempt_number,
        diagnostics={"reason_code": code, "gate": gate, "gradle_distribution": result.get("gradle_distribution")},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


_TEST_VERIFIERS = frozenset({"test", "tests", "regression"})


def _declares_test_verification(required_verification: Iterable[Dict[str, Any]]) -> bool:
    """The stage's declared verification includes a test-suite verifier."""
    return any(item.get("type") == "tool" and item.get("tool_name") in _TEST_VERIFIERS
               for item in required_verification or ())


def _process_boundary_obligation_id(subtask_id: str) -> str:
    return f"attempt.subtask.{subtask_id}.process_boundary_compatibility"


_PROCESS_BOUNDARY_RECURRENCE_ESCALATION = (
    "\n\nRECURRING FAILURE: this exact process-boundary obligation was already recorded "
    "VIOLATED on an earlier attempt for this subtask and is still unresolved. Toggling the "
    "same call between two states again (e.g. undoing a previous attempt's fix) will not "
    "resolve it - propose the structural separation (or out-of-process verification) "
    "described above instead of repeating a change already tried."
)


def _record_process_boundary_obligation(
    deps: GateDeps,
    ctx: AttemptCtx, state: RunState, *, violated: bool, evidence: Dict[str, Any],
) -> bool:
    """PRV-06 (2026-08-28): records ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY
    when a test_process_terminated failure fires (violated=True), and clears
    it back to SATISFIED the next time this subtask's test gate passes
    (violated=False) - the same VIOLATED->SATISFIED tracking shape already
    established for PLAN_STRUCTURAL_VALIDITY, giving this failure class real
    cross-attempt identity instead of looking like a fresh, unrelated defect
    every retry.

    Returns True when violated=True and this exact obligation was ALREADY on
    record as VIOLATED before this call - i.e. the SAME process-boundary
    conflict recurring on a later attempt for the SAME subtask, never a
    different subtask or an earlier-but-since-SATISFIED occurrence (prior
    must itself be VIOLATED, not merely "violated at some point in this
    obligation's history" - a subtask that fixed this and later broke it a
    different way starts a fresh, non-recurrent record; recurrence means
    "still unresolved between two consecutive checks," not "has a violated
    entry somewhere in its past"). Callers use this to escalate the next
    repair message with _PROCESS_BOUNDARY_RECURRENCE_ESCALATION instead of
    silently repeating the same generic guidance every attempt - this is
    what actually closes the oscillation PRV-06 hit live (the Developer
    flipped System.exit <-> return for 11 attempts because nothing
    distinguished "first occurrence" from "still unresolved"; recording the
    obligation alone would only have made that observable in the ledger, not
    prevented it). Recurrence is also written into the RECORD's own
    `evidence` (recurrence/prior_violation_attempt/current_attempt) - a
    durable control-plane fact inspectable from the ledger itself, not just
    baked into a transient message string; the escalation text is only how
    that fact gets exposed to the Developer, not the fact's only home.

    Deliberately scoped to a structured/enforce subtask only
    (ctx.current_subtask_id is the natural revision-tracked owner here, same
    reasoning as every other owner_subtask_id in this module family) - a
    plain Legacy run has no subtask id to anchor cross-attempt identity on
    and doesn't consume obligation tracking today either. A SATISFIED record
    is only written when this exact obligation was already VIOLATED for this
    subtask, so an ordinary subtask that never hit this failure never
    accumulates a needless record. Different subtasks get different
    obligation ids (the subtask id is baked into the id itself) and one
    ObligationLedger is scoped to one workflow run (see its own class
    docstring) - recurrence can never fire across an unrelated subtask or an
    unrelated run."""
    if ctx.obligation_ledger is None or not ctx.current_subtask_id:
        return False
    obligation_id = deps._process_boundary_obligation_id(ctx.current_subtask_id)
    prior = ctx.obligation_ledger.current(obligation_id)
    if not violated and prior is None:
        return False
    is_recurrence = violated and prior is not None and prior.status == ObligationStatus.VIOLATED
    record_evidence = dict(evidence)
    if violated:
        record_evidence["recurrence"] = is_recurrence
        record_evidence["current_attempt"] = state.attempt_number
        if is_recurrence:
            record_evidence["prior_violation_attempt"] = prior.revision
    record = ObligationRecord(
        id=obligation_id, kind=ObligationKind.PROCESS_BOUNDARY_COMPATIBILITY,
        status=ObligationStatus.VIOLATED if violated else ObligationStatus.SATISFIED,
        authority=ObligationAuthority.DETERMINISTIC,
        description="subtask's test execution must not trigger process/fork termination "
                    "in already-written production code invoked in-process by a test",
        source="attempt.run_attempt", revision=state.attempt_number,
        evidence=record_evidence, owner_subtask_id=ctx.current_subtask_id, terminal_required=False,
    )
    ctx.obligation_ledger.record(record)
    # MA9 (2026-08-29): a VIOLATED record is the trigger to (re)derive a
    # coordinated RepairContract; a SATISFIED record closes whichever
    # contract this exact obligation opened, if any - see
    # kriya/workflow/repair_contract.py's own module docstring. Neither
    # branch does anything when build_repair_contract()/evidence extraction
    # can't unambiguously classify this - state.repair_contract simply stays
    # whatever it already was (None for every run that never hits this).
    if violated:
        deps._sync_active_repair_contract(ctx, state, record)
    elif state.repair_contract is not None and obligation_id in state.repair_contract.source_obligation_ids:
        state.repair_contract.status = RepairContractStatus.SATISFIED
        state.record_event(RunEvent(
            kind="repair_contract_satisfied", attempt=state.attempt_number, source="attempt.run_attempt",
            authority=EventAuthority.ADVISORY,
            message=f"RepairContract '{state.repair_contract.id}' satisfied - originating obligation resolved.",
            details={"repair_contract_id": state.repair_contract.id, "obligation_id": obligation_id},
        ))
    return is_recurrence


def _raise_unsafe_process_boundary_test_candidate(
    deps: GateDeps,
    state: RunState, ctx: AttemptCtx, test_files: List[str],
) -> None:
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    java_files = [path for path in known_files if path.endswith(".java")]
    entrypoints = sorted(set(deps._build_java_main_class_map(java_files, ctx).values()))
    findings = deps.find_in_process_terminating_test_invocations(
        deps._runtime_contract_requirements(ctx), ctx.worktree_path, test_files, entrypoints,
    )
    if not findings:
        return
    offending_files = sorted({item["test_file"] for item in findings})
    detail_lines = [
        f"- {item['test_file']}:{item['line']}"
        + (f" method={item['test_method']}" if item.get("test_method") else "")
        + f" invokes {item['entrypoint_call']} for {item['terminating_case']}"
        for item in findings
    ]
    message = (
        "PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS: generated test code invokes an "
        "application entrypoint in the test runner process for behavior the structured "
        "runtime/process-boundary contract requires to terminate with a non-zero exit.\n"
        + "\n".join(detail_lines)
        + "\nPreserve the product's required process-terminating behavior. Repair the TEST only: "
        "remove the in-process case or execute it through a child process and assert exit "
        "code/stdout/stderr. The application_runtime verifier remains responsible for the "
        "required exit-code behavior."
    )
    failure = Failure(
        type="process_terminating_behavior_tested_in_process",
        message=message,
        raw_output=message,
        file_locations=[
            FileLocation(filepath=item["test_file"], line=item["line"])
            for item in findings
        ],
        likely_files=offending_files,
        failed_content=_capture_failed_content(ctx.worktree_path, offending_files),
        diagnostics={
            "reason_code": "PROCESS_TERMINATING_BEHAVIOR_TESTED_IN_PROCESS",
            "repair_owner": "test",
            "findings": findings,
        },
        attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _raise_unexecuted_test_delta(
    deps: GateDeps,
    state: RunState, ctx: AttemptCtx, validator: Any,
    accepted_result: Optional[Dict[str, Any]], selected_test: Optional[str],
) -> None:
    """FS-1A test-execution integrity for a subtask explicitly responsible
    for tests (kriya/workflow/test_delta.py): its changed test sources must
    have produced test identities that executed and passed in the accepted
    gate invocation's own structured report. When that invocation did not
    run every changed test source (a single targeted file) or carries no
    structured report (a self-corrected result), one covering run supplies
    the evidence. TEST_NOT_EXECUTED / TEST_NOT_PASSED is a repairable
    test-acceptance failure naming the identities; INDETERMINATE evidence is
    a typed stop, never a pass."""
    changed = deps._changed_test_sources(state, ctx)
    if not changed:
        return
    covered = selected_test is None or set(changed) == {selected_test}
    result = accepted_result
    if not covered or deps.test_execution.report_from_result(result) is None:
        targeted = validator.stack == "python"
        result = validator.run_tests(target_test=sorted(changed)) if targeted else validator.run_tests()
        deps._stop_on_environment_gate_result(state, result, "test")
        # A targeted run executes only the changed tests - its failures are the candidate's own; a full-suite
        # run is attributed against the frozen baseline (CANDIDATE-GATE-BASELINE-POLICY-001).
        blocking, evidence, extra = ((not result.get("success"), result.get("output", ""), {}) if targeted
                                     else deps._judge_candidate_suite(state, ctx, result, deps.SCOPE_TEST_DELTA_COVERING_RUN))
        if blocking:
            failure = _build_test_quality_gate_failure(
                "test", f"TEST FAILURE:\n{evidence}",
                evidence, ctx.worktree_path, state.all_files_written, state.attempt_number,
            )
            failure.diagnostics = {**(failure.diagnostics or {}), **extra}
            state.record_gate_outcome({**failure.to_gate_outcome(), **extra})
            raise QualityGateFailure(failure)
    report = deps.test_execution.report_from_result(result)
    verdict = judge_test_delta(changed, report)
    state.record_gate_outcome({
        "attempt": state.attempt_number, "type": "test_delta", "success": verdict.satisfied,
        "reason_code": verdict.reason_code, "test_delta": verdict.to_dict(),
        "test_execution": report.summary() if report is not None else None,
    })
    if verdict.satisfied:
        return
    if verdict.reason_code == TEST_EXECUTION_EVIDENCE_INDETERMINATE:
        failure = Failure(
            type="verification_infrastructure_failure",
            message=f"{TEST_EXECUTION_EVIDENCE_INDETERMINATE}: {verdict.detail}",
            raw_output=verdict.detail, likely_files=sorted(changed), attempt=state.attempt_number,
            diagnostics={"reason_code": verdict.reason_code, "test_delta": verdict.to_dict()},
        )
    else:
        failure = Failure(
            type="test_acceptance",
            message=(
                f"TEST ACCEPTANCE FAILURE ({verdict.reason_code}): the goal requires tests, and the "
                f"changed test code must run as tests and pass. {verdict.detail}. A test the runner "
                "does not execute (for example a method without its test annotation, or a function "
                "the runner does not collect) verifies nothing."
            ),
            raw_output=verdict.detail, likely_files=sorted(changed), attempt=state.attempt_number,
            diagnostics={"reason_code": verdict.reason_code, "test_delta": verdict.to_dict()},
        )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _raise_ungrounded_child_process_test_candidate(
    deps: GateDeps,
    state: RunState, ctx: AttemptCtx, test_files: List[str],
) -> None:
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    java_files = [path for path in known_files if path.endswith(".java")]
    findings = deps.find_ungrounded_java_child_process_tests(
        deps._runtime_contract_requirements(ctx), ctx.worktree_path, test_files,
        deps._build_java_main_class_map(java_files, ctx), known_files,
    )
    if not findings:
        return
    offending = sorted({item["test_file"] for item in findings})
    details = "\n".join(
        f"- {item['test_file']}: " + "; ".join(item["reasons"])
        for item in findings
    )
    exemplar = findings[0]
    message = (
        "VERIFICATION_INFRASTRUCTURE_FAILURE: generated child-process verification "
        "does not use Kriya's grounded application launch.\n" + details
        + "\nRepair the TEST/verification mechanism only; preserve application behavior. "
        f"Use main class {exemplar['entrypoint']} with classpath "
        f"{exemplar['classes_dir']}, launch a distinct child, wait for it, and capture "
        "stdout/stderr/exit code. A child launch/setup failure is not evidence against "
        "the application source."
    )
    failure = Failure(
        type="test_verification_infrastructure_failure",
        message=message, raw_output=message,
        likely_files=offending, attempt=state.attempt_number,
        diagnostics={
            "reason_code": "UNGROUNDED_CHILD_PROCESS_LAUNCH",
            "grounded_entrypoint": exemplar["entrypoint"],
            "grounded_classpath": exemplar["classes_dir"],
        },
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


def _build_workspace_type_index(deps: GateDeps, state: RunState, ctx: AttemptCtx) -> Dict[str, List[str]]:
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
    db_path = deps._dependency_graph_db_path(ctx)
    try:
        DependencyGraph = deps.load_dependency_graph()
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
            for name in deps._extract_class_names_best_effort(db_path, written_path, written_content):
                paths = index.setdefault(name, [])
                if written_path not in paths:
                    paths.append(written_path)
        return index
    except Exception as e:
        deps.logger.debug(f"_build_workspace_type_index: skipping duplicate-type check for this attempt: {e}")
        return {}


def _judge_candidate_suite(
    deps: GateDeps,
    state: RunState, ctx: AttemptCtx, result: Dict[str, Any], scope: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    """(blocking, Developer-facing evidence, gate-outcome evidence) for one
    full-suite run: attributed against the frozen baseline when one was
    captured, else the raw ``success`` rule exactly as before."""
    attribution = deps._attribute_candidate_suite(state, ctx, result, scope)
    if attribution is None:
        return not result["success"], result.get("output", ""), {}
    evidence = attribution.evidence_text(state.validation_baseline_full_regression, result.get("output", ""),
                                         deps.test_execution.failure_details(result))
    return attribution.blocking, evidence, {"regression_attribution": attribution.gate_evidence()}
