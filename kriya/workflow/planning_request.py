"""The structured-planning request and the plan checks that build on it (full modularization W4, E11).

Moved verbatim from ``kriya/workflow/workflow_controller.py`` (module-level code; the enforce loop that calls it stays in
the controller):

- the bounded subtask texts the enforce loop hands each unit (``build_subtask_goal_text``,
  ``build_subtask_constraint_context``, ``build_subtask_semantic_context``, ``build_subtask_plan_text``,
  ``BOUNDED_SUBTASK_DESIGN``, ``split_planned_source`` with ``PLANNED_FILE_SOURCE_REASON``);
- the authoritative Planner request: its system prompt (``AUTHORITATIVE_PLANNER_SYSTEM_PROMPT``), the user request
  (``build_authoritative_planner_request``), the goal-ranked repository candidates
  (``_authoritative_planner_extension_candidates``) and the grounded structural evidence
  (``build_planning_structural_evidence``, ``_JAVA_CONSTRUCTOR_INSTANTIATION_RE``, ``_JAVA_TYPE_MENTION_RE``);
- the plan checks of the structured-plan repair loop: the grounded-production-artifact completeness check
  (``find_missing_grounded_production_artifacts``, ``_transitive_depends_on``), the planned-edit feasibility
  (``evaluate_plan_edit_feasibility``), the repair rounds' regression guards (``_is_strict_regression``, the
  must-preserve lines, ``_subtask_ids_mentioned``, ``_semantic_contract_regression_subtasks``,
  ``_preserved_reference_pairs_mentioned``, ``_preserved_reference_regressions`` and their patterns) and the approved
  plan's durable document (``build_approved_plan_document``).

Every text, decision and obligation record is unchanged (the records keep their
``workflow_controller.find_missing_grounded_production_artifacts`` source). ``kriya/workflow/workflow_controller.py``
keeps every one of these names (a forwarder, or a re-export of a constant) so a test patch on
``kriya.workflow.workflow_controller`` keeps intercepting the controller's calls and ``ownership_findings``' call-time
import of ``build_planning_structural_evidence``. What arrives in ``PlanningDeps``, resolved through
``kriya.workflow.workflow_controller`` by ``workflow_controller._planning_deps()`` when a forwarder runs: the
controller's ``logger``, the edit-feasibility names the suite patches on the controller (``model_capacities``,
``assess_planned_edit``, ``collect_unit_grounding``) with the other names of that all-import-cycle module
(``EditFeasibility``, ``Feasibility``) and the cycle's ``topological_subtask_order`` (``subtask_checkpoint``). The
moved functions call each other directly (no test patches one of them expecting the call from another to be
intercepted). Names of modules outside that cycle that never reach it are imported here directly. This module never
imports ``kriya.workflow.workflow_controller``, ``workflow``, ``attempt`` or ``kriya.cli``, nor any all-import-cycle
module; ``EditFeasibility`` in an annotation is spelled ``Any`` for that reason.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from kriya.agents.contracts import (
    AUTHORITATIVE_GOAL_SECTION_HEADER,
    ENVIRONMENT_DEPENDENCY_RULE,
    GLOBAL_INVARIANT_PROJECTION_RULE,
    PLANNED_IMPLEMENTATION_SECTION_HEADER,
)
from kriya.analyzer.graph import DependencyGraph
from kriya.workflow.edit_safety import read_file_revision, strip_java_comments_and_strings
from kriya.workflow.file_resolution import is_runnable_test_file
from kriya.workflow.generation_manifest import FileRole, classify_file_role
from kriya.workflow.obligations import (
    ObligationAuthority,
    ObligationKind,
    ObligationLedger,
    ObligationRecord,
    ObligationStatus,
)
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, ExecutionRole, FileAction, Subtask
from kriya.workflow.triage import ChangeKind


@dataclass(frozen=True)
class PlanningDeps:
    """What the moved planning-request code may use, resolved through ``kriya.workflow.workflow_controller`` by
    ``workflow_controller._planning_deps()``. Every field is required: a builder that omits one fails at construction,
    never at the first use."""

    # the controller's logger
    logger: Any
    # kriya.workflow.edit_feasibility (all-import cycle; the first three are patched on the controller by the suite)
    model_capacities: Callable[..., Any]
    assess_planned_edit: Callable[..., Any]
    collect_unit_grounding: Callable[..., Any]
    EditFeasibility: Any
    Feasibility: Any
    # kriya.workflow.subtask_checkpoint (all-import cycle)
    topological_subtask_order: Callable[..., Any]


def build_subtask_goal_text(
    subtask: Subtask,
    position: int,
    total: int,
    *,
    plan: Optional[EngineeringPlan] = None,
    grounding_goal: str = "",
) -> str:
    """MA7.8 - the per-subtask analogue of kriya/workflow/milestones.py's
    build_milestone_goal_text(): deterministic string assembly, no extra
    LLM call, same reasoning (run_generation_workflow's own repo-analysis
    stage re-scans workspace_path fresh on every call, so real upstream
    output is visible once applied. Plan-level semantic contracts and
    global invariants are rendered separately by
    build_subtask_semantic_context(), keeping this string the bounded
    executable goal rather than an unstructured copy of the whole plan).

    grounding_goal (authority-isolation fix, PRV-11, 2026-08-30): the
    unmediated top-level user request, passed by the ONE real caller
    (_invoke_bounded_subtask, which already has it in scope as its own
    outer `goal`). When present, everything this function would otherwise
    return unlabeled (subtask.description, mapped acceptance criteria,
    planned_files, verification - ALL Planner-authored, however faithfully
    derived from the real goal) is rendered under an explicit "Planned
    Implementation Strategy" header, separate from the "Authoritative
    Goal" section holding grounding_goal verbatim - see contracts.py's own
    AUTHORITATIVE_GOAL_SECTION_HEADER/PLANNED_IMPLEMENTATION_SECTION_HEADER
    docstring for the live incident this closes and how SpecComplianceAgent
    (the one consumer that was actually treating this flattening as unsafe)
    now reads the split. Every existing test-visible substring (the
    dependency header, subtask.description, mapped acceptance text, the
    planned-files list, verification text) is preserved verbatim - this is
    an additive relabeling, not a content change. Omitting grounding_goal
    (default "") reproduces the exact prior flat return, unchanged - no
    caller that doesn't pass it sees any difference."""
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
    planned_strategy = header + subtask.description + acceptance + planned + verification
    if not grounding_goal:
        return planned_strategy
    return (
        f"{AUTHORITATIVE_GOAL_SECTION_HEADER}\n"
        "This is the real, unmediated user request - the source of truth for what is "
        "actually required. Nothing below this section may expand it: a concrete "
        "identifier, structure, or value that appears ONLY in the Planned Implementation "
        "Strategy below (never in this section) is the Planner's own implementation "
        "choice, not a new user requirement.\n"
        f"{grounding_goal}\n\n"
        f"{PLANNED_IMPLEMENTATION_SECTION_HEADER}\n"
        "The Planner's own chosen approach for satisfying the authoritative goal above "
        "for THIS subtask - follow it, but it may be adapted if it conflicts with the "
        "authoritative goal or with real constraints discovered while implementing it.\n"
        f"{planned_strategy}"
    )


