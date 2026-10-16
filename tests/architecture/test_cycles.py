"""Import cycles (full modularization, slice A1; DEPENDENCY-RULES.md §2).

(a) Top-level imports only: the strongly connected components of the module
graph must be exactly ``CYCLE_BASELINE`` (the two 2-cycles MEASURED in
CURRENT-DEPENDENCY-MAP.md §3). A new cycle fails; a removed one fails until
it is deleted here, so the baseline only shrinks.
(b) All imports (function-local included): the SCCs, as members per package,
must equal ``baseline.json``. Growth fails; a shrink fails until the slice
that achieved it regenerates the file:

    .venv/bin/python -m tests.architecture.test_cycles --write-baseline

Q1 sets both to empty, except documented intra-module cycles.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Set

from . import module_map as mm

BASELINE = Path(__file__).with_name("baseline.json")
CYCLE_BASELINE: FrozenSet[FrozenSet[str]] = frozenset({
    frozenset({"kriya.tools.toolchain_identity", "kriya.tools.containment"}),
    frozenset({"kriya.workflow.proposal_binding", "kriya.workflow.review_context"}),
})


def strongly_connected(graph: Mapping[str, Iterable[str]]) -> List[FrozenSet[str]]:
    """Tarjan, iterative; only components with more than one member."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    found: List[FrozenSet[str]] = []
    successors = {node: sorted(targets) for node, targets in graph.items()}
    counter = 0
    for root in sorted(successors):
        if root in index:
            continue
        work = [(root, iter(successors[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(successors.get(child, ()))))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(frozenset(component))
    return found


def graphs(edges: Iterable[mm.Edge], modules: Set[str]):
    top: Dict[str, Set[str]] = collections.defaultdict(set)
    every: Dict[str, Set[str]] = collections.defaultdict(set)
    for edge in edges:
        if edge.target in modules and edge.target != edge.source:
            every[edge.source].add(edge.target)
            if not edge.lazy:
                top[edge.source].add(edge.target)
    return top, every


def package(module: str) -> str:
    """The Wave 1 inventory's package key: ``kriya.<pkg>``, ``kriya(top)`` for
    a top-level module, ``kriya`` for the package root."""
    parts = module.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else ("kriya(top)" if len(parts) == 2 else "kriya")


def measure() -> dict:
    files = mm.python_files()
    modules = {mm.module_name(path) for path in files}
    edges = mm.scan()
    top, every = graphs(edges, modules)
    components = sorted(strongly_connected(every), key=lambda c: (-len(c), sorted(c)))
    return {
        "files": len(files),
        "edges": len(edges),
        "top_level_sccs": sorted(sorted(c) for c in strongly_connected(top)),
        "all_import_scc_sizes": [len(c) for c in components],
        "all_import_scc_members_per_package": [dict(sorted(collections.Counter(map(package, c)).items()))
                                               for c in components],
        "all_import_sccs": [sorted(c) for c in components],
    }


def test_top_level_cycles_are_exactly_the_baseline():
    modules = {mm.module_name(path) for path in mm.python_files()}
    top, _ = graphs(mm.scan(), modules)
    found = set(strongly_connected(top))
    assert found - CYCLE_BASELINE == set(), f"new top-level import cycle(s): {found - CYCLE_BASELINE}"
    assert CYCLE_BASELINE - found == set(), f"cycle(s) gone: delete from CYCLE_BASELINE {CYCLE_BASELINE - found}"


def test_function_local_cycles_never_grow_and_the_baseline_is_current():
    committed = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = measure()
    committed_members = sum(map(collections.Counter, committed["all_import_scc_members_per_package"]),
                            collections.Counter())
    now_members = sum(map(collections.Counter, now["all_import_scc_members_per_package"]), collections.Counter())
    grown = {p: (committed_members[p], n) for p, n in now_members.items() if n > committed_members[p]}
    assert grown == {}, f"modules in import cycles grew per package (baseline, now): {grown}"
    assert max(now["all_import_scc_sizes"], default=0) <= max(committed["all_import_scc_sizes"], default=0)
    assert now["all_import_sccs"] == committed["all_import_sccs"], (
        "the cycle baseline shrank or changed shape: regenerate it with "
        "`.venv/bin/python -m tests.architecture.test_cycles --write-baseline` and commit it")
    assert committed["top_level_sccs"] == sorted(sorted(c) for c in CYCLE_BASELINE)


def test_the_cycle_detector_separates_top_level_from_function_local_cycles():
    planted = {
        "kriya/__init__.py": "",
        "kriya/a.py": "import kriya.b\n",
        "kriya/b.py": "from kriya import a\n",
        "kriya/c.py": "from kriya import d\n",
        "kriya/d.py": "def later():\n    import kriya.c\n",
        "kriya/e.py": "import kriya.a\n",
    }
    modules = {mm.module_name(path) for path in planted}
    top, every = graphs(mm.scan_sources(planted), modules)
    assert set(strongly_connected(top)) == {frozenset({"kriya.a", "kriya.b"})}
    assert set(strongly_connected(every)) == {frozenset({"kriya.a", "kriya.b"}), frozenset({"kriya.c", "kriya.d"})}


if __name__ == "__main__" and sys.argv[1:] == ["--write-baseline"]:
    BASELINE.write_text(json.dumps(measure(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {BASELINE}")
