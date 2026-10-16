"""The logical module map (full modularization, slice A1).

ONE table, ``MODULES``, assigns every ``kriya/**.py`` file to exactly one
logical module (handover/MODULARIZATION/TARGET-MODULE-MAP.md §1) and names
that module's public contract modules. The architecture tests key on this
table, not on folder names, so an "extract the contract in place" decision
(a file that stays where it is but is declared to another owner) is
enforced exactly like a physical move.

Ownership is resolved by the most specific entry: an exact file path wins
over a directory prefix (``kriya/x/``), a longer prefix over a shorter one.
A file no entry owns, or that two modules claim with the same entry, fails
``test_import_direction``: a new file must be classified (fail closed).

Files that a later slice moves physically (decision B/C in the target map)
stay owned by the module of the package they live in today, so the edge
that the move removes is a measured allowlist entry naming that slice.
Files under ``kriya/core/`` (a folder with no logical owner) are declared
to their target owner now.

The import scanner lives here too so every architecture test parses
``kriya/`` once per session (``sources()``/``trees()``/``scan()`` are cached). Edge semantics are the
ones of the Wave 1 inventory script (CURRENT-DEPENDENCY-MAP.md): module to
module; ``from a.b import c`` is an edge to ``a.b.c`` when that is a module,
else to ``a.b``; relative imports resolved against the importing package;
an import inside a function body is ``lazy`` (it still is a dependency), any
other position (module body, class body, ``if TYPE_CHECKING``) is ``top``.
"""
from __future__ import annotations

import ast
import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
PACKAGE = "kriya"


@dataclass(frozen=True)
class Module:
    owner_paths: Tuple[str, ...]  # "kriya/x/" directory prefixes or exact "kriya/x/y.py" files
    public: Tuple[str, ...]       # dotted module names other logical modules may import
    layer: int                    # DEPENDENCY-RULES.md §1 (L0 .. L6)


