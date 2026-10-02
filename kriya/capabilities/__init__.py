"""Capability adapters (Capability Adapters R1): ecosystem knowledge behind
two narrow ports. ``JAVA`` and ``MAVEN`` are the first adapters; the
registry is closed and in-process (no plugin discovery)."""
from typing import Optional

from kriya.capabilities.java import JavaLanguageAdapter
from kriya.capabilities.maven import MavenBuildAdapter
from kriya.capabilities.ports import BuildAdapter, LanguageAdapter

JAVA = JavaLanguageAdapter()
MAVEN = MavenBuildAdapter()
BUILD_ADAPTERS = (MAVEN,)
LANGUAGE_ADAPTERS = (JAVA,)


def build_adapter_for_tool(tool: str) -> Optional[BuildAdapter]:
    """The build adapter whose executable ``tool`` (a basename) is, or None."""
    return next((adapter for adapter in BUILD_ADAPTERS if tool in adapter.tools), None)


__all__ = ["BUILD_ADAPTERS", "JAVA", "LANGUAGE_ADAPTERS", "MAVEN", "BuildAdapter", "LanguageAdapter",
           "build_adapter_for_tool"]
