"""Deterministic dependency/technology migration obligation resolution and
terminal completion validation.

Found live, PRV-05 (2026-08-28): a goal explicitly asking to "replace the
existing JSON serialization library with the JSON library already approved
for this repository" ended with Quality Gates PASSED while the final
JsonService.java still imported and used Gson, and pom.xml still declared
both Gson and Jackson. Compile/tests/Spec Compliance never treated an
unfinished migration as a hard failure - worse, Spec Compliance itself
hallucinated the migration DIRECTION mid-run ("the task is about using a new
JSON library, which is satisfied by the use of Gson in the implementation")
and drove the Developer to actively revert a correct Jackson-based fix back
to Gson.

MigrationObligation resolution is deliberately ecosystem-general in SHAPE
(migration_kind/source_identity/target_identity/source_artifacts/
target_artifacts/grounded_consumers) even though this first version only
ships a Maven/Java adapter (_parse_maven_dependency_artifacts, Java import
scanning) - see resolve_migration_resolution's own docstring for why source/
target identity is resolved from repository grounding rather than literal
goal-text name matching, and why that keeps this module free of any
Gson/Jackson special-casing (those names only ever appear in this module's
own tests/fixtures, matching the explicit constraint that produced this
design). A later npm/Gradle/PyPI/Cargo/NuGet/Go adapter would implement the
same two functions (declared-artifact discovery + source-usage scanning)
against that ecosystem's own manifest/import conventions without changing
MigrationObligation's shape or find_migration_incomplete's terminal logic.

Deliberately NOT the full AUTHORIZED_CHANGE_SET framework the PRV-05 review
proposed (PRESERVE/MODIFY/REMOVE/ADD across arbitrary surfaces, or Planner-
level migration-aware sequencing) - scoped to exactly the confirmed failure
shape (an explicit dependency/technology replacement goal, terminal
completion evidence) per the same "smallest repair scope" principle already
used for EXISTING_CONTRACT_PRESERVATION (PRV-03) and
UNREQUESTED_ARCHITECTURAL_SURFACE (PRV-04).

PRV-05 run 6 (2026-08-28) found a second, deeper defect in the FIRST version
of this module: resolve_migration_obligation() re-derived source/target
identity from CURRENT repository state at every call site (per-attempt
dependency authorization, the terminal gate), using an "exactly one unused
dependency = the target" heuristic. That heuristic is timing-sensitive by
construction - before the migration, the real target (e.g. Jackson) looks
unused and resolves correctly; by the time the migration is DONE, the real
SOURCE (e.g. Gson) looks unused instead, so the same heuristic inverts its
own interpretation. Worse, an entirely unrelated dependency (JUnit - a test
framework, never a candidate for a production JSON-library replacement)
could tip the "exactly one" count into ambiguous at either end, degrading
resolution to None and silently disabling both the authorization check (so
the preservation validator fights the migration) and the terminal gate (so
an incomplete migration still reports PASSED).

The fix is architectural, not just a smarter heuristic: migration identity
is a goal-level invariant, not a snapshot property of the repository at
whatever moment it happens to be queried. resolve_migration_resolution() is
now the ONLY entry point for identity resolution, is meant to be called
EXACTLY ONCE per run against the immutable PRE-mutation baseline workspace,
and returns a MigrationResolution (NOT_APPLICABLE / RESOLVED / INDETERMINATE)
that the caller persists (AttemptContext.migration_resolution) and reuses
everywhere - the per-attempt authorization check, the per-subtask completion
check, and the terminal gate all consume the SAME resolved obligation rather
than each re-inferring their own. find_migration_incomplete() still runs
fresh against whatever candidate tree is being checked (that's its actual
job: is the CURRENT state consistent with the FIXED obligation) - only
identity resolution moved to run once.

The heuristic itself was also narrowed: candidates are restricted to
production-scope Maven dependencies (anything but the effectively-Maven-
default `compile`/unspecified/`runtime` scopes is excluded - most
concretely, `<scope>test</scope>`, matching the real PRV-05 fixture's own
JUnit declaration) before the "exactly one unused / exactly one used" count
is taken, and only production (non test-tree) `.java` files are scanned for
usage. This is a generic Maven dependency-scope signal, not JSON/Gson/
Jackson-specific - it just happens to be exactly what's needed to keep an
unrelated test-framework dependency out of a production library migration's
candidate set."""
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set

