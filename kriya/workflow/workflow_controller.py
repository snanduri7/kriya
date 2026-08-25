"""WorkflowController - MA6.8/6.9/6.10/6.13/6.14 of the MA6 structured-
execution implementation plan. The shell that will eventually own
orchestration (MA6 invariant 10). Every ChangeKind still routes the REAL
outcome to the SAME place - the existing, untouched run_generation_workflow(),
called via _run_legacy_generation() (MA6.10's own explicit first step:
"WorkflowController -> LegacyGenerationAdapter -> run_generation_workflow(),
then responsibilities extracted gradually"). Does NOT rewrite
run_generation_workflow() (MA6 hard constraint) - the legacy call is
byte-for-byte what every existing caller already makes.

MA6.9 gives the four kinds real, DISTINCT control-plane bookkeeping ahead
of that call: MILESTONE attaches milestone_group_id/current_milestone_id;
REFACTOR captures a real git base_commit/tree_hash baseline. TASK/
ENHANCEMENT get no extra bookkeeping - genuinely differentiating their
SHAPE requires MA6.10's gradual internal extraction from
run_generation_workflow(), which has not happened.

MA6.10/6.13's remaining, real scope in this slice: `migration_mode="shadow"`
builds an actual EngineeringPlan (Planner -> parse_planner_structured_output,
MA6.3) validates it (plan_validation.validate_plan, MA6.2) and, if valid,
runs EVERY subtask through the real SubtaskExecutor (MA6.5) against a
ContextPackage assembled through the real ContextOrchestrator (MA5.7,
wired in MA7.2 - see _build_context) PLUS real on-disk content of the
plan's own planned files. Still NOT the hybrid vector/graph retrieval the
legacy path's Graph RAG stage does - _build_context's own docstring
explains why that stays out of scope here. Critically: this NEVER
replaces or influences the real outcome -
_run_legacy_generation still runs unconditionally and its result is what
WorkflowResult.legacy_result reports. Why the new path can't safely own
the real outcome yet: SubtaskExecutor stops at "get file content or a
tool result" - it does not itself apply edits, run compile/test
verification, or gate on approval, all of which still live only in
run_generation_workflow()'s own Quality Gates loop. Any exception in the
shadow path is caught and logged, never allowed to fail the real run.

MA7.8 (2026-08-24): `migration_mode="enforce"` is now real, explicitly
authorized code, not refused. kriya.config.config.WorkflowControllerConfig's
own validator no longer rejects it either - lifting that restriction was
its own deliberate decision, confirmed with the user directly, mirroring
how MA7.3 handled the analogous execution_policy.mode restriction (asked
first, never silently lifted). Still defaults to `mode: shadow`/
`enabled: false` in the packaged config - "enforce" only ever runs for a
project that explicitly opts in, matching every other "ship the mechanism,
default it off" precedent in this codebase (MA4.15/MA5/MA6 alike).

What "enforce" actually does (_run_structured_enforce): deliberately does
NOT reimplement edit-application/compile-test verification/approval
gating from scratch - SubtaskExecutor still only produces file content or
a tool result, exactly as before. Instead reuses the SAME real, mature
mechanism kriya/workflow/milestones.py::run_milestones() already uses for
the analogous problem (decomposing one big goal into smaller real
generation calls): calls the EXISTING, unmodified
run_generation_workflow() once PER SUBTASK (instead of once per
milestone), in dependency order, threading each subtask's real written
output forward via the same render_established_file_context/
project_implementation_source machinery run_milestones() already uses.
This genuinely delivers per-subtask retry locality (each subtask gets its
own independent Quality-Gates retry budget) without inventing a new,
unproven apply/verify/approve mechanism. See that method's own docstring
for the honest scope boundaries this first cut still has (TOOL-tagged
subtasks refused outright, MA6.7's per-subtask checkpoint not yet wired).

MA7.1: wired into `kriya generate`'s real call path (kriya/cli.py's
`_dispatch_generation` helper, all three run_generation_workflow call
sites in the `generate` command) - still gated by
`workflow_controller.enabled: false` by default in kriya.yaml, so a stock
install's behavior is byte-for-byte unchanged regardless of mode. `kriya
fix` and `kriya plan-milestones` are NOT wired - deliberately out of
MA7.1's scope, left for a later increment.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple

from kriya.agents.contracts import parse_planner_structured_output
from kriya.control.decisions import (
    Decision,
    DecisionLedger,
    stamp_legacy_decision_ledger_ownership,
)
from kriya.control.artifacts import ArtifactRegistry
from kriya.control.contracts import ContractRegistry
from kriya.control.persistence import (
    load_artifact_registry,
    load_contract_registry,
    load_control_state,
    artifact_registry_path,
    contract_registry_path,
    control_state_path,
    save_artifact_registry,
    save_approved_plan,
    save_contract_registry,
    save_control_state,
)
from kriya.control.state import ControlState
from kriya.control.workspace_identity import json_document_is_ownerless
from kriya.workflow import subtask_executor
from kriya.workflow.checkpoint import (
    ResumeStatus,
    compute_base_commit,
    compute_registry_hash,
    compute_tree_hash,
    new_run_id,
    validate_resume_against_reality,
    list_checkpoints,
    save_checkpoint,
)
from kriya.workflow.context_orchestrator import ContextOrchestrator
from kriya.workflow.context_package import (
    ContextPackage,
    artifact_entry_from_record,
    contract_entry_from_record,
    make_context_item,
)
from kriya.workflow.context_projection import project_implementation_source, render_established_file_context
from kriya.workflow.control_context import WorkflowControlContext
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    Subtask,
    VerificationMethodType,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.subtask_checkpoint import topological_subtask_order
from kriya.workflow.subtask_context_projection import project_for_subtask
from kriya.workflow.subtask_telemetry import (
    record_context_package_for_subtask,
    record_plan_created,
    record_subtask_attempt,
    record_undeclared_file_touch,
)
from kriya.workflow.triage import ChangeKind
from kriya.workflow.verification_report import build_verification_report
from kriya.workflow.workflow import _log_phase_banner
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus, VerificationReport, WorkflowResult


def _evaluate_subtask_verification(
    subtask: Subtask, call_result: Dict[str, Any],
) -> Tuple[SubtaskStatus, Optional[str], Tuple[str, ...]]:
    if not subtask.verification:
        return SubtaskStatus.COMPLETED, None, ()
    evidence = call_result.get("verification_results")
    if not isinstance(evidence, list) or len(evidence) != len(subtask.verification):
        return (
            SubtaskStatus.NEEDS_REVIEW,
            "declared verification requirements have no complete authoritative evidence",
            ("VERIFICATION_EVIDENCE_MISSING",),
        )
    unresolved: List[str] = []
    failed: List[str] = []
    for method, item in zip(subtask.verification, evidence, strict=True):
        if not isinstance(item, dict) or item.get("type") != method.type.value:
            unresolved.append(method.description)
            continue
        if method.type == VerificationMethodType.TOOL and item.get("tool_name") != method.tool_name:
            unresolved.append(method.description)
            continue
        if item.get("description", "") != method.description:
            unresolved.append(method.description)
            continue
        if item.get("passed") is False:
            failed.append(method.description)
        elif item.get("passed") is not True:
            unresolved.append(method.description)
    if failed:
        return SubtaskStatus.FAILED, f"verification failed: {failed!r}", ("VERIFICATION_FAILED",)
    if unresolved:
        return (
            SubtaskStatus.NEEDS_REVIEW,
            f"verification unresolved: {unresolved!r}",
            ("VERIFICATION_UNRESOLVED",),
        )
    return SubtaskStatus.COMPLETED, None, ()

logger = logging.getLogger(__name__)

_VALID_MIGRATION_MODES = ("legacy", "shadow", "enforce")

# MA7.8 - mirrors kriya/workflow/milestones.py's own
# _ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE exactly (same value, a local
# constant rather than importing that module's private name across a
# module boundary - the two orchestrators are siblings, not one built on
# the other).
_ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE = 4000


class WorkflowControllerConfigurationError(ValueError):
    pass


class _StructuredPlanUnavailable(Exception):
    """MA7.8, fixed 2026-08-24 after a real live-validation finding: raised
    ONLY by _run_structured_enforce's pre-execution steps (Stage A parse,
    zero-subtask output, a TOOL-tagged subtask, plan validation) - every
    one of those happens BEFORE any subtask has actually run, so zero real
    side effects exist yet. execute() catches this specifically and falls
    back to the legacy whole-goal path, restoring PlannerStructuredOutput's
    own original design contract (kriya/workflow/plan_schema.py's own
    docstring: "a malformed or missing structured block must never break
    generation; every extraction failure degrades to 'no structured plan
    available,' identical to today's prose-only behavior") - a contract
    the very first version of enforce mode violated: a single malformed
    subtask in an otherwise-fine Stage A JSON block (confirmed live,
    2026-08-24, protocol_encoder_java: a subtask claimed
    execution_method=tool with no tool_name, failing pydantic validation)
    made the WHOLE run produce zero files, even though the SAME goal would
    almost certainly have succeeded via the ordinary prose-based Planner/
    Architect/Developer path. Once the per-subtask loop has actually
    started (even one real run_generation_workflow() call has happened),
    a failure is NOT this exception - it's a genuine outcome that must
    stay a clean failure result, never silently retried via a different
    execution model that could double-generate or conflict with whatever
    was already applied to the real workspace."""


class _UnsafeStructuredPlan(Exception):
    """A parsed plan violates an authoritative safety boundary."""

    def __init__(
        self,
        message: str,
        *,
        reason_codes: Optional[List[str]] = None,
        invalid_subtask_ids: Optional[List[str]] = None,
        repair_attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason_codes = tuple(reason_codes or ["PLAN_VALIDATION_FAILED"])
        self.invalid_subtask_ids = tuple(invalid_subtask_ids or ())
        self.repair_attempts = repair_attempts


def build_subtask_goal_text(
    subtask: Subtask,
    position: int,
    total: int,
    *,
    plan: Optional[EngineeringPlan] = None,
) -> str:
    """MA7.8 - the per-subtask analogue of kriya/workflow/milestones.py's
    build_milestone_goal_text(): deterministic string assembly, no extra
    LLM call, same reasoning (run_generation_workflow's own repo-analysis
    stage re-scans workspace_path fresh on every call, so real upstream
    output is visible once applied. Plan-level semantic contracts and
    global invariants are rendered separately by
    build_subtask_semantic_context(), keeping this string the bounded
    executable goal rather than an unstructured copy of the whole plan)."""
    header = ""
    if subtask.depends_on:
        dep_list = ", ".join(sorted(subtask.depends_on))
        header = (
            f"This is subtask '{subtask.id}' ({position} of {total}) of a larger "
            f"structured plan, depending on: {dep_list}. Earlier subtasks' output has "
            "already been applied to this project on disk - inspect the existing "
            "code/build files in the Workspace Context below rather than assuming a "
            "blank project, and do NOT recreate, restructure, or rename anything that "
            "already exists and already works unless this subtask's own goal below "
            "explicitly requires changing it.\n\n"
        )
    planned = ""
    if subtask.planned_files:
        planned = "\n\nFiles this subtask should touch:\n" + "\n".join(
            f"- {pf.path} ({pf.action.value})" + (f" - {pf.reason}" if pf.reason else "")
            for pf in subtask.planned_files
        )
    acceptance = ""
    if plan is not None and subtask.acceptance_criteria_ids:
        criteria_by_id = {criterion.id: criterion for criterion in plan.acceptance_criteria}
        mapped = [
            criteria_by_id[criterion_id].description
            for criterion_id in subtask.acceptance_criteria_ids
            if criterion_id in criteria_by_id
        ]
        if mapped:
            acceptance = "\n\nAcceptance criteria assigned to this subtask:\n" + "\n".join(
                f"- {description}" for description in mapped
            )
    verification = ""
    if subtask.verification:
        verification = "\n\nVerification: " + "; ".join(v.description for v in subtask.verification)
    return header + subtask.description + acceptance + planned + verification


def build_subtask_constraint_context(original_goal: str) -> str:
    """Retain global constraints without redefining the bounded stage's goal."""
    if not original_goal:
        return ""
    return (
        "Overall request constraints (context only; not this stage's completion scope):\n"
        f"{original_goal}\n\n"
        "Implement only the current subtask and only its approved files, but preserve every "
        "overall constraint relevant to those files. Do not implement later stages early, and "
        "do not judge this bounded stage as responsible for completing the entire overall request."
    )


def build_subtask_semantic_context(plan: EngineeringPlan, subtask: Subtask) -> str:
    """Render the plan-level semantic contract for one bounded stage."""
    providers = {
        capability: provider.id
        for provider in plan.subtasks
        for capability in provider.provides
    }
    upstream = [
        {"capability": requirement, "provider": providers.get(requirement)}
        for requirement in subtask.requires
    ]
    downstream = [
        {"consumer": consumer.id, "capability": requirement}
        for consumer in plan.subtasks
        for requirement in consumer.requires
        if requirement in subtask.provides
    ]
    payload = {
        "local_description": subtask.description,
        "planned_files": [pf.path for pf in subtask.planned_files],
        "relevant_global_invariants": list(subtask.relevant_global_invariants),
        "upstream_contracts": upstream,
        "downstream_requirements": downstream,
        "verification_targets": [vm.description for vm in subtask.verification],
        "runtime_execution_required": any(
            vm.requires_runtime_execution for vm in subtask.verification
        ),
    }
    return "--- bounded subtask semantic context ---\n" + json.dumps(payload, indent=2, sort_keys=True)


def build_subtask_plan_text(subtask: Subtask) -> str:
    """MA7-C1 (2026-08-25 external review) - the 'plan' half of the
    predetermined_plan/predetermined_design bypass (kriya/workflow/
    workflow.py::run_generation_workflow) that lets _run_structured_enforce
    stop re-running a fresh Planner+Architect cycle for an already-validated
    Subtask. Deliberately short, plain prose with no fenced code blocks -
    run_attempt()'s own extract_planner_code_blocks(ctx.plan, ...) call
    looks for inline code a real Planner sometimes drafts directly; finding
    none here is correct (this subtask has no drafted code yet, only a
    validated description/file list), and that extraction already degrades
    gracefully to an ordinary fresh Developer generation call when it finds
    nothing - exactly the behavior a bounded subtask needs."""
    return f"Implement: {subtask.description}"


AUTHORITATIVE_PLANNER_SYSTEM_PROMPT = (
    "You are Kriya's authoritative structured Planner. Return exactly one valid JSON object and "
    "nothing else: no Markdown, code fences, prose, or rationale. Decompose the request into the "
    "smallest safe set of bounded implementation subtasks. Use this top-level shape: "
    '{"global_invariants": ["..."], "subtasks": [{"id": "s1", "description": "...", '
    '"execution_method": "model", "depends_on": [], "planned_files": '
    '[{"path": "...", "action": "create|modify|delete"}], "provides": ["..."], '
    '"requires": [], "relevant_global_invariants": ["..."], "verification": [], '
    '"acceptance_criteria_ids": ["ac1"]}], "acceptance_criteria": '
    '[{"id": "ac1", "description": "...", "method": "judgment"}], '
    '"extension_points": [], "refactor_baseline": null}. '
    "Every model subtask must own every file it may change. Verification-only work belongs in "
    "verification or acceptance_criteria. Preserve explicit producer/consumer dependencies and "
    "goal-derived invariants. Never invent product requirements."
)


def build_structured_plan_repair_prompt(
    goal: str,
    previous_plan_text: str,
    errors: List[str],
    reason_codes: List[str],
    repair_attempt: int,
) -> str:
    """Build a bounded local-only correction request for the complete plan."""
    targeted_correction = ""
    if "TOOL_SUBTASK_MISSING_TOOL_NAME" in reason_codes:
        targeted_correction += (
            "- A TOOL subtask with no tool_name is not executable. If it is a non-editing check, "
            "REMOVE it from subtasks and move its acceptance_criteria_ids plus an equivalent "
            "verification entry onto its nearest declared implementation dependency. Do not relabel "
            "it MODEL.\n"
        )
    if "MODEL_SUBTASK_MISSING_PLANNED_FILES" in reason_codes:
        targeted_correction += (
            "- For each named unscoped MODEL subtask: if it is a non-editing build/test/run/output "
            "check, REMOVE it from subtasks, redirect any downstream depends_on edges to its own "
            "dependencies, and move its acceptance criteria plus an equivalent verification entry "
            "onto the nearest implementation dependency. If it genuinely edits files, retain it only "
            "with the exact real planned_files it owns. Never invent a fake file for a check.\n"
        )
    return (
        "Repair the previous structured engineering plan. This is PLAN_REPAIR, not implementation.\n"
        "Return only one complete JSON object and nothing else. Do not use Markdown or code fences.\n\n"
        f"Original request:\n{goal}\n\n"
        f"Deterministic reason codes: {json.dumps(reason_codes)}\n"
        "Deterministic validation errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\nCorrection rules:\n"
        "- Return a complete corrected plan, preserving every valid subtask and dependency.\n"
        "- Correct only invalid plan structure; do not broaden scope or invent modules/entrypoints.\n"
        "- Declare depends_on for every stage that consumes files, configuration, contracts, or "
        "build setup produced by another stage.\n"
        "- Preserve or add goal-derived global_invariants, relevant_global_invariants, and stable "
        "provides/requires metadata; every requirement needs exactly one provider and a depends_on edge.\n"
        "- Every execution_method=model subtask MUST declare every file it may modify in planned_files.\n"
        "- Verification-only build/test/run/output checks belong in verification or acceptance_criteria, "
        "not in a MODEL subtask with no files.\n"
        + targeted_correction
        + "- Do not emit TOOL subtasks: authoritative enforce mode has no policy-mediated TOOL router yet.\n"
        "- Output the complete corrected JSON object, not a patch, explanation, or Markdown plan.\n\n"
        f"Previous Planner response (repair attempt {repair_attempt}):\n"
        + previous_plan_text[-20000:]
    )


def build_authoritative_planner_request(goal: str) -> str:
    """Add enforce-only protocol constraints without changing the product goal."""
    return (
        "Original product request:\n"
        f"{goal}\n\n"
        "Return only one complete JSON object containing the execution-relevant structured plan. "
        "Emit no prose, Markdown, code fences, rationale, or architecture essay.\n"
        "Authoritative structured-plan protocol (planning metadata, not product requirements):\n"
        "- Do not emit execution_method=tool subtasks; this execution path has no policy-mediated "
        "TOOL router. Represent non-editing checks as verification or acceptance criteria.\n"
        "- Every MODEL subtask must own at least one exact planned_files path.\n"
        "- Include goal-derived global_invariants and per-subtask relevant_global_invariants, "
        "provides, requires, and complete depends_on edges.\n"
        "- Build/config stages may use compile or test verification. Any original-request "
        "requirement for observable application behavior must also be verified by an entrypoint-owning "
        "stage that actually runs the application, observes the required result, and confirms clean exit; "
        "set requires_runtime_execution=true on that verification method and false on build-only checks.\n"
        "- Do not copy these protocol rules into global_invariants; derive those only from the "
        "original product request above."
    )


def build_approved_plan_document(
    plan: EngineeringPlan,
    *,
    plan_hash: str,
    repair_attempts: int,
    stage_states: Dict[str, str],
    lifecycle_state: str,
) -> Dict[str, Any]:
    """Canonical durable representation of an authoritative approved plan."""
    return {
        "schema_version": 1,
        "plan_id": plan.plan_id,
        "plan_hash": plan_hash,
        "approval_status": "approved",
        "approval_basis": "authoritative_validation",
        "repair_attempts": repair_attempts,
        "lifecycle_state": lifecycle_state,
        "stage_order": topological_subtask_order(plan),
        "stage_states": dict(stage_states),
        "plan": plan.model_dump(mode="json"),
    }


def transitive_subtask_dependents(plan: EngineeringPlan, subtask_id: str) -> List[str]:
    """Return every downstream stage invalidated by reopening one owner."""
    dependents: set[str] = set()
    frontier = [subtask_id]
    while frontier:
        provider_id = frontier.pop()
        direct = [st.id for st in plan.subtasks if provider_id in st.depends_on]
        for dependent_id in direct:
            if dependent_id not in dependents:
                dependents.add(dependent_id)
                frontier.append(dependent_id)
    return [subtask_id_ for subtask_id_ in topological_subtask_order(plan) if subtask_id_ in dependents]


def resolve_scope_conflict_owners(
    plan: EngineeringPlan, required_files: List[str], failed_subtask: Subtask,
) -> Dict[str, List[str]]:
    """Resolve required repair files to unique, declared upstream owners."""
    owners: Dict[str, List[str]] = {}
    for path in required_files:
        owner = plan.file_owner(path)
        if owner is None or owner.id == failed_subtask.id or owner.id not in failed_subtask.depends_on:
            continue
        owners.setdefault(owner.id, []).append(path)
    return owners


def compute_abandoned_plan_files(
    prior_subtask_states: Dict[str, str],
    prior_subtask_written_files: Dict[str, List[str]],
    new_plan_files: Any,
) -> List[str]:
    """Pure computation of which real, already-applied files belong ONLY to
    an abandoned plan and no longer belong to any subtask in a freshly
    re-planned one - see ControlState.subtask_written_files's own docstring
    (kriya/control/state.py) for the live incident (protocol_encoder_java,
    2026-08-25) this closes.

    A file counts as abandoned only if it was written by a subtask the prior
    ControlState recorded as genuinely "completed" (a failed/incomplete
    subtask's own written files are ordinary in-progress work, not
    abandoned residue - resume already refuses to reuse them for an
    unrelated reason) AND it does not appear anywhere in the NEW plan's own
    declared planned_files. Deliberately conservative: a file the new plan
    happens to declare too (the re-plan genuinely reuses/regenerates it) is
    left alone, never treated as abandoned just because it came from an
    earlier subtask id."""
    new_files = set(new_plan_files)
    abandoned: set = set()
    for subtask_id, status in prior_subtask_states.items():
        if status != "completed":
            continue
        for path in prior_subtask_written_files.get(subtask_id, []):
            if path not in new_files:
                abandoned.add(path)
    return sorted(abandoned)


def quarantine_abandoned_plan_files(
    workspace_path: str, abandoned_files: List[str], quarantine_subdir: str,
) -> List[str]:
    """Moves (never deletes) each real, on-disk abandoned file into
    `.kriya/abandoned_plan_files/<quarantine_subdir>/<relative_path>`,
    preserving it exactly rather than discarding it outright - a re-plan
    correctly abandoning a file is still a real, possibly-informative
    Kriya-authored artifact, not proven garbage, and this whole mechanism
    only ever runs unattended (enforce mode, no human approval gate in this
    path) - see this module's own risk-handling convention (prefer a
    reversible move over deletion whenever nothing forces an irreversible
    one). Best-effort per file: a single unmovable file (permissions, races,
    already gone) is logged and skipped, never allowed to fail the real run
    this cleanup is only ever a courtesy alongside. Returns the list of
    files actually moved."""
    moved: List[str] = []
    for rel_path in abandoned_files:
        src = os.path.join(workspace_path, rel_path)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(workspace_path, ".kriya", "abandoned_plan_files", quarantine_subdir, rel_path)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            moved.append(rel_path)
        except OSError as e:
            logger.warning(f"Could not quarantine abandoned plan file {rel_path!r} (non-fatal, left in place): {e}")
    return moved


def _migrate_legacy_milestone_ownership(
    workspace_path: str,
    run_state: Any,
    contract_registry: Any,
    artifact_registry: Any,
    *,
    prior_control_state_ownerless: bool,
) -> Tuple[str, ...]:
    """One-time ownership migration for authoritative milestone state."""
    migrated: List[str] = []
    contracts_path = contract_registry_path(workspace_path)
    if json_document_is_ownerless(contracts_path):
        with open(contracts_path, "r", encoding="utf-8") as handle:
            ContractRegistry.from_dict(json.load(handle))
        save_contract_registry(workspace_path, contract_registry)
        migrated.append("contract_registry")
    artifacts_path = artifact_registry_path(workspace_path)
    if json_document_is_ownerless(artifacts_path):
        with open(artifacts_path, "r", encoding="utf-8") as handle:
            ArtifactRegistry.from_dict(json.load(handle))
        save_artifact_registry(workspace_path, artifact_registry)
        migrated.append("artifact_registry")
    if prior_control_state_ownerless:
        migrated.append("control_state")

    sidecar_path = os.path.join(
        workspace_path, ".kriya", "milestones", f"{run_state.group_id}.json",
    )
    if json_document_is_ownerless(sidecar_path):
        from kriya.workflow.milestones import MilestoneRunState, save_milestone_run_state
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            MilestoneRunState.from_dict(json.load(handle))
        save_milestone_run_state(workspace_path, run_state)
        migrated.append("milestone_run_state")

    migrated_checkpoints = 0
    for checkpoint in list_checkpoints(workspace_path):
        if checkpoint.get("_workspace") is not None:
            continue
        run_id = checkpoint.get("run_id")
        if run_id:
            save_checkpoint(workspace_path, run_id, checkpoint)
            migrated_checkpoints += 1
    if migrated_checkpoints:
        migrated.append(f"checkpoints:{migrated_checkpoints}")

    migrated_decisions = stamp_legacy_decision_ledger_ownership(workspace_path)
    if migrated_decisions:
        migrated.append(f"decision_ledger_entries:{migrated_decisions}")

    if migrated:
        DecisionLedger().record_and_persist(
            workspace_path,
            "legacy_workspace_ownership_migrated",
            stores=sorted(migrated),
            milestone_group_id=run_state.group_id,
        )
    return tuple(sorted(migrated))


def _migrate_legacy_controller_ownership(workspace_path: str, run_id: str) -> Tuple[str, ...]:
    """Stamp ownerless control documents before authoritative plain execution."""
    migrated: List[str] = []
    state_path = control_state_path(workspace_path)
    if json_document_is_ownerless(state_path):
        with open(state_path, "r", encoding="utf-8") as handle:
            prior_state = ControlState.from_dict(json.load(handle))
        save_control_state(workspace_path, prior_state)
        migrated.append("control_state")
    contracts_path = contract_registry_path(workspace_path)
    if json_document_is_ownerless(contracts_path):
        with open(contracts_path, "r", encoding="utf-8") as handle:
            contracts = ContractRegistry.from_dict(json.load(handle))
        save_contract_registry(workspace_path, contracts)
        migrated.append("contract_registry")
    artifacts_path = artifact_registry_path(workspace_path)
    if json_document_is_ownerless(artifacts_path):
        with open(artifacts_path, "r", encoding="utf-8") as handle:
            artifacts = ArtifactRegistry.from_dict(json.load(handle))
        save_artifact_registry(workspace_path, artifacts)
        migrated.append("artifact_registry")
    migrated_decisions = stamp_legacy_decision_ledger_ownership(workspace_path)
    if migrated_decisions:
        migrated.append(f"decision_ledger_entries:{migrated_decisions}")
    migrated_checkpoints = 0
    for checkpoint in list_checkpoints(workspace_path):
        if checkpoint.get("_workspace") is not None:
            continue
        checkpoint_run_id = checkpoint.get("run_id")
        if checkpoint_run_id:
            save_checkpoint(workspace_path, checkpoint_run_id, checkpoint)
            migrated_checkpoints += 1
    if migrated_checkpoints:
        migrated.append(f"checkpoints:{migrated_checkpoints}")
    if migrated:
        DecisionLedger().record_and_persist(
            workspace_path,
            "legacy_workspace_ownership_migrated",
            stores=sorted(migrated),
            run_id=run_id,
        )
    return tuple(sorted(migrated))


class WorkflowController:
    """Wraps an existing WorkflowEngine (kriya/workflow/workflow.py) -
    never constructs its own agents/LLM client, and deliberately reuses
    that engine's OWN `engineering_triage`/`planner`/`developer`/`kernel`
    rather than building second copies, so triage state/kernel wiring
    stays exactly what a real caller already set up."""

    def __init__(self, workflow_engine: Any) -> None:
        self.workflow_engine = workflow_engine

    async def execute(
        self,
        goal: str,
        workspace_path: str,
        *,
        known_files: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        milestone_group_id: Optional[str] = None,
        milestone_index: Optional[int] = None,
        milestone_total: Optional[int] = None,
        migration_mode: str = "legacy",
        **legacy_kwargs: Any,
    ) -> WorkflowResult:
        if migration_mode not in _VALID_MIGRATION_MODES:
            raise WorkflowControllerConfigurationError(
                f"migration_mode must be one of {_VALID_MIGRATION_MODES!r}, got {migration_mode!r}."
            )

        run_id = run_id or legacy_kwargs.get("trace_id_override") or new_run_id()

        _log_phase_banner("REQUEST ANALYSIS")
        route = await self.workflow_engine.engineering_triage.classify(
            goal, workspace_path, known_files=known_files,
        )
        control_context = WorkflowControlContext.for_route(route)
        control_state = ControlState.new(
            run_id=run_id,
            engineering_route=route,
            process_profile=control_context.process_profile,
            milestone_group_id=milestone_group_id,
        )
        logger.info(
            "Run route: kind=%s risk=%s weight=%s",
            route.kind.value.upper(), route.max_observed_risk_class.name,
            route.execution_weight.value.upper(),
        )

        if route.kind == ChangeKind.MILESTONE:
            control_state = self._attach_milestone_metadata(
                control_state, milestone_group_id, milestone_index,
            )
        elif route.kind == ChangeKind.REFACTOR:
            control_state = self._attach_refactor_baseline(control_state, workspace_path)
        # TASK/ENHANCEMENT: no extra bookkeeping in this slice - see this
        # module's own docstring for why that's a deliberate scoping
        # decision, not an oversight.

        subtask_results: Tuple[SubtaskResult, ...] = ()
        decisions: Tuple[Decision, ...] = ()
        verification_report: Optional[VerificationReport] = None

        if migration_mode == "enforce":
            kernel = getattr(self.workflow_engine, "kernel", None)
            if kernel is not None:
                _log_phase_banner("KNOWLEDGE GUARD")
                from kriya.tools.knowledge import KnowledgeGuard
                knowledge_config = kernel.config.knowledge
                cutoff = kernel.config.llm.knowledge_cutoff
                if knowledge_config.training_cutoff != "2023-12-01":
                    cutoff = knowledge_config.training_cutoff
                guard = KnowledgeGuard(
                    skills_dir=kernel.config.paths.skills,
                    cutoff_date_str=cutoff,
                    offline=knowledge_config.offline_mode,
                    memory_dir=kernel.config.paths.memory,
                    cache_ttl_days=knowledge_config.release_cache_ttl_days,
                )
                goal_gap_report = guard.check_goal(goal, workspace_path)
                resolved_coordinates = [gap["library"] for gap in goal_gap_report.gaps]
                if goal_gap_report.has_gaps and not legacy_kwargs.get("knowledge_risk_confirmed", False):
                    logger.warning(
                        "KnowledgeGuard requires resolution before structured planning: %s",
                        resolved_coordinates,
                    )
                    return WorkflowResult(
                        run_id=run_id, control_state=control_state, route=route,
                        legacy_result={
                            "status": "knowledge_gap",
                            "gap_report": goal_gap_report.to_dict(),
                            "goal": goal,
                            "workspace_path": workspace_path,
                            "run_id": run_id,
                        },
                    )
                logger.info(
                    "Knowledge resolution: %s",
                    "cleared" if not resolved_coordinates else f"acknowledged {resolved_coordinates}",
                )
                legacy_kwargs["resolved_knowledge_coordinates"] = resolved_coordinates

                # One registry load for the authoritative run. Individual
                # subtasks project relevant skills from this shared engine;
                # genuine skill writes still trigger their existing reloads.
                from kriya.skills.skill import SkillEngine
                shared_skill_engine = SkillEngine.from_config(
                    kernel.config, workspace_path=workspace_path,
                )
                shared_skill_engine.discover_and_load()
                legacy_kwargs["skill_engine_override"] = shared_skill_engine
            try:
                _migrate_legacy_controller_ownership(workspace_path, run_id)
            except Exception as exc:
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "reason_codes": ["LEGACY_STATE_MIGRATION_FAILED"],
                    "error": str(exc),
                    "run_id": run_id,
                }
                return WorkflowResult(
                    run_id=run_id,
                    control_state=control_state,
                    route=route,
                    legacy_result=legacy_result,
                )
            # MA7.8 - deliberately NOT wrapped in the shadow path's own
            # broad try/except. Shadow must never take down the real run
            # because shadow ISN'T the real run - enforce mode IS, so a bug
            # here has to surface loudly (this codebase's own "fail loud,
            # never silently do something unsafe" convention), not be
            # mistaken for a different outcome. _run_structured_enforce
            # itself already degrades a genuine SUBTASK-LOOP failure into a
            # clean, normally-shaped failure result (same status/
            # quality_gates_passed shape run_generation_workflow's own
            # ordinary failures use) rather than raising for those - only a
            # real bug in this orchestration code itself propagates as an
            # uncaught exception.
            #
            # _StructuredPlanUnavailable remains a distinct pre-execution
            # outcome, but authoritative mode must never discard its safety
            # model by degrading to whole-goal legacy execution. Bounded
            # repair handles correctable structure; true unavailability is
            # surfaced for review below.
            plan: Optional[EngineeringPlan] = None
            try:
                legacy_result, plan, subtask_results, decisions, verification_report, control_state = (
                    await self._run_structured_enforce(
                        goal, workspace_path, route, run_id, control_context, control_state, legacy_kwargs,
                    )
                )
            except _UnsafeStructuredPlan as e:
                logger.error(f"WorkflowController enforce run {run_id!r}: unsafe structured plan: {e}")
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "PLANNING_ERROR",
                    "recovery": "PLAN_REPAIR",
                    "reason_codes": list(e.reason_codes),
                    "invalid_subtask_ids": list(e.invalid_subtask_ids),
                    "plan_repair_attempts": e.repair_attempts,
                    "error": str(e),
                    "run_id": run_id,
                }
            except _StructuredPlanUnavailable as e:
                logger.error(
                    f"WorkflowController enforce run {run_id!r}: structured plan unavailable "
                    f"({e}); authoritative mode will not degrade to legacy execution."
                )
                legacy_result = {
                    "status": "needs_review",
                    "quality_gates_passed": False,
                    "files": [],
                    "failure_type": "PLANNING_ERROR",
                    "recovery": "PLAN_REPAIR",
                    "reason_codes": ["STRUCTURED_PLAN_UNAVAILABLE"],
                    "error": str(e),
                    "run_id": run_id,
                }

            # Enforce mode is authoritative: repository progress without a
            # durable matching ControlState must stop before another unit
            # can execute.
            save_control_state(workspace_path, control_state)

            return WorkflowResult(
                run_id=run_id, control_state=control_state, route=route,
                legacy_result=legacy_result, subtask_results=subtask_results,
                decisions=decisions, verification_report=verification_report,
            )

        if migration_mode == "shadow":
            try:
                plan, subtask_results, decisions, verification_report, notes = await self._run_structured_shadow(
                    goal, workspace_path, route, run_id, control_context, control_state,
                )
                if plan is not None:
                    control_state = control_state.with_updates(current_plan_hash=plan.content_hash())
                # Always logged, even with zero notes - a clean run (every
                # subtask COMPLETED, nothing to flag) is real, useful
                # evidence the shadow path executed at all, not an absence
                # of output. Discovered via live validation (MA7.1): the
                # previous `if notes:` gate meant a fully successful shadow
                # run left NO trace anywhere - the only way to confirm it
                # happened was inferring it from LLM call timing in the raw
                # log, which is not something a real operator should have
                # to do.
                summary = (
                    f"plan={plan.plan_id if plan else None} subtasks={len(subtask_results)} "
                    f"decisions={len(decisions)} verdict={verification_report.verdict.value if verification_report else None}"
                )
                if notes:
                    summary += f" notes={'; '.join(notes)}"
                logger.info(f"WorkflowController shadow run {run_id!r}: {summary}")
            except Exception as e:
                # The shadow path is strictly observational - see this
                # module's own docstring. A bug in Stage A parsing, plan
                # validation, or SubtaskExecutor must never take down the
                # real generation run underneath it.
                logger.warning(f"WorkflowController shadow run {run_id!r} failed (non-fatal): {e}")
                subtask_results = ()
                decisions = ()
                verification_report = None

        legacy_result = await self._run_legacy_generation(
            goal, workspace_path,
            milestone_group_id=milestone_group_id, milestone_index=milestone_index,
            milestone_total=milestone_total, **legacy_kwargs,
        )

        # MA7.2: ControlState is now durable, not just an in-memory return
        # value - a caller of THIS run can load it back
        # (kriya.control.persistence.load_control_state) after the process
        # exits. Written last, with every bookkeeping update (milestone/
        # refactor baseline, shadow's current_plan_hash) already folded in,
        # so a partial/interrupted run never persists a half-updated state.
        # Best-effort: a write failure here must not fail a real generation
        # run that otherwise succeeded.
        try:
            save_control_state(workspace_path, control_state)
        except Exception as e:
            logger.warning(f"WorkflowController run {run_id!r}: failed to persist ControlState (non-fatal): {e}")

        return WorkflowResult(
            run_id=run_id, control_state=control_state, route=route,
            legacy_result=legacy_result, subtask_results=subtask_results,
            decisions=decisions, verification_report=verification_report,
        )

    async def execute_milestones(self, run_state: Any, workspace_path: str, **milestone_kwargs: Any) -> WorkflowResult:
        """MA7-C4 (2026-08-25 external review) - the milestone-DAG
        counterpart to execute() above: makes WorkflowController the
        single real composition root for ALL FOUR route kinds (TASK/
        ENHANCEMENT/REFACTOR via execute(), MILESTONE via this method),
        closing the architectural split where kriya/cli.py's
        `generate --from-milestones` called kriya/workflow/milestones.py::
        run_milestones() directly, bypassing WorkflowController - and
        therefore ControlState/the rest of the control plane - entirely.

        Deliberately does NOT reimplement or duplicate run_milestones()'s
        own topological DAG execution, dependency-regression checks, or
        established-file threading - same "reuse the mature mechanism,
        don't duplicate it" principle as execute()'s own
        migration_mode="enforce" path (MA7.8). Unlike execute(), there is
        no shadow/legacy distinction here: run_milestones() already IS the
        real, mature mechanism this wraps, not a new implementation being
        compared against an old one, so there is nothing to "observe only"
        or fall back to - this method is only ever called when
        workflow_controller.enabled is true (kriya/cli.py's own gate,
        mirroring _dispatch_generation's identical convention for the
        plain-goal path); when disabled, the CLI still calls
        run_milestones() directly, completely unaffected by this method's
        existence.

        run_state.group_id becomes both run_id and milestone_group_id on
        the resulting ControlState - a milestone sequence's real identity
        already IS its group_id (MilestoneRunState's own field), not
        something this method should mint independently. engineering_
        triage classification here is for the SEQUENCE as a whole (real
        observability parity with execute()'s own always-classify
        behavior) - each individual milestone's own run_generation_
        workflow() call already does its own per-milestone classification
        internally, unaffected either way."""
        route = await self.workflow_engine.engineering_triage.classify(run_state.original_goal, workspace_path)
        control_context = WorkflowControlContext.for_route(route)
        control_state = ControlState.new(
            run_id=run_state.group_id, engineering_route=route,
            process_profile=control_context.process_profile, milestone_group_id=run_state.group_id,
        )
        milestones = list(run_state.milestones)
        plan_blob = json.dumps(
            [milestone.model_dump(mode="json") for milestone in milestones],
            sort_keys=True,
            separators=(",", ":"),
        )
        prior_control_state_ownerless = json_document_is_ownerless(
            control_state_path(workspace_path),
        )
        if prior_control_state_ownerless:
            with open(control_state_path(workspace_path), "r", encoding="utf-8") as handle:
                ControlState.from_dict(json.load(handle))
        contract_registry = load_contract_registry(workspace_path)
        artifact_registry = load_artifact_registry(workspace_path)
        next_milestone_id = next(
            (
                milestone.id for milestone in milestones
                if milestone.id not in run_state.completed_milestone_ids
            ),
            None,
        )
        durable_control_state = load_control_state(workspace_path)
        if (
            durable_control_state is not None
            and durable_control_state.milestone_group_id == run_state.group_id
        ):
            control_state = durable_control_state
        control_state = control_state.with_updates(
            current_milestone_id=next_milestone_id,
            milestone_states={
                milestone.id: (
                    "stale" if milestone.id in run_state.stale_milestone_ids
                    else "done" if milestone.id in run_state.completed_milestone_ids
                    else "pending"
                )
                for milestone in milestones
            },
            current_plan_hash=hashlib.sha256(plan_blob.encode("utf-8")).hexdigest(),
            current_contract_hash=compute_registry_hash(contract_registry.to_dict()),
            current_artifact_registry_hash=compute_registry_hash(artifact_registry.to_dict()),
        )

        try:
            save_control_state(workspace_path, control_state)
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["CONTROL_STATE_PERSISTENCE_FAILED"],
                "persistence_store": "control_state",
                "persistence_error": str(exc),
            }
            return WorkflowResult(
                run_id=run_state.group_id,
                control_state=control_state,
                route=route,
                legacy_result=legacy_result,
            )

        try:
            _migrate_legacy_milestone_ownership(
                workspace_path,
                run_state,
                contract_registry,
                artifact_registry,
                prior_control_state_ownerless=prior_control_state_ownerless,
            )
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["LEGACY_STATE_MIGRATION_FAILED"],
                "persistence_store": "legacy_workspace_state",
                "persistence_error": str(exc),
            }
            return WorkflowResult(
                run_id=run_state.group_id,
                control_state=control_state,
                route=route,
                legacy_result=legacy_result,
            )

        from kriya.workflow.milestones import MilestonePersistenceError, run_milestones
        try:
            legacy_result = await run_milestones(
                self.workflow_engine,
                run_state,
                workspace_path,
                authoritative=True,
                **milestone_kwargs,
            )
        except MilestonePersistenceError as exc:
            legacy_result = exc.to_result(run_state.group_id)

        durable_control_state = load_control_state(workspace_path)
        if (
            durable_control_state is not None
            and durable_control_state.milestone_group_id == run_state.group_id
        ):
            control_state = durable_control_state
        control_state = control_state.with_updates(
            milestone_states={
                milestone.id: (
                    "stale" if milestone.id in run_state.stale_milestone_ids
                    else "done" if milestone.id in run_state.completed_milestone_ids
                    else "pending"
                )
                for milestone in milestones
            },
            current_contract_hash=compute_registry_hash(load_contract_registry(workspace_path).to_dict()),
            current_artifact_registry_hash=compute_registry_hash(load_artifact_registry(workspace_path).to_dict()),
        )

        try:
            save_control_state(workspace_path, control_state)
        except Exception as exc:
            legacy_result = {
                "status": "needs_review",
                "group_id": run_state.group_id,
                "quality_gates_passed": False,
                "reason_codes": ["CONTROL_STATE_PERSISTENCE_FAILED"],
                "persistence_store": "control_state",
                "persistence_error": str(exc),
            }

        return WorkflowResult(
            run_id=run_state.group_id, control_state=control_state, route=route, legacy_result=legacy_result,
        )

    def _attach_milestone_metadata(
        self, control_state: ControlState, milestone_group_id: Optional[str], milestone_index: Optional[int],
    ) -> ControlState:
        if milestone_group_id is None:
            return control_state
        current_milestone_id = (
            f"{milestone_group_id}:{milestone_index}" if milestone_index is not None else milestone_group_id
        )
        return control_state.with_updates(
            milestone_group_id=milestone_group_id, current_milestone_id=current_milestone_id,
        )

    def _attach_refactor_baseline(self, control_state: ControlState, workspace_path: str) -> ControlState:
        base_commit = compute_base_commit(workspace_path)
        tree_hash = compute_tree_hash(workspace_path)
        if base_commit is None and tree_hash is None:
            return control_state
        return control_state.with_updates(base_commit=base_commit, tree_hash=tree_hash)

    async def _run_structured_shadow(
        self, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: ControlState,
    ) -> Tuple[
        Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Decision, ...], Optional[VerificationReport], List[str],
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
        ledger = DecisionLedger()

        plan_text = await self.workflow_engine.planner.run(goal)
        structured_output, parse_issue = parse_planner_structured_output(plan_text)
        if structured_output is None:
            notes.append(f"no structured plan: {parse_issue}")
            return None, (), (), None, notes

        plan = build_engineering_plan_from_planner_output(structured_output, plan_id=run_id, kind=route.kind)
        if plan is None:
            notes.append("structured output parsed but produced zero subtasks")
            return None, (), (), None, notes

        record_plan_created(ledger, plan)

        kernel = getattr(self.workflow_engine, "kernel", None)
        available_tool_names = None
        if kernel is not None:
            try:
                available_tool_names = kernel.registry.list_components("tool")
            except Exception as e:
                logger.debug(f"WorkflowController shadow run {run_id!r}: could not list registered tools: {e}")

        validation = await validate_plan(
            plan, workspace_path=workspace_path, available_tool_names=available_tool_names,
            route=route, triage_service=self.workflow_engine.engineering_triage,
        )
        if not validation.valid:
            notes.append(f"plan failed validation: {validation.errors}")
            return plan, (), ledger.all(), None, notes

        context = await self._build_context(goal, plan, workspace_path, route, control_context, control_state)

        results: List[SubtaskResult] = []
        for subtask_id in topological_subtask_order(plan):
            subtask = plan.subtask_by_id(subtask_id)
            if subtask is None:
                continue

            if subtask.execution_method == ExecutionMethod.TOOL:
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
                result = SubtaskResult(
                    subtask_id=subtask.id, status=SubtaskStatus.NEEDS_REVIEW,
                    execution_method=ExecutionMethod.TOOL.value,
                    error=(
                        "shadow mode does not execute TOOL subtasks for real - doing so would "
                        "have real side effects on the workspace, violating shadow's non-mutating "
                        "contract. This subtask needs a real (non-shadow) execution path."
                    ),
                )
                results.append(result)
                record_subtask_attempt(ledger, plan, result, attempt=1)
                notes.append(f"stopped at subtask {subtask_id!r}: TOOL subtasks are never executed in shadow mode")
                break

            projected = project_for_subtask(context, subtask)
            record_context_package_for_subtask(ledger, plan, subtask_id, projected)
            result = await subtask_executor.execute(
                subtask=subtask, plan=plan, context=projected,
                kernel=kernel, developer_agent=self.workflow_engine.developer,
            )
            results.append(result)
            record_subtask_attempt(ledger, plan, result, attempt=1)
            if result.undeclared_files:
                record_undeclared_file_touch(ledger, plan, result)
            if result.status != SubtaskStatus.COMPLETED:
                notes.append(f"stopped at subtask {subtask_id!r}: status={result.status.value}")
                break

        report = build_verification_report(plan.acceptance_criteria)
        return plan, tuple(results), ledger.all(), report, notes

    async def _run_structured_enforce(
        self, goal: str, workspace_path: str, route: Any, run_id: str,
        control_context: WorkflowControlContext, control_state: ControlState,
        legacy_kwargs: Dict[str, Any],
    ) -> Tuple[
        Dict[str, Any], Optional[EngineeringPlan], Tuple[SubtaskResult, ...],
        Tuple[Decision, ...], Optional[VerificationReport], ControlState,
    ]:
        """MA7.8 - WorkflowController actually OWNING the real outcome for
        the first time. Deliberately does NOT reimplement edit-application/
        compile-test verification/approval gating (SubtaskExecutor still
        only produces file content or a tool result, exactly as documented
        elsewhere in this module) - instead reuses the SAME real, mature
        mechanism kriya/workflow/milestones.py::run_milestones() already
        uses for the analogous "decompose one big goal into smaller real
        generation calls" problem: call the EXISTING, unmodified
        self.workflow_engine.run_generation_workflow() once per SUBTASK
        (instead of once per MILESTONE), in dependency order, threading
        each completed subtask's real written-file content forward as the
        next one's supplementary_context/established_files - the exact
        same render_established_file_context/project_implementation_source
        machinery run_milestones() already calls, not a second copy of it.
        This is real per-subtask retry locality (MA7's own stated goal):
        each subtask gets its OWN run_generation_workflow() call, with its
        own independent retry budget/Quality-Gates loop/approval gate - a
        failure in subtask 2 only ever retries subtask 2's own call, never
        re-triggers subtask 1 or 3.

        MA7-C1 (2026-08-25 external review): each subtask's own call no
        longer runs a FRESH Planner+Architect cycle internally either - the
        already-validated Subtask is now authoritative. build_subtask_plan_
        text()/build_subtask_goal_text() synthesize predetermined_plan/
        predetermined_design (plus predetermined_architect_files from the
        subtask's own planned_files) and pass them into
        run_generation_workflow(), which skips its own stages 2/3 (Plan/
        Architect LLM calls) entirely when supplied - see that method's own
        docstring for the exact mechanism and why it's a dedicated
        parameter, not an overload of resume_state. Every OTHER stage
        (Graph RAG retrieval, skill matching, the Developer/Quality-Gates
        retry loop, failure grounding, attribution, repair, approval,
        regression verification, trace logging) is completely unmodified -
        this closes the "current: validated subtask -> fresh legacy
        Planner/Architect cycle -> Developer" gap the external review
        flagged as the single most important remaining MA6/MA7
        architectural discrepancy, without duplicating or reimplementing
        any of the retry/verification engine SubtaskExecutor deliberately
        still doesn't own. MA6 invariant 4 (a subtask may not silently
        modify undeclared files) is now actually enforced, not just
        detectable - see the subtask loop below.

        Known, honest scope boundaries for this first real cut (not silent
        gaps - each is a deliberate, separate decision):
        - TOOL-tagged subtasks are refused outright (see below) - the same
          reasoning as _run_structured_shadow's own hard stop:
          SubtaskExecutor's TOOL dispatch has zero policy gate a raw shell
          string can't trivially defeat. Enforcing SAFELY requires either a
          real per-tool policy mapping or accepting a materially weaker
          guarantee - a separate, later decision, not bundled into this one.
        - Subtask-spanning resume (2026-08-24): when the CALLER passes
          resume=True or resume_id=<id> (the same flags each subtask's own
          run_generation_workflow() call already accepted), this method now
          ALSO checks for a persisted ControlState from an earlier
          interrupted enforce run of this workspace and, if the current
          re-planned EngineeringPlan's content_hash() and the workspace's
          real git tree_hash/base_commit still match what was recorded
          (kriya/workflow/checkpoint.py's validate_resume_against_reality,
          MA5.9 - genuinely wired to something for the first time), skips
          already-COMPLETED subtasks rather than re-running them, restoring
          their real on-disk file content into established_file_context.
          Any drift in either check -> the resume is refused, exactly like
          the legacy path's own workspace/config/goal drift check, and the
          plan runs fresh from subtask 1. MA6.7's SubtaskCheckpoint/
          resolve_subtask_resume_point (a plan-hash+tree-hash-keyed
          per-subtask mechanism) remains a real, unused alternative for a
          future, more granular resume signal - not needed now that
          ControlState.subtask_states covers the same real gap.
        - Some duplication with _run_structured_shadow's own plan-build/
          validate prologue (Planner call, parse, validate_plan) is
          accepted here rather than refactoring that already-live,
          independently-tested method under time pressure - a real,
          flagged simplification opportunity, not something to silently
          leave undocumented.
        - MA7.9 adds real subtask_retry_locality data to the aggregated
          result (each subtask's own generation_metrics()["calls"], real
          per-subtask retry counts, never spilling into another subtask's
          count by construction) and MA7.10 adds real ArtifactRegistry
          derivation (ArtifactRegistry.derive_from_workspace against the
          final workspace state, only on full success, persisted alongside
          whatever was already recorded) - see the code below for both.
          ContractRegistry has NO equivalent hook: EngineeringPlan/Subtask
          carry no contract-shaped metadata a real registration could key
          off, so real contract invalidation through an actual workflow
          remains open - a separate, later design decision (what should a
          Subtask declare to make a contract registration meaningful),
          not a shallow, invented schema addition bolted on here.
        - Context quality, verified by construction rather than measured:
          each subtask's Developer call goes through the UNMODIFIED
          run_generation_workflow(), so it receives the full legacy Graph
          RAG context (hybrid vector+dependency-graph retrieval) - the
          SAME context quality any ordinary `kriya generate` call already
          gets, strictly richer than _run_structured_shadow's own
          deliberately narrower ContextOrchestrator-only context (see that
          method's own docstring). Enforce mode does not inherit shadow's
          context-quality gap.

A structural, PRE-EXECUTION problem (no parseable plan, zero subtasks,
        failed validation, a TOOL-tagged subtask present) enters a bounded
        local PLAN_REPAIR loop. Two unsuccessful corrections raise
        _UnsafeStructuredPlan and fail closed without a legacy fallback,
        preserving the authoritative write-scope boundary. A
        subtask-loop failure (at least one real run_generation_workflow()
        call has already happened) is NOT this exception - it stays a
        clean, normally-shaped FAILURE result in the aggregated dict
        (same status/quality_gates_passed shape any ordinary
        run_generation_workflow() failure already has), never a legacy
        fallback, since real side effects may already exist in the
        workspace by that point. Only a genuine bug in this method's own
        code propagates as a real (uncaught) exception."""
        ledger = DecisionLedger()

        kernel = getattr(self.workflow_engine, "kernel", None)
        available_tool_names = None
        if kernel is not None:
            try:
                available_tool_names = kernel.registry.list_components("tool")
            except Exception as e:
                logger.debug(f"WorkflowController enforce run {run_id!r}: could not list registered tools: {e}")

        # Subtask-spanning resume (MA5.9 finally wired to something,
        # 2026-08-24) - opt-in only, same convention as
        # run_generation_workflow()'s own resume (no auto-detection from
        # goal-text matching). Loaded BEFORE validate_plan (moved here
        # 2026-08-24, see has_own_established_progress below for why) -
        # MA6 has no separate plan-persistence sidecar the way MA3's
        # milestone flow does (plan_text above is a FRESH Planner call
        # every time), so trusting a persisted ControlState's subtask_states
        # for the actual SKIP decision is only safe when the freshly-
        # rebuilt plan and the real workspace both still match what was
        # recorded - exactly the drift compute_control_plane_hashes/
        # validate_resume_against_reality were built to catch. Any mismatch
        # (or no prior state at all) -> resumed_subtask_states stays empty
        # and the plan runs fresh from subtask 1, same as if resume had
        # never been requested.
        prior_control_state: Optional[ControlState] = None
        if legacy_kwargs.get("resume") or legacy_kwargs.get("resume_id"):
            prior_control_state = load_control_state(workspace_path)
            if prior_control_state is None:
                logger.info(
                    f"WorkflowController enforce run {run_id!r}: resume requested but no prior "
                    "ControlState found for this workspace - starting the plan fresh."
                )

        # Real live-validation finding, 2026-08-24, protocol_encoder_java:
        # after subtask s1 genuinely completed (real Protocol.java on disk)
        # and s2 exhausted its retries, a resumed run's FRESH re-plan
        # correctly triggered extension_points validation (the workspace is
        # no longer empty - _workspace_appears_empty's own exemption
        # correctly stopped applying) - but the Planner, unprompted about
        # continuation, didn't supply one. That validation failure raised
        # _StructuredPlanUnavailable, which fell back to the LEGACY
        # whole-goal path - the exact safety net built for a truly
        # side-effect-free pre-execution failure - except here real side
        # effects already existed (s1's Protocol.java), and the legacy
        # Developer, with no reason to leave it alone, regenerated it from
        # scratch and introduced new bugs that didn't exist before. The
        # established content here is Kriya's OWN prior subtask output for
        # THIS SAME resumed goal, not foreign pre-existing work the
        # extension_points rule exists to protect - so a real resume in
        # progress (a persisted ControlState with at least one completed
        # subtask) exempts extension_points the same honest way a truly
        # empty workspace already does, via the has_own_established_progress
        # flag threaded into validate_plan below.
        has_own_established_progress = bool(
            prior_control_state and any(v == "completed" for v in prior_control_state.subtask_states.values())
        )

        # Authoritative plan correction is bounded and happens before any
        # implementation subtask can run. This keeps planned_files as a
        # hard write boundary while allowing a local Planner to correct a
        # structurally invalid late verification step. Exhaustion never
        # falls back to whole-goal legacy generation, because that would
        # discard the boundary we are trying to establish.
        _log_phase_banner("STRUCTURED PLANNING")
        planner_model = getattr(
            getattr(self.workflow_engine.planner, "role_llm", None), "model", None,
        ) or getattr(getattr(getattr(self.workflow_engine, "kernel", None), "config", None), "llm", None)
        if planner_model is not None and not isinstance(planner_model, str):
            planner_model = getattr(planner_model, "model", None)
        planner_token_cap = getattr(
            getattr(getattr(getattr(self.workflow_engine, "kernel", None), "config", None), "llm", None),
            "planner_max_tokens", None,
        )
        logger.info("Generating validated EngineeringPlan (planner_model=%s)...", planner_model or "default")
        plan_text = await self.workflow_engine.planner.run(
            build_authoritative_planner_request(goal),
            max_tokens_override=planner_token_cap,
            system_prompt_override=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
            json_mode=True,
        )
        _log_phase_banner("PLAN VALIDATION")
        repair_attempts = 0
        while True:
            errors: List[str] = []
            reason_codes: List[str] = []
            invalid_subtask_ids: List[str] = []
            plan: Optional[EngineeringPlan] = None

            structured_output, parse_issue = parse_planner_structured_output(plan_text)
            if structured_output is None:
                errors.append(f"structured plan parse failed: {parse_issue}")
                if parse_issue and "execution_method=tool but no tool_name" in parse_issue:
                    reason_codes.append("TOOL_SUBTASK_MISSING_TOOL_NAME")
                else:
                    reason_codes.append("STRUCTURED_PLAN_PARSE_FAILED")
            else:
                plan = build_engineering_plan_from_planner_output(
                    structured_output, plan_id=run_id, kind=route.kind,
                )
                if plan is None:
                    errors.append("structured output parsed but produced zero subtasks")
                    reason_codes.append("STRUCTURED_PLAN_EMPTY")
                else:
                    tool_subtasks = [
                        st.id for st in plan.subtasks
                        if st.execution_method == ExecutionMethod.TOOL
                    ]
                    if tool_subtasks:
                        invalid_subtask_ids.extend(tool_subtasks)
                        errors.append(
                            f"TOOL-tagged subtask(s) {tool_subtasks!r} are unsupported in enforce mode"
                        )
                        reason_codes.append("TOOL_SUBTASK_UNSUPPORTED_IN_ENFORCE")

                    validation = await validate_plan(
                        plan, workspace_path=workspace_path,
                        available_tool_names=available_tool_names,
                        route=route, triage_service=self.workflow_engine.engineering_triage,
                        resuming_own_established_progress=has_own_established_progress,
                        require_model_planned_files=True,
                        require_semantic_contracts=True,
                    )
                    errors.extend(validation.errors)
                    reason_codes.extend(validation.reason_codes)
                    unbounded_model_subtasks = [
                        st.id for st in plan.subtasks
                        if st.execution_method == ExecutionMethod.MODEL and not st.planned_files
                    ]
                    invalid_subtask_ids.extend(unbounded_model_subtasks)
                    if unbounded_model_subtasks and not any(
                        "declares no planned_files" in error for error in errors
                    ):
                        errors.append(
                            f"MODEL subtask(s) {unbounded_model_subtasks!r} declare no planned_files"
                        )
                        reason_codes.append("MODEL_SUBTASK_MISSING_PLANNED_FILES")

            reason_codes = list(dict.fromkeys(reason_codes))
            invalid_subtask_ids = list(dict.fromkeys(invalid_subtask_ids))
            if plan is not None and not errors:
                ledger.record_and_persist(
                    workspace_path, "structured_plan_validation", run_id=run_id,
                    valid=True, repair_attempts=repair_attempts,
                )
                break

            ledger.record_and_persist(
                workspace_path, "structured_plan_validation", run_id=run_id,
                valid=False, repair_attempts=repair_attempts,
                reason_codes=reason_codes, invalid_subtask_ids=invalid_subtask_ids,
                error_count=len(errors),
            )
            logger.warning(
                "WorkflowController enforce run %r: structured plan validation failed "
                "(attempt=%d, reason_codes=%s, invalid_subtask_ids=%s)",
                run_id, repair_attempts, reason_codes, invalid_subtask_ids,
            )
            if repair_attempts >= 2:
                reason_codes.append("STRUCTURED_PLAN_REPAIR_EXHAUSTED")
                raise _UnsafeStructuredPlan(
                    "structured plan remained unsafe after two bounded repair attempts",
                    reason_codes=list(dict.fromkeys(reason_codes)),
                    invalid_subtask_ids=invalid_subtask_ids,
                    repair_attempts=repair_attempts,
                )

            repair_attempts += 1
            repair_prompt = build_structured_plan_repair_prompt(
                goal, plan_text, errors, reason_codes, repair_attempts,
            )
            ledger.record_and_persist(
                workspace_path, "structured_plan_repair_requested", run_id=run_id,
                repair_attempt=repair_attempts, reason_codes=reason_codes,
                invalid_subtask_ids=invalid_subtask_ids,
            )
            plan_text = await self.workflow_engine.planner.run(
                repair_prompt,
                max_tokens_override=planner_token_cap,
                system_prompt_override=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                json_mode=True,
            )

        assert plan is not None
        logger.info(
            "Plan validation: PASSED (%d subtasks, %d dependency edges, %d planned files, %d global invariants)",
            len(plan.subtasks), sum(len(st.depends_on) for st in plan.subtasks),
            sum(len(st.planned_files) for st in plan.subtasks), len(plan.global_invariants),
        )
        for stage in plan.subtasks:
            logger.info(
                "  %s files=%s depends_on=%s",
                stage.id.upper(), [pf.path for pf in stage.planned_files], stage.depends_on,
            )
        record_plan_created(ledger, plan)
        current_plan_hash = plan.content_hash()

        execution_context = await self._build_context(
            goal, plan, workspace_path, route, control_context, control_state,
        )

        resumed_subtask_states: Dict[str, str] = {}
        if prior_control_state is not None:
            if prior_control_state.current_plan_hash != current_plan_hash:
                logger.warning(
                    f"WorkflowController enforce run {run_id!r}: refusing subtask resume - the "
                    "freshly re-planned goal no longer matches the plan these subtask states "
                    "were recorded against. Starting the plan fresh."
                )
            else:
                resume_check = validate_resume_against_reality(
                    checkpoint_data={
                        "base_commit": prior_control_state.base_commit,
                        "tree_hash": prior_control_state.tree_hash,
                    },
                    workspace_path=workspace_path,
                )
                if resume_check.status != ResumeStatus.OK:
                    logger.warning(
                        f"WorkflowController enforce run {run_id!r}: refusing subtask resume - "
                        f"workspace drift detected ({'; '.join(resume_check.mismatches)}). "
                        "Starting the plan fresh."
                    )
                else:
                    resumed_subtask_states = dict(prior_control_state.subtask_states)
                    logger.info(
                        f"WorkflowController enforce run {run_id!r}: resuming - "
                        f"{sum(1 for v in resumed_subtask_states.values() if v == 'completed')} "
                        "subtask(s) already completed will be skipped."
                    )

        # A refused resume (either branch above) leaves resumed_subtask_states
        # empty while prior_control_state still holds real record of an
        # earlier, now-abandoned plan's completed work - the exact gap that
        # let ProtocolMain.java linger in the real workspace live, 2026-08-25
        # (protocol_encoder_java): see compute_abandoned_plan_files()/
        # quarantine_abandoned_plan_files()'s own docstrings above for the
        # full incident and design. Best-effort/non-fatal, matching this
        # whole method's "a bookkeeping/cleanup convenience never fails the
        # real run" convention - deliberately scoped to only the resume-was-
        # REQUESTED-but-refused case (prior_control_state is only ever loaded
        # at all when the caller opted into resume), not a broader always-on
        # scan of the workspace for anything Kriya might have ever written.
        if prior_control_state is not None and not resumed_subtask_states:
            try:
                new_plan_files = {pf.path for st in plan.subtasks for pf in st.planned_files}
                abandoned_files = compute_abandoned_plan_files(
                    prior_control_state.subtask_states,
                    prior_control_state.subtask_written_files,
                    new_plan_files,
                )
                quarantined = quarantine_abandoned_plan_files(
                    workspace_path, abandoned_files, prior_control_state.run_id,
                )
                if quarantined:
                    logger.warning(
                        f"WorkflowController enforce run {run_id!r}: the abandoned plan's own "
                        f"completed subtask(s) had already applied {quarantined!r} to the real "
                        "workspace - moved to .kriya/abandoned_plan_files/ since no subtask in "
                        "the freshly re-planned goal references them anymore."
                    )
            except Exception as e:
                logger.warning(f"WorkflowController enforce run {run_id!r}: abandoned-plan-file cleanup failed (non-fatal): {e}")

        # base_commit/tree_hash are refreshed here for EVERY route kind, not
        # just REFACTOR (_attach_refactor_baseline above only sets them for
        # that one kind) - subtask resume needs a real workspace-drift
        # signal regardless of what kind of change this plan is, and these
        # are the same real git-derived fields/functions that method
        # already uses, just given a second, route-independent purpose.
        control_state = control_state.with_updates(
            current_plan_hash=current_plan_hash, subtask_states=dict(resumed_subtask_states),
            base_commit=compute_base_commit(workspace_path), tree_hash=compute_tree_hash(workspace_path),
        )

        order = topological_subtask_order(plan)
        total = len(order)
        approved_stage_states = {
            subtask_id: (
                "completed" if resumed_subtask_states.get(subtask_id) == "completed" else "pending"
            )
            for subtask_id in order
        }
        control_state = control_state.with_updates(subtask_states=dict(approved_stage_states))
        # Persist the complete validated plan before any implementation can
        # begin. ControlState remains the compact resume index; this owned
        # document is the durable source for what was approved and how each
        # ordered execution stage progressed.
        save_approved_plan(
            workspace_path, plan.plan_id,
            build_approved_plan_document(
                plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                stage_states=approved_stage_states, lifecycle_state="approved",
            ),
        )
        save_control_state(workspace_path, control_state)
        established_file_context: Dict[str, str] = {}
        subtask_results: List[SubtaskResult] = []
        subtask_call_results: List[Dict[str, Any]] = []
        knowledge_gap_break: Optional[Dict[str, Any]] = None
        recovered_owner_ids: set[str] = set()
        plan_recovery_events: List[Dict[str, Any]] = []

        async def _invoke_bounded_subtask(
            target: Subtask,
            target_position: int,
            *,
            recovery_context: str = "",
        ) -> Dict[str, Any]:
            target_goal = build_subtask_goal_text(target, target_position, total, plan=plan)
            target_files = [pf.path for pf in target.planned_files]
            target_context = project_for_subtask(execution_context, target)
            target_context_text = subtask_executor._render_context_package(target_context)
            kernel = getattr(self.workflow_engine, "kernel", None)
            config = getattr(kernel, "config", None)
            process_profiles = getattr(config, "process_profiles", None)
            return await self.workflow_engine.run_generation_workflow(
                goal=target_goal,
                workspace_path=workspace_path,
                supplementary_context="\n\n".join(filter(None, (
                    build_subtask_constraint_context(goal),
                    build_subtask_semantic_context(plan, target),
                    recovery_context,
                    render_established_file_context(established_file_context),
                    target_context_text,
                ))),
                established_files=sorted(established_file_context.keys()),
                predetermined_plan=build_subtask_plan_text(target),
                predetermined_design=target_goal,
                predetermined_architect_files=target_files,
                allowed_write_relpaths=target_files,
                required_verification=[vm.model_dump(mode="json") for vm in target.verification],
                runtime_verification_required=any(
                    vm.requires_runtime_execution for vm in target.verification
                ),
                strict_spec_compliance=True,
                strict_dependency_index=bool(
                    process_profiles is not None
                    and getattr(process_profiles, "enabled", False) is True
                    and getattr(process_profiles, "enforce_context_depth", False) is True
                ),
                **{k: v for k, v in legacy_kwargs.items() if k != "trace_id_override"},
            )

        for position, subtask_id in enumerate(order, start=1):
            subtask = plan.subtask_by_id(subtask_id)
            if subtask is None:
                continue

            if resumed_subtask_states.get(subtask_id) == "completed":
                logger.info(f"Subtask '{subtask.id}' ({position}/{total}) already completed (resume) - skipping.")
                subtask_results.append(SubtaskResult(
                    subtask_id=subtask.id, status=SubtaskStatus.COMPLETED,
                    execution_method=ExecutionMethod.MODEL.value, error=None,
                ))
                for planned_file in subtask.planned_files:
                    try:
                        with open(
                            os.path.join(workspace_path, planned_file.path), "r", encoding="utf-8", errors="replace",
                        ) as fh:
                            content = fh.read()
                    except OSError as e:
                        logger.warning(
                            f"WorkflowController enforce run {run_id!r}: could not capture established "
                            f"content for {planned_file.path!r} from resumed subtask {subtask_id!r}: {e}"
                        )
                        continue
                    projection = project_implementation_source(
                        content, planned_file.path, _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                        reason="established_by_earlier_subtask",
                    )
                    established_file_context[planned_file.path] = projection.content
                continue

            approved_stage_states[subtask_id] = "in_progress"
            control_state = control_state.with_updates(
                subtask_states={**control_state.subtask_states, subtask_id: "in_progress"},
            )
            save_approved_plan(
                workspace_path, plan.plan_id,
                build_approved_plan_document(
                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                    stage_states=approved_stage_states, lifecycle_state="in_progress",
                ),
            )
            save_control_state(workspace_path, control_state)

            _log_phase_banner(f"SUBTASK '{subtask.id}' ({position}/{total}): {subtask.description[:40]}")
            logger.info(
                "Current subtask: %s/%s id=%s files=%s depends_on=%s relevant_invariants=%s",
                position, total, subtask.id, [pf.path for pf in subtask.planned_files],
                subtask.depends_on, subtask.relevant_global_invariants,
            )
            # MA7-C1 (2026-08-25 external review): the validated Subtask is
            # now authoritative - predetermined_plan/predetermined_design/
            # predetermined_architect_files (kriya/workflow/workflow.py)
            # make run_generation_workflow() skip its own fresh Planner+
            # Architect cycle entirely and use this subtask's own already-
            # decided description/file list instead, while every other real
            # guarantee (Graph RAG retrieval, skill matching, the Developer/
            # Quality-Gates retry loop, failure grounding, attribution,
            # repair, approval, regression verification, trace logging)
            # stays completely intact and un-duplicated - see that method's
            # own docstring for the full account of what is and isn't
            # skipped.
            predetermined_files = [pf.path for pf in subtask.planned_files]
            call_result = await _invoke_bounded_subtask(subtask, position)
            scope_conflict = call_result.get("plan_scope_conflict") or {}
            owner_map = resolve_scope_conflict_owners(
                plan, list(scope_conflict.get("required_files", [])), subtask,
            ) if scope_conflict else {}
            required_scope_files = set(scope_conflict.get("required_files", []))
            resolved_scope_files = {
                path for owner_paths in owner_map.values() for path in owner_paths
            }
            if len(owner_map) == 1 and resolved_scope_files == required_scope_files:
                owner_id, required_owner_files = next(iter(owner_map.items()))
                owner = plan.subtask_by_id(owner_id)
                if owner is not None and owner_id not in recovered_owner_ids:
                    recovered_owner_ids.add(owner_id)
                    invalidated = transitive_subtask_dependents(plan, owner_id)
                    for invalidated_id in invalidated:
                        approved_stage_states[invalidated_id] = "pending"
                    approved_stage_states[owner_id] = "in_progress"
                    control_state = control_state.with_updates(
                        subtask_states={
                            **control_state.subtask_states,
                            **{invalidated_id: "pending" for invalidated_id in invalidated},
                            owner_id: "in_progress",
                        },
                    )
                    save_approved_plan(
                        workspace_path, plan.plan_id,
                        build_approved_plan_document(
                            plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                            stage_states=approved_stage_states, lifecycle_state="recovering",
                        ),
                    )
                    save_control_state(workspace_path, control_state)
                    owner_position = order.index(owner_id) + 1
                    recovery_context = (
                        "--- authoritative plan recovery ---\n"
                        f"Failed consumer: {subtask.id}\n"
                        f"Reopened owner: {owner_id}\n"
                        f"Required repair files: {json.dumps(required_owner_files)}\n"
                        f"Failure type: {scope_conflict.get('failure_type')}\n"
                        "Repair only the reopened owner's approved files. Preserve its provided "
                        "contracts and all relevant global invariants."
                    )
                    owner_result = await _invoke_bounded_subtask(
                        owner, owner_position, recovery_context=recovery_context,
                    )
                    owner_declared = {pf.path for pf in owner.planned_files}
                    owner_undeclared = sorted(
                        set(owner_result.get("files") or []) - owner_declared
                    )
                    owner_written = set(owner_result.get("files") or [])
                    owner_passed = (
                        bool(owner_result.get("quality_gates_passed"))
                        and not owner_undeclared
                        and set(required_owner_files).issubset(owner_written)
                    )
                    plan_recovery_events.append({
                        "failed_subtask": subtask.id,
                        "reopened_owner": owner_id,
                        "required_repair_files": sorted(required_owner_files),
                        "classification": scope_conflict.get(
                            "classification", "PLAN_SCOPE_INSUFFICIENT",
                        ),
                        "reason": scope_conflict.get("reason"),
                        "invalidated_subtasks": invalidated,
                        "owner_recovery_passed": owner_passed,
                    })
                    approved_stage_states[owner_id] = "completed" if owner_passed else "needs_review"
                    control_state = control_state.with_updates(
                        subtask_states={
                            **control_state.subtask_states,
                            owner_id: "completed" if owner_passed else "needs_review",
                        },
                        subtask_written_files={
                            **control_state.subtask_written_files,
                            owner_id: sorted(owner_result.get("files") or []),
                        },
                    )
                    save_approved_plan(
                        workspace_path, plan.plan_id,
                        build_approved_plan_document(
                            plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                            stage_states=approved_stage_states,
                            lifecycle_state="in_progress" if owner_passed else "needs_review",
                        ),
                    )
                    save_control_state(workspace_path, control_state)
                    if owner_passed:
                        for path in owner_result.get("files") or []:
                            try:
                                with open(
                                    os.path.join(workspace_path, path), "r",
                                    encoding="utf-8", errors="replace",
                                ) as fh:
                                    owner_content = fh.read()
                            except OSError:
                                continue
                            projection = project_implementation_source(
                                owner_content, path,
                                _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                                reason="recovered_upstream_subtask",
                            )
                            established_file_context[path] = projection.content
                        approved_stage_states[subtask.id] = "in_progress"
                        control_state = control_state.with_updates(
                            subtask_states={
                                **control_state.subtask_states,
                                subtask.id: "in_progress",
                            },
                        )
                        save_approved_plan(
                            workspace_path, plan.plan_id,
                            build_approved_plan_document(
                                plan, plan_hash=current_plan_hash,
                                repair_attempts=repair_attempts,
                                stage_states=approved_stage_states,
                                lifecycle_state="in_progress",
                            ),
                        )
                        save_control_state(workspace_path, control_state)
                        call_result = await _invoke_bounded_subtask(
                            subtask, position,
                            recovery_context=(
                                "--- upstream recovery completed ---\n"
                                f"Upstream owner {owner_id} was repaired and re-verified. "
                                "Re-run this consumer against the updated workspace."
                            ),
                        )
                    else:
                        for result_index, prior_result in enumerate(subtask_results):
                            if prior_result.subtask_id == owner_id:
                                subtask_results[result_index] = SubtaskResult(
                                    subtask_id=owner_id,
                                    status=SubtaskStatus.NEEDS_REVIEW,
                                    execution_method=ExecutionMethod.MODEL.value,
                                    error="upstream owner failed bounded plan recovery verification",
                                    reason_codes=("PLAN_RECOVERY_OWNER_FAILED",),
                                )
                                break
            subtask_call_results.append(call_result)

            quality_gates_passed = bool(call_result.get("quality_gates_passed"))
            # MA6 invariant 4 (kriya/workflow/workflow_types.py::SubtaskResult's
            # own docstring: "a subtask may not modify undeclared files
            # silently... deciding what to do about it is the calling
            # orchestrator's job") - finally enforced here, not just
            # detectable. Skipped entirely when the subtask declared NO
            # planned_files at all (nothing to enforce against, would
            # otherwise guarantee a false violation on every file written).
            undeclared_files = (
                sorted(set(call_result.get("files") or []) - set(predetermined_files))
                if predetermined_files else []
            )
            verification_status, verification_error, verification_reason_codes = (
                _evaluate_subtask_verification(subtask, call_result)
            )
            passed = (
                quality_gates_passed
                and not undeclared_files
                and verification_status == SubtaskStatus.COMPLETED
            )
            if not quality_gates_passed:
                scope_conflict = call_result.get("plan_scope_conflict")
                if scope_conflict:
                    error = (
                        "subtask repair requires approved-plan scope revision; grounded required "
                        f"files {scope_conflict.get('required_files', [])!r} are outside this stage's "
                        f"allowed files {scope_conflict.get('allowed_files', [])!r}"
                    )
                else:
                    error = f"subtask did not pass Quality Gates (status={call_result.get('status')!r})"
            elif undeclared_files:
                error = (
                    f"subtask wrote file(s) outside its own declared planned_files scope: "
                    f"{undeclared_files!r} (declared: {predetermined_files!r}) - refusing to accept "
                    "a Quality-Gates-passed result that silently broadened the validated subtask's scope"
                )
            elif verification_error:
                error = verification_error
            else:
                error = None
            result = SubtaskResult(
                subtask_id=subtask.id,
                status=(
                    SubtaskStatus.COMPLETED if passed
                    else SubtaskStatus.NEEDS_REVIEW if call_result.get("plan_scope_conflict")
                    else SubtaskStatus.FAILED if not quality_gates_passed or undeclared_files
                    else verification_status
                ),
                execution_method=ExecutionMethod.MODEL.value,
                undeclared_files=tuple(undeclared_files), error=error,
                reason_codes=(
                    ("PLAN_SCOPE_REVISION_REQUIRED",)
                    if call_result.get("plan_scope_conflict") else verification_reason_codes
                ),
            )
            subtask_results.append(result)
            record_subtask_attempt(ledger, plan, result, attempt=1)

            # Persisted incrementally and fail-closed, not only at
            # the end of the whole plan - a mid-plan crash must leave
            # accurate resumable state behind, matching run_milestones()'s
            # own completed_milestone_ids sidecar convention.
            control_state = control_state.with_updates(
                subtask_states={**control_state.subtask_states, subtask.id: result.status.value},
                # The real, applied file list (not the plan's mere upfront
                # planned_files declaration) - see ControlState.subtask_
                # written_files's own docstring for why a later re-plan
                # needs this to detect abandoned residue.
                subtask_written_files={
                    **control_state.subtask_written_files,
                    subtask.id: sorted(call_result.get("files", [])),
                },
            )
            approved_stage_states[subtask.id] = result.status.value
            plan_lifecycle_state = (
                "in_progress" if passed else
                "needs_review" if result.status == SubtaskStatus.NEEDS_REVIEW else "failed"
            )
            save_approved_plan(
                workspace_path, plan.plan_id,
                build_approved_plan_document(
                    plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                    stage_states=approved_stage_states, lifecycle_state=plan_lifecycle_state,
                ),
            )
            save_control_state(workspace_path, control_state)

            if not passed:
                logger.warning(
                    f"WorkflowController enforce run {run_id!r}: stopped at subtask {subtask_id!r} "
                    f"({position}/{total}) - did not pass Quality Gates."
                )
                # Found live, 2026-08-25 (ignite_qpid_protocol): a subtask's own
                # internal run_generation_workflow() call can hit KnowledgeGuard's
                # stage-0 gap check (kriya/workflow/workflow.py) and return
                # status="knowledge_gap" - previously this got flattened into an
                # ordinary "did not pass Quality Gates" failure with the real
                # gap_report silently discarded, so the CLI's own already-built
                # confirmation UX (interactive prompt / --knowledge-policy / -y,
                # kriya/cli.py's `res.get("status") == "knowledge_gap"` check)
                # could never fire for enforce mode at all - the run just produced
                # zero files with zero visible explanation. Scoped to the SAME
                # safety boundary _StructuredPlanUnavailable already uses (only
                # when literally nothing has been established yet - a knowledge
                # gap on a LATER subtask, after real prior work exists, stays the
                # ordinary generic failure rather than inviting a fresh top-level
                # retry that would silently re-run already-completed subtasks).
                if call_result.get("status") == "knowledge_gap" and not established_file_context:
                    knowledge_gap_break = {
                        "status": "knowledge_gap",
                        "gap_report": call_result.get("gap_report"),
                        "run_id": call_result.get("run_id", run_id),
                    }
                break

            for path in call_result.get("files", []):
                try:
                    with open(os.path.join(workspace_path, path), "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError as e:
                    logger.warning(
                        f"WorkflowController enforce run {run_id!r}: could not capture established "
                        f"content for {path!r} after subtask {subtask_id!r}: {e}"
                    )
                    continue
                projection = project_implementation_source(
                    content, path, _ENFORCE_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                    reason="established_by_earlier_subtask",
                )
                established_file_context[path] = projection.content

        all_completed = len(subtask_results) == total and all(
            r.status == SubtaskStatus.COMPLETED for r in subtask_results
        )
        needs_review = any(r.status == SubtaskStatus.NEEDS_REVIEW for r in subtask_results)
        final_plan_lifecycle = "completed" if all_completed else "needs_review" if needs_review else "failed"
        save_approved_plan(
            workspace_path, plan.plan_id,
            build_approved_plan_document(
                plan, plan_hash=current_plan_hash, repair_attempts=repair_attempts,
                stage_states=approved_stage_states, lifecycle_state=final_plan_lifecycle,
            ),
        )
        aggregated: Dict[str, Any] = {
            "status": "success" if all_completed else "needs_review" if needs_review else "failed",
            "quality_gates_passed": all_completed,
            "run_id": run_id,
            "subtask_results": [r.to_dict() for r in subtask_results],
            "files": sorted(established_file_context.keys()),
        }
        if knowledge_gap_break is not None:
            # Overrides status/run_id above (not quality_gates_passed/subtask_
            # results/files - those stay honest) so the CLI's real, already-built
            # knowledge_gap confirmation flow (kriya/cli.py) can engage exactly
            # as it already does for the legacy/shadow paths, instead of a silent
            # generic failure - see the knowledge_gap_break assignment above for
            # the full incident this closes.
            aggregated["status"] = knowledge_gap_break["status"]
            aggregated["gap_report"] = knowledge_gap_break["gap_report"]
            aggregated["run_id"] = knowledge_gap_break["run_id"]
        scope_conflict_result = next(
            (result.get("plan_scope_conflict") for result in subtask_call_results
             if result.get("plan_scope_conflict")),
            None,
        )
        if scope_conflict_result is not None:
            aggregated["failure_type"] = "PLANNING_ERROR"
            aggregated["recovery"] = "PLAN_REPAIR"
            aggregated["reason_codes"] = ["PLAN_SCOPE_REVISION_REQUIRED"]
            aggregated["plan_scope_conflict"] = scope_conflict_result
        if plan_recovery_events:
            aggregated["plan_recovery_events"] = plan_recovery_events
        if subtask_call_results:
            aggregated["last_subtask_result"] = subtask_call_results[-1]
            # MA7.9 - real subtask retry-locality data, not a guess: each
            # entry's own generation_metrics()["calls"] (workflow.py's own
            # GenerationState.generation_metrics) is that ONE subtask's
            # OWN run_generation_workflow() invocation's real retry count -
            # by construction (each subtask gets its own, fully separate
            # call), a retry can never spill into a different subtask's
            # count. This is what makes "most failures retry one subtask,
            # not the whole goal" (MA7's own stated validation target)
            # actually measurable now, rather than asserted.
            aggregated["subtask_retry_locality"] = [
                {
                    "subtask_id": r.subtask_id,
                    "generation_calls": cr.get("generation_metrics", {}).get("calls"),
                    "quality_gates_passed": bool(cr.get("quality_gates_passed")),
                }
                for r, cr in zip(subtask_results, subtask_call_results, strict=False)
            ]

        # MA7.10 - real ArtifactRegistry derivation through an actual
        # workflow, not a direct constructor unit test: once every subtask
        # has really applied its files to the real workspace (only on full
        # success - a partial/failed run's workspace state is not a
        # trustworthy basis for real build-artifact facts), derive
        # whatever real artifacts now exist there and persist them
        # alongside whatever was already recorded. ContractRegistry has no
        # equivalent hook yet - EngineeringPlan/Subtask carry no
        # contract-shaped metadata a real registration could key off, so
        # real contract invalidation through an actual workflow remains a
        # separate, unclosed gap (see this method's own module docstring
        # for the honest scope note) rather than a shallow, invented
        # schema addition here.
        if all_completed:
            try:
                milestone_id = control_state.current_milestone_id or run_id
                artifact_registry = load_artifact_registry(workspace_path)
                derived = ArtifactRegistry.derive_from_workspace(
                    artifact_registry, workspace_path, milestone_id,
                )
                for record in derived:
                    artifact_registry.record(record)
                if derived:
                    save_artifact_registry(workspace_path, artifact_registry)
                    aggregated["derived_artifacts"] = [r.to_dict() for r in derived]
            except Exception as e:
                logger.error(f"WorkflowController enforce run {run_id!r}: artifact derivation failed: {e}")
                aggregated["status"] = "needs_review"
                aggregated["quality_gates_passed"] = False
                aggregated["artifact_error"] = str(e)

        report = build_verification_report(plan.acceptance_criteria)
        return aggregated, plan, tuple(subtask_results), ledger.all(), report, control_state

    async def _build_context(
        self, goal: str, plan: EngineeringPlan, workspace_path: str, route: Any,
        control_context: WorkflowControlContext, control_state: ControlState,
    ) -> ContextPackage:
        """MA7.2: routes through the real ContextOrchestrator (MA5.7) for
        strategy selection, token-budget-aware assembly, and real
        contract_entries/artifact_entries (loaded from whatever
        ContractRegistry/ArtifactRegistry already have persisted for this
        workspace - kriya/control/persistence.py, empty registries are a
        legitimate, honest result for a workspace with no prior milestone
        history, not an error). `milestone=None`: WorkflowController does
        not receive a real MilestoneV2 object today, only bare
        milestone_group_id/milestone_index identifiers (MA6.9) - passing
        None here rather than fabricating one is the same "don't guess"
        discipline as raw_rag_context=None below.

        What ContextOrchestrator.build() does NOT give this path: the
        actual current-on-disk content of files the plan's subtasks will
        touch - its own reuse boundary only accepts already-assembled
        `established_file_context` (labeled, by that parameter's real
        semantics, as content from an EARLIER COMPLETED MILESTONE) or an
        opaque `raw_rag_context` blob. Neither is an honest label for "this
        subtask's own planned file, read fresh off disk right now" -
        mislabeling it as established_file_context would corrupt exactly
        the kind of real provenance this whole control-plane initiative
        exists to guarantee (kriya/workflow/context_package.py's own
        CONTEXT_SOURCE_TYPES docstring). So that content is still read
        directly here, with the SAME correct
        source_type="named_in_request" labeling the pre-MA7.2 version of
        this method already used, and layered on top of
        ContextOrchestrator's own package via with_changes() rather than
        threaded through build() under a false pretense."""
        contract_registry = load_contract_registry(workspace_path)
        artifact_registry = load_artifact_registry(workspace_path)
        contract_entries = tuple(contract_entry_from_record(r) for r in contract_registry.all_records())
        artifact_entries = tuple(artifact_entry_from_record(r) for r in artifact_registry.all_records())

        base_package = await ContextOrchestrator().build(
            request=goal, route=route, profile=control_context.process_profile,
            workspace_path=workspace_path, milestone=None, control_state=control_state,
            contract_entries=contract_entries, artifact_entries=artifact_entries,
        )

        items = []
        seen = set()
        for subtask in plan.subtasks:
            for pf in subtask.planned_files:
                if pf.path in seen:
                    continue
                seen.add(pf.path)
                full_path = os.path.join(workspace_path, pf.path)
                if not os.path.isfile(full_path):
                    continue
                try:
                    with open(full_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception as e:
                    logger.debug(f"WorkflowController shadow context: could not read {pf.path!r}: {e}")
                    continue
                items.append(make_context_item(
                    path=pf.path, content=content, reason="planned file, current on-disk content",
                    source_type="named_in_request", trust_level="repository",
                ))

        return base_package.with_changes(relevant_files=base_package.relevant_files + tuple(items))

    async def _run_legacy_generation(self, goal: str, workspace_path: str, **legacy_kwargs: Any) -> Dict[str, Any]:
        """MA6.10's LegacyGenerationAdapter, in its very first, unmodified
        form: exactly the call every existing caller (kriya/cli.py's
        `generate` command) already makes, so this slice changes zero
        behavior of the actual generation pipeline - only wraps it with
        control-plane bookkeeping (and, in shadow mode, an observational
        run alongside it) around the outside."""
        return await self.workflow_engine.run_generation_workflow(goal, workspace_path, **legacy_kwargs)
