"""C-05 / R-05 (slice S3): every opener of a project store goes through the store API.

A project store (``dependency_graph.db``, ``vector_index.db``, the learned-knowledge store) is handed out by RETRIEVAL only
(``kriya/memory/project_store.py``: ``project_store_path`` / ``project_store_status`` to a reader, ``claim_project_store`` /
``claim_project_knowledge_store`` to a writer), which checks the identity stamp. A module that builds the path itself
(``os.path.join(config.paths.memory, "dependency_graph.db")``) reads or writes whatever file is there, whoever's it is: that was the defect
(MEASURED, A5 P2-P4), and two such readers were still present when this guard was written (``enforce_loop`` and ``stage_analysis``, the goal-facts
block).

Two inventories over the production tree, both closed:
1. a store FILE NAME literal appears only as a direct argument of a store-API call, or at an explained site;
2. ``<config>.paths.memory`` is read only at an explained site (none of them builds a project-store path).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = (ROOT / "kriya", ROOT / "plugins" / "core_tools")

STORE_NAMES = {"dependency_graph.db", "vector_index.db", "web_knowledge.db"}
STORE_API = {"project_store_path", "project_store_status", "claim_project_store", "claim_project_knowledge_store"}

# (file, enclosing scope) -> why a store name literal may stand outside a store-API call there
NAME_SITES = {
    ("kriya/memory/project_store.py", "<module>"): "the store mechanism's own constants (DEPENDENCY_GRAPH_STORE, VECTOR_INDEX_STORE)",
    ("kriya/memory/learned_knowledge.py", "<module>"): "the learned-knowledge owner's constant; every use of it is a store-API call in this module",
}

# (file, enclosing scope) -> why the configured memory directory may be read there
MEMORY_SITES = {
    ("kriya/memory/project_store.py", "project_store_status"): "the store API: READ side (stamp checked before the path is handed out)",
    ("kriya/memory/project_store.py", "_claim"): "the store API: WRITE side (stamp checked or written before the path is handed out)",
    ("kriya/cli.py", "doctor"): "display of the configured directory; opens nothing",
    ("kriya/workflow/context_certification.py", "_index_repository"):
        "builds an isolated certification configuration (assigns the field and creates the directory); its stores are claimed through the API",
}
CONFIG_PACKAGE = "kriya/config/"


def _sources():
    for package in PACKAGES:
        for path in sorted(package.rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def _call_name(node):
    function = node.func
    return function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", None)


class _Scan(ast.NodeVisitor):
    def __init__(self, relative, tree):
        self.relative, self.stack, self.docstrings = relative, [], _docstrings(tree)
        self.api_arguments = {id(argument) for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node) in STORE_API
                              for argument in node.args}
        self.names, self.memory = [], []

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def _where(self):
        functions = [name for name in self.stack]
        return functions[-1] if functions else "<module>"

    def visit_Constant(self, node):
        if isinstance(node.value, str) and node.value in STORE_NAMES and id(node) not in self.docstrings and id(node) not in self.api_arguments:
            self.names.append((self.relative, self._where(), node.lineno))

    def visit_Attribute(self, node):
        if node.attr == "memory" and isinstance(node.value, ast.Attribute) and node.value.attr == "paths" and isinstance(node.ctx, ast.Load):
            self.memory.append((self.relative, self._where(), node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node):
        # getattr(<paths>, "memory", ...): the same read, spelled dynamically
        if _call_name(node) == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "memory":
            self.memory.append((self.relative, self._where(), node.lineno))
        self.generic_visit(node)


def _scan(sources):
    names, memory = [], []
    for relative, source in sources:
        tree = ast.parse(source)
        scan = _Scan(relative, tree)
        scan.visit(tree)
        names.extend(scan.names)
        memory.extend(scan.memory)
    return names, memory


# getattr(...,"memory") reads that only test "is there a configured memory directory" before asking the store API, or exclude it from a walk
DYNAMIC_MEMORY_SITES = {
    ("kriya/workflow/enforce_loop.py", "run"): "guard only (is a memory directory configured): the path comes from project_store_path",
    ("kriya/workflow/stage_analysis.py", "analyze_work_unit"): "guard only: the path comes from project_store_path",
    ("kriya/workflow/candidate_transaction.py", "_code_intelligence_for"): "guard only: the path comes from project_store_path",
    ("kriya/analyzer/analyzer.py", "kriya_owned_index_roots"): "names the directory so the repository walk EXCLUDES it; opens nothing",
    ("kriya/core/state_paths.py", "legacy_shared_memory_stores"): "compares the configured directory with the legacy one; opens nothing",
}


def test_a_store_name_stands_only_in_a_store_api_call_or_at_an_explained_site():
    names, _memory = _scan(_sources())
    unexplained = sorted(f"{relative}:{line} in {scope}" for relative, scope, line in names if (relative, scope) not in NAME_SITES)
    assert unexplained == [], (
        "a module names a project store outside the store API. Take the path from kriya.memory.project_store (project_store_path for a reader, "
        "claim_project_store for a writer): a path built by hand reads or writes whatever file is there, whoever's it is: " + "; ".join(unexplained))
    present = {(relative, scope) for relative, scope, _line in names}
    assert sorted(set(NAME_SITES) - present) == [], "NAME_SITES lists a site that no longer names a store: remove it"


def test_the_configured_memory_directory_is_read_only_at_explained_sites():
    _names, memory = _scan(_sources())
    allowed = {**MEMORY_SITES, **DYNAMIC_MEMORY_SITES}
    unexplained = sorted(f"{relative}:{line} in {scope}" for relative, scope, line in memory
                         if (relative, scope) not in allowed and not relative.startswith(CONFIG_PACKAGE))
    assert unexplained == [], (
        "a module reads paths.memory directly. A project store's path comes from kriya.memory.project_store; the release cache's directory from "
        "kriya.config.resolution.global_store_directory. Add the site with its reason only if it opens no store: " + "; ".join(unexplained))
    present = {(relative, scope) for relative, scope, _line in memory}
    assert sorted(set(allowed) - present) == [], "an explained site no longer reads paths.memory: remove it"


def test_the_guard_sees_a_hand_built_store_path_and_a_direct_memory_read():
    """Negative control: the two shapes of the defect, planted, are both reported; the API shape is not."""
    planted = [
        ("kriya/workflow/planted.py",
         "import os\n"
         "def reader(config):\n"
         "    return os.path.join(config.paths.memory, 'dependency_graph.db')\n"
         "def dynamic(config):\n"
         "    return os.path.join(getattr(config.paths, 'memory', None), 'vector_index.db')\n"
         "def good(config):\n"
         "    '''dependency_graph.db is only named in this docstring.'''\n"
         "    return project_store_path(config, 'dependency_graph.db')\n"),
    ]
    names, memory = _scan(planted)
    assert [(scope, line) for _relative, scope, line in names] == [("reader", 3), ("dynamic", 5)]
    assert [(scope, line) for _relative, scope, line in memory] == [("reader", 3), ("dynamic", 5)]
