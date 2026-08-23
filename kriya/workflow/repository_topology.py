"""Deterministic repository physical-build-topology detection - no LLM calls,
same restraint as kriya/workflow/milestone_validation.py. Exists to answer
ONE narrow question cheaply and reliably: is this workspace already
single-module/single-entrypoint or already multi-module/multi-entrypoint -
so MilestonePlanValidator's physical-topology-preservation check (MA3.4,
below) never has to ASK the Milestone Planner "is this a multi-module
repository?" (Kriya already knows, from real evidence on disk) and never has
to guess from milestone text alone.

Deliberately NOT a general repository-architecture system (see the MA3
design doc's own "Do Not Overfit This Validator" - a repo with one pom.xml
today can still legitimately grow a second module tomorrow if the GOAL
requires it): this only detects what's needed to tell "the plan matches
what's already here" from "the plan invents something new," reusable
identically for a pre-execution check (this module, called with the
workspace as it is NOW) or a post-execution regression check (MA3.8, calling
this same function again after milestones run, diffing the two results)."""

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Same noise-directory convention as milestones.py's own
# _list_workspace_files - a cheap, dependency-free workspace scan, not
# RepositoryAnalyzer's full repo_model.
_IGNORED_DIRS = {".git", ".kriya", "__pycache__", "node_modules", "target", ".venv", "venv", "build", "dist", ".idea"}
_BUILD_MARKERS_TO_SYSTEM = (
    ("pom.xml", "maven"),
    ("build.gradle.kts", "gradle"),
    ("build.gradle", "gradle"),
    ("package.json", "npm"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
)
_MAX_JAVA_FILES_SCANNED = 500


@dataclass(frozen=True)
class RepositoryTopology:
    """`build_roots` is every directory (relative to the workspace root, "."
    for the root itself) that is independently buildable (has its own
    recognized build manifest); `modules` is that same set minus the root -
    i.e. genuine CHILD sub-projects only. A plain single-project Maven repo
    is build_roots=(".",), modules=(), is_multi_module=False; the design
    doc's own multi-module example (root/protocol/pom.xml,
    root/server/pom.xml, aggregator root/pom.xml) is
    build_roots=(".", "protocol", "server"), modules=("protocol", "server"),
    is_multi_module=True."""

    build_system: Optional[str]
    build_roots: Tuple[str, ...]
    modules: Tuple[str, ...]
    entrypoints: Tuple[str, ...]
    is_multi_module: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "build_system": self.build_system,
            "build_roots": list(self.build_roots),
            "modules": list(self.modules),
            "entrypoints": list(self.entrypoints),
            "is_multi_module": self.is_multi_module,
        }


def _root_build_marker(workspace_path: str) -> Optional[Tuple[str, str]]:
    """Returns (marker_filename, build_system) for the FIRST marker found at
    workspace root, in the fixed priority order above - a repo with both a
    pom.xml and a package.json (e.g. a Java backend with a small frontend)
    reports its primary system deterministically rather than ambiguously."""
    for marker, system in _BUILD_MARKERS_TO_SYSTEM:
        if os.path.isfile(os.path.join(workspace_path, marker)):
            return marker, system
    return None


def _parse_maven_declared_modules(pom_path: str) -> List[str]:
    """Same ElementTree namespace-stripping convention as
    kriya/tools/validate.py::get_pom_dependencies, reused here rather than
    duplicated with different logic so both stay in sync with Maven's own
    namespace behavior."""
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        modules_elem = root.find(f"{ns}modules")
        if modules_elem is None:
            return []
        return [
            mod.text.strip() for mod in modules_elem.findall(f"{ns}module")
            if mod.text and mod.text.strip()
        ]
    except Exception as e:
        logger.warning(f"Failed to parse Maven <modules> at {pom_path}: {e}")
        return []


_GRADLE_QUOTED_TOKEN = re.compile(r"""['"]([^'"]+)['"]""")


def _parse_gradle_declared_modules(settings_path: str) -> List[str]:
    """Regex, not a Groovy/Kotlin parser - settings.gradle(.kts) is a real
    build script, not a declarative format, so this only recognizes the
    conventional `include ':a', ':b'` / `include("a")` shape, matching this
    module's own "just enough" scope, not a general Gradle DSL evaluator."""
    try:
        with open(settings_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        logger.warning(f"Failed to read Gradle settings at {settings_path}: {e}")
        return []
    modules: List[str] = []
    for line in content.splitlines():
        if not line.strip().startswith("include"):
            continue
        for token in _GRADLE_QUOTED_TOKEN.findall(line):
            modules.append(token.lstrip(":").replace(":", "/"))
    return modules


def _parse_npm_workspaces(package_json_path: str) -> List[str]:
    try:
        with open(package_json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse package.json at {package_json_path}: {e}")
        return []
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])
    if not isinstance(workspaces, list):
        return []
    # Literal entries only, no glob expansion ("packages/*" won't resolve to
    # concrete names here) - deliberately not a general glob matcher.
    return [w for w in workspaces if isinstance(w, str) and "*" not in w]


