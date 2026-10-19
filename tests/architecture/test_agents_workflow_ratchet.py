"""Ratchet on the agents -> workflow imports (OI-66 / OI-73; owner decision 2026-10-19).

``kriya.agents`` is the lower layer of the pair: it must not reach back into ``kriya.workflow``. Eight imports into seven
workflow modules exist today (MEASURED). Two of those targets (context_budget, verifier_evidence) are import-CYCLE edges and are
removed by N1-CY; the other five are plain cleanup and reach empty in P2. Until then the SET of workflow modules the agents
package imports can only shrink: a new target fails here, a removed one must be deleted from the table (the table never grows).
"""
from __future__ import annotations

from typing import FrozenSet, Iterable

from . import module_map as mm

# target workflow module -> the slice that removes it from the agents package's imports
ALLOWED_TARGETS = {
    "kriya.workflow.context_budget": "N1-CY",     # OI-66 edges A and B (cycle edges)
    "kriya.workflow.verifier_evidence": "N1-CY",  # OI-66 edge C (cycle edge)
    "kriya.workflow.generation_manifest": "P2",   # OI-73
    "kriya.workflow.milestone_normalization": "P2",
    "kriya.workflow.plan_schema": "P2",
    "kriya.workflow.requirements": "P2",
    "kriya.workflow.untrusted_context": "P2",
}


def agents_workflow_targets(edges: Iterable[mm.Edge]) -> FrozenSet[str]:
    return frozenset(
        edge.target for edge in edges
        if (edge.source == "kriya.agents" or edge.source.startswith("kriya.agents."))
        and edge.target.startswith("kriya.workflow.")
    )


def test_the_agents_package_imports_no_new_workflow_module():
    new = agents_workflow_targets(mm.scan()) - frozenset(ALLOWED_TARGETS)
    assert not new, f"kriya.agents must not import kriya.workflow modules beyond the ratchet table: {sorted(new)}"


def test_the_ratchet_table_only_names_imports_that_still_exist():
    stale = frozenset(ALLOWED_TARGETS) - agents_workflow_targets(mm.scan())
    assert not stale, f"delete the removed import(s) from ALLOWED_TARGETS (the table only shrinks): {sorted(stale)}"


def test_the_ratchet_is_not_vacuous():
    """Planted trees: a new agents -> workflow import is found, a workflow -> agents import and a non-workflow import are not."""
    planted = {
        "kriya/workflow/__init__.py": "",
        "kriya/workflow/brand_new.py": "",
        "kriya/workflow/consumer.py": "import kriya.agents.thing\n",
        "kriya/agents/__init__.py": "",
        "kriya/agents/thing.py": "def f():\n    from kriya.workflow import brand_new\n    return brand_new\n",
        "kriya/agents/other.py": "import kriya.agents.thing\n",
    }
    assert agents_workflow_targets(mm.scan_sources(planted)) == {"kriya.workflow.brand_new"}
