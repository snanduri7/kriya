"""CAGC data model (Capability-Aware Generation Context, CAGC-0).

Guidance is advisory text. It is never mutation authority, verification
evidence, requirement evidence or a reason to accept a candidate: request
composition, fitting and telemetry may read a GuidanceBlock's metadata, but no
semantic, authority, acceptance or verification decision may branch on its
text, rule ids or digest.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Tuple


class Role(str, Enum):
    PLANNER = "planner"        # the direct Planner and the enforce structured Planner (never the Milestone Planner)
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"      # pre-approval and final review


PLANNING_ROLES = frozenset({Role.PLANNER, Role.ARCHITECT})


class Operation(str, Enum):
    CREATE = "create"
    EDIT = "edit"
    REPAIR = "repair"
    PLAN = "plan"
    REVIEW = "review"


class Domain(str, Enum):
    FRAMEWORK = "framework"
    BUILD = "build"
    LANGUAGE = "language"


# Render order of the domains.
DOMAIN_ORDER = (Domain.FRAMEWORK, Domain.BUILD, Domain.LANGUAGE)


class RepoContext(str, Enum):
    GREENFIELD = "greenfield"
    EXISTING = "existing"


@dataclass(frozen=True)
class GuidanceRule:
    rule_id: str                         # "<capability>.<plan|dev|review>.<name>", stable, never reused
    text: str                            # one imperative sentence, <= 200 chars
    roles: FrozenSet[Role]               # required, non-empty (no implicit "all roles")
    operations: FrozenSet[Operation]     # empty: every operation of those roles
    contexts: FrozenSet[RepoContext]     # empty: both
    priority: int                        # lower is kept longer (0..99)
    evidence: str                        # registry id, test id or "CAGC-MIGRATED-<source>"


@dataclass(frozen=True)
class CapabilityGuidance:
    capability_id: str
    version: int                         # bumped on any rule text or scope change
    domain: Domain
    rules: Tuple[GuidanceRule, ...]


@dataclass(frozen=True)
class RenderedRule:
    capability_id: str
    capability_version: int
    domain: Domain
    rule_id: str
    text: str
    priority: int


@dataclass(frozen=True)
class GuidanceBlock:
    text: str                                       # "" when no rule survives (no header)
    selected_capability_ids: Tuple[str, ...]        # "id@version", selected-but-empty capabilities included
    rendered_capability_ids: Tuple[str, ...]        # only those with at least one visible rule
    base_rule_entries: Tuple[RenderedRule, ...]     # the role-capped immutable baseline (never telemetry)
    visible_rule_entries: Tuple[RenderedRule, ...]  # exactly the rules ``text`` shows
    dropped_by_cap_rule_ids: Tuple[str, ...]        # removed before base_rule_entries was formed
    dropped_by_fit_rule_ids: Tuple[str, ...]        # in base_rule_entries but removed by request fit
    estimated_tokens: int
    digest: str                                     # sha256(text)

    @property
    def rule_ids(self) -> Tuple[str, ...]:
        return tuple(rule.rule_id for rule in self.visible_rule_entries)
