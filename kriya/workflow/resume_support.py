"""Resume support of the controller's runs: what a resumed or re-planned run may reuse, quarantine or must migrate
(full modularization W4, E11).

Moved verbatim from ``kriya/workflow/workflow_controller.py`` (module-level code; ``execute``,
``execute_milestones`` and the enforce loop that call it stay in the controller):

- resume revalidation of the structured plan: TOOL subtasks are never resume-completed
  (``exclude_tool_subtasks_from_resume``) and the terminal all-completed gate (``all_subtasks_completed``);
- a re-plan's abandoned files (``compute_abandoned_plan_files``, ``quarantine_abandoned_plan_files``).

D2 M4 (owner decision 2026-10-23): the one-time ownership migration of legacy (ownerless) control documents that used to live here
(``_migrate_legacy_milestone_ownership``, ``_migrate_legacy_controller_ownership``) is RETIRED. A control document without an ownership
stamp, or with another workspace's, is refused (``kriya.control.ownership_audit``, ``kriya.context.workspace_identity.validate_ownership``),
never adopted by stamping it.

Every decision and move is unchanged, in the same order. ``kriya/workflow/workflow_controller.py`` keeps every one of these names (a
forwarder). What arrives in ``ResumeDeps``, resolved through ``kriya.workflow.workflow_controller`` by
``workflow_controller._resume_deps()`` when a forwarder runs: the controller's ``logger``. This module never imports
``kriya.workflow.workflow_controller``, ``workflow``, ``attempt`` or ``kriya.cli``, nor any all-import-cycle module.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple

from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod
from kriya.workflow.workflow_types import SubtaskStatus


@dataclass(frozen=True)
class ResumeDeps:
    """What the moved resume-support code may use, resolved through ``kriya.workflow.workflow_controller`` by
    ``workflow_controller._resume_deps()``. Every field is required."""

    # the controller's logger
    logger: Any


def exclude_tool_subtasks_from_resume(
    plan: EngineeringPlan, resumed_subtask_states: Dict[str, str],
) -> Tuple[Dict[str, str], List[str]]:
    """TOOL-001 (2026-09-13), Invariant 18 ("resume/checkpoint cannot
    bypass revalidation"): pure computation, separated out for direct unit
    testing (mirrors compute_abandoned_plan_files's own "pure/testable,
    real call site wires it in" split just above).

    A MODEL subtask's "completed" resume record is self-verifying via the
    caller's own git tree_hash/base_commit match - the written file
    content IS the artifact, and it's still on disk exactly as recorded.
    A TOOL subtask's "completed" record has no such self-verifying
    property: the side effect (an MCP tools/call, a Maven build, ...) left
    no git-visible trace that check can confirm is still current, and
    TOOL-002/TOOL-003's own authority state (durable approval, capability
    profile) can drift independently of the git workspace entirely.
    Rather than inventing new idempotency/staleness semantics for tool
    side effects (explicitly out of scope), a TOOL-tagged subtask is never
    treated as resume-completed - removed from the returned dict
    unconditionally, so the caller's own per-subtask loop always
    re-executes it through the full governed path, which always
    re-consults current TOOL-002/TOOL-003/SEC-005/ExecutionPolicy
    authority fresh. This is a disclosed behavior CHOICE, not a
    side-effect-free compromise: a non-idempotent tool re-invoked on
    resume is a plan-design hazard the existing governance layer surfaces
    the same way it would on any fresh second run of the same plan,
    whereas silently skipping TOOL-002 revalidation would be a
    security-contract violation - the two are not symmetric risks, so
    this does not split the difference.

    Only ever REMOVES entries (never adds/completes one) - a subtask id
    from `resumed_subtask_states` that is no longer present in the
    (possibly freshly re-planned) `plan` is left exactly as the caller
    passed it; this function has no opinion on plan-drift handling, only
    on which currently-plan-present, currently-"completed" entries are
    safe to treat as resume-skippable."""
    tool_subtask_ids = {
        st.id for st in plan.subtasks if st.execution_method == ExecutionMethod.TOOL
    }
    excluded = sorted(
        subtask_id for subtask_id, status in resumed_subtask_states.items()
        if status == "completed" and subtask_id in tool_subtask_ids
    )
    if not excluded:
        return resumed_subtask_states, excluded
    filtered = {k: v for k, v in resumed_subtask_states.items() if k not in excluded}
    return filtered, excluded


def all_subtasks_completed(
    current_plan_subtask_ids: Set[str], latest_status_by_subtask: Dict[str, SubtaskStatus],
) -> bool:
    """Terminal-correctness gate (TOOL-001, 2026-09-13; Invariant 20: a
    Subtask's own execution status only ever means "the attempt itself
    didn't error" - never "the workflow overall may terminate successful"
    on its own). Pure computation, separated out for direct unit testing:
    a plain AND-reduction over every CURRENT plan subtask's latest
    status - a single TOOL subtask reporting COMPLETED (even if its own
    `tool_output` happens to contain text like "PASS") cannot make this
    True while any OTHER subtask (MODEL or TOOL) in the same plan is not
    itself COMPLETED; nothing here ever inspects `tool_output`/`files`
    content at all, only the structural `SubtaskStatus` each subtask's own
    real execution produced. Scope recovery can merge/remove a subtask
    mid-run, leaving a stale COMPLETED entry in the results dict even
    though it's no longer part of the current plan - measured against the
    current plan's ids only, never bare dict/set equality, so a
    successfully executed revised plan is never falsely reported failed
    solely because it now has fewer stages."""
    return all(
        latest_status_by_subtask.get(subtask_id) == SubtaskStatus.COMPLETED
        for subtask_id in current_plan_subtask_ids
    )


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
    deps: ResumeDeps,
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
            deps.logger.warning(f"Could not quarantine abandoned plan file {rel_path!r} (non-fatal, left in place): {e}")
    return moved