async def evaluate_plan_edit_feasibility(
    deps: PlanningDeps,
    config: Optional[Any], plan: EngineeringPlan, goal: str, workspace_path: str,
) -> List[Any]:
    """PLAN-EDIT-FEASIBILITY-001: the feasibility of every planned edit of an
    existing file in a MODEL implementation unit, from the same unit goal text
    and the same helpers the executor decides with (kriya/workflow/
    edit_feasibility.py). The unit's own retrieval runs only for a file whose
    whole source cannot fit and no other source has grounded yet. Anything that
    cannot be decided is UNKNOWN, never a rejection: a file an earlier unit of
    the plan also edits (the unit will see other bytes), an unreadable file, or
    a failure while deciding."""
    if config is None:  # no kernel config (a bare engine): no model capacity to decide with
        return []
    try:
        capacities = deps.model_capacities(config)
    except Exception as error:  # the verdict is UNKNOWN for every file; the plan loop must not abort on it
        deps.logger.warning("Edit feasibility: model capacity unavailable (%s: %s)", type(error).__name__, error)
        capacities = []
    order = deps.topological_subtask_order(plan)
    by_id = {subtask.id: subtask for subtask in plan.subtasks}
    results: List[Any] = []
    edited_by_earlier_units: set = set()
    for position, subtask_id in enumerate(order, start=1):
        subtask = by_id[subtask_id]
        planned_paths = [planned.path for planned in subtask.planned_files]
        if subtask.execution_method != ExecutionMethod.MODEL or subtask.execution_role == ExecutionRole.VERIFICATION:
            edited_by_earlier_units.update(planned_paths)
            continue
        existing = []
        for planned in subtask.planned_files:
            full = os.path.join(workspace_path, planned.path)
            if planned.action != FileAction.MODIFY or not os.path.isfile(full):
                continue
            if planned.path in edited_by_earlier_units:
                results.append(deps.EditFeasibility(
                    subtask.id, planned.path, deps.Feasibility.UNKNOWN,
                    "an earlier unit of the plan also edits this file, so the unit will see different bytes"))
                continue
            try:
                with open(full, encoding="utf-8") as handle:
                    existing.append((planned.path, handle.read()))
            except (OSError, UnicodeDecodeError):
                results.append(deps.EditFeasibility(
                    subtask.id, planned.path, deps.Feasibility.UNKNOWN, "the current source cannot be read as text"))
        edited_by_earlier_units.update(planned_paths)
        if not existing:
            continue
        unit_goal = build_subtask_goal_text(subtask, position, len(order), plan=plan, grounding_goal=goal)
        grounding = None
        grounding_collected = False
        for path, content in existing:
            try:
                assessment = deps.assess_planned_edit(
                    subtask_id=subtask.id, path=path, content=content, unit_goal=unit_goal, grounding_goal=goal,
                    capacities=capacities, grounding=grounding, unit_file_count=len(subtask.planned_files))
                if assessment.evidence.get("needs_grounding") and not grounding_collected:
                    grounding = await deps.collect_unit_grounding(config, workspace_path, unit_goal)
                    grounding_collected = True
                    assessment = deps.assess_planned_edit(
                        subtask_id=subtask.id, path=path, content=content, unit_goal=unit_goal,
                        grounding_goal=goal, capacities=capacities, grounding=grounding,
                        unit_file_count=len(subtask.planned_files))
            except Exception as error:  # UNKNOWN, never a rejection; the runtime decision stands
                deps.logger.warning("Edit feasibility: %s of %s not decided (%s: %s)", subtask.id, path,
                               type(error).__name__, error)
                assessment = deps.EditFeasibility(
                    subtask.id, path, deps.Feasibility.UNKNOWN, f"not decided: {type(error).__name__}")
            results.append(assessment)
    return results


# GRAPHIFY-OVERSIZE-REQUEST-001: the reason _build_context labels a planned
# file's full current on-disk content with. The bounded subtask call never
# renders such an item as prompt text; it passes the paths on, and the
# attempt renders them budgeted (planned_source_files).
PLANNED_FILE_SOURCE_REASON = "planned file, current on-disk content"


# GOAL-DESIGN-DEDUP-001: GOAL != PREDETERMINED DESIGN. A validated Subtask
# carries no design artifact of its own, so a bounded subtask supplies an
# empty design: supplied (run_generation_workflow does not call the
# Architect) but never the subtask goal restated as one. The goal already
# reaches the Developer as its task (target_goal embeds the authoritative
# goal) and as the overall request constraints; rendering it as the design
# too put the full original goal into the request a third time.
BOUNDED_SUBTASK_DESIGN = ""


def split_planned_source(package: Any) -> Tuple[List[str], Any]:
    """(the planned-file paths whose full current source ``package`` carries,
    the package without those items) - what the bounded subtask renders as
    prompt text and what it hands the attempt to render budgeted."""
    paths = [item.path for item in package.relevant_files if item.reason == PLANNED_FILE_SOURCE_REASON]
    rest = tuple(item for item in package.relevant_files if item.reason != PLANNED_FILE_SOURCE_REASON)
    return paths, package.with_changes(relevant_files=rest)


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
    runtime_owner_ids = sorted({
        candidate.id for candidate in plan.subtasks
        if any(vm.requires_application_runtime for vm in candidate.verification)
    })
    owns_test_artifact = any(
        classify_file_role(planned.path) is FileRole.TEST
        for planned in subtask.planned_files
    )
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
        "application_runtime_owners": runtime_owner_ids,
        "verification_ownership": (
            "This subtask's unit-test artifacts do not own process-exit, process-termination, "
            "or termination-associated stdout/stderr obligations. Those are owned exclusively "
            "by the declared application_runtime verifier. Unit tests may cover only safe "
            "in-process behavior and must not independently reproduce the process obligation."
            if owns_test_artifact and runtime_owner_ids else None
        ),
    }
    return "--- bounded subtask semantic context ---\n" + json.dumps(payload, indent=2, sort_keys=True)


def build_subtask_plan_text(subtask: Subtask) -> str:
    """MA7-C1 (2026-08-25 external review) - the 'plan' half of the
    predetermined_plan/predetermined_design bypass (kriya/workflow/
    workflow.py::run_generation_workflow) that lets _run_structured_enforce
    stop re-running a fresh Planner+Architect cycle for an already-validated
    Subtask. Deliberately short, plain prose: a plan is never a source of
    file content (FILE-INTEGRITY-CONTRACT-001B), so the subtask's files come
    from an ordinary Developer generation call."""
    return f"Implement: {subtask.description}"


