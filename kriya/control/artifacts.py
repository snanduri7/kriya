"""ArtifactRegistry - MA5.4 of the control-plane implementation plan.

Hard rule (section 16): artifact facts are DERIVED from real build/
workspace metadata, never asked of an LLM. "What Maven coordinates did M1
produce" is answered by parsing pom.xml, not by prompting a model - an
LLM's own output is never the source of truth for a physical artifact
fact (see kriya/control/__init__.py's own principle). Where Kriya already
has real parsing utilities (kriya/tools/validate.py's get_pom_dependencies/
get_pom_own_coordinate), this module reuses their exact namespace-
stripping ElementTree convention rather than inventing a second one, but
derives strictly more than those two functions do (version, packaging,
module path, resource roots) since MA3's artifact-registry needs are
broader than the failure-grounding/search-term-filtering they were built
for.

Deterministic derivation is implemented for Maven (the ecosystem section
18 gives a concrete "derive at least" bar for) plus Python (pyproject.toml)
and npm (package.json) - real, marker-file-gated parsing for each,
mirroring PolymorphicValidator._detect_stack()'s own marker convention
(kriya/tools/validate.py). An ecosystem with no derivation implemented
here yields no ArtifactRecord for that workspace rather than a fabricated
or guessed one - honest absence, not silent wrongness.

Parent inheritance (Maven's <parent> block) is explicitly RESOLVED, never
assumed: a module pom.xml missing its own <groupId>/<version> falls back
to reading its <parent>'s real, declared groupId/version - the actual
inherited value, not a guess at what it might be.
"""

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

ARTIFACT_REGISTRY_CORRUPT = "ARTIFACT_REGISTRY_CORRUPT"


class ArtifactRegistryCorruptError(RuntimeError):
    """The persisted artifact registry exists but cannot be read as a valid registry. Never treated as an empty registry (which switched ARTIFACT_DRIFT detection off and let the next save
    overwrite the evidence) and never overwritten automatically: the bytes are left exactly as they are. The twin of ContractRegistryCorruptError (PRD-029)."""

    reason_code = ARTIFACT_REGISTRY_CORRUPT

    def __init__(self, path: str, detail: str):
        super().__init__(f"{ARTIFACT_REGISTRY_CORRUPT}: {path}: {detail}")
        self.path = path
        self.detail = detail


ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE = "ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE"

# The descriptor file each derivable ecosystem keys its artifact on (relative to the artifact's module path).
_DESCRIPTOR_NAMES = {"maven": "pom.xml", "python": "pyproject.toml", "npm": "package.json"}


class ArtifactDerivationIndeterminate(RuntimeError):
    """ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001: a canonical manifest IS present (so a physical artifact may be expected) but
    its artifact identity cannot be derived: it is unreadable, does not parse (XML / TOML / JSON), is not the expected top-level
    shape, or it is a descriptor of an artifact the registry already RECORDS that no longer derives it. Never a successful empty
    derivation. Distinct from NOT APPLICABLE, which stays an honest empty derivation: the marker file is absent, or it parses and
    carries no artifact identity (no ``[project]`` table or one without a ``name``, a package.json without ``name``, a pom without
    ``artifactId``: well-formed skeleton markers) and nothing recorded expects it."""

    reason_code = ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE

    def __init__(self, problems: Tuple[str, ...]):
        super().__init__(f"{ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE}: " + "; ".join(problems))
        self.problems = tuple(problems)


_MAVEN_RESOURCE_CANDIDATES: Tuple[str, ...] = (
    os.path.join("src", "main", "java"),
    os.path.join("src", "main", "resources"),
    os.path.join("src", "test", "java"),
    os.path.join("src", "test", "resources"),
)


