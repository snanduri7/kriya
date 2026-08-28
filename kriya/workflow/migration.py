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
scanning) - see resolve_migration_obligation's own docstring for why source/
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
UNREQUESTED_ARCHITECTURAL_SURFACE (PRV-04)."""
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

_IGNORED_DIRS = {".git", ".kriya", "target", "build", "dist", "node_modules", ".venv", "venv"}


@dataclass(frozen=True)
class MigrationObligation:
    migration_kind: str
    source_identity: str
    target_identity: str
    source_artifacts: List[str]
    target_artifacts: List[str]
    grounded_consumers: List[str]


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
        artifacts.append({"group": group.text.strip(), "artifact": artifact.text.strip()})
    return artifacts


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


def resolve_migration_obligation(
    goal: str, workspace_path: str, target_files: List[str],
) -> Optional[MigrationObligation]:
    """Resolves source/target identity from repository GROUNDING, not from
    literal names in the goal text - PRV-05's real goal ("the JSON library
    already approved for this repository") never names either library at
    all, so a name-matching approach would miss the exact incident this
    exists to catch. Instead: a Maven dependency declared but imported by
    NOTHING anywhere in the baseline workspace is a plausible dormant
    "already approved" target; among the goal's own grounded scope
    (`target_files`), a dependency imported by exactly one of those files is
    the plausible replacement source. Deliberately conservative at every
    ambiguous branch (zero or 2+ dormant dependencies, zero or 2+ candidate
    sources) - returns None rather than guess, matching this module's
    established practice (find_established_stack_drift et al.) of a missed
    catch costing nothing beyond skipping this one extra check, while a
    false positive could wrongly block a legitimate PASS.

    Only fires when the goal expresses explicit replacement intent
    (_goal_expresses_replacement_intent) - a goal with no such intent never
    even reaches the (more expensive) repository-grounding scan."""
    if not _goal_expresses_replacement_intent(goal):
        return None
    pom_path = os.path.join(workspace_path, "pom.xml")
    if not os.path.isfile(pom_path):
        return None
    try:
        with open(pom_path, "r", encoding="utf-8", errors="replace") as fh:
            pom_content = fh.read()
    except OSError:
        return None
    artifacts = _parse_maven_dependency_artifacts(pom_content)
    if len(artifacts) < 2:
        return None

    java_files = _scan_java_files(workspace_path)
    usage: Dict[str, Set[str]] = {}
    for artifact in artifacts:
        key = f"{artifact['group']}:{artifact['artifact']}"
        tokens = _artifact_import_tokens(artifact["artifact"])
        usage[key] = {
            path for path, content in java_files.items()
            if _file_imports_any_token(content, tokens)
        }

    unused = {key for key, files in usage.items() if not files}
    if len(unused) != 1:
        return None
    target_key = next(iter(unused))

    grounded_scope = set(target_files) & set(java_files)
    if not grounded_scope:
        return None
    candidate_sources = {
        key for key, files in usage.items()
        if key != target_key and files & grounded_scope
    }
    if len(candidate_sources) != 1:
        return None
    source_key = next(iter(candidate_sources))

    source_artifact = next(a for a in artifacts if f"{a['group']}:{a['artifact']}" == source_key)
    target_artifact = next(a for a in artifacts if f"{a['group']}:{a['artifact']}" == target_key)

    return MigrationObligation(
        migration_kind="dependency_or_technology_replacement",
        source_identity=source_artifact["artifact"],
        target_identity=target_artifact["artifact"],
        source_artifacts=[source_key],
        target_artifacts=[target_key],
        grounded_consumers=sorted(usage[source_key] & grounded_scope),
    )


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


def resolve_migration_obligation_for_workspace(
    goal: str, workspace_path: str,
) -> Optional[MigrationObligation]:
    """resolve_migration_obligation grounded against every known .java file
    in the workspace, not one subtask's own narrow planned-file scope - for
    a caller (the global final-state check, kriya/workflow/
    workflow_controller.py) that needs to know whether an obligation exists
    at all across the WHOLE plan's final applied state, independent of
    which one file happened to establish it. Same grounding shape as
    resolve_authorized_dependency_removals above, for the same reason."""
    java_files = _scan_java_files(workspace_path)
    return resolve_migration_obligation(goal, workspace_path, sorted(java_files.keys()))


def resolve_authorized_dependency_removals(goal: str, workspace_path: str) -> "set[str]":
    """Maven artifact keys (group:artifact) an explicit migration goal
    authorizes REMOVING - the SOURCE side of whatever
    resolve_migration_obligation would resolve, grounded against every known
    .java file in the workspace rather than one subtask's own narrow
    planned-file list.

    Found live, PRV-05 (2026-08-28, run 5): PolymorphicValidator.
    run_compile_check()'s dependency-preservation check (kriya/tools/
    validate.py) unconditionally rejects ANY pom.xml dependency removal,
    with zero awareness of an explicit, goal-authorized migration - it
    restored Gson every time s1 (the subtask that OWNS pom.xml) tried to
    remove it, even though the top-level goal explicitly says to replace
    it. resolve_migration_obligation's own grounded_scope requirement
    (target_files must intersect a real .java consumer) is the wrong shape
    for THIS caller: a subtask that only owns pom.xml (planned_files=
    ['pom.xml'], not a .java file at all) can never itself supply a
    grounded consumer, so it would never get authorization to remove
    anything under the stricter API. This function grounds against every
    known .java file in the workspace instead, since the question being
    asked here is only "is removing this dependency authorized by the goal
    at all" - not "which specific file consumes it," which is
    find_migration_incomplete's job at terminal validation time.

    Returns the SOURCE artifact key(s) - safe to remove - or an empty set
    when no unambiguous, goal-authorized migration can be resolved (same
    conservative degrade as resolve_migration_obligation itself: a missed
    authorization just means the existing preservation rule stays in
    force, never a security regression)."""
    java_files = _scan_java_files(workspace_path)
    obligation = resolve_migration_obligation(goal, workspace_path, sorted(java_files.keys()))
    return set(obligation.source_artifacts) if obligation else set()
