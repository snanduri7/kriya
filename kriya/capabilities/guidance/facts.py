"""CAGC facts: what guidance selection may read (CAGC-0).

RepositoryFacts are computed once per run, on the ORIGINAL workspace (before
any greenfield bootstrap or Kriya write); SelectionFacts once per request,
from the evidence that request carries. Both are deterministic: markers,
adapter detection and Code Intelligence structure, never prose (the goal only
names build systems for a greenfield request with no target).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from kriya.analyzer.analyzer import SPRING_BOOT_FRAMEWORK
from kriya.capabilities import BUILD_ADAPTERS, LANGUAGE_ADAPTERS
from kriya.capabilities.gradle import BUILD_SCRIPT as GRADLE_BUILD_SCRIPT
from kriya.capabilities.guidance.model import Operation, RepoContext, Role
from kriya.capabilities.maven import POM as MAVEN_POM
from kriya.capabilities.pip import MARKERS as PIP_MARKERS
from kriya.code_intel.config_parsing import SPRING_XML, config_language_for_path
from kriya.code_intel.model import FileStructure, ParseState
from kriya.code_intel.parsing import JAVA, language_for_path, parse_file

SPRING_IMPORT_PREFIX = "org.springframework"
# Every adapter marker file name: a directory holding one is a build-root candidate.
BUILD_MARKERS = frozenset({MAVEN_POM, GRADLE_BUILD_SCRIPT, *PIP_MARKERS})

# The greenfield walk (§5.4): directories that are metadata only, file names
# (case-insensitive basename) that are not project content, and the bound
# past which the workspace is EXISTING.
GREENFIELD_METADATA_DIRS = frozenset({".git", ".kriya", ".idea", ".vscode"})
GREENFIELD_FILE_PREFIXES = ("readme", "license", "notice", "changelog")
GREENFIELD_FILE_NAMES = frozenset({".gitignore", ".gitattributes", ".editorconfig", ".ds_store",
                                   "kriya.yaml", "kriya.yml"})
GREENFIELD_WALK_LIMIT = 10_000


@dataclass(frozen=True)
class BuildRootFact:
    root: str                            # workspace-relative directory ("" = workspace root)
    build_systems: FrozenSet[str]        # adapter.build_system values detected there


@dataclass(frozen=True)
class RepositoryFacts:
    spring_repository: bool
    greenfield: bool
    build_roots: Tuple[BuildRootFact, ...]   # sorted by (depth descending, path)
    # Build systems the user's goal names (positive clauses only): the only
    # build evidence of a greenfield request that has no target (§7 note 2).
    goal_build_systems: FrozenSet[str] = frozenset()

    @property
    def context(self) -> RepoContext:
        return RepoContext.GREENFIELD if self.greenfield else RepoContext.EXISTING


@dataclass(frozen=True)
class SelectionFacts:
    role: Role
    operation: Operation
    context: RepoContext
    target_paths: Tuple[str, ...]
    target_languages: FrozenSet[str]
    target_symbol_kinds: FrozenSet[str]
    target_annotations: FrozenSet[str]
    target_imports_spring: bool
    build_systems: FrozenSet[str]
    spring_repository: bool


# -- repository facts --------------------------------------------------------


def _is_greenfield_file(name: str) -> bool:
    lowered = name.lower()
    return lowered in GREENFIELD_FILE_NAMES or lowered.startswith(GREENFIELD_FILE_PREFIXES)


def workspace_is_greenfield(workspace_path: str, *, limit: int = GREENFIELD_WALK_LIMIT) -> bool:
    """§5.4, fail-conservative: True only when no language adapter finds
    sources, no build adapter detects a build, and a bounded walk finds no
    file outside the metadata directories and allowlisted names. Symlinked
    directories are never followed; a symlink is project content unless it
    has an allowlisted name and resolves to a regular file. Any ambiguity -
    an unreadable entry, the walk limit, a filesystem error - is EXISTING."""
    seen = 0
    pending = [workspace_path]
    try:
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        return False
                    if entry.is_symlink():
                        if not (_is_greenfield_file(entry.name) and os.path.isfile(entry.path)):
                            return False
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name not in GREENFIELD_METADATA_DIRS:
                            pending.append(entry.path)
                    elif entry.name not in GREENFIELD_METADATA_DIRS and not _is_greenfield_file(entry.name):
                        return False  # a .git FILE (a worktree pointer) is metadata too
    except OSError:
        return False
    return not any(adapter.has_sources(workspace_path) for adapter in LANGUAGE_ADAPTERS) and \
        not any(adapter.detects(workspace_path) for adapter in BUILD_ADAPTERS)


def workspace_inventory(workspace_path: str, *, limit: int = GREENFIELD_WALK_LIMIT) -> List[str]:
    """The workspace's files, workspace-relative: Git's tracked plus untracked
    non-ignored files when it is a Git work tree (the inventory the
    verification-tree binding reads), else a bounded walk that never follows
    symlinks. ``.kriya/`` is never part of it."""
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=workspace_path,
                            capture_output=True, text=True)
    if inside.returncode == 0 and inside.stdout.strip() == "true":
        listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                cwd=workspace_path, capture_output=True)
        if listed.returncode == 0:
            paths = (os.fsdecode(item) for item in listed.stdout.split(b"\0") if item)
            return sorted({p for p in paths if p != ".kriya" and not p.startswith(".kriya/")})
    found: List[str] = []
    for directory, dirnames, filenames in os.walk(workspace_path):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", ".kriya"))
        found += [os.path.relpath(os.path.join(directory, name), workspace_path) for name in filenames]
        if len(found) > limit:
            break
    return sorted(found)


def build_roots(workspace_path: str, inventory: Iterable[str]) -> Tuple[BuildRootFact, ...]:
    """§5.5: every directory holding an adapter marker, confirmed by the
    adapters' own ``detects``; a directory none confirms is dropped."""
    candidates = {os.path.dirname(path) for path in inventory if os.path.basename(path) in BUILD_MARKERS}
    roots = []
    for root in candidates:
        directory = os.path.join(workspace_path, root) if root else workspace_path
        systems = frozenset(adapter.build_system for adapter in BUILD_ADAPTERS if adapter.detects(directory))
        if systems:
            roots.append(BuildRootFact(root, systems))
    return tuple(sorted(roots, key=lambda fact: (-_depth(fact.root), fact.root)))