MODULES: Dict[str, Module] = {
    "FOUNDATION": Module(
        owner_paths=("kriya/foundation/", "kriya/__init__.py", "kriya/build_info.py", "kriya/core/tomlcompat.py",
                     "kriya/core/db.py", "kriya/core/file_stamp.py"),
        public=("kriya.foundation", "kriya.foundation.ids", "kriya.foundation.errors", "kriya.foundation.clock",
                "kriya.foundation.cancellation", "kriya.foundation.values", "kriya.foundation.build_info",
                "kriya.foundation.tomlcompat"),
        layer=0),
    "PLATFORM": Module(
        owner_paths=("kriya/platform/",),
        public=("kriya.platform", "kriya.platform.services", "kriya.platform.capabilities",
                "kriya.platform.filesystem_semantics"),
        layer=0),
    "CONTEXT": Module(
        owner_paths=("kriya/context/",),
        public=("kriya.context", "kriya.context.model", "kriya.context.workspace_identity"),
        layer=1),
    "CONFIG": Module(
        owner_paths=("kriya/config/", "kriya/core/logging_setup.py"),
        public=("kriya.config", "kriya.config.service", "kriya.config.environment", "kriya.config.config"),
        layer=1),
    "AUTHORITY": Module(
        owner_paths=("kriya/policy/", "kriya/config/authority.py", "kriya/config/authority_approval.py",
                     "kriya/control/launch_approval.py", "kriya/workflow/runtime_command_approval.py",
                     "kriya/workflow/acceptance_approval.py", "kriya/workflow/verification_binding.py",
                     "kriya/workflow/authority_bundle.py", "kriya/workflow/authority_request.py",
                     "kriya/workflow/authority_escalation.py", "kriya/mcp/invocation_approval.py",
                     "kriya/mcp/capability.py"),
        public=("kriya.policy", "kriya.policy.authority", "kriya.policy.execution", "kriya.policy.filesystem",
                "kriya.policy.egress", "kriya.policy.approval", "kriya.config.authority"),
        layer=2),
    "STATE": Module(
        owner_paths=("kriya/control/", "kriya/core/state_paths.py", "kriya/core/trace.py"),
        public=("kriya.control", "kriya.control.run_coordinator", "kriya.control.persistence",
                "kriya.control.control_store", "kriya.control.run_record", "kriya.control.history",
                "kriya.control.checkpoint", "kriya.core.state_paths"),
        layer=2),
    "EVIDENCE": Module(
        owner_paths=("kriya/core/attempt_evidence/",),
        public=("kriya.core.attempt_evidence", "kriya.core.attempt_evidence.scope",
                "kriya.core.attempt_evidence.model", "kriya.core.attempt_evidence.reader",
                "kriya.core.attempt_evidence.decisions"),
        layer=2),
    "PROVIDER": Module(
        owner_paths=("kriya/core/llm.py", "kriya/core/inference_runtime.py", "kriya/core/model_runtime.py",
                     "kriya/core/provider_contract.py", "kriya/core/completion.py", "kriya/core/token_budget.py",
                     "kriya/core/inference_settings.py", "kriya/core/model_capabilities.py",
                     "kriya/core/role_metrics.py", "kriya/core/model_routing.py",
                     "kriya/core/execution_environment.py"),
        public=("kriya.core.inference_runtime", "kriya.core.provider_contract", "kriya.core.llm"),
        layer=2),
    "REPOSITORY": Module(
        owner_paths=("kriya/repository/",),
        public=("kriya.repository", "kriya.repository.port", "kriya.repository.atomic_write",
                "kriya.repository.commit", "kriya.repository.snapshot", "kriya.repository.worktree",
                "kriya.repository.git"),
        layer=2),
    "EXECUTION": Module(
        owner_paths=("kriya/tools/", "kriya/capabilities/", "kriya/static_analysis/"),
        public=("kriya.tools.execution_port", "kriya.tools.process", "kriya.tools.containment",
                "kriya.tools.validate", "kriya.capabilities.ports"),
        layer=2),
    "TOOLBOX": Module(
        owner_paths=("kriya/toolbox/", "kriya/plugins/", "kriya/mcp/", "kriya/tools/tool.py", "kriya/tools/service.py",
                     "kriya/tools/search.py", "kriya/tools/web.py", "kriya/core/registry.py"),
        public=("kriya.toolbox", "kriya.toolbox.registry", "kriya.toolbox.tool", "kriya.mcp.mcp"),
        layer=2),
    "SKILLS": Module(
        owner_paths=("kriya/skills/",),
        public=("kriya.skills", "kriya.skills.registry", "kriya.skills.service"),
        layer=2),
    "KNOWLEDGE": Module(
        owner_paths=("kriya/knowledge/", "kriya/tools/knowledge.py", "kriya/memory/learned_knowledge.py"),
        public=("kriya.knowledge", "kriya.knowledge.guard", "kriya.knowledge.packs", "kriya.knowledge.lookup_policy"),
        layer=2),
    "RETRIEVAL": Module(
        owner_paths=("kriya/code_intel/", "kriya/analyzer/", "kriya/memory/"),
        public=("kriya.code_intel.service", "kriya.analyzer.analyzer", "kriya.memory.vector", "kriya.memory.embedding"),
        layer=2),
    "KERNEL": Module(
        owner_paths=("kriya/workflow/", "kriya/agents/", "kriya/prompt/", "kriya/core/__init__.py",
                     "kriya/core/kernel.py", "kriya/core/events.py"),
        public=("kriya.workflow", "kriya.workflow.workflow", "kriya.workflow.workflow_controller",
                "kriya.workflow.plan_executor", "kriya.workflow.execution_plan", "kriya.workflow.state",
                "kriya.workflow.failure", "kriya.workflow.plan_schema", "kriya.agents.agent", "kriya.core",
                "kriya.core.kernel"),
        layer=3),
    "QUALIFICATION": Module(
        owner_paths=("kriya/qualification/", "kriya/core/model_qualification.py", "kriya/core/qualification_identity.py",
                     "kriya/core/model_certification.py", "kriya/core/release_candidate.py",
                     "kriya/core/release_identity.py", "kriya/workflow/context_certification.py"),
        public=("kriya.qualification", "kriya.qualification.service", "kriya.qualification.identity"),
        layer=4),
    "APPLICATION": Module(
        owner_paths=("kriya/application/",),
        public=("kriya.application", "kriya.application.services", "kriya.application.compose"),  # compose: the B4 composition entry
        layer=4),
    "CLIENT": Module(
        owner_paths=("kriya/client/",),
        public=("kriya.client", "kriya.client.contract", "kriya.client.local"),
        layer=5),
    "ADAPTER-KUP": Module(
        owner_paths=("kriya/kup/",),
        public=("kriya.kup.serve", "kriya.kup.cli_ops", "kriya.kup.policy", "kriya.kup.inspect"),
        layer=6),
    "ADAPTER-CLI": Module(
        owner_paths=("kriya/cli.py", "kriya/cli_commands/", "kriya/repl.py", "kriya/routing.py", "kriya/cli_output.py",
                     "kriya/distribution.py", "kriya/production_doctor.py"),
        public=("kriya.cli",),
        layer=6),
    "METRICS": Module(
        owner_paths=("kriya/metrics/",),
        public=("kriya.metrics.report",),
        layer=6),
}

