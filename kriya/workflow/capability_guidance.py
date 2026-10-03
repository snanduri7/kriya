"""CAGC-0 request integration: one capability-guidance section of one model
request, fitted by the request's own canonical fitter and reported once that
fit is final.

A GuidanceSection holds the composed block (its immutable rule baseline);
``rebuild(budget)`` is what the fitter calls on every budget probe - always a
refit of that original block, never of a previous probe's result - and
``observe(text)`` is called with the text the request finally carries. The
``capability.guidance`` event therefore describes the exact block sent: its
rule ids, fit drops, token count and digest. Guidance is advisory text only;
nothing here decides anything.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from kriya.capabilities.guidance import (
    GuidanceBlock,
    Operation,
    RepositoryFacts,
    Role,
    SelectionFacts,
    compose_guidance,
    file_reader,
    guidance_event_details,
    refit_guidance,
    repository_facts,
    selection_facts,
)

logger = logging.getLogger(__name__)

CAPABILITY_GUIDANCE_EVENT = "capability.guidance"
# The Developer's optional-section kind (context_budget.DEVELOPER_SECTION_ORDER).
CAPABILITY_GUIDANCE_SECTION = "capability_guidance"

# Facts used when a run's repository facts cannot be computed: an EXISTING
# repository with no build root and no Spring evidence (greenfield-only rules
# never apply; only path-grounded capabilities can be selected).
UNKNOWN_REPOSITORY_FACTS = RepositoryFacts(spring_repository=False, greenfield=False, build_roots=())


class GuidanceSection:
    """One request's guidance: its facts, its composed block and where the
    final block is reported (``on_sent(facts, block)``)."""

    def __init__(self, facts: SelectionFacts, block: GuidanceBlock,
                 on_sent: Optional[Callable[[SelectionFacts, GuidanceBlock], None]] = None) -> None:
        self.facts, self.base, self._on_sent = facts, block, on_sent
        self._built: Dict[str, GuidanceBlock] = {block.text: block}

    @property
    def text(self) -> str:
        return self.base.text

    def rebuild(self, budget: int) -> str:
        """The block refitted to ``budget`` allocator units, from the base."""
        block = refit_guidance(self.base, budget)
        self._built[block.text] = block
        return block.text

    def sent(self, text: str) -> GuidanceBlock:
        """The block whose text the request finally carries ("" = every base
        rule dropped by the fit)."""
        if text in self._built:
            return self._built[text]
        if not text:
            return refit_guidance(self.base, 0)
        raise ValueError("guidance text was not produced by this section")

    def observe(self, text: str) -> GuidanceBlock:
        block = self.sent(text)
        if self._on_sent is not None:
            self._on_sent(self.facts, block)
        return block

    def with_sink(self, on_sent: Callable[[SelectionFacts, GuidanceBlock], None]) -> "GuidanceSection":
        return GuidanceSection(self.facts, self.base, on_sent)


def compose_section(role: Role, operation: Operation, repo: Optional[RepositoryFacts], target_paths: Sequence[str],
                    read: Callable[[str], Optional[bytes]],
                    on_sent: Optional[Callable[[SelectionFacts, GuidanceBlock], None]] = None) -> GuidanceSection:
    """The guidance of one request from its selection inputs (§7)."""
    facts = selection_facts(role, operation, repo or UNKNOWN_REPOSITORY_FACTS, target_paths, read)
    return GuidanceSection(facts, compose_guidance(facts), on_sent)


def event_details(facts: SelectionFacts, block: GuidanceBlock, **extra: Any) -> Dict[str, Any]:
    return {**guidance_event_details(facts, block), **extra}


def run_repository_facts(workspace_path: str, *, frameworks: Iterable[str], dependency_graph_path: Optional[str],
                         goal: str) -> RepositoryFacts:
    """The run's RepositoryFacts (on the original workspace, before any
    bootstrap or Kriya write). Guidance never stops a run: when the facts
    cannot be computed the run proceeds with UNKNOWN_REPOSITORY_FACTS."""
    from kriya.capabilities import BUILD_ADAPTERS
    from kriya.workflow.static_checks import positively_requested_terms

    try:
        return repository_facts(
            workspace_path, frameworks=frameworks, dependency_graph_path=dependency_graph_path,
            goal_build_systems=positively_requested_terms(goal, [a.build_system for a in BUILD_ADAPTERS]),
        )
    except Exception as error:  # advisory text only; the normal output is tested
        logger.warning("Capability guidance repository facts unavailable (%s); using conservative facts", error)
        return UNKNOWN_REPOSITORY_FACTS


def developer_operation(attempt_number: int, target_paths: Sequence[str], workspace_path: str) -> Operation:
    """§6.1.1: any retry is REPAIR; a first attempt EDITs when a target exists
    in the original workspace, else CREATEs."""
    if attempt_number > 1:
        return Operation.REPAIR
    read = file_reader(workspace_path)
    return Operation.EDIT if any(read(path) is not None for path in target_paths) else Operation.CREATE
