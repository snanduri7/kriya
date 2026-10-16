"""KERNEL purity (full modularization, slice A2; DEPENDENCY-RULES.md §1 hard
forbiddens, §2 ``test_kernel_purity``, §4.6; SLICE-PLAN.md §0 budget rule).

KERNEL = the logical module of ``module_map.MODULES`` (kriya/workflow,
kriya/agents, kriya/prompt, core/kernel.py, core/events.py, core/__init__.py),
resolved per file, never by folder prefix. A KERNEL module:
- imports nothing from ADAPTER-CLI, ADAPTER-KUP, CLIENT, APPLICATION,
  METRICS, QUALIFICATION (resolved through the logical module of the
  target, so ``core.model_qualification`` counts as QUALIFICATION today);
- contains no provider name, no plugin tool name used as a registry key, no
  configured MCP server name and no skill name as a string literal;
- never reads ``os.environ``/``Path.home()`` and never imports ``subprocess``.
Every current exception is MEASURED, shrink-only, names its removal slice,
and carries the SAME slice as the matching entry of
``test_import_direction.ALLOWLIST`` / ``test_direct_access_owners.ALLOWLIST``.

Budget rule (owner directive 2): the 1800 s human-wait allowance is ONE
documented constant ``HUMAN_WAIT_ALLOWANCE_SECONDS`` owned by
``kriya/workflow/budget_policy.py`` (created in H2/H4). No other module in
kriya/workflow, kriya/core or kriya/config contains that name or a bare
``1800`` numeric literal. Vacuous-safe before H2: the owner does not exist
yet and the rule only constrains everyone else.
"""
from __future__ import annotations

import ast
import glob
import re
from typing import Dict, FrozenSet, Iterator, List, Mapping, Optional, Set, Tuple

import yaml

from . import access_sites as sites
from . import module_map as mm
from . import test_direct_access_owners as direct
from . import test_import_direction as direction

FORBIDDEN_TARGETS = frozenset({"ADAPTER-CLI", "ADAPTER-KUP", "CLIENT", "APPLICATION", "METRICS", "QUALIFICATION"})

# (KERNEL module, target module) -> (removal slice, reason); MEASURED at 5981965.
FORBIDDEN_IMPORT_ALLOWLIST: Dict[Tuple[str, str], Tuple[str, str]] = {
    (source, "kriya.core.model_qualification"): ("P2", "kernel reads qualification limits directly; decided in P2")
    for source in ("kriya.workflow.attempt", "kriya.workflow.context_budget", "kriya.workflow.model_transition",
                   "kriya.workflow.resume_fingerprints", "kriya.workflow.workflow")
}

# KERNEL module -> removal slice, per mechanism (the slice of the same module's
# entry in test_direct_access_owners.ALLOWLIST).
KERNEL_MECHANISM_ALLOWLIST: Dict[str, Dict[str, str]] = {
    # D3: the kernel's env/home reads (acceptance_jvm/checkpoint/milestone_completion/workflow) go through kriya.config.environment.
    "os_environ": {},
    "home": {},
    "subprocess": {"kriya.workflow.checkpoint": "M2",  # M1: file_integrity's untracked_repository_paths moved to repository.snapshot
                   "kriya.workflow.milestone_completion": "M3", "kriya.workflow.named_test_oracle": "L2",
                   "kriya.workflow.worktree": "M2"},
}

# KERNEL module -> (site count, removal slice, reason) for a skill / MCP server /
# plugin-tool name in a string literal.
NAME_LITERAL_ALLOWLIST: Dict[str, Tuple[int, str, str]] = {
    "kriya.workflow.outbound_lookup": (1, "J2", "the public-technology word 'qpid' equals the skill name 'qpid'; "
                                                "the lookup vocabulary moves to knowledge.lookup_policy"),
}

BUDGET_OWNER = "kriya.workflow.budget_policy"
BUDGET_PACKAGES = ("kriya.workflow", "kriya.core", "kriya.config")
BUDGET_NAME = "HUMAN_WAIT_ALLOWANCE_SECONDS"
BUDGET_LITERAL = 1800
# (module, enclosing symbol) -> (removal slice, reason). Keyed by symbol, not line.
BUDGET_LITERAL_ALLOWLIST: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("kriya.workflow.goal_facts", "MAX_BLOCK_CHARS"): (
        "H4", "a character limit, not a time budget: H4 reclassifies it (documented non-budget exemption "
              "or a distinct value) when HUMAN_WAIT_ALLOWANCE_SECONDS lands in budget_policy.py"),
}


def kernel_modules(table: Mapping[str, Optional[str]]) -> Set[str]:
    return {module for module, owner in table.items() if owner == "KERNEL"}


# ------------------------------------------------------- name vocabularies