def _discover_child_build_roots(workspace_path: str) -> List[str]:
    """Depth-1 only (direct subdirectories) - catches the design doc's own
    multi-module example even without an aggregator pom declaring
    <modules>, without turning into an unbounded repository crawl."""
    try:
        entries = sorted(os.listdir(workspace_path))
    except OSError:
        return []
    children = []
    for name in entries:
        if name in _IGNORED_DIRS or name.startswith("."):
            continue
        full = os.path.join(workspace_path, name)
        if os.path.isdir(full) and _root_build_marker(full) is not None:
            children.append(name)
    return children


_JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _find_java_main_entrypoints(workspace_path: str) -> List[str]:
    """Bounded scan (at most _MAX_JAVA_FILES_SCANNED .java files) for a
    `public static void main(` method, reporting each as a best-effort
    fully-qualified class name (package + filename) - the same "package it
    declares" concept kriya/workflow/milestones.py's own
    render_established_file_context() already relies on for cross-milestone
    Java grounding, reused here for consistency."""
    found: List[str] = []
    scanned = 0
    for dirpath, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for fn in filenames:
            if not fn.endswith(".java"):
                continue
            scanned += 1
            if scanned > _MAX_JAVA_FILES_SCANNED:
                return found
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            if "public static void main(" not in content:
                continue
            pkg_match = _JAVA_PACKAGE_RE.search(content)
            class_name = os.path.splitext(fn)[0]
            found.append(f"{pkg_match.group(1)}.{class_name}" if pkg_match else class_name)
    return found


def _find_npm_entrypoint(package_json_path: str) -> List[str]:
    try:
        with open(package_json_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    entrypoints = []
    for field in ("main", "bin"):
        value = data.get(field)
        if isinstance(value, str):
            entrypoints.append(value)
        elif isinstance(value, dict):
            entrypoints.extend(v for v in value.values() if isinstance(v, str))
    return entrypoints


def _find_python_main_scripts(workspace_path: str) -> List[str]:
    """Top-level (non-recursive) only, matching this detector's own bounded
    "just enough" scope for a secondary, lower-signal ecosystem here - Java
    is Kriya's own most-exercised stack (PolymorphicValidator's own marker
    priority), so it gets the deeper scan."""
    entrypoints = []
    try:
        entries = os.listdir(workspace_path)
    except OSError:
        return []
    for name in entries:
        if not name.endswith(".py"):
            continue
        path = os.path.join(workspace_path, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if re.search(r"""if\s+__name__\s*==\s*['"]__main__['"]\s*:""", content):
            entrypoints.append(name)
    return entrypoints


def detect_repository_topology(workspace_path: str) -> RepositoryTopology:
    root_marker = _root_build_marker(workspace_path)
    build_system = root_marker[1] if root_marker else None

    declared_modules: List[str] = []
    if root_marker and root_marker[1] == "maven":
        declared_modules = _parse_maven_declared_modules(os.path.join(workspace_path, "pom.xml"))
    elif root_marker and root_marker[1] == "gradle":
        for settings_name in ("settings.gradle.kts", "settings.gradle"):
            settings_path = os.path.join(workspace_path, settings_name)
            if os.path.exists(settings_path):
                declared_modules = _parse_gradle_declared_modules(settings_path)
                break
    elif root_marker and root_marker[1] == "npm":
        declared_modules = _parse_npm_workspaces(os.path.join(workspace_path, "package.json"))

    modules = tuple(sorted(set(declared_modules) | set(_discover_child_build_roots(workspace_path))))
    build_roots = ((".",) if root_marker else ()) + modules

    entrypoints = set(_find_java_main_entrypoints(workspace_path))
    root_package_json = os.path.join(workspace_path, "package.json")
    if os.path.isfile(root_package_json):
        entrypoints.update(_find_npm_entrypoint(root_package_json))
    entrypoints.update(_find_python_main_scripts(workspace_path))

    return RepositoryTopology(
        build_system=build_system,
        build_roots=build_roots,
        modules=modules,
        entrypoints=tuple(sorted(entrypoints)),
        is_multi_module=len(build_roots) > 1,
    )