AUTHORITATIVE_PLANNER_SYSTEM_PROMPT = (
    "You are Kriya's authoritative structured Planner. Return exactly one valid JSON object and "
    "nothing else: no Markdown, code fences, prose, or rationale. Decompose the request into the "
    "smallest safe set of bounded implementation subtasks. Use this top-level shape: "
    '{"global_invariants": [{"id": "gi1", "statement": "..."}], "subtasks": [{"id": "s1", '
    '"description": "...", "execution_method": "model", "execution_role": "implementation", '
    '"depends_on": [], "planned_files": [{"path": "...", "action": "create|modify|delete", '
    '"environment_requirements": ["..."], "requires_capabilities": ["..."], '
    '"preserved_references": ["..."]}], '
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
    + GLOBAL_INVARIANT_PROJECTION_RULE + " "
    "Every model subtask has an execution_role: implementation or verification. An implementation "
    "subtask must own every file it may change in planned_files. A verification subtask (e.g. "
    "\"run the regression suite and confirm existing behavior is preserved\") MUST set "
    "execution_role=verification, planned_files=[] (it never writes anything), and at least one "
    "real entry in verification - never invent a file for it. Each planned path must be owned by "
    "exactly one implementation subtask. Do not emit an implementation subtask solely to analyze, "
    "inspect, research, or explain code; the implementation subtask performs any necessary "
    "analysis before editing its owned file. Every implementation subtask that changes files must "
    "list at least one deterministic verifier (compile, test, or application_runtime) in its "
    "verification; judgment acceptance criteria alone never complete it. "
    "A verification entry must always be an object, never "
    "a string. A judgment verification must omit tool_name. A tool verification must name a real "
    "registered tool; use tool_name=compile/verifier_kind=compile for compilation and "
    "tool_name=test/verifier_kind=test for tests. An application_runtime verification is a "
    "JUDGMENT verification, not a tool verification: set type=judgment, omit tool_name entirely, "
    "and set verifier_kind=application_runtime and requires_runtime_execution=true TOGETHER - all "
    "four together, only when the plan explicitly requires executing the application - never set "
    "one without the other, and never set type=tool or any tool_name for this verifier; a verifier "
    "claiming application_runtime kind without requires_runtime_"
    "execution=true will never actually be executed. Do not use verifier_kind=judgment for "
    "behavior that can only be established by executing the application - observable output, "
    "exit behavior, processing sample input, runtime side effects, etc.; a judgment "
    "verification with no tool_name and requires_runtime_execution=false has no execution path "
    "that can ever confirm it and will be rejected - use verifier_kind=application_runtime with "
    "requires_runtime_execution=true instead. Every "
    "acceptance criterion may be assigned only to a stage capable of demonstrating it. Every "
    "requires value must exactly equal one provides value from exactly one "
    "declared dependency. Preserve goal-derived invariants without inventing unspecified choices. "
    "A stage whose tests need build/test tooling (a Maven/Gradle/npm/pip manifest, a test "
    "framework dependency) that another stage's planned_files owns must declare that manifest "
    "stage's provides value in the artifact's requires_capabilities and in the stage's own "
    "requires and depends_on - execution order is not "
    "guaranteed by planned_files or list position alone, only by depends_on. "
    "Put ambient runtimes and tools such as java, maven, python, node, and pytest in "
    "planned_files[].environment_requirements, never in subtask requires/provides. "
    "planned_files[].requires_capabilities names only an exact capability supplied by another "
    "planned subtask. "
    + ENVIRONMENT_DEPENDENCY_RULE + " "
    "If a test or other planned artifact references a real, pre-existing production file that "
    "the goal does not require changing, do not invent a subtask to own it - list its exact "
    "existing path in that artifact's own planned_files[].preserved_references instead, to "
    "declare the reference is intentional and the file stays unchanged. "
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


def _is_strict_regression(
    retained_reason_codes: Optional[frozenset], candidate_reason_codes: frozenset,
) -> bool:
    """Planner-convergence audit (2026-09-06, P2 production-validation run
    7): reason-code sets across repair attempts form a PARTIAL order, not a
    total one - {D} -> {X} is incomparable (a different problem, possibly a
    legitimate trade-off), never itself evidence of regression, while
    {D} -> {A, D} is unambiguous: every previously-unresolved problem in
    the retained baseline is STILL unresolved, and a problem already
    cleared as of the retained baseline has reappeared. Only that second,
    strict-superset shape is safe to reject deterministically - anything
    else (unchanged, empty/success, or a genuinely different set) must
    fall through to the existing repair-loop behavior unchanged. No
    cardinality/heuristic scoring: `retained_reason_codes < candidate_
    reason_codes` is Python's own frozenset "proper subset" operator, not
    a size comparison (a smaller candidate set could still fail this check
    if it isn't a superset at all, and a same-size candidate never can).

    Found live: P2 run 7's attempt 1 correctly resolved 4 of 5 semantic
    reason codes, leaving only MISSING_GROUNDED_PRODUCTION_ARTIFACT -
    attempt 2, chasing that one remaining code, silently reintroduced
    APPLICATION_RUNTIME_OWNER_MISSING (already-cleared) while STILL not
    resolving MISSING_GROUNDED_PRODUCTION_ARTIFACT. must_preserve (prompt
    text only, no structural enforcement) did not stop this - this
    function backs a REAL, deterministic rejection instead."""
    if retained_reason_codes is None:
        return False
    return retained_reason_codes < candidate_reason_codes


def _preserved_reference_must_preserve_lines(obligation_ledger: ObligationLedger) -> List[str]:
    """MUST-PRESERVE reinforcement lines for currently-SATISFIED
    PRESERVED_REFERENCE obligations (PRV-11 preservation extension,
    2026-09-07, Production Validation P5) - see build_structured_plan_
    repair_prompt's own docstring for the live incident this closes.
    Each line self-attributes its own (source, target) pair explicitly, so
    one source's already-validated preservation can never be misread as
    applying to a different source - no grouping/scoping structure needed
    beyond that. Surfaces validated ObligationLedger state only, never the
    previous draft's raw JSON directly - the same discipline
    PLAN_STRUCTURAL_VALIDITY's own must_preserve block already follows."""
    return [
        f"{rec.evidence.get('source')} must keep declaring "
        f"{rec.evidence.get('target')} in its own "
        "planned_files[].preserved_references (already validated - a real grounded "
        "edge with no legitimate owner; do not remove this entry while fixing other "
        "reported issues, and do not replace it with a different target)"
        for rec in obligation_ledger.relevant_for_preservation(ObligationKind.PRESERVED_REFERENCE)
        if rec.evidence.get("source") and rec.evidence.get("target")
    ]


def _semantic_contract_must_preserve_lines(obligation_ledger: ObligationLedger) -> List[str]:
    """MUST-PRESERVE reinforcement (layer 1) for currently-SATISFIED
    SUBTASK_SEMANTIC_CONTRACT obligations (PRV-17, 2026-09-07, Production
    Validation P7) - see that kind's own docstring (obligations.py) for the
    live 3-attempt oscillation this closes, and _semantic_contract_
    regression_subtasks below for the deterministic layer-2 rejection that
    backs this prompt-level reinforcement the same way _is_strict_
    regression already backs must_preserve for reason-code sets. Each line
    self-attributes its own subtask id and requirement/capability string, so
    one subtask's already-validated contract can never be misread as
    applying to a different one - same discipline as
    _preserved_reference_must_preserve_lines above."""
    lines: List[str] = []
    for rec in obligation_ledger.relevant_for_preservation(ObligationKind.SUBTASK_SEMANTIC_CONTRACT):
        relation = rec.evidence.get("relation")
        subtask_id = rec.evidence.get("subtask_id")
        if relation == "requires":
            requirement = rec.evidence.get("requirement")
            if subtask_id and requirement:
                lines.append(
                    f"subtask {subtask_id!r} must keep declaring {requirement!r} in its own "
                    "requires (already validated - a real provider exists with a correct "
                    "depends_on edge; do not drop this entry while fixing other reported issues)"
                )
        elif relation == "provides":
            capability = rec.evidence.get("capability")
            if subtask_id and capability:
                lines.append(
                    f"subtask {subtask_id!r} must keep declaring {capability!r} in its own "
                    "provides (already validated as its sole provider; do not drop this entry "
                    "while fixing other reported issues)"
                )
    return lines


_SUBTASK_MENTION_RE = re.compile(r"subtask\(?s?\)?\s*\[?\s*((?:'[^']+'(?:,\s*)?)+)")


_QUOTED_TOKEN_RE = re.compile(r"'([^']+)'")


_REQUIRES_PROVIDES_ERROR_KEYWORDS = ("requires", "provide", "capabilit")


def _subtask_ids_mentioned(texts: List[str]) -> Set[str]:
    """Subtask ids named in a round's own validation error text, RESTRICTED
    to lines that are themselves about a requires/provides/capability
    relationship (PRV-17, 2026-09-07, P7) - used only to decide which
    subtask(s) a repair round was actually asked to fix a semantic-contract
    problem for, feeding _semantic_contract_regression_subtasks' own
    "unless directly implicated" exemption below. The keyword filter matters:
    P7's own attempt 0 named s3 in its ONE reported error
    (PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP, about s3's
    preserved_references, containing neither "requires" nor "provide" nor
    "capabilit") - a bare subtask-id mention, unfiltered, would have wrongly
    exempted s3 from the very requires-drop this mechanism exists to catch.
    Every requires/provides/capability-related error string in
    plan_validation.py and this module (SUBTASK_REQUIREMENT_UNPROVIDED,
    SEMANTIC_DEPENDENCY_EDGE_MISSING, AMBIGUOUS_SUBTASK_CAPABILITY_PROVIDER,
    SUBTASK_SEMANTIC_CONTRACT_MISSING, the MISSING_GROUNDED_PRODUCTION_
    ARTIFACT/MISWIRED_GROUNDED_DEPENDENCY_EDGE/GROUNDED_SEMANTIC_PROVIDER_
    MISMATCH family, PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED, and this
    module's own SEMANTIC_CONTRACT_REGRESSION_REJECTED) already contains at
    least one of these keywords AND at least one subtask id named either as
    `subtask 'sN'` or `subtask(s) ['sN', ...]` by construction (the grounded-
    edge family's own errors were extended, 2026-09-07, to also name the
    CONSUMER subtask via `gap['consumer_subtask']`, not only the producer
    that already appeared as `owning_subtask` - the consumer's own requires
    is exactly what a targeted repair for these codes legitimately changes).
    Deliberately reads the SAME free-text errors already shown to the
    Planner in the repair prompt, never a separately re-derived or reworded
    source."""
    ids: Set[str] = set()
    for text in texts:
        lowered = text.lower()
        if not any(keyword in lowered for keyword in _REQUIRES_PROVIDES_ERROR_KEYWORDS):
            continue
        for mention in _SUBTASK_MENTION_RE.finditer(text):
            ids.update(_QUOTED_TOKEN_RE.findall(mention.group(1)))
    return ids


def _semantic_contract_regression_subtasks(
    obligation_ledger: ObligationLedger, regressions_before: int, implicated_subtask_ids: Set[str],
) -> List[str]:
    """Deterministic layer 2 (PRV-17, 2026-09-07, P7): returns the subtask
    id(s) whose previously-SATISFIED SUBTASK_SEMANTIC_CONTRACT obligation
    just regressed to VIOLATED THIS round (i.e. was newly appended to
    obligation_ledger.regressions since `regressions_before`), and which
    were NOT named by the errors that prompted this round's own repair
    prompt (`implicated_subtask_ids`). A subtask actively being repaired for
    an unrelated reason may still legitimately touch its own requires/
    provides as a side effect - the exemption is scoped to the SUBTASK, not
    the exact field, matching the granularity `implicated_subtask_ids`
    itself can support from free text (see _subtask_ids_mentioned). Reuses
    ObligationLedger.record()'s existing SATISFIED->VIOLATED regression
    detection rather than a second, parallel comparison - see obligations.py
    SUBTASK_SEMANTIC_CONTRACT's own docstring. Called from
    _run_structured_enforce; the caller ORs a non-empty result into the
    same retained-baseline redirect _is_strict_regression already drives,
    rather than this function rejecting anything itself - _is_strict_
    regression's own reason-code-set semantics stay untouched."""
    offending: List[str] = []
    for event in obligation_ledger.regressions[regressions_before:]:
        if event.kind != ObligationKind.SUBTASK_SEMANTIC_CONTRACT:
            continue
        subtask_id = event.current.evidence.get("subtask_id") or event.current.owner_subtask_id
        if subtask_id and subtask_id not in implicated_subtask_ids:
            offending.append(subtask_id)
    return list(dict.fromkeys(offending))


_PRESERVED_REFERENCE_CONFLICT_RE = re.compile(
    r"artifact '([^']+)' declares '([^']+)' as a preserved reference"
)


def _preserved_reference_pairs_mentioned(texts: List[str]) -> Set[Tuple[str, str]]:
    """(source, target) pairs named by THIS round's own
    PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP error text (plan_validation.
    py's own wording: "artifact '<source>' declares '<target>' as a
    preserved reference, but '<target>' is itself planned for modification
    by [...]") - used only to decide which preservation pair(s) a round was
    actually asked to correct, feeding _preserved_reference_regressions'
    own "unless directly implicated" exemption below (PRV-17, 2026-09-08, P5
    audit, mirrors _subtask_ids_mentioned's exact rationale for the sibling
    requires/provides mechanism: a round legitimately asked to REMOVE a
    wrong preservation claim (this exact conflict) must not have that
    correction itself flagged as a silent, unrelated drop)."""
    pairs: Set[Tuple[str, str]] = set()
    for text in texts:
        for match in _PRESERVED_REFERENCE_CONFLICT_RE.finditer(text):
            pairs.add((match.group(1), match.group(2)))
    return pairs


def _preserved_reference_regressions(
    obligation_ledger: ObligationLedger, regressions_before: int,
    implicated_pairs: Set[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Deterministic sibling to _semantic_contract_regression_subtasks
    (PRV-17, 2026-09-08, P5 audit): returns the (source, target) pair(s)
    whose previously-SATISFIED PRESERVED_REFERENCE obligation just
    regressed to VIOLATED THIS round (newly appended to obligation_ledger.
    regressions since `regressions_before` by find_missing_grounded_
    production_artifacts' own closing pass), and which were NOT named by
    the errors that prompted this round's own repair prompt
    (`implicated_pairs`).

    A separate function, not a broadened SUBTASK_SEMANTIC_CONTRACT filter,
    deliberately - PRESERVED_REFERENCE's real identity is the (source,
    target) FILE pair (an edge-level fact, matching preserved_references'
    own edge-level design - see that field's own docstring), not a subtask
    id; forcing it through subtask identity would be wrong when a single
    subtask's PlannedFile legitimately drops one preserved target while
    correctly retaining another on the SAME file. Reuses ObligationLedger.
    record()'s existing SATISFIED->VIOLATED regression detection rather
    than a second, parallel comparison - see ObligationKind.
    PRESERVED_REFERENCE's own docstring. Called from _run_structured_
    enforce; the caller ORs a non-empty result into the same retained-
    baseline redirect _is_strict_regression and
    _semantic_contract_regression_subtasks already drive, rather than this
    function rejecting anything itself - _is_strict_regression's own
    reason-code-set semantics stay untouched.

    Live incident this closes (P5, PetTests.java): attempt 1 satisfies
    BaseEntity.java, leaving Visit.java missing (reason_codes=
    {MISSING_GROUNDED_PRODUCTION_ARTIFACT}); attempt 2 satisfies
    Visit.java but silently drops BaseEntity.java, again leaving exactly
    one file missing (reason_codes={MISSING_GROUNDED_PRODUCTION_ARTIFACT} -
    an IDENTICAL single-element set). _is_strict_regression's reason-code-
    set comparison requires a proper subset relationship; two equal sets
    are never a proper subset of each other, so it cannot see this
    oscillation - exactly the class of gap _semantic_contract_regression_
    subtasks already closed for requires/provides, left open here until
    now."""
    offending: List[Tuple[str, str]] = []
    for event in obligation_ledger.regressions[regressions_before:]:
        if event.kind != ObligationKind.PRESERVED_REFERENCE:
            continue
        pair = (event.current.evidence.get("source"), event.current.evidence.get("target"))
        if pair[0] and pair[1] and pair not in implicated_pairs:
            offending.append(pair)
    return list(dict.fromkeys(offending))


def build_authoritative_planner_request(
    goal: str,
    *,
    route_kind: Optional[ChangeKind] = None,
    extension_candidates: Optional[List[str]] = None,
    repository_candidates: Optional[List[str]] = None,
    structural_evidence: str = "",
) -> str:
    """Add enforce-only protocol constraints without changing the product goal.

    structural_evidence (PRV-11, 2026-08-30 - see build_planning_structural_
    evidence()'s own docstring): a compact block of REAL, grounded import/
    call relationships among the bounded candidate set - never raw source
    content, never a mandate. Empty string (the default) reproduces the
    exact prior prompt, unchanged, for every caller that doesn't supply it."""
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
        "- " + GLOBAL_INVARIANT_PROJECTION_RULE + "\n"
        "- Each requires string must exactly match one provides string from exactly one upstream "
        "subtask, and that provider must appear in depends_on.\n"
        "- Every verification entry must be an object with type, description, verifier_kind, and "
        "requires_runtime_execution. Use type=tool with tool_name=compile/verifier_kind=compile for compilation and "
        "tool_name=test/verifier_kind=test for tests. Use type=judgment without tool_name only for a genuinely "
        "non-deterministic semantic check. "
        "Never emit verification strings.\n"
        "- Assign an acceptance_criteria id only to a subtask that can directly demonstrate it. "
        "An application-output or round-trip criterion belongs on the runnable entrypoint stage, "
        "not an upstream library/configuration stage.\n"
        "- Build/config stages may use compile or test verification. Any original-request "
        "requirement for observable application behavior must also be verified by an entrypoint-owning "
        "stage that actually runs the application, observes the required result, and confirms clean exit; "
        "set verifier_kind=application_runtime and requires_runtime_execution=true only on that "
        "explicit application verifier, and false on build-only checks. Successful test execution "
        "satisfies a test verifier and must not synthesize an application-runtime requirement. Do not use "
        "verifier_kind=judgment for behavior that can only be established by executing the application - "
        "output, exit behavior, processing sample input, runtime side effects, etc.; a judgment-only "
        "requirement with no tool_name and requires_runtime_execution=false has no way to ever be "
        "confirmed and will be rejected.\n"
        "- Do not copy these protocol rules into global_invariants; derive those only from the "
        "original product request above. Do not invent unspecified implementation choices.\n"
        "- A stage whose tests need build/test tooling (a manifest, a test framework dependency) "
        "owned by another stage's planned_files must declare that stage's provides value in its "
        "own requires and depends_on, and in the artifact's requires_capabilities - do not rely "
        "on planned_files or list position to imply "
        "execution order.\n"
        "- Put ambient runtimes and tools such as java, maven, python, node, and pytest in "
        "planned_files[].environment_requirements, never in subtask requires/provides. "
        "planned_files[].requires_capabilities names only exact capabilities supplied by another "
        "planned subtask. "
        + ENVIRONMENT_DEPENDENCY_RULE + "\n"
        "- If a stage's entrypoint may terminate the process and another stage's tests are "
        "expected to exercise it directly, keep the process-terminating call separate from the "
        "directly-tested logic (a thin wrapper performs termination, tests target the underlying "
        "logic that returns a result instead) - applies to any process-termination mechanism, no "
        "specific method name or file structure required.\n"
        + route_guidance
        + (
            "- Grounded structural evidence (real import/call relationships already discovered "
            "among the repository candidates above - not a mandate, but every planned_files "
            "selection and every requires/provides/depends_on edge must be consistent with it "
            "where it names a relationship relevant to this change):\n"
            f"{structural_evidence}\n"
            if structural_evidence else ""
        )
    )


_JAVA_CONSTRUCTOR_INSTANTIATION_RE = re.compile(r"\bnew\s+(\w+)\s*\(")


# A Java type mention: a capitalized identifier in code (comments and string
# literals blanked first) - the evidence a bare cross-file call needs.
_JAVA_TYPE_MENTION_RE = re.compile(r"\b([A-Z]\w*)\b")


def build_planning_structural_evidence(
    workspace_path: str, candidate_paths: List[str],
) -> Tuple[str, Dict[str, List[str]]]:
    """Bounded, grounded structural evidence for authoritative planning
    (PRV-11, 2026-08-30) - the missing-wiring half of a live incident where
    the Planner produced a plan connecting Customer -> CustomerService ->
    CustomerControllerTest directly, omitting CustomerController.java (the
    real intermediate consumer/producer) entirely: nothing available to
    planning ever told it CustomerController.java existed in a load-bearing
    relationship to the goal, only that a file by that name existed
    somewhere among a hundred bare candidate paths (_authoritative_planner_
    extension_candidates() is deliberately content-free).

    Builds a PURELY EPHEMERAL, in-memory DependencyGraph (kriya/analyzer/
    graph.py) scoped to ONLY the bounded candidate_paths already ranked for
    this goal - never the persisted dependency_graph.db `kriya analyze`
    owns (that command's own contract, and MA-era's "opt-in, explicit"
    indexing convention, are both left completely untouched), never a
    whole-repository walk. Derives two kinds of real, already-implemented
    relations from kriya/analyzer/graph.py's own existing parser - never a
    new graph, never raw source content:

    1. imports: exact, regex-parsed `import` statements, resolved to an
       owning candidate file via get_class_symbol_locations() (the same
       simple-name index the duplicate-type Quality Gate already trusts).
    2. calls: bare method-name invocations, resolved to an owning candidate
       file ONLY when that method name is uniquely declared by exactly one
       OTHER candidate in this bounded set and NOT by the calling file
       itself (a self-declared callee resolves to the caller: no edge), and
       only when the caller's code names a type the callee's file declares -
       deliberately conservative (ambiguous/duplicate method names resolve
       to nothing; a chained call on a factory result or a var-typed local
       that never names the type yields no edge) to avoid a false
       relationship in a small-candidate-set name collision. Needed
       because Java same-package references (the common brownfield shape -
       Customer/CustomerService/CustomerController/CustomerControllerTest
       all in one package) never produce an `import` statement at all;
       imports alone would see nothing between them.

    Returns (evidence_text, resolved_edges) - resolved_edges maps each
    candidate file to the OTHER candidate files it references, the single
    source of truth BOTH the Planner-prompt text above AND find_missing_
    grounded_production_artifacts() below are built from, so a prompt hint
    and a validation error can never disagree about what the graph says."""
    resolved: Dict[str, List[str]] = {}
    graph = DependencyGraph(":memory:")
    try:
        indexed_paths: List[str] = []
        contents: Dict[str, str] = {}
        for rel_path in candidate_paths:
            full_path = os.path.join(workspace_path, rel_path)
            if not os.path.isfile(full_path):
                continue
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                mtime = os.path.getmtime(full_path)
            except OSError:
                continue
            graph.index_file(rel_path, content, mtime)
            indexed_paths.append(rel_path)
            contents[rel_path] = content

        class_locations = graph.get_class_symbol_locations()
        method_owners: Dict[str, List[str]] = {}
        for rel_path in indexed_paths:
            for symbol_name in graph.get_symbols_for_file(rel_path):
                method_owners.setdefault(symbol_name, [])
                if rel_path not in method_owners[symbol_name]:
                    method_owners[symbol_name].append(rel_path)
        # STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001: the simple type names each
        # Java candidate declares (the same class-name index imports use),
        # and each Java candidate's code with comments and string literals
        # blanked - the text a structural claim about it may be read from.
        declared_types: Dict[str, set] = {}
        for key, declaring_files in class_locations.items():
            _ext, _, simple_name = key.partition(":")
            for declaring in declaring_files:
                declared_types.setdefault(declaring, set()).add(simple_name)
        code_text = {
            rel_path: (strip_java_comments_and_strings(contents[rel_path])
                       if rel_path.lower().endswith(".java") else contents[rel_path])
            for rel_path in indexed_paths
        }

        for rel_path in indexed_paths:
            ext = os.path.splitext(rel_path)[1].lower()
            targets: List[str] = []
            mentioned_types = (set(_JAVA_TYPE_MENTION_RE.findall(code_text[rel_path]))
                               if ext == ".java" else None)
            for imp in graph.get_imports(rel_path):
                simple = imp.rsplit(".", 1)[-1]
                if simple == "*":
                    continue
                for owner in class_locations.get(f"{ext}:{simple}", []):
                    if owner != rel_path and owner not in targets:
                        targets.append(owner)
            for callee in graph.get_callees(rel_path):
                owners = method_owners.get(callee["target"], [])
                # STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001 (BACKEND-FINAL-CLOSURE-005
                # cohort 2, C2-S3_A): a callee this file itself declares
                # resolves to this file - a class's calls to its own methods
                # are never an edge to another candidate. Before this check
                # the lookup excluded the calling file and kept "the unique
                # OTHER owner", so two same-package classes declaring the
                # same method names (a class and its deprecated twin) each
                # referenced the other through their own self-calls: a false
                # mutual edge no plan could order (MISWIRED cycle, planner
                # exhausted). Symbol evidence that is ambiguous (several
                # other declarers) still resolves to nothing, as before.
                if rel_path in owners or len(owners) != 1 or owners[0] in targets:
                    continue
                # A bare name alone is weak evidence (C2-S3_A, same incident:
                # CharBuffer.wrap(...) - a JDK static call - resolved to a
                # candidate declaring its own wrap()). A Java caller references
                # another candidate only when its code (comments and strings
                # blanked) names a type that candidate declares.
                if mentioned_types is not None and not (declared_types.get(owners[0], set()) & mentioned_types):
                    continue
                targets.append(owners[0])
            # Constructor instantiation ("new ClassName(") - deliberately a
            # SEPARATE, narrow regex scan here, not a _parse_java()/"calls"
            # relation (that regex requires a preceding "." and never
            # matches "new X(" at all) and NOT resolved through method_
            # owners (a class's own declared-method symbols can silently
            # fail to extract at all when a return type has a multi-param
            # generic containing a comma, e.g. "Map<String,Object>" -
            # _parse_java()'s own JAVA_METHOD_SIGNATURE_CORE regex doesn't
            # match that shape - found live tracing the exact PRV-11
            # fixture's own CustomerController.details() signature; not
            # touched here, a shared parser used elsewhere, out of this
            # fix's scope). Resolved through class_locations instead - the
            # SAME reliable class-name index imports already use, since
            # class declarations don't have this gap. Close to universal in
            # Java test conventions ("ClassUnderTest x = new ClassUnderTest()")
            # and far more robust for "this test exercises that class" than
            # depending on a single uniquely-named method resolving cleanly.
            # Scanned over the comment- and string-blanked code
            # (STRUCTURAL-EVIDENCE-SELF-CALL-EDGE-001): a Javadoc example
            # such as "StrBuilder b = new StrBuilder();" is not an instantiation.
            for class_name in _JAVA_CONSTRUCTOR_INSTANTIATION_RE.findall(code_text.get(rel_path, "")):
                for owner in class_locations.get(f"{ext}:{class_name}", []):
                    if owner != rel_path and owner not in targets:
                        targets.append(owner)
            if targets:
                resolved[rel_path] = sorted(targets)
    finally:
        graph.close()

    lines = [
        f"{source} references -> {target}"
        for source, targets in sorted(resolved.items())
        for target in targets
    ]
    return "\n".join(lines), resolved


def _transitive_depends_on(plan: EngineeringPlan, subtask_id: str) -> set:
    """Every OTHER subtask id `subtask_id` depends on, directly or
    transitively (own id never included) - the same backward BFS-over-
    depends_on shape resolve_scope_conflict_owners() above already uses,
    reused here rather than re-derived."""
    upstream: set = set()
    start = plan.subtask_by_id(subtask_id)
    frontier = list(start.depends_on) if start is not None else []
    while frontier:
        dep_id = frontier.pop()
        if dep_id in upstream:
            continue
        upstream.add(dep_id)
        dep = plan.subtask_by_id(dep_id)
        if dep is not None:
            frontier.extend(dep.depends_on)
    return upstream


def find_missing_grounded_production_artifacts(
    plan: EngineeringPlan, resolved_edges: Dict[str, List[str]],
    *,
    workspace_path: Optional[str] = None,
    obligation_ledger: Optional[ObligationLedger] = None,
    revision: object = None,
) -> List[Dict[str, str]]:
    """Bounded plan-completeness check (PRV-11, 2026-08-30/31): a REAL,
    grounded structural edge (see build_planning_structural_evidence's own
    docstring - an import, or a uniquely-resolved method call) from a
    goal-relevant TEST file to a PRODUCTION file is exactly the defect this
    session traced FOUR separate live times, in two distinct shapes:

    1. UNOWNED (reason="unowned"): the referenced production file isn't
       owned by ANY subtask - the Planner correctly plans the producer and
       the test but omits the intermediate consumer that actually connects
       them (or invents an unrelated substitute test file instead of the
       real, pre-existing one - scanning EVERY candidate test file this
       function was given evidence for, not only currently-planned ones,
       catches that shape too). Scoped to a runnable-test SOURCE only
       (2026-09-04, Production Validation P1): build_planning_structural_
       evidence only ever indexes candidates that already exist on disk, so
       every target this function sees - real gap or not - already exists
       in the workspace. Filesystem existence therefore can't distinguish
       a genuine omitted owner from a production file's ordinary, correct
       reference to an untouched brownfield dependency (found live: a
       clean plan modifying EmployeeRepository.java was rejected for its
       own unremarkable, unchanged import of IgniteConfig.java). What
       distinguishes them is the SOURCE: a test's reference to an unowned
       production file is exactly the PRV-11 omitted-producer shape; a
       production file's reference to an unowned production file is an
       ordinary dependency this plan was never asked to touch.
    2. MISWIRED (reason="not_in_dependency_chain", 2026-08-31): the
       referenced production file IS owned by a real subtask, but that
       subtask is not in the REFERENCING subtask's own transitive
       depends_on closure - the Planner correctly creates a subtask for
       every file (unlike shape 1) but wires the test's own requires/
       depends_on to skip past the real intermediate producer and point
       directly at an earlier one instead (found live: a plan correctly
       created s3=CustomerController.java, but s4=CustomerControllerTest.
       java's own requires/depends_on pointed at s2=CustomerService.java,
       never at s3 - the exact edge FUTURE_ORDERED handling and MA9
       recovery then both, correctly, treat as authoritative).
    3. SEMANTICALLY MISWIRED (reason="semantic_provider_mismatch"): the
       production owner appears in the test's dependency chain, but none of
       that owner's provides capabilities appears in the test's requires.
       This is invalid because FUTURE_OWNER_VERIFICATION resolves the
       responsible owner through requires -> provides, not depends_on.

    Deliberately narrow in both shapes: only fires on a REAL, already-
    indexed structural edge - never "this file is probably related," never
    a subtask-per-touched-file rule, and shape 2 never second-guesses an
    edge that IS already correctly wired (the referencing subtask's own
    planned_files, or any subtask already in its own depends_on closure,
    are never flagged for owning the SAME grounded target). A target
    itself test/documentation is never flagged.

    4. PRESERVE (accepted, not a gap - 2026-09-06, Production Validation
       P2): the referencing source's OWN PlannedFile lists the target in
       preserved_references. Requires no further grounding check here -
       `target` is only ever reached by iterating resolved_edges[source]
       in the first place, so it is already a real structural edge (never
       an invented one: an entry naming a target with no real edge from
       that source is simply never consulted) and build_planning_
       structural_evidence only ever indexes candidates that already
       exist on disk (see its own docstring), so `target` already exists.
       Declaration is per-source (this function keys the check off
       `source`'s own PlannedFile, never a plan-wide path list), so one
       artifact's correct preservation claim never suppresses a genuine
       gap on a DIFFERENT source referencing the same target. A target
       that is ALSO planned for modification elsewhere is a plan-
       authoring contradiction rejected by plan_validation.validate_plan
       (PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP), not silently
       resolved here - this function only ever sees `target not in
       owned_paths`, so an owned target never reaches this branch at all.
       When workspace_path/obligation_ledger are supplied, an accepted
       preservation is also recorded as a SATISFIED ObligationKind.
       PRESERVED_REFERENCE record with the target's real pre-generation
       content hash as evidence - see that kind's own docstring (kriya/
       workflow/obligations.py) for how the terminal sweep uses it to
       enforce byte identity with no new gate."""
    owned_paths = {pf.path for st in plan.subtasks for pf in st.planned_files}
    owner_by_path = {pf.path: st.id for st in plan.subtasks for pf in st.planned_files}
    source_subtask_by_path = {pf.path: st for st in plan.subtasks for pf in st.planned_files}
    planned_file_by_path = {pf.path: pf for st in plan.subtasks for pf in st.planned_files}
    upstream_cache: Dict[str, set] = {}
    gaps: List[Dict[str, str]] = []
    seen = set()
    for source, targets in resolved_edges.items():
        # Grounded import/reference evidence is an execution prerequisite for
        # any planned code consumer, not only a test.  PRV-17 exposed the
        # production form: urls.py imported a view owned by a later stage and
        # was nevertheless required to compile first.
        if source not in source_subtask_by_path:
            continue
        for target in targets:
            if is_runnable_test_file(target) or classify_file_role(target) in (
                FileRole.TEST, FileRole.DOCUMENTATION,
            ):
                continue
            key = (source, target)
            if key in seen:
                continue
            if target not in owned_paths:
                if not is_runnable_test_file(source):
                    continue
                source_pf = planned_file_by_path.get(source)
                if source_pf is not None and target in source_pf.preserved_references:
                    if obligation_ledger is not None and workspace_path is not None:
                        obligation_ledger.record(ObligationRecord(
                            id=f"plan.preserved_reference.{source}->{target}",
                            kind=ObligationKind.PRESERVED_REFERENCE,
                            status=ObligationStatus.SATISFIED,
                            authority=ObligationAuthority.DETERMINISTIC,
                            description=(
                                f"{source} references {target} without requiring it modified - "
                                "target must remain byte-identical to its pre-generation content"
                            ),
                            source="workflow_controller.find_missing_grounded_production_artifacts",
                            revision=revision,
                            evidence={
                                "source": source, "target": target,
                                "baseline_hash": read_file_revision(
                                    os.path.join(workspace_path, target)
                                ),
                            },
                            terminal_required=True,
                        ))
                    continue
                seen.add(key)
                gaps.append({
                    "test_file": source, "missing_production_artifact": target,
                    "consumer_subtask": source_subtask_by_path[source].id,
                    "reason": "unowned",
                })
                continue
            target_subtask_id = owner_by_path[target]
            source_subtask = source_subtask_by_path.get(source)
            if source_subtask is None or source_subtask.id == target_subtask_id:
                continue
            if source_subtask.id not in upstream_cache:
                upstream_cache[source_subtask.id] = _transitive_depends_on(plan, source_subtask.id)
            if target_subtask_id not in upstream_cache[source_subtask.id]:
                seen.add(key)
                gaps.append({
                    "test_file": source, "missing_production_artifact": target,
                    "owning_subtask": target_subtask_id,
                    "consumer_subtask": source_subtask.id,
                    "reason": "not_in_dependency_chain",
                })
                continue
            if not (
                is_runnable_test_file(source)
                or classify_file_role(source) is FileRole.TEST
            ):
                # For an ordinary production import, grounded provider-before-
                # consumer ordering is the required intermediate-workspace
                # invariant. Capability-name matching remains the stronger
                # verification/FUTURE_OWNER rule for tests only.
                continue
            target_subtask = plan.subtask_by_id(target_subtask_id)
            owner_capabilities = set(target_subtask.provides if target_subtask else [])
            if not owner_capabilities.intersection(source_subtask.requires):
                seen.add(key)
                gaps.append({
                    "test_file": source,
                    "missing_production_artifact": target,
                    "owning_subtask": target_subtask_id,
                    "consumer_subtask": source_subtask.id,
                    "owner_provides": sorted(owner_capabilities),
                    "test_requires": sorted(source_subtask.requires),
                    "reason": "semantic_provider_mismatch",
                })
    if obligation_ledger is not None:
        # PRV-17 (2026-09-08, Production Validation P5 audit): mirrors
        # plan_validation.py's own SUBTASK_SEMANTIC_CONTRACT closing pass
        # exactly (same rationale, same mechanism) - the SATISFIED-recording
        # branch above only ever (re-)records an id for a (source, target)
        # pair that is STILL a real edge with a STILL-declared
        # preserved_references entry this round. A pair silently dropped
        # from a later draft (the source no longer declares that target
        # preserved - whether because the edge disappeared or the
        # declaration was simply removed) never reaches that branch again,
        # so its previously-SATISFIED record would otherwise sit stale,
        # hiding the exact live incident this closes: P5's PetTests.java
        # needed BaseEntity.java and Visit.java preserved simultaneously;
        # one attempt satisfied only the first, the next satisfied only the
        # second while silently dropping the first - both attempts reported
        # the IDENTICAL single-element reason-code set
        # {MISSING_GROUNDED_PRODUCTION_ARTIFACT}, so _is_strict_regression's
        # reason-code-set comparison could not see it (equal sets are never
        # a proper subset of each other). This closing pass re-records
        # VIOLATED against each dropped id, which is what makes
        # ObligationLedger.record()'s own SATISFIED->VIOLATED regression
        # detection fire - no new detection mechanism, only ensuring every
        # previously-tracked id gets a fresh record every round, exactly
        # like the requires/provides closing pass already does.
        current_preserved_pairs = {
            (source, target)
            for source, targets in resolved_edges.items()
            for target in targets
            if (planned_file_by_path.get(source) is not None
                and target in planned_file_by_path[source].preserved_references)
        }
        for oid in obligation_ledger.ids_by_kind(ObligationKind.PRESERVED_REFERENCE):
            rec = obligation_ledger.current(oid)
            if rec is None or rec.status != ObligationStatus.SATISFIED:
                continue
            pair = (rec.evidence.get("source"), rec.evidence.get("target"))
            if pair in current_preserved_pairs:
                continue
            obligation_ledger.record(ObligationRecord(
                id=oid, kind=ObligationKind.PRESERVED_REFERENCE,
                status=ObligationStatus.VIOLATED,
                authority=ObligationAuthority.DETERMINISTIC,
                description=(
                    f"{pair[0]!r} previously validated preservation of {pair[1]!r} is no "
                    "longer declared this round"
                ),
                source="workflow_controller.find_missing_grounded_production_artifacts",
                revision=revision,
                evidence={**rec.evidence, "dropped_between_revisions": True},
                # terminal_required=False (unlike the live per-round
                # recording above): the source/target pair may have been
                # legitimately retired (e.g. the Planner correctly removed a
                # WRONG preservation claim after a
                # PRESERVED_REFERENCE_CONFLICTS_WITH_OWNERSHIP correction -
                # see _preserved_reference_regressions()'s own implicated-
                # pair exemption for how a round that explicitly corrects
                # this exact pair is never treated as a silent drop in the
                # first place), in which case nothing will ever re-satisfy
                # THIS exact id again - the unconditional final-gate check
                # this drives (unresolved_terminal_obligations()) must not
                # permanently fail a run over an obligation no longer live.
                # record()'s SATISFIED->VIOLATED regression detection (this
                # record's actual purpose) fires regardless of
                # terminal_required - only the final aggregation gate reads
                # that flag.
                terminal_required=False,
            ))
    return gaps


def build_approved_plan_document(
    deps: PlanningDeps,
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
        "stage_order": deps.topological_subtask_order(plan),
        "stage_states": dict(stage_states),
        "plan": plan.model_dump(mode="json"),
    }
