"""Subtask context projection - MA6.4 of the MA6 structured-execution
implementation plan.

project_for_subtask() narrows an already-assembled ContextPackage (MA5.6,
kriya/workflow/context_package.py - built by ContextOrchestrator, MA5.7,
for the whole request/milestone) down to just what ONE Subtask (MA6.1,
kriya/workflow/plan_schema.py) actually needs. Per the MA6 spec: "the
Developer sees ONLY this subtask's narrow slice (not full milestone/plan/
repo/retry history) - one of the biggest improvements for local-model
reliability." SubtaskExecutor (MA6.5) calls this once per subtask, never
hands a MODEL-tagged subtask the full package.

Deliberately NOT a re-analysis pass: this is a cheap, pure projection over
content ContextOrchestrator already retrieved - it never reads the
filesystem, queries the dependency graph, or calls an LLM. Anything it
can't confidently determine is relevant, it keeps rather than guesses at
dropping (contract_entries, conventions, spec_slice, carried_forward_
criteria all lack a reliable per-file relevance signal in their current
shape - silently dropping one of these on a bad guess risks losing an
applicable invariant the Developer actually needed; the token cost of
keeping them is small next to a single subtask's already-narrow file
slice). What it narrows with real confidence: relevant_files (the bulk of
a package's size) down to the subtask's own target files plus items whose
own recorded `reason` provenance (kriya/workflow/context_package.py's
ContextItem.reason) names one of those target files, and artifact_entries
down to those whose path-shaped fields (module_path/resource_roots/
entrypoints) actually overlap a target file.

Verification-target information is NOT duplicated into the projected
package - a subtask's `verification` list already lives on the Subtask
itself (plan_schema.Subtask.verification); SubtaskExecutor reads it
directly from there rather than through a second, potentially-drifting
copy carried on the context object.
"""
from __future__ import annotations

import os
from typing import Tuple

from kriya.workflow.context_package import ContextPackage, make_omitted_entry
from kriya.workflow.plan_schema import Subtask

# ContextItem source_types (kriya/workflow/context_package.py's own
# CONTEXT_SOURCE_TYPES vocabulary) that represent a relationship TO another
# file, as opposed to a hit found for its own sake (named_in_request,
# semantic_hit) - only these are eligible to be kept as an indirect
# "direct dependency of a target file" match via reason-text overlap.
_RELATIONAL_SOURCE_TYPES = frozenset(
    {"lsp_reference", "graph_dependency", "contract_provider", "artifact_dependency"}
)


def _basename(path: str) -> str:
    return os.path.basename(path.replace("\\", "/"))


def _reason_names_a_target(reason: str, target_paths: Tuple[str, ...], target_basenames: Tuple[str, ...]) -> bool:
    text = reason or ""
    return any(t in text for t in target_paths) or any(b in text for b in target_basenames)


def _path_overlaps_target(candidate_path: str, target_paths: Tuple[str, ...]) -> bool:
    """True if candidate_path is a prefix of (or equal to, or a parent
    directory of) any target file path - used for artifact_entries'
    module_path/resource_roots/entrypoints, which are directory- or
    module-shaped, not always exact file paths."""
    normalized = candidate_path.replace("\\", "/").rstrip("/")
    for target in target_paths:
        norm_target = target.replace("\\", "/")
        if norm_target == normalized or norm_target.startswith(normalized + "/") or normalized.startswith(norm_target):
            return True
    return False


def _artifact_entry_relevant(entry: dict, target_paths: Tuple[str, ...]) -> bool:
    """Keeps an artifact entry when it has no path-shaped fields at all
    (nothing to confidently filter on - fail open, not closed, since
    dropping a build/packaging fact the Developer actually needs is worse
    than a slightly larger prompt) or when one of its path-shaped fields
    overlaps a target file."""
    path_fields = []
    module_path = entry.get("module_path")
    if module_path:
        path_fields.append(module_path)
    path_fields.extend(entry.get("resource_roots") or [])
    path_fields.extend(entry.get("entrypoints") or [])

    if not path_fields:
        return True
    return any(_path_overlaps_target(p, target_paths) for p in path_fields)


def project_for_subtask(package: ContextPackage, subtask: Subtask) -> ContextPackage:
    """Returns a NEW ContextPackage narrowed to `subtask` - never mutates
    `package` (same frozen + with_changes()-style contract every MA5
    control-plane object already follows). Every relevant_files entry this
    drops is recorded in the result's `omitted` list (ContextPackage's own
    "explicit, auditable" omission convention) with
    reason="narrowed out of subtask <id>'s context projection", never a
    silent truncation."""
    target_paths = tuple(pf.path for pf in subtask.planned_files)
    target_basenames = tuple(_basename(p) for p in target_paths)
    target_set = set(target_paths)

    kept_files: list = []
    omitted = list(package.omitted)
    for rank, item in enumerate(package.relevant_files):
        is_direct_hit = item.path in target_set
        is_named_dependency = (
            item.source_type in _RELATIONAL_SOURCE_TYPES
            and _reason_names_a_target(item.reason, target_paths, target_basenames)
        )
        if is_direct_hit or is_named_dependency:
            kept_files.append(item)
        else:
            omitted.append(
                make_omitted_entry(
                    path=item.path,
                    rank=rank,
                    reason=f"narrowed out of subtask {subtask.id!r}'s context projection",
                    estimated_tokens=max(1, len(item.content) // 4),
                )
            )

    kept_artifact_entries = tuple(
        entry for entry in package.artifact_entries if _artifact_entry_relevant(entry, target_paths)
    )

    return package.with_changes(
        relevant_files=tuple(kept_files),
        artifact_entries=kept_artifact_entries,
        omitted=tuple(omitted),
    )
