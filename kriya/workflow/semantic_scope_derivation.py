"""CORR-018 general-case closure (2026-09-13): deterministic derivation of
`AuthorizedSemanticRegion[]` for ordinary generate/fix Java brownfield runs,
gated entirely behind `autonomy.semantic_region_enforcement_required`
(default False - see `kriya/config/config.py`'s own field docstring for why
an unconditional default was explicitly rejected with the user).

TWO sources only, matching this closure's own Invariant 4/5 exactly - never
a third:

1. **Explicit** (`derive_explicit_semantic_regions`) - a pure translation of
   CORR-016's own `derive_direct_contract_authorizations()` output
   (`kriya/workflow/contract_authority.py`, unchanged, reused verbatim -
   one requirement-authority derivation, not two) into this module's own
   region representation. Reads only `grounding_goal` (never Planner text)
   plus the REAL baseline file content (to determine record-vs-class shape
   and to look up an EXISTING method/constructor's exact parameter types
   for a MODIFY-category clause - never invented, never guessed).

2. **Repository-grounded** (`derive_repository_grounded_semantic_regions`)
   - authorizes an interface's own newly-required method AND the exact
   corresponding method in a REAL implementer class, when the `implements`
   relationship is structurally present in the repository (compiler-forced,
   not a heuristic). Scoped to the given `structured_plan`'s own
   `planned_files` only - never an unbounded repository-wide scan (Task 7's
   own framing: "Planner includes implementation file" - the implementer
   must already be somewhere the plan legitimately reaches). V1 SCOPE,
   explicit and disclosed: ZERO-ARGUMENT interface methods only - a
   parameterized method's exact parameter types cannot be recovered from
   goal text or repository evidence for a method that does not exist yet
   in either file, so this derivation fails closed (produces no region) for
   that case rather than guessing. (The record+serializer style example in
   the closure task's own Task 7 is explicitly NOT implemented here for the
   same reason at a larger scale - no Java-level forced relationship
   identifies "the serializer" the way `implements` identifies an
   implementer; see this closure's own return report / risk register entry
   for the full disclosure, matching TOOL-003's own EXPLICIT_DESTINATIONS
   "disclosed residual, not a closure blocker" precedent.)

Planner strategy is NEVER a third source (Invariant 3/5): `Subtask.
description`/`requires`/`provides`/`GlobalInvariant.statement` are never
read anywhere in this module, directly or indirectly - both functions above
consult only `grounding_goal` text (via the reused, unchanged
`derive_direct_contract_authorizations()`) and real, on-disk repository
content. A Planner-selected file that is neither goal-grounded nor
`implements`-grounded receives ZERO regions from this module, by
construction - this is what makes Task 9's Planner-only-file denial and
Task 10's grounded-second-file acceptance both true from the SAME
mechanism, not two special cases.

Provenance (Task 8): every region's own `source` field (already part of
`AuthorizedSemanticRegion`, `semantic_region_authority.py`) states which of
the two derivations produced it and the originating `ContractEvolution
Authorization.source_requirement_id` (which itself carries `derivation_
evidence["grounding_clause"]` - the exact raw goal text) - inspectable
without a new subsystem, per this closure task's own explicit instruction.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

from kriya.analyzer.java_members import extract_java_members
from kriya.workflow.contract_authority import (
    ChangeCategory,
    _find_symbol,
    derive_direct_contract_authorizations,
)
from kriya.workflow.edit_safety import _strip_java_comments_and_strings

# Annotation-only names, imported at runtime so typing.get_type_hints()
# resolves (PRD-001); no cycle.
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.semantic_region_authority import (
    AuthorizedSemanticRegion,
    RegionType,
    stable_field_key,
    stable_member_key,
    stable_record_component_key,
)

_RECORD_DECL_RE = re.compile(r"\brecord\s+([A-Za-z_$][\w$]*)\b")
_INTERFACE_DECL_RE_TEMPLATE = r"\binterface\s+{name}\b"
_IMPLEMENTS_CLASS_RE_TEMPLATE = (
    r"\bclass\s+([A-Za-z_$][\w$]*)\b[^{{]*\bimplements\b[^{{]*(?<!\w){name}(?!\w)[^{{]*\{{"
)


def _read_workspace_file(workspace_path: str, relpath: str) -> Optional[str]:
    try:
        with open(os.path.join(workspace_path, relpath), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _owner_type_name(owner_path: str) -> str:
    basename = owner_path.rsplit("/", 1)[-1]
    return basename.rsplit(".", 1)[0] if "." in basename else basename


def derive_explicit_semantic_regions(
    grounding_goal: str,
    structured_plan: Optional["EngineeringPlan"],
    workspace_path: str,
) -> List[AuthorizedSemanticRegion]:
    """Translates each DIRECT contract authorization into an
    AuthorizedSemanticRegion, reading the REAL baseline file only to
    disambiguate shape (record vs. class) and, for a MODIFY-category
    method/constructor clause, to look up the existing member's own exact
    parameter types (never invented). A field/record-component-shaped
    clause always yields a region (the owner's own real shape decides
    FIELD_DECLARATION vs. RECORD_COMPONENT) - a method/constructor clause
    yields one only for MODIFY where the member resolves UNAMBIGUOUSLY by
    name in the baseline (an overload collision, or a genuine ADD whose
    parameter types cannot be known from goal text alone, produces no
    region - fail closed, per Invariant 6/12, not a guess). REMOVE-category
    method/constructor clauses are intentionally never granted a region
    here - CORR-016's own `find_brownfield_public_api_changes()` already
    independently authorizes/protects a signature removal; a semantic-
    region grant for it would be redundant, not additive."""
    authorizations = derive_direct_contract_authorizations(grounding_goal, structured_plan)
    regions: List[AuthorizedSemanticRegion] = []
    for auth in authorizations:
        clause = auth.derivation_evidence.get("grounding_clause", "")
        symbol_match = _find_symbol(clause)
        if symbol_match is None:
            continue  # defensive - should not happen for a real authorization
        real_symbol_name, is_field = symbol_match
        owner_path = auth.affected_owner
        content = _read_workspace_file(workspace_path, owner_path)
        if content is None:
            continue
        source = f"grounding_goal_direct:{auth.source_requirement_id}"
        owner_type_name = _owner_type_name(owner_path)
        stripped = _strip_java_comments_and_strings(content)

        if is_field:
            is_record_owner = bool(_RECORD_DECL_RE.search(stripped))
            if is_record_owner:
                key = stable_record_component_key(owner_path, owner_type_name, real_symbol_name)
                regions.append(AuthorizedSemanticRegion(
                    relpath=owner_path, region_type=RegionType.RECORD_COMPONENT,
                    member_key=key, source=source,
                ))
            else:
                key = stable_field_key(owner_path, owner_type_name, real_symbol_name)
                regions.append(AuthorizedSemanticRegion(
                    relpath=owner_path, region_type=RegionType.FIELD_DECLARATION,
                    member_key=key, source=source,
                ))
            regions.append(AuthorizedSemanticRegion(
                relpath=owner_path, region_type=RegionType.IMPORTS, source=source,
            ))
            continue

        if auth.allowed_change_category != ChangeCategory.MODIFY:
            continue  # ADD (params unknowable here) / REMOVE (CORR-016's own job) - no region

        is_constructor = real_symbol_name == owner_type_name
        try:
            members = extract_java_members(content, enclosing_type=owner_type_name)
        except Exception:
            continue
        matches = [
            m for m in members
            if m.name == real_symbol_name and (m.kind == "constructor") == is_constructor
        ]
        if len(matches) != 1:
            continue  # not found, or an overload collision - fail closed, never guess
        m = matches[0]
        key = stable_member_key(owner_path, owner_type_name, m.kind, m.name, m.parameter_types)
        region_type = RegionType.CONSTRUCTOR_BODY if is_constructor else RegionType.METHOD_BODY
        regions.append(AuthorizedSemanticRegion(relpath=owner_path, region_type=region_type, member_key=key, source=source))
        regions.append(AuthorizedSemanticRegion(relpath=owner_path, region_type=RegionType.IMPORTS, source=source))
    return regions


def derive_repository_grounded_semantic_regions(
    grounding_goal: str,
    structured_plan: Optional["EngineeringPlan"],
    workspace_path: str,
) -> List[AuthorizedSemanticRegion]:
    """Interface -> implementer necessity, V1 scope: a newly-required
    ZERO-ARGUMENT interface method, plus the corresponding method in a REAL
    implementer class found among `structured_plan`'s own `planned_files`
    (never an unbounded repository scan). See this module's own docstring
    for why a parameterized method, and the "record + serializer" shape
    from the closure task's own Task 7, are both explicitly out of scope
    for this pass."""
    if structured_plan is None:
        return []
    authorizations = derive_direct_contract_authorizations(grounding_goal, structured_plan)
    plan_paths = sorted({
        pf.path for st in structured_plan.subtasks for pf in (st.planned_files or [])
    })
    regions: List[AuthorizedSemanticRegion] = []
    for auth in authorizations:
        if auth.allowed_change_category != ChangeCategory.ADD:
            continue
        clause = auth.derivation_evidence.get("grounding_clause", "")
        symbol_match = _find_symbol(clause)
        if symbol_match is None or symbol_match[1]:
            continue  # not a method-shaped (non-field) symbol
        method_name = symbol_match[0]
        owner_path = auth.affected_owner
        owner_content = _read_workspace_file(workspace_path, owner_path)
        if owner_content is None:
            continue
        owner_type_name = _owner_type_name(owner_path)
        stripped_owner = _strip_java_comments_and_strings(owner_content)
        if not re.search(_INTERFACE_DECL_RE_TEMPLATE.format(name=re.escape(owner_type_name)), stripped_owner):
            continue  # only a real `interface` owner is an eligible seed

        source = f"grounding_goal_direct:{auth.source_requirement_id}"
        iface_key = stable_member_key(owner_path, owner_type_name, "method", method_name, ())
        regions.append(AuthorizedSemanticRegion(
            relpath=owner_path, region_type=RegionType.METHOD_ADD, member_key=iface_key, source=source,
        ))

        implements_re = re.compile(_IMPLEMENTS_CLASS_RE_TEMPLATE.format(name=re.escape(owner_type_name)))
        for candidate_path in plan_paths:
            if candidate_path == owner_path or not candidate_path.endswith(".java"):
                continue
            impl_content = _read_workspace_file(workspace_path, candidate_path)
            if impl_content is None:
                continue
            stripped_impl = _strip_java_comments_and_strings(impl_content)
            impl_match = implements_re.search(stripped_impl)
            if not impl_match:
                continue
            impl_type_name = impl_match.group(1)
            impl_source = f"repo_grounded:interface_implementer:{owner_path}::{auth.source_requirement_id}"
            impl_key = stable_member_key(candidate_path, impl_type_name, "method", method_name, ())
            regions.append(AuthorizedSemanticRegion(
                relpath=candidate_path, region_type=RegionType.METHOD_ADD,
                member_key=impl_key, source=impl_source,
            ))
    return regions


def derive_semantic_authority_for_run(
    grounding_goal: str,
    structured_plan: Optional["EngineeringPlan"],
    workspace_path: str,
) -> List[AuthorizedSemanticRegion]:
    """Combined orchestrator - the one function production call sites use.
    Both sub-derivations are pure functions of (grounding_goal,
    structured_plan, real on-disk repository content) only; Planner
    strategy (description/requires/provides/labels) is never an input to
    either, so a Planner-only file (Task 9) structurally cannot receive a
    region from this function, and a legitimately goal-required second file
    (Task 10) can, from the SAME call, with no special-casing between the
    two."""
    regions = list(derive_explicit_semantic_regions(grounding_goal, structured_plan, workspace_path))
    regions.extend(derive_repository_grounded_semantic_regions(grounding_goal, structured_plan, workspace_path))
    return regions
