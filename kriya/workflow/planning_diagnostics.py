"""Bounded, local-only evidence for authoritative structured planning.

These records may contain goals, paths, and planner output. They are written
only below the workspace's ``.kriya/control`` directory and are never inputs
to outward lookup. Diagnostics observe planning; they do not authorize a plan,
alter validation, or change bounded repair behavior.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from kriya.control.workspace_identity import workspace_identity
from kriya.policy.filesystem import AuthorizedFileWriter
from kriya.workflow.edit_safety import read_file_revision
from kriya.workflow.plan_schema import EngineeringPlan


_MAX_TEXT_CHARS = 20_000
_MAX_REPOSITORY_PATHS = 100


def _bounded_text(value: Optional[str]) -> Optional[str]:
    if value is None or len(value) <= _MAX_TEXT_CHARS:
        return value
    return value[:_MAX_TEXT_CHARS] + "...[truncated]"


def bounded_repository_evidence(workspace_path: str, paths: Iterable[str]) -> List[Dict[str, Any]]:
    """Return content-free exact-path evidence, bounded and deterministically ordered."""
    evidence: List[Dict[str, Any]] = []
    for path in sorted(set(paths))[:_MAX_REPOSITORY_PATHS]:
        full_path = os.path.join(workspace_path, path)
        evidence.append({
            "path": path,
            "exists": os.path.exists(full_path),
            "is_file": os.path.isfile(full_path),
            "is_directory": os.path.isdir(full_path),
        })
    return evidence


def normalized_ownership_validation_records(
    plan: Optional[EngineeringPlan], *, workspace_path: str,
) -> List[Dict[str, Any]]:
    """Project the validator's actual file-ownership rule into typed records.

    The current plan schema has no planned-symbol or ownership-evidence field,
    and the validator performs no semantic existing-owner discovery. Recording
    those facts as null/false is intentional: diagnostics must expose missing
    evidence rather than manufacture it.
    """
    if plan is None:
        return []
    claims: Dict[str, List[Dict[str, str]]] = {}
    for subtask in plan.subtasks:
        for planned_file in subtask.planned_files:
            claims.setdefault(planned_file.path, []).append({
                "subtask_id": subtask.id,
                "action": planned_file.action.value,
                "reason": planned_file.reason,
            })

    records: List[Dict[str, Any]] = []
    for planned_path, path_claims in sorted(claims.items()):
        owners = [claim["subtask_id"] for claim in path_claims]
        unique = len(owners) == 1
        exists = os.path.exists(os.path.join(workspace_path, planned_path))
        records.append({
            "planned_path": planned_path,
            "planned_symbol": None,
            "declared_owner": owners[0] if unique else None,
            "declared_owners": owners,
            "candidate_existing_owners": [planned_path] if exists else [],
            "repository_evidence": {
                "exact_path_exists": exists,
                "ownership_discovery_performed": False,
                "claims": path_claims,
            },
            "validator_rule": "each planned file path must be owned by exactly one subtask",
            "decision": "accepted" if unique else "rejected",
            "reason": (
                "unique planned-file owner"
                if unique else f"planned path is claimed by {len(owners)} subtasks"
            ),
        })
    return records


def planning_diagnostics_path(workspace_path: str, run_id: str) -> str:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)
    return os.path.join(
        workspace_path, ".kriya", "control", "planning-diagnostics", f"{safe_run_id}.jsonl",
    )


def approved_plan_diagnostic(
    plan: Optional[EngineeringPlan], *, validation_errors: Iterable[str],
) -> Optional[Dict[str, Any]]:
    """Compact, explicit dump of the execution graph that was approved.

    ``parsed_plan`` preserves the complete Planner payload for every attempt.
    This projection has a different purpose: make the final runtime ownership
    contract directly observable without reconstructing it from execution logs
    or guessing which planning attempt was accepted.
    """
    errors = list(validation_errors)
    if plan is None or errors:
        return None
    return {
        "plan_id": plan.plan_id,
        "subtasks": [
            {
                "id": subtask.id,
                "planned_files": [
                    {
                        "path": planned_file.path,
                        "action": planned_file.action.value,
                        "environment_requirements": list(planned_file.environment_requirements),
                        "requires_capabilities": list(planned_file.requires_capabilities),
                    }
                    for planned_file in subtask.planned_files
                ],
                "depends_on": list(subtask.depends_on),
                "requires": list(subtask.requires),
                "provides": list(subtask.provides),
            }
            for subtask in plan.subtasks
        ],
    }


def persist_planning_attempt_diagnostic(
    workspace_path: str,
    run_id: str,
    *,
    attempt: int,
    planner_request: str,
    planner_system_prompt: str,
    raw_plan_response: str,
    plan: Optional[EngineeringPlan],
    validation_errors: Iterable[str],
    reason_codes: Iterable[str],
    repository_evidence: Iterable[Dict[str, Any]],
    repair_prompt: Optional[str],
) -> str:
    """Atomically append one bounded attempt record under the local workspace."""
    path = planning_diagnostics_path(workspace_path, run_id)
    validation_errors = list(validation_errors)
    payload = {
        "schema_version": 2,
        "workspace_id": workspace_identity(workspace_path),
        "run_id": run_id,
        "attempt": attempt,
        "planner_request": _bounded_text(planner_request),
        "planner_system_prompt": _bounded_text(planner_system_prompt),
        "raw_plan_response": _bounded_text(raw_plan_response),
        "parsed_plan": plan.model_dump(mode="json") if plan is not None else None,
        "approved_plan": approved_plan_diagnostic(
            plan, validation_errors=validation_errors,
        ),
        "validation_errors": validation_errors,
        "reason_codes": list(reason_codes),
        "ownership_validation": normalized_ownership_validation_records(
            plan, workspace_path=workspace_path,
        ),
        "repository_evidence": list(repository_evidence)[:_MAX_REPOSITORY_PATHS],
        "repair_prompt": _bounded_text(repair_prompt),
    }
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    content = existing + json.dumps(payload, sort_keys=True) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    AuthorizedFileWriter(workspace_path).commit_file(
        path, content, expected_revision=read_file_revision(path),
    )
    return path
