"""Milestone-based goal decomposition orchestrator.

Decomposes a large goal into small, independently executable/verifiable
milestones (MilestonePlannerAgent, kriya/agents/agent.py), then drives the
EXISTING, unmodified kriya/workflow/workflow.py::run_generation_workflow()
once per milestone against the same growing workspace_path - see
docs/design.md's own manually-validated precedent for this exact pattern
(three sequential real `kriya generate` calls against one growing workspace,
which found and fixed the two worktree bugs that make this safe today:
kriya/workflow/worktree.py's _resolve_repo_head and
_sync_uncommitted_changes_into_worktree).

Zero changes to run_generation_workflow()'s pipeline logic were needed beyond
the additive milestone_group_id/index/total trace passthrough - each
milestone's own call applies its changes to workspace_path via a plain file
copy (never a git commit), and the next milestone's worktree is freshly
rebuilt from workspace_path's current on-disk state, so "does milestone N+1
see milestone N's real applied output" is already true by construction, with
no new plumbing.
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.agents.contracts import Milestone, MilestoneMode, MilestoneV2
from kriya.workflow.context_projection import (
    project_implementation_source,
    render_established_file_context,  # MA5.8 - moved to context_projection.py, re-exported here for
                                        # backward compatibility (tests/test_milestones.py's own import,
                                        # any other existing consumer of kriya.workflow.milestones.render_established_file_context).
)
from kriya.workflow.file_resolution import _resolve_run_command
from kriya.workflow.milestone_normalization import normalize_legacy_milestones
from kriya.workflow.milestone_validation import (
    MilestonePlanValidator,
    MilestoneValidationResult,
    plan_structure_telemetry,
    topological_order,
)
from kriya.workflow.repository_topology import RepositoryTopology, detect_repository_topology
from kriya.workflow.verification_contract import extract_contract_verdict
from kriya.workflow.workflow import _log_phase_banner

logger = logging.getLogger(__name__)

# Deliberately language-agnostic (project_implementation_source just bounds
# raw text by character count - no per-language parsing), not the signature-
# tier skeletonizer in context_budget.py: that one only has real extraction
# logic for Python and brace-languages (.java/.cpp/.c/.h/.cs) - anything else
# (Ruby, JS/TS, Go, HTML, ...) falls through to an unstructured "first 15
# lines" placeholder there, which for a class definition is often just
# imports, giving a later milestone's Architect/Developer nothing useful to
# ground on. A bounded head+tail of the REAL file, in whatever language it's
# actually written in, degrades gracefully for every stack Kriya supports
# (see PolymorphicValidator's own multi-language detection) instead of
# silently degrading to noise for all but two of them. Generous relative to
# retry_package.py's per-file budgets (which split a much smaller total
# across potentially many files): here each earlier-milestone file gets its
# own allowance, and milestone-decomposition's own premise (small vertical
# slices) keeps the file count and size low in practice.
_ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE = 4000



def _is_legacy_milestone_dict(raw: Dict[str, Any]) -> bool:
    """A v2-shaped dict always has "id" (required field); a v1-shaped dict
    never does. A saved plan file is never a mix of the two (Kriya itself
    only ever writes one shape per save), so checking the first entry is
    sufficient - used by MilestoneRunState.from_dict below to load BOTH an
    old (pre-MA3.7) saved plan/sidecar file and a new one."""
    return "id" not in raw


@dataclass
class MilestoneRunState:
    """Orchestrator-owned bookkeeping for one decomposed goal, persisted to a
    sidecar JSON file (see save_milestone_run_state/load_milestone_run_state)
    - deliberately NOT stored in traces.db (no room for a structured command
    list there, and this is orchestration bookkeeping, not a trace record)
    or kriya/workflow/state.py's GenerationState (single-call-scoped by
    design; see the architecture review that led to this module's Option A
    approach, which keeps that scoping unchanged).

    MA3.7: `milestones` is now Schema v2 (kriya/agents/contracts.py's
    MilestoneV2) and completion tracking is ID-based
    (`completed_milestone_ids`) rather than positional
    (`completed_milestone_indices`) - milestone identity is never list
    position, per the MA3 design doc's own invariant. from_dict() below
    still loads an OLD (pre-MA3.7) saved plan/sidecar file transparently,
    normalizing v1 milestones and migrating index-based progress fields to
    their v2 id-based equivalents on the fly (Rule 9: "Legacy milestone
    plans remain loadable")."""

    group_id: str
    original_goal: str
    milestones: List[MilestoneV2]
    completed_milestone_ids: List[str] = field(default_factory=list)
    established_dependencies: List[str] = field(default_factory=list)
    # milestone id -> RunVerifierAgent.judge()'s own run_commands shape
    # (List[List[str]]), persisted so the integration phase's replay step
    # (replay_prior_milestone_verifications) can re-execute an EARLIER
    # milestone's real verification without re-asking the model to re-infer
    # it, and so --resume can pick up mid-sequence.
    verification_commands: Dict[str, List[List[str]]] = field(default_factory=dict)
    # filepath -> bounded real content (project_implementation_source, head+
    # tail if it doesn't fit) of every file an EARLIER, already-completed
    # milestone wrote - grows monotonically as milestones complete (see
    # run_milestones()'s post-success capture step) and is folded into every
    # later milestone's run_generation_workflow() call via
    # supplementary_context, so Planner/Architect/Developer are grounded on
    # the REAL API of already-built dependencies instead of guessing one.
    established_file_context: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "original_goal": self.original_goal,
            "milestones": [m.model_dump() for m in self.milestones],
            "completed_milestone_ids": self.completed_milestone_ids,
            "established_dependencies": self.established_dependencies,
            "verification_commands": {str(k): v for k, v in self.verification_commands.items()},
            "established_file_context": self.established_file_context,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MilestoneRunState":
        raw_milestones = data["milestones"]
        is_legacy = bool(raw_milestones) and _is_legacy_milestone_dict(raw_milestones[0])
        milestones = (
            normalize_legacy_milestones([Milestone(**m) for m in raw_milestones])
            if is_legacy
            else [MilestoneV2(**m) for m in raw_milestones]
        )

        if "completed_milestone_ids" in data:
            completed_ids = list(data["completed_milestone_ids"])
        else:
            # Legacy sidecar (completed_milestone_indices, 1-based list
            # position) - canonical ids were assigned positionally by
            # normalize_legacy_milestones (index i -> "M{i}"), so this
            # migration is a direct, deterministic rename, not a guess.
            completed_ids = [f"M{i}" for i in data.get("completed_milestone_indices", [])]

        raw_verification_commands = data.get("verification_commands", {})
        verification_commands = (
            {f"M{int(k)}": v for k, v in raw_verification_commands.items()}
            if is_legacy
            else {str(k): v for k, v in raw_verification_commands.items()}
        )

        return cls(
            group_id=data["group_id"],
            original_goal=data["original_goal"],
            milestones=milestones,
            completed_milestone_ids=completed_ids,
            established_dependencies=list(data.get("established_dependencies", [])),
            verification_commands=verification_commands,
            established_file_context=dict(data.get("established_file_context", {})),
        )


def _sidecar_path(workspace_path: str, group_id: str) -> str:
    return os.path.join(workspace_path, ".kriya", "milestones", f"{group_id}.json")


def save_milestone_run_state(workspace_path: str, run_state: MilestoneRunState) -> None:
    path = _sidecar_path(workspace_path, run_state.group_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run_state.to_dict(), fh, indent=2)


def load_milestone_run_state(workspace_path: str, group_id: str) -> Optional[MilestoneRunState]:
    path = _sidecar_path(workspace_path, group_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return MilestoneRunState.from_dict(json.load(fh))


def load_or_resume_milestone_run_state(workspace_path: str, plan_data: Dict[str, Any]) -> MilestoneRunState:
    """Builds the MilestoneRunState `kriya generate --from-milestones <file>`
    actually executes: `milestones`/`original_goal` ALWAYS come from the
    freshly-loaded plan file, never a stale sidecar - `kriya plan-milestones`
    explicitly advertises "review and hand-edit" as the intended workflow, so
    an edit made to the plan file after a first partial run must take effect,
    not be silently discarded in favor of whatever was baked into the
    sidecar the first time. Progress fields (completed_milestone_ids/
    established_dependencies/verification_commands/established_file_context)
    DO come from an existing same-group_id sidecar when one exists, so
    re-running the same command still resumes rather than restarting the
    whole sequence from scratch."""
    fresh_state = MilestoneRunState.from_dict(plan_data)
    sidecar_state = load_milestone_run_state(workspace_path, plan_data["group_id"])
    if sidecar_state is not None:
        fresh_state.completed_milestone_ids = sidecar_state.completed_milestone_ids
        fresh_state.established_dependencies = sidecar_state.established_dependencies
        fresh_state.verification_commands = sidecar_state.verification_commands
        fresh_state.established_file_context = sidecar_state.established_file_context
    return fresh_state


def _list_workspace_files(workspace_path: str, max_files: int = 200) -> List[str]:
    """Cheap, dependency-free workspace snapshot for the Milestone Planner's
    prompt - deliberately NOT RepositoryAnalyzer's full repo_model (that's
    skills/Graph-RAG-oriented and considerably heavier, built for a stage
    that designs file CONTENTS; the Milestone Planner slices GOAL TEXT by
    behavior and doesn't need that depth). Just enough for the model to know
    whether it's planning against a blank workspace or one that already has
    an earlier milestone group's real output on disk."""
    ignored_dirs = {".git", ".kriya", "__pycache__", "node_modules", "target", ".venv", "venv"}
    files: List[str] = []
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for fn in filenames:
            files.append(os.path.relpath(os.path.join(root, fn), workspace_path))
            if len(files) >= max_files:
                return sorted(files)
    return sorted(files)


def render_repository_topology_summary(topology: RepositoryTopology) -> str:
    """MA3.5 evidence block for the Milestone Planner's prompt (see
    plan_milestones() below) - deterministic facts about the REAL, on-disk
    build topology, given as evidence rather than asked as a question ("is
    this a multi-module repository?") the model would otherwise have to
    guess at. This is advisory only: the AUTHORITATIVE gate is
    MilestonePlanValidator's own physical-topology-preservation check
    (kriya/workflow/milestone_validation.py, wired in MA3.6) - this summary
    exists so a well-behaved model avoids that rejection in the first place,
    not to replace it."""
    if topology.build_system is None and not topology.build_roots:
        return (
            "Repository topology: no existing build artifacts detected - this "
            "workspace is empty or new, so this plan establishes its own structure."
        )
    return "\n".join([
        "Repository topology (detected from the existing workspace, not guessed):",
        f"- Build system: {topology.build_system or 'unknown'}",
        f"- Build roots: {', '.join(topology.build_roots) or 'none'}",
        f"- Child modules: {', '.join(topology.modules) or 'none'}",
        f"- Existing entrypoints: {', '.join(topology.entrypoints) or 'none detected'}",
        f"- Multi-module project: {'yes' if topology.is_multi_module else 'no'}",
    ])


_MAX_MILESTONE_PLANNING_ATTEMPTS = 3


def _build_plan_correction_feedback(result: MilestoneValidationResult) -> str:
    """Section 21's "specific failure -> specific correction -> bounded
    retry" shape (the same retry philosophy Kriya's own Quality Gates loop
    already uses, one level up) rather than a generic "try again": each line
    names the real reason code and milestone id MilestonePlanValidator found,
    so a capable model has a concrete checklist of exactly what to fix, not
    just a vague rejection."""
    lines = [
        "PLAN_VALIDATION_ERROR",
        "",
        "Your previous milestone plan was rejected for the following reason(s):",
    ]
    for issue in result.errors:
        location = f" (milestone '{issue.milestone_id}')" if issue.milestone_id else ""
        lines.append(f"- [{issue.code}]{location}: {issue.message}")
    lines.append("")
    lines.append(
        "Required correction: revise your milestone list to fix ALL of the issues "
        "above, then resubmit. Return ONLY the corrected milestones JSON block in "
        "the exact same shape as before - do not add prose explaining the change."
    )
    return "\n".join(lines)


def _log_milestone_plan_telemetry(
    logs_path: Optional[str],
    group_id: str,
    status: str,
    milestones: List[MilestoneV2],
    validation_attempts: int,
    validation_failures: List[str],
    topology: RepositoryTopology,
) -> None:
    """MA3.9 - no-op when logs_path isn't supplied (kriya plan-milestones
    passes config.paths.logs; a caller that doesn't care about telemetry,
    e.g. most of this module's own tests, gets zero I/O). Lazy import + a
    fresh TraceLogger per call, same convention as every trace-logging call
    site in kriya/workflow/workflow.py."""
    if not logs_path:
        return
    from kriya.core.trace import TraceLogger

    structure = plan_structure_telemetry(milestones)
    trace_logger = TraceLogger(os.path.join(logs_path, "traces.db"))
    try:
        trace_logger.log_milestone_plan(
            group_id=group_id,
            status=status,
            schema_version=2,
            milestone_count=structure["milestone_count"],
            dependency_edges=structure["dependency_edges"],
            extension_count=structure["extension_count"],
            composition_count=structure["composition_count"],
            validation_attempts=validation_attempts,
            validation_failures=validation_failures,
            repository_topology={
                "build_system": topology.build_system,
                "module_count": len(topology.build_roots),
                "entrypoint_count": len(topology.entrypoints),
            },
        )
    finally:
        trace_logger.close()


async def plan_milestones(
    milestone_planner: Any,
    goal: str,
    workspace_path: str,
    stream_callback: Optional[Callable[[str], None]] = None,
    max_planning_attempts: int = _MAX_MILESTONE_PLANNING_ATTEMPTS,
    logs_path: Optional[str] = None,
) -> Tuple[Optional[MilestoneRunState], Optional[str]]:
    """Runs the Milestone Planner and returns a fresh, nothing-executed-yet
    MilestoneRunState, or (None, error). Caller (kriya plan-milestones)
    persists the result via save_milestone_run_state() so a human can
    review/hand-edit the proposed slicing before `kriya generate
    --from-milestones` ever executes a single real generate call against
    their workspace - see the design doc's own note that this is a genuine
    qualitative judgment call worth a human eyeballing, not just an assertion.

    MA3.6: each attempt's output is validated through MilestonePlanValidator
    (DAG/capability/extension/acceptance/physical-topology -
    kriya/workflow/milestone_validation.py) BEFORE being accepted - an
    invalid plan never reaches MilestoneRunState or a human's review. On
    rejection, a specific, reason-coded correction request
    (_build_plan_correction_feedback above) is appended to the prompt and the
    planner is asked again, bounded by max_planning_attempts - never an
    unbounded re-planning loop (the design doc's own explicit "Do not
    introduce a generic infinite re-planning loop" rule). A response that
    fails to produce any parseable milestone list at all (parse_milestone_list
    returning None - a genuinely different, pre-existing failure mode; see
    MilestonePlannerAgent.run_with_milestone_list's own docstring) is still an
    IMMEDIATE failure, not retried here - that degrade-on-malformed-output
    behavior is unchanged from before this milestone.

    MA3.7: MilestonePlannerAgent.run_with_milestone_list now returns Schema
    v2 (MilestoneV2) directly - either the model genuinely emitted v2 JSON,
    or it reverted to the old v1 shape and that agent's own fallback already
    normalized it (see that method's docstring). Either way, what arrives
    here is already MilestoneV2, so it's validated as-is - no normalization
    call on this live path any more. The MilestoneRunState this function
    returns carries the VALIDATOR's own normalized milestones (extends
    auto-merged into depends_on - see MilestonePlanValidator.validate()'s own
    `.milestones` result), not the raw pre-validation list, so
    run_milestones() sees the fully-correct dependency graph without
    re-deriving it.

    MA3.9: when logs_path is supplied (kriya plan-milestones passes
    config.paths.logs), one milestone_plans row (kriya/core/trace.py) is
    recorded for this call REGARDLESS of outcome - accepted, rejected after
    exhausting max_planning_attempts, or malformed_output - keyed by the
    SAME group_id a resulting MilestoneRunState would carry, so an accepted
    plan's telemetry row and its later executed `runs` rows share one id."""
    group_id = str(uuid.uuid4())
    existing_files = _list_workspace_files(workspace_path)
    workspace_note = (
        "Workspace is currently empty (or new)."
        if not existing_files
        else "Workspace already contains these files:\n" + "\n".join(f"- {f}" for f in existing_files)
    )
    topology = detect_repository_topology(workspace_path)
    base_prompt = f"Goal: {goal}\n\n{workspace_note}\n\n{render_repository_topology_summary(topology)}"

    validator = MilestonePlanValidator()
    correction_feedback = ""
    last_error = ""
    accumulated_failure_codes: List[str] = []
    last_attempted_milestones: List[MilestoneV2] = []

    for attempt in range(1, max(1, max_planning_attempts) + 1):
        prompt = base_prompt if not correction_feedback else f"{base_prompt}\n\n{correction_feedback}"
        _raw, milestones = await milestone_planner.run_with_milestone_list(prompt, stream_callback=stream_callback)
        if milestones is None:
            _log_milestone_plan_telemetry(
                logs_path, group_id, "malformed_output", [], attempt, accumulated_failure_codes, topology,
            )
            return None, "Milestone Planner output did not produce a valid milestone list."

        last_attempted_milestones = milestones
        validation_result = validator.validate(milestones, repository_topology=topology, goal_text=goal)
        if validation_result.valid:
            _log_milestone_plan_telemetry(
                logs_path, group_id, "accepted", validation_result.milestones,
                attempt, accumulated_failure_codes, topology,
            )
            return MilestoneRunState(
                group_id=group_id, original_goal=goal, milestones=validation_result.milestones,
            ), None

        accumulated_failure_codes.extend(e.code for e in validation_result.errors)
        last_error = (
            f"Milestone Planner output failed validation after {attempt} attempt(s): "
            + "; ".join(f"[{e.code}] {e.message}" for e in validation_result.errors)
        )
        correction_feedback = _build_plan_correction_feedback(validation_result)

    _log_milestone_plan_telemetry(
        logs_path, group_id, "rejected", last_attempted_milestones,
        max(1, max_planning_attempts), accumulated_failure_codes, topology,
    )
    return None, last_error


def build_milestone_goal_text(
    milestone: MilestoneV2, position: int, total: int, established_deps: List[str]
) -> str:
    """Deterministic string assembly, no extra LLM call. The header/footer are
    the only genuinely new context a milestone's goal text needs -
    run_generation_workflow's own repo-analysis stage already re-scans
    workspace_path fresh on every call, so "what does milestone N see of
    milestones 1..N-1" is otherwise already free (confirmed: it applies via
    plain file copy, never a git commit, so the next call's repo scan sees
    the real, current on-disk state).

    MA3.7: header is now driven by milestone.depends_on being non-empty,
    not index > 1 - a milestone whose EXPLICIT dependency list is empty
    (a genuine DAG root, not just "happens to be first in the list")
    structurally has no predecessor to warn about, matching
    MilestonePlanValidator's own already-validated semantics rather than a
    positional heuristic. `position`/`total` (this milestone's place in the
    topological execution order - kriya/workflow/milestone_validation.py's
    topological_order()) are still passed through for the human-readable
    banner text only, never for dependency logic."""
    header = ""
    if milestone.depends_on:
        dep_list = ", ".join(sorted(milestone.depends_on))
        header = (
            f"This is milestone '{milestone.id}' ({position} of {total}) in a "
            f"larger effort, depending on: {dep_list}. Prior milestones have "
            "already been applied to this project on disk - inspect the "
            "existing code/build files in the Workspace Context below rather "
            "than assuming a blank project, and do NOT recreate, restructure, "
            "or rename anything that already exists and already works unless "
            "this milestone's own goal below explicitly requires changing "
            "it.\n\n"
        )
        if milestone.mode == MilestoneMode.EXTENSION and milestone.extends:
            header += (
                f"This milestone EXTENDS milestone '{milestone.extends}' - evolve "
                "its existing entry point/build file, do not create a new "
                "one.\n\n"
            )
    footer = ""
    if established_deps:
        footer = (
            "\n\nDependencies established by earlier milestones that this "
            "project already relies on (do not remove or replace, even if "
            "this milestone doesn't need them itself):\n"
            + "\n".join(f"- {d}" for d in sorted(established_deps))
        )
    acceptance_text = "; ".join(a.description for a in milestone.acceptance)
    criterion = f"\n\nVerification: {acceptance_text}"
    return header + milestone.goal + criterion + footer


def build_integration_goal_text(original_goal: str, milestones: List[MilestoneV2]) -> str:
    """Synthesized from the original goal + every milestone's own acceptance
    criteria - deliberately NOT the original goal text re-submitted
    verbatim, which would risk re-triggering the exact "build everything at
    once" problem this whole feature exists to avoid."""
    criteria_lines = "\n".join(
        f"{m.id}: {a.description}" for m in milestones for a in m.acceptance
    )
    return (
        "This is the final integration/verification pass over a project "
        f"already built across {len(milestones)} milestones, in this "
        "workspace. Do NOT rewrite, restructure, or regenerate working code. "
        "Your job is to confirm the COMPLETE assembled system - all "
        "milestones together - satisfies the original goal below, fix ONLY "
        "what's broken, and verify EVERY milestone's own behavior still "
        "holds, not just the final one.\n\n"
        f"Original goal:\n{original_goal}\n\n"
        "Milestones already applied, each with its own success criterion "
        f"that must STILL hold after all later milestones:\n{criteria_lines}"
    )


def check_dependency_regression(
    workspace_path: str, established_deps: List[str]
) -> List[str]:
    """Returns the (possibly empty) list of dependencies that existed in
    established_deps but are missing from the CURRENT pom.xml - closes the
    confirmed real "pom.xml tug-of-war" bug (docs/design.md section 2.3.4d:
    by attempt 7 of one real sequential run, all 3 Ignite dependencies had
    been silently dropped), as an independent, deterministic, code-level
    check rather than relying solely on the existing reactive prompt-level
    mitigation (kriya/workflow/workflow.py's required_dependencies_prompt_block,
    which only ASKS the model not to drop anything and was documented as
    insufficient on its own). Returns [] when there's nothing to check (no
    established deps yet, or no pom.xml - a non-Java project isn't this bug)."""
    from kriya.tools.validate import get_pom_dependencies

    pom_path = os.path.join(workspace_path, "pom.xml")
    if not established_deps or not os.path.exists(pom_path):
        return []
    current_deps = set(get_pom_dependencies(pom_path))
    return sorted(set(established_deps) - current_deps)


def replay_prior_milestone_verifications(
    workspace_path: str, run_state: MilestoneRunState
) -> List[Dict[str, Any]]:
    """Re-executes every completed milestone's own persisted verification
    command sequence against the CURRENT workspace state and re-checks the
    [VERIFICATION] PASS/FAIL marker via the SAME extract_contract_verdict()
    the normal pipeline already uses for a single goal - closing a gap
    nothing else in Kriya covers today: the end-of-run full regression suite
    (kriya/workflow/workflow.py) is framework-tests-only, and
    RunVerifierAgent's own judge()/grade() never replays an EARLIER goal's
    command sequence during a LATER one. Runs BEFORE the integration call
    (not after) so a regression is attributable to milestone-to-milestone
    drift specifically, not muddied by the integration call's own edits.

    Returns a list of {"milestone_id", "reason"} dicts, one per regressed
    milestone, empty if everything replayed still passes. A milestone with no
    deterministic marker in its captured output is silently skipped (not
    counted as a failure) - a false alarm here would be worse than staying
    silent, and the integration call's own full regression suite still runs
    regardless of what this function finds."""
    from kriya.tools.validate import PolymorphicValidator

    failures: List[Dict[str, Any]] = []
    validator = PolymorphicValidator(workspace_path, original_workspace_path=workspace_path)
    for milestone_id in sorted(run_state.verification_commands):
        raw_commands = run_state.verification_commands[milestone_id]
        if not raw_commands:
            continue
        resolved_commands = [_resolve_run_command(cmd, workspace_path) for cmd in raw_commands]
        run_res = validator.run_app_sequence(resolved_commands)
        if run_res["timed_out"]:
            failures.append({
                "milestone_id": milestone_id,
                "reason": "replay timed out - possible resource-lifecycle regression",
            })
            continue
        verdict = extract_contract_verdict(run_res["output"])
        if verdict is None:
            continue
        if not verdict.get("passed"):
            failures.append({
                "milestone_id": milestone_id,
                "reason": verdict.get("reasoning") or "verification marker reported FAIL on replay",
            })
    return failures


async def run_milestones(
    we: Any,
    run_state: MilestoneRunState,
    workspace_path: str,
    approval_callback: Optional[Callable[[List[Dict[str, str]], str], Any]] = None,
    stream_callback: Optional[Callable[[str, str], None]] = None,
    skill_gap_callback: Optional[Callable[[str, List[str]], Any]] = None,
    skill_conflict_callback: Optional[Callable[[str, str, str, str, str], Any]] = None,
    web_lookup_callback: Optional[Callable[[List[Dict[str, str]]], Any]] = None,
    web_lookup_query_callback: Optional[Callable[[List[str], str], Any]] = None,
    milestone_failure_callback: Optional[Callable[[int, int, MilestoneV2, Dict[str, Any]], str]] = None,
    knowledge_risk_confirmed: bool = False,
    resume: bool = False,
    resume_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Executes a (possibly hand-edited) milestone plan: calls the EXISTING,
    unmodified we.run_generation_workflow() once per milestone against the
    SAME workspace_path, then the replay step, then one final integration
    call. Intentionally a thin driver over the existing pipeline, not a
    reimplementation of it - see this module's own top-of-file docstring for
    why that's safe.

    knowledge_risk_confirmed: threaded into EVERY run_generation_workflow()
    call below (each milestone and the final integration pass alike) -
    without this, a milestone whose goal mentions a post-cutoff library
    version hits the same knowledge_gap status plain `kriya generate` can
    resolve via --knowledge-policy/--ack-knowledge-gap/-y, but a milestone
    sequence had no equivalent: under -y the run auto-abandoned, and
    interactively, choosing "retry" via milestone_failure_callback just
    re-issued the identical goal with the risk still unconfirmed, reproducing
    the same knowledge_gap forever. The CLI decides this once, up front, for
    the whole sequence (see kriya/cli.py's `generate --from-milestones`) -
    unlike plain `generate`, this is not resolved per-gap interactively mid-
    sequence, since that would require threading gap-detail UI through this
    orchestrator's own retry loop.

    milestone_failure_callback(failed_index, total, milestone, failure_result)
    is consulted whenever a milestone exhausts its own retry budget; it must
    return "abandon" or "retry" (mirroring the existing approval_callback
    convention: present -> ask; absent -> the safe default, "abandon" - never
    silently skip ahead, since a later milestone building on a known-broken
    one directly contradicts the vertical-slice premise). "retry" simply
    calls run_generation_workflow() again for the same milestone goal - a
    fresh call already gets fresh retry budgets by construction, no new
    retry infrastructure needed.

    resume/resume_id: passed through unchanged to EVERY run_generation_workflow()
    call below (each milestone and the integration pass alike), so a sequence
    interrupted mid a single milestone's own Plan/Design/Developer stage (a
    separate, finer-grained checkpoint than this module's own
    completed_milestone_ids sidecar - see run_generation_workflow()'s own
    resume docstring) can pick back up inside that milestone instead of
    restarting it from scratch. This is safe to pass unconditionally to every
    call, not just the one milestone actually in flight when the interruption
    happened: run_generation_workflow() only actually resumes a checkpoint
    whose goal/config/workspace fingerprints still match current state, and
    refuses (falling back to a fresh run, with a warning) on any drift -
    since each milestone's goal text differs, at most one call in the
    sequence can ever match a given saved checkpoint.

    MA3.7: milestones execute in DETERMINISTIC TOPOLOGICAL order
    (kriya/workflow/milestone_validation.py's topological_order()), not
    necessarily plan-list order - a hand-edited plan file's own list
    position is no longer trusted as execution order, only its `depends_on`
    DAG is (MA3 section 23's own "no parallel execution yet, correct logical
    order first" rule - still strictly sequential, one milestone at a time).
    completed_milestone_ids tracks progress by id, not list position."""
    ordered = topological_order(run_state.milestones)
    total = len(ordered)

    for position, milestone in enumerate(ordered, start=1):
        if milestone.id in run_state.completed_milestone_ids:
            logger.info(f"Milestone '{milestone.id}' ({position}/{total}) already completed (resume) - skipping.")
            continue

        _log_phase_banner(f"MILESTONE '{milestone.id}' ({position}/{total}): {milestone.goal[:40]}")
        milestone_goal = build_milestone_goal_text(
            milestone, position, total, run_state.established_dependencies
        )

        while True:
            result = await we.run_generation_workflow(
                goal=milestone_goal,
                workspace_path=workspace_path,
                approval_callback=approval_callback,
                stream_callback=stream_callback,
                skill_gap_callback=skill_gap_callback,
                skill_conflict_callback=skill_conflict_callback,
                web_lookup_callback=web_lookup_callback,
                web_lookup_query_callback=web_lookup_query_callback,
                milestone_group_id=run_state.group_id,
                milestone_index=position,
                milestone_total=total,
                knowledge_risk_confirmed=knowledge_risk_confirmed,
                resume=resume,
                resume_id=resume_id,
                supplementary_context=render_established_file_context(run_state.established_file_context),
                established_files=sorted(run_state.established_file_context.keys()),
            )

            if result.get("quality_gates_passed"):
                # A milestone's OWN Quality Gates can pass while still
                # silently dropping an earlier milestone's dependency (the
                # confirmed real pom.xml "tug-of-war" bug, docs/design.md
                # section 2.3.4d) - by this point run_generation_workflow()
                # has already applied the milestone's files to the real
                # workspace, so this is treated as an ordinary milestone
                # failure needing the SAME human decision point
                # (milestone_failure_callback) as a Quality-Gates failure,
                # not a separate exception path the callback never sees and
                # that leaves no record behind for a later --from-milestones
                # re-run to act on.
                dropped = check_dependency_regression(workspace_path, run_state.established_dependencies)
                if dropped:
                    result = dict(result)
                    result["quality_gates_passed"] = False
                    result["status"] = "dependency_regression"
                    result["dropped_dependencies"] = dropped
                else:
                    break

            decision = "abandon"
            if milestone_failure_callback:
                decision = milestone_failure_callback(position, total, milestone, result)
            if decision == "retry":
                logger.info(f"Retrying milestone '{milestone.id}' ({position}/{total}) per milestone_failure_callback decision.")
                continue

            return {
                "status": "milestone_failed",
                "group_id": run_state.group_id,
                "milestone_id": milestone.id,
                "milestone_index": position,
                "milestone_total": total,
                "result": result,
            }

        try:
            from kriya.tools.validate import get_pom_dependencies
            pom_path = os.path.join(workspace_path, "pom.xml")
            if os.path.exists(pom_path):
                run_state.established_dependencies = sorted(
                    set(run_state.established_dependencies) | set(get_pom_dependencies(pom_path))
                )
        except Exception as e:
            logger.warning(f"Could not refresh established dependencies after milestone '{milestone.id}': {e}")

        for path in result.get("files", []):
            try:
                with open(os.path.join(workspace_path, path), "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError as e:
                logger.warning(f"Could not capture established content for '{path}' after milestone '{milestone.id}': {e}")
                continue
            projection = project_implementation_source(
                content, path, _ESTABLISHED_CONTEXT_MAX_CHARS_PER_FILE,
                reason="established_by_earlier_milestone",
            )
            run_state.established_file_context[path] = projection.content

        try:
            # Read the CURRENT real pom.xml (post-apply, same convention as
            # attempt.py's own judge() call site) - without it judge() reliably
            # mis-infers exec:java for a pom actually shaped for exec:exec
            # (confirmed real, repeated failure - see RunVerifierAgent.judge()'s
            # own system prompt/docstring), exactly the goal shape (Ignite/Qpid,
            # needing --add-opens) this feature was built for.
            pom_content_for_judge = None
            try:
                with open(os.path.join(workspace_path, "pom.xml"), "r", encoding="utf-8") as f:
                    pom_content_for_judge = f.read()
            except Exception as e:
                logger.debug(f"No pom.xml available for milestone '{milestone.id}'s run-verification judgment: {e}")
            judgment = await we.run_verifier.judge(
                goal=milestone_goal,
                design=result.get("design", ""),
                files_written=result.get("files", []),
                build_file_content=pom_content_for_judge,
            )
            if judgment.get("should_run") and judgment.get("run_commands"):
                run_state.verification_commands[milestone.id] = judgment["run_commands"]
        except Exception as e:
            logger.warning(f"Could not capture milestone '{milestone.id}'s verification commands for later replay: {e}")

        run_state.completed_milestone_ids.append(milestone.id)
        save_milestone_run_state(workspace_path, run_state)

    replay_failures = replay_prior_milestone_verifications(workspace_path, run_state)
    if replay_failures:
        return {
            "status": "milestone_replay_failed",
            "group_id": run_state.group_id,
            "milestone_total": total,
            "failures": replay_failures,
        }

    _log_phase_banner("INTEGRATION")
    integration_goal = build_integration_goal_text(run_state.original_goal, ordered)
    integration_result = await we.run_generation_workflow(
        goal=integration_goal,
        workspace_path=workspace_path,
        approval_callback=approval_callback,
        stream_callback=stream_callback,
        skill_gap_callback=skill_gap_callback,
        skill_conflict_callback=skill_conflict_callback,
        web_lookup_callback=web_lookup_callback,
        web_lookup_query_callback=web_lookup_query_callback,
        milestone_group_id=run_state.group_id,
        milestone_index=total + 1,
        milestone_total=total + 1,
        knowledge_risk_confirmed=knowledge_risk_confirmed,
        resume=resume,
        resume_id=resume_id,
        supplementary_context=render_established_file_context(run_state.established_file_context),
        established_files=sorted(run_state.established_file_context.keys()),
    )

    return {
        "status": "success" if integration_result.get("quality_gates_passed") else "integration_failed",
        "group_id": run_state.group_id,
        "milestone_total": total,
        "integration_result": integration_result,
    }
