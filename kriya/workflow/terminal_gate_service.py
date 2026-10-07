"""The enforce run's terminal global gates (PRD-030).

Once every subtask of the current plan has completed, the verified candidate
must pass every blocking terminal gate before anything reaches the real
workspace (PRD-004). The gates run in a fixed order, and each emits one
``terminal_gate_outcome`` event:

1. migration: the goal's migration obligation, judged on the final state;
2. stack_contract: the goal's stack contract against every planned artifact;
3. preserved_references: every preserved reference still byte-identical;
4. static_analysis (PRD-031A): the provider-neutral static-analysis gate
   (kriya/static_analysis/service.py) on the exact batch the commit will
   write. Deterministic, like 1-3, and before the model-backed requirement
   gate. Its status is the outcome's (passed, passed_with_warnings,
   accepted_risk, failed, unknown, unavailable, disabled - never "passed"
   for disabled);
5. terminal_obligations: the MA8 terminal aggregation over the ledger;
6. original_requirements: the user's own requirements (PRD-020), judged by
   the verifier and closed by deterministic evidence where it exists;
7. artifact_registry: the candidate's artifact facts, derived for recording
   after the commit.

A gate that raises is a failed gate with an INDETERMINATE message, never a
pass. :class:`TerminalGateService` only reads the real workspace (the
artifact registry and the run's committed history). It writes obligation
records to the ledger it is given, and nothing to the workspace. The commit
belongs to kriya/workflow/commit_service.py, which accepts only a
commit-eligible report from here.

The validators are injected (:class:`TerminalGateValidators`). The
controller builds them from its own module names on every run, which keeps
it the single place they are bound, and lets this service be exercised
without a WorkflowEngine.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from kriya.control.artifacts import ArtifactRegistry
from kriya.control.persistence import load_artifact_registry
from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.static_analysis.service import StaticAnalysisCandidate, StaticAnalysisGateResult, banner
from kriya.workflow.edit_safety import (
    CandidateMaterializationError,
    StagedFileWrite,
    content_revision,
    read_file_revision,
)
from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus, MigrationValidationScope
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import EngineeringPlan, FileAction
from kriya.workflow.requirements import (
    REQUIREMENTS_UNRESOLVED,
    RequirementSet,
    record_requirement_verdicts,
    verifier_result_verdicts,
)
from kriya.workflow.static_checks import derive_stack_contract, log_stack_contract_boundary
from kriya.workflow.verification_binding import CandidateVerificationBinding, bind_candidate

logger = logging.getLogger(__name__)

GateOutcomeEmitter = Callable[[str, str, Optional[str]], Awaitable[None]]


def _terminal_candidate_paths(plan: EngineeringPlan) -> List[str]:
    """Every path the plan leaves in the candidate (its final action is
    not a delete), in plan order."""
    final_action: Dict[str, FileAction] = {}
    for subtask in plan.subtasks:
        for planned in subtask.planned_files:
            final_action[planned.path] = planned.action
    return [path for path, action in final_action.items() if action != FileAction.DELETE]


async def _verify_original_requirements(
    spec_compliance: Any, requirements: RequirementSet, goal: str, candidate_root: str,
    paths: List[str], ledger: ObligationLedger, baseline_root: Optional[str] = None,
) -> List[str]:
    """PRD-020: one verifier pass over the whole candidate, recording each
    original requirement's outcome (UNKNOWN when the verifier gave none)
    with the candidate's content fingerprint as its evidence id. Returns
    the verifier findings (an unavailable verdict, e.g. a call PRD-016
    refused for size, invented ids) for the gate's message."""
    contents: Dict[str, str] = {}
    baselines: Dict[str, Optional[str]] = {}
    for path in paths:
        full = os.path.join(candidate_root, path)
        if os.path.isfile(full):
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                contents[path] = handle.read()
            before = os.path.join(baseline_root, path) if baseline_root else None
            if before and os.path.isfile(before):
                with open(before, "r", encoding="utf-8", errors="replace") as handle:
                    baselines[path] = handle.read()
            else:
                baselines[path] = None
    files = sorted(contents)
    fingerprint = content_revision(
        goal + "\x00" + "\x00".join(f"{path}\x01{contents[path]}" for path in files))
    result = await spec_compliance.check(
        goal=goal, files_written=files, file_contents=contents, requirements=requirements,
        **({"baseline_contents": baselines} if baseline_root else {}),
    )
    # MODEL-EVIDENCE-HARDENING-001: every verdict with its reason code and
    # the verifier's identity; an id without one says why there is none.
    verdicts, findings, missing_reason, missing_detail = verifier_result_verdicts(result, requirements)
    if findings:
        logger.warning("Original requirement verification findings: %s", findings)
    record_requirement_verdicts(
        ledger, requirements, verdicts, revision="terminal", evidence_fingerprint=fingerprint,
        source="workflow_controller.terminal_requirements",
        gate_evidence=["enforce terminal gates: every subtask verified"],
        missing_reason=missing_reason, missing_detail=missing_detail,
        verifier=(result or {}).get("verifier"),
    )
    return findings