def skill_names() -> FrozenSet[str]:
    """Every skill.yaml ``name`` under skills/, tests/fixtures and demo-v2."""
    names = set()
    for pattern in ("skills/*/skill.yaml", "tests/fixtures/**/skill.yaml", "demo-v2/**/skill.yaml"):
        for path in glob.glob(str(mm.REPO / pattern), recursive=True):
            with open(path, encoding="utf-8") as handle:
                names.add(yaml.safe_load(handle)["name"])
    return frozenset(names)


def mcp_server_names() -> FrozenSet[str]:
    """The ``mcp.<server>`` keys of the tracked example configurations."""
    names = set()
    for name in ("mcp_config.yaml", "local_mcp_config.yaml"):
        with open(mm.REPO / name, encoding="utf-8") as handle:
            names |= set(((yaml.safe_load(handle) or {}).get("mcp") or {}))
    return frozenset(names)


def plugin_tool_names() -> FrozenSet[str]:
    """``registry.register("tool", "<name>", ...)`` in plugins/core_tools."""
    tree = ast.parse((mm.REPO / "plugins" / "core_tools" / "__init__.py").read_text(encoding="utf-8"))
    return frozenset(node.args[1].value for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "register" and len(node.args) >= 2
                     and all(isinstance(arg, ast.Constant) for arg in node.args[:2]) and node.args[0].value == "tool")


def _word(names: FrozenSet[str]) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_.-])(" + "|".join(map(re.escape, sorted(names))) + r")(?![A-Za-z0-9_.-])")


