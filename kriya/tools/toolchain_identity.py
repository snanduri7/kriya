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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from kriya.core.tomlcompat import tomllib
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
    # A declared constraint the minor-version profile alone cannot prove
    # (e.g. requires-python ">=3.11.4" on the 3.11 profile): the exact
    # runtime version observed at attestation must satisfy it.
    runtime_constraint: Optional[str] = None

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


# Versioned prebuilt images exist for each (maven:3.9-eclipse-temurin-<N>,
# gradle:8-jdk<N>, python:<X.Y>-slim). Order is preference.
_SUPPORTED_JAVA = (21, 17, 11, 8)
_DEFAULT_JAVA = 21
_SUPPORTED_PYTHON: Sequence[Tuple[int, int]] = ((3, 14), (3, 13), (3, 12), (3, 11), (3, 10))
# An unconstrained (or loosely constrained) project keeps the long-standing
# 3.12 profile rather than silently moving to the newest interpreter.
_DEFAULT_PYTHON: Tuple[int, int] = (3, 12)


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
    version = version or _DEFAULT_JAVA
    if version not in _SUPPORTED_JAVA:
        raise ToolchainResolutionError(
            f"Java {version} is required by {source}, but production containment supports exactly "
            f"{', '.join(map(str, _SUPPORTED_JAVA))}; refusing host-tool fallback."
        )
    if build_tool == "gradle":
        image = f"gradle:8-jdk{version}"
        build_version = "8"
    else:
        image = f"maven:3.9-eclipse-temurin-{version}"
    return ToolchainIdentity("java", "jdk", str(version), build_tool, build_version, image, source)


_SPECIFIER = re.compile(r"(===|==|!=|~=|>=|<=|>|<)\s*(\d+(?:\.\d+){0,2})(\.\*)?")


def _parse_python_spec(spec: str) -> List[Tuple[str, Tuple[int, ...], bool]]:
    """The PEP 440 subset real requires-python values use: ==, !=, ~=, >=,
    <=, >, < on 1-3 release segments, and a trailing ``.*`` on == / !=.
    Anything else (``===``, pre/post/dev/local versions) is refused rather
    than guessed at."""
    clauses = []
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        match = _SPECIFIER.fullmatch(part)
        if not match or match.group(1) == "===":
            raise ToolchainResolutionError(f"Unsupported requires-python constraint {part!r}")
        op, release, wildcard = match.group(1), tuple(int(x) for x in match.group(2).split(".")), bool(match.group(3))
        if wildcard and op not in ("==", "!="):
            raise ToolchainResolutionError(f"Unsupported requires-python constraint {part!r}")
        if op == "~=" and len(release) < 2:
            raise ToolchainResolutionError(f"Invalid compatible-release constraint {part!r}")
        clauses.append((op, release, wildcard))
    return clauses


def _pad(release: Tuple[int, ...], length: int = 3) -> Tuple[int, ...]:
    return tuple(release) + (0,) * (length - len(release))


def _clause_holds(version: Tuple[int, int, int], op: str, release: Tuple[int, ...], wildcard: bool) -> bool:
    if wildcard:
        prefix = version[:len(release)] == release
        return prefix if op == "==" else not prefix
    target = _pad(release)
    if op == "==":
        return version == target
    if op == "!=":
        return version != target
    if op == ">=":
        return version >= target
    if op == "<=":
        return version <= target
    if op == ">":
        return version > target
    if op == "<":
        return version < target
    # ~=X.Y[.Z]: >= X.Y[.Z] and the same release prefix minus its last segment.
    return version >= target and version[:len(release) - 1] == release[:-1]


def python_version_satisfies(version: Tuple[int, int, int], spec: str) -> bool:
    return all(_clause_holds(version, *clause) for clause in _parse_python_spec(spec))


_ALL, _SOME, _NONE = "all", "some", "none"


def _minor_coverage(minor: Tuple[int, int], spec: str) -> str:
    """Whether every, some or no patch release of ``minor`` satisfies
    ``spec``. The predicate is piecewise constant between the patch numbers
    the spec names for this minor, so those boundaries plus 0 and a
    far-future patch decide it exactly."""
    clauses = _parse_python_spec(spec)
    patches = {0, 10_000}
    for _op, release, _wildcard in clauses:
        if len(release) == 3 and release[:2] == minor:
            patches.update({max(release[2] - 1, 0), release[2], release[2] + 1})
    results = {python_version_satisfies((minor[0], minor[1], patch), spec) for patch in patches}
    if results == {True}:
        return _ALL
    return _SOME if True in results else _NONE


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
            pinned = open(python_version_file, encoding="utf-8").read().split()
        except OSError as exc:
            raise ToolchainResolutionError(f"Cannot read .python-version: {exc}") from exc
        match = re.fullmatch(r"(?:python-?|cpython-?)?(\d+)\.(\d+)(?:\.\d+)?", pinned[0] if pinned else "")
        if not match:
            raise ToolchainResolutionError(f"Unsupported .python-version value {' '.join(pinned)!r}")
        # A pyenv pin names the developer's local interpreter; containment
        # honours its minor version and records the exact patch it observed.
        spec, source = f"=={match.group(1)}.{match.group(2)}.*", f".python-version:{pinned[0]}"
    if spec is None:
        version, constraint = _DEFAULT_PYTHON, None
    else:
        # Nearest to the default first (newer on a tie): the smallest move
        # away from the long-standing profile that the project allows.
        preference = sorted(
            _SUPPORTED_PYTHON, key=lambda minor: (abs(minor[1] - _DEFAULT_PYTHON[1]), -minor[1]),
        )
        coverage = {minor: _minor_coverage(minor, spec) for minor in preference}
        full = [minor for minor in preference if coverage[minor] == _ALL]
        partial = [minor for minor in preference if coverage[minor] == _SOME]
        if full:
            version, constraint = full[0], None
        elif partial:
            version, constraint = partial[0], spec
        else:
            raise ToolchainResolutionError(
                f"Python requirement {spec!r} from {source} cannot be satisfied by the production "
                f"containment profiles ({', '.join(f'{a}.{b}' for a, b in _SUPPORTED_PYTHON)}); "
                "refusing host-tool fallback."
            )
    rendered = f"{version[0]}.{version[1]}"
    return ToolchainIdentity(
        "python", "cpython", rendered, "pip", None, f"python:{rendered}-slim", source,
        runtime_constraint=constraint,
    )


def resolve_toolchain_identity(
    workspace_path: str, detected_stack: str, *, java_home_override: Optional[str] = None,
) -> Optional[ToolchainIdentity]:
    """Resolve a versioned profile from the validator's existing stack decision."""
    if detected_stack == "java":
        return _resolve_java(workspace_path, java_home_override)
    if detected_stack == "python":
        return _resolve_python(workspace_path)
    return None