def enforce_preserved_reference_terminal_integrity(
    obligation_ledger: ObligationLedger, workspace_path: str,
) -> None:
    """PRV-11 preservation extension (2026-09-06/07, Production Validation
    P2): the terminal half of ObligationKind.PRESERVED_REFERENCE - see that
    kind's own docstring (kriya/workflow/obligations.py) for why this
    re-hash, not a heuristic, is the enforcement mechanism.

    Re-checks only currently-SATISFIED records: a record already VIOLATED
    or PENDING for some other reason needs no further evidence to already
    disqualify the run via unresolved_terminal_obligations(); this
    function's only job is catching the case that check alone cannot -
    a preservation that was genuinely honored at plan-acceptance time but
    silently violated somewhere during generation."""
    for rec in obligation_ledger.current_by_kind(ObligationKind.PRESERVED_REFERENCE):
        if rec.status != ObligationStatus.SATISFIED:
            continue
        target = rec.evidence.get("target")
        baseline_hash = rec.evidence.get("baseline_hash")
        if not target or baseline_hash is None:
            continue
        current_hash = read_file_revision(os.path.join(workspace_path, target))
        if current_hash != baseline_hash:
            obligation_ledger.record(ObligationRecord(
                id=rec.id, kind=ObligationKind.PRESERVED_REFERENCE,
                status=ObligationStatus.VIOLATED,
                authority=ObligationAuthority.DETERMINISTIC,
                description=rec.description,
                source="workflow_controller.enforce_preserved_reference_terminal_integrity",
                revision="terminal",
                evidence={**rec.evidence, "current_hash": current_hash},
                owner_subtask_id=rec.owner_subtask_id,
                terminal_required=True,
            ))


@dataclass(frozen=True)
class TerminalGateValidators:
    """The deterministic and verifier checks the gates delegate to."""

    find_migration_incomplete: Callable[..., Optional[Dict[str, Any]]]
    validate_stack_contract_artifacts: Callable[..., Optional[str]]
    enforce_preserved_reference_terminal_integrity: Callable[[ObligationLedger, str], None]
    blocking_requirements: Callable[..., List[Any]]
    verify_original_requirements: Callable[..., Awaitable[List[str]]]
    # PRD-031A: StaticAnalysisService(cfg).evaluate_candidate. None only in
    # callers that construct the service without it; the commit guard then
    # refuses a commit whenever static analysis is enabled (no evidence).
    evaluate_static_analysis: Optional[Callable[[StaticAnalysisCandidate], StaticAnalysisGateResult]] = None


@dataclass(frozen=True)
class TerminalGateRequest:
    """One run's terminal gate inputs. ``candidate_root`` is the isolated
    candidate every gate judges; ``workspace_path`` is the real workspace,
    only read (artifact registry, committed run history, test toolchain)."""

    plan: EngineeringPlan
    goal: str
    candidate_root: str
    workspace_path: str
    migration_resolution: MigrationResolution
    obligation_ledger: ObligationLedger
    requirement_set: RequirementSet
    # AutonomyConfig, or None when the engine carries no config.
    autonomy: Any
    spec_compliance: Any
    milestone_id: str
    # CANDIDATE-VERIFIED-DIGEST-BINDING-001: materializes the exact batch the
    # commit will write; bound before the first gate, whatever static
    # analysis is configured to do.
    commit_batch: Callable[[], List[StagedFileWrite]]
    # PRD-031A: the batch the commit will write, materialized only when
    # static analysis is enabled.
    static_analysis_candidate: Optional[StaticAnalysisCandidate] = None
    # FS-1C2 B2-a: the operator's acceptance file bound before generation
    # (kriya/workflow/acceptance_oracle.py AcceptanceArtifact), None when none.
    acceptance: Any = None
    # FS-1C2 B3: the operator's approval of that suite (acceptance_approval.py).
    acceptance_approval: Any = None