_IGNORED_DIRS = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}
_NON_PRODUCTION_SCOPES = {"test", "provided", "system"}
_TEST_PATH_SEGMENT_RE = re.compile(r"(^|[\\/])tests?([\\/]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class MigrationObligation:
    migration_kind: str
    source_identity: str
    target_identity: str
    source_artifacts: List[str]
    target_artifacts: List[str]
    grounded_consumers: List[str]


class MigrationResolutionStatus(str, Enum):
    # No explicit replacement intent in the goal, or no Maven manifest to
    # resolve against - this run simply isn't a dependency migration.
    NOT_APPLICABLE = "not_applicable"
    # Source/target identity resolved unambiguously from the baseline.
    RESOLVED = "resolved"
    # The goal expresses replacement intent, but source/target identity
    # can't be resolved confidently from repository grounding - unlike
    # NOT_APPLICABLE, callers must NOT silently treat this as "no migration
    # obligation applies" (see this module's own docstring, PRV-05 run 6).
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class MigrationResolution:
    status: MigrationResolutionStatus
    obligation: Optional[MigrationObligation] = None
    reason: str = ""


# Narrow, high-precision-only (matching this codebase's established
# convention for goal-text intent gates - e.g. file_resolution.py's
# _goal_explicitly_requests_api_change/_goal_explicitly_requests_new_
# entrypoint): a false negative here only costs a missed deterministic
# check (the existing gates still run); a false positive would invent a
# migration obligation for a goal that never asked for one.
_REPLACEMENT_INTENT_RE = re.compile(
    r"\breplace\b[^.\n]{0,80}\b(?:library|libraries|dependency|dependencies|"
    r"technology|framework|driver|client|provider)\b"
    r"|\bmigrat(?:e|ion)\b[^.\n]{0,60}\bto\b"
    r"|\bswitch\b[^.\n]{0,60}\bto\b",
    re.IGNORECASE,
)


def _goal_expresses_replacement_intent(goal: str) -> bool:
    return bool(_REPLACEMENT_INTENT_RE.search(goal or ""))


def _parse_maven_dependency_artifacts(pom_content: str) -> List[Dict[str, str]]:
    """Real XML parsing (stdlib, zero false positives by construction),
    matching this codebase's own established preference (see
    IgniteDuplicateSpringContextCheck in static_checks.py) over a regex scan
    of pom.xml text."""
    try:
        root = ET.fromstring(pom_content)
    except ET.ParseError:
        return []
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    artifacts = []
    for dep in root.iter(f"{ns}dependency"):
        group = dep.find(f"{ns}groupId")
        artifact = dep.find(f"{ns}artifactId")
        if group is None or artifact is None or not (group.text or "").strip() or not (artifact.text or "").strip():
            continue
        scope_el = dep.find(f"{ns}scope")
        scope = (scope_el.text or "").strip().lower() if scope_el is not None and scope_el.text else "compile"
        artifacts.append({"group": group.text.strip(), "artifact": artifact.text.strip(), "scope": scope})
    return artifacts


def _is_test_source_path(relpath: str) -> bool:
    """Generic Maven/Gradle convention (src/test/java/...), not a JSON- or
    Gson/Jackson-specific check - matches any path with a "test"/"tests"
    path segment, workspace-relative or not, case-insensitively."""
    return bool(_TEST_PATH_SEGMENT_RE.search(relpath.replace(os.sep, "/")))


# Strips generic Maven-artifact-naming suffixes so an artifactId correlates
# with the Java import package segments a consumer would actually use,
# without hardcoding any specific library's name - "jackson-databind" ->
# ["jackson", "databind"], "gson" -> ["gson"], "commons-io" -> ["commons",
# "io"]. Deliberately loose (ANY token match, not ALL) - a real ecosystem
# artifactId-to-package-namespace mapping isn't exact (e.g. gson's own
# groupId is "com.google.code.gson" but its package is "com.google.gson"),
# so requiring every token to match would under-fire on real libraries.
_GENERIC_ARTIFACT_SUFFIXES = {"core", "api", "impl", "client", "driver", "provider", "lib", "library"}


def _artifact_import_tokens(artifact_id: str) -> List[str]:
    parts = [p for p in re.split(r"[-_]", artifact_id.lower()) if p and p not in _GENERIC_ARTIFACT_SUFFIXES]
    return parts or [artifact_id.lower()]


_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)