@dataclass(frozen=True)
class ArtifactRecord:
    milestone_id: str

    ecosystem: str
    kind: str

    module_path: Optional[str] = None

    coordinates: Dict[str, str] = field(default_factory=dict)

    packaging: Optional[str] = None

    resource_roots: Tuple[str, ...] = ()

    entrypoints: Tuple[str, ...] = ()

    resolved_at_commit: Optional[str] = None
    content_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "ecosystem": self.ecosystem,
            "kind": self.kind,
            "module_path": self.module_path,
            "coordinates": dict(self.coordinates),
            "packaging": self.packaging,
            "resource_roots": list(self.resource_roots),
            "entrypoints": list(self.entrypoints),
            "resolved_at_commit": self.resolved_at_commit,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRecord":
        return cls(
            milestone_id=data["milestone_id"],
            ecosystem=data["ecosystem"],
            kind=data["kind"],
            module_path=data.get("module_path"),
            coordinates=dict(data.get("coordinates", {})),
            packaging=data.get("packaging"),
            resource_roots=tuple(data.get("resource_roots", ())),
            entrypoints=tuple(data.get("entrypoints", ())),
            resolved_at_commit=data.get("resolved_at_commit"),
            content_hash=data.get("content_hash"),
        )


@dataclass(frozen=True)
class ArtifactValidationResult:
    recorded: ArtifactRecord
    current: Optional[ArtifactRecord]
    drifted: bool
    reason_code: Optional[str] = None


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _find_direct_child(parent: ET.Element, name: str) -> Optional[ET.Element]:
    """Direct-child-only lookup (never nested, e.g. inside <dependencies>) -
    same distinction get_pom_own_coordinate (kriya/tools/validate.py) already
    draws between a project's OWN groupId and one nested inside some
    unrelated <dependency>/<parent> element it does not itself define."""

    for child in parent:
        if _local_tag(child) == name:
            return child
    return None


def _text(elem: Optional[ET.Element]) -> Optional[str]:
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


def derive_maven_artifact(workspace_path: str, pom_path: str, milestone_id: str) -> Optional[ArtifactRecord]:
    """Parses one pom.xml into an ArtifactRecord. Returns None (never a
    partially-fabricated record) if the file is missing/unparsable, or if
    even the parent-resolved groupId/artifactId can't be determined -
    section 16's "do not ask an LLM" cuts both ways: this function also
    never guesses when real data is unavailable. ``ArtifactRegistry.derive_from_workspace`` is the authority that tells a
    missing pom (not applicable) from an unparseable one (indeterminate)."""

    return _derive_maven(workspace_path, pom_path, milestone_id)[0]


def _derive_maven(workspace_path: str, pom_path: str, milestone_id: str) -> Tuple[Optional[ArtifactRecord], Optional[str]]:
    """(record, problem): ``problem`` is set exactly when the pom is present but is not well-formed XML."""

    if not os.path.isfile(pom_path):
        return None, None
    rel = os.path.relpath(pom_path, workspace_path)
    try:
        root = ET.parse(pom_path).getroot()
    except ET.ParseError as error:
        return None, f"{rel}: not well-formed XML ({error})"

    parent_elem = _find_direct_child(root, "parent")

    group_id = _text(_find_direct_child(root, "groupId"))
    if group_id is None and parent_elem is not None:
        group_id = _text(_find_direct_child(parent_elem, "groupId"))

    artifact_id = _text(_find_direct_child(root, "artifactId"))

    version = _text(_find_direct_child(root, "version"))
    if version is None and parent_elem is not None:
        version = _text(_find_direct_child(parent_elem, "version"))

    if not artifact_id:
        # Parses, but carries no artifact identity: not applicable (a skeleton ``<project/>`` marker is a valid document and
        # Maven's own build reports the missing artifactId). Only an UNPARSEABLE pom is indeterminate.
        return None, None

    # Maven's own documented default when <packaging> is absent - a real
    # spec default, not a guess (https://maven.apache.org/pom.html).
    packaging = _text(_find_direct_child(root, "packaging")) or "jar"

    module_dir = os.path.dirname(pom_path)
    module_path = os.path.relpath(module_dir, workspace_path)
    if module_path == ".":
        module_path = ""

    resource_roots = tuple(
        candidate for candidate in _MAVEN_RESOURCE_CANDIDATES
        if os.path.isdir(os.path.join(module_dir, candidate))
    )

    coordinates = {"artifactId": artifact_id}
    if group_id:
        coordinates["groupId"] = group_id
    if version:
        coordinates["version"] = version

    return ArtifactRecord(
        milestone_id=milestone_id,
        ecosystem="maven",
        kind="library",
        module_path=module_path,
        coordinates=coordinates,
        packaging=packaging,
        resource_roots=resource_roots,
    ), None