@dataclass(frozen=True)
class TerminalGateReport:
    """Each gate's failure message (None = passed), plus what the requirement
    and artifact gates produced. ``ran`` is False when the gates never ran
    (a subtask did not complete): such a report is never commit-eligible."""

    ran: bool
    migration_gap: Optional[str] = None
    stack_contract_gap: Optional[str] = None
    preserved_reference_gap: Optional[str] = None
    terminal_obligation_gap: Optional[str] = None
    requirement_gap: Optional[str] = None
    artifact_error: Optional[str] = None
    requirement_closure_attempts: Tuple[Dict[str, Any], ...] = ()
    candidate_derived_artifacts: Tuple[Any, ...] = ()
    # PRD-031A: the gate's result (None when it was not evaluated) and its
    # blocking message (None when the result permits the commit).
    static_analysis: Optional[StaticAnalysisGateResult] = None
    static_analysis_gap: Optional[str] = None
    # The binding of the batch these gates verified (None when it could not
    # be materialized: the commit then refuses, VERIFIED_CANDIDATE_EVIDENCE_MISSING).
    verified_candidate: Optional[CandidateVerificationBinding] = None

    @property
    def commit_eligible(self) -> bool:
        return self.ran and not any((
            self.migration_gap, self.stack_contract_gap, self.preserved_reference_gap,
            self.static_analysis_gap, self.terminal_obligation_gap, self.requirement_gap, self.artifact_error,
        ))

    def global_gaps(self) -> Tuple[Tuple[str, Optional[str]], ...]:
        """The result keys the enforce result reports each gap under."""
        return (
            ("global_migration_gap", self.migration_gap),
            ("global_stack_contract_gap", self.stack_contract_gap),
            ("global_preserved_reference_gap", self.preserved_reference_gap),
            ("global_static_analysis_gap", self.static_analysis_gap),
            ("global_terminal_obligation_gap", self.terminal_obligation_gap),
            ("global_requirement_gap", self.requirement_gap),
        )


TERMINAL_GATES_NOT_RUN = TerminalGateReport(ran=False)


def _record_terminal_report(report: TerminalGateReport) -> None:
    """LR-R1-M1 ``gate.result`` for each terminal gate, from the report the
    commit decision itself reads (observational)."""
    if attempt_evidence_scope.capture_mode() is None:
        return
    gates = (("migration", report.migration_gap), ("stack_contract", report.stack_contract_gap),
             ("preserved_references", report.preserved_reference_gap),
             ("static_analysis", report.static_analysis_gap),
             ("terminal_obligations", report.terminal_obligation_gap),
             ("original_requirements", report.requirement_gap), ("artifact_registry", report.artifact_error))
    for gate, gap in gates:
        attempt_evidence_scope.emit("gate.result", {
            "stage": "terminal", "gate": gate, "success": gap is None,
            "commit_eligible": report.commit_eligible}, content={"output": gap})


