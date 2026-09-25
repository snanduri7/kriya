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


TOOLCHAIN_REQUIREMENT_CONFLICT = "TOOLCHAIN_REQUIREMENT_CONFLICT"


class ToolchainRequirementConflictError(ToolchainResolutionError):
    """The run needs a toolchain other than the repository declares, and has
    no authority to change that declaration. Raised before any candidate
    command runs; never resolved by picking one side."""

    reason_code = TOOLCHAIN_REQUIREMENT_CONFLICT

    def __init__(self, detail: str) -> None:
        super().__init__(f"{TOOLCHAIN_REQUIREMENT_CONFLICT}: {detail}")


# The files that declare a stack's toolchain. Changing one is a toolchain
# change, authorized only through the run's write authority over that file.
TOOLCHAIN_DECLARATION_FILES: Dict[str, Tuple[str, ...]] = {
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "python": ("pyproject.toml", ".python-version"),
}
ALL_TOOLCHAIN_DECLARATION_FILES: Tuple[str, ...] = tuple(
    name for names in TOOLCHAIN_DECLARATION_FILES.values() for name in names
)


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
    # How the verification toolchain was chosen: the repository's baseline
    # declaration, the target, the basis (repository_declaration,
    # authorized_declaration_change, authorized_goal_requirement,
    # goal_requirement_undeclared) and whether the run may change the
    # declaration. Part of the identity: a resumed run reuses gate evidence
    # only for the same baseline -> target selection.
    selection: Optional[Dict[str, Any]] = None

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


def _java_declaration(workspace_path: str) -> Tuple[Optional[int], str, Optional[str], Optional[str]]:
    """(declared version or None, its source, build tool, build-tool version)."""
    if os.path.isfile(os.path.join(workspace_path, "pom.xml")):
        version, source = _pom_java_version(workspace_path)
        return version, source, "maven", "3.9"
    if os.path.isfile(os.path.join(workspace_path, "build.gradle")) or os.path.isfile(
        os.path.join(workspace_path, "build.gradle.kts")
    ):
        version, source = _gradle_java_version(workspace_path)
        return version, source, "gradle", "8"
    return None, "default:standalone-java", None, None


def _java_identity(version: int, source: str, build_tool: Optional[str], build_version: Optional[str]) -> ToolchainIdentity:
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


def _resolve_java(workspace_path: str) -> ToolchainIdentity:
    version, source, build_tool, build_version = _java_declaration(workspace_path)
    return _java_identity(version or _DEFAULT_JAVA, source, build_tool, build_version)


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


def _summary(identity: ToolchainIdentity) -> Dict[str, Any]:
    return {
        "runtime_version": identity.runtime_version, "runtime_constraint": identity.runtime_constraint,
        "requirement_source": identity.requirement_source, "containment_image": identity.containment_image,
    }


def _same_toolchain(a: ToolchainIdentity, b: ToolchainIdentity) -> bool:
    return (a.runtime_version, a.runtime_constraint, a.build_tool) == (b.runtime_version, b.runtime_constraint, b.build_tool)


def resolve_toolchain_selection(
    candidate_path: str, detected_stack: str, *, baseline_path: Optional[str] = None,
    java_home_override: Optional[str] = None, declaration_mutable: bool = False,
) -> Optional[ToolchainIdentity]:
    """The toolchain candidate verification runs under.

    - baseline: what the repository (``baseline_path``, the pre-mutation
      workspace) declares; target: what the candidate is verified under.
    - A candidate whose toolchain declaration differs from the baseline is a
      toolchain migration. It is honoured only when the run may change the
      declaration (``declaration_mutable``: the declaration file is inside
      the run's authorized write scope) - the declaration can only have
      changed through that governed write path; otherwise it is a
      TOOLCHAIN_REQUIREMENT_CONFLICT.
    - A goal-stated JDK (``java_home_override``) that differs from the
      declaration becomes the target only under that same authority (the
      migration may not have landed in the candidate yet), or when nothing
      declares a version at all. Otherwise it is a conflict. It never
      authorizes a change by itself.

    Authority is never inferred from goal wording here: it is the caller's
    structured write scope."""
    if detected_stack == "python":
        target = _resolve_python(candidate_path)
        baseline = _resolve_python(baseline_path) if baseline_path else None
        basis = "repository_declaration"
        if baseline is not None and not _same_toolchain(baseline, target):
            if not declaration_mutable:
                raise ToolchainRequirementConflictError(
                    f"the candidate changes the Python toolchain from {baseline.runtime_version} "
                    f"({baseline.requirement_source}) to {target.runtime_version} without authority to modify "
                    f"{'/'.join(TOOLCHAIN_DECLARATION_FILES['python'])}."
                )
            basis = "authorized_declaration_change"
        return replace(target, selection={
            "baseline": _summary(baseline) if baseline is not None else None,
            "target": _summary(target), "basis": basis, "declaration_mutable": declaration_mutable,
        })
    if detected_stack != "java":
        return None

    declared, source, build_tool, build_version = _java_declaration(candidate_path)
    target = _java_identity(declared or _DEFAULT_JAVA, source, build_tool, build_version)
    baseline = None
    baseline_declared = None
    basis = "repository_declaration"
    if baseline_path:
        baseline_declared, baseline_source, baseline_tool, baseline_build = _java_declaration(baseline_path)
        baseline = _java_identity(baseline_declared or _DEFAULT_JAVA, baseline_source, baseline_tool, baseline_build)
        if not _same_toolchain(baseline, target):
            if not declaration_mutable:
                raise ToolchainRequirementConflictError(
                    f"the candidate changes the Java toolchain from {baseline.runtime_version} "
                    f"({baseline.requirement_source}) to {target.runtime_version} ({source}) without authority "
                    f"to modify {'/'.join(TOOLCHAIN_DECLARATION_FILES['java'])}."
                )
            basis = "authorized_declaration_change"
    if java_home_override:
        goal_version = _java_home_major(java_home_override)
        if goal_version != int(target.runtime_version):
            goal_source = f"JAVA_HOME:{java_home_override}/release"
            if declared is None and baseline_declared is None:
                basis = "goal_requirement_undeclared"
            elif declaration_mutable:
                basis = "authorized_goal_requirement"
            else:
                raise ToolchainRequirementConflictError(
                    f"the goal-stated JDK {java_home_override!r} is Java {goal_version}, but the repository "
                    f"requires Java {target.runtime_version} via {target.requirement_source} and this run has no "
                    f"authority to modify {'/'.join(TOOLCHAIN_DECLARATION_FILES['java'])}."
                )
            target = _java_identity(goal_version, goal_source, build_tool, build_version)
    return replace(target, selection={
        "baseline": _summary(baseline) if baseline is not None else None,
        "target": _summary(target), "basis": basis, "declaration_mutable": declaration_mutable,
    })


def resolve_toolchain_identity(
    workspace_path: str, detected_stack: str, *, java_home_override: Optional[str] = None,
) -> Optional[ToolchainIdentity]:
    """The repository's own toolchain (no baseline comparison, no authority
    to change the declaration)."""
    return resolve_toolchain_selection(workspace_path, detected_stack, java_home_override=java_home_override)