def _file_imports_any_token(content: str, tokens: List[str]) -> bool:
    for match in _JAVA_IMPORT_RE.finditer(content or ""):
        segments = match.group(1).lower().split(".")
        if any(token in segments for token in tokens):
            return True
    return False


def _scan_java_files(root_path: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for root, dirs, filenames in os.walk(root_path):
        dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS]
        for filename in filenames:
            if not filename.endswith(".java"):
                continue
            full_path = os.path.join(root, filename)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    files[os.path.relpath(full_path, root_path)] = fh.read()
            except OSError:
                continue
    return files


def resolve_migration_resolution(goal: str, workspace_path: str) -> MigrationResolution:
    """THE canonical, single entry point for migration-obligation identity
    resolution. Call this EXACTLY ONCE per run, against the immutable
    PRE-mutation baseline workspace, and persist the result (e.g.
    AttemptContext.migration_resolution) for every other call site to reuse
    - never call this again later against a partially- or fully-mutated
    tree. See this module's own top-of-file docstring (PRV-05 run 6,
    2026-08-28) for why re-resolving identity at multiple points in time is
    itself the defect this function exists to close, not just a smarter
    heuristic.

    Resolves source/target identity from repository GROUNDING, not from
    literal names in the goal text - PRV-05's real goal ("the JSON library
    already approved for this repository") never names either library at
    all, so a name-matching approach would miss the exact incident this
    exists to catch. Among PRODUCTION-scope Maven dependencies only (see
    _NON_PRODUCTION_SCOPES - a test-scoped dependency like JUnit can never be
    a source or target of a production library replacement, so it's excluded
    before any ambiguity count is taken, not just at usage-scanning time): a
    dependency imported by NOTHING in the baseline's production `.java`
    files is the plausible dormant "already approved" target; a dependency
    imported by at least one production file is the plausible replacement
    source. Deliberately conservative - fewer than 2 production dependencies,
    or no dormant candidate at all, means this Maven-migration pattern
    simply doesn't apply (NOT_APPLICABLE); 2+ dormant candidates or 2+ active
    candidates means the goal's own replacement intent can't be resolved
    confidently (INDETERMINATE - callers must not silently treat this as "no
    obligation applies").

    Only fires when the goal expresses explicit replacement intent
    (_goal_expresses_replacement_intent) - a goal with no such intent never
    even reaches the (more expensive) repository-grounding scan."""
    if not _goal_expresses_replacement_intent(goal):
        return MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE, reason="goal expresses no replacement intent")
    pom_path = os.path.join(workspace_path, "pom.xml")
    if not os.path.isfile(pom_path):
        return MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE, reason="no pom.xml (Maven adapter only)")
    try:
        with open(pom_path, "r", encoding="utf-8", errors="replace") as fh:
            pom_content = fh.read()
    except OSError:
        return MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE, reason="pom.xml unreadable")

    artifacts = _parse_maven_dependency_artifacts(pom_content)
    production_artifacts = [a for a in artifacts if a.get("scope", "compile") not in _NON_PRODUCTION_SCOPES]
    if len(production_artifacts) < 2:
        return MigrationResolution(
            MigrationResolutionStatus.NOT_APPLICABLE, reason="fewer than 2 production-scope dependencies declared",
        )

    java_files = _scan_java_files(workspace_path)
    production_files = {
        path: content for path, content in java_files.items() if not _is_test_source_path(path)
    }

    usage: Dict[str, Set[str]] = {}
    for artifact in production_artifacts:
        key = f"{artifact['group']}:{artifact['artifact']}"
        tokens = _artifact_import_tokens(artifact["artifact"])
        usage[key] = {
            path for path, content in production_files.items()
            if _file_imports_any_token(content, tokens)
        }

    unused = {key for key, files in usage.items() if not files}
    used = {key for key, files in usage.items() if files}
    if not unused or not used:
        return MigrationResolution(
            MigrationResolutionStatus.NOT_APPLICABLE,
            reason="no dormant production-scope dependency candidate found",
        )
    if len(unused) != 1 or len(used) != 1:
        return MigrationResolution(
            MigrationResolutionStatus.INDETERMINATE,
            reason=(
                f"goal expresses replacement intent but source/target dependency identity is "
                f"ambiguous: {len(used)} candidate source(s), {len(unused)} candidate target(s)"
            ),
        )

    source_key = next(iter(used))
    target_key = next(iter(unused))
    source_artifact = next(a for a in production_artifacts if f"{a['group']}:{a['artifact']}" == source_key)
    target_artifact = next(a for a in production_artifacts if f"{a['group']}:{a['artifact']}" == target_key)

    obligation = MigrationObligation(
        migration_kind="dependency_or_technology_replacement",
        source_identity=source_artifact["artifact"],
        target_identity=target_artifact["artifact"],
        source_artifacts=[source_key],
        target_artifacts=[target_key],
        grounded_consumers=sorted(usage[source_key]),
    )
    return MigrationResolution(MigrationResolutionStatus.RESOLVED, obligation=obligation)


