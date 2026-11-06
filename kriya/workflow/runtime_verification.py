"""Runtime verification and managed-service verification of one attempt (full modularization W4, E9).

Moved verbatim from ``kriya/workflow/attempt.py``:

- the approve-and-execute half of the candidate gates' runtime verification
  (``_approve_and_execute_runtime_verification``: the runtime-command review, the execution of exactly the reviewed
  commands, the grading of the captured output, the bounded self-correction and its stdin-bound re-run, the recorded
  outcome) and the verification-only unit's direct execution (``_execute_runtime_verification_directly``);
- the managed-service verification (``_execute_managed_service_verification``) and its admission checks
  (``_validate_and_convert_managed_service_contract``, ``_validate_and_convert_readiness``/``_probe``,
  ``_reject_non_local_managed_service_host``, ``_looks_like_shell_compound_command``,
  ``_managed_service_blocked_without_approval_subject``, ``_resolve_execution_mode``);
- the finite-command artifact preparation (``_prepare_finite_command_runtime_artifacts``);
- the runtime-command approval helpers (``_runtime_verification_is_advisory_only``, ``_derive_runtime_command_subject``,
  ``_runtime_commands_need_approval``, ``_approve_runtime_commands``, ``_required_runtime_verification_missing_message``,
  ``_raise_if_required_runtime_verification_declined``);
- the runtime contract and grading helpers (``_apply_runtime_verification_contract``,
  ``_classify_grounded_contract_verdict``, ``_resolve_runtime_verification_grade``,
  ``_deterministic_result_provenance_field``, ``_raise_runtime_verification_infrastructure_failure``,
  ``_raise_candidate_runtime_entrypoint_failure``, ``_run_verification_basis_hash``) and the Python runtime grounding
  (``_collect_python_runtime_grounding_facts``, ``_build_python_runtime_grounding``);
- their constants (the synthetic runtime input, the distrust notice, the managed-service shapes and kinds,
  ``MANAGED_SERVICE_APPROVAL_UNSUPPORTED``).

Every approval, containment, deadline, stdin-binding and refusal decision is unchanged, in the same order. Not here:
the runtime judgment's caller (``gate_orchestrator._run_executing_candidate_gates`` and the verification-only attempt),
the process-boundary test-candidate checks and the helpers they share with this code (``_build_java_main_class_map``,
``_runtime_contract_requirements``, ``_runtime_contract_expects_numeric_input``: they stay in attempt.py), the two
managed-service outcome sets built from ``kriya.tools.service_runtime``'s enum (attempt.py keeps them; that module is not
on EXECUTION's public surface) and the Python grounding's excluded directories (they name ``.kriya``, a direct-access
site the A2 tripwire counts on attempt.py; moving it would split that allowlist row).

``kriya/workflow/attempt.py`` keeps every one of these names (a forwarder, or a re-export of a constant) so a test patch
on ``kriya.workflow.attempt`` keeps intercepting the moved code. What arrives in ``RuntimeDeps``, resolved through
``kriya.workflow.attempt`` when a forwarder runs: every name the bodies looked up that attempt.py defines (its helpers,
the moved siblings' forwarders), every name tests patch on ``kriya.workflow.attempt``
(``tests/architecture/test_patch_points.py::PATCH_POINTS``: ``run_managed_service_verification``, ``_resolve_run_command``,
``_check_java_toolchain_mismatch``, ``_resolve_java_home_override``, ``time``, the review, preparation, admission and
managed-service seams), attempt.py's ``logger``, the names of the kernel's all-import cycle (``verifier_evidence``,
``skill_extraction``, ``context_budget.request_capacity``; ``core.llm``, ``self_correction`` and ``skills.skill``
through loaders called where the bodies imported them) and ``kriya.tools.service_runtime``'s names. Names of modules
outside that cycle that never reach this module and that no test patches on ``kriya.workflow.attempt`` are imported here
directly (``kriya.tools.validate`` and ``kriya.tools.process`` are in their own EXECUTION cycles, which do not reach this
module). This module never imports ``kriya.workflow.attempt``, ``workflow``, ``workflow_controller`` or ``kriya.cli``,
nor any all-import-cycle module.

``RunState`` is the attempt's ``kriya.workflow.state.GenerationState``, ``AttemptCtx`` its
``kriya.workflow.attempt.AttemptContext`` and ``Validator`` its ``kriya.tools.validate.PolymorphicValidator``; all are
spelled ``Any`` (``kriya/workflow/stage_context.py`` says why the state module is not imported; AttemptContext lives in
attempt.py, OI-46; tests patch the validator class at its source).
"""
import dataclasses
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from kriya.config.authority import is_human_in_the_loop
from kriya.policy.approval import (
    MAX_REVIEW_ROUNDS,
    RuntimeCommandSubject,
    TypedApprovalCallback,
    approval_matches,
    commands_authored_in_goal,
)
from kriya.policy.approval import ask as ask_runtime_commands
from kriya.policy.approval import build_subject as build_runtime_subject
from kriya.policy.filesystem import WriteScopeMode, is_within_scope, make_workspace_scope
from kriya.tools.process import ProcessController
from kriya.tools.validate import RUNTIME_VERIFICATION_GATE, execution_evidence, get_pom_dependencies
from kriya.workflow.acceptance import (
    CANDIDATE_RUNTIME_ENTRYPOINT_INVALID,
    classify_runtime_entrypoint,
    runtime_verification_infrastructure_reason,
)
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.failure_grounding import (
    _build_quality_gate_failure,
    classify_environment_failure,
    extract_missing_project_local_python_module,
)
from kriya.workflow.file_resolution import (
    declared_and_ambiguous_member_files,
    downgrade_ungrounded_goal_explicit_commands,
    extract_jvm_module_flags,
    ground_java_entrypoint_in_no_build_file_projects,
    ground_python_runtime_target,
    python_command_targets_test_path,
    python_file_is_runnable_script,
    python_target_path_is_test_shaped,
)
from kriya.workflow.run_events import EventAuthority, RunEvent
from kriya.workflow.verification_authority import deterministic_sequence_kind, deterministic_verification_kind
from kriya.workflow.verification_contract import ContractVerdictState, classify_contract_verdict
from kriya.workflow.worktree import (
    candidate_has_canonical_membership,
    candidate_member_files,
    clean_untracked_files_since,
    snapshot_untracked_files,
)

RunState = Any    # kriya.workflow.state.GenerationState
AttemptCtx = Any  # kriya.workflow.attempt.AttemptContext
Validator = Any   # kriya.tools.validate.PolymorphicValidator


@dataclass(frozen=True)
class RuntimeDeps:
    """What the moved runtime-verification code may use, resolved through ``kriya.workflow.attempt`` by
    ``attempt._runtime_deps()`` when a forwarder runs. Every field is required: a builder that omits one fails at
    construction, never at the first use."""

    # attempt.py's logger and clock (a patch of kriya.workflow.attempt.time reaches the runtime timings)
    logger: Any
    time: Any
    # names tests patch on kriya.workflow.attempt (PATCH_POINTS)
    run_managed_service_verification: Callable[..., Any]
    _resolve_run_command: Callable[..., Any]
    _check_java_toolchain_mismatch: Callable[..., Any]
    _resolve_java_home_override: Callable[..., Any]
    _approve_runtime_commands: Callable[..., Any]
    _prepare_finite_command_runtime_artifacts: Callable[..., Any]
    _validate_and_convert_managed_service_contract: Callable[..., Any]
    _execute_managed_service_verification: Callable[..., Any]
    # kriya.tools.service_runtime (not on EXECUTION's public surface; attempt.py's import is allowlisted) and the two
    # outcome sets attempt.py builds from its enum
    ManagedServiceVerificationSpec: Any
    ProbeSpec: Any
    ReadinessSpec: Any
    ServiceVerificationOutcomeKind: Any
    _prepare_required_artifact: Callable[..., Any]
    _MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES: FrozenSet[Any]
    _MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_OUTPUT: FrozenSet[Any]
    # the Python grounding's excluded directories (they name '.kriya': the kriya_dir direct-access site stays counted
    # on attempt.py, where tests/architecture/test_direct_access_owners.py allowlists it)
    _PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS: Any
    # the kernel's all-import cycle: verifier_evidence, skill_extraction, context_budget, and the loaders of the bodies'
    # function-local core.llm / self_correction / skills.skill imports
    RetainedRuntimeEvidence: Any
    apply_runtime_disposition: Callable[..., Any]
    runtime_evidence_outcome_fields: Callable[..., Any]
    _skill_verification_context: Callable[..., Any]
    request_capacity: Callable[..., Any]
    load_is_local_url: Callable[[], Any]
    load_run_self_correction_loop: Callable[[], Any]
    load_held_skill_verification: Callable[[], Any]
    # attempt.py's own names that stay there (shared with code that stays, or moved by E8 to gate_orchestrator)
    _apply_candidate_pom_corrections: Callable[..., Any]
    _as_developer: Callable[..., Any]
    _bind_verification_tree: Callable[..., Any]
    _build_java_main_class_map: Callable[..., Any]
    _record_self_correction_scope_conflict: Callable[..., Any]
    _stop_on_environment_gate_result: Callable[..., Any]
    assert_candidate_unchanged_since_source_review: Callable[..., Any]
    exit_authority_text: Callable[..., Any]
    # the moved siblings, through their attempt.py forwarders
    _apply_runtime_verification_contract: Callable[..., Any]
    _build_python_runtime_grounding: Callable[..., Any]
    _classify_grounded_contract_verdict: Callable[..., Any]
    _collect_python_runtime_grounding_facts: Callable[..., Any]
    _derive_runtime_command_subject: Callable[..., Any]
    _deterministic_result_provenance_field: Callable[..., Any]
    _looks_like_shell_compound_command: Callable[..., Any]
    _managed_service_blocked_without_approval_subject: Callable[..., Any]
    _raise_candidate_runtime_entrypoint_failure: Callable[..., Any]
    _raise_if_required_runtime_verification_declined: Callable[..., Any]
    _raise_runtime_verification_infrastructure_failure: Callable[..., Any]
    _reject_non_local_managed_service_host: Callable[..., Any]
    _required_runtime_verification_missing_message: Callable[..., Any]
    _resolve_execution_mode: Callable[..., Any]
    _resolve_runtime_verification_grade: Callable[..., Any]
    _run_verification_basis_hash: Callable[..., Any]
    _runtime_commands_need_approval: Callable[..., Any]
    _runtime_verification_is_advisory_only: Callable[..., Any]
    _validate_and_convert_probe: Callable[..., Any]
    _validate_and_convert_readiness: Callable[..., Any]