def derive_python_artifact(workspace_path: str, milestone_id: str) -> Optional[ArtifactRecord]:
    """pyproject.toml's [project] table only - real, deterministic, no
    setup.py execution (running arbitrary setup.py code to introspect it
    would be its own security/reliability problem, not a parsing one)."""

    return _derive_python(workspace_path, milestone_id)[0]


def _derive_python(workspace_path: str, milestone_id: str) -> Tuple[Optional[ArtifactRecord], Optional[str]]:
    """(record, problem). NOT APPLICABLE (no problem, no record): no pyproject.toml, or one that parses and carries no artifact
    identity (no ``[project]`` table, or one without a ``name``: a skeleton marker). INDETERMINATE (problem): unreadable or
    unparseable, or ``project`` present but not a table. A recorded artifact lost from a present descriptor is the registry's
    (``ArtifactRegistry._lost_recorded_artifacts``)."""

    pyproject_path = os.path.join(workspace_path, "pyproject.toml")
    if not os.path.isfile(pyproject_path):
        return None, None
    from kriya.core.tomlcompat import tomllib

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as error:
        return None, f"pyproject.toml: unreadable or not valid TOML ({type(error).__name__}: {error})"

    if "project" not in data:
        return None, None
    project = data["project"]
    if not isinstance(project, dict):
        return None, "pyproject.toml: [project] is not a table"
    if not project.get("name"):
        return None, None       # parses, carries no identity: not applicable unless a recorded artifact makes it expected

    coordinates = {"name": str(project["name"])}
    if project.get("version"):
        coordinates["version"] = str(project["version"])

    return ArtifactRecord(
        milestone_id=milestone_id,
        ecosystem="python",
        kind="library",
        module_path="",
        coordinates=coordinates,
        packaging="wheel",
    ), None


def derive_npm_artifact(workspace_path: str, milestone_id: str) -> Optional[ArtifactRecord]:
    return _derive_npm(workspace_path, milestone_id)[0]


