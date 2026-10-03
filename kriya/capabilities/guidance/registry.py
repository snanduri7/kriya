"""The closed CAGC guidance registry (CAGC-0: migrated rules only).

Rules are package source, never loaded from user directories, config or
skills, so the resume fingerprint (kriya_runtime_fingerprint) covers them. A
migrated rule keeps its source prompt's role; widening it to another role needs
per-rule ablation evidence. Selected-but-empty capabilities are listed so their
selection stays observable.
"""
from __future__ import annotations

from typing import Tuple

from kriya.capabilities.guidance.model import (
    CapabilityGuidance,
    Domain,
    GuidanceRule,
    RepoContext,
    Role,
)

_PLANNER = "CAGC-MIGRATED-kriya.agents.agent:PlannerAgent.system_prompt"
_ARCHITECT = "CAGC-MIGRATED-kriya.agents.agent:ArchitectAgent.system_prompt"
_DEVELOPER = "CAGC-MIGRATED-kriya.agents.agent:DeveloperAgent.system_prompt"
_REVIEWER = "CAGC-MIGRATED-kriya.agents.agent:ReviewerAgent.system_prompt"

_GREENFIELD = frozenset({RepoContext.GREENFIELD})
_EXISTING = frozenset({RepoContext.EXISTING})


def _rule(rule_id: str, text: str, role: Role, evidence: str, *, contexts: frozenset = frozenset(),
          priority: int = 50) -> GuidanceRule:
    return GuidanceRule(rule_id, text, frozenset({role}), frozenset(), contexts, priority, evidence)


def _build_plan_rules(capability: str, topology: str) -> Tuple[GuidanceRule, ...]:
    """The build-topology rules moved out of the Planner and Architect prompts;
    ``topology`` is the build system's own wording of a single-unit build."""
    return (
        _rule(f"{capability}.plan.greenfield_minimal_topology",
              f"For a new project, use the smallest build topology that satisfies the goal: {topology} unless the "
              "goal asks for more.", Role.PLANNER, _PLANNER, contexts=_GREENFIELD, priority=20),
        _rule(f"{capability}.plan.existing_topology_preserved",
              "Keep the existing module or project structure unless the goal requires changing it.",
              Role.PLANNER, _PLANNER, contexts=_EXISTING, priority=20),
        _rule(f"{capability}.plan.greenfield_manifest_required",
              "A new project of this build system needs its build file in the design.",
              Role.ARCHITECT, _ARCHITECT, contexts=_GREENFIELD, priority=10),
    )


REGISTRY: Tuple[CapabilityGuidance, ...] = (
    CapabilityGuidance("spring", 1, Domain.FRAMEWORK, (
        _rule("spring.review.transactional_self_invocation",
              "For proxy-based Spring transactions, check same-class @Transactional calls against the shown call "
              "path before claiming interception.", Role.REVIEWER, _REVIEWER, priority=30),
    )),
    CapabilityGuidance("spring_xml", 1, Domain.FRAMEWORK, (
        _rule("spring_xml.plan.extend_existing_context",
              "Prefer the existing Spring XML context that owns related beans; add a new context only when the "
              "goal or repository structure requires it.", Role.ARCHITECT, _ARCHITECT, contexts=_EXISTING,
              priority=20),
    )),
    CapabilityGuidance("spring_config", 1, Domain.FRAMEWORK, ()),
    CapabilityGuidance("maven", 1, Domain.BUILD, _build_plan_rules("maven", "one module") + (
        _rule("maven.review.manifest_care",
              "Check build-file changes element by element against the current file; do not report missing "
              "elements you have not located.", Role.REVIEWER, _REVIEWER, priority=30),
    )),
    CapabilityGuidance("gradle", 1, Domain.BUILD, _build_plan_rules("gradle", "one project without subprojects")),
    CapabilityGuidance("pip", 1, Domain.BUILD, ()),
    CapabilityGuidance("java", 1, Domain.LANGUAGE, (
        _rule("java.dev.import_style",
              "Follow the file's existing import style; add imports for referenced external types and keep "
              "intentional qualified names.", Role.DEVELOPER, _DEVELOPER, priority=30),
    )),
    CapabilityGuidance("python", 1, Domain.LANGUAGE, ()),
)


def registry_without(rule_id: str) -> Tuple[CapabilityGuidance, ...]:
    """REGISTRY with one rule removed - the per-rule ablation input to
    compose_guidance(..., registry=...). An unknown id is an error, never a
    silent no-op ablation."""
    if not any(rule.rule_id == rule_id for capability in REGISTRY for rule in capability.rules):
        raise KeyError(rule_id)
    return tuple(CapabilityGuidance(c.capability_id, c.version, c.domain,
                                    tuple(r for r in c.rules if r.rule_id != rule_id)) for c in REGISTRY)
