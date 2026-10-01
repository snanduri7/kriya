"""Typed, stack-neutral manifest for deterministic multi-file generation."""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Tuple


class FileRole(str, Enum):
    BUILD = "build"
    MODEL = "model"
    SOURCE = "source"
    CONFIG = "config"
    ENTRYPOINT = "entrypoint"
    TEST = "test"
    DOCUMENTATION = "documentation"
    ASSET = "asset"


_ROLE_PRIORITY = {
    FileRole.BUILD: 0,
    FileRole.MODEL: 1,
    FileRole.SOURCE: 2,
    FileRole.CONFIG: 3,
    FileRole.ENTRYPOINT: 4,
    FileRole.TEST: 5,
    FileRole.DOCUMENTATION: 6,
    FileRole.ASSET: 7,
}

_BUILD_FILENAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "package.json", "cargo.toml", "go.mod",
    "gemfile", "composer.json", "makefile",
}
_SOURCE_EXTENSIONS = {".java", ".kt", ".kts", ".py", ".go", ".rs", ".rb", ".js", ".ts", ".tsx", ".cs", ".cpp", ".c", ".h"}
_CONFIG_EXTENSIONS = {".xml", ".yaml", ".yml", ".properties", ".toml", ".ini", ".conf"}


def classify_file_role(path: str) -> FileRole:
    normalized = path.replace("\\", "/").lower()
    basename = os.path.basename(normalized)
    stem, extension = os.path.splitext(basename)
    segments = set(normalized.split("/"))

    if basename in _BUILD_FILENAMES or basename.startswith("dockerfile"):
        return FileRole.BUILD
    if "test" in segments or "tests" in segments or "spec" in segments or "specs" in segments:
        return FileRole.TEST
    if stem.startswith("test_") or stem.endswith(("test", "tests", "spec", "specs")):
        return FileRole.TEST
    if extension in {".md", ".rst", ".adoc"}:
        return FileRole.DOCUMENTATION
    if extension in _CONFIG_EXTENSIONS or "resources" in segments or "config" in segments:
        return FileRole.CONFIG
    if segments.intersection({"model", "models", "entity", "entities", "domain", "dto", "schema"}):
        return FileRole.MODEL
    if stem.endswith(("model", "entity", "dto", "request", "response", "record")):
        return FileRole.MODEL
    if extension in _SOURCE_EXTENSIONS and (
        stem in {
            "main", "__main__", "app", "application", "server", "cli", "program",
            "bootstrap", "runner",
        }
        # A bare exact-match set misses the standard Spring Boot convention
        # (e.g. DemoApplication.java, OrderServiceApplication.java) and other
        # common compound entrypoint names (BrokerServer.java, TaskRunner.java)
        # entirely - confirmed live to silently drop the Runtime Verification
        # Contract reminder (kriya/agents/agent.py's only consumer of this
        # role) for any of them, since a class named exactly "Application" or
        # "Server" is far rarer in practice than one with a project-specific
        # prefix. Suffix matching is deliberately narrower than a bare
        # substring check - the specific project-identifying prefix doesn't
        # matter, only that the file plausibly IS the thing that gets run.
        or stem.endswith(("application", "server", "bootstrap", "runner"))
    ):
        return FileRole.ENTRYPOINT
    if extension in _SOURCE_EXTENSIONS:
        return FileRole.SOURCE
    return FileRole.ASSET


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    role: FileRole
    depends_on: Tuple[str, ...]


@dataclass(frozen=True)
class GenerationManifest:
    entries: Tuple[ManifestEntry, ...]

    @property
    def ordered_paths(self) -> List[str]:
        return [entry.path for entry in self.entries]

    def entry_for(self, path: str) -> ManifestEntry:
        for entry in self.entries:
            if entry.path == path:
                return entry
        raise KeyError(path)

    def render_prompt(self) -> str:
        if not self.entries:
            return ""
        lines = [
            "\n\nRequired generation manifest (produce every entry; follow this order so "
            "later files use the real declarations from earlier dependencies):"
        ]
        for entry in self.entries:
            visible_dependencies = entry.depends_on[:8]
            dependency_text = ", ".join(visible_dependencies) if visible_dependencies else "none"
            if len(entry.depends_on) > len(visible_dependencies):
                dependency_text += (
                    f", +{len(entry.depends_on) - len(visible_dependencies)} earlier manifest entries"
                )
            lines.append(
                f"- {entry.path} [role={entry.role.value}; depends_on={dependency_text}]"
            )
        lines.append(
            "Preserve package/module names, public signatures, imports, dependency coordinates, "
            "resource paths, and configuration references consistently across the manifest."
        )
        return "\n".join(lines)


def build_generation_manifest(paths: Iterable[str]) -> GenerationManifest:
    unique_paths = sorted(set(path for path in paths if path))
    roles: Dict[str, FileRole] = {
        path: classify_file_role(path) for path in unique_paths
    }
    ordered = sorted(
        unique_paths, key=lambda path: (_ROLE_PRIORITY[roles[path]], path),
    )

    by_role: Dict[FileRole, List[str]] = {role: [] for role in FileRole}
    for path in ordered:
        by_role[roles[path]].append(path)

    entries: List[ManifestEntry] = []
    for path in ordered:
        role = roles[path]
        dependencies: List[str] = []
        if role is not FileRole.BUILD:
            dependencies.extend(by_role[FileRole.BUILD])
        if role in {FileRole.SOURCE, FileRole.CONFIG, FileRole.ENTRYPOINT, FileRole.TEST}:
            dependencies.extend(by_role[FileRole.MODEL])
        if role in {FileRole.CONFIG, FileRole.ENTRYPOINT, FileRole.TEST}:
            dependencies.extend(
                candidate for candidate in by_role[FileRole.SOURCE]
                if candidate != path
            )
        if role in {FileRole.ENTRYPOINT, FileRole.TEST}:
            dependencies.extend(
                candidate for candidate in by_role[FileRole.CONFIG]
                if candidate != path
            )
        if role is FileRole.TEST:
            dependencies.extend(
                candidate for candidate in by_role[FileRole.ENTRYPOINT]
                if candidate != path
            )
        entries.append(ManifestEntry(
            path=path,
            role=role,
            depends_on=tuple(dict.fromkeys(dependencies)),
        ))
    return GenerationManifest(tuple(entries))