def _depth(root: str) -> int:
    return len(root.split("/")) if root else 0


def spring_repository(frameworks: Iterable[str], dependency_graph_path: Optional[str]) -> bool:
    """§5.3: the analyzer's Spring Boot framework, else a PARSED Spring XML
    file or a Java import from org.springframework in the Code Intelligence
    index (XML-only Spring projects). An absent index is never created."""
    if SPRING_BOOT_FRAMEWORK in set(frameworks):
        return True
    if not dependency_graph_path or not os.path.exists(dependency_graph_path):
        return False
    from kriya.code_intel.store import StructuralStore

    store = StructuralStore(dependency_graph_path)
    try:
        return store.has_file(SPRING_XML, ParseState.PARSED) or store.has_import_prefix(JAVA, SPRING_IMPORT_PREFIX)
    finally:
        store.close()


def repository_facts(workspace_path: str, *, frameworks: Iterable[str] = (),
                     dependency_graph_path: Optional[str] = None,
                     goal_build_systems: Iterable[str] = ()) -> RepositoryFacts:
    """The run's RepositoryFacts, computed on the original workspace."""
    return RepositoryFacts(
        spring_repository=spring_repository(frameworks, dependency_graph_path),
        greenfield=workspace_is_greenfield(workspace_path),
        build_roots=build_roots(workspace_path, workspace_inventory(workspace_path)),
        goal_build_systems=frozenset(goal_build_systems),
    )


# -- selection facts ---------------------------------------------------------


def nearest_build_root(roots: Sequence[BuildRootFact], path: str) -> Optional[BuildRootFact]:
    """The deepest root that is ``path``'s directory or one of its ancestors."""
    directory = os.path.dirname(path.replace(os.sep, "/"))
    for fact in roots:  # deepest first
        if not fact.root or directory == fact.root or directory.startswith(fact.root + "/"):
            return fact
    return None


def target_structure(path: str, data: Optional[bytes]) -> Optional[FileStructure]:
    """A target's structure from its current bytes (None: a new file)."""
    return parse_file(path, data) if data is not None else None


def _target_languages(path: str, structure: Optional[FileStructure], spring: bool) -> FrozenSet[str]:
    if structure is not None:
        # An existing file is what its bytes parse as: a non-Spring XML
        # (pom.xml, any other XML) parses with no language.
        return frozenset({structure.language}) if structure.language else frozenset()
    language = language_for_path(path) or config_language_for_path(path)
    if language == SPRING_XML and (not spring or os.path.basename(path) in BUILD_MARKERS):
        return frozenset()  # every .xml is only a Spring XML candidate by name
    return frozenset({language}) if language else frozenset()


def selection_facts(role: Role, operation: Operation, repo: RepositoryFacts, target_paths: Sequence[str],
                    read: Callable[[str], Optional[bytes]]) -> SelectionFacts:
    """One request's SelectionFacts. ``target_paths`` are the request's own
    selection inputs (§7) and ``read(path)`` their current bytes (None for a
    file that does not exist yet). Build systems are the union of each
    target's nearest owning build root (§5.5); a greenfield request with no
    target takes the goal's build systems (§7 note 2)."""
    paths = tuple(dict.fromkeys(p for p in target_paths if p))
    languages, kinds, annotations = set(), set(), set()
    imports_spring = False
    systems = set()
    for path in paths:
        structure = target_structure(path, read(path))
        languages |= _target_languages(path, structure, repo.spring_repository)
        if structure is not None:
            for symbol in structure.symbols:
                kinds.add(symbol.kind)
                annotations.update(symbol.annotations)
            imports_spring = imports_spring or any(name.startswith(SPRING_IMPORT_PREFIX) for name in structure.imports)
        root = nearest_build_root(repo.build_roots, path)
        if root is not None:
            systems |= root.build_systems
    if not paths and repo.greenfield:
        systems |= repo.goal_build_systems
    return SelectionFacts(
        role=role, operation=operation, context=repo.context, target_paths=paths,
        target_languages=frozenset(languages), target_symbol_kinds=frozenset(kinds),
        target_annotations=frozenset(annotations), target_imports_spring=imports_spring,
        build_systems=frozenset(systems), spring_repository=repo.spring_repository,
    )


def file_reader(root: str) -> Callable[[str], Optional[bytes]]:
    """``read`` for selection_facts over files under ``root`` (None when a
    path does not exist or is not a regular file)."""
    def read(path: str) -> Optional[bytes]:
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as handle:
            return handle.read()
    return read
