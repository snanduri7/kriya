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

from kriya.agents.contracts import Milestone
from kriya.workflow.file_resolution import _resolve_run_command
from kriya.workflow.verification_contract import extract_contract_verdict
from kriya.workflow.workflow import _log_phase_banner

logger = logging.getLogger(__name__)



@dataclass
class MilestoneRunState:
    """Orchestrator-owned bookkeeping for one decomposed goal, persisted to a
    sidecar JSON file (see save_milestone_run_state/load_milestone_run_state)
    - deliberately NOT stored in traces.db (no room for a structured command
    list there, and this is orchestration bookkeeping, not a trace record)
    or kriya/workflow/state.py's GenerationState (single-call-scoped by
    design; see the architecture review that led to this module's Option A
    approach, which keeps that scoping unchanged)."""

    group_id: str
    original_goal: str
    milestones: List[Milestone]
    completed_milestone_indices: List[int] = field(default_factory=list)
    established_dependencies: List[str] = field(default_factory=list)
    # milestone_index (1-based) -> RunVerifierAgent.judge()'s own run_commands
    # shape (List[List[str]]), persisted so the integration phase's replay
    # step (replay_prior_milestone_verifications) can re-execute an EARLIER
    # milestone's real verification without re-asking the model to re-infer
    # it, and so --resume can pick up mid-sequence.
    verification_commands: Dict[int, List[List[str]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "original_goal": self.original_goal,
            "milestones": [m.model_dump() for m in self.milestones],
            "completed_milestone_indices": self.completed_milestone_indices,
            "established_dependencies": self.established_dependencies,
            "verification_commands": {str(k): v for k, v in self.verification_commands.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MilestoneRunState":
        return cls(
            group_id=data["group_id"],
            original_goal=data["original_goal"],
            milestones=[Milestone(**m) for m in data["milestones"]],
            completed_milestone_indices=list(data.get("completed_milestone_indices", [])),
            established_dependencies=list(data.get("established_dependencies", [])),
            verification_commands={
                int(k): v for k, v in data.get("verification_commands", {}).items()
            },
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
    sidecar the first time. Progress fields (completed_milestone_indices/
    established_dependencies/verification_commands) DO come from an existing
    same-group_id sidecar when one exists, so re-running the same command
    still resumes rather than restarting the whole sequence from scratch."""
    fresh_state = MilestoneRunState.from_dict(plan_data)
    sidecar_state = load_milestone_run_state(workspace_path, plan_data["group_id"])
    if sidecar_state is not None:
        fresh_state.completed_milestone_indices = sidecar_state.completed_milestone_indices
        fresh_state.established_dependencies = sidecar_state.established_dependencies
        fresh_state.verification_commands = sidecar_state.verification_commands
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


async def plan_milestones(
    milestone_planner: Any,
    goal: str,
    workspace_path: str,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[MilestoneRunState], Optional[str]]:
    """Runs the Milestone Planner once and returns a fresh, nothing-executed-
    yet MilestoneRunState, or (None, error). Caller (kriya plan-milestones)
    persists the result via save_milestone_run_state() so a human can
    review/hand-edit the proposed slicing before `kriya generate
    --from-milestones` ever executes a single real generate call against
    their workspace - see the design doc's own note that this is a genuine
    qualitative judgment call worth a human eyeballing, not just an assertion."""
    existing_files = _list_workspace_files(workspace_path)
    workspace_note = (
        "Workspace is currently empty (or new)."
        if not existing_files
        else "Workspace already contains these files:\n" + "\n".join(f"- {f}" for f in existing_files)
    )
    prompt = f"Goal: {goal}\n\n{workspace_note}"
    _raw, milestones = await milestone_planner.run_with_milestone_list(prompt, stream_callback=stream_callback)
    if milestones is None:
        return None, "Milestone Planner output did not produce a valid milestone list."
    return MilestoneRunState(group_id=str(uuid.uuid4()), original_goal=goal, milestones=milestones), None


def build_milestone_goal_text(
    milestone: Milestone, index: int, total: int, established_deps: List[str]
) -> str:
    """Deterministic string assembly, no extra LLM call. The header/footer are
    the only genuinely new context a milestone's goal text needs -
    run_generation_workflow's own repo-analysis stage already re-scans
    workspace_path fresh on every call, so "what does milestone N see of
    milestones 1..N-1" is otherwise already free (confirmed: it applies via
    plain file copy, never a git commit, so the next call's repo scan sees
    the real, current on-disk state).

    index == 1 is treated as non-dependent unconditionally, regardless of
    milestone.depends_on_previous - a first milestone structurally has no
    predecessor to depend on. Milestone.depends_on_previous defaults to True
    (kriya/agents/contracts.py), so a model that simply omits the field for
    milestone 1 in its JSON output (a plausible under-specification for the
    smaller local models this project targets) would otherwise silently get
    the "prior milestones already exist - do NOT recreate/restructure
    anything" header on a brand-new, empty workspace, discouraging it from
    creating the very structure this milestone needs to create."""
    header = ""
    if index > 1 and milestone.depends_on_previous:
        header = (
            f"This is milestone {index} of {total} in a larger effort. Prior "
            "milestones have already been applied to this project on disk - "
            "inspect the existing code/build files in the Workspace Context below "
            "rather than assuming a blank project, and do NOT recreate, "
            "restructure, or rename anything that already exists and already "
            "works unless this milestone's own goal below explicitly requires "
            "changing it.\n\n"
        )
    footer = ""
    if established_deps:
        footer = (
            "\n\nDependencies established by earlier milestones that this "
            "project already relies on (do not remove or replace, even if "
            "this milestone doesn't need them itself):\n"
            + "\n".join(f"- {d}" for d in sorted(established_deps))
        )
    criterion = f"\n\nVerification: {milestone.success_criterion}"
    return header + milestone.goal + criterion + footer


def build_integration_goal_text(original_goal: str, milestones: List[Milestone]) -> str:
    """Synthesized from the original goal + every milestone's own success
    criterion - deliberately NOT the original goal text re-submitted
    verbatim, which would risk re-triggering the exact "build everything at
    once" problem this whole feature exists to avoid."""
    criteria_lines = "\n".join(f"{i + 1}. {m.success_criterion}" for i, m in enumerate(milestones))
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

    Returns a list of {"milestone_index", "reason"} dicts, one per
    regressed milestone, empty if everything replayed still passes. A
    milestone with no deterministic marker in its captured output is
    silently skipped (not counted as a failure) - a false alarm here would
    be worse than staying silent, and the integration call's own full
    regression suite still runs regardless of what this function finds."""
    from kriya.tools.validate import PolymorphicValidator

    failures: List[Dict[str, Any]] = []
    validator = PolymorphicValidator(workspace_path, original_workspace_path=workspace_path)
    for idx in sorted(run_state.verification_commands):
        raw_commands = run_state.verification_commands[idx]
        if not raw_commands:
            continue
        resolved_commands = [_resolve_run_command(cmd, workspace_path) for cmd in raw_commands]
        run_res = validator.run_app_sequence(resolved_commands)
        if run_res["timed_out"]:
            failures.append({
                "milestone_index": idx,
                "reason": "replay timed out - possible resource-lifecycle regression",
            })
            continue
        verdict = extract_contract_verdict(run_res["output"])
        if verdict is None:
            continue
        if not verdict.get("passed"):
            failures.append({
                "milestone_index": idx,
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
    milestone_failure_callback: Optional[Callable[[int, int, Milestone, Dict[str, Any]], str]] = None,
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
    completed_milestone_indices sidecar - see run_generation_workflow()'s own
    resume docstring) can pick back up inside that milestone instead of
    restarting it from scratch. This is safe to pass unconditionally to every
    call, not just the one milestone actually in flight when the interruption
    happened: run_generation_workflow() only actually resumes a checkpoint
    whose goal/config/workspace fingerprints still match current state, and
    refuses (falling back to a fresh run, with a warning) on any drift -
    since each milestone's goal text differs, at most one call in the
    sequence can ever match a given saved checkpoint."""
    total = len(run_state.milestones)

    for idx, milestone in enumerate(run_state.milestones, start=1):
        if idx in run_state.completed_milestone_indices:
            logger.info(f"Milestone {idx}/{total} already completed (resume) - skipping.")
            continue

        _log_phase_banner(f"MILESTONE {idx}/{total}: {milestone.goal[:40]}")
        milestone_goal = build_milestone_goal_text(
            milestone, idx, total, run_state.established_dependencies
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
                milestone_index=idx,
                milestone_total=total,
                knowledge_risk_confirmed=knowledge_risk_confirmed,
                resume=resume,
                resume_id=resume_id,
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
                decision = milestone_failure_callback(idx, total, milestone, result)
            if decision == "retry":
                logger.info(f"Retrying milestone {idx}/{total} per milestone_failure_callback decision.")
                continue

            return {
                "status": "milestone_failed",
                "group_id": run_state.group_id,
                "milestone_index": idx,
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
            logger.warning(f"Could not refresh established dependencies after milestone {idx}: {e}")

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
                logger.debug(f"No pom.xml available for milestone {idx}'s run-verification judgment: {e}")
            judgment = await we.run_verifier.judge(
                goal=milestone_goal,
                design=result.get("design", ""),
                files_written=result.get("files", []),
                build_file_content=pom_content_for_judge,
            )
            if judgment.get("should_run") and judgment.get("run_commands"):
                run_state.verification_commands[idx] = judgment["run_commands"]
        except Exception as e:
            logger.warning(f"Could not capture milestone {idx}'s verification commands for later replay: {e}")

        run_state.completed_milestone_indices.append(idx)
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
    integration_goal = build_integration_goal_text(run_state.original_goal, run_state.milestones)
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
    )

    return {
        "status": "success" if integration_result.get("quality_gates_passed") else "integration_failed",
        "group_id": run_state.group_id,
        "milestone_total": total,
        "integration_result": integration_result,
    }