def _derive_npm(workspace_path: str, milestone_id: str) -> Tuple[Optional[ArtifactRecord], Optional[str]]:
    """(record, problem). NOT APPLICABLE: no package.json, or a valid JSON object without a ``name`` (an unnamed/private
    package has no artifact identity). INDETERMINATE: unreadable, not valid JSON, or not a JSON object."""

    package_json_path = os.path.join(workspace_path, "package.json")
    if not os.path.isfile(package_json_path):
        return None, None
    try:
        with open(package_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as error:
        return None, f"package.json: unreadable or not valid JSON ({type(error).__name__}: {error})"
    if not isinstance(data, dict):
        return None, "package.json: not a JSON object"

    name = data.get("name")
    if not name:
        return None, None

    coordinates = {"name": str(name)}
    if data.get("version"):
        coordinates["version"] = str(data["version"])

    return ArtifactRecord(
        milestone_id=milestone_id,
        ecosystem="npm",
        kind="library" if not data.get("bin") else "application",
        module_path="",
        coordinates=coordinates,
        packaging="npm-package",
    ), None


def _find_pom_files(workspace_path: str) -> List[str]:
    """Root pom.xml plus every declared <modules>/<module> child, resolved
    ONE level at a time (a module's own pom.xml can itself declare further
    modules) - a simple worklist, not a recursive glob, so a workspace with
    no Maven module structure at all costs one failed os.path.isfile check
    and nothing more."""

    root_pom = os.path.join(workspace_path, "pom.xml")
    if not os.path.isfile(root_pom):
        return []
    found = [root_pom]
    worklist = [root_pom]
    seen = {root_pom}
    while worklist:
        pom_path = worklist.pop()
        try:
            root = ET.parse(pom_path).getroot()
        except ET.ParseError:
            continue
        modules_elem = _find_direct_child(root, "modules")
        if modules_elem is None:
            continue
        module_dir = os.path.dirname(pom_path)
        for module_elem in modules_elem:
            if _local_tag(module_elem) != "module" or not module_elem.text:
                continue
            child_pom = os.path.join(module_dir, module_elem.text.strip(), "pom.xml")
            if os.path.isfile(child_pom) and child_pom not in seen:
                seen.add(child_pom)
                found.append(child_pom)
                worklist.append(child_pom)
    return found


def registry_digest(records: Any) -> str:
    """Canonical digest of one derivation result: independent of record order and of ``resolved_at_commit`` (the revision
    binding is added AFTER a commit, so the same derivation has the same digest before and after it). Gate F: the milestone
    pre-commit gate validates a candidate derivation, and settlement requires the committed workspace to derive the same
    thing."""
    rows = sorted(
        json.dumps({**record.to_dict(), "resolved_at_commit": None}, sort_keys=True, separators=(",", ":"))
        for record in records
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


class ArtifactRegistry:
    """In-memory registry with explicit save()/load() to
    .kriya/control/artifacts.json (kriya/control/persistence.py). Keyed by
    milestone_id -> the (possibly multiple, e.g. a multi-module Maven
    build) artifacts that milestone's real workspace state produced."""

    def __init__(self) -> None:
        self._records: Dict[str, List[ArtifactRecord]] = {}

    def derive_from_workspace(
        self, workspace_path: str, milestone_id: str, resolved_at_commit: Optional[str] = None,
    ) -> Tuple[ArtifactRecord, ...]:
        """Real, marker-gated derivation across every ecosystem this module
        implements - never fabricates a record for an ecosystem with no
        derivation logic here. A workspace can legitimately yield MULTIPLE
        records (a Maven multi-module build produces one per pom.xml).

        An ABSENT marker (or a manifest that parses and carries no artifact identity) is an honest empty derivation. A
        PRESENT canonical manifest whose identity cannot be derived raises ``ArtifactDerivationIndeterminate``: it is never a
        successful empty derivation (ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001). Every terminal gate that publishes or
        validates a registry (enforce terminal, milestone pre-commit gate, settlement) derives through here."""

        derived, problems = self._derive_collect(workspace_path, milestone_id, resolved_at_commit)
        problems = problems + self._lost_recorded_artifacts(workspace_path, derived)
        if problems:
            raise ArtifactDerivationIndeterminate(problems)
        return derived

    def _lost_recorded_artifacts(self, workspace_path: str, derived: Tuple[ArtifactRecord, ...]) -> Tuple[str, ...]:
        """An artifact is EXPECTED when this registry already records it (an earlier derivation of the same ecosystem and module):
        if its descriptor is still PRESENT but now derives no such artifact (a parseable pom that lost its ``artifactId``, a
        package.json that lost its ``name``, a pyproject that lost ``[project]``), that is indeterminate, never an empty success.
        A descriptor that is gone is not flagged here (removal is drift, reported to consumers by ``validate``), and with no
        recorded artifact nothing is expected, so an incomplete descriptor stays a valid empty derivation."""

        present = {(record.ecosystem, record.module_path) for record in derived}
        problems = []
        for recorded in self.all_records():
            key = (recorded.ecosystem, recorded.module_path)
            descriptor = _DESCRIPTOR_NAMES.get(recorded.ecosystem)
            if key in present or descriptor is None:
                continue
            relative = os.path.join(recorded.module_path, descriptor)
            if os.path.isfile(os.path.join(workspace_path, relative)):
                problems.append(f"{relative}: the {recorded.ecosystem} artifact recorded for {recorded.milestone_id} "
                                "can no longer be derived from its present descriptor")
        return tuple(dict.fromkeys(problems))

    @staticmethod
    def _derive_collect(
        workspace_path: str, milestone_id: str, resolved_at_commit: Optional[str] = None,
    ) -> Tuple[Tuple[ArtifactRecord, ...], Tuple[str, ...]]:
        """(records, problems): the tolerant derivation. ``problems`` names every present manifest that could not be derived."""

        derived: List[ArtifactRecord] = []
        problems: List[str] = []

        def take(record: Optional[ArtifactRecord], why_none: Any) -> None:
            # The public ``derive_*_artifact`` calls stay the seams (documented patch points); only when one yields no record
            # is the (same-implementation) reason asked for: a present-but-underivable manifest is a problem, never silence.
            if record is not None:
                derived.append(record)
                return
            problem = why_none()
            if problem is not None:
                problems.append(problem)

        for pom_path in _find_pom_files(workspace_path):
            take(derive_maven_artifact(workspace_path, pom_path, milestone_id),
                 lambda pom_path=pom_path: _derive_maven(workspace_path, pom_path, milestone_id)[1])
        take(derive_python_artifact(workspace_path, milestone_id), lambda: _derive_python(workspace_path, milestone_id)[1])
        take(derive_npm_artifact(workspace_path, milestone_id), lambda: _derive_npm(workspace_path, milestone_id)[1])

        if resolved_at_commit is not None:
            derived = [replace(r, resolved_at_commit=resolved_at_commit) for r in derived]

        return tuple(derived), tuple(problems)

    def record(self, artifact: ArtifactRecord) -> None:
        self._records.setdefault(artifact.milestone_id, []).append(artifact)

    def resolve_for_milestone(self, milestone_id: str) -> Tuple[ArtifactRecord, ...]:
        return tuple(self._records.get(milestone_id, ()))

    def invalidate(self, milestone_id: str) -> None:
        self._records.pop(milestone_id, None)

    def validate(self, workspace_path: str, milestone_id: str) -> Tuple[ArtifactValidationResult, ...]:
        """Re-derives the milestone's artifacts from the CURRENT workspace
        state and compares against what's on record - section 19's "lookup
        -> verify recorded path/coordinate still exists -> compare against
        current build metadata -> surface ARTIFACT_DRIFT rather than
        silently using stale data." A recorded artifact whose module_path/
        ecosystem no longer appears among the current derivation at all
        (not just changed coordinates) is drift too - it's just as stale
        as a coordinate mismatch."""

        recorded = self.resolve_for_milestone(milestone_id)
        # Tolerant on purpose: validate() reports a recorded artifact whose manifest is now underivable as ARTIFACT_DRIFT (the
        # recorded artifact is not among the current derivation); it must not start raising.
        current, _indeterminate = self._derive_collect(workspace_path, milestone_id)
        current_by_module = {(r.ecosystem, r.module_path): r for r in current}

        results = []
        for record in recorded:
            match = current_by_module.get((record.ecosystem, record.module_path))
            if match is None:
                results.append(ArtifactValidationResult(
                    recorded=record, current=None, drifted=True, reason_code="ARTIFACT_DRIFT",
                ))
                continue
            drifted = match.coordinates != record.coordinates or match.packaging != record.packaging
            results.append(ArtifactValidationResult(
                recorded=record, current=match, drifted=drifted,
                reason_code="ARTIFACT_DRIFT" if drifted else None,
            ))
        return tuple(results)

    def all_records(self) -> Tuple[ArtifactRecord, ...]:
        return tuple(r for records in self._records.values() for r in records)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifacts": {
                milestone_id: [r.to_dict() for r in records]
                for milestone_id, records in self._records.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRegistry":
        registry = cls()
        for milestone_id, records in data.get("artifacts", {}).items():
            registry._records[milestone_id] = [ArtifactRecord.from_dict(r) for r in records]
        return registry