class TerminalGateService:
    """Runs the terminal gates in order against one candidate."""

    def __init__(self, validators: TerminalGateValidators) -> None:
        self._validators = validators

    async def run(self, request: TerminalGateRequest, emit_gate_outcome: GateOutcomeEmitter) -> TerminalGateReport:
        verified_candidate = self._bind_candidate(request)
        migration_gap = self._migration_gap(request)
        await emit_gate_outcome("migration", "failed" if migration_gap else "passed", migration_gap)

        stack_contract_gap = self._stack_contract_gap(request)
        await emit_gate_outcome("stack_contract", "failed" if stack_contract_gap else "passed", stack_contract_gap)

        preserved_reference_gap = self._preserved_reference_gap(request)
        await emit_gate_outcome(
            "preserved_references", "failed" if preserved_reference_gap else "passed", preserved_reference_gap,
        )

        static_analysis, static_analysis_gap = self._static_analysis(request)
        await emit_gate_outcome(
            "static_analysis",
            static_analysis.gate_status if static_analysis is not None
            else "failed" if static_analysis_gap else "not_evaluated",
            static_analysis_gap or (banner(static_analysis) if static_analysis is not None else None),
        )

        terminal_obligation_gap = self._terminal_obligation_gap(request)
        await emit_gate_outcome(
            "terminal_obligations", "failed" if terminal_obligation_gap else "passed", terminal_obligation_gap,
        )

        requirement_gap, closure_attempts = await self._requirement_gap(request, migration_gap)
        await emit_gate_outcome(
            "original_requirements", "failed" if requirement_gap else "passed", requirement_gap,
        )

        artifact_error: Optional[str] = None
        derived: Tuple[Any, ...] = ()
        try:
            derived = tuple(ArtifactRegistry.derive_from_workspace(
                load_artifact_registry(request.workspace_path), request.candidate_root, request.milestone_id,
            ))
        except Exception as error:
            artifact_error = str(error)
        await emit_gate_outcome("artifact_registry", "failed" if artifact_error else "passed", artifact_error)

        report = TerminalGateReport(
            ran=True, migration_gap=migration_gap, stack_contract_gap=stack_contract_gap,
            preserved_reference_gap=preserved_reference_gap, terminal_obligation_gap=terminal_obligation_gap,
            static_analysis=static_analysis, static_analysis_gap=static_analysis_gap,
            requirement_gap=requirement_gap, artifact_error=artifact_error,
            requirement_closure_attempts=tuple(closure_attempts), candidate_derived_artifacts=derived,
            verified_candidate=verified_candidate,
        )
        _record_terminal_report(report)
        return report

    @staticmethod
    def _bind_candidate(request: TerminalGateRequest) -> Optional[CandidateVerificationBinding]:
        """The binding of the batch the gates below verify. A batch that
        cannot be materialized has none; the commit refuses it."""
        try:
            return bind_candidate(request.commit_batch(), request.workspace_path)
        except CandidateMaterializationError:
            return None

    def _static_analysis(
        self, request: TerminalGateRequest,
    ) -> Tuple[Optional[StaticAnalysisGateResult], Optional[str]]:
        """(result, gap). The service fails closed internally (an error is
        UNKNOWN); a validator that raises anyway is a failed gate, never a
        pass. (None, None) only when the gate is not wired - the commit
        guard then refuses whenever static analysis is enabled."""
        evaluate = self._validators.evaluate_static_analysis
        if evaluate is None or request.static_analysis_candidate is None:
            return None, None
        try:
            result = evaluate(request.static_analysis_candidate)
        except Exception as error:
            return None, (
                "STATIC ANALYSIS INDETERMINATE (global final-state check): the static-analysis "
                f"gate itself raised ({type(error).__name__}: {error}) - refusing to report success."
            )
        return result, result.gap

    def _migration_gap(self, request: TerminalGateRequest) -> Optional[str]:
        resolution = request.migration_resolution
        ledger = request.obligation_ledger
        try:
            if resolution.status == MigrationResolutionStatus.RESOLVED:
                obligation = resolution.obligation
                gap = self._validators.find_migration_incomplete(
                    obligation, request.candidate_root,
                    validation_scope=MigrationValidationScope.TERMINAL,
                    obligation_ledger=ledger,
                    revision="terminal", source="migration.terminal_gate",
                ) if obligation else None
                if gap:
                    return (
                        "MIGRATION INCOMPLETE (global final-state check): the goal "
                        f"explicitly requires replacing {gap['source_identity']} with "
                        f"{gap['target_identity']}, but {', '.join(gap['reason_codes'])}."
                    )
            elif resolution.status == MigrationResolutionStatus.INDETERMINATE:
                if ledger is not None:
                    ledger.record(ObligationRecord(
                        id="migration.identity_resolution",
                        kind=ObligationKind.MIGRATION_COMPLETION,
                        status=ObligationStatus.INDETERMINATE,
                        authority=ObligationAuthority.DETERMINISTIC,
                        description="migration source/target dependency identity could not be "
                                    "resolved confidently from the immutable pre-mutation baseline",
                        source="migration.terminal_gate", revision="terminal",
                        evidence={"reason": resolution.reason},
                        terminal_required=True,
                    ))
                return (
                    "MIGRATION OBLIGATION INDETERMINATE (global final-state check): the goal "
                    "explicitly expresses replacement intent, but source/target dependency "
                    f"identity could not be resolved confidently ({resolution.reason}). "
                    "Refusing to report success on an unconfirmed migration obligation."
                )
        except Exception as error:
            return (
                "MIGRATION FINAL-STATE CHECK INDETERMINATE: the deterministic terminal "
                f"migration validator itself raised ({type(error).__name__}: {error}) - refusing to "
                "report success on an unverifiable terminal obligation rather than silently "
                "trusting the per-subtask gates."
            )
        return None

    def _stack_contract_gap(self, request: TerminalGateRequest) -> Optional[str]:
        try:
            contract = derive_stack_contract(request.goal)
            gap = self._validators.validate_stack_contract_artifacts(
                contract, (pf.path for st in request.plan.subtasks for pf in st.planned_files),
            )
            log_stack_contract_boundary("terminal", contract, gap)
            return gap
        except Exception as error:
            return f"STACK CONTRACT FINAL-STATE CHECK INDETERMINATE: {type(error).__name__}: {error}"

    def _preserved_reference_gap(self, request: TerminalGateRequest) -> Optional[str]:
        ledger = request.obligation_ledger
        try:
            self._validators.enforce_preserved_reference_terminal_integrity(ledger, request.candidate_root)
            violated = [
                record for record in ledger.current_by_kind(ObligationKind.PRESERVED_REFERENCE)
                if record.terminal_required and record.status != ObligationStatus.SATISFIED
            ]
            if violated:
                return "PRESERVED REFERENCES UNSATISFIED: " + "; ".join(
                    f"{record.id} ({record.status.value})" for record in violated
                )
        except Exception as error:
            return f"PRESERVED REFERENCE FINAL-STATE CHECK INDETERMINATE: {type(error).__name__}: {error}"
        return None

    @staticmethod
    def _terminal_obligation_gap(request: TerminalGateRequest) -> Optional[str]:
        try:
            unresolved = request.obligation_ledger.unresolved_terminal_obligations()
            if unresolved:
                return "TERMINAL OBLIGATIONS UNSATISFIED (MA8 global aggregation check): " + "; ".join(
                    f"{rec.id} ({rec.status.value}, authority={rec.authority.value})" for rec in unresolved
                )
        except Exception as error:
            return f"TERMINAL OBLIGATION AGGREGATION INDETERMINATE: {type(error).__name__}: {error}"
        return None

    async def _requirement_gap(
        self, request: TerminalGateRequest, migration_gap: Optional[str],
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """PRD-020: the user's original requirements, judged against the
        whole verified candidate by the verifier (never by the plan's own
        acceptance text), then the requirement policy decides."""
        closure_attempts: List[Dict[str, Any]] = []
        ledger, requirement_set, autonomy = request.obligation_ledger, request.requirement_set, request.autonomy
        try:
            verifier_findings: List[str] = []
            if autonomy is not None and autonomy.spec_compliance_enabled:
                verifier_findings = await self._validators.verify_original_requirements(
                    request.spec_compliance, requirement_set, request.goal,
                    request.candidate_root, _terminal_candidate_paths(request.plan), ledger,
                    baseline_root=request.workspace_path,
                )
                # "Do not modify any other file": decided from what this
                # final candidate (and the run's committed history)
                # actually changed, against the files the goal names.
                from kriya.policy.filesystem import WriteScopeMode
                from kriya.workflow.toolchain import toolchain_declaration_mutable
                from kriya.workflow.workflow import (
                    close_requirements_by_mutation_scope,
                    close_requirements_by_suite_preservation,
                    close_requirements_by_test_immutability,
                    close_requirements_with_acceptance_tests,
                    close_requirements_with_named_tests,
                )

                scope_closures = await asyncio.to_thread(
                    close_requirements_by_mutation_scope, ledger, requirement_set,
                    request.candidate_root, request.workspace_path,
                    candidate_paths=[planned.path for subtask in request.plan.subtasks
                                     for planned in subtask.planned_files],
                    revision="terminal",
                )
                if scope_closures:
                    logger.info("Original requirement mutation-scope evidence: %s", scope_closures)
                # An UNVERIFIED requirement naming existing tests: run
                # exactly those on this candidate (never a model citation).
                from kriya.workflow.file_integrity import VerificationTreeBinding
                from kriya.workflow.worktree import repository_content_paths

                # FILE-INTEGRITY-CONTRACT-001: the named tests run repository
                # code on the candidate; a tracked-content change fails this gate.
                tree_binding = VerificationTreeBinding(
                    request.candidate_root, repository_content_paths(request.workspace_path),
                    _terminal_candidate_paths(request.plan),
                )
                # FS-1C2 B2-a: the operator's acceptance file judges the
                # behaviour claims it covers on this final candidate.
                acceptance_closures = await asyncio.to_thread(
                    close_requirements_with_acceptance_tests, autonomy, ledger,
                    requirement_set, request.candidate_root, request.workspace_path,
                    acceptance=request.acceptance, approval=request.acceptance_approval,
                    modified=_terminal_candidate_paths(request.plan),
                    revision="terminal",
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        WriteScopeMode.DENY_ALL, (), request.plan,
                    ),
                    tree_binding=tree_binding,
                )
                if acceptance_closures:
                    logger.info("Original requirement acceptance evidence: %s", acceptance_closures)
                # D8: the terminal writes nothing; the toolchain authority is
                # the approved plan's (the same derivation its units used).
                closures = await asyncio.to_thread(
                    close_requirements_with_named_tests, autonomy, ledger,
                    requirement_set, request.candidate_root, request.workspace_path,
                    modified=_terminal_candidate_paths(request.plan), revision="terminal",
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        WriteScopeMode.DENY_ALL, (), request.plan,
                    ),
                    tree_binding=tree_binding,
                )
                if closures:
                    logger.info("Original requirement closure by named tests: %s", closures)
                # REQUIREMENT-CLOSURE-PLAIN-GOAL-001: the final candidate's
                # mutation record and its own complete, green full suite.
                immutability_closures = await asyncio.to_thread(
                    close_requirements_by_test_immutability, ledger, requirement_set,
                    request.candidate_root, request.workspace_path,
                    candidate_paths=_terminal_candidate_paths(request.plan), revision="terminal",
                )
                if immutability_closures:
                    logger.info("Original requirement test-immutability evidence: %s", immutability_closures)
                suite_closures = await asyncio.to_thread(
                    close_requirements_by_suite_preservation, autonomy, ledger,
                    requirement_set, request.candidate_root, request.workspace_path,
                    revision="terminal",
                    toolchain_declaration_mutable=toolchain_declaration_mutable(
                        WriteScopeMode.DENY_ALL, (), request.plan,
                    ),
                    tree_binding=tree_binding,
                )
                if suite_closures:
                    logger.info("Original requirement suite-preservation evidence: %s", suite_closures)
                closure_attempts = scope_closures + acceptance_closures + closures + immutability_closures + suite_closures
                # The terminal migration gate just judged this same final
                # candidate; a requirement stating the migration itself
                # is closed by it (attempt._close_requirements_by_migration_gate).
                if not migration_gap and requirement_set.requirements:
                    self._close_by_migration_gate(ledger, requirement_set)
            blocking = self._validators.blocking_requirements(
                ledger, requirement_set,
                unknown_policy=getattr(autonomy, "requirement_unknown_policy", "record"),
                unverified_policy=getattr(autonomy, "requirement_unverified_policy", "record"),
            )
            if blocking:
                gap = f"{REQUIREMENTS_UNRESOLVED}: " + "; ".join(
                    f"{req.id} ({outcome.value}): {req.text}" for req, outcome in blocking)
                if verifier_findings:
                    gap += " [verifier: " + "; ".join(verifier_findings) + "]"
                return gap, closure_attempts
        except Exception as error:
            return (
                f"{REQUIREMENTS_UNRESOLVED}: original requirement verification failed "
                f"({type(error).__name__}: {error}); refusing success without it."
            ), closure_attempts
        return None, closure_attempts

    @staticmethod
    def _close_by_migration_gate(ledger: ObligationLedger, requirement_set: RequirementSet) -> None:
        from types import SimpleNamespace

        from kriya.workflow.attempt import _close_requirements_by_migration_gate
        from kriya.workflow.requirements import requirement_obligation_id

        verdict = ledger.current(requirement_obligation_id(requirement_set.requirements[0].id))
        terminal_fingerprint = (verdict.evidence or {}).get("evidence_id") if verdict else None
        if terminal_fingerprint:
            _close_requirements_by_migration_gate(
                SimpleNamespace(attempt_number="terminal"),
                SimpleNamespace(requirement_set=requirement_set, obligation_ledger=ledger),
                terminal_fingerprint,
            )
