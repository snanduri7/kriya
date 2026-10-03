"""Capability adapters (Capability Adapters R1): ecosystem knowledge behind
two narrow ports. ``JAVA``, ``PYTHON``, ``MAVEN``, ``GRADLE`` and ``PIP`` are the adapters so far;
the registry is closed and in-process (no plugin discovery). Order is
precedence: a workspace that declares both builds is Maven, as before."""
from typing import Optional

from kriya.capabilities.gradle import GradleBuildAdapter
from kriya.capabilities.java import JavaLanguageAdapter
from kriya.capabilities.maven import MavenBuildAdapter
from kriya.capabilities.pip import PipBuildAdapter
from kriya.capabilities.ports import BuildAdapter, LanguageAdapter
from kriya.capabilities.python import PythonLanguageAdapter

JAVA = JavaLanguageAdapter()
MAVEN = MavenBuildAdapter()
GRADLE = GradleBuildAdapter()
PYTHON = PythonLanguageAdapter()
PIP = PipBuildAdapter()
BUILD_ADAPTERS = (MAVEN, GRADLE, PIP)
LANGUAGE_ADAPTERS = (JAVA, PYTHON)


def build_adapter_for_tool(tool: str) -> Optional[BuildAdapter]:
    """The build adapter whose executable ``tool`` (a basename) is, or None."""
    return next((adapter for adapter in BUILD_ADAPTERS if adapter.invokes(tool)), None)


__all__ = ["BUILD_ADAPTERS", "GRADLE", "JAVA", "LANGUAGE_ADAPTERS", "MAVEN", "PIP", "PYTHON", "BuildAdapter",
           "LanguageAdapter", "build_adapter_for_tool"]
