"""Capability adapter ports (Capability Adapters R1).

Ecosystem knowledge - how a build system compiles and tests a project, where
its output goes, how a language's sources are found - lives behind two
narrow ports, so the verification core (kriya/tools/validate.py) asks an
adapter instead of carrying each ecosystem inline.

This is ownership, not authority: an adapter runs only through the
validator handle it is given (its containment, gate binding, resource
limits and acquisition rules), never a process of its own. The first slice
moves existing Maven and Java behaviour unchanged.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, FrozenSet, List, Optional


class LanguageAdapter(ABC):
    """What Kriya knows about one source language."""

    language: str

    @abstractmethod
    def has_sources(self, workspace_root: str) -> bool:
        """Whether a marker-free workspace holds a source of this language."""

    @abstractmethod
    def source_files(self, workspace_root: str) -> List[str]:
        """Every source of this language, workspace-relative, sorted."""


class BuildAdapter(ABC):
    """What Kriya knows about one build system."""

    build_system: str
    language: str
    tools: FrozenSet[str]  # executable basenames that invoke this build system

    @abstractmethod
    def detects(self, workspace_root: str) -> bool:
        """Whether the workspace declares a project of this build system."""

    @abstractmethod
    def output_roots(self, cmd: List[str], cwd: str) -> List[str]:
        """Where ``cmd`` (one of ``tools``) writes its own build output."""

    @abstractmethod
    def compile(self, validator: Any, files: List[str], *, deadline: Optional[float]) -> Optional[Dict[str, Any]]:
        """The compile gate result, or None when this build system did not
        decide it (the caller continues with its next option)."""

    @abstractmethod
    def run_tests(self, validator: Any, test_class: Optional[str]) -> Dict[str, Any]:
        """The test gate result (``test_class``: a bare class name or None)."""
