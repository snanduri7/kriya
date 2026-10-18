"""The controller's shadow run and the enforce run's trace row (full modularization W4, E12).

Moved verbatim from ``kriya/workflow/workflow_controller.py`` (``WorkflowController`` methods; ``execute``, which
chooses the migration mode and calls both, stays in the controller):

- ``ShadowMode.run`` (was ``WorkflowController._run_structured_shadow``): the observational shadow plan and
  SubtaskExecutor pass of ``migration_mode="shadow"`` - Planner call under its own role bucket, Stage A parse, plan
  validation, the structural TOOL hard stop, the first-non-completed stop, the in-memory DecisionLedger and the
  UNRESOLVED-heavy VerificationReport; never touches the real outcome (``execute`` swallows its failures);
- ``ShadowMode.write_enforce_trace`` (was ``WorkflowController._write_enforce_trace``): the one traces.db row of an
  enforce run (``<run_id>.enforce``), written by ``execute`` after every enforce outcome and before a propagating bug.
  It lives here, beside the shadow run, as the brief and the TARGET-MODULE-MAP row place it; its only caller is
  ``execute`` (twice), not the enforce loop (TRACED: AST call graph at b834d71).

Every decision, record and log line is unchanged, in the same order. ``WorkflowController`` keeps both method names
and signatures (forwarders), so ``execute``'s ``self._run_structured_shadow`` / ``self._write_enforce_trace`` calls
and the suite's class-attribute patches of them keep working. What arrives in ``ShadowDeps`` / ``TraceDeps``, resolved
by ``workflow_controller._shadow_deps(controller)`` / ``_trace_deps(controller)`` once, when the forwarder is entered:
the controller instance's ``workflow_engine`` and bound ``_build_context``; EVERY ``kriya.workflow.workflow_controller``
module-level name the bodies read (the suite patches ``parse_planner_structured_output``,
``build_engineering_plan_from_planner_output``, ``validate_plan``, ``canonicalize_planned_file_actions``,
``record_plan_created`` and ``subtask_executor`` there; the rest are all-import-cycle members or names the controller
imports through allowlisted edges); the controller's ``logger``; and a loader doing the trace writer's call-time import
of ``kriya.workflow.run_trace`` (all-import cycle). Annotations that would need such an import (``ControlState``,
``Decision``) are spelled ``Any``. This module never imports ``kriya.workflow.workflow_controller``, ``workflow``,
``attempt`` or ``kriya.cli``, nor any all-import-cycle module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow_types import SubtaskResult, VerificationReport


@dataclass(frozen=True)
class ShadowDeps:
    """What the moved shadow run may use, resolved through ``kriya.workflow.workflow_controller`` (its module names and
    the controller instance) by ``workflow_controller._shadow_deps(controller)`` once, when the forwarder is entered.
    Every field is required: a builder that omits one fails at construction, never at the first use.
    """

    # the controller instance's collaborators (its engine; its bound _build_context)
    workflow_engine: Any
    _build_context: Any
    # defined in kriya.workflow.workflow_controller
    SHADOW_PLANNER_ROLE: Any
    logger: Any
    # kriya.agents.contracts
    parse_planner_structured_output: Callable[..., Any]
    # kriya.control.decisions
    DecisionLedger: Any
    # kriya.workflow
    subtask_executor: Any
    # kriya.workflow.acceptance
    goal_requires_runtime_behavior: Callable[..., Any]
    # kriya.workflow.plan_schema
    ExecutionMethod: Any
    build_engineering_plan_from_planner_output: Callable[..., Any]
    # kriya.workflow.plan_validation (all-import cycle)
    canonicalize_planned_file_actions: Callable[..., Any]
    validate_plan: Callable[..., Any]
    # kriya.workflow.planner_repair (all-import cycle)
    classify_planner_outcome: Callable[..., Any]
    classify_structured_plan_parse_issue: Callable[..., Any]
    planner_response_model: Callable[..., Any]
    record_planner_outcome: Callable[..., Any]
    # kriya.workflow.static_checks
    derive_stack_contract: Callable[..., Any]
    log_stack_contract_boundary: Callable[..., Any]
    # kriya.workflow.subtask_checkpoint (all-import cycle)
    topological_subtask_order: Callable[..., Any]
    # kriya.workflow.subtask_context_projection
    project_for_subtask: Callable[..., Any]
    # kriya.workflow.subtask_telemetry (all-import cycle)
    record_context_package_for_subtask: Callable[..., Any]
    record_plan_created: Callable[..., Any]
    record_subtask_attempt: Callable[..., Any]
    record_undeclared_file_touch: Callable[..., Any]
    # kriya.workflow.verification_report
    build_verification_report: Callable[..., Any]
    # kriya.workflow.workflow_types
    SubtaskResult: Any
    SubtaskStatus: Any


@dataclass(frozen=True)
class TraceDeps:
    """What the moved enforce-trace writer may use, resolved by ``workflow_controller._trace_deps(controller)`` once,
    when the forwarder is entered. Every field is required.
    """

    # the controller instance's engine
    workflow_engine: Any
    # defined in kriya.workflow.workflow_controller
    logger: Any
    # function-local imports the owner may not do itself, done by the controller when the body reaches them
    load_write_outcome_trace: Callable[[], Any]


class ShadowMode:
    """The shadow run and the enforce run's trace row, moved verbatim from ``WorkflowController`` (E12)."""

    @staticmethod
    async def run(
        deps: ShadowDeps, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: Any,
    ) -> Tuple[
        Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Any, ...], Optional[VerificationReport], List[str],
    ]:
        """Builds a real EngineeringPlan and runs SubtaskExecutor against
        every subtask - purely observational, see this module's own
        docstring for why the result never touches the real outcome.
        Uses a plain `goal` prompt for the Planner call, NOT the richer,
        RAG/skills/conventions-assembled plan_prompt run_generation_workflow()
        builds internally - a real, honestly-tracked gap (not silently
        pretended away) between what this shadow path sees and what the
        legacy path actually sees. MA7.2 wires ContextOrchestrator (MA5.7)
        itself in (see _build_context below), but NOT the Graph RAG
        retrieval that feeds its raw_rag_context parameter in the legacy
        path - ContextOrchestrator's own docstring is explicit that
        retrieval is out of its reuse boundary (it composes ALREADY-
        RETRIEVED content, never re-implements the retrieval itself), and
        duplicating workflow.py's deeply embedded Graph RAG stage here
        would violate MA5's "preserve current execution core" constraint.
        So this shadow path's context is real strategy selection + real
        contract/artifact entries + real on-disk content of planned files,
        still without the hybrid vector/graph retrieval the legacy path's
        Developer actually sees - a narrower, honestly-labeled slice, not a
        parity claim.

        MA7.1: also drives subtask_telemetry (MA6.12) and
        build_verification_report (MA6.11) - both existed as pure,
        independently-tested functions since MA6 but were never actually
        CALLED anywhere, including this method, until now (MA7.0's
        reachability inventory flagged this as a second, smaller dead spot
        beyond WorkflowController's own lack of a caller). The returned
        DecisionLedger is in-memory only here - persisting it to
        .kriya/control/decisions.jsonl is left to a future increment once
        this path is more than observational (execute(), not this method,
        now persists ControlState itself - MA7.2 - but ContractRegistry/
        ArtifactRegistry are only READ here via _build_context, never
        written back: this path never derives new contracts/artifacts from
        the workspace or records changes to them, only surfaces whatever
        was already persisted by something else). The VerificationReport is necessarily
        UNRESOLVED-heavy in shadow mode: SubtaskExecutor never runs real
        compile/test/tool verification (see this module's own docstring),
        so build_verification_report is called with no tool_results/
        judgment_results - an honest reflection of what shadow mode can
        actually attest to, not a simulated pass."""
        notes: List[str] = []
        ledger = deps.DecisionLedger()

        # MODEL-EVIDENCE-HARDENING-001: shadow planning is observational, so
        # its calls and typed outcomes go to their own role bucket and never
        # reach the Planner's routing evidence.
        planner = deps.workflow_engine.planner
        plan_text = await planner.run(goal, metrics_role=deps.SHADOW_PLANNER_ROLE)
        shadow_model = deps.planner_response_model(planner)

        def shadow_outcome(codes: List[str], *, valid: bool = False) -> None:
            deps.record_planner_outcome(planner, deps.classify_planner_outcome(codes, valid=valid),
                                   model=shadow_model, role=deps.SHADOW_PLANNER_ROLE)

        structured_output, parse_issue = deps.parse_planner_structured_output(plan_text)
        if structured_output is None:
            shadow_outcome(deps.classify_structured_plan_parse_issue(parse_issue))
            notes.append(f"no structured plan: {parse_issue}")
            return None, (), (), None, notes

        plan = deps.build_engineering_plan_from_planner_output(structured_output, plan_id=run_id, kind=route.kind)
        if plan is None:
            shadow_outcome(["STRUCTURED_PLAN_EMPTY"])
            notes.append("structured output parsed but produced zero subtasks")
            return None, (), (), None, notes
        plan, _ = deps.canonicalize_planned_file_actions(plan, workspace_path)

        deps.record_plan_created(ledger, plan)

        kernel = getattr(deps.workflow_engine, "kernel", None)
        available_tool_names = None
        if kernel is not None:
            try:
                available_tool_names = kernel.registry.list_components("tool")
            except Exception as e:
                deps.logger.debug(f"WorkflowController shadow run {run_id!r}: could not list registered tools: {e}")

        shadow_stack_contract = deps.derive_stack_contract(goal)
        validation = await deps.validate_plan(
            plan, workspace_path=workspace_path, available_tool_names=available_tool_names,
            route=route, triage_service=deps.workflow_engine.engineering_triage,
            runtime_verification_required=deps.goal_requires_runtime_behavior(goal),
            stack_contract=shadow_stack_contract,
        )
        deps.log_stack_contract_boundary(
            "plan", shadow_stack_contract, None if validation.valid else "REJECT",
        )
        shadow_outcome(list(validation.reason_codes), valid=validation.valid)
        if not validation.valid:
            notes.append(f"plan failed validation: {validation.errors}")
            return plan, (), ledger.all(), None, notes

        context = await deps._build_context(goal, plan, workspace_path, route, control_context, control_state)

        results: List[SubtaskResult] = []
        for subtask_id in deps.topological_subtask_order(plan):
            subtask = plan.subtask_by_id(subtask_id)
            if subtask is None:
                continue

            if subtask.execution_method == deps.ExecutionMethod.TOOL:
                # DELIBERATE, EXPLICITLY-AUTHORIZED HARD STOP (2026-08-24) -
                # not an ExecutionPolicy audit/enforce question at all. A
                # MODEL subtask is already safe to run for real here:
                # DeveloperAgent.run_generation only returns file content,
                # it never writes anything (SubtaskExecutor doesn't apply
                # the result). A TOOL subtask is NOT safe: running a real
                # tool (kernel.registry.get("tool", ...).execute(...)) IS
                # the side effect, by definition - and "shell"/"git" are
                # always-registered real tools (plugins/core_tools) capable
                # of arbitrary command execution / real git mutation, with
                # ZERO policy consultation anywhere in SubtaskExecutor's own
                # TOOL dispatch. Since this shadow path's whole contract -
                # this module's own docstring, and the MA7 hardening plan's
                # own requirement - is "never mutating, purely
                # observational," a TOOL-tagged subtask is never actually
                # executed here, for ANY tool_name, not just a denylist of
                # known-dangerous ones (a future registered tool could just
                # as easily have real side effects, and this shouldn't
                # depend on a maintained list staying complete). This is a
                # structural guarantee, not a pattern-matched policy
                # decision - deliberately NOT routed through
                # ExecutionPolicy's command-allowlist stage, since that
                # stage reasons about parsed argv shape and cannot reliably
                # judge an arbitrary shell string's real effect (shell
                # metacharacters - `;`, `&&`, `|`, `$(...)` - defeat a
                # prefix/allowlist check trivially; a structural "shadow
                # never runs tools" rule has no such bypass). If/when
                # SubtaskExecutor becomes a real, authoritative execution
                # path (a future enforce mode), TOOL subtasks running for
                # real is the whole point of that mode - this restriction
                # is specific to THIS shadow-observational caller, not a
                # change to SubtaskExecutor itself.
                result = deps.SubtaskResult(
                    subtask_id=subtask.id, status=deps.SubtaskStatus.NEEDS_REVIEW,
                    execution_method=deps.ExecutionMethod.TOOL.value,
                    error=(
                        "shadow mode does not execute TOOL subtasks for real - doing so would "
                        "have real side effects on the workspace, violating shadow's non-mutating "
                        "contract. This subtask needs a real (non-shadow) execution path."
                    ),
                )
                results.append(result)
                deps.record_subtask_attempt(ledger, plan, result, attempt=1)
                notes.append(f"stopped at subtask {subtask_id!r}: TOOL subtasks are never executed in shadow mode")
                break

            projected = deps.project_for_subtask(context, subtask)
            deps.record_context_package_for_subtask(ledger, plan, subtask_id, projected)
            result = await deps.subtask_executor.execute(
                subtask=subtask, plan=plan, context=projected,
                kernel=kernel, developer_agent=deps.workflow_engine.developer,
            )
            results.append(result)
            deps.record_subtask_attempt(ledger, plan, result, attempt=1)
            if result.undeclared_files:
                deps.record_undeclared_file_touch(ledger, plan, result)
            if result.status != deps.SubtaskStatus.COMPLETED:
                notes.append(f"stopped at subtask {subtask_id!r}: status={result.status.value}")
                break

        report = deps.build_verification_report(plan.acceptance_criteria)
        return plan, tuple(results), ledger.all(), report, notes

    @staticmethod
    def write_enforce_trace(deps: TraceDeps, run_id: str, goal: str, result: Dict[str, Any], started_at: float) -> bool:
        """One traces.db row (``<run_id>.enforce``, never a subtask's own id)
        for an enforce run: its real terminal status, a ``planning.failed``
        event when structured planning failed, the ``requirement.verdicts``
        event when the terminal verifier ran, and the role metrics no subtask
        row reported. Not a RunRecord and not a lifecycle state."""
        from kriya.core.state_paths import trace_db_path
        from kriya.workflow.run_events import EventAuthority, RunEvent
        write_outcome_trace = deps.load_write_outcome_trace()

        engine = deps.workflow_engine
        kernel = getattr(engine, "kernel", None)
        if kernel is None:
            return False
        source = "workflow_controller.enforce"
        status = str(result.get("status") or "unknown")
        events: List[Dict[str, Any]] = []
        if status == "error":
            events.append(RunEvent(
                kind="run.exception", attempt=0, source=source, authority=EventAuthority.AUTHORITATIVE,
                message=f"{result.get('failure_type')}: {str(result.get('error') or '')[:300]}",
                details={"exception_type": result.get("failure_type"), "message": result.get("error")},
            ).to_dict())
        if result.get("failure_type") == "PLANNING_ERROR":
            events.append(RunEvent(
                kind="planning.failed", attempt=0, source=source, authority=EventAuthority.AUTHORITATIVE,
                message=f"structured planning failed: {', '.join(result.get('reason_codes') or [])}",
                details={key: result[key] for key in ("reason_codes", "invalid_subtask_ids",
                                                     "plan_repair_attempts", "error") if key in result},
            ).to_dict())
        contract = result.get("verification_contract") if isinstance(result.get("verification_contract"), dict) else None
        if contract is not None:
            # VERIFICATION-CONTRACT-003: compiled always; sealed when admitted, else
            # the typed refusal (GOAL_INSUFFICIENT_FOR_VERIFICATION or
            # VERIFICATION_AUTHORITY_REQUIRED) with its structured reason.
            totals = ", ".join(f"{k}={v}" for k, v in (contract.get("totals") or {}).items())
            events.append(RunEvent(
                kind="verification_contract.compiled", attempt=0, source=source, authority=EventAuthority.AUXILIARY,
                message=f"verification contract {str(contract.get('contract_digest'))[:12]}: {contract.get('admission')}; {totals}",
                details=contract,
            ).to_dict())
            baseline = result.get("baseline_authority") if isinstance(result.get("baseline_authority"), dict) else None
            if baseline is not None:
                events.append(RunEvent(
                    kind="verification_contract.baseline", attempt=0, source=source, authority=EventAuthority.AUTHORITATIVE,
                    message=(f"baseline authority run: no_mutation_required={baseline.get('no_mutation_required')}, "
                             f"discriminating={baseline.get('discriminating')}"), details=baseline,
                ).to_dict())
                if result.get("no_mutation_required"):
                    events.append(RunEvent(
                        kind="verification_contract.no_mutation_required", attempt=0, source=source,
                        authority=EventAuthority.AUTHORITATIVE,
                        message="NO_MUTATION_REQUIRED: the baseline satisfies every mandatory requirement of the goal",
                        details=baseline,
                    ).to_dict())
                if result.get("failure_category") == "verification_authority_unavailable":
                    events.append(RunEvent(
                        kind="verification_contract.refused", attempt=0, source=source, authority=EventAuthority.AUTHORITATIVE,
                        message=str(result.get("error") or "")[:600],
                        details={"reason_code": "VERIFICATION_AUTHORITY_UNAVAILABLE",
                                 "unavailable": [e for e in (baseline.get("authorities_run") or [])
                                                 if e.get("reason_code") in ("AUTHORITY_EXECUTION_UNAVAILABLE",
                                                                             "AUTHORITY_PREPARE_FAILED", "AUTHORITY_TIMEOUT")]},
                    ).to_dict())
            refusal = result.get("requirements_admission") if isinstance(result.get("requirements_admission"), dict) else None
            if refusal is not None:
                events.append(RunEvent(
                    kind=("requirement.authority_required"
                          if refusal.get("reason_code") == "VERIFICATION_AUTHORITY_REQUIRED"
                          else "requirement.admission_refused"),
                    attempt=0, source=source, authority=EventAuthority.AUTHORITATIVE,
                    message=str(result.get("error") or "")[:600], details=refusal,
                ).to_dict())
                if result.get("authority_requests"):
                    # BACKEND-READINESS-004 (D2): the typed requests, same payload as the direct path.
                    requests = list(result["authority_requests"])
                    events.append(RunEvent(
                        kind="verification_contract.authority_requested", attempt=0, source=source,
                        authority=EventAuthority.AUTHORITATIVE,
                        message=f"{len(requests)} authority request(s) for "
                                f"{len({r.get('requirement_id') for r in requests})} requirement(s); "
                                "a request proposes authority, only the operator seals it",
                        details={"contract_digest": contract.get("contract_digest"),
                                 "stored_path": result.get("authority_requests_path"), "requests": requests},
                    ).to_dict())
            else:
                events.append(RunEvent(
                    kind="verification_contract.sealed", attempt=0, source=source,
                    authority=EventAuthority.AUTHORITATIVE,
                    message=f"verification contract {str(contract.get('contract_digest'))[:12]} sealed before the first model call",
                    details={"digest": contract.get("contract_digest"), "stored_path": contract.get("stored_path"),
                             "admission": contract.get("admission"), "totals": contract.get("totals"),
                             "authorities": contract.get("authorities"), "visibility": contract.get("visibility"),
                             "dispositions": contract.get("dispositions")},
                ).to_dict())
                if contract.get("dispositions"):
                    # BACKEND-READINESS-004 (D3): audit event, same payload as the direct path.
                    dispositioned = [{"requirement_id": row.get("id"), "status": row.get("status"),
                                      "claims": row.get("dispositioned_claims"), "disposition": row.get("disposition")}
                                     for row in (contract.get("requirements") or [])
                                     if row.get("disposition") or row.get("dispositioned_claims")]
                    events.append(RunEvent(
                        kind="verification_contract.dispositioned", attempt=0, source=source,
                        authority=EventAuthority.AUTHORITATIVE,
                        message=f"operator disposition {str((contract.get('dispositions') or {}).get('digest'))[:12]}: "
                                f"{len(dispositioned)} statement(s) dispositioned before the first model call",
                        details={"dispositions": contract.get("dispositions"), "entries": dispositioned},
                    ).to_dict())
        requirements = result.get("requirements") if isinstance(result.get("requirements"), dict) else {}
        if requirements.get("verdicts"):
            events.append(RunEvent(
                kind="requirement.verdicts", attempt=0, source=source, authority=EventAuthority.ADVISORY,
                message="original requirement outcomes: " + ", ".join(
                    f"{rid}={verdict.get('outcome')}({verdict.get('reason_code')})"
                    for rid, verdict in sorted(requirements["verdicts"].items())),
                details={"requirement_set_digest": requirements.get("digest"),
                         "outcomes": requirements.get("outcomes"), "verdicts": requirements["verdicts"]},
            ).to_dict())
        failure_category = None if status == "success" else str(result.get("failure_type") or status)
        try:
            trace_db = trace_db_path(kernel.config)
        except Exception as error:  # the run's own result stands; tested for the normal output
            deps.logger.warning("No traces.db for enforce run %r: %s", run_id, error)
            return False
        return write_outcome_trace(
            trace_db, run_id=f"{run_id}.enforce", goal=goal, status=status,
            llm=getattr(getattr(engine, "developer", None), "llm", None), source=source, started_at=started_at,
            failure_category=failure_category, events=events, files=result.get("files") or (),
        )
