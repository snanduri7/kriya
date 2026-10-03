"""Capability-Aware Generation Context (CAGC): small, deterministic,
role-aware ADVISORY guidance for the language, framework and build semantics
that apply to the evidence a model request actually carries.

Guidance is never mutation authority, verification evidence, requirement
evidence or a reason to accept a candidate. ``kriya/workflow`` and
``kriya/agents`` import only this public API (tripwire in
tests/test_cagc_registry.py); selection and low-level rendering stay private.
"""
from kriya.capabilities.guidance.facts import (
    BuildRootFact,
    RepositoryFacts,
    SelectionFacts,
    file_reader,
    repository_facts,
    selection_facts,
)
from kriya.capabilities.guidance.model import (
    PLANNING_ROLES,
    CapabilityGuidance,
    Domain,
    GuidanceBlock,
    GuidanceRule,
    Operation,
    RenderedRule,
    RepoContext,
    Role,
)
from kriya.capabilities.guidance.registry import REGISTRY, registry_without
from kriya.capabilities.guidance.render import compose_guidance, guidance_event_details, refit_guidance

__all__ = [
    "PLANNING_ROLES", "REGISTRY", "BuildRootFact", "CapabilityGuidance", "Domain", "GuidanceBlock", "GuidanceRule",
    "Operation", "RenderedRule", "RepoContext", "RepositoryFacts", "Role", "SelectionFacts", "compose_guidance",
    "file_reader", "guidance_event_details", "refit_guidance", "registry_without", "repository_facts",
    "selection_facts",
]
