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
import re
import shutil
from dataclasses import dataclass, field
from enum import Enum
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
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow.migration import (
    MigrationResolution, MigrationResolutionStatus, MigrationValidationScope,
    find_migration_incomplete, resolve_migration_resolution,
)
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    FileOwnershipRelation,
    PlannedFile,
    Subtask,
    VerificationMethodType,
    build_engineering_plan_from_planner_output,
)
from kriya.workflow.edit_safety import (
    StagedFileWrite,
    commit_revision_grounded_batch,
    read_file_revision,
)
from kriya.workflow.plan_validation import canonicalize_planned_file_actions, validate_plan
from kriya.workflow.planning_diagnostics import (
    bounded_repository_evidence,
    persist_planning_attempt_diagnostic,
)
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
from kriya.workflow.worktree import create_git_worktree, remove_git_worktree


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

# PRV-03 hardened finding (2026-08-27): a grounded owner's own repair can
# surface a FURTHER grounded owner outside the just-revised scope (e.g.
# CustomerController's fix needs Customer.java, whose own fix needs
# CustomerService.java) - a chain, not a single hop. The recovery loop
# below re-attempts revise_plan_for_grounded_scope_owner for each new
# PLAN_SCOPE_DEFECT it sees, bounded by this constant so a pathological
# oscillating diagnosis can't loop forever; once exhausted it falls
# through to the existing upstream-owner fallback / termination path.
_MAX_PLAN_SCOPE_REVISION_ATTEMPTS = 3


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
    invariant_statements = {gi.id: gi.statement for gi in plan.global_invariants}
    payload = {
        "local_description": subtask.description,
        "planned_files": [pf.path for pf in subtask.planned_files],
        "relevant_global_invariants": [
            invariant_statements.get(gi_id, gi_id) for gi_id in subtask.relevant_global_invariant_ids
        ],
        "upstream_contracts": upstream,
        "downstream_requirements": downstream,
        "verification_targets": [vm.description for vm in subtask.verification],
        "runtime_execution_required": any(
            vm.requires_application_runtime for vm in subtask.verification
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
    '{"global_invariants": [{"id": "gi1", "statement": "..."}], "subtasks": [{"id": "s1", '
    '"description": "...", "execution_method": "model", "execution_role": "implementation", '
    '"depends_on": [], "planned_files": [{"path": "...", "action": "create|modify|delete"}], '
    '"provides": ["..."], "requires": [], "relevant_global_invariant_ids": ["gi1"], "verification": '
    '[{"type": "tool", "description": "...", "tool_name": "compile", '
    '"verifier_kind": "compile", '
    '"requires_runtime_execution": false}], '
    '"acceptance_criteria_ids": ["ac1"]}], "acceptance_criteria": '
    '[{"id": "ac1", "description": "...", "method": "judgment"}], '
    '"extension_points": [], "refactor_baseline": null}. '
    "Each global invariant has a short stable id and a human-readable statement; a subtask "
    "references it via relevant_global_invariant_ids using that exact id, never by restating or "
    "paraphrasing the statement text - a subtask relevant to only PART of a compound invariant "
    "still references the whole invariant's id, it does not invent a new id or a partial "
    "statement. Never reference an id that isn't declared in global_invariants, and never invent "
    "one on a subtask that wasn't first declared in global_invariants. "
    "Every model subtask has an execution_role: implementation or verification. An implementation "
    "subtask must own every file it may change in planned_files. A verification subtask (e.g. "
    "\"run the regression suite and confirm existing behavior is preserved\") MUST set "
    "execution_role=verification, planned_files=[] (it never writes anything), and at least one "
    "real entry in verification - never invent a file for it. Each planned path must be owned by "
    "exactly one implementation subtask. Do not emit an implementation subtask solely to analyze, "
    "inspect, research, or explain code; the implementation subtask performs any necessary "
    "analysis before editing its owned file. A verification entry must always be an object, never "
    "a string. A judgment verification must omit tool_name. A tool verification must name a real "
    "registered tool; use tool_name=compile/verifier_kind=compile for compilation and "
    "tool_name=test/verifier_kind=test for tests. Use verifier_kind=application_runtime "
    "and requires_runtime_execution=true TOGETHER, only "
    "when the plan explicitly requires executing the application - never set one without "
    "the other; a verifier claiming application_runtime kind without requires_runtime_"
    "execution=true will never actually be executed. Every "
    "acceptance criterion may be assigned only to a stage capable of demonstrating it. Every "
    "requires value must exactly equal one provides value from exactly one "
    "declared dependency. Preserve goal-derived invariants without inventing unspecified choices. "
    "A stage whose tests need build/test tooling (a Maven/Gradle/npm/pip manifest, a test "
    "framework dependency) that another stage's planned_files owns must declare that manifest "
    "stage's provides value in its own requires and depends_on - execution order is not "
    "guaranteed by planned_files or list position alone, only by depends_on. "
    "If a stage's entrypoint may terminate the running process (an explicit exit/return-code "
    "path, not just falling off the end of a function) and another stage's tests are expected to "
    "exercise that entrypoint directly, plan them so the process-terminating behavior stays "
    "separable from the logic the tests actually invoke - e.g. the terminating call sits in a "
    "thin wrapper the tests never call directly, while the tests target the underlying logic "
    "that returns a result instead of terminating. This applies to any process-termination "
    "mechanism (not just one language's), and does not require a specific method name or file "
    "structure - only that a directly-tested code path and a process-terminating code path stay "
    "separate enough that invoking the tested path in-process cannot kill the test process itself. "
    "When two subtasks' outputs are meant to compose into ONE behavior - one subtask's file is "
    "meant to be called, imported, or otherwise directly used by another's, not merely scheduled "
    "before it - add an object to a top-level integration_relationships list: {\"id\": \"ir1\", "
    "\"kind\": \"uses\", \"producer_subtask_ids\": [\"s3\"], \"consumer_subtask_ids\": [\"s2\"], "
    "\"relationship_statement\": \"...\"} (kind is one of uses/provides_to/configures/implements/"
    "verifies/depends_on). This is a STRONGER claim than depends_on/provides/requires (which only "
    "order execution and name a producer) - only add it when the goal actually requires the "
    "consumer's own generated code to reference the producer's artifact, e.g. two subtasks would "
    "otherwise each be free to implement the same concern independently without ever composing "
    "(a real live incident: a storage-service subtask and a main-application subtask that reads/"
    "writes storage each passed their own local checks while the main application never actually "
    "used the storage service it depended on). Omit the list entirely when no subtask's output is "
    "meant to be directly used by another's code - most plans need none."
)


def _authoritative_planner_extension_candidates(
    workspace_path: str, max_files: int = 100, *, goal: str = "",
) -> List[str]:
    """Return bounded, goal-ranked local path evidence for planning.

    Ranking is stack-neutral and content-free: camel-case/path tokens that
    overlap the request sort ahead of unrelated paths, while path order keeps
    ties deterministic. This prevents a large repository's first 100
    lexicographic files from crowding the likely existing owner out of the
    planner's bounded evidence.
    """
    ignored_dirs = {
        ".git", ".kriya", "__pycache__", "node_modules", "target", ".venv",
        "venv", "logs", "memory",
    }
    ignored_root_files = {"kriya.yaml", "kriya.yml"}
    candidates: List[str] = []
    if not os.path.isdir(workspace_path):
        return candidates
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = sorted(name for name in dirs if name not in ignored_dirs)
        is_root = os.path.abspath(root) == os.path.abspath(workspace_path)
        for name in sorted(filenames):
            if is_root and (name in ignored_root_files or name.lower().endswith(".md")):
                continue
            candidates.append(os.path.relpath(os.path.join(root, name), workspace_path))
    if goal:
        def tokens(value: str) -> set[str]:
            expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
            return {
                token.lower() for token in re.split(r"[^A-Za-z0-9]+", expanded)
                if len(token) > 1
            }

        goal_tokens = tokens(goal)
        candidates.sort(key=lambda path: (-len(tokens(path) & goal_tokens), path))
    return candidates[:max_files]


def build_structured_plan_repair_prompt(
    goal: str,
    previous_plan_text: str,
    errors: List[str],
    reason_codes: List[str],
    repair_attempt: int,
    *,
    route_kind: Optional[ChangeKind] = None,
    extension_candidates: Optional[List[str]] = None,
    repository_candidates: Optional[List[str]] = None,
    must_preserve: Optional[List[str]] = None,
) -> str:
    """Build a bounded local-only correction request for the complete plan.

    must_preserve (PRV-05 run #8, MA8 - kriya/workflow/obligations.py):
    human-readable descriptions of PLAN_STRUCTURAL_VALIDITY obligations the
    PREVIOUS draft already satisfied (computed by the caller from the
    ObligationLedger, not re-derived here) - found live, run #8: the
    Planner fixed refactor_baseline on repair attempt 2 but silently
    regressed an already-fixed planned-file action, because the repair
    prompt only ever showed the CURRENT attempt's error list, with nothing
    telling the model that both constraints had to hold simultaneously.
    This is a best-effort PROMPT instruction, not an enforcement mechanism
    - the ledger's own regression detection (surfaced by the caller as
    PLAN_REPAIR_OSCILLATION/PLAN_REPAIR_NON_CONVERGENCE) is what actually
    catches it if the model ignores this anyway."""
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
            "check, set its execution_role to verification (keep it as its own subtask, keep its "
            "depends_on and acceptance_criteria_ids) and give it at least one concrete verification "
            "entry - do NOT remove it or invent a planned_files path for it. If it genuinely edits "
            "files, retain it as execution_role=implementation with the exact real planned_files it "
            "owns. Never invent a fake file for a check.\n"
        )
    if "STRUCTURED_PLAN_SCHEMA_INVALID" in reason_codes:
        targeted_correction += (
            "- Repair every schema-invalid field to the system contract. In particular, each "
            "verification item must be an object with type, description, verifier_kind, and "
            "requires_runtime_execution; never use a string verification item.\n"
        )
    if "SUBTASK_REQUIREMENT_UNPROVIDED" in reason_codes:
        targeted_correction += (
            "- Replace each unprovided requires value with the exact, character-for-character "
            "provides value exported by its declared upstream dependency. Do not paraphrase "
            "capability names.\n"
        )
    if "AMBIGUOUS_PLANNED_FILE_OWNERSHIP" in reason_codes:
        candidates = repository_candidates or []
        targeted_correction += (
            "- Each planned file path must be owned by exactly one MODEL subtask. For every "
            "duplicated path named in the validation errors, retain it only on the subtask that "
            "actually performs that file's implementation change. REMOVE any separate MODEL "
            "subtask whose sole purpose is to analyze, inspect, research, or explain that same "
            "file; fold necessary analysis into the implementation subtask. Express downstream checks as "
            "verification or acceptance criteria on an appropriate implementation subtask; do "
            "not duplicate a path merely so another subtask can compile, test, inspect, or use "
            "it. Preserve real dependency edges and do not rename, replace, or invent files to "
            "avoid the ownership conflict. Existing local paths available as ownership evidence: "
            f"{json.dumps(candidates)}. For modify/delete, use an exact relevant existing path; "
            "do not create or rename a parallel artifact when a relevant owner exists.\n"
        )
    if "EXTENSION_POINT_REQUIRED" in reason_codes:
        candidates = extension_candidates or []
        targeted_correction += (
            f"- The {route_kind.value if route_kind else 'current'} route requires a real "
            "extension point. Set extension_points using only these existing relative paths: "
            f"{json.dumps(candidates)}. Do not invent a path.\n"
        )
    if "REFACTOR_BASELINE_MISSING" in reason_codes:
        targeted_correction += (
            "- Set refactor_baseline to the exact id (a string like \"s3\") of the subtask whose "
            "completed output the equivalence verification should be ordered against - typically "
            "the LAST implementation subtask in dependency order, not an empty string, null, or a "
            "prose description.\n"
        )
    if "PLANNED_FILE_ACTION_MISMATCH" in reason_codes:
        targeted_correction += (
            "- For each planned file the errors name as an action mismatch: if the file does not "
            "yet exist in the repository evidence, its action must be \"create\"; if it already "
            "exists, its action must be \"modify\" (or \"delete\"). Do not change which subtask "
            "owns the file, only its action.\n"
        )
    if "UNKNOWN_GLOBAL_INVARIANT" in reason_codes:
        targeted_correction += (
            "- For each subtask the errors name as referencing an unknown global invariant id: "
            "replace that entry with one of the declared ids listed in the same error (shown as "
            "\"declared ids are [...]\"), the one whose statement is actually relevant to this "
            "subtask. Do not invent a new id, do not restate the invariant's statement text as the "
            "id, and do not add a new entry to global_invariants unless the goal states a real "
            "constraint no existing invariant covers. A subtask relevant to only part of a compound "
            "invariant still references that invariant's existing id whole - it does not split it "
            "into a new id or a partial statement. Existing global invariant ids from the previous "
            "draft must be preserved unchanged (same id, same statement) unless the invariant "
            "itself is being genuinely removed or replaced.\n"
        )
    must_fix_section = ""
    if must_preserve:
        must_fix_section = (
            "\nMUST PRESERVE (already correct in the previous draft above - do not undo any of "
            "these while fixing the items below; a corrected plan that changes one of these back "
            "is itself a regression):\n"
            + "\n".join(f"- {item}" for item in must_preserve)
            + "\n\nMUST FIX (still wrong in the previous draft):\n"
        )
    return (
        "Repair the previous structured engineering plan. This is PLAN_REPAIR, not implementation.\n"
        "Return only one complete JSON object and nothing else. Do not use Markdown or code fences.\n\n"
        f"Original request:\n{goal}\n\n"
        f"Deterministic reason codes: {json.dumps(reason_codes)}\n"
        + must_fix_section
        + "Deterministic validation errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\nCorrection rules:\n"
        "- Return a complete corrected plan, preserving every valid subtask and dependency.\n"
        "- Correct only invalid plan structure; do not broaden scope or invent modules/entrypoints.\n"
        "- Declare depends_on for every stage that consumes files, configuration, contracts, or "
        "build setup produced by another stage.\n"
        "- Preserve or add goal-derived global_invariants (each with a stable id and a statement) "
        "and per-subtask relevant_global_invariant_ids referencing those ids, plus stable "
        "provides/requires metadata; every requires string must exactly equal one provides string "
        "from exactly one declared dependency, and every relevant_global_invariant_ids entry must "
        "exactly equal one global_invariants id - never restate the statement text as the id.\n"
        "- Every verification item must be an object with type, description, verifier_kind, and "
        "requires_runtime_execution; use type=tool/tool_name=compile/verifier_kind=compile for compilation, "
        "type=tool/tool_name=test/verifier_kind=test for tests, and type=judgment without tool_name for "
        "semantic/runtime checks; never emit a verification string.\n"
        "- Map each acceptance criterion only to a stage capable of directly proving it; runtime "
        "output criteria belong on the runnable entrypoint stage.\n"
        "- Every execution_role=implementation subtask MUST declare every file it may modify in "
        "planned_files.\n"
        "- A non-editing build/test/run/output check is its own subtask with "
        "execution_role=verification, planned_files=[], and at least one concrete verification "
        "entry - never a MODEL subtask with no files and no verification entry, and never a fake "
        "planned_files path invented just to pass validation.\n"
        + targeted_correction
        + "- Do not emit TOOL subtasks: authoritative enforce mode has no policy-mediated TOOL router yet.\n"
        "- Output the complete corrected JSON object, not a patch, explanation, or Markdown plan.\n\n"
        f"Previous Planner response (repair attempt {repair_attempt}):\n"
        + previous_plan_text[-20000:]
    )


