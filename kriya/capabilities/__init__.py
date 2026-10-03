"""Capability adapters (Capability Adapters R1): ecosystem knowledge behind
two narrow ports. ``JAVA``, ``PYTHON``, ``MAVEN``, ``GRADLE``, ``PIP`` and ``JAVAC`` are the adapters so
far; the registry is closed and in-process (no plugin discovery). ``BUILD_ADAPTERS`` order is
precedence: a workspace that declares both builds is Maven, as before. ``JAVAC`` is not in it: no
workspace declares a raw-javac build; it is the Java compile fallback the validator reaches after
every Java build adapter left the compile undecided."""
from typing import Optional

from kriya.capabilities.gradle import GradleBuildAdapter
from kriya.capabilities.java import JavaLanguageAdapter
from kriya.capabilities.javac import JavacBuildAdapter
from kriya.capabilities.maven import MavenBuildAdapter
from kriya.capabilities.pip import PipBuildAdapter
from kriya.capabilities.ports import BuildAdapter, LanguageAdapter
from kriya.capabilities.python import PythonLanguageAdapter

JAVA = JavaLanguageAdapter()
MAVEN = MavenBuildAdapter()
GRADLE = GradleBuildAdapter()
PYTHON = PythonLanguageAdapter()
PIP = PipBuildAdapter()
JAVAC = JavacBuildAdapter()
BUILD_ADAPTERS = (MAVEN, GRADLE, PIP)
LANGUAGE_ADAPTERS = (JAVA, PYTHON)


def build_adapter_for_tool(tool: str) -> Optional[BuildAdapter]:
    """The build adapter whose executable ``tool`` (a basename) is, or None."""
    return next((adapter for adapter in BUILD_ADAPTERS + (JAVAC,) if adapter.invokes(tool)), None)


__all__ = ["BUILD_ADAPTERS", "GRADLE", "JAVA", "JAVAC", "LANGUAGE_ADAPTERS", "MAVEN", "PIP", "PYTHON",
           "BuildAdapter", "LanguageAdapter", "build_adapter_for_tool"]