def name_literal_sites(tree: ast.Module, names: FrozenSet[str], tools: FrozenSet[str]) -> Set[int]:
    """A skill / MCP server name as a word of a (non-docstring) string literal,
    or a plugin tool name used as a registry key (``x.registry.<m>("tool",
    "<name>")`` or compared with a ``*tool*`` name)."""
    pattern = _word(names)
    lines = {node.lineno for node in sites.strings(tree) if pattern.search(node.value)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "tool" and isinstance(node.args[1], ast.Constant) \
                and node.args[1].value in tools:
            lines.add(node.lineno)
        elif isinstance(node, ast.Compare) and len(node.comparators) == 1:
            sides = (node.left, node.comparators[0])
            if any(isinstance(side, ast.Constant) and side.value in tools for side in sides) \
                    and any("tool" in ast.unparse(side).lower() for side in sides
                            if not isinstance(side, ast.Constant)):
                lines.add(node.lineno)
    return lines


# --------------------------------------------------------- budget literal

def _symbols(tree: ast.Module) -> Iterator[Tuple[ast.AST, str]]:
    """(node, enclosing symbol): the nearest enclosing assignment target name,
    else the enclosing function/class name, else ``<module>``."""
    stack: List[Tuple[ast.AST, str]] = [(tree, "<module>")]
    while stack:
        node, symbol = stack.pop()
        yield node, symbol
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbol = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            named = [target for target in targets if isinstance(target, (ast.Name, ast.Attribute))]
            if named:
                symbol = named[0].id if isinstance(named[0], ast.Name) else named[0].attr
        stack.extend((child, symbol) for child in ast.iter_child_nodes(node))


def budget_sites(tree: ast.Module) -> Set[str]:
    found = set()
    for node, symbol in _symbols(tree):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value == BUDGET_LITERAL:
            found.add(symbol)
        elif (isinstance(node, ast.Name) and node.id == BUDGET_NAME) \
                or (isinstance(node, ast.Attribute) and node.attr == BUDGET_NAME) \
                or (isinstance(node, ast.alias) and BUDGET_NAME in (node.name, node.asname)) \
                or (isinstance(node, ast.Constant) and node.value == BUDGET_NAME):
            found.add(BUDGET_NAME)
    return found


def budget_violations(trees: Mapping[str, ast.Module]) -> Set[Tuple[str, str]]:
    return {(module, symbol) for path, tree in trees.items()
            for module in [mm.module_name(path)]
            if module.startswith(BUDGET_PACKAGES) and module != BUDGET_OWNER
            for symbol in budget_sites(tree)}


# --------------------------------------------------------------- measured

def forbidden_imports(edges, table) -> Set[Tuple[str, str]]:
    return {(edge.source, edge.target) for edge in edges
            if table.get(edge.source) == "KERNEL" and mm.logical(edge.target, table) in FORBIDDEN_TARGETS}


def kernel_mechanisms(trees, table) -> Dict[str, Set[str]]:
    kernel = kernel_modules(table)
    found: Dict[str, Set[str]] = {}
    for mechanism, finder in (("os_environ", sites.environ_sites), ("home", sites.home_sites),
                              ("subprocess", sites.subprocess_sites)):
        found[mechanism] = {module for module in sites.counts(finder, trees) if module in kernel}
    found["subprocess"] |= {module for module in sites.counts(sites.subprocess_import_sites, trees) if module in kernel}
    return found


def test_kernel_imports_no_adapter_client_application_metrics_or_qualification():
    found = forbidden_imports(mm.scan(), mm.owner_table())
    assert found == set(FORBIDDEN_IMPORT_ALLOWLIST), "KERNEL must not import an upper layer (DEPENDENCY-RULES §1)"
    for pair, (slice_id, reason) in FORBIDDEN_IMPORT_ALLOWLIST.items():
        assert reason and direction.ALLOWLIST[pair][0] == slice_id, f"{pair}: same slice as test_import_direction"
        assert slice_id in direction.SLICES and slice_id not in direction.DONE_SLICES


def test_kernel_contains_no_provider_name():
    table = mm.owner_table()
    kernel = kernel_modules(table)
    assert {module for module in sites.counts(sites.provider_word_sites) if module in kernel} == set()


def test_kernel_names_no_skill_mcp_server_or_plugin_tool_registry_key():
    names = skill_names() | mcp_server_names()
    tools = plugin_tool_names()
    assert {"ignite-java17", "mock_svc"} <= names and {"filesystem", "shell", "git"} <= tools  # never vacuous
    kernel = kernel_modules(mm.owner_table())
    found = {mm.module_name(path): len(lines) for path, tree in mm.trees().items()
             if mm.module_name(path) in kernel for lines in [name_literal_sites(tree, names, tools)] if lines}
    assert found == {module: entry[0] for module, entry in NAME_LITERAL_ALLOWLIST.items()}
    for module, (_count, slice_id, reason) in NAME_LITERAL_ALLOWLIST.items():
        assert reason and slice_id in direction.SLICES and slice_id not in direction.DONE_SLICES, module


def test_kernel_reads_no_environment_or_home_and_spawns_no_subprocess():
    table = mm.owner_table()
    found = kernel_mechanisms(mm.trees(), table)
    assert found == {mechanism: set(entries) for mechanism, entries in KERNEL_MECHANISM_ALLOWLIST.items()}
    for mechanism, entries in KERNEL_MECHANISM_ALLOWLIST.items():
        for module, slice_id in entries.items():
            assert direct.ALLOWLIST[mechanism][module][1] == slice_id, (
                f"{mechanism}/{module}: same removal slice as test_direct_access_owners")


def test_the_human_wait_allowance_literal_lives_only_in_budget_policy():
    found = budget_violations(mm.trees())
    assert found == set(BUDGET_LITERAL_ALLOWLIST), (
        f"{BUDGET_NAME} / a bare {BUDGET_LITERAL} belongs to {BUDGET_OWNER} only (owner directive 2)")
    for key, (slice_id, reason) in BUDGET_LITERAL_ALLOWLIST.items():
        assert reason and slice_id in direction.SLICES and slice_id not in direction.DONE_SLICES, key


# --------------------------------------------------------------- self-checks

_PLANTED = {
    "kriya/__init__.py": "",
    "kriya/cli.py": "",
    "kriya/workflow/__init__.py": "",
    "kriya/workflow/budget_policy.py": "HUMAN_WAIT_ALLOWANCE_SECONDS = 1800\n",
    "kriya/workflow/planted.py": (
        '"""ollama, ignite-java17 and 1800 in a docstring never count."""\n'
        "import os, subprocess\n"
        "from pathlib import Path\n"
        "from kriya.workflow.budget_policy import HUMAN_WAIT_ALLOWANCE_SECONDS\n"
        "WAIT = 1800.0\n"
        "def f(kernel, tool_name):\n"
        "    from kriya import cli\n"
        "    os.environ.get('X'); Path.home()\n"
        "    kernel.registry.get('tool', 'shell')\n"
        "    if tool_name == 'git':\n"
        "        return 'ollama', 'ignite-java17', 'mock_svc', cli\n"
        "    return 'org.apache.qpid.jms', 'qpid-client', 1800\n"),
    "kriya/core/__init__.py": "",
    "kriya/core/clean.py": "LIMIT = 1799\nNAME = 'HUMAN_WAIT_ALLOWANCE'\n",
}


def test_the_purity_checks_detect_every_planted_form():
    trees = mm.parse_sources(_PLANTED)
    table = mm.owner_table_for(_PLANTED)
    planted = "kriya.workflow.planted"
    assert forbidden_imports(mm.scan_trees(trees), table) == {(planted, "kriya.cli")}
    assert sites.counts(sites.provider_word_sites, trees) == {planted: 1}
    assert len(name_literal_sites(trees["kriya/workflow/planted.py"], frozenset({"ignite-java17", "mock_svc", "qpid"}),
                                  frozenset({"shell", "git"}))) == 3  # registry key, compare, skill+mcp line
    assert kernel_mechanisms(trees, table) == {"os_environ": {planted}, "home": {planted}, "subprocess": {planted}}
    assert budget_violations(trees) == {(planted, BUDGET_NAME), (planted, "WAIT"), (planted, "f")}


def test_the_allowlisted_budget_literal_is_really_measured():
    # Removing the entry must fail the rule: the measured site is real, not stale.
    assert ("kriya.workflow.goal_facts", "MAX_BLOCK_CHARS") in budget_violations(mm.trees())
    assert BUDGET_OWNER not in {mm.module_name(path) for path in mm.python_files()}  # vacuous-safe until H2