def _classify_grounded_contract_verdict(
    deps: RuntimeDeps, output: str, worktree_path: str, files_written: List[str]
) -> Tuple[ContractVerdictState, Optional[Dict[str, Any]]]:
    """IO wrapper around verification_contract.classify_contract_verdict()
    (which stays pure/file-content-free, matching its own established
    convention) - reads the written files from worktree_path, then
    classifies. VER-006 (2026-09-10): this used to be
    _extract_grounded_contract_verdict(), returning a bare Optional[Dict]
    that collapsed two structurally different situations into the same
    `None` - "no marker exists at all" (ABSENT) and "a marker exists and was
    deterministically rejected as ungrounded" (INDETERMINATE_DISTRUSTED) -
    both of which then received identical, fully-trusted LLM grading. See
    ContractVerdictState's own docstring for the live incident (run
    `bpwsqscrg`) that exploited exactly this collapse. A single shared
    helper, not seven copies of the same logic - used identically at every
    run_res-outcome branch in both run_attempt() and
    _execute_runtime_verification_directly() (clean run / timed out / plain
    nonzero exit / post-self-correction re-verification) via
    _resolve_runtime_verification_grade() below, so classification is
    consistent everywhere, not just wherever someone happened to add it
    first."""
    files_content = []
    for filepath in files_written:
        full_path = os.path.join(worktree_path, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                files_content.append(f.read())
        except Exception:
            continue
    state, verdict = classify_contract_verdict(output, files_content)
    if state is ContractVerdictState.INDETERMINATE_DISTRUSTED:
        deps.logger.info(
            "Runtime verification: a deterministic '[VERIFICATION] PASS' marker was found, but "
            "none of the written files contain a '[VERIFICATION] FAIL' string anywhere - the "
            "check doesn't look like it actually branches on anything. Not trusting it; falling "
            "back to LLM grading instead, disclosed as distrusted (VER-006) - it cannot become "
            "a terminal PASS on that evidence alone."
        )
    return state, verdict


_DISTRUST_DISCLOSURE_NOTE = (
    "A deterministic check already inspected the captured output above and found a "
    "self-reported '[VERIFICATION] PASS' marker, but rejected it as ungrounded: none of the "
    "written files contain a '[VERIFICATION] FAIL' string anywhere, meaning this marker does "
    "not appear to be gated behind any real comparison and could have been printed "
    "unconditionally regardless of outcome. Do not cite this marker, by itself, as evidence "
    "the goal was achieved. Evaluate only evidence independent of that marker. If no such "
    "independent evidence exists in the captured output, you must return passed: false and "
    "say so explicitly - a distrusted marker does not become trustworthy because it is "
    "reinterpreted."
)


async def _resolve_runtime_verification_grade(
    deps: RuntimeDeps, ctx: AttemptCtx, state: ContractVerdictState,
    contract_verdict: Optional[Dict[str, Any]], grade_kwargs: Dict[str, Any],
    *, run_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    """VER-006 containment (2026-09-10) - the single place every run_res-
    outcome branch (clean run / timed out / plain nonzero exit / post-self-
    correction re-verification, in both run_attempt() and
    _execute_runtime_verification_directly()) must route through once
    _classify_grounded_contract_verdict() has produced a state, so
    'deterministically distrusted evidence cannot become terminal PASS via
    LLM judgment over that same evidence' is enforced identically
    everywhere rather than in seven hand-rolled copies that could drift out
    of sync. Returns (grade, verification_authority) - grade is always a
    {"passed", "reasoning", "likely_files"}-shaped dict, matching every
    existing call site's expectations exactly.

    PASS/FAIL: today's existing fast path, completely unchanged - the
    grounded deterministic verdict IS the grade, grade() is never called.

    ABSENT: today's existing LLM-fallback behavior, completely unchanged -
    no deterministic contract evidence exists at all, so nothing here
    should (or safely can) second-guess ctx.run_verifier.grade()'s own
    authority over the raw captured output.

    INDETERMINATE_DISTRUSTED: grade() may still be called - its reasoning/
    likely_files remain useful diagnostic value for the retry loop, and
    Invariant 5 (LLM grading may remain advisory where no stronger
    contradictory/distrust evidence exists) does not forbid an advisory
    call, only an AUTHORITATIVE one - but it is called with the distrust
    disclosed as a separate, trusted (non-fenced) prompt section, never
    folded into the untrusted captured-output text. Whatever it returns,
    `passed` is force-set to False before it ever reaches a caller: this
    verification path's only evidence channel is the same captured stdout/
    stderr the deterministic check already distrusted (run_app_sequence()
    captures nothing else), so there is no existing independent
    corroboration source this function could check instead (VER-006 Task 2
    explicitly forbids inventing one) - prompt wording alone is
    acknowledged as insufficient enforcement (a grader could still ignore
    the notice and hallucinate a PASS anyway), so the override here is
    unconditional on the returned `passed` value, not merely a check that
    the notice was heeded."""
    if state in (ContractVerdictState.PASS, ContractVerdictState.FAIL):
        return contract_verdict, "contract"

    # PRD-025: the grader gets a bounded package built from the per-step
    # retained capture, scanned once here - only when a grader call happens.
    if run_result is not None:
        grade_kwargs = {**grade_kwargs, "evidence": deps.RetainedRuntimeEvidence.from_run_result(run_result)}
    if state is ContractVerdictState.ABSENT:
        grade = await ctx.run_verifier.grade(**grade_kwargs)
        return grade, "llm"

    # INDETERMINATE_DISTRUSTED.
    disclosed_kwargs = dict(grade_kwargs)
    disclosed_kwargs["distrust_notice"] = _DISTRUST_DISCLOSURE_NOTE
    try:
        grade = await ctx.run_verifier.grade(**disclosed_kwargs)
    except Exception as e:
        deps.logger.warning(f"Run Verifier grade() call failed during distrusted-evidence disclosure: {e}")
        grade = {"passed": False, "reasoning": f"Grader call failed: {e}", "likely_files": []}
    if grade.get("passed"):
        deps.logger.info(
            "VER-006: LLM grader returned passed=true over deterministically distrusted "
            "evidence with no independent corroboration available on this verification path - "
            "overriding to non-PASS regardless. Grader's own (overridden) reasoning: %s",
            grade.get("reasoning", ""),
        )
    # Mutate in place, matching every other branch in this module (e.g. the
    # timed-out branch's own grade["reasoning"]=.../grade["passed"]=False
    # above). ctx.run_verifier.grade() always returns a freshly-constructed
    # dict in production (RunVerifierAgent.grade()'s own implementation), so
    # there is no shared-mutable-state risk to guard against by copying. Test
    # doubles must return a real dict too: on a bare AsyncMock this
    # assignment is only recorded, and the truthy grade.get("passed") read
    # later turned this forced non-PASS into a PASS (two cleanup tests passed
    # that way until their doubles were made strict).
    grade["reasoning"] = (
        "A deterministic check found a self-reported verification marker but rejected it as "
        "ungrounded (present but never demonstrated to gate on anything). No independent "
        "corroborating evidence exists on this verification path, so this cannot pass on LLM "
        f"judgment over that same distrusted evidence alone. Grader's own verdict: "
        f"{grade.get('reasoning', '')}"
    )
    grade["passed"] = False
    return grade, "llm_over_distrusted_evidence"


def _deterministic_result_provenance_field(verification_authority: str, passed: bool) -> Optional[str]:
    """The ``deterministic_result`` projection of a runtime-verification gate outcome (VER-006 provenance, completed by WAVE8 OI-56).

    ONE authority, multiple projections: ``passed`` is the AUTHORITATIVE verdict of the gate (the grade that decides ``success``), and
    the projection is derived from it - never a second authority. Before OI-56 the process-exit authority projected ``"PASS"`` whatever the
    verdict, so a FAILED deterministic test/build outcome was recorded (and shown by the GUI Inspector) as ``deterministic_result: "PASS"``
    next to ``success: false``.

    ``process_exit`` (the exit status of a deterministic test/compile command sequence IS the verdict): ``"PASS"`` only when ``passed is True``,
    otherwise ``"FAIL"`` (fail-closed: anything that is not an explicit True never reads PASS). ``llm_over_distrusted_evidence`` is its own
    ``"DISTRUSTED"`` (a marker existed and was rejected, then forced non-PASS). Every other authority (ordinary ``llm`` = ABSENT, ``contract``)
    projects ``None``: no deterministic verdict exists. NOT_RUN / NOT_APPLICABLE / BLOCKED record no such outcome at all, so they can never
    collapse into PASS or FAIL here."""
    if verification_authority == "process_exit":
        return "PASS" if passed is True else "FAIL"
    if verification_authority == "llm_over_distrusted_evidence":
        return "DISTRUSTED"
    return None


_RUNTIME_VERIFICATION_SYNTHETIC_INPUT = "kriya-verification-input"


def _prepare_finite_command_runtime_artifacts(
    deps: RuntimeDeps, resolved_run_commands: List[List[str]],
    validator: Validator,
    ctx: AttemptCtx,
    state: RunState,
) -> None:
    """finite_command artifact preparation (2026-09-11) - the finite_command
    counterpart to Managed Runtime Verification's own P6 fix
    (kriya/tools/service_runtime.py's _prepare_required_artifact). Real live
    incident this closes: a `java -jar target/artemis-demo-1.0-SNAPSHOT.jar`
    finite_command failed with "Unable to access jarfile" on every attempt,
    because neither run_compile_check() (`mvn clean compile`) nor run_tests()
    (`mvn test`) ever reaches Maven's `package` phase - only managed_service
    verification had ever run `mvn package` before this. Reuses the EXACT
    SAME detection/staleness/build primitive managed_service already uses
    (never a second Maven-specific implementation) - only the caller and the
    failure-shape mapping below are new.

    Called for every command in the sequence, not just the last - REQUIRED
    BEHAVIOR 3/4: `_prepare_required_artifact` is itself a fast no-op for any
    command that doesn't reference a `-jar`/single-jar `-cp` artifact, or
    whose artifact already exists and is current relative to pom.xml/src -
    this function never runs `mvn package` unconditionally.

    Threads the validator's own JAVA_HOME/sandbox policy
    (build_subprocess_env_and_preexec()) into the SAME preparation call, so
    `mvn package` never silently runs under a different JDK than the one
    the compile gate already validated against.

    Failure-shape mapping (REQUIRED BEHAVIOR 5/6): a build command that
    actually ran and failed (non-zero exit/timeout) - real evidence about
    THIS project's own pom.xml/content - is raised as an ordinary,
    repair-eligible "compile" Quality Gate failure, exactly like any other
    compile failure; a build that reported success but still didn't produce
    the expected artifact gets its own distinct, also repair-eligible
    "package_preparation_failed" type (neither of these ever sets
    state.environment_failure - see retry_strategy.py's STOP_ENVIRONMENT
    type-set, which deliberately does not include either). Only a genuine
    inability to even ATTEMPT a build - no pom.xml, or the build command
    itself could not be invoked (e.g. 'mvn' missing from PATH) - remains
    "verification_infrastructure_failure", the existing, correct
    environment/toolchain-eligible classification."""
    # D2 S4 / S8: the build runs through the validator's contained Maven path (never the host, never the project's ./mvnw); contained is the only mode.
    contained_build = validator.contained_maven_build
    controller = ProcessController()
    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
    for command in resolved_run_commands:
        outcome = deps._prepare_required_artifact(
            command, validator.workspace_path, controller=controller,  # OI-142: in the session's view, where the run consumes it
            contained_build=contained_build,
        )
        if outcome.outcome is None:
            continue
        output = f"stdout:\n{outcome.stdout}\n\nstderr:\n{outcome.stderr}"
        build_command_desc = " ".join(outcome.command) if outcome.command else "(none invoked)"

        if outcome.outcome == deps.ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED:
            message = (
                f"PACKAGE_PREPARATION_FAILED: {outcome.reasoning} This means either the "
                "run command's artifact path/name doesn't match what this project's "
                "pom.xml (artifactId/version/finalName) actually produces, or a "
                "packaging plugin configuration issue silently produced no output there "
                "- not an environment/toolchain problem (the build itself exited "
                f"successfully).\n\nBuild command: {build_command_desc}\n\n{output}"
            )
            failure = _build_quality_gate_failure(
                type_="package_preparation_failed", message=message, raw_output=output,
                worktree_path=ctx.worktree_path, known_files=known_files,
                attempt=state.attempt_number, extra_likely_files=["pom.xml"],
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        # PREPARATION_FAILED - distinguish "the build command actually ran
        # and failed" (command+returncode both set - ProcessController.run()
        # never leaves returncode as None, see its own RunResult) from
        # "never got that far" (no pom.xml, or the invocation itself raised
        # before producing any process result - command may be set but
        # returncode stays the dataclass default None either way).
        if outcome.command is not None and outcome.returncode is not None:
            message = (
                f"PACKAGE_PREPARATION_FAILED: {outcome.reasoning}\n\n"
                f"Build command: {build_command_desc}\n\n{output}"
            )
            failure = _build_quality_gate_failure(
                type_="compile", message=message, raw_output=output,
                worktree_path=ctx.worktree_path, known_files=known_files,
                attempt=state.attempt_number, extra_likely_files=["pom.xml"],
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        message = (
            "VERIFICATION_INFRASTRUCTURE_FAILURE: runtime behavior was not observed "
            f"because Kriya's own artifact-preparation step failed: {outcome.reasoning}."
        )
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=outcome.stderr or outcome.reasoning, attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)


def _raise_runtime_verification_infrastructure_failure(
    deps: RuntimeDeps, state: RunState,
    run_result: Dict[str, Any],
    commands: List[List[str]],
) -> None:
    """Stop application-launch failures before grading/source repair.

    Build/test runners are process-based too, but their process status is
    already the deterministic verdict.  Entrypoint-shaped text in their
    output therefore belongs to test/build execution and must never be
    reinterpreted by application-runtime infrastructure handling.

    RUNTIME-VERIFICATION-ENV-STOP-001 (third repair cycle review F1,
    2026-10-10): the structured ``environment_reason_code`` is consulted
    first - the validator could not prepare the environment the command
    needs (VERIFICATION-UNIT-ENV-FALLBACK-001's typed result), so nothing
    ran: there is no process authority for the deterministic early return
    below and no behaviour to grade. Keyed on the structured field only,
    never on output text; the same typed stop the compile and test gates
    take (STOP_ENVIRONMENT: no grading, no Developer retry, no owner
    recovery).
    """
    deps._stop_on_environment_gate_result(state, run_result, "runtime")
    if deterministic_sequence_kind(commands) is not None:
        return
    # D4: a missing entrypoint is owned by whoever chose it (the validator's
    # deterministic diagnosis of the fresh build), not infrastructure by default.
    entrypoint = run_result.get("entrypoint_diagnosis")
    ownership = classify_runtime_entrypoint(entrypoint, run_result.get("output", ""))
    if ownership == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID:
        deps._raise_candidate_runtime_entrypoint_failure(state, run_result, commands, entrypoint)
    reason = runtime_verification_infrastructure_reason(run_result)
    if reason is None:
        return
    if ownership is not None:
        reason = (f"{ownership}: the runtime entrypoint {entrypoint['effective_entrypoint']} "
                  f"(chosen by {entrypoint['effective_entrypoint_provenance']}; declared in source: "
                  f"{entrypoint['source_declares_entrypoint']}; compiled: {entrypoint['compiled_artifact_exists']}) "
                  "could not be loaded")
    state.cached_run_verification_judgment = None
    message = (
        "VERIFICATION_INFRASTRUCTURE_FAILURE: runtime behavior was not observed because "
        f"the verifier infrastructure failed: {reason}.\n\nCaptured output:\n"
        f"{run_result.get('output', '')}"
    )
    code = reason.split(":", 1)[0]
    failure = Failure(
        type="verification_infrastructure_failure", message=message,
        raw_output=run_result.get("output", ""), attempt=state.attempt_number,
        diagnostics={"reason_code": code} if code.isupper() and "_" in code else None,
    )
    outcome = failure.to_gate_outcome()
    outcome.update({"commands": commands, "steps": run_result.get("steps", [])})
    state.record_gate_outcome(outcome)
    raise QualityGateFailure(failure)


def _raise_candidate_runtime_entrypoint_failure(
    state: RunState, run_result: Dict[str, Any], commands: List[List[str]], entrypoint: Dict[str, Any],
) -> None:
    """D4 case A: the candidate's own build configuration selects a main class
    that no current source declares and the fresh build did not produce. A
    candidate defect implicating that configuration file - routed through the
    ordinary repair path (the file's own work unit, or, from a unit that owns
    no files, the existing grounded cross-owner recovery). The evidence names
    the defect; Kriya never rewrites the configuration itself."""
    config = entrypoint["candidate_config_source"]
    message = (
        f"{CANDIDATE_RUNTIME_ENTRYPOINT_INVALID}: the candidate's {config} selects the runtime main class "
        f"{entrypoint['effective_entrypoint']} ({entrypoint['candidate_config_key']}), but no source in the "
        "project declares it and the fresh build did not produce it. Maven uses this configuration over the run "
        f"command's -Dexec.mainClass={entrypoint['requested_entrypoint']}, so the application cannot start.\n\n"
        f"Captured output:\n{run_result.get('output', '')}"
    )
    failure = Failure(
        type="candidate_runtime_entrypoint_invalid", message=message,
        raw_output=run_result.get("output", ""), attempt=state.attempt_number, likely_files=[config],
        diagnostics={"reason_code": CANDIDATE_RUNTIME_ENTRYPOINT_INVALID, "entrypoint_diagnosis": entrypoint},
    )
    outcome = failure.to_gate_outcome()
    outcome.update({"commands": commands, "steps": run_result.get("steps", [])})
    state.record_gate_outcome(outcome)
    raise QualityGateFailure(failure)


def _apply_runtime_verification_contract(
    commands: List[List[str]], input_channel: str,
) -> Tuple[List[List[str]], Optional[str], Optional[str]]:
    """Runtime Verification Contract (PRV-06, 2026-08-29). RunVerifierAgent.
    judge() now states input_channel ("argv"/"stdin"/"none") as an explicit
    fact about what the goal requires, independent of whatever literal
    command it happened to return - this is the deterministic enforcement
    layer that makes that fact actually reach the real invocation, closing
    a live incident where the judge's own success_criteria correctly named
    "the command line argument" but its own run_commands never supplied
    one, and the app's own correct "no input provided" response was then
    misdiagnosed as an application defect for 9 wasted attempts.

    Returns (corrected_commands, stdin_payload, incomplete_reason).
    incomplete_reason is None whenever the contract was successfully
    satisfied (including "none", and "argv"/"stdin" already supplied) -
    non-None means the caller must raise RUNTIME_VERIFICATION_CONTRACT_
    INCOMPLETE and never launch the process (see this module's own call
    sites). The synthetic value is a single, stable, Kriya-owned constant
    (never asked of an LLM, matching the same discipline as every other
    typed value this codebase generates deterministically) - it is not
    goal-specific text, so it carries no proprietary/external data and is
    safe to record verbatim in verification evidence.

    Deliberately narrow, evidence-bounded shape detection - three proven-live
    invocation forms get real flag-aware handling, everything else falls
    back to a plain two-token heuristic rather than being guessed at: a
    Maven exec:java invocation (recognized by an "exec:java" token or an
    already-present "-Dexec.mainClass=" token - exec:exec is deliberately
    NOT matched here, since its own argument-passing mechanism is
    pom.xml-configured, not a "-Dexec.args=" command-line property) gets
    "-Dexec.args=<value>" appended; any other "mvn"/"gradle"/"./gradlew"
    invocation is left unrecognized rather than guessed at - a bare
    trailing token there is parsed as an additional lifecycle phase/goal,
    not a program argument, and would fail the build outright. A `java`
    launch is parsed through its own known JVM/classpath flags-with-values
    (-cp/-classpath/--module-path/...) to find the entrypoint token, so a
    missing application argument is appended after the class rather than
    confused with a JVM option's value - this flag-aware handling is
    Java-specific, not a general per-runtime mechanism; a different
    runtime with its own flag-with-value syntax (e.g. `dotnet run
    --project X`, `node --experimental-modules app.js`) does NOT get the
    same treatment and instead falls through to the plain two-token
    fallback below. Any other exactly-two-token command (["java","Main"],
    ["python","app.py"], ["node","app.js"], ...) gets the value appended as
    a third, positional token. Other commands already carrying more than
    two tokens (a non-Java command with its own extra argv), or an
    exec:java command that already sets "-Dexec.args=", are assumed to
    already supply their own value and are left untouched - a pre-existing
    boundary, not new here. Only applied to the LAST command in the
    sequence - the one that actually exercises the application's behavior
    in every real sequence this codebase produces (see run_app_sequence's own docstring
    for the multi-invocation case, e.g. "add an item, then list items",
    where every earlier step is its own already-fully-specified
    invocation, not something this function is meant to touch)."""
    if not commands or input_channel not in ("argv", "stdin"):
        return commands, None, None
    if input_channel == "stdin":
        return commands, _RUNTIME_VERIFICATION_SYNTHETIC_INPUT, None
    *prefix, last = commands
    # Build/test commands report their own authoritative process verdict and
    # are not application invocations. An application argv contract is simply
    # inapplicable to them; never turn the input value into a lifecycle goal,
    # test selector, or package-manager argument.
    if deterministic_verification_kind(last) is not None:
        return commands, None, None
    if last and os.path.basename(last[0]).lower() == "java":
        options_with_values = {
            "-cp", "-classpath", "--class-path", "-p", "--module-path",
            "--upgrade-module-path", "--add-modules", "--limit-modules",
            "--add-reads", "--add-exports", "--add-opens", "--patch-module",
        }
        target_index: Optional[int] = None
        skip_next = False
        for index, token in enumerate(last[1:], start=1):
            if skip_next:
                skip_next = False
                continue
            if token in options_with_values:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            target_index = index
            break
        if target_index is not None and target_index == len(last) - 1:
            return prefix + [last + [_RUNTIME_VERIFICATION_SYNTHETIC_INPUT]], None, None
        if target_index is not None:
            return commands, None, None
    is_maven_exec_java = "exec:java" in last or any(
        tok.startswith("-Dexec.mainClass=") for tok in last
    )
    if is_maven_exec_java:
        if any(tok.startswith("-Dexec.args=") for tok in last):
            return commands, None, None
        return prefix + [last + [f"-Dexec.args={_RUNTIME_VERIFICATION_SYNTHETIC_INPUT}"]], None, None
    # A bare trailing token is a valid program argument for a plain
    # interpreter invocation (java/python/node/...), but NOT for "mvn"/
    # "gradle" (a bare token there is parsed as an additional lifecycle
    # phase/goal to run, e.g. "mvn exec:exec kriya-verification-input"
    # would fail Maven outright with an unknown-phase error) - any other
    # mvn/gradle shape besides the exec:java one already handled above is
    # left unrecognized rather than guessed at.
    if last and last[0] in ("mvn", "gradle", "./gradlew", "gradlew"):
        return commands, None, (
            f"input_channel=argv but the final command ({last!r}) is a build-tool "
            "invocation this deterministic injector doesn't know how to pass an "
            "argument to safely (only mvn exec:java's -Dexec.args= is supported)."
        )
    if len(last) == 2:
        return prefix + [last + [_RUNTIME_VERIFICATION_SYNTHETIC_INPUT]], None, None
    if len(last) > 2:
        return commands, None, None
    return commands, None, (
        f"input_channel=argv but the final command's shape ({last!r}) isn't one this "
        "deterministic injector recognizes - refusing to guess where to place a "
        "command-line argument rather than silently running an under-specified invocation."
    )


def _run_verification_basis_hash(ctx: AttemptCtx, state: RunState) -> str:
    """Fingerprint the real invocation-affecting basis for cached judgment."""
    digest = hashlib.sha256()
    digest.update(ctx.goal.encode("utf-8", errors="replace"))
    digest.update(ctx.design.encode("utf-8", errors="replace"))
    for filepath in sorted(set(state.all_files_written) | set(ctx.established_files)):
        digest.update(filepath.encode("utf-8", errors="replace"))
        full_path = os.path.join(ctx.worktree_path, filepath)
        try:
            with open(full_path, "rb") as fh:
                # Repair-envelope parsing may add or remove only terminal
                # whitespace while preserving the executable file.  Do not
                # spend another judgment call for that serialization detail;
                # substantive source/config changes still alter this basis.
                content = fh.read().replace(b"\r\n", b"\n").rstrip()
                digest.update(content)
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def _python_grounding_facts_from_members(root: str, members: Iterable[str]) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """W3-3 unit U1: the two grounding facts of a Git-backed root from its canonical membership (``members``: root-relative real files), never from a directory name -
    every in-scope ``.py`` member, and the directories of the ``__init__.py`` members ("" is the root). The same per-entry ``is_within_scope`` containment as the
    legacy walk: a symlink member that resolves outside ``root`` never reaches either set."""
    scope = make_workspace_scope(root)
    all_py: set = set()
    package_dirs: set = set()
    for member in members:
        rel = member.replace(os.sep, "/")
        name = rel.rsplit("/", 1)[-1]
        if name == "__init__.py":
            package_dirs.add(rel.rpartition("/")[0])
        if name.endswith(".py") and is_within_scope(scope, os.path.join(root, member)):
            all_py.add(rel)
    return frozenset(all_py), frozenset(package_dirs)


def _collect_python_runtime_grounding_facts(deps: RuntimeDeps, root: str) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Every real, in-scope `.py` file's workspace-relative path, plus the set
    of real Python package directories (those containing __init__.py) - the
    two repository-wide facts ground_python_runtime_target() (kriya/workflow/
    file_resolution.py) needs to validate an LLM-proposed runtime target
    against real evidence instead of trusting it.

    W3-3 unit U1: on a Git-backed root the facts are its canonical bound
    tree's membership (``worktree.candidate_member_files``: a tracked
    ``pkg/build/runner.py`` is a member, a git-ignored output is not; a root
    Git cannot list is the typed ``BoundTreeError``, never an empty set). The
    PARKED root classes (the pre-bootstrap non-Git workspace and the
    scoped-snapshot sandbox, which have no canonical membership owner) keep
    the legacy bounded walk below UNCHANGED - not endorsed: its directory-name
    prune is the defect corrected for a Git-backed root. followlinks=False
    plus a per-entry is_within_scope() check (the same symlink-safe
    containment idiom PolymorphicValidator.run_app_sequence()'s own javac -d
    directory creation already uses, kriya/tools/validate.py) - a malicious
    symlink inside the repository can never smuggle an outside path into
    either returned set."""
    members = candidate_member_files(root)
    if members is not None:
        return _python_grounding_facts_from_members(root, members)
    scope = make_workspace_scope(root)
    all_py: set = set()
    package_dirs: set = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in deps._PYTHON_RUNTIME_GROUNDING_EXCLUDED_DIRS and not d.startswith(".")
        ]
        rel_dir = os.path.relpath(dirpath, root)
        if "__init__.py" in filenames:
            norm_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            package_dirs.add(norm_dir)
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            if not is_within_scope(scope, full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            all_py.add(rel)
    return frozenset(all_py), frozenset(package_dirs)


def _build_python_runtime_grounding(
    deps: RuntimeDeps, root: str, declared_paths: Iterable[str] = (),
) -> Tuple[FrozenSet[str], FrozenSet[str], List[str]]:
    """(all_python_files, package_dirs, entrypoint_files) - the real,
    repository-wide facts ground_python_runtime_target() validates an
    LLM-proposed Python runtime-verification target against (VER-005
    implementation, 2026-09-13). Deliberately walks the real filesystem
    from `root` rather than being scoped to this run's own
    state.all_files_written the way Java's _build_java_main_class_map is:
    Python entrypoint/package structure routinely PREDATES the current
    attempt entirely in a brownfield repository (a pre-existing top-level
    main.py never touched this run, a pre-existing validation/__init__.py
    from before Kriya ever ran) - scoping to run-local writes would
    silently reintroduce a false-negative version of the exact defect this
    fixes (a genuinely valid, untouched entrypoint rejected as
    "nonexistent"). Recomputed fresh every attempt, never cached alongside
    RunVerifierAgent.judge()'s own judgment - same "a retry can edit file
    content between attempts, so this must reflect what's actually on disk
    right now" reasoning ground_java_entrypoint_in_no_build_file_projects's
    own call sites already apply.

    entrypoint_files is every real, non-test, non-__init__.py `.py` file
    under root with REAL, observable top-level behavior when run directly -
    the Python sibling of _build_java_main_class_map's real-main()-method
    detection, but grounded in Python's own semantics (see python_file_
    is_runnable_script()'s own docstring for why a required `__main__`
    guard is the wrong transliteration of Java's requirement).

    W3-3 unit U1 - membership is not target authority. On a Git-backed root
    ``all_python_files`` / ``package_dirs`` are the canonical bound tree's
    (visible evidence: the file exists), but only a DECLARED file may be an
    entrypoint the grounding selects or substitutes: DECLARED = Git tracked or
    proven written by THIS run (``declared_paths``, path-exact - the caller's
    ``state.all_files_written``), the one positive-declaration split
    ``file_resolution.declared_and_ambiguous_member_files`` also applies to
    artifact owners. A pre-existing untracked, non-ignored file (a project's own
    uncommitted script and a dependency tree nobody ignored are
    indistinguishable by presence) stays visible but never becomes an
    automatically selected runtime target on membership alone - by no directory
    name. The parked root classes keep the legacy facts and every file a
    candidate, UNCHANGED."""
    if candidate_has_canonical_membership(root):
        declared, ambiguous = declared_and_ambiguous_member_files(root, declared_paths)
        all_py, package_dirs = _python_grounding_facts_from_members(root, [*declared, *ambiguous])
        selectable = {member.replace(os.sep, "/") for member in declared}
        candidates = sorted(all_py & selectable)
    else:
        all_py, package_dirs = deps._collect_python_runtime_grounding_facts(root)
        candidates = sorted(all_py)
    entrypoints: List[str] = []
    for rel in candidates:
        if python_target_path_is_test_shaped(rel) or os.path.basename(rel) == "__init__.py":
            continue
        full = os.path.join(root, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception as e:
            deps.logger.debug(f"Python entrypoint detection: couldn't read {rel}, skipping it: {e}")
            continue
        if python_file_is_runnable_script(content):
            entrypoints.append(rel)
    return all_py, package_dirs, entrypoints


def _runtime_verification_is_advisory_only(ctx: AttemptCtx, judgment: Dict[str, Any]) -> bool:
    """The ONE authority for "is this subtask allowed to actually execute
    the run-verification judge's inferred command right now" - PRV-17
    (2026-09-03). ctx.goal for a bounded MA6 subtask always includes the
    full, unmediated "Authoritative Goal" section (build_subtask_goal_
    text(), the PRV-11 authority-isolation fix) - deliberately, so judge()
    can still recognize a goal-explicit run command - but judge() itself
    has no per-subtask stage-scoping equivalent to SpecComplianceAgent's
    own _stage_scoped_spec_compliance_goal() (that fix's own docstring:
    "this gate's own prompt below is the other half of that fix" - judge()
    was never given the other half). ctx.runtime_verification_required IS
    already computed correctly per-subtask (workflow_controller.py, from
    THIS subtask's own target.verification) - an inferred should_run=True
    from a subtask whose own approved plan declares no runtime obligation
    is judge()'s best-effort guess against the full goal text, not
    evidence this specific, bounded subtask must satisfy runtime behavior
    right now. A later subtask whose plan DOES declare the obligation
    still runs this exact check for real - this only ever suppresses an
    UNDECLARED inference, never a declared requirement (a no-op whenever
    ctx.runtime_verification_required is True).

    A live incident found TWO separate call sites independently re-
    implementing "call judge(), maybe execute, maybe grade()"
    (_execute_runtime_verification_directly for a DENY_ALL verification-
    only subtask, and run_attempt()'s own much larger inline block for an
    ordinary ALLOWLIST implementation subtask) - an earlier fix patched
    only the first, so an ordinary scaffold subtask (ALLOWLIST, not
    DENY_ALL - the common case) still executed a FUTURE-owned endpoint's
    inferred `manage.py runserver` command. Both call sites now consult
    this ONE function instead of each re-deriving the same decision - the
    "second authority" is closed by construction, not by finding and
    patching every copy by hand. Unstructured/legacy callers (ctx.
    structured_plan is None) are completely unaffected - runtime_
    verification_required there is already derived from the WHOLE goal,
    matching pre-existing behavior exactly."""
    return bool(
        ctx.structured_plan is not None and ctx.current_subtask_id
        and not ctx.runtime_verification_required
        and judgment.get("should_run")
    )


def _derive_runtime_command_subject(deps: RuntimeDeps, ctx: AttemptCtx, judgment: Dict[str, Any], intro: str) -> RuntimeCommandSubject:
    """The subject a human approves, built from the commands that will actually EXECUTE: resolved (``_resolve_run_command``) and with the runtime-verification contract applied,
    never the judge's raw argv (those can differ: interpreter/PATH substitution, an appended argument or stdin payload). Pure and deterministic, so the executor derives the
    same set again."""
    resolved = [deps._resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
    channel = judgment.get("input_channel") or "none"
    final, stdin_payload, incomplete = deps._apply_runtime_verification_contract(resolved, channel)
    commands_desc = "\n".join(f"    {i}. {' '.join(cmd)}" for i, cmd in enumerate(final, 1))
    reason = (f"{intro}\n  Command(s):\n{commands_desc}\n  Looking for: {judgment['success_criteria']}\n"
              "Allow Kriya to execute these command(s) inside the sandboxed worktree?")
    return build_runtime_subject(unit_id=ctx.current_subtask_id, criterion=str(judgment.get("success_criteria") or ""), working_directory=str(ctx.worktree_path),
                                 commands=final, input_channel=channel, stdin_payload=stdin_payload, reason=reason, incomplete_reason=incomplete)


def _runtime_commands_need_approval(deps: RuntimeDeps, ctx: AttemptCtx, state: RunState, judgment: Dict[str, Any], autonomy_mode: str) -> bool:
    """A human must approve, in human-in-the-loop mode, every command set that is not the user's own exact words (``commands_authored_in_goal``: judge-generated, inferred, expanded,
    reordered or substituted commands all need review; merely naming a tool in the goal is not authorization; the judge's ``command_source`` label is not trusted), unless the
    exact set that would run now is the one already approved in this run. ExecutionPolicy, the sandbox and containment still apply to every command, authored or approved."""
    if not is_human_in_the_loop(autonomy_mode):  # OI-135: the fail-closed reader
        return False
    subject = deps._derive_runtime_command_subject(ctx, judgment, "")
    # A desktop launch (typed approval callback) always asks: the goal text it would be matched against is supplied by the window, so "the user's own words" cannot be trusted there, and the
    # native confirmation is the one commitment outside the renderer. Only the terminal keeps the exact-words exemption.
    if not isinstance(ctx.approval_callback, TypedApprovalCallback) and commands_authored_in_goal(subject.commands, deps.exit_authority_text(ctx), subject.stdin_sha256):
        return False
    return state.run_verification_approved_fingerprint != subject.fingerprint


async def _approve_runtime_commands(deps: RuntimeDeps, ctx: AttemptCtx, state: RunState, judgment: Dict[str, Any], intro: str) -> bool:
    """Ask a human to approve the exact runtime command set; True (and the approved fingerprint recorded) only when the approval is bound to the commands the executor derives
    right after the answer. A changed set (the worktree or PATH moved while the person decided) is reviewed again, at most MAX_REVIEW_ROUNDS times, and never executed."""
    for _round in range(MAX_REVIEW_ROUNDS):
        subject = deps._derive_runtime_command_subject(ctx, judgment, intro)
        if subject.incomplete_reason:
            return True  # the runtime-verification contract cannot be satisfied: nothing will run and the caller raises RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE; no one is asked to approve it
        decision = await ask_runtime_commands(ctx.approval_callback, subject)
        if not decision.approved:
            return False
        if approval_matches(decision, deps._derive_runtime_command_subject(ctx, judgment, intro)):
            state.run_verification_approved_fingerprint = decision.approved_fingerprint
            return True
        deps.logger.warning("RUNTIME_COMMAND_CHANGED: the runtime command set differs from the one that was approved; it is not executed (review round %d)", _round + 1)
    return False


def _required_runtime_verification_missing_message(judgment: Dict[str, Any]) -> str:
    """PRV-06 (2026-08-28): observability fix, not a behavioral one - the
    judge's own stated reasoning (RunVerifierAgent.judge()'s `reasoning`
    field) is never consulted by any should_run/run_commands decision
    anywhere in this codebase, only surfaced here so a
    REQUIRED_RUNTIME_VERIFICATION_MISSING failure's persisted gate_outcome/
    traces.db record carries the judge's own explanation instead of a bare
    boolean with no way to tell a genuine "no runtime behavior to check"
    call apart from a judgment mistake. Found live, PRV-06 (2026-08-28):
    a Legacy run hit this exact failure after 8 prior compile/test-gate
    failures (the judge is called at most once per subtask, only once
    compile+test finally pass), and there was no way to tell whether the
    single should_run=False verdict was a genuine call or a mistake -
    `reasoning` may still be empty (the model can omit it despite the
    prompt asking for it; never fabricated here), but when present this
    closes that exact gap."""
    reasoning = judgment.get("reasoning") or "(no reasoning field returned by the judge)"
    return (
        "REQUIRED_RUNTIME_VERIFICATION_MISSING: the declared verification contract requires "
        "observable runtime behavior, but no executable verification sequence was produced.\n"
        f"Judge's own reasoning: {reasoning}"
    )


_SUPPORTED_RUNTIME_EXECUTION_MODES = frozenset({"finite_command", "managed_service"})


_MANAGED_SERVICE_SHELL_MARKERS = frozenset({"&&", "||", ";", "&"})


_MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES = frozenset({"nohup", "sleep"})


_SHELL_INTERPRETER_EXECUTABLES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


_MANAGED_SERVICE_READINESS_KINDS = frozenset({"tcp", "http"})


_MANAGED_SERVICE_PROBE_KINDS = frozenset({"http"})


def _resolve_execution_mode(judgment: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
    """Managed Runtime Verification (2026-09-03): (mode, error). error is
    None for either of the two supported modes; anything else is itself a
    verification-infrastructure defect, not a silent fallback to
    finite_command - a caller passing an unsupported execution_mode is
    exactly as unexecutable as a missing service_command, and must be
    rejected the same way. RunVerifierAgent.judge() only backward-
    compatibly defaults a genuinely ABSENT execution_mode to
    "finite_command" - an explicitly-present, unrecognized value now
    survives unchanged so this check (the single source of truth both
    runtime-verification call sites consult, exactly like _runtime_
    verification_is_advisory_only above) can actually reject it, rather
    than the agent layer silently rewriting it into something that always
    passed. isinstance-guarded (not a bare `in _SUPPORTED_RUNTIME_
    EXECUTION_MODES` membership test) so a malformed non-string value (a
    list, a dict) is reported as unsupported instead of crashing on an
    unhashable-type lookup - a JSON response neither this function nor
    judge() controls the exact shape of."""
    execution_mode = judgment.get("execution_mode")
    if execution_mode is None:
        execution_mode = "finite_command"
    if not isinstance(execution_mode, str) or execution_mode not in _SUPPORTED_RUNTIME_EXECUTION_MODES:
        return execution_mode, f"unsupported execution_mode {execution_mode!r}"
    return execution_mode, None


def _looks_like_shell_compound_command(command: Any) -> bool:
    """Managed Runtime Verification (2026-09-03): the one thing a managed-
    service judgment must never be allowed to smuggle through - a service-
    start-then-probe intent re-encoded as a single shell-compound command
    (`runserver && curl`, `runserver ; curl`, `nohup runserver &`, a
    `sh -c "..."` wrapper) instead of the structured service_command/
    readiness/probe fields MANAGED_SERVICE exists specifically to carry.
    Deliberately narrow: `["bash", "start_server.sh"]` (a real launcher
    script as an ordinary argument, no `-c`) is NOT flagged - only the
    shapes that are actually shell orchestration are."""
    if not isinstance(command, list) or not command or not all(isinstance(tok, str) for tok in command):
        return False
    executable = os.path.basename(command[0]).lower()
    if executable in _MANAGED_SERVICE_FORBIDDEN_LEADING_EXECUTABLES:
        return True
    if executable in _SHELL_INTERPRETER_EXECUTABLES and "-c" in command:
        return True
    return any(tok in _MANAGED_SERVICE_SHELL_MARKERS for tok in command)


def _reject_non_local_managed_service_host(deps: RuntimeDeps, field_name: str, host: str) -> Optional[str]:
    """External review, 2026-09-03 (P0): a managed-service readiness/probe
    target is a real outbound network connection kriya/tools/service_
    runtime.py will actually make (socket.create_connection/urllib.request.
    urlopen), completely bypassing kriya/core/llm.py's egress boundary -
    that boundary only ever guards LLM completion calls, and MA4.6's
    ExecutionPolicy network-egress check (kriya/policy/execution.py) only
    ever guards NETWORK_ACCESS/LLM_NETWORK_ACCESS action requests routed
    through the policy engine, neither of which this new code path is. An
    LLM judgment naming host="example.com" would otherwise connect
    externally with zero enforcement - exactly the local-only boundary
    CLAUDE.md's "Egress control" section says every LLM call site must
    respect, now extended to this one. Deliberately reuses is_local_url
    itself (function-local import, mirroring kriya/policy/execution.py's
    own _check_network_egress precedent and its own documented reasoning)
    rather than writing a second, independently-maintained local/private-
    address heuristic that could quietly drift from the real boundary's
    definition of "local"."""
    is_local_url = deps.load_is_local_url()

    if not is_local_url(f"http://{host}"):
        return (
            f"managed_service.{field_name} host {host!r} is not a local/private address - "
            "managed-service verification may only target the local workspace, matching "
            "Kriya's local-only egress boundary (kriya/core/llm.py::is_local_url)"
        )
    return None


def _validate_and_convert_readiness(deps: RuntimeDeps, raw: Any) -> Tuple[Optional[Any], Optional[str]]:
    if not isinstance(raw, dict):
        return None, "managed_service.readiness is missing or not a JSON object"
    kind = raw.get("kind")
    if kind not in _MANAGED_SERVICE_READINESS_KINDS:
        return None, f"managed_service.readiness.kind must be one of {sorted(_MANAGED_SERVICE_READINESS_KINDS)}, got {kind!r}"
    host = raw.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        return None, f"managed_service.readiness.host must be a non-empty string, got {host!r}"
    egress_error = deps._reject_non_local_managed_service_host("readiness", host)
    if egress_error:
        return None, egress_error
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        return None, f"managed_service.readiness.port must be an integer 1-65535, got {port!r}"
    path = raw.get("path", "/")
    if not isinstance(path, str) or (kind == "http" and not path.startswith("/")):
        return None, f"managed_service.readiness.path must be a string starting with '/', got {path!r}"
    return deps.ReadinessSpec(kind=kind, host=host, port=port, path=path), None


def _validate_and_convert_probe(deps: RuntimeDeps, raw: Any) -> Tuple[Optional[Any], Optional[str]]:
    if not isinstance(raw, dict):
        return None, "managed_service.probe is missing or not a JSON object"
    kind = raw.get("kind", "http")
    if kind not in _MANAGED_SERVICE_PROBE_KINDS:
        return None, f"managed_service.probe.kind must be one of {sorted(_MANAGED_SERVICE_PROBE_KINDS)}, got {kind!r}"
    host = raw.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host:
        return None, f"managed_service.probe.host must be a non-empty string, got {host!r}"
    egress_error = deps._reject_non_local_managed_service_host("probe", host)
    if egress_error:
        return None, egress_error
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        return None, f"managed_service.probe.port must be an integer 1-65535, got {port!r}"
    path = raw.get("path", "/")
    if not isinstance(path, str) or not path.startswith("/"):
        return None, f"managed_service.probe.path must be a string starting with '/', got {path!r}"
    expected_status = raw.get("expected_status", 200)
    if not isinstance(expected_status, int) or isinstance(expected_status, bool) or not (100 <= expected_status <= 599):
        return None, f"managed_service.probe.expected_status must be an HTTP status integer, got {expected_status!r}"
    method = raw.get("method", "GET")
    if not isinstance(method, str) or not method:
        return None, f"managed_service.probe.method must be a non-empty string, got {method!r}"
    expected_body_contains = raw.get("expected_body_contains")
    if expected_body_contains is not None and not isinstance(expected_body_contains, str):
        return None, f"managed_service.probe.expected_body_contains must be a string or null, got {expected_body_contains!r}"
    return deps.ProbeSpec(
        kind=kind, method=method, host=host, port=port, path=path,
        expected_status=expected_status, expected_body_contains=expected_body_contains,
    ), None


def _validate_and_convert_managed_service_contract(
    deps: RuntimeDeps, managed_service: Any, worktree_path: str, validator: Validator,
) -> Tuple[Optional[Any], Optional[str]]:
    """Managed Runtime Verification (2026-09-03) - the deterministic
    admission check a MANAGED_SERVICE judgment must pass BEFORE any
    process starts. Mirrors _apply_runtime_verification_contract's own
    (spec_or_none, reason_or_none) shape exactly: a non-None reason means
    the caller raises VERIFICATION_INFRASTRUCTURE_FAILURE and calls
    neither run_managed_service_verification() nor the Developer, the same
    RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE precedent the finite-command
    path already uses. Pure validation/conversion - no process is started,
    no I/O happens here, nothing in this function can itself fail non-
    deterministically. This is the one place a kriya.tools.service_runtime
    type gets constructed from agent/workflow-facing data - the dependency
    direction stays agent/workflow representation -> this conversion ->
    kriya.tools.service_runtime's own execution spec, never the reverse
    (kriya/tools/service_runtime.py imports nothing from kriya.workflow or
    kriya.agents, and stays completely unaware of interpreter/dependency
    resolution - that grounding happens here, once, before the spec is
    ever built).

    Environment-grounding fix (2026-09-03, external review): a live PRV-17
    run proved this service_command reached ProcessController.start_managed()
    completely ungrounded - a bare "python" token resolves via whatever
    PATH/environment Kriya's own process happens to inherit, which is NOT
    the project-local, dependency-installed interpreter run_tests()/
    run_app_sequence() already resolve for this same workspace
    (PolymorphicValidator._resolve_python_interpreter()/_substitute_python_
    interpreter()) - a Django app's managed-service verification failed
    with ModuleNotFoundError even though the identical dependency had
    already been correctly installed for the test gate one step earlier.
    Reuses that EXISTING primitive (the same one run_app()/run_app_sequence()
    already call) rather than inventing a second resolver - a no-op for
    every non-Python stack or a Python workspace with no real dependency
    manifest, exactly like its two existing call sites."""
    if not isinstance(managed_service, dict):
        return None, "managed_service object is missing or not a JSON object"

    service_command = managed_service.get("service_command")
    if (
        not isinstance(service_command, list) or not service_command
        or not all(isinstance(tok, str) and tok for tok in service_command)
    ):
        return None, "managed_service.service_command is missing, empty, or not a list of non-empty strings"
    if deps._looks_like_shell_compound_command(service_command):
        return None, (
            f"managed_service.service_command {service_command!r} looks like a shell-compound "
            "invocation (&&, ;, &, nohup, sleep, or a shell -c wrapper) - the service and its "
            "probe must be represented as separate structured fields, not one shell command"
        )

    grounded_commands, install_error = validator._substitute_python_interpreter([service_command])
    if install_error:
        return None, f"managed_service.service_command dependency resolution failed: {install_error}"
    service_command = grounded_commands[0]

    # VER-005 implementation (2026-09-13): a MANAGED_SERVICE target has no
    # safe deterministic substitute the way a FINITE_COMMAND target does
    # (ground_python_runtime_target() above) - there is no principled way
    # to guess which OTHER command should start a long-running service
    # instead. The one thing this can and must still refuse deterministically,
    # at this same pre-process-launch admission boundary every other
    # managed_service field is already validated at: a test file/module is
    # never a valid service entrypoint, regardless of what RunVerifierAgent.
    # judge() returned. Structural-only (python_command_targets_test_path
    # needs no repository-wide grounding facts) - fails closed here rather
    # than ever reaching ProcessController.start_managed().
    if python_command_targets_test_path(service_command):
        return None, (
            f"managed_service.service_command {service_command!r} targets a test file/module - "
            "a test artifact is never a valid long-running service entrypoint"
        )

    readiness, readiness_error = deps._validate_and_convert_readiness(managed_service.get("readiness"))
    if readiness_error:
        return None, readiness_error
    probe, probe_error = deps._validate_and_convert_probe(managed_service.get("probe"))
    if probe_error:
        return None, probe_error

    timeout_fields = {}
    for field_name, default in (
        ("startup_timeout_seconds", 20.0),
        ("probe_timeout_seconds", 10.0),
        ("shutdown_timeout_seconds", 10.0),
    ):
        raw_value = managed_service.get(field_name, default)
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool) or raw_value <= 0:
            return None, f"managed_service.{field_name} must be a positive number, got {raw_value!r}"
        timeout_fields[field_name] = float(raw_value)

    return deps.ManagedServiceVerificationSpec(
        service_command=service_command, cwd=worktree_path,
        readiness=readiness, probe=probe, **timeout_fields,
    ), None


MANAGED_SERVICE_APPROVAL_UNSUPPORTED = "MANAGED_SERVICE_APPROVAL_UNSUPPORTED"


def _managed_service_blocked_without_approval_subject(deps: RuntimeDeps, state: RunState, ctx: AttemptCtx) -> bool:
    """Demo V2 owner decision (2026-10-15): in human-in-the-loop mode a managed-service verification (a model-chosen long-running service) has NO reviewable approval subject - the
    RuntimeCommandSubject covers finite commands only - so it never starts. Decided BEFORE the contract is admitted, before interpreter/build preparation and before any process starts.
    The outcome mirrors the finite-command path's: when the declared verification contract REQUIRES the check, the run fails closed with a typed ``verification_infrastructure_failure``
    (MANAGED_SERVICE_APPROVAL_UNSUPPORTED - an unsupported approval semantic, not a human rejection: nothing is asked and the existing classification stops the run without Developer
    retries); when it is NOT required it is recorded as a declined check (``run_verification_declined``) and nothing runs, exactly as an unavailable approval does for a finite command.
    Returns True when the service must not run. Every other autonomy mode keeps its behavior. The single choke point both runtime call sites route through."""
    if not is_human_in_the_loop(ctx.kernel.config.autonomy.mode):  # OI-135: the fail-closed reader
        return False
    message = (
        f"{MANAGED_SERVICE_APPROVAL_UNSUPPORTED}: this goal requires Kriya to start a long-running service to verify it, and this version has no way to ask you to approve "
        "that. No service was started."
    )
    deps.logger.warning(message)
    if not ctx.runtime_verification_required:
        state.run_verification_declined = True
        return True
    failure = Failure(
        type="verification_infrastructure_failure", message=message, raw_output=message, attempt=state.attempt_number,
        diagnostics={"reason_code": MANAGED_SERVICE_APPROVAL_UNSUPPORTED},
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


async def _execute_managed_service_verification(
    deps: RuntimeDeps, state: RunState, ctx: AttemptCtx, judgment: Dict[str, Any], known_files: List[str],
    validator: Validator,
) -> None:
    """Managed Runtime Verification (2026-09-03) - the ONE place both
    runtime-verification call sites route a MANAGED_SERVICE judgment
    through, matching _runtime_verification_is_advisory_only's own "one
    shared authority, not two independently-patched copies" precedent from
    the previous PRV-17 round. Validates the judgment's managed_service
    object deterministically before starting any process, converts it to
    kriya.tools.service_runtime's own execution-layer spec type, runs it
    (a plain blocking call - run_app_sequence() is called exactly the same
    way a few lines away in the finite-command path, no new async pattern
    introduced here), and maps the result onto the SAME Failure/
    gate_outcome shapes the finite-command path already produces: no new
    recovery system, no new ownership decision - WHETHER this subtask owns
    the obligation was already decided above (advisory-only suppression,
    ctx.runtime_verification_required) before this function is ever
    called."""
    if deps._managed_service_blocked_without_approval_subject(state, ctx):
        return
    # R3 (Python venv): the admission below grounds the service command through the project-interpreter resolver, which BUILDS the project venv, and the venv
    # lives exactly as long as the verification scope that built it. So admission and the run are ONE session: the venv admission prepared is the venv the
    # service is started with (a session opened only around the run discarded it on entry and the service could not start).
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):
        spec, invalid_reason = deps._validate_and_convert_managed_service_contract(
            judgment.get("managed_service"), ctx.worktree_path, validator,
        )
        if invalid_reason:
            # RUNTIME-VERIFICATION-ENV-STOP-001 (review F1, 2026-10-10): the
            # admission grounds service_command through the interpreter
            # resolver, which records on the validator - structured, (code,
            # message) - that the REQUIRED environment could not be prepared.
            # That is the environment stop, never a verdict on the judgment's
            # contract: the service cannot start whatever the contract says.
            environment = validator.python_environment_error
            if environment is not None:
                deps._stop_on_environment_gate_result(
                    state, {"environment_reason_code": environment[0], "output": environment[1]}, "managed_service",
                )
            message = f"MANAGED_SERVICE_CONTRACT_INVALID: {invalid_reason}"
            deps.logger.warning(message)
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)

        deps.logger.info(
            "Quality Gates: Running managed_service verification: service=%s probe=%s %s:%d%s",
            " ".join(spec.service_command), spec.probe.method, spec.probe.host, spec.probe.port, spec.probe.path,
        )
        pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
        _managed_service_started = deps.time.monotonic()
        # D2 S4.1 (OI-142): the build, the service, its readiness checks and its probes are ONE verification in ONE exact-bound view of the candidate,
        # alive from before the build until the process is terminated and its output collected - so an ignored or untracked file in the worktree (a
        # stale artifact, a planted module, build configuration, a prior run's state) can decide none of it, and what the build produces (target/)
        # is what the service runs. Contained or not, the SAME containment decision the validator's gates take (build_containment_profile_and_backend,
        # built for the view): its profile mounts the view, and a contained build takes the validator's contained Maven path (registry-scoped
        # acquisition), never the host and never the project's ./mvnw.
        refusal = validator.view_command_refusal(spec.service_command, validator.workspace_path)
        if refusal is not None:
            message = f"MANAGED_SERVICE_CONTRACT_INVALID: {refusal}"
            deps.logger.warning(message)
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        containment_profile, containment_backend = validator.build_containment_profile_and_backend()
        result = deps.run_managed_service_verification(
            dataclasses.replace(spec, cwd=validator.workspace_path),
            containment_profile=containment_profile, containment_backend=containment_backend,
            contained_build=validator.contained_maven_build,
        )
    # R1 Deliverable 5 - observational only, coarse phase only: prepare/
    # launch/readiness/probe/shutdown are not separately timed here because
    # they are not separable from OUTSIDE run_managed_service_verification()
    # without modifying kriya/tools/service_runtime.py's own internals - an
    # architecture change this instrumentation-only task deliberately does
    # not make (see docs/assurance/KRIYA_PERFORMANCE_TELEMETRY.md's own
    # documented limitation).
    state.validator_timings.append({
        "kind": "managed_service_verification",
        "duration_seconds": deps.time.monotonic() - _managed_service_started,
        "success": bool(getattr(result, "passed", False)),
    })
    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)

    output = f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    if result.probe_status is not None:
        output += f"\n\nprobe_status: {result.probe_status}\nprobe_body: {result.probe_body}"

    if result.outcome in deps._MANAGED_SERVICE_INFRASTRUCTURE_OUTCOMES:
        # Runtime-Evidence Plan Repair (PRV-17 Run 13, 2026-09-04): before
        # unconditionally treating this as environment-only (as every one
        # of these outcomes used to be, unconditionally), give the SAME
        # classify_environment_failure() compile/test failures already
        # consult a chance to read the target application's own captured
        # output - see _MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_
        # OUTPUT's own docstring for exactly which outcomes qualify and why.
        if result.outcome in deps._MANAGED_SERVICE_OUTCOMES_WITH_CAPTURED_APPLICATION_OUTPUT:
            environment_verdict = classify_environment_failure(
                output, worktree_path=ctx.worktree_path, known_files=known_files,
            )
            if environment_verdict is None:
                missing_module = extract_missing_project_local_python_module(
                    output, known_files=known_files,
                )
                if missing_module is not None:
                    # Deterministic evidence that a project-local Python
                    # module the running application imports is missing -
                    # NOT an environment failure (classify_environment_
                    # failure already ruled that out) and NOT ordinary
                    # Developer retry evidence either: this subtask may own
                    # no write scope at all (a managed-service verification
                    # subtask is frequently DENY_ALL by construction), and
                    # even when it does, the missing module may belong to a
                    # DIFFERENT subtask entirely. Surfaced as its own typed
                    # Failure so retry_strategy.py can route it through
                    # plan-scope-conflict evidence rather than either the
                    # STOP_ENVIRONMENT bypass or the ordinary attribution/
                    # retry pipeline - see ObligationKind.RUNTIME_PLAN_GAP's
                    # own docstring for the live incident this closes and
                    # the invariant it preserves: execution evidence may
                    # prove the plan is incomplete, but only a validated
                    # plan revision (kriya/workflow/workflow_controller.py)
                    # may convert that evidence into new write authority -
                    # never this function, never the Developer.
                    message = (
                        f"MANAGED_SERVICE_RUNTIME_PLAN_GAP: managed-service verification "
                        f"failed ({result.outcome.value}) importing project-local Python "
                        f"module {missing_module!r}, which no subtask in the approved plan "
                        f"currently owns.\n\nCaptured output:\n{output}"
                    )
                    failure = Failure(
                        type="managed_service_runtime_plan_gap", message=message,
                        raw_output=output, attempt=state.attempt_number,
                        diagnostics={
                            "missing_python_module": missing_module,
                            "managed_service_outcome": result.outcome.value,
                        },
                    )
                    outcome_dict = failure.to_gate_outcome()
                    outcome_dict.update({
                        "commands": [spec.service_command], "managed_service_outcome": result.outcome.value,
                    })
                    state.record_gate_outcome(outcome_dict)
                    raise QualityGateFailure(failure)
                # Neither an environment gap nor a missing-module shape -
                # classify_environment_failure found nothing conclusive
                # either way. Deliberately falls through to the UNCHANGED
                # verification_infrastructure_failure/STOP_ENVIRONMENT path
                # below rather than guessing this is ordinary Developer-
                # retryable evidence: Runtime-Evidence Plan Repair (PRV-17
                # Run 13, 2026-09-04) is scoped narrowly to the one grounded
                # case classify_environment_failure/extract_missing_
                # project_local_python_module can actually prove - widening
                # the classification of every OTHER captured-output shape is
                # a separate, unauthorized behavior change (and would
                # silently break test_infrastructure_outcomes_map_to_
                # verification_infrastructure_failure's own, deliberately
                # unconditional, coverage of this exact case).
        message = (
            f"VERIFICATION_INFRASTRUCTURE_FAILURE: managed-service verification could not "
            f"produce behavioral evidence ({result.outcome.value}): {result.reasoning}\n\n"
            f"Captured output:\n{output}"
        )
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=output, attempt=state.attempt_number,
        )
        outcome_dict = failure.to_gate_outcome()
        outcome_dict.update({
            "commands": [spec.service_command], "managed_service_outcome": result.outcome.value,
        })
        state.record_gate_outcome(outcome_dict)
        raise QualityGateFailure(failure)

    if result.outcome == deps.ServiceVerificationOutcomeKind.PROBE_FAILED:
        message = (
            f"RUNTIME VERIFICATION FAILURE (managed service): {result.reasoning}"
            f"\n\nCaptured output:\n{output}"
        )
        failure = _build_quality_gate_failure(
            "run_verification", message, output, ctx.worktree_path, known_files, state.attempt_number,
        )
        failure_outcome = failure.to_gate_outcome()
        failure_outcome.update({
            "graded_by": "managed_service_probe", "commands": [spec.service_command],
            "managed_service_outcome": result.outcome.value,
        })
        state.record_gate_outcome(failure_outcome)
        raise QualityGateFailure(failure)

    # PROBE_PASSED - successful runtime evidence, deterministic (the probe's
    # own status/body match IS the verdict, no LLM grade() call needed -
    # same "process exit is already the authoritative verdict" precedent
    # deterministic_sequence_kind's test/compile commands already use.
    state.record_gate_outcome({
        "attempt": state.attempt_number, "type": "run_verification", "success": True,
        "output": output + f"\n\n[Managed service probe]: {result.reasoning}",
        "graded_by": "managed_service_probe", "commands": [spec.service_command],
        "managed_service_outcome": result.outcome.value, "deterministic_result": "PASS",
        **execution_evidence(result.to_dict()),
    })


def _raise_if_required_runtime_verification_declined(state: RunState, ctx: AttemptCtx) -> None:
    """Declining REQUIRED runtime verification is a refusal of required evidence, never a pass. The goal needs observable execution; the person (or the absence of any approver) did not
    authorize it, so nothing ran and correctness remains unverified: a typed ``verification_infrastructure_failure`` (REQUIRED_RUNTIME_VERIFICATION_DECLINED; retry_strategy
    STOP_ENVIRONMENT, so no Developer retry) that stops the run before any approval or apply. It is NOT a claim that the generated code failed validation. An optional check
    (``runtime_verification_required`` False) keeps its declined-and-skipped semantics. Called on entry (a decline carried in ``state`` from an earlier gate call) and immediately after
    a decline is recorded, so the same attempt cannot fall through to success on either plan shape."""
    if not (ctx.runtime_verification_required and state.run_verification_declined):
        return
    message = (
        "REQUIRED_RUNTIME_VERIFICATION_DECLINED: the declared runtime check was not "
        "authorized, so correctness remains unverified."
    )
    failure = Failure(
        type="verification_infrastructure_failure", message=message,
        raw_output=message, attempt=state.attempt_number,
    )
    state.record_gate_outcome(failure.to_gate_outcome())
    raise QualityGateFailure(failure)


async def _execute_runtime_verification_directly(
    deps: RuntimeDeps, state: RunState, ctx: AttemptCtx, validator: Validator,
) -> None:
    """PRV-06 (2026-08-28): the application_runtime half of
    _run_verification_only_attempt's direct-execution path - judge, correct,
    (approve), execute, grade, raise-or-return, with NO Developer
    invocation anywhere in this function. Deliberately reuses every
    existing sub-helper by reference (RunVerifierAgent.judge()/grade(),
    _resolve_run_command, ground_java_entrypoint_in_no_build_file_projects,
    the JDK/JVM-flag preflight corrections, run_app_sequence,
    deterministic_sequence_kind, _classify_grounded_contract_verdict, _resolve_runtime_verification_grade,
    _build_quality_gate_failure) - none of their own internal logic is
    reimplemented here, only the SEQUENCE in which a verification-only
    subtask needs to call them is new.

    Deliberately narrower than the mutating path's inline runtime-
    verification block (kriya/workflow/attempt.py's own "Quality Gates:
    Runtime Verification" section) in two respects, both intentional:
    - No self-correction micro-loop. That loop exists to patch
      infrastructure/classpath issues in code a Developer just wrote in
      THIS attempt - a verification-only subtask writes nothing, so
      self_correction_loop's own writable_files would already collapse to
      [] under DENY_ALL, making it a pure no-op burn of one extra LLM call.
      "Verification does not generate code. Failure recovery may generate
      code" (this fix's own design principle) - on failure, this function
      raises the same typed Failure the mutating path already raises, and
      the EXISTING outer retry/recovery machinery (which CAN re-enter a
      mutating context for a different subtask/attempt) takes over from
      there, unchanged.
    - No SpecCompliance goal-check. That check verifies concrete literal
      requirements (exact field/method/class names) in code just written -
      irrelevant for a subtask that writes no new code; whatever
      established files it's verifying already passed that check when an
      earlier, mutating subtask wrote them.

    Judgment caching (state.cached_run_verification_judgment) is preserved
    across this subtask's own retry attempts, exactly like the mutating
    path - a repeat attempt after a transient failure re-judges only when
    the workspace's invocation-affecting content actually changed."""
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
    if not autonomy_cfg_rv.run_verification_enabled or state.run_verification_declined:
        return

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
    validator.java_home_override = state.java_home_override

    known_files = sorted(set(state.all_files_written) | set(ctx.established_files))
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
            goal=ctx.goal, design=ctx.design,
            files_written=known_files, build_file_content=pom_content_for_judge,
        )
        if raw_judgment.get("infrastructure_error") and ctx.runtime_verification_required:
            message = (
                "VERIFICATION INFRASTRUCTURE FAILURE: runtime behavior is required, but "
                f"the runtime-verification judge was unavailable: {raw_judgment['infrastructure_error']}"
            )
            failure = Failure(
                type="verification_infrastructure_failure", message=message,
                raw_output=message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(failure.to_gate_outcome())
            raise QualityGateFailure(failure)
        state.cached_run_verification_judgment = downgrade_ungrounded_goal_explicit_commands(
            raw_judgment, ctx.goal
        )
        state.cached_run_verification_basis_hash = current_judgment_basis
    else:
        deps.logger.debug("Reusing cached run-verification judgment from an earlier attempt in this run.")
    judgment = state.cached_run_verification_judgment

    if ctx.runtime_verification_required and not judgment.get("should_run"):
        message = deps._required_runtime_verification_missing_message(judgment)
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    if deps._runtime_verification_is_advisory_only(ctx, judgment):
        deps.logger.info(
            "Run-verification judge inferred should_run=True, but this subtask's own "
            "approved plan declares no runtime-verification obligation - treating as "
            "advisory only, not executing (a later subtask that DOES own this "
            "obligation still runs it for real)."
        )
        return
    if not judgment.get("should_run"):
        return
    execution_mode, execution_mode_error = deps._resolve_execution_mode(judgment)
    if execution_mode_error:
        message = f"MANAGED_SERVICE_CONTRACT_INVALID: {execution_mode_error}"
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    if execution_mode == "managed_service":
        validator.tree_binding = deps._bind_verification_tree(state, ctx)  # D2 S4.1: the service is verified against the bound tree, like every other gate here
        await deps._execute_managed_service_verification(state, ctx, judgment, known_files, validator)
        return
    if deterministic_sequence_kind(judgment.get("run_commands") or []) == "build":
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

    if judgment.get("run_commands"):
        pom_path_for_correction = os.path.join(ctx.worktree_path, "pom.xml")
        pom_content_for_correction = None
        try:
            with open(pom_path_for_correction, "r", encoding="utf-8") as f:
                pom_content_for_correction = f.read()
        except Exception:
            pass
        java_files = [f for f in known_files if f.endswith(".java")]
        if java_files:
            # A pom.xml with real <dependency> entries needs Maven's own
            # classpath resolution - the direct-javac grounded route below
            # has no mechanism to resolve third-party jars and would compile
            # with a bare classpath, breaking any dependency import. Only
            # prefer the grounded route when there's no pom, or the pom has
            # no dependencies to lose (a bare pom.xml with no libraries is
            # exactly as safe as no build file at all).
            pom_declares_dependencies = bool(
                pom_content_for_correction and get_pom_dependencies(pom_path_for_correction)
            )
            corrected_commands = ground_java_entrypoint_in_no_build_file_projects(
                judgment["run_commands"], judgment["command_source"], known_files,
                deps._build_java_main_class_map(java_files, ctx),
                extract_jvm_module_flags(ctx.skills_prompt), pom_content_for_correction,
                prefer_grounded_runtime=not pom_declares_dependencies,
            )
            if corrected_commands is None:
                deps.logger.info(
                    "Deterministic Java entrypoint resolution: no pom.xml/build.gradle found "
                    "and no known .java file has a main() method - overriding should_run to "
                    "False instead of executing a command that targets a nonexistent entrypoint class."
                )
                judgment = dict(judgment)
                judgment["should_run"] = False
                judgment["run_commands"] = None
            elif corrected_commands != judgment["run_commands"]:
                judgment = dict(judgment)
                judgment["run_commands"] = corrected_commands

        # VER-005 implementation (2026-09-13): the Python sibling of the
        # Java entrypoint grounding just above - see ground_python_runtime_
        # target()'s own docstring (kriya/workflow/file_resolution.py) for
        # the live E4 defect this closes and why Python facts are resolved
        # repository-wide rather than scoped to known_files. Independent of
        # the Java branch above (different commands, never both touched by
        # the same corrected_commands variable), so no interaction with it.
        if any(f.endswith(".py") for f in known_files) or validator.stack == "python":
            all_python_files, package_dirs, entrypoint_files = deps._build_python_runtime_grounding(
                validator.workspace_path, state.all_files_written   # W3-3 U1: DECLARED = tracked or proven written by THIS run
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
                    "entrypoint exists elsewhere in the repository to substitute - overriding "
                    "should_run to False instead of executing an invalid target."
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

    if not judgment.get("should_run"):
        return

    proceed_with_run = True
    # The approval is bound to the exact command set (fingerprint), not to "this run was approved once": a retry that re-judged DIFFERENT commands is asked again.
    if deps._runtime_commands_need_approval(ctx, state, judgment, autonomy_cfg_rv.mode):
        if ctx.approval_callback:
            proceed_with_run = await deps._approve_runtime_commands(
                ctx, state, judgment,
                "Kriya judged that this goal describes runtime behavior compile/test checks can't verify, and wants to actually run the generated app (verification-only subtask):")
        else:
            # Fail-closed (2026-09-20) - mirrors workflow.py's own code-application approval gate fix for the IDENTICAL shape (2026-08-16 adversarial review Finding 2: "this gate
            # used to fail OPEN... execution silently fell through... with no approval ever having been requested"). human-in-the-loop mode's entire purpose is preventing
            # exactly this - a real command executing inside the sandboxed worktree with zero human involvement because no approval_callback happened to be wired for this
            # call, previously treated as "proceed under default policy" (fail open).
            deps.logger.warning(
                "Runtime verification warrants human approval but no approval_callback "
                "is available - refusing to execute unreviewed rather than proceeding "
                "under default policy."
            )
            proceed_with_run = False
        if not proceed_with_run:
            state.run_verification_declined = True
            deps._raise_if_required_runtime_verification_declined(state, ctx)
    if not proceed_with_run:
        return

    resolved_run_commands = [deps._resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
    input_channel = judgment.get("input_channel") or "none"
    resolved_run_commands, stdin_payload, contract_incomplete_reason = deps._apply_runtime_verification_contract(
        resolved_run_commands, input_channel,
    )
    deps.logger.info(
        "RUNTIME_VERIFICATION_CONTRACT input_channel=%s argument_count=%d stdin_present=%s",
        input_channel, len(resolved_run_commands[-1]) if resolved_run_commands else 0,
        bool(stdin_payload),
    )
    if contract_incomplete_reason:
        message = f"RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE: {contract_incomplete_reason}"
        deps.logger.warning(message)
        failure = Failure(
            type="verification_infrastructure_failure", message=message,
            raw_output=message, attempt=state.attempt_number,
        )
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    # FILE-INTEGRITY-CONTRACT-001: pom corrections are candidate mutations,
    # applied before the first gate of this attempt, never between gates.
    deps._apply_candidate_pom_corrections(state, ctx, sorted(state.all_files_written | set(ctx.established_files)))
    validator.tree_binding = deps._bind_verification_tree(state, ctx)
    command_verification_kind = deterministic_sequence_kind(resolved_run_commands)
    deps.logger.info(
        "Quality Gates: Running %s verification (verification-only subtask): "
        % (command_verification_kind or "application_runtime")
        + " && ".join(" ".join(cmd) for cmd in resolved_run_commands)
    )
    pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
    _run_app_sequence_started = deps.time.monotonic()
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):  # OI-142: the build the command needs and the run that consumes it share one view
        deps._prepare_finite_command_runtime_artifacts(resolved_run_commands, validator, ctx, state)
        run_res = validator.run_app_sequence(
            resolved_run_commands, timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
            stdin_payload=stdin_payload,
        )
    # R1 Deliverable 5 - observational only. kind reflects the deterministic
    # command classification already computed above (command_verification_
    # kind), matching gate_type's own "test" vs "run_verification" split
    # elsewhere in this function - so this coarse timing bucket lines up
    # with the same distinction INV-RUNTIME-002's own evidence-matching
    # fix relies on, not a new taxonomy.
    state.validator_timings.append({
        "kind": command_verification_kind or "run_verification",
        "duration_seconds": deps.time.monotonic() - _run_app_sequence_started,
        "success": not bool(run_res.get("timed_out")),
    })
    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
    deps._raise_runtime_verification_infrastructure_failure(
        state, run_res, resolved_run_commands,
    )

    gate_type = "test" if command_verification_kind == "test" else "run_verification"
    verification_authority = "llm"
    if run_res["timed_out"]:
        state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
        grade, verification_authority = await deps._resolve_runtime_verification_grade(
            ctx, state_, contract_verdict,
            {
                "goal": ctx.goal, "success_criteria": judgment["success_criteria"],
                "output": run_res["output"], "returncode": run_res["returncode"],
                "files_written": known_files, "timed_out": True,
            },
            run_result=run_res,
        )
        timeout_s = autonomy_cfg_rv.run_verification_timeout_seconds
        if grade["passed"]:
            gate_type = "run_verification_hung"
            grade["reasoning"] = (
                f"The goal's described output WAS produced correctly, but the process never "
                f"exited on its own and had to be killed after {timeout_s}s. This is still a "
                "real defect - almost always an unclosed resource keeping the process alive "
                f"after all application logic already finished. Grader's evidence: {grade['reasoning']}"
            )
        else:
            grade["reasoning"] = (
                f"Run timed out after {timeout_s}s, and the output captured before the forced "
                f"kill does not show the goal was achieved either: {grade['reasoning']}"
            )
        grade["passed"] = False
    elif not run_res["success"]:
        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
        if deterministic_kind is not None:
            verification_authority = "process_exit"
            grade = {
                "passed": False,
                "reasoning": (
                    f"One or more deterministic {deterministic_kind} verification commands "
                    "returned a non-zero process status."
                ),
                "likely_files": [],
            }
        else:
            state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
            grade, verification_authority = await deps._resolve_runtime_verification_grade(
                ctx, state_, contract_verdict,
                {
                    "goal": ctx.goal, "success_criteria": judgment["success_criteria"],
                    "output": run_res["output"], "returncode": run_res["returncode"],
                    "files_written": known_files,
                },
                run_result=run_res,
            )
        # PRD-025: a nonzero exit outranks any semantic grade unless the
        # user's goal declares that exit as expected from a launched app.
        deps.apply_runtime_disposition(
            grade, run_res, goal_text=deps.exit_authority_text(ctx),
            verification_authority=verification_authority,
        )
    else:
        deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
        if deterministic_kind is not None:
            verification_authority = "process_exit"
            grade = {
                "passed": True,
                "reasoning": (
                    f"All deterministic {deterministic_kind} verification commands completed "
                    "successfully (exit code 0)."
                ),
                "likely_files": [],
            }
        else:
            state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, known_files)
            grade, verification_authority = await deps._resolve_runtime_verification_grade(
                ctx, state_, contract_verdict,
                {
                    "goal": ctx.goal, "success_criteria": judgment["success_criteria"],
                    "output": run_res["output"], "returncode": run_res["returncode"],
                    "files_written": known_files,
                },
                run_result=run_res,
            )

    deps.apply_runtime_disposition(
        grade, run_res, goal_text=deps.exit_authority_text(ctx),
        verification_authority=verification_authority,
    )
    if not grade["passed"]:
        message = (
            f"RUNTIME VERIFICATION FAILURE (verification-only subtask): {grade['reasoning']}"
            f"\n\nCaptured output:\n{run_res['output']}"
        )
        enriched_output = run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}"
        failure = _build_quality_gate_failure(
            gate_type, message, enriched_output, ctx.worktree_path, known_files,
            state.attempt_number, extra_likely_files=grade.get("likely_files") or [],
        )
        failure_outcome = failure.to_gate_outcome()
        failure_outcome.update({
            "graded_by": verification_authority, "commands": resolved_run_commands,
            "steps": run_res.get("steps", []),
            "deterministic_result": deps._deterministic_result_provenance_field(verification_authority, grade["passed"]),
            **deps.runtime_evidence_outcome_fields(grade),
        })
        state.record_gate_outcome(failure_outcome)
        raise QualityGateFailure(failure)

    state.record_gate_outcome({
        "attempt": state.attempt_number, "type": gate_type, "success": True,
        "output": run_res["output"] + f"\n\n[Grader reasoning]: {grade['reasoning']}",
        "graded_by": verification_authority, "commands": resolved_run_commands,
        "steps": run_res.get("steps", []),
        "deterministic_result": deps._deterministic_result_provenance_field(verification_authority, grade["passed"]),
        **deps.runtime_evidence_outcome_fields(grade),
        **execution_evidence(run_res),
    })


async def _approve_and_execute_runtime_verification(
    deps: RuntimeDeps, state: RunState, ctx: AttemptCtx, validator: Any, active_code_context: str,
    judgment: Dict[str, Any], autonomy_cfg_rv: Any,
) -> None:
    """The approve-and-execute half of the runtime-verification gate, for a judgment that says ``should_run``: the runtime-command review, the execution of exactly the reviewed commands,
    the grading of the captured output and the recorded outcome. Extracted unchanged from ``run_attempt`` so that, in human-in-the-loop mode, it can run AFTER the person has reviewed
    and approved the candidate source (``defer_execution_until_source_review``): the commands then run against bytes the person has seen.
    After source approval (``ctx.post_source_review_guard`` set) the candidate must not change: the guard is consulted immediately before execution and the self-correction
    micro-loop, which edits candidate files, is not entered - a failing run is repaired by the ordinary retry loop, whose new candidate needs a new source review."""
    proceed_with_run = True
    if deps._runtime_commands_need_approval(ctx, state, judgment, autonomy_cfg_rv.mode):
        if ctx.approval_callback:
            proceed_with_run = await deps._approve_runtime_commands(
                ctx, state, judgment,
                "Kriya judged that this goal describes runtime behavior compile/test checks can't verify, and wants to actually run the generated app:")
        else:
            # Fail-closed (2026-09-20) - same fix as the sibling gate in _execute_runtime_verification_directly() above; see that call site's own comment for
            # the full 2026-08-16 precedent.
            deps.logger.warning(
                "Runtime verification warrants human approval but no "
                "approval_callback is available - refusing to execute "
                "unreviewed rather than proceeding under default policy."
            )
            proceed_with_run = False
        if not proceed_with_run:
            state.run_verification_declined = True
            deps._raise_if_required_runtime_verification_declined(state, ctx)
    if proceed_with_run:
        resolved_run_commands = [deps._resolve_run_command(cmd, ctx.worktree_path) for cmd in judgment["run_commands"]]
        if resolved_run_commands != judgment["run_commands"]:
            deps.logger.info(
                "One or more inferred run commands aren't resolvable as given here - "
                "substituted Kriya's own interpreter/PATH-resolved equivalents."
            )
        rv_input_channel = judgment.get("input_channel") or "none"
        resolved_run_commands, rv_stdin_payload, rv_contract_incomplete_reason = (
            deps._apply_runtime_verification_contract(resolved_run_commands, rv_input_channel)
        )
        deps.logger.info(
            "RUNTIME_VERIFICATION_CONTRACT input_channel=%s argument_count=%d stdin_present=%s",
            rv_input_channel, len(resolved_run_commands[-1]) if resolved_run_commands else 0,
            bool(rv_stdin_payload),
        )
        if rv_contract_incomplete_reason:
            rv_message = f"RUNTIME_VERIFICATION_CONTRACT_INCOMPLETE: {rv_contract_incomplete_reason}"
            deps.logger.warning(rv_message)
            rv_failure = Failure(
                type="verification_infrastructure_failure", message=rv_message,
                raw_output=rv_message, attempt=state.attempt_number,
            )
            state.record_gate_outcome(rv_failure.to_gate_outcome())
            raise QualityGateFailure(rv_failure)
        command_verification_kind = deterministic_sequence_kind(resolved_run_commands)
        deps.logger.info(
            "Quality Gates: Running %s verification: "
            % (command_verification_kind or "application_runtime")
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
        deps.assert_candidate_unchanged_since_source_review(state, ctx)  # before Maven prepares anything for the reviewed command
        pre_run_untracked = snapshot_untracked_files(ctx.worktree_path)
        with validator.verification_session(RUNTIME_VERIFICATION_GATE):  # OI-142: the build and the run that consumes it share one view
            deps._prepare_finite_command_runtime_artifacts(resolved_run_commands, validator, ctx, state)
            deps.assert_candidate_unchanged_since_source_review(state, ctx)
            run_res = validator.run_app_sequence(
                resolved_run_commands,
                timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                stdin_payload=rv_stdin_payload,
            )
        clean_untracked_files_since(ctx.worktree_path, pre_run_untracked)
        deps._raise_runtime_verification_infrastructure_failure(
            state, run_res, resolved_run_commands,
        )
        gate_type = (
            "test" if command_verification_kind == "test"
            else "run_verification"
        )
        # Set unconditionally (only ever reassigned in the "plain
        # nonzero exit, no hang" branch below - see its own
        # comment for why the other two branches are deliberately
        # NOT self-corrected) so the shared failure-raising block
        # further down can attach it to the Failure regardless of
        # which branch actually ran, mirroring the compile gate's
        # own self_correction_attempt pattern above.
        self_correction_result = None
        verification_authority = "llm"
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
            state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
            if state_ in (ContractVerdictState.PASS, ContractVerdictState.FAIL):
                deps.logger.info(
                    "Runtime verification: using deterministic verification-contract "
                    "marker instead of LLM grading (timed-out run)."
                )
            grade, verification_authority = await deps._resolve_runtime_verification_grade(
                ctx, state_, contract_verdict,
                {
                    "goal": ctx.goal,
                    "success_criteria": judgment["success_criteria"],
                    "output": run_res["output"],
                    "returncode": run_res["returncode"],
                    "files_written": list(state.all_files_written),
                    "timed_out": True,
                },
                run_result=run_res,
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
            deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
            if deterministic_kind is not None:
                verification_authority = "process_exit"
                grade = {
                    "passed": False,
                    "reasoning": (
                        f"One or more deterministic {deterministic_kind} verification "
                        "commands returned a non-zero process status."
                    ),
                    "likely_files": [],
                }
            else:
                state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                if state_ in (ContractVerdictState.PASS, ContractVerdictState.FAIL):
                    deps.logger.info(
                        "Runtime verification: using deterministic verification-contract "
                        "marker instead of LLM grading (non-zero exit, no hang)."
                    )
                grade, verification_authority = await deps._resolve_runtime_verification_grade(
                    ctx, state_, contract_verdict,
                    {
                        "goal": ctx.goal,
                        "success_criteria": judgment["success_criteria"],
                        "output": run_res["output"],
                        "returncode": run_res["returncode"],
                        "files_written": list(state.all_files_written),
                    },
                    run_result=run_res,
                )

            # A semantic expected-failure verdict is admissible
            # only when every setup step succeeded and the final
            # application process actually launched. JVM/module/
            # executable launch failures were already classified
            # as verifier infrastructure above and never reach
            # this branch.
            #
            # PRD-025: beyond that, a nonzero exit outranks any
            # semantic grade unless the user's own goal declares
            # that exit as the expected behaviour.
            deps.apply_runtime_disposition(
                grade, run_res, goal_text=deps.exit_authority_text(ctx),
                verification_authority=verification_authority,
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
            if not grade["passed"] and ctx.kernel.config.autonomy.self_correction_loop_enabled and ctx.post_source_review_guard is None:
                run_self_correction_loop = deps.load_run_self_correction_loop()
                deps.logger.info(
                    "Runtime verification failed with a plain nonzero exit (no hang) - "
                    "attempting bounded self-correction micro-loop before raising "
                    "QualityGateFailure."
                )
                run_verification_known_files = sorted(
                    set(state.all_files_written) | set(ctx.established_files)
                )
                self_correction_result = await deps._as_developer(run_self_correction_loop(
                    llm=ctx.developer.llm,
                    worktree_path=ctx.worktree_path,
                    validator=validator,
                    files_in_scope=run_verification_known_files,
                    # See the compile-gate self-correction call
                    # site's own comment above for why DENY_ALL is
                    # checked explicitly here too.
                    writable_files=(
                        [] if ctx.write_scope_mode == WriteScopeMode.DENY_ALL
                        else (ctx.allowed_write_relpaths or list(state.all_files_written))
                    ),
                    compile_error_output=(
                        f"RUNTIME VERIFICATION FAILURE (plain nonzero exit): {grade['reasoning']}"
                        f"\n\nCaptured output:\n{run_res['output']}"
                    ),
                    active_code_context=active_code_context,
                    max_turns=ctx.kernel.config.autonomy.self_correction_loop_max_turns,
                    failure_type="run_verification",
                    authorized_semantic_regions=ctx.authorized_semantic_regions,
                    strict_existing_java_files=ctx.kernel.config.autonomy.semantic_region_enforcement_required,
                    baseline_contents=state.all_original_contents,
                    request_capacity=deps.request_capacity(ctx.kernel.config),
                ))
                deps._record_self_correction_scope_conflict(
                    state, ctx, self_correction_result, "run_verification",
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
                    deps.logger.info(
                        "Self-correction micro-loop resolved the run-verification "
                        f"infrastructure issue in {self_correction_result.turns_used} "
                        "turn(s) - re-running the actual application to confirm."
                    )
                    pre_run_untracked_after_repair = snapshot_untracked_files(ctx.worktree_path)
                    with validator.verification_session(RUNTIME_VERIFICATION_GATE):  # OI-142: the build and the run share one view
                        deps._prepare_finite_command_runtime_artifacts(resolved_run_commands, validator, ctx, state)
                        run_res = validator.run_app_sequence(
                            resolved_run_commands,
                            timeout=autonomy_cfg_rv.run_verification_timeout_seconds,
                            # The reviewed subject binds the stdin digest: the re-run executes the SAME input as the first run.
                            stdin_payload=rv_stdin_payload,
                        )
                    clean_untracked_files_since(ctx.worktree_path, pre_run_untracked_after_repair)
                    # RUNTIME-VERIFICATION-ENV-STOP-001 residual (focused review
                    # 2026-10-10, F1): the repair may have changed what the run
                    # REQUIRES (a declared dependency), so this re-run's result can
                    # be the typed environment result just like the first run's -
                    # nothing launched, no behaviour to grade. The same single typed
                    # stop the first run takes (structured reason code only, never
                    # output text), before the deterministic exit status or the
                    # grader ever reads it; a re-run that executed is graded below
                    # exactly as before.
                    deps._stop_on_environment_gate_result(state, run_res, "runtime")
                    repaired_deterministic_kind = deterministic_sequence_kind(
                        resolved_run_commands
                    )
                    if repaired_deterministic_kind is not None:
                        verification_authority = "process_exit"
                        repaired_reasoning = (
                            f"All deterministic {repaired_deterministic_kind} "
                            "verification commands completed successfully (exit code 0)."
                            if run_res["success"] else
                            f"One or more deterministic {repaired_deterministic_kind} "
                            "verification commands still returned a non-zero process "
                            "status after repair."
                        )
                        grade = {
                            "passed": bool(run_res["success"]),
                            "reasoning": repaired_reasoning,
                            "likely_files": [],
                        }
                    else:
                        state_, contract_verdict = deps._classify_grounded_contract_verdict(
                            run_res["output"], ctx.worktree_path, list(state.all_files_written),
                        )
                        grade, verification_authority = await deps._resolve_runtime_verification_grade(
                            ctx, state_, contract_verdict,
                            {
                                "goal": ctx.goal,
                                "success_criteria": judgment["success_criteria"],
                                "output": run_res["output"],
                                "returncode": run_res["returncode"],
                                "files_written": list(state.all_files_written),
                            },
                            run_result=run_res,
                        )
        else:
            deterministic_kind = deterministic_sequence_kind(resolved_run_commands)
            if deterministic_kind is not None:
                # An allowlisted build/test tool's zero exit status
                # is its authoritative verdict. Quiet output is a
                # normal success mode, not evidence of missing
                # runtime behavior for an LLM to reinterpret.
                verification_authority = "process_exit"
                grade = {
                    "passed": True,
                    "reasoning": (
                        f"All deterministic {deterministic_kind} verification "
                        "commands completed successfully (exit code 0)."
                    ),
                    "likely_files": [],
                }
                deps.logger.info(
                    "Runtime verification: trusting deterministic %s command "
                    "process status instead of behavioral LLM grading.",
                    deterministic_kind,
                )
            else:
                state_, contract_verdict = deps._classify_grounded_contract_verdict(run_res["output"], ctx.worktree_path, list(state.all_files_written))
                if state_ in (ContractVerdictState.PASS, ContractVerdictState.FAIL):
                    deps.logger.info(
                        "Runtime verification: using deterministic verification-contract "
                        "marker instead of LLM grading."
                    )
                grade, verification_authority = await deps._resolve_runtime_verification_grade(
                    ctx, state_, contract_verdict,
                    {
                        "goal": ctx.goal,
                        "success_criteria": judgment["success_criteria"],
                        "output": run_res["output"],
                        "returncode": run_res["returncode"],
                        "files_written": list(state.all_files_written),
                    },
                    run_result=run_res,
                )
        # PRD-025: the final disposition, re-applied after any
        # self-correction re-verification replaced the grade.
        deps.apply_runtime_disposition(
            grade, run_res, goal_text=deps.exit_authority_text(ctx),
            verification_authority=verification_authority,
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
            failure_outcome = failure.to_gate_outcome()
            failure_outcome.update({
                "graded_by": verification_authority,
                "commands": resolved_run_commands,
                "steps": run_res.get("steps", []),
                "deterministic_result": deps._deterministic_result_provenance_field(verification_authority, grade["passed"]),
                **deps.runtime_evidence_outcome_fields(grade),
            })
            state.record_gate_outcome(failure_outcome)
            raise QualityGateFailure(failure)
        run_verification_outcome = {
            "attempt": state.attempt_number,
            "type": gate_type,
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
            "graded_by": verification_authority,
            "commands": resolved_run_commands,
            "steps": run_res.get("steps", []),
            "deterministic_result": deps._deterministic_result_provenance_field(verification_authority, grade["passed"]),
            **deps.runtime_evidence_outcome_fields(grade),
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
        state.record_gate_outcome(run_verification_outcome)
        deps.logger.info(f"Quality Gates: Runtime verification PASSED: {grade['reasoning']}")
        # A passing real-world run is exactly the proof the
        # skill-verification gap check is looking for - every
        # skill that contributed to this generation earns a
        # verification so future runs stop asking about it.
        # P-B: it is only HELD here (state.pending_skill_verifications).
        # Persisting it commits into the git work tree holding the
        # skill - by default the run's own workspace repository -
        # which would move that repository's HEAD under every gate
        # still to run on this candidate (GIT_METADATA_STALE) and
        # claim a verification before the candidate's terminal
        # verification exists. The run's owner persists it after the
        # terminal apply (run_generation_workflow / the enforce
        # terminal commit).
        for active_skill_name in ctx.active_skills:
            try:
                active_skill_obj = ctx.skill_engine.get_skill(active_skill_name)
                context = deps._skill_verification_context(active_skill_obj, ctx.goal)
                # The per-rule provenance flips later for exactly the
                # rules that were part of this skill when this
                # run's context was built (the pre-retry-loop
                # snapshot) - not whatever rules.txt contains now.
                snapshot = ctx.active_skill_rules_snapshot.get(active_skill_name)
                held_skill_verification = deps.load_held_skill_verification()
                state.pending_skill_verifications[active_skill_name] = held_skill_verification(
                    skill_name=active_skill_name, context=context,
                    rule_texts=tuple(snapshot) if snapshot is not None else None,
                )
            except Exception as ex:
                deps.logger.debug(f"Failed to hold skill '{active_skill_name}' verification: {ex}")