# DEPENDENCY-RULES.md §1 matrix: row module -> the modules it may import
# (through their public contract modules). Intra-module imports are always
# allowed and never checked against ``public``.
_L2_L3 = ("AUTHORITY", "STATE", "EVIDENCE", "PROVIDER", "REPOSITORY", "EXECUTION", "TOOLBOX", "SKILLS",
          "KNOWLEDGE", "RETRIEVAL", "KERNEL")
_BASE = ("FOUNDATION", "PLATFORM", "CONTEXT", "CONFIG")
ALLOWED: Dict[str, FrozenSet[str]] = {
    "FOUNDATION": frozenset(),
    "PLATFORM": frozenset({"FOUNDATION"}),
    "CONTEXT": frozenset({"FOUNDATION", "PLATFORM"}),
    "CONFIG": frozenset({"FOUNDATION", "PLATFORM", "CONTEXT", "AUTHORITY"}),
    "AUTHORITY": frozenset({*_BASE, "STATE", "EVIDENCE", "REPOSITORY"}),
    "STATE": frozenset({*_BASE, "AUTHORITY", "EVIDENCE", "REPOSITORY"}),
    "EVIDENCE": frozenset({*_BASE, "STATE", "REPOSITORY"}),
    "PROVIDER": frozenset({*_BASE, "AUTHORITY", "EVIDENCE"}),
    "REPOSITORY": frozenset({*_BASE, "AUTHORITY", "EVIDENCE", "EXECUTION"}),
    "EXECUTION": frozenset({*_BASE, "AUTHORITY", "EVIDENCE", "REPOSITORY"}),
    "TOOLBOX": frozenset({*_BASE, "AUTHORITY", "STATE", "EVIDENCE", "EXECUTION"}),
    "SKILLS": frozenset({*_BASE, "AUTHORITY", "REPOSITORY", "RETRIEVAL"}),
    "KNOWLEDGE": frozenset({*_BASE, "AUTHORITY", "PROVIDER", "SKILLS", "RETRIEVAL"}),
    "RETRIEVAL": frozenset({*_BASE, "PROVIDER", "REPOSITORY"}),
    "KERNEL": frozenset({*_BASE, *_L2_L3[:-1]}),
    "QUALIFICATION": frozenset({*_BASE, *_L2_L3}),
    "APPLICATION": frozenset({*_BASE, *_L2_L3, "QUALIFICATION"}),
    "CLIENT": frozenset({"FOUNDATION", "CONTEXT", "CONFIG", "APPLICATION"}),
    "ADAPTER-KUP": frozenset({*_BASE, "CLIENT"}),
    "ADAPTER-CLI": frozenset({*_BASE, *_L2_L3, "QUALIFICATION", "APPLICATION", "CLIENT", "ADAPTER-KUP",
                              "METRICS"}),
    "METRICS": frozenset({*_BASE, "STATE", "EVIDENCE"}),
}
# The matrix footnotes (DEPENDENCY-RULES.md §1, ¹-⁸): the edge is allowed only
# into these target modules. Footnote 8 (ADAPTER-CLI into L2-L4) is the
# composition root: Kernel, LLMClient, WorkflowEngine; every other such CLI
# import is an ALLOWLIST entry removed by O2.
_CLI_COMPOSITION = ("kriya.core.kernel", "kriya.core.llm", "kriya.workflow.workflow")
RESTRICTED: Dict[Tuple[str, str], Tuple[str, ...]] = {
    ("CONFIG", "AUTHORITY"): ("kriya.config.authority", "kriya.config.authority_approval"),
    ("AUTHORITY", "STATE"): ("kriya.control.launch_approval", "kriya.control.persistence"),
    ("EVIDENCE", "STATE"): ("kriya.core.state_paths",),
    ("PROVIDER", "AUTHORITY"): ("kriya.policy.egress",),
    ("REPOSITORY", "EXECUTION"): ("kriya.tools.process",),
    ("SKILLS", "RETRIEVAL"): ("kriya.analyzer.analyzer",),
    ("KNOWLEDGE", "PROVIDER"): ("kriya.core.llm",),
    ("RETRIEVAL", "PROVIDER"): ("kriya.core.llm",),
    **{("ADAPTER-CLI", target): _CLI_COMPOSITION for target in (*_L2_L3, "QUALIFICATION")},
}


# ----------------------------------------------------------------- scanning