def find_migration_incomplete(
    obligation: MigrationObligation, worktree_path: str,
) -> Optional[Dict[str, Any]]:
    """Terminal completion check against the final candidate state
    (worktree_path - the real, fully-applied post-write tree, matching this
    module's terminal-time convention, e.g. static_checks.py's
    run_static_checks(worktree_path, ...) and find_established_stack_drift).
    Returns None (obligation satisfied) or a dict of unmet TERMINAL_
    REQUIREMENTS reason codes plus the evidence behind them."""
    pom_path = os.path.join(worktree_path, "pom.xml")
    pom_content = ""
    if os.path.isfile(pom_path):
        try:
            with open(pom_path, "r", encoding="utf-8", errors="replace") as fh:
                pom_content = fh.read()
        except OSError:
            pass
    final_artifacts = {
        f"{a['group']}:{a['artifact']}" for a in _parse_maven_dependency_artifacts(pom_content)
    }
    target_present = bool(set(obligation.target_artifacts) & final_artifacts)
    source_absent = not (set(obligation.source_artifacts) & final_artifacts)

    java_files = _scan_java_files(worktree_path)
    source_tokens = [
        token for artifact_key in obligation.source_artifacts
        for token in _artifact_import_tokens(artifact_key.split(":", 1)[-1])
    ]
    target_tokens = [
        token for artifact_key in obligation.target_artifacts
        for token in _artifact_import_tokens(artifact_key.split(":", 1)[-1])
    ]

    source_usage_files = sorted(
        path for path, content in java_files.items()
        if _file_imports_any_token(content, source_tokens)
    )
    source_usage_absent = not source_usage_files

    unmigrated_consumers = sorted(
        consumer for consumer in obligation.grounded_consumers
        if consumer not in java_files or not _file_imports_any_token(java_files[consumer], target_tokens)
    )
    grounded_consumer_uses_target = not unmigrated_consumers

    if target_present and source_absent and source_usage_absent and grounded_consumer_uses_target:
        return None

    reason_codes = []
    # manifest_files: evidence for the two reason codes that are purely
    # about the DECLARATION (pom.xml), not any .java file's content - found
    # live, PRV-05 (2026-08-28): when JsonService is fully migrated (no
    # remaining source usage) but pom.xml still declares the old dependency,
    # BOTH source_usage_files and unmigrated_consumers are correctly empty -
    # there's real nothing wrong with any consumer file - but that left the
    # resulting failure with NO likely_files at all, unable to point the
    # retry/attribution pipeline at the one file that actually needs fixing
    # (pom.xml, typically owned by a DIFFERENT, already-completed subtask).
    manifest_files: List[str] = []
    if not target_present or not source_absent:
        if os.path.isfile(os.path.join(worktree_path, "pom.xml")):
            manifest_files.append("pom.xml")
    if not target_present:
        reason_codes.append("TARGET_DEPENDENCY_MISSING")
    if not source_absent:
        reason_codes.append("SOURCE_DEPENDENCY_REMAINS")
    if not source_usage_absent:
        reason_codes.append("SOURCE_USAGE_REMAINS")
    if not grounded_consumer_uses_target:
        reason_codes.append("TARGET_NOT_USED_BY_GROUNDED_OWNER")

    return {
        "reason_codes": reason_codes,
        "source_identity": obligation.source_identity,
        "target_identity": obligation.target_identity,
        "grounded_consumers": obligation.grounded_consumers,
        "source_usage_files": source_usage_files,
        "unmigrated_consumers": unmigrated_consumers,
        "manifest_files": manifest_files,
    }