def build_authoritative_planner_request(
    goal: str,
    *,
    route_kind: Optional[ChangeKind] = None,
    extension_candidates: Optional[List[str]] = None,
    repository_candidates: Optional[List[str]] = None,
) -> str:
    """Add enforce-only protocol constraints without changing the product goal."""
    route_guidance = ""
    if route_kind in {ChangeKind.ENHANCEMENT, ChangeKind.MILESTONE}:
        candidates = extension_candidates or []
        if candidates:
            route_guidance = (
                f"- This request is routed as {route_kind.value}. extension_points must name at "
                "least one real existing path where the capability attaches. Select only from "
                f"these local workspace candidates: {json.dumps(candidates)}.\n"
            )
        else:
            route_guidance = (
                f"- This request is routed as {route_kind.value}, but the workspace has no existing "
                "project files that can be an insertion point; use extension_points=[].\n"
            )
    return (
        "Original product request:\n"
        f"{goal}\n\n"
        "Return only one complete JSON object containing the execution-relevant structured plan. "
        "Emit no prose, Markdown, code fences, rationale, or architecture essay.\n"
        "Authoritative structured-plan protocol (planning metadata, not product requirements):\n"
        "- Do not emit execution_method=tool subtasks; this execution path has no policy-mediated "
        "TOOL router.\n"
        "- Every MODEL subtask sets execution_role: implementation or verification. An "
        "implementation subtask must own at least one exact planned_files path. A verification "
        "subtask (a non-editing build/test/run/output check, e.g. regression confirmation) sets "
        "execution_role=verification, planned_files=[], and at least one concrete verification "
        "entry - never a fake planned_files path.\n"
        "- Each planned_files path must be owned by exactly one implementation MODEL subtask. Do "
        "not create an implementation subtask solely to analyze, inspect, research, or explain "
        "code; fold necessary analysis into the implementation subtask that owns and edits the "
        "file.\n"
        "- Existing local workspace paths available as repository evidence are: "
        f"{json.dumps(repository_candidates or [])}. For modify/delete actions, select the exact "
        "relevant existing path from this evidence. Do not invent a parallel replacement when a "
        "relevant existing owner is present. Use action=create only for a genuinely requested "
        "artifact with no relevant existing path.\n"
        "- Include goal-derived global_invariants, each an object with a short stable id and a "
        "human-readable statement, plus per-subtask relevant_global_invariant_ids referencing "
        "only those ids (never restating or paraphrasing the statement text on the subtask), "
        "provides, requires, and complete depends_on edges.\n"
        "- Each requires string must exactly match one provides string from exactly one upstream "
        "subtask, and that provider must appear in depends_on.\n"
        "- Every verification entry must be an object with type, description, verifier_kind, and "
        "requires_runtime_execution. Use type=tool with tool_name=compile/verifier_kind=compile for compilation and "
        "tool_name=test/verifier_kind=test for tests. Use type=judgment without tool_name for semantic/runtime checks. "
        "Never emit verification strings.\n"
        "- Assign an acceptance_criteria id only to a subtask that can directly demonstrate it. "
        "An application-output or round-trip criterion belongs on the runnable entrypoint stage, "
        "not an upstream library/configuration stage.\n"
        "- Build/config stages may use compile or test verification. Any original-request "
        "requirement for observable application behavior must also be verified by an entrypoint-owning "
        "stage that actually runs the application, observes the required result, and confirms clean exit; "
        "set verifier_kind=application_runtime and requires_runtime_execution=true only on that "
        "explicit application verifier, and false on build-only checks. Successful test execution "
        "satisfies a test verifier and must not synthesize an application-runtime requirement.\n"
        "- Do not copy these protocol rules into global_invariants; derive those only from the "
        "original product request above. Do not invent unspecified implementation choices.\n"
        "- A stage whose tests need build/test tooling (a manifest, a test framework dependency) "
        "owned by another stage's planned_files must declare that stage's provides value in its "
        "own requires and depends_on - do not rely on planned_files or list position to imply "
        "execution order.\n"
        "- If a stage's entrypoint may terminate the process and another stage's tests are "
        "expected to exercise it directly, keep the process-terminating call separate from the "
        "directly-tested logic (a thin wrapper performs termination, tests target the underlying "
        "logic that returns a result instead) - applies to any process-termination mechanism, no "
        "specific method name or file structure required.\n"
        + route_guidance
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
    upstream_ids: set[str] = set()
    frontier = list(failed_subtask.depends_on)
    while frontier:
        dependency_id = frontier.pop()
        if dependency_id in upstream_ids:
            continue
        upstream_ids.add(dependency_id)
        dependency = plan.subtask_by_id(dependency_id)
        if dependency is not None:
            frontier.extend(dependency.depends_on)
    owners: Dict[str, List[str]] = {}
    for path in required_files:
        owner = plan.file_owner(path)
        if owner is None or owner.id == failed_subtask.id or owner.id not in upstream_ids:
            continue
        owners.setdefault(owner.id, []).append(path)
    return owners


class ArtifactOwnerResolutionBasis(str, Enum):
    """MA8.1 completion v3 (2026-08-29, 'Effective Artifact Ownership and
    Recovery Routing'): WHY resolve_effective_artifact_owner picked a given
    subtask as an artifact's recovery owner - pure observability/testing
    metadata, never itself a control-flow input (every caller only ever
    branches on owner_subtask_id being present or absent).

    LATEST_SUCCESSFUL_MODIFIER: the artifact's real, applied execution
    provenance (ControlState.subtask_states/subtask_written_files) names a
    subtask that actually wrote it and completed successfully - the
    primary, authoritative signal (see resolve_effective_artifact_owner).

    DEPENDENCY_ANCESTRY: no execution provenance was available (or usable
    yet), so the plan's own declared file_owner() was used, gated - exactly
    as resolve_scope_conflict_owners always required - on that owner being
    a transitive depends_on ancestor of the failing subtask. This is the
    ORIGINAL (pre-2026-08-29-v3) resolution mechanism, kept as the fallback
    for when provenance genuinely doesn't exist yet.

    UNIQUE_PLAN_OWNER: reserved for a unique plan-declared owner accepted
    WITHOUT dependency-ancestry confirmation. Deliberately never produced by
    resolve_effective_artifact_owner today - test_enforce_surfaces_
    unrelated_owner_scope_conflict_as_plan_revision_required_instead_of_
    merging depends on a same-shaped case (a unique owner, declared but
    neither upstream nor yet-executed) staying UNRESOLVED, not silently
    auto-picked. Kept as a distinct typed value so a future, more permissive
    policy could be represented without another vocabulary change - not a
    live code path.

    UNRESOLVED: no owner could be defensibly selected. Callers must fail
    closed, never guess (PRV-06 §26 invariant 3/8)."""

    LATEST_SUCCESSFUL_MODIFIER = "latest_successful_modifier"
    UNIQUE_PLAN_OWNER = "unique_plan_owner"
    DEPENDENCY_ANCESTRY = "dependency_ancestry"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class OwnerResolution:
    """One artifact's recovery-owner resolution result - small, typed,
    reused as-is for both control flow (owner_subtask_id is None or not)
    and observability (resolution_basis/evidence, logged verbatim by
    resolve_effective_scope_conflict_owners as ARTIFACT_OWNER_RESOLVED/
    ARTIFACT_OWNER_UNRESOLVED)."""

    artifact: str
    owner_subtask_id: Optional[str]
    resolution_basis: ArtifactOwnerResolutionBasis
    evidence: Dict[str, Any] = field(default_factory=dict)


def _successful_modifiers_in_execution_order(
    path: str, order: List[str], control_state: ControlState,
) -> List[str]:
    """Every subtask id (in real execution order) whose OWN ControlState
    record shows it completed successfully AND actually wrote `path` - the
    real, applied file list (subtask_written_files), never the plan's mere
    upfront planned_files declaration. A subtask that hasn't run yet has no
    entry here at all (ControlState is populated incrementally, in
    execution order, as _run_structured_enforce's own per-subtask loop
    proceeds) - so this can never surface a not-yet-executed subtask, no
    special-casing required to keep it safe for that case."""
    return [
        subtask_id for subtask_id in order
        if control_state.subtask_states.get(subtask_id) == SubtaskStatus.COMPLETED.value
        and path in control_state.subtask_written_files.get(subtask_id, [])
    ]


def resolve_effective_artifact_owner(
    plan: EngineeringPlan,
    path: str,
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
) -> OwnerResolution:
    """MA8.1 completion v3 (PRV-06, 2026-08-29): the artifact's EFFECTIVE
    recovery owner - the most recent successfully-completed subtask that
    actually produced/modified the revision currently visible to the
    workflow - not necessarily the planner-assigned owner, the nearest
    depends_on ancestor, or the first subtask that mentioned the file.

    Live PRV-06 evidence this closes: s1 created App.java, s2 later
    successfully MODIFIED it (both declare it in planned_files - a
    legitimate sequential ownership chain, see plan_schema.py's
    classify_file_ownership docstring) - but s3 (needing a further App.java
    fix) only declared depends_on=['s1'], omitting s2. The OLD single
    mechanism (resolve_scope_conflict_owners, walking depends_on only)
    could never find s2: plan.file_owner('App.java') itself already returns
    None for a MULTI-declared path, and even if it didn't, s2 sits outside
    s3's own declared ancestry. Recovery then fell through to
    revise_plan_for_grounded_scope_owner's DAG-mutation path, which hit its
    own real bug (see that function's own docstring) and the run terminated
    unresolved - despite Kriya already knowing, from its own execution
    state, that s2 was the artifact's real current owner.

    Two-step authority order, execution provenance ALWAYS tried first:

    1. Execution provenance (_successful_modifiers_in_execution_order) -
       the most recent completed, real modifier. Requires `order` and
       `control_state`; skipped entirely (falls through to step 2) when
       either is omitted, preserving the exact old behavior for any caller
       that doesn't have them (e.g. direct unit tests).
    2. Dependency-ancestry fallback (resolve_scope_conflict_owners,
       UNCHANGED) - the plan's own declared file_owner(), gated on being a
       transitive depends_on ancestor of the failing subtask. This is
       deliberately the exact ORIGINAL mechanism, not a new one: widening
       it to "any unique plan-declared owner" would resolve
       test_enforce_surfaces_unrelated_owner_scope_conflict_as_plan_
       revision_required_instead_of_merging's own case (a unique but
       genuinely unrelated/not-yet-run owner) into an incorrect silent
       auto-pick - exactly the "arbitrary topological predecessor" failure
       mode this design explicitly forbids (PRV-06 §4/§20 invariant 2).

    Never returns a subtask that hasn't successfully completed (a FAILED
    candidate's own written files are ordinary in-progress residue, not
    ownership - PRV-06 §26 invariant 3) and never returns failed_subtask
    itself."""
    if order is not None and control_state is not None:
        modifiers = [
            subtask_id for subtask_id in _successful_modifiers_in_execution_order(path, order, control_state)
            if subtask_id != failed_subtask.id
        ]
        if modifiers:
            return OwnerResolution(
                artifact=path, owner_subtask_id=modifiers[-1],
                resolution_basis=ArtifactOwnerResolutionBasis.LATEST_SUCCESSFUL_MODIFIER,
                evidence={"prior_modifiers": modifiers},
            )
    ancestry_owners = resolve_scope_conflict_owners(plan, [path], failed_subtask)
    for owner_id, owned_paths in ancestry_owners.items():
        if path in owned_paths:
            return OwnerResolution(
                artifact=path, owner_subtask_id=owner_id,
                resolution_basis=ArtifactOwnerResolutionBasis.DEPENDENCY_ANCESTRY,
                evidence={},
            )
    return OwnerResolution(
        artifact=path, owner_subtask_id=None,
        resolution_basis=ArtifactOwnerResolutionBasis.UNRESOLVED,
        evidence={},
    )


def resolve_effective_scope_conflict_owners(
    plan: EngineeringPlan,
    required_files: List[str],
    failed_subtask: Subtask,
    order: Optional[List[str]] = None,
    control_state: Optional[ControlState] = None,
) -> Dict[str, List[str]]:
    """Multi-artifact wrapper around resolve_effective_artifact_owner,
    returning the exact same Dict[owner_id, [paths]] shape resolve_scope_
    conflict_owners always has - both real call sites in
    _run_structured_enforce (the PLAN_SCOPE_DEFECT merge-skip condition and
    the owner-recovery loop's own owner_map) swap directly to this, no
    other change needed downstream: once an owner is resolved, control
    still hands off entirely to the existing, unmodified MA8.1 requirement-
    scoped recovery mechanism (this function performs no recovery itself).

    Every per-artifact resolution is logged (ARTIFACT_OWNER_RESOLVED /
    ARTIFACT_OWNER_UNRESOLVED) so a later forensic read never has to
    reconstruct "why was X picked as owner of Y" by hand (PRV-06 §22)."""
    owners: Dict[str, List[str]] = {}
    for path in required_files:
        resolution = resolve_effective_artifact_owner(
            plan, path, failed_subtask, order=order, control_state=control_state,
        )
        if resolution.owner_subtask_id is None:
            logger.info(
                "ARTIFACT_OWNER_UNRESOLVED artifact=%s candidate_owners=%s reason=%s",
                path, [], resolution.resolution_basis.value,
            )
            continue
        logger.info(
            "ARTIFACT_OWNER_RESOLVED artifact=%s owner_subtask_id=%s resolution_basis=%s prior_modifiers=%s",
            path, resolution.owner_subtask_id, resolution.resolution_basis.value,
            resolution.evidence.get("prior_modifiers", []),
        )
        owners.setdefault(resolution.owner_subtask_id, []).append(path)
    return owners


def _cross_owner_obligation_id(
    originating_subtask_id: str, owner_subtask_id: str, required_files: List[str],
    failure_family: str, generation: int,
) -> str:
    """MA8.1 completion (2026-08-29 v2 design review): identity is keyed by
    REQUIREMENT, not by owner alone - see this module's own "Requirement-
    scoped repeated owner recovery" note (near `recovery_generation_by_key`
    below) for the live incident this closes. `failure_family` is the
    EXISTING, already-typed `scope_conflict["failure_type"]` (compile,
    diagnosis_mismatch, misdirected_edit, ...) - deliberately NOT a hash of
    free-text requirement prose (the design review's own explicit
    instruction: "identity must be based primarily on deterministic/typed
    information"). `generation` is a monotonic counter, incremented once
    per completed owner-recovery-and-downstream-retry cycle for this exact
    (origin, owner, files) tuple (see `recovery_generation_by_key`) - NOT
    incremented per raw occurrence, so a requirement recurring *within* the
    same still-in-progress cycle keeps the SAME id (sticky - see
    `_get_or_create_cross_owner_obligation`), while a requirement that
    surfaces *after* a full cycle has already completed gets a NEW id even
    when its failure_family happens to match the prior one - exactly the
    live PRV-06 case (both occurrences were "diagnosis_mismatch", but the
    second arose only after the first owner-recovery-plus-retry cycle had
    already finished)."""
    return (
        f"recovery.{originating_subtask_id}.{owner_subtask_id}."
        f"{'.'.join(sorted(required_files))}.{failure_family}.{generation}"
    )


def _prior_cross_owner_musts(
    ledger: Optional[ObligationLedger], *, owner_subtask_id: str, required_files: List[str],
) -> List[str]:
    """Every earlier-generation CROSS_OWNER_ARTIFACT_REQUIREMENT's own
    MUST_FIX text for this exact owner+artifact combination, regardless of
    which originating subtask or failure_family raised it - folded into a
    LATER generation's own MUST_PRESERVE (see _get_or_create_cross_owner_
    obligation) so a second recovery of the same owner cannot silently
    regress a correction an earlier, still-relevant requirement already
    established (2026-08-29 v2 design review, invariant 6: "a later
    recovery of an owner must preserve corrections established by earlier
    active recovery requirements unless authoritative evidence explicitly
    invalidates them"). Uses the ledger's own current_by_kind() - no new
    query surface - and filters by the same required_files tuple already
    stored in repair_scope."""
    if ledger is None:
        return []
    target_scope = tuple(sorted(required_files))
    musts: List[str] = []
    for record in ledger.current_by_kind(ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT):
        if record.owner_subtask_id != owner_subtask_id or record.repair_scope != target_scope:
            continue
        musts.extend(record.evidence.get("must_fix", []))
    return musts


def _get_or_create_cross_owner_obligation(
    ledger: Optional[ObligationLedger],
    *,
    originating_subtask_id: str,
    owner_subtask_id: str,
    required_files: List[str],
    scope_conflict: Dict[str, Any],
    generation: int,
    revision: Any,
) -> Optional[ObligationRecord]:
    """MA8.1 (PRV-06, 2026-08-29; completed 2026-08-29 v2): the single place
    a downstream failure's grounded requirement for an upstream owner
    becomes a durable, ledger-tracked fact instead of a one-shot prompt
    string that can be lost or overwritten - see ObligationKind.
    CROSS_OWNER_ARTIFACT_REQUIREMENT's own docstring for the original live
    incident, and this module's "Requirement-scoped repeated owner
    recovery" note for the v2 completion (a SECOND, genuinely different
    requirement on the same owner was being silently blocked by an owner-
    level one-shot guard).

    Sticky WITHIN one generation: if this EXACT requirement (same
    originating subtask, owner, required files, failure_family, AND
    generation - see _cross_owner_obligation_id) is already on record as
    VIOLATED, returns it UNCHANGED rather than re-deriving from the
    CURRENT scope_conflict - a later attempt's own grounded evidence can
    be weaker or differently shaped than the first, strongest occurrence
    within the same cycle - the obligation's own MUST_FIX/evidence must
    not silently regress just because a later symptom looks different. A
    NEW generation (see the caller's own `recovery_generation_by_key`)
    always gets a fresh obligation, even when failure_family repeats -
    genuinely different requirements are allowed to coexist rather than
    collapsing into one.

    A new obligation's own MUST_PRESERVE always folds in every prior
    generation's MUST_FIX for the same owner+artifact (_prior_cross_owner_
    musts) - the mechanism behind invariant 6 (later recovery must
    preserve earlier corrections).

    Reuses ObligationRecord as-is (no new dataclass) - owner_subtask_id and
    repair_scope are already exactly the fields this needs; must_fix/
    must_preserve/acceptance_conditions/raw_evidence live in the free-form
    `evidence` dict.

    Returns None only when there is no ledger to record into (a caller
    outside structured/enforce execution, which never reaches this
    recovery path today anyway - matches every other MA8/MA9 obligation
    hook's own "no ledger -> no-op" convention)."""
    if ledger is None:
        return None
    failure_family = scope_conflict.get("failure_type") or "unknown"
    obligation_id = _cross_owner_obligation_id(
        originating_subtask_id, owner_subtask_id, required_files, failure_family, generation,
    )
    existing = ledger.current(obligation_id)
    if existing is not None and existing.status == ObligationStatus.VIOLATED:
        # Sticky CONTENT (must_fix/must_preserve/evidence unchanged - never
        # regresses to a weaker later symptom), but a NEW history entry is
        # still recorded each time - recurrence must remain a durable,
        # countable ledger fact (matching PROCESS_BOUNDARY_COMPATIBILITY's
        # own precedent), not silently invisible. This is what gives the
        # caller's own bound check (ledger.history(id) vs
        # _MAX_PLAN_SCOPE_REVISION_ATTEMPTS) real teeth - a requirement
        # that keeps recurring within the same generation actually
        # exhausts its own budget instead of looking, forever, like a
        # single untouched record.
        recurring = ObligationRecord(
            id=obligation_id, kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
            status=ObligationStatus.VIOLATED, authority=ObligationAuthority.DETERMINISTIC,
            description=existing.description, source="workflow_controller.owner_recovery",
            revision=revision, evidence=existing.evidence,
            owner_subtask_id=existing.owner_subtask_id, terminal_required=False,
            repair_scope=existing.repair_scope,
        )
        ledger.record(recurring)
        return recurring
    grounded_reason = scope_conflict.get("reason") or "a grounded requirement discovered by the reopened downstream subtask"
    must_preserve = [
        "existing project/module identity",
        "existing configuration and declarations unrelated to this requirement",
    ] + _prior_cross_owner_musts(ledger, owner_subtask_id=owner_subtask_id, required_files=required_files)
    evidence = {
        "must_fix": [grounded_reason],
        "must_preserve": must_preserve,
        "raw_evidence": scope_conflict.get("raw_evidence") or "",
        "required_artifacts": list(required_files),
        "failure_family": failure_family,
        "generation": generation,
        "acceptance_conditions": [
            f"subtask {originating_subtask_id} passes its own Quality Gates without the same "
            "grounded requirement recurring",
        ],
        "originating_subtask_id": originating_subtask_id,
    }
    record = ObligationRecord(
        id=obligation_id,
        kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
        status=ObligationStatus.VIOLATED,
        authority=ObligationAuthority.DETERMINISTIC,
        description=f"{owner_subtask_id} must satisfy a requirement grounded by {originating_subtask_id}'s own failure: {grounded_reason}",
        source="workflow_controller.owner_recovery",
        revision=revision,
        evidence=evidence,
        owner_subtask_id=owner_subtask_id,
        terminal_required=False,
        repair_scope=tuple(sorted(required_files)),
    )
    ledger.record(record)
    logger.info(
        "CROSS_OWNER_REQUIREMENT_CREATED requirement_id=%s origin=%s owner=%s "
        "failure_family=%s generation=%d",
        obligation_id, originating_subtask_id, owner_subtask_id, failure_family, generation,
    )
    return record


def _build_owner_recovery_context(
    *,
    owner_id: str,
    failed_subtask_id: str,
    required_owner_files: List[str],
    cross_owner_obligation: Optional[ObligationRecord],
    scope_conflict: Dict[str, Any],
) -> str:
    """The Developer-facing recovery_context text for a reopened owner
    subtask - extracted to its own function (2026-08-29, MA8.1) so the
    exact wording/fields it produces are directly unit-testable, matching
    this codebase's own precedent for every other retry-prompt builder
    (e.g. kriya/workflow/retry_prompts.py). When cross_owner_obligation is
    available, surfaces its MUST FIX/MUST PRESERVE/EVIDENCE/ACCEPTANCE
    explicitly - the exact grounded reason this owner is being reopened,
    not a generic "preserve brownfield identity" framing (see
    ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT's own docstring for the
    live incident this closes). Falls back to the original, plainer
    framing only when no ledger was available to record an obligation into
    (a caller outside structured/enforce execution)."""
    if cross_owner_obligation is not None:
        ev = cross_owner_obligation.evidence
        return (
            "--- ACTIVE CROSS-OWNER RECOVERY REQUIREMENT ---\n"
            f"Reopened owner: {owner_id}\n"
            f"Originating failure: subtask {failed_subtask_id}\n"
            f"Required repair files: {json.dumps(required_owner_files)}\n\n"
            f"MUST FIX: {'; '.join(ev.get('must_fix', [])) or 'unavailable'}\n"
            f"MUST PRESERVE: {'; '.join(ev.get('must_preserve', [])) or 'unavailable'}\n"
            f"EVIDENCE: {ev.get('raw_evidence') or 'unavailable'}\n"
            f"ACCEPTANCE: {'; '.join(ev.get('acceptance_conditions', [])) or 'unavailable'}\n\n"
            "This is the exact grounded reason this owner was reopened - address it "
            "specifically, not a generic regeneration. Preserve every other existing "
            "declaration this file already has."
        )
    return (
        "--- authoritative plan recovery ---\n"
        f"Failed consumer: {failed_subtask_id}\n"
        f"Reopened owner: {owner_id}\n"
        f"Required repair files: {json.dumps(required_owner_files)}\n"
        f"Failure type: {scope_conflict.get('failure_type')}\n"
        f"Grounded failure diagnosis: {scope_conflict.get('reason') or 'unavailable'}\n"
        "Repair only the reopened owner's approved files. Preserve its provided "
        "contracts and all relevant global invariants."
    )


def _integration_reference_token(path: str) -> str:
    """The stem of a file's basename (no directory, no extension) - e.g.
    "InMemoryService" from "src/main/java/com/example/InMemoryService.java",
    "service" from "app/repository.py". A deliberately crude, generic,
    language-agnostic proxy for "the identifier another file would use to
    reference this one" - no AST, no import-graph, no per-language parsing
    (Part C2's own explicit boundary: use existing plan/obligation
    machinery, never build a semantic dependency graph)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem


def _evaluate_integration_obligations(
    plan: EngineeringPlan,
    obligation_ledger: Optional[ObligationLedger],
    completed_subtask_id: str,
    established_file_context: Dict[str, str],
    revision: Any,
) -> None:
    """Correctness Continuity Part C (PRV-06, 2026-08-29) - the deterministic
    evidence source that can transition a plan.integration.* obligation
    (seeded PENDING by plan_validation.validate_plan(), see its own
    docstring there) to SATISFIED or VIOLATED. Called once, right after a
    subtask finishes and its files land in established_file_context - see
    this module's own per-subtask loop for the call site.

    Deliberately narrow evidence: a whole-word textual reference from the
    token(s) of every relevant PRODUCER artifact's basename appearing
    somewhere in the CONSUMER subtask's own just-established file content -
    generic, cheap, and language-agnostic (Part C8: deterministic reference
    evidence preferred over any new LLM judge; Part C2: no semantic graph,
    no AST, no per-ecosystem adapter). This is intentionally a coarse
    proxy for "the consumer actually uses the producer," not a compiler -
    it can't confirm the reference is semantically correct, only that it
    exists at all. That is exactly the class of evidence PRV-06 showed
    Kriya had NONE of before this: App.java never once mentioned
    "InMemoryService" anywhere in its own content, which this check would
    have caught with zero false negatives on the real live incident.

    Only evaluates a relationship once it is still PENDING (never re-
    evaluates an already-SATISFIED/VIOLATED one - Part A's own evidence-
    monotonicity spirit applied here too: this module deliberately does not
    build upstream-change invalidation/stale-propagation, per Part C's own
    explicit "not built until MA9 ever does partial regeneration" boundary
    - see repair_contract.py's own three-level dependency model note)."""
    if obligation_ledger is None or not plan.integration_relationships:
        return
    for rel in plan.integration_relationships:
        if completed_subtask_id not in rel.consumer_subtask_ids:
            continue
        obligation_id = f"plan.integration.{rel.id}"
        current = obligation_ledger.current(obligation_id)
        if current is None or current.status != ObligationStatus.PENDING:
            continue
        producer_paths = [
            pf.path
            for pid in rel.producer_subtask_ids
            for pf in (plan.subtask_by_id(pid).planned_files if plan.subtask_by_id(pid) else [])
            if not rel.participating_artifacts or pf.path in rel.participating_artifacts
        ]
        consumer_paths = [
            pf.path
            for cid in rel.consumer_subtask_ids
            for pf in (plan.subtask_by_id(cid).planned_files if plan.subtask_by_id(cid) else [])
            if not rel.participating_artifacts or pf.path in rel.participating_artifacts
        ]
        consumer_content = "\n".join(
            established_file_context[p] for p in consumer_paths if p in established_file_context
        )
        missing_producers = []
        for producer_path in producer_paths:
            if producer_path not in established_file_context:
                # The producer hasn't been established yet at all - cannot
                # possibly have been integrated (Part C6's scheduling
                # invariant: a consumer finalizing before its producer's
                # contract exists is itself the defect this records).
                missing_producers.append(producer_path)
                continue
            token = _integration_reference_token(producer_path)
            if not re.search(rf"\b{re.escape(token)}\b", consumer_content):
                missing_producers.append(producer_path)
        satisfied = not missing_producers
        record = ObligationRecord(
            id=obligation_id,
            kind=ObligationKind.CROSS_SUBTASK_INTEGRATION,
            status=ObligationStatus.SATISFIED if satisfied else ObligationStatus.VIOLATED,
            authority=ObligationAuthority.DETERMINISTIC,
            description=current.description,
            source="workflow_controller.integration_check",
            revision=revision,
            evidence={
                **current.evidence,
                "missing_producer_references": missing_producers,
            },
            owner_subtask_id=current.owner_subtask_id,
            terminal_required=True,
            repair_scope=current.repair_scope,
        )
        obligation_ledger.record(record)
        logger.info(
            "INTEGRATION_OBLIGATION_%s id=%s consumer=%s missing=%s",
            "SATISFIED" if satisfied else "VIOLATED",
            obligation_id, completed_subtask_id, missing_producers,
        )


def _transitive_dependents(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """Every subtask that depends on `subtask_id`, directly or through a
    chain of other subtasks - i.e. everything that is DOWNSTREAM of it in
    the DAG. Used to keep a plan-surgery merge from introducing a cycle."""
    dependents: set[str] = set()
    frontier = [subtask_id]
    while frontier:
        current = frontier.pop()
        for st in plan.subtasks:
            if current in st.depends_on and st.id not in dependents:
                dependents.add(st.id)
                frontier.append(st.id)
    return dependents


def _transitive_upstream_ids(plan: EngineeringPlan, subtask_id: str) -> set[str]:
    """Every subtask `subtask_id` depends on, directly or through a chain -
    i.e. everything UPSTREAM of it in the DAG (the mirror of
    _transitive_dependents above, walking depends_on forward instead of
    finding reverse-dependents).

    2026-08-29 (PRV-06, MA8.1) - added after live evidence: the merge loop
    below already protects one direction (an owner's OWN depends_on
    pointing at something downstream of the failed stage - see
    dropped_owner_deps) but not the other. When a THIRD subtask that
    already depends on the deleted owner gets redirected to depend on
    `failed` instead, that redirect is only safe if the third subtask is
    NOT itself upstream of `failed` - otherwise `failed` already
    (transitively) depends on it, and pointing it at `failed` too closes a
    cycle: consumer -> failed -> ... -> consumer. Confirmed live: a
    grounded-owner plan revision reopening a build-manifest owner (pom.xml)
    for a downstream test subtask redirected the manifest's OTHER, earlier
    consumers (production-code subtasks upstream of the failing test stage)
    to depend on the failing test stage - inverting the original
    dependency direction and producing exactly this cycle, caught only by
    validate_plan()'s own after-the-fact structural check. See the merge
    loop's own use of this function for the fix - skip (drop, don't
    redirect) any such edge instead of constructing it."""
    upstream: set[str] = set()
    frontier = list(plan.subtask_by_id(subtask_id).depends_on) if plan.subtask_by_id(subtask_id) else []
    while frontier:
        current = frontier.pop()
        if current in upstream:
            continue
        upstream.add(current)
        dependency = plan.subtask_by_id(current)
        if dependency is not None:
            frontier.extend(dependency.depends_on)
    return upstream


def _plan_scope_conflict_files(scope_conflict: Optional[Dict[str, object]]) -> List[str]:
    """The file(s) a PLAN_SCOPE_DEFECT scope_conflict names as needing
    authoritative plan revision - grounded_owner_files if present, else
    required_files.

    PRV-03 hardened (2026-08-27): a chain's SECOND hop can be attributed
    through a different path than its first. retry_strategy.py only
    populates grounded_owner_files when the offending attribution reached
    "architectural_owner" tier (its own strongest, most direct signal - a
    deterministic AuthorizedFileWriter write-scope denial always lands
    there). A later hop in the SAME chain can instead surface through a
    misdirected_edit-triggered lookup or another attribution path that
    never reaches that tier, leaving grounded_owner_files empty even though
    required_files is populated and classification is still
    PLAN_SCOPE_DEFECT - retry_strategy.py already required
    scope_conflict_is_grounded (high confidence, or fail_type ==
    "misdirected_edit") before setting plan_scope_conflict at all, so
    required_files is not a guess. Confirmed live: s2's second hop (needing
    Customer.java, attributed via a misdirected_edit lookup after the
    goal-spec-compliance failure) had grounded_owner_files == [] and
    required_files == ["...Customer.java"], which silently ended the
    revise-and-revalidate loop one hop early and fell to the weaker,
    upstream-only resolve_scope_conflict_owners() fallback instead.

    Safe to widen this way: revise_plan_for_grounded_scope_owner() itself
    re-derives ownership from the PLAN's own declared file_owner() (or
    treats a genuinely unowned path as newly owned by the failed subtask)
    rather than trusting either list blindly, and every revision this
    produces is revalidated by validate_plan() before being accepted - a
    spurious entry here fails that revalidation closed instead of silently
    corrupting the plan."""
    scope_conflict = scope_conflict or {}
    return list(
        scope_conflict.get("grounded_owner_files")
        or scope_conflict.get("required_files")
        or []
    )


def revise_plan_for_grounded_scope_owner(
    plan: EngineeringPlan,
    failed_subtask_id: str,
    grounded_files: List[str],
    workspace_path: str,
) -> EngineeringPlan:
    """Move deterministically grounded existing owners into the failed stage.

    This is plan surgery, not a retry hint: ownership remains unique, an
    emptied owner stage is merged into the failed stage, dependency edges are
    rewired, and the caller must validate the returned plan before execution.

    The grounded owner is not always upstream of the failed stage - a
    compliance gate can just as easily discover that a LATER stage (e.g. a
    controller three subtasks downstream) is the real owner of behavior the
    failed stage's goal requires. When that happens, naively unioning the
    owner's OWN depends_on into the failed stage's depends_on can point the
    failed stage at something that (transitively) already depends on it -
    an immediate cycle, silently rejected by validate_plan()'s acyclic
    check with no actionable signal for the caller. See
    _transitive_dependents below: any owner dependency already downstream
    of the failed stage is dropped rather than inherited - the failed stage
    keeps its own original ordering constraints (it already runs at its
    original position; a dependency that only existed to sequence the
    NOW-REMOVED owner stage relative to ITS neighbors is meaningless once
    that stage's scope has been absorbed here).
    """
    revised = plan.model_copy(deep=True)
    failed = revised.subtask_by_id(failed_subtask_id)
    if failed is None:
        raise ValueError(f"unknown failed subtask {failed_subtask_id!r}")

    grounded = list(dict.fromkeys(grounded_files))
    for path in grounded:
        if not os.path.isfile(os.path.join(workspace_path, path)):
            raise ValueError(f"grounded scope owner does not exist: {path}")
        # MA8.1 completion v3 (PRV-06, 2026-08-29): a path may legitimately
        # be declared by MULTIPLE subtasks at once (a validated sequential
        # ownership chain - e.g. one subtask creates, a later one modifies;
        # plan_schema.py's classify_file_ownership own docstring). The
        # single-owner-only file_owner() lookup this used to call returns
        # None for exactly that shape, which fell through to the
        # "genuinely new file" branch below WITHOUT stripping the path
        # from ANY of its real declared owners - producing an invalid plan
        # where the same path ended up declared on every original owner
        # AND the failed stage simultaneously (live PRV-06 evidence:
        # App.java on s1, s2, AND s3 at once - "planned file ownership
        # must be unique", the exact validation failure that then forced
        # a fallback to owner-recovery with no valid path forward either).
        # Re-derived directly here instead, over every declaring subtask.
        owners = [
            st for st in revised.subtasks
            if st.id != failed.id and any(pf.path == path for pf in st.planned_files)
        ]
        moved: Optional[PlannedFile] = None
        if owners:
            # The LAST declared owner's own PlannedFile metadata (action/
            # reason) seeds `moved` - a simple, sufficient heuristic since
            # actual content correctness is enforced by the merged
            # subtask's own compile/test Quality Gates afterward, not by
            # this planning-time metadata (the same acceptance already
            # applies to owner_requires below).
            source_owner = owners[-1]
            moved = next(pf for pf in source_owner.planned_files if pf.path == path)
            moved = moved.model_copy(update={
                "reason": "deterministically grounded architectural owner",
            })
            for owner in owners:
                owner.planned_files = [pf for pf in owner.planned_files if pf.path != path]
        else:
            moved = PlannedFile(
                path=path, action=FileAction.MODIFY,
                reason="deterministically grounded architectural owner",
            )
        if moved is not None and not any(pf.path == path for pf in failed.planned_files):
            failed.planned_files.append(moved)

        for owner in owners:
            if owner.planned_files:
                continue

            # The old stage has no independently executable scope after
            # moving its sole remaining declaration. Merge its contract and
            # remove it, preserving the DAG. owner.depends_on is only safe
            # to inherit when owner was UPSTREAM of failed; when it was
            # downstream (this is a forward-grounded discovery, not a
            # backward one), those dependencies are already reachable FROM
            # failed and inheriting them would create a cycle - drop those
            # instead of raising, since failed's own original position
            # already sequences it correctly relative to them.
            failed_downstream = _transitive_dependents(revised, failed.id)
            dropped_owner_deps = {
                dep for dep in owner.depends_on if dep in failed_downstream
            }
            failed.depends_on = list(dict.fromkeys(
                dep for dep in failed.depends_on + owner.depends_on
                if dep not in (failed.id, owner.id) and dep not in failed_downstream
            ))
            failed.acceptance_criteria_ids = list(dict.fromkeys(
                failed.acceptance_criteria_ids + owner.acceptance_criteria_ids
            ))
            existing_verification = {
                json.dumps(vm.model_dump(mode="json"), sort_keys=True)
                for vm in failed.verification
            }
            failed.verification.extend(
                vm for vm in owner.verification
                if json.dumps(vm.model_dump(mode="json"), sort_keys=True)
                not in existing_verification
            )
            failed.provides = list(dict.fromkeys(failed.provides + owner.provides))
            # A requires whose sole provider was one of dropped_owner_deps can no
            # longer be validly declared - keeping it would fail validate_plan's
            # SEMANTIC_DEPENDENCY_EDGE_MISSING check (requires X but doesn't
            # depend on X's provider) for exactly the edge we just had to drop
            # above to avoid a cycle. The capability itself may genuinely not be
            # ready yet; that risk is accepted the same way any other grounded-
            # owner recovery accepts it - actual correctness is still enforced
            # by the merged subtask's own compile/test Quality Gates, not by
            # this planning-time metadata alone.
            capability_providers: Dict[str, List[str]] = {}
            for st in revised.subtasks:
                for capability in st.provides:
                    capability_providers.setdefault(capability, []).append(st.id)
            owner_requires = [
                item for item in owner.requires
                if not (
                    len(capability_providers.get(item, [])) == 1
                    and capability_providers[item][0] in dropped_owner_deps
                )
            ]
            failed.requires = [
                item for item in dict.fromkeys(failed.requires + owner_requires)
                if item not in failed.provides
            ]
            failed.relevant_global_invariant_ids = list(dict.fromkeys(
                failed.relevant_global_invariant_ids + owner.relevant_global_invariant_ids
            ))
            revised.subtasks = [st for st in revised.subtasks if st.id != owner.id]
            # 2026-08-29 (PRV-06, MA8.1): computed BEFORE the redirect loop,
            # against the plan as it stood before this merge (owner already
            # removed above, but no depends_on rewritten yet) - see
            # _transitive_upstream_ids' own docstring for the live cyclic-DAG
            # incident this guards against.
            failed_upstream = _transitive_upstream_ids(revised, failed.id)
            for consumer in revised.subtasks:
                if owner.id not in consumer.depends_on:
                    continue
                if consumer.id in failed_upstream or consumer.id == failed.id:
                    # consumer already runs before (or is) failed - the merge
                    # doesn't need it to depend on anything for ordering, and
                    # redirecting it to depend on failed would invert direction
                    # and close a cycle (failed already transitively depends on
                    # it). Drop the now-dangling reference to the deleted owner
                    # instead of redirecting it.
                    consumer.depends_on = [dep for dep in consumer.depends_on if dep != owner.id]
                    continue
                consumer.depends_on = list(dict.fromkeys(
                    failed.id if dep == owner.id else dep for dep in consumer.depends_on
                    if (failed.id if dep == owner.id else dep) != consumer.id
                ))

    failed.description = (
        f"{failed.description} Authoritative scope revision: modify grounded owner(s) "
        f"{', '.join(grounded)}."
    )
    return EngineeringPlan.model_validate(revised.model_dump(mode="json"))


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
        plan, _ = canonicalize_planned_file_actions(plan, workspace_path)

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
        planning_repository_candidates = _authoritative_planner_extension_candidates(
            workspace_path, goal=goal,
        )
        authoritative_planner_request = build_authoritative_planner_request(
            goal,
            route_kind=route.kind,
            extension_candidates=planning_repository_candidates,
            repository_candidates=planning_repository_candidates,
        )
        planning_repository_evidence = bounded_repository_evidence(
            workspace_path, planning_repository_candidates,
        )
        plan_text = await self.workflow_engine.planner.run(
            authoritative_planner_request,
            max_tokens_override=planner_token_cap,
            system_prompt_override=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
            json_mode=True,
        )
        _log_phase_banner("PLAN VALIDATION")
        repair_attempts = 0
        # MA8 (PRV-05 run #8, 2026-08-28) - kriya/workflow/obligations.py.
        # One ledger for the whole run, created here (before
        # PLAN_STRUCTURAL_VALIDITY obligations start being produced) and
        # threaded unchanged into every subtask's run_generation_workflow()
        # call below (MIGRATION_COMPLETION/GOAL_SPEC_REQUIREMENT obligations)
        # and the terminal migration gate - "satisfied" means the same thing
        # everywhere in one run. See this module's own docstring for the two
        # concrete run #8 defects this closes.
        obligation_ledger = ObligationLedger()
        # raw_plan is the Planner's own asserted output, before
        # canonicalize_planned_file_actions() derives create/modify from
        # real repository state - kept only so the resume-hash comparison
        # below (current_plan_hash = raw_plan.content_hash()) reflects what
        # the Planner actually said, not a Kriya-derived detail. A file a
        # completed subtask already wrote legitimately flips create->modify
        # between the original planning call and a later resume replan of
        # the SAME goal/intent; hashing the canonicalized plan would make
        # that legitimate, expected drift look like "a different plan,"
        # wrongly refusing an otherwise-valid resume (found via this
        # session's own regression sweep, not a live incident).
        raw_plan: Optional[EngineeringPlan] = None
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
                elif parse_issue and "failed schema validation" in parse_issue:
                    reason_codes.append("STRUCTURED_PLAN_SCHEMA_INVALID")
                else:
                    reason_codes.append("STRUCTURED_PLAN_PARSE_FAILED")
            else:
                raw_plan = build_engineering_plan_from_planner_output(
                    structured_output, plan_id=run_id, kind=route.kind,
                )
                if raw_plan is None:
                    errors.append("structured output parsed but produced zero subtasks")
                    reason_codes.append("STRUCTURED_PLAN_EMPTY")
                else:
                    plan, _ = canonicalize_planned_file_actions(raw_plan, workspace_path)
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
                        obligation_ledger=obligation_ledger, revision=repair_attempts,
                    )
                    errors.extend(validation.errors)
                    reason_codes.extend(validation.reason_codes)
                    unbounded_model_subtasks = [
                        st.id for st in plan.subtasks
                        if st.execution_method == ExecutionMethod.MODEL
                        and st.execution_role != ExecutionRole.VERIFICATION
                        and not st.planned_files
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
                try:
                    persist_planning_attempt_diagnostic(
                        workspace_path, run_id,
                        attempt=repair_attempts,
                        planner_request=authoritative_planner_request,
                        planner_system_prompt=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                        raw_plan_response=plan_text,
                        plan=plan,
                        validation_errors=errors,
                        reason_codes=reason_codes,
                        repository_evidence=planning_repository_evidence,
                        repair_prompt=None,
                    )
                except Exception as diagnostic_error:
                    logger.warning(
                        "Failed to persist local structured-planning diagnostics for run %r "
                        "attempt %d: %s", run_id, repair_attempts, diagnostic_error,
                    )
                ledger.record_and_persist(
                    workspace_path, "structured_plan_validation", run_id=run_id,
                    valid=True, repair_attempts=repair_attempts,
                )
                break

            repair_prompt = None
            if repair_attempts < 2:
                # MA8: everything PLAN_STRUCTURAL_VALIDITY currently reports
                # SATISFIED (as of the validate_plan() call just above) must
                # survive the next draft - see build_structured_plan_repair_
                # prompt's own docstring for the run #8 incident this closes.
                currently_violated_ids = [
                    rec.id for rec in obligation_ledger.current_by_kind(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
                    if rec.status == ObligationStatus.VIOLATED
                ]
                must_preserve = [
                    f"{rec.description} (evidence: {json.dumps(rec.evidence, default=str)})"
                    for rec in obligation_ledger.relevant_for_preservation(
                        ObligationKind.PLAN_STRUCTURAL_VALIDITY, currently_violated_ids,
                    )
                ]
                repair_prompt = build_structured_plan_repair_prompt(
                    goal, plan_text, errors, reason_codes, repair_attempts + 1,
                    route_kind=route.kind,
                    extension_candidates=planning_repository_candidates,
                    repository_candidates=planning_repository_candidates,
                    must_preserve=must_preserve,
                )
            try:
                persist_planning_attempt_diagnostic(
                    workspace_path, run_id,
                    attempt=repair_attempts,
                    planner_request=authoritative_planner_request,
                    planner_system_prompt=AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
                    raw_plan_response=plan_text,
                    plan=plan,
                    validation_errors=errors,
                    reason_codes=reason_codes,
                    repository_evidence=planning_repository_evidence,
                    repair_prompt=repair_prompt,
                )
            except Exception as diagnostic_error:
                logger.warning(
                    "Failed to persist local structured-planning diagnostics for run %r "
                    "attempt %d: %s", run_id, repair_attempts, diagnostic_error,
                )

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
                # MA8 (PRV-05 run #8): distinguish "kept oscillating between
                # constraints" from "just never converged" - both are
                # reported, never used to raise the repair-attempt bound
                # itself (that stays fixed at 2, per this fix's own scope).
                oscillating = obligation_ledger.oscillating_ids(ObligationKind.PLAN_STRUCTURAL_VALIDITY)
                if oscillating:
                    reason_codes.append("PLAN_REPAIR_OSCILLATION")
                    logger.error(
                        "WorkflowController enforce run %r: plan repair OSCILLATED on "
                        "obligation(s) %s - full revision history: %s",
                        run_id, oscillating,
                        {oid: [(r.revision, r.status.value) for r in obligation_ledger.history(oid)]
                         for oid in oscillating},
                    )
                else:
                    reason_codes.append("PLAN_REPAIR_NON_CONVERGENCE")
                raise _UnsafeStructuredPlan(
                    "structured plan remained unsafe after two bounded repair attempts",
                    reason_codes=list(dict.fromkeys(reason_codes)),
                    invalid_subtask_ids=invalid_subtask_ids,
                    repair_attempts=repair_attempts,
                )

            repair_attempts += 1
            assert repair_prompt is not None
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
        assert raw_plan is not None
        current_plan_hash = raw_plan.content_hash()

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
        # The authoritative plan is one transaction. Individual subtask
        # workflows may apply only into this plan-level sandbox; the user
        # workspace remains unchanged until every subtask and any bounded
        # owner-recovery pass succeeds.
        planned_paths = sorted({
            planned_file.path
            for planned_subtask in plan.subtasks
            for planned_file in planned_subtask.planned_files
        })
        original_plan_revisions = {
            path: read_file_revision(os.path.join(workspace_path, path))
            for path in planned_paths
        }
        plan_workspace_path = create_git_worktree(workspace_path)
        # Resolved ONCE, here, against workspace_path - the real, immutable
        # PRE-mutation baseline, before plan_workspace_path accumulates any
        # subtask's committed writes - and reused unchanged by every bounded
        # subtask's own attempt AND the terminal global gate below. Found
        # live, PRV-05 run 6 (2026-08-28): re-resolving source/target
        # identity from CURRENT (progressively-mutated) workspace state at
        # each call site is itself the defect (see kriya/workflow/
        # migration.py's own docstring) - a migration obligation is a
        # goal-level invariant, not something to rediscover at every point
        # in time. Wrapped in its own try/except (not just the terminal
        # gate's) - an internal bug HERE must also fail closed, at the
        # terminal gate below, rather than propagate as an unhandled
        # exception that crashes the whole enforce run.
        try:
            migration_resolution = resolve_migration_resolution(goal, workspace_path)
        except Exception as e:
            migration_resolution = MigrationResolution(
                MigrationResolutionStatus.INDETERMINATE,
                reason=f"migration resolution itself raised ({type(e).__name__}: {e})",
            )
        established_file_context: Dict[str, str] = {}
        subtask_results: List[SubtaskResult] = []
        subtask_call_results: List[Dict[str, Any]] = []
        knowledge_gap_break: Optional[Dict[str, Any]] = None
        # MA8.1 completion (2026-08-29 v2 design review): "Requirement-
        # scoped repeated owner recovery." A live PRV-06 run proved a bare
        # owner-id set (reopen each owner at most once per run) is the
        # wrong abstraction - it blocked a SECOND, genuinely different
        # grounded requirement on the same owner (s3/MainApplication.java)
        # purely because s3 had already been reopened once for a DIFFERENT
        # requirement earlier in the same run. Replaced with a per-
        # (originating subtask, owner, required files) generation counter -
        # incremented once a full owner-recovery-and-downstream-retry cycle
        # completes for that exact tuple (see the increment site near
        # execution_role="consumer_retry" below), never per raw occurrence.
        # A requirement recurring WITHIN the current generation is sticky
        # (same obligation id, same MUST_FIX - see _get_or_create_cross_
        # owner_obligation); a requirement surfacing AFTER a generation has
        # completed gets a fresh id and is eligible for a new reopening.
        # Boundedness is preserved WITHOUT a new retry engine or budget: a
        # given requirement id's own recovery attempts are capped by
        # reusing _MAX_PLAN_SCOPE_REVISION_ATTEMPTS (the same constant the
        # sibling DAG-mutation revision loop already uses), checked via the
        # ledger's own history() - no new counter needed for that part.
        recovery_generation_by_key: Dict[Tuple[str, str, Tuple[str, ...]], int] = {}
        plan_recovery_events: List[Dict[str, Any]] = []

        async def _invoke_bounded_subtask(
            target: Subtask,
            target_position: int,
            *,
            recovery_context: str = "",
            execution_role: str = "planned",
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
                workspace_path=plan_workspace_path,
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
                # PRV-05 run 7 (2026-08-28): the validated EngineeringPlan and
                # this subtask's own id, so migration validation and retry
                # attribution can both answer "who owns this file, and are we
                # there yet" via EngineeringPlan.classify_file_ownership() -
                # see AttemptContext.structured_plan's own docstring.
                structured_plan=plan,
                current_subtask_id=target.id,
                # MA8 (PRV-05 run #8): same ledger the plan-repair loop
                # above used, so MIGRATION_COMPLETION obligations recorded
                # during this subtask's own attempt accumulate into the
                # SAME per-run ledger, not a fresh one per subtask.
                obligation_ledger=obligation_ledger,
                # DENY_ALL for a verification-role subtask - enforced at the
                # real write gate (AuthorizedFileWriter), not merely implied
                # by target_files being empty. Every other subtask keeps
                # today's inferred ALLOWLIST/UNRESTRICTED behavior (None ->
                # run_generation_workflow's own backward-compatible
                # inference).
                write_scope_mode=(
                    WriteScopeMode.DENY_ALL if target.execution_role == ExecutionRole.VERIFICATION
                    else None
                ),
                required_verification=[vm.model_dump(mode="json") for vm in target.verification],
                runtime_verification_required=any(
                    vm.requires_application_runtime for vm in target.verification
                ),
                strict_spec_compliance=True,
                execution_scope=f"subtask={target.id} role={execution_role}",
                grounding_goal=goal,
                migration_resolution=migration_resolution,
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
                            os.path.join(plan_workspace_path, planned_file.path), "r", encoding="utf-8", errors="replace",
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
                "Current subtask: %s/%s id=%s files=%s depends_on=%s relevant_invariant_ids=%s",
                position, total, subtask.id, [pf.path for pf in subtask.planned_files],
                subtask.depends_on, subtask.relevant_global_invariant_ids,
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
            grounded_scope_files = _plan_scope_conflict_files(scope_conflict)
            # MA8 (spec §30): a DETERMINISTIC-authority obligation
            # (currently only the migration gate's authoritative_files,
            # attribution tier "authoritative_deterministic" - see
            # attribution.py) naming a file some OTHER real subtask in this
            # validated plan already owns must never be silently folded
            # into the failing subtask via the merge loop just below
            # (revise_plan_for_grounded_scope_owner, designed for genuine
            # architecture-discovery - PRV-03/04's own case) - that would
            # be exactly the "silently edit across stage boundaries" the
            # spec forbids, whether the true owner already ran (a
            # completed predecessor that should be REOPENED, not stripped)
            # or hasn't run yet / sits outside this subtask's own
            # dependency chain entirely (a genuine plan inconsistency that
            # should surface for revision, not be auto-resolved).
            #
            # - PAST_ORDERED (a real upstream owner, found via
            #   resolve_scope_conflict_owners which walks depends_on
            #   transitively): skip the merge loop entirely so control
            #   falls through to the existing reopen-owner path below.
            # - FUTURE_ORDERED / UNRELATED (owned by some other subtask,
            #   but not upstream - resolve_scope_conflict_owners finds
            #   nothing for it, yet classify_file_ownership confirms it
            #   isn't UNOWNED either): also skip the merge loop, but this
            #   time the reopen-owner path finds nothing either (it only
            #   ever looks upstream) - the scope conflict then surfaces
            #   unresolved via the existing PLAN_SCOPE_REVISION_REQUIRED
            #   aggregation below (`scope_conflict_result`), which is
            #   exactly the doc's own "treat as plan inconsistency... do
            #   not cross-edit silently" outcome, achieved by NOT resolving
            #   it automatically rather than by inventing a new failure path.
            # - Genuinely UNOWNED (nobody in the plan declared this file at
            #   all): leave grounded_scope_files untouched - the merge
            #   loop's own "no owner -> plan/scope defect -> use the
            #   existing authoritative plan revision mechanism" behavior is
            #   correct for this case and stays exactly as it was.
            #
            # Widened (2026-08-29, PRV-06, MA8.1), narrowly: the PAST_ORDERED
            # disjunct below (resolve_scope_conflict_owners finds a real,
            # unique UPSTREAM owner) now skips the merge loop regardless of
            # attribution_tier - the original gate ("authoritative_
            # deterministic" only) left an ordinary grounded compile-error
            # finding (tier "architectural_owner" - a downstream subtask
            # proving an upstream owner's file is missing something, e.g. a
            # build manifest missing a test dependency) going through the
            # DAG-mutating merge loop even when a clean upstream owner was
            # resolvable. revise_plan_for_grounded_scope_owner() is for
            # genuine architecture-discovery (permanently reassigning
            # ownership because the PLAN mis-assigned it) - a different
            # operation from "temporarily reopen an owner that's already
            # correctly assigned, to satisfy one new requirement, then
            # resume the downstream subtask," which is exactly what the
            # non-mutating owner-recovery path below (resolve_scope_
            # conflict_owners + CROSS_OWNER_ARTIFACT_REQUIREMENT) does - the
            # "recovery scheduling, not plan dependency" distinction that
            # incident's forensic analysis identified as the actual
            # conceptual mistake behind a live-observed cyclic-DAG revision
            # failure.
            #
            # The FUTURE_ORDERED/UNRELATED disjunct (owned, but NOT
            # upstream) deliberately KEEPS its original authoritative_
            # deterministic-only gate, unchanged - test_enforce_revises_
            # service_scope_to_grounded_controller_and_continues exercises
            # exactly this combination (tier="architectural_owner", a real
            # owner that is downstream, not upstream, of the failing
            # subtask) and depends on it continuing to route through the
            # merge path; widening THAT disjunct too was not evidenced by
            # the PRV-06 incident (whose owner was genuinely upstream) and
            # would have silently changed already-tested behavior with no
            # live incident behind it.
            # MA8.1 completion v3 (2026-08-29, "Effective Artifact Ownership
            # and Recovery Routing"): resolve_effective_scope_conflict_
            # owners replaces the bare depends_on-only resolve_scope_
            # conflict_owners here - the live PRV-06 incident this closes
            # is a downstream subtask needing an artifact whose real,
            # already-completed modifier sits OUTSIDE its own declared
            # depends_on (see resolve_effective_artifact_owner's own
            # docstring). This is the actual root fix: with it, this skip-
            # condition correctly recognizes a real effective owner and
            # never lets the failure fall through into the DAG-mutating
            # merge path below at all for that case.
            if (
                scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                and grounded_scope_files
                and (
                    resolve_effective_scope_conflict_owners(
                        plan, grounded_scope_files, subtask, order=order, control_state=control_state,
                    )
                    or (
                        scope_conflict.get("attribution_tier") == "authoritative_deterministic"
                        and any(
                            plan.classify_file_ownership(subtask.id, path) != FileOwnershipRelation.UNOWNED
                            for path in grounded_scope_files
                        )
                    )
                )
            ):
                grounded_scope_files = []
            # PRV-03: a chain of grounded owners (each repair surfacing the
            # NEXT file outside scope) needs repeated revise-and-revalidate
            # passes, not just one - see _MAX_PLAN_SCOPE_REVISION_ATTEMPTS.
            for _scope_revision_attempt in range(_MAX_PLAN_SCOPE_REVISION_ATTEMPTS):
                if not (
                    scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                    and grounded_scope_files
                ):
                    break
                revised_plan = revise_plan_for_grounded_scope_owner(
                    plan, subtask.id, grounded_scope_files, plan_workspace_path,
                )
                revision_validation = await validate_plan(
                    revised_plan,
                    workspace_path=plan_workspace_path,
                    available_tool_names=available_tool_names,
                    route=route,
                    triage_service=self.workflow_engine.engineering_triage,
                    resuming_own_established_progress=True,
                    require_model_planned_files=True,
                    require_semantic_contracts=True,
                )
                if revision_validation.valid:
                    prior_hash = current_plan_hash
                    plan = revised_plan
                    current_plan_hash = plan.content_hash()
                    execution_context = await self._build_context(
                        goal, plan, plan_workspace_path, route, control_context, control_state,
                    )
                    subtask = plan.subtask_by_id(subtask_id)
                    assert subtask is not None
                    predetermined_files = [pf.path for pf in subtask.planned_files]
                    for path in grounded_scope_files:
                        original_plan_revisions.setdefault(
                            path, read_file_revision(os.path.join(workspace_path, path)),
                        )
                    approved_stage_states = {
                        sid: approved_stage_states.get(sid, "pending")
                        for sid in topological_subtask_order(plan)
                    }
                    approved_stage_states[subtask.id] = "in_progress"
                    control_state = control_state.with_updates(
                        current_plan_hash=current_plan_hash,
                        subtask_states=dict(approved_stage_states),
                    )
                    save_approved_plan(
                        workspace_path, plan.plan_id,
                        build_approved_plan_document(
                            plan, plan_hash=current_plan_hash,
                            repair_attempts=repair_attempts,
                            stage_states=approved_stage_states,
                            lifecycle_state="scope_revised",
                        ),
                    )
                    save_control_state(workspace_path, control_state)
                    plan_recovery_events.append({
                        "failed_subtask": subtask.id,
                        "classification": "PLAN_SCOPE_DEFECT",
                        "required_repair_files": sorted(grounded_scope_files),
                        "prior_plan_hash": prior_hash,
                        "revised_plan_hash": current_plan_hash,
                        "ownership_revalidated": True,
                        "revalidation_basis": "deterministic grounded owner plus validate_plan",
                    })
                    logger.warning(
                        "Authoritative PLAN_SCOPE_DEFECT recovery revised subtask %s scope to %s "
                        "and revalidated plan %s.",
                        subtask.id, predetermined_files, current_plan_hash,
                    )
                    call_result = await _invoke_bounded_subtask(
                        subtask, position,
                        recovery_context=(
                            "--- authoritative PLAN_SCOPE_DEFECT recovery ---\n"
                            f"The validated scope now includes grounded owner(s): "
                            f"{json.dumps(grounded_scope_files)}. Modify the actual owner; do not "
                            "repeat the former service-only repair."
                        ),
                        execution_role="plan_scope_recovery",
                    )
                    scope_conflict = call_result.get("plan_scope_conflict") or {}
                    grounded_scope_files = _plan_scope_conflict_files(scope_conflict)
                else:
                    revision_failure_reason = (
                        "authoritative grounded-owner plan revision failed validation: "
                        + "; ".join(revision_validation.errors)
                    )
                    logger.error(
                        "WorkflowController enforce run %r: authoritative PLAN_SCOPE_DEFECT "
                        "recovery for subtask %r (grounded owner file(s) %s) produced a plan "
                        "that failed revalidation - %s. Falling back to upstream-owner "
                        "recovery; if that also cannot resolve it, this run terminates with "
                        "the scope conflict unresolved.",
                        run_id, subtask.id, grounded_scope_files, revision_failure_reason,
                    )
                    logger.info(
                        "PLAN_SCOPE_REVISION_REJECTED subtask=%s grounded_files=%s reason=%s",
                        subtask.id, grounded_scope_files, revision_failure_reason,
                    )
                    logger.info(
                        "OWNER_RECOVERY_FALLBACK_STARTED subtask=%s required_files=%s",
                        subtask.id, sorted(set(scope_conflict.get("required_files", []))),
                    )
                    scope_conflict = {
                        **scope_conflict,
                        "reason": revision_failure_reason,
                    }
                    break
            else:
                if (
                    scope_conflict.get("classification") == "PLAN_SCOPE_DEFECT"
                    and grounded_scope_files
                ):
                    logger.error(
                        "WorkflowController enforce run %r: subtask %r hit the bound of %d "
                        "authoritative PLAN_SCOPE_DEFECT revision attempts and still surfaces a "
                        "further grounded owner scope conflict (%s) - falling back to "
                        "upstream-owner recovery; if that also cannot resolve it, this run "
                        "terminates with the scope conflict unresolved.",
                        run_id, subtask.id, _MAX_PLAN_SCOPE_REVISION_ATTEMPTS, grounded_scope_files,
                    )
                    logger.info(
                        "OWNER_RECOVERY_FALLBACK_STARTED subtask=%s required_files=%s",
                        subtask.id, sorted(set(scope_conflict.get("required_files", []))),
                    )
            # MA8.1 completion (2026-08-29 v2): looped, not one-shot - a
            # downstream retry after a successful owner recovery may
            # surface a genuinely NEW grounded requirement on the SAME
            # owner (see "Requirement-scoped repeated owner recovery"
            # near recovery_generation_by_key's own declaration above).
            # Each iteration re-resolves owner_map fresh against the
            # CURRENT scope_conflict; boundedness comes from each
            # requirement's own attempt count (via the ledger's history()),
            # not from a blanket "this owner already ran once" guard.
            for _owner_recovery_cycle in range(_MAX_PLAN_SCOPE_REVISION_ATTEMPTS):
                # MA8.1 completion v3: same effective-owner upgrade as the
                # merge-skip condition above - execution provenance tried
                # first, dependency-ancestry fallback unchanged.
                owner_map = resolve_effective_scope_conflict_owners(
                    plan, list(scope_conflict.get("required_files", [])), subtask,
                    order=order, control_state=control_state,
                ) if scope_conflict else {}
                required_scope_files = set(scope_conflict.get("required_files", []))
                resolved_scope_files = {
                    path for owner_paths in owner_map.values() for path in owner_paths
                }
                if not (len(owner_map) == 1 and resolved_scope_files == required_scope_files):
                    # MA8.1 completion v3 (§13/§22, "fix false fallback
                    # behavior"): the plan-revision loop above logs that it
                    # is "falling back to upstream-owner recovery" whenever
                    # its own validation fails - this is the code that IS
                    # that fallback. Previously, when it couldn't resolve a
                    # unique owner either, it broke here with NO log line at
                    # all, so a real run's own logs claimed a fallback that
                    # never observably happened. Emitted only when there was
                    # a genuine conflict to resolve (an empty scope_conflict
                    # on the very first pass is not a failure).
                    if scope_conflict:
                        logger.warning(
                            "SCOPE_RECOVERY_OWNER_UNRESOLVED subtask=%s required_files=%s "
                            "resolved_owners=%s - failing closed, no owner will be guessed.",
                            subtask.id, sorted(required_scope_files), owner_map,
                        )
                    break
                owner_id, required_owner_files = next(iter(owner_map.items()))
                owner = plan.subtask_by_id(owner_id)
                if owner is None:
                    break
                generation_key = (subtask.id, owner_id, tuple(sorted(required_owner_files)))
                generation = recovery_generation_by_key.get(generation_key, 0)
                candidate_requirement_id = _cross_owner_obligation_id(
                    subtask.id, owner_id, required_owner_files,
                    scope_conflict.get("failure_type") or "unknown", generation,
                )
                prior_attempts = (
                    len(obligation_ledger.history(candidate_requirement_id))
                    if obligation_ledger is not None else 0
                )
                if prior_attempts >= _MAX_PLAN_SCOPE_REVISION_ATTEMPTS:
                    logger.warning(
                        "WorkflowController enforce run %r: requirement %r exhausted its "
                        "bounded recovery attempts (%d) - stopping rather than reopening %r "
                        "again for the same unresolved requirement.",
                        run_id, candidate_requirement_id, prior_attempts, owner_id,
                    )
                    break
                if owner is not None:
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
                    # MA8.1 (PRV-06, 2026-08-29): the grounded reason this
                    # owner is being reopened is promoted into a durable
                    # CROSS_OWNER_ARTIFACT_REQUIREMENT obligation and
                    # threaded explicitly into the owner's own Developer
                    # prompt - the exact requirement-propagation gap the
                    # live incident exposed. Sticky within one generation
                    # (see _get_or_create_cross_owner_obligation's own
                    # docstring) - a recurring unresolved requirement reuses
                    # the SAME MUST_FIX text, never degrading back to
                    # generic brownfield framing; a genuinely new generation
                    # gets its own fresh requirement instead of being
                    # silently blocked.
                    cross_owner_obligation = _get_or_create_cross_owner_obligation(
                        obligation_ledger,
                        originating_subtask_id=subtask.id,
                        owner_subtask_id=owner_id,
                        required_files=required_owner_files,
                        scope_conflict=scope_conflict,
                        generation=generation,
                        revision=len(plan_recovery_events),
                    )
                    recovery_context = _build_owner_recovery_context(
                        owner_id=owner_id,
                        failed_subtask_id=subtask.id,
                        required_owner_files=required_owner_files,
                        cross_owner_obligation=cross_owner_obligation,
                        scope_conflict=scope_conflict,
                    )
                    logger.info(
                        "CROSS_OWNER_RECOVERY_SCHEDULED requirement_id=%s artifact=%s owner_subtask_id=%s",
                        candidate_requirement_id, sorted(required_owner_files), owner_id,
                    )
                    logger.info(
                        "OWNER_RECOVERY_STARTED requirement_id=%s owner=%s generation=%d",
                        candidate_requirement_id, owner_id, generation,
                    )
                    owner_result = await _invoke_bounded_subtask(
                        owner, owner_position, recovery_context=recovery_context,
                        execution_role="owner_recovery",
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
                    logger.info(
                        "OWNER_RECOVERY_COMPLETED requirement_id=%s owner=%s generation=%d passed=%s",
                        candidate_requirement_id, owner_id, generation, owner_passed,
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
                        "ownership_revalidated": True,
                        "revalidation_basis": "unique approved upstream file owner",
                        "owner_recovery_passed": owner_passed,
                        "requirement_id": candidate_requirement_id,
                        "generation": generation,
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
                                    os.path.join(plan_workspace_path, path), "r",
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
                        # Correctness Continuity Part C: the reopened owner
                        # may itself be a relationship's consumer (not just
                        # its producer) - no-op when it isn't.
                        _evaluate_integration_obligations(
                            plan, obligation_ledger, owner_id, established_file_context, owner_position,
                        )
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
                        logger.info(
                            "CONSUMER_RETRY_STARTED subtask=%s active_requirement_id=%s",
                            subtask.id, candidate_requirement_id,
                        )
                        call_result = await _invoke_bounded_subtask(
                            subtask, position,
                            recovery_context=(
                                "--- upstream recovery completed ---\n"
                                f"Upstream owner {owner_id} was repaired and re-verified. "
                                "Re-run this consumer against the updated workspace."
                            ),
                            execution_role="consumer_retry",
                        )
                        # MA8.1 (PRV-06, 2026-08-29): the obligation's own
                        # acceptance condition IS this consumer_retry call's
                        # Quality Gates - not a separate, speculative check
                        # of the owner's file content (which the owner's own
                        # narrow gate has no way to evaluate against a
                        # DOWNSTREAM requirement anyway - confirmed live:
                        # pom.xml's own compile check happily accepted a
                        # still-incomplete manifest). SATISFIED only on the
                        # real, authoritative signal; left VIOLATED (sticky,
                        # unchanged) otherwise, so a further reopening of
                        # the SAME owner for the SAME requirement reuses the
                        # SAME MUST_FIX text rather than losing it again.
                        if cross_owner_obligation is not None:
                            if bool(call_result.get("quality_gates_passed")):
                                obligation_ledger.record(ObligationRecord(
                                    id=cross_owner_obligation.id,
                                    kind=ObligationKind.CROSS_OWNER_ARTIFACT_REQUIREMENT,
                                    status=ObligationStatus.SATISFIED,
                                    authority=ObligationAuthority.DETERMINISTIC,
                                    description=cross_owner_obligation.description,
                                    source="workflow_controller.owner_recovery",
                                    revision=len(plan_recovery_events),
                                    evidence=cross_owner_obligation.evidence,
                                    owner_subtask_id=cross_owner_obligation.owner_subtask_id,
                                    terminal_required=False,
                                    repair_scope=cross_owner_obligation.repair_scope,
                                ))
                        # This exact (origin, owner, files) tuple has now
                        # completed one full owner-recovery-and-downstream-
                        # retry cycle - the NEXT scope conflict for this
                        # same tuple (if any) is a new generation, eligible
                        # for its own fresh requirement rather than being
                        # silently treated as a continuation of this one.
                        recovery_generation_by_key[generation_key] = generation + 1
                        if bool(call_result.get("quality_gates_passed")):
                            break
                        scope_conflict = call_result.get("plan_scope_conflict") or {}
                        if not scope_conflict:
                            break
                        continue
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
                        recovery_generation_by_key[generation_key] = generation + 1
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
                    + (f" Reason: {error}" if error else "")
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
                    with open(os.path.join(plan_workspace_path, path), "r", encoding="utf-8", errors="replace") as fh:
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

            # Correctness Continuity Part C (PRV-06, 2026-08-29): this
            # subtask just finished and its files are now in
            # established_file_context - the earliest point any
            # relationship it's a CONSUMER for can be deterministically
            # (re-)evaluated. No-op when the plan declares no integration
            # relationships at all (every plan predating this feature).
            _evaluate_integration_obligations(
                plan, obligation_ledger, subtask_id, established_file_context, position,
            )

        # Authoritative scope recovery may merge/remove a stage. Completion
        # must be measured against the revalidated CURRENT plan, not the
        # original loop's pre-revision `total`, or a successfully executed
        # revised plan is falsely reported as failed solely because it has
        # fewer stages.
        current_plan_subtask_ids = {subtask.id for subtask in plan.subtasks}
        latest_status_by_subtask = {
            result.subtask_id: result.status for result in subtask_results
        }
        # Scope recovery can merge/remove a subtask mid-run, leaving its
        # stale COMPLETED entry in subtask_results even though it's no
        # longer part of the current plan. Measure completion against the
        # current plan's ids only - a set-equality check against the
        # (append-only, never-pruned) subtask_results keys would falsely
        # report a fully successful revised plan as failed.
        all_completed = all(
            latest_status_by_subtask.get(subtask_id) == SubtaskStatus.COMPLETED
            for subtask_id in current_plan_subtask_ids
        )
        try:
            if all_completed and plan_workspace_path != workspace_path:
                terminal_writes: List[StagedFileWrite] = []
                # plan_validation.py's AMBIGUOUS_PLANNED_FILE_OWNERSHIP check
                # legitimately allows the same path to be owned by a
                # dependency-ordered sequential chain of subtasks (e.g. an
                # "identify usages" stage and a later "migrate" stage both
                # touching pom.xml) - so plan.subtasks can list the same
                # planned_file.path more than once. Building one StagedFileWrite
                # per (subtask, planned_file) pair here fed
                # commit_revision_grounded_batch's own duplicate-target-path
                # guard a genuine duplicate and hard-crashed terminal commit
                # for any such plan. Resolve to exactly ONE entry per unique
                # path instead: walk subtasks in real dependency/execution
                # order (not plan.subtasks' possibly-unordered list order) and
                # let the last owner's declared action win - plan_workspace_path
                # is a single worktree shared by every subtask, so its on-disk
                # content already reflects that final, validated state.
                subtasks_by_id = {subtask.id: subtask for subtask in plan.subtasks}
                execution_ordered_subtasks = [
                    subtasks_by_id[sid] for sid in topological_subtask_order(plan) if sid in subtasks_by_id
                ]
                final_action_by_path: Dict[str, FileAction] = {}
                for planned_subtask in execution_ordered_subtasks:
                    for planned_file in planned_subtask.planned_files:
                        final_action_by_path[planned_file.path] = planned_file.action
                for path, action in final_action_by_path.items():
                    if action == FileAction.DELETE:
                        candidate_path = os.path.join(plan_workspace_path, path)
                        if os.path.exists(candidate_path):
                            raise RuntimeError(
                                f"Terminal plan candidate did not delete approved file {path!r}"
                            )
                        target_path = os.path.join(workspace_path, path)
                        terminal_writes.append(StagedFileWrite(
                            target_path=target_path,
                            content="",
                            base_path=target_path,
                            expected_base_revision=original_plan_revisions[path],
                            delete=True,
                        ))
                        continue
                    candidate_path = os.path.join(plan_workspace_path, path)
                    try:
                        with open(candidate_path, "r", encoding="utf-8", errors="replace") as handle:
                            candidate_content = handle.read()
                    except OSError as error:
                        raise RuntimeError(
                            f"Terminal plan candidate is missing approved file {path!r}: {error}"
                        ) from error
                    target_path = os.path.join(workspace_path, path)
                    terminal_writes.append(StagedFileWrite(
                        target_path=target_path,
                        content=candidate_content,
                        base_path=target_path,
                        expected_base_revision=original_plan_revisions[path],
                    ))
                commit_revision_grounded_batch(terminal_writes, workspace_path=workspace_path)
            elif not all_completed and plan_workspace_path != workspace_path:
                # No subtask output reached the user workspace. A later resume
                # must rerun previously successful stages rather than skipping
                # them based on sandbox-only work that has now been discarded.
                approved_stage_states = {
                    subtask_id: ("pending" if status == "completed" else status)
                    for subtask_id, status in approved_stage_states.items()
                }
                control_state = control_state.with_updates(
                    subtask_states={
                        subtask_id: ("pending" if status == "completed" else status)
                        for subtask_id, status in control_state.subtask_states.items()
                    },
                    subtask_written_files={},
                )
                save_control_state(workspace_path, control_state)
        finally:
            if plan_workspace_path != workspace_path:
                remove_git_worktree(workspace_path, plan_workspace_path)

        # Global final-state validation: every subtask can pass its own
        # LOCAL Quality Gates while a plan-wide obligation is still globally
        # unsatisfied in the final applied state - "s1 PASSED, s2 PASSED,
        # s3 PASSED, s4 PASSED, therefore overall PASSED" is insufficient
        # for a migration. Found live, PRV-05 (2026-08-28, run 5): the
        # per-subtask migration-completion check (kriya/workflow/attempt.py)
        # only ever sees the ONE subtask's own grounded scope; this is the
        # same completion check re-run once, here, against the real,
        # fully-applied workspace_path - the authoritative terminal state -
        # after every subtask has already completed and its output has
        # already been committed above.
        #
        # PRV-05 run 6 (2026-08-28): this used to RE-RESOLVE identity too
        # (resolve_migration_obligation_for_workspace against the final,
        # already-migrated tree) - by the time migration is done, the real
        # SOURCE looks "unused" and the heuristic inverts itself, silently
        # resolving None and never reaching find_migration_incomplete at
        # all. Reuses the run's ONE `migration_resolution`, resolved once
        # above against the immutable baseline, instead - identity is fixed;
        # only completion is checked here, against the final tree, which is
        # exactly what find_migration_incomplete is for.
        #
        # Three distinct outcomes, not two: NOT_APPLICABLE (continue -
        # obligation satisfied or no migration intent at all), RESOLVED-and-
        # satisfied (continue), RESOLVED-and-unsatisfied (fail). INDETERMINATE
        # is ALSO a fail, not a silent continue: the goal explicitly expressed
        # replacement intent but this run could never confidently establish
        # what was being replaced with what - claiming success on an
        # unconfirmed migration obligation is the exact false-PASS shape this
        # gate exists to prevent, so an unresolved identity must not default
        # to "no obligation applies." The check itself raising is handled the
        # same way - fail closed, never silently trust the per-subtask gates.
        # A goal with no migration intent at all can't realistically reach
        # either failure path (_goal_expresses_replacement_intent is a cheap
        # regex gate checked first, before any file I/O), so this only ever
        # activates for a goal that already looks like a migration - not a
        # broad new failure surface for ordinary tasks.
        global_migration_gap: Optional[str] = None
        if all_completed:
            try:
                if migration_resolution.status == MigrationResolutionStatus.RESOLVED:
                    obligation = migration_resolution.obligation
                    gap = find_migration_incomplete(
                        obligation, workspace_path,
                        validation_scope=MigrationValidationScope.TERMINAL,
                        obligation_ledger=obligation_ledger,
                        revision="terminal", source="migration.terminal_gate",
                    ) if obligation else None
                    if gap:
                        global_migration_gap = (
                            "MIGRATION INCOMPLETE (global final-state check): the goal "
                            f"explicitly requires replacing {gap['source_identity']} with "
                            f"{gap['target_identity']}, but {', '.join(gap['reason_codes'])}."
                        )
                        all_completed = False
                elif migration_resolution.status == MigrationResolutionStatus.INDETERMINATE:
                    global_migration_gap = (
                        "MIGRATION OBLIGATION INDETERMINATE (global final-state check): the goal "
                        "explicitly expresses replacement intent, but source/target dependency "
                        f"identity could not be resolved confidently ({migration_resolution.reason}). "
                        "Refusing to report success on an unconfirmed migration obligation."
                    )
                    # MA8 (spec §3.4/§9's own example: "migration identity
                    # cannot be resolved safely: INDETERMINATE") - this was
                    # the one status ObligationStatus already defined but no
                    # producer ever actually recorded, so
                    # unresolved_terminal_obligations()'s own INDETERMINATE
                    # handling stayed dead code. No MigrationObligation
                    # exists to derive source_identity/target_identity from
                    # here (that's exactly WHY resolution is indeterminate),
                    # so the id is plan-level, not per-migration-requirement.
                    if obligation_ledger is not None:
                        obligation_ledger.record(ObligationRecord(
                            id="migration.identity_resolution",
                            kind=ObligationKind.MIGRATION_COMPLETION,
                            status=ObligationStatus.INDETERMINATE,
                            authority=ObligationAuthority.DETERMINISTIC,
                            description="migration source/target dependency identity could not be "
                                        "resolved confidently from the immutable pre-mutation baseline",
                            source="migration.terminal_gate", revision="terminal",
                            evidence={"reason": migration_resolution.reason},
                            terminal_required=True,
                        ))
                    all_completed = False
            except Exception as e:
                global_migration_gap = (
                    "MIGRATION FINAL-STATE CHECK INDETERMINATE: the deterministic terminal "
                    f"migration validator itself raised ({type(e).__name__}: {e}) - refusing to "
                    "report success on an unverifiable terminal obligation rather than silently "
                    "trusting the per-subtask gates."
                )
                all_completed = False

        # MA8 (spec §42/43) - a generic backstop layered ALONGSIDE the
        # migration-specific gate above, not a replacement for it (§43's
        # own explicit interim model: existing gates AND terminal MA8
        # obligations, not one instead of the other). Today every
        # terminal_required obligation kind (PLAN_STRUCTURAL_VALIDITY,
        # MIGRATION_COMPLETION) already has its own dedicated gate earlier
        # in this run (plan-repair loop, the migration check just above),
        # so this rarely fires on its own - its value is generalizing:
        # any FUTURE obligation producer that marks terminal_required=True
        # is automatically covered here without a new gate being wired by
        # hand, and it catches the case where a specific gate's own
        # all_completed flip was somehow bypassed.
        global_terminal_obligation_gap: Optional[str] = None
        if all_completed:
            unresolved_terminal = obligation_ledger.unresolved_terminal_obligations()
            if unresolved_terminal:
                global_terminal_obligation_gap = (
                    "TERMINAL OBLIGATIONS UNSATISFIED (MA8 global aggregation check): "
                    + "; ".join(
                        f"{rec.id} ({rec.status.value}, authority={rec.authority.value})"
                        for rec in unresolved_terminal
                    )
                )
                all_completed = False

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
        if global_migration_gap:
            aggregated["global_migration_gap"] = global_migration_gap
            logger.error(
                f"WorkflowController enforce run {run_id!r}: {global_migration_gap}"
            )
        if global_terminal_obligation_gap:
            aggregated["global_terminal_obligation_gap"] = global_terminal_obligation_gap
            logger.error(
                f"WorkflowController enforce run {run_id!r}: {global_terminal_obligation_gap}"
            )
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
