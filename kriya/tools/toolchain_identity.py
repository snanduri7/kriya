"""Project-required toolchain identity for contained verification.

Stack selection stays owned by :class:`PolymorphicValidator`.  This module
receives that already-resolved stack and only resolves its version/profile;
it never performs a second Java/Python project detection pass.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Optional, Sequence, Tuple

import tomllib

from kriya.tools.containment import BackendUnavailableError


class ToolchainResolutionError(BackendUnavailableError):
    """The repository's declared toolchain cannot be mapped exactly enough."""


class ToolchainMismatchError(BackendUnavailableError):
    """A selected image does not contain the toolchain it claims to contain."""


@dataclass(frozen=True)
class ToolchainIdentity:
    language: str
    runtime: str
    runtime_version: str
    build_tool: Optional[str]
    build_tool_version: Optional[str]
    containment_image: str
    requirement_source: str
    image_digest: Optional[str] = None
    observed_runtime_version: Optional[str] = None
    observed_build_tool_version: Optional[str] = None

    def with_runtime_evidence(
        self, *, image_digest: str, observed_runtime_version: str,
        observed_build_tool_version: Optional[str] = None,
    ) -> "ToolchainIdentity":
        return replace(
            self, image_digest=image_digest,
            observed_runtime_version=observed_runtime_version,
            observed_build_tool_version=observed_build_tool_version,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_SUPPORTED_JAVA = (21, 17)
_SUPPORTED_PYTHON: Sequence[Tuple[int, int]] = ((3, 12), (3, 11), (3, 10))


def _pom_java_version(workspace_path: str) -> Tuple[Optional[int], str]:
    path = os.path.join(workspace_path, "pom.xml")
    if not os.path.isfile(path):
        return None, "default:no-pom-toolchain-version"
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ToolchainResolutionError(f"Cannot resolve Java toolchain from pom.xml: {exc}") from exc
    values = []
    for name in ("maven.compiler.release", "maven.compiler.source", "maven.compiler.target", "java.version"):
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == name and element.text:
                raw = element.text.strip()
                match = re.fullmatch(r"(?:1\.)?(\d+)", raw)
                if match:
                    values.append((name, int(match.group(1))))
    distinct = {value for _, value in values}
    if len(distinct) > 1:
        detail = ", ".join(f"{name}={value}" for name, value in values)
        raise ToolchainResolutionError(f"Conflicting Java toolchain requirements in pom.xml: {detail}")
    if not distinct:
        return None, "default:pom-without-java-version"
    return distinct.pop(), "pom.xml"


def _gradle_java_version(workspace_path: str) -> Tuple[Optional[int], str]:
    for filename in ("build.gradle", "build.gradle.kts"):
        path = os.path.join(workspace_path, filename)
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            raise ToolchainResolutionError(f"Cannot resolve Java toolchain from {filename}: {exc}") from exc
        matches = re.findall(
            r"JavaLanguageVersion\.of\((\d+)\)|(?:sourceCompatibility|targetCompatibility)\s*=\s*(?:JavaVersion\.VERSION_)?['\"]?(?:1_)?(\d+)",
            text,
        )
        versions = {int(a or b) for a, b in matches}
        if len(versions) > 1:
            raise ToolchainResolutionError(f"Conflicting Java toolchain requirements in {filename}: {sorted(versions)}")
        return (versions.pop() if versions else None), filename
    return None, "default:no-java-build-file"


def _java_home_major(java_home: str) -> int:
    release_path = os.path.join(java_home, "release")
    try:
        release = open(release_path, encoding="utf-8").read()
    except OSError as exc:
        raise ToolchainResolutionError(
            f"Cannot prove the JDK version selected by JAVA_HOME {java_home!r}: {exc}"
        ) from exc
    match = re.search(r'^JAVA_VERSION="(?:1\.)?(\d+)(?:\.|\")', release, re.MULTILINE)
    if not match:
        raise ToolchainResolutionError(f"JAVA_HOME release file {release_path!r} has no parseable JAVA_VERSION")
    return int(match.group(1))


def _resolve_java(workspace_path: str, java_home_override: Optional[str] = None) -> ToolchainIdentity:
    if os.path.isfile(os.path.join(workspace_path, "pom.xml")):
        version, source = _pom_java_version(workspace_path)
        build_tool = "maven"
        build_version = "3.9"
    elif os.path.isfile(os.path.join(workspace_path, "build.gradle")) or os.path.isfile(
        os.path.join(workspace_path, "build.gradle.kts")
    ):
        version, source = _gradle_java_version(workspace_path)
        build_tool = "gradle"
        build_version = None
    else:
        version, source = None, "default:standalone-java"
        build_tool = None
        build_version = None
    if java_home_override:
        override_version = _java_home_major(java_home_override)
        if version is not None and version != override_version:
            raise ToolchainResolutionError(
                f"Repository requires Java {version} via {source}, but the selected host toolchain "
                f"{java_home_override!r} is Java {override_version}; refusing ambiguous containment."
            )
        if version is None:
            version, source = override_version, f"JAVA_HOME:{java_home_override}/release"
    version = version or 21
    if version not in _SUPPORTED_JAVA:
        raise ToolchainResolutionError(
            f"Java {version} is required by {source}, but production containment supports exactly "
            f"{', '.join(map(str, _SUPPORTED_JAVA))}; refusing host-tool fallback."
        )
    if build_tool == "gradle":
        image = f"gradle:8-jdk{version}"
    else:
        image = f"maven:3.9-eclipse-temurin-{version}"
    return ToolchainIdentity("java", "jdk", str(version), build_tool, build_version, image, source)


def _version_tuple(value: str) -> Tuple[int, int]:
    match = re.match(r"\s*(\d+)\.(\d+)", value)
    if not match:
        raise ToolchainResolutionError(f"Unsupported Python version expression component: {value!r}")
    return int(match.group(1)), int(match.group(2))


def _python_satisfies(version: Tuple[int, int], spec: str) -> bool:
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if re.search(r"\d+\.\d+\.\d+", part):
            raise ToolchainResolutionError(
                f"Patch-specific requires-python constraint {part!r} cannot be proven by the "
                "available minor-version OCI profiles."
            )
        match = re.fullmatch(r"(>=|<=|==|~=|>|<)\s*(\d+\.\d+)(?:\.\d+)?(?:\.\*)?", part)
        if not match:
            raise ToolchainResolutionError(f"Unsupported requires-python constraint {part!r}")
        op, required_raw = match.groups()
        required = _version_tuple(required_raw)
        if op == ">=" and not version >= required:
            return False
        if op == ">" and not version > required:
            return False
        if op == "<=" and not version <= required:
            return False
        if op == "<" and not version < required:
            return False
        if op == "==" and not version == required:
            return False
        if op == "~=" and not (version >= required and version[0] == required[0]):
            return False
    return True


def _resolve_python(workspace_path: str) -> ToolchainIdentity:
    spec: Optional[str] = None
    source = "default:python-runtime-policy"
    pyproject = os.path.join(workspace_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "rb") as stream:
                project = tomllib.load(stream).get("project", {})
            raw = project.get("requires-python")
            if raw is not None:
                if not isinstance(raw, str):
                    raise ToolchainResolutionError("project.requires-python must be a string")
                spec, source = raw, "pyproject.toml:project.requires-python"
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ToolchainResolutionError(f"Cannot resolve Python toolchain from pyproject.toml: {exc}") from exc
    python_version_file = os.path.join(workspace_path, ".python-version")
    if spec is None and os.path.isfile(python_version_file):
        try:
            spec = "==" + open(python_version_file, encoding="utf-8").read().strip()
            source = ".python-version"
        except OSError as exc:
            raise ToolchainResolutionError(f"Cannot read .python-version: {exc}") from exc
    candidates = [version for version in _SUPPORTED_PYTHON if spec is None or _python_satisfies(version, spec)]
    if not candidates:
        raise ToolchainResolutionError(
            f"Python requirement {spec!r} from {source} cannot be satisfied by the production "
            "containment profiles (3.12, 3.11, 3.10); refusing host-tool fallback."
        )
    version = candidates[0]
    rendered = f"{version[0]}.{version[1]}"
    return ToolchainIdentity("python", "cpython", rendered, "pip", None, f"python:{rendered}-slim", source)


def resolve_toolchain_identity(
    workspace_path: str, detected_stack: str, *, java_home_override: Optional[str] = None,
) -> Optional[ToolchainIdentity]:
    """Resolve a versioned profile from the validator's existing stack decision."""
    if detected_stack == "java":
        return _resolve_java(workspace_path, java_home_override)
    if detected_stack == "python":
        return _resolve_python(workspace_path)
    return None
