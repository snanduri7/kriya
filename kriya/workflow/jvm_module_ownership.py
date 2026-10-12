"""JVM-MODULE-OWNERSHIP-001: which build module of a JVM repository owns a main compilation unit, decided from the
repository's own build metadata (never from a task, a repository name or a directory convention that the build file does
not declare).

The goal-example acceptance class (kriya/workflow/example_oracle_java.py) is a JUnit class the runner injects into the
candidate's test source set and runs through the project's ordinary test gate (kriya/workflow/acceptance_jvm.py). In a
multi-module build the class must live in, and be run by, the module that owns the types its examples call; injected at the
root of an aggregator it has neither the module's dependencies nor its source set. This module answers the one question
that placement needs: ``source path -> owning module``.

Rules (fail closed, no guessing):
- the module is the directory that holds the declaring file's ``src/main/java`` root; ``""`` is the root project itself
  (today's behaviour, byte-for-byte);
- a non-root module is accepted only when the ROOT build file DECLARES it (Gradle ``include``; Maven ``<modules>``) -
  ``repository_topology.declared_build_modules``, the same parsers the topology uses - and its directory is a project of
  that build (Gradle: ``tools.validate.gradle_project_dirs``, the set whose ``build/test-results`` the report binding reads;
  Maven: a ``pom.xml``);
- anything else (undeclared directory, a build system other than the detected runner, nested layouts the root file does
  not list) is UNRESOLVED with a reason: the caller keeps the contract shadow/unavailable, it never infers a module."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence

from kriya.workflow.repository_topology import declared_build_modules

_MAIN_MARKER = "src/main/java/"


@dataclass(frozen=True)
class ModuleResolution:
    module: Optional[str]  # "" = root project; None = unresolved
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.module is not None


def module_of_source_path(source_path: str) -> Optional[str]:
    """The directory (relative, ``/``-separated; ``""`` for the root) that holds the ``src/main/java`` root of a
    workspace-relative main source path, or None when the path is not under a ``src/main/java`` root."""
    normalized = source_path.replace(os.sep, "/")
    if normalized.startswith(_MAIN_MARKER):
        return ""
    index = normalized.find("/" + _MAIN_MARKER)
    return normalized[:index] if index > 0 else None


def _normalize(module: str) -> str:
    module = module.strip().replace("\\", "/")
    while module.startswith("./"):
        module = module[2:]
    return module.strip("/")


def resolve_module_ownership(candidate_root: str, source_path: Optional[str], runner: Optional[str]) -> ModuleResolution:
    """The module owning ``source_path`` (workspace-relative path of a main compilation unit) under the ``runner``
    (``maven`` / ``gradle``) the project is built with."""
    if not source_path:
        return ModuleResolution(None, "the declaring main compilation unit is not exactly one file")
    module = module_of_source_path(source_path)
    if module is None:
        return ModuleResolution(None, f"{source_path} is not under a src/main/java root")
    if module == "":
        return ModuleResolution("")
    if runner not in ("maven", "gradle"):
        return ModuleResolution(None, f"module {module!r} needs a Maven or Gradle runner, not {runner!r}")
    build_system, declared = declared_build_modules(candidate_root)
    if build_system != runner:
        return ModuleResolution(None, f"the root build is {build_system!r}, not {runner!r}")
    declared_set = {_normalize(entry) for entry in declared}
    if module not in declared_set:
        return ModuleResolution(None, f"module {module!r} is not declared by the root {runner} build")
    if not _is_project_of(candidate_root, module, runner):
        return ModuleResolution(None, f"module {module!r} is declared but is not a {runner} project directory")
    return ModuleResolution(module)


def _is_project_of(candidate_root: str, module: str, runner: str) -> bool:
    directory = os.path.join(candidate_root, *module.split("/"))
    if runner == "maven":
        return os.path.isfile(os.path.join(directory, "pom.xml"))
    from kriya.tools.validate import gradle_project_dirs

    wanted = os.path.realpath(directory)
    return any(os.path.realpath(found) == wanted for found in gradle_project_dirs(candidate_root))


def gradle_project_path(module: str) -> str:
    """The Gradle task prefix of a declared module (``core`` -> ``:core``; ``a/b`` -> ``:a:b``): the conventional
    projectDir mapping that ``resolve_module_ownership`` required the settings file to declare."""
    return ":" + ":".join(part for part in module.split("/") if part)


def injection_path_for(module: str, package: str, class_name: str) -> str:
    """Where the acceptance class lives: the module's test source set."""
    parts: List[str] = [*([m for m in module.split("/") if m] if module else []), "src", "test", "java",
                        *package.split("."), f"{class_name}.java"]
    return "/".join(parts)


def class_module(resolved_modules: Sequence[str]) -> Optional[str]:
    """The module the single acceptance class is placed in, from the owning modules of the examples that resolved:
    the root project when any example belongs to it (the legacy placement, so a root example is never displaced), else
    the unique non-root module they all share; None when they span several non-root modules (no single class can be run
    by two modules - those examples stay shadow) or there are none."""
    modules = set(resolved_modules)
    if "" in modules:
        return ""
    return next(iter(modules)) if len(modules) == 1 else None