@dataclass(frozen=True)
class Edge:
    source: str  # dotted importing module
    target: str  # dotted imported module
    lazy: bool   # True only when EVERY import of target in source sits in a function body


def module_name(relpath: str) -> str:
    name = relpath[:-3].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def python_files(root: Path = REPO) -> List[str]:
    files = []
    for directory, dirs, names in os.walk(root / PACKAGE):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        files.extend(os.path.relpath(os.path.join(directory, n), root).replace(os.sep, "/")
                     for n in names if n.endswith(".py"))
    return sorted(files)


def _import_targets(tree: ast.AST, source: str, is_package: bool, modules: Set[str]) -> Dict[str, bool]:
    """target -> lazy for every kriya import in one module."""
    found: Dict[str, bool] = {}

    def add(target: str, in_function: bool) -> None:
        found[target] = found.get(target, True) and in_function

    def walk(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, True)
                continue
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == PACKAGE or alias.name.startswith(PACKAGE + "."):
                        add(alias.name, in_function)
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    base = source.split(".") if is_package else source.split(".")[:-1]
                    base = base[: len(base) - (child.level - 1)]
                    target = ".".join(base + ([child.module] if child.module else []))
                else:
                    target = child.module or ""
                if target == PACKAGE or target.startswith(PACKAGE + "."):
                    for alias in child.names:
                        sub = f"{target}.{alias.name}"
                        add(sub if sub in modules else target, in_function)
            walk(child, in_function)

    walk(tree, False)
    return found


def parse_sources(sources: Mapping[str, str]) -> Dict[str, ast.Module]:
    return {path: ast.parse(text, filename=path) for path, text in sources.items()}


def scan_trees(trees_by_path: Mapping[str, ast.Module]) -> Tuple[Edge, ...]:
    """Edges of a {relpath: parsed module} tree."""
    modules = {module_name(path) for path in trees_by_path}
    edges = []
    for path in sorted(trees_by_path):
        source = module_name(path)
        targets = _import_targets(trees_by_path[path], source, path.endswith("__init__.py"), modules)
        edges.extend(Edge(source, target, lazy) for target, lazy in sorted(targets.items()))
    return tuple(edges)


def scan_sources(sources: Mapping[str, str]) -> Tuple[Edge, ...]:
    """Edges of a {relpath: source text} tree (the self-checks feed planted trees)."""
    return scan_trees(parse_sources(sources))


@functools.lru_cache(maxsize=1)
def sources() -> Dict[str, str]:
    """relpath -> source text of every kriya file, read once per session."""
    return {path: (REPO / path).read_text(encoding="utf-8") for path in python_files()}


@functools.lru_cache(maxsize=1)
def trees() -> Dict[str, ast.Module]:
    """relpath -> parsed module of every kriya file: each file is parsed ONCE
    per session for every architecture test (import edges, direct-access
    owners, kernel purity, facades). Callers must not mutate the trees."""
    return parse_sources(sources())


@functools.lru_cache(maxsize=1)
def scan() -> Tuple[Edge, ...]:
    """Every kriya import edge at the current tree."""
    return scan_trees(trees())


# ---------------------------------------------------------------- ownership

def owners(relpath: str, modules: Mapping[str, Module] = MODULES) -> List[str]:
    """The logical modules claiming ``relpath`` with its most specific entry."""
    best: List[Tuple[int, str]] = []
    for name, module in modules.items():
        for entry in module.owner_paths:
            if entry == relpath or (entry.endswith("/") and relpath.startswith(entry)):
                best.append((len(entry) + (1 if entry == relpath else 0), name))
    if not best:
        return []
    top = max(score for score, _ in best)
    return sorted(name for score, name in best if score == top)


def owner_table_for(files: Iterable[str], modules: Mapping[str, Module] = MODULES) -> Dict[str, Optional[str]]:
    """dotted module -> its logical module, or None when no entry or two
    entries claim the file (the ownership test names those files)."""
    table: Dict[str, Optional[str]] = {}
    for path in files:
        claimed = owners(path, modules)
        table[module_name(path)] = claimed[0] if len(claimed) == 1 else None
    return table


@functools.lru_cache(maxsize=1)
def owner_table() -> Dict[str, Optional[str]]:
    return owner_table_for(python_files())


def logical(module: str, table: Mapping[str, Optional[str]]) -> Optional[str]:
    """The logical module of a file module. A target that is not a file is
    resolved to its nearest enclosing package that is one (an import of a
    name the scanner could not resolve to a module); a file never borrows
    its package's owner."""
    while module:
        if module in table:
            return table[module]
        module = module.rpartition(".")[0]
    return None
