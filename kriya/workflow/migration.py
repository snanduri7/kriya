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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from kriya.workflow.obligations import ObligationAuthority, ObligationKind, ObligationRecord, ObligationStatus
from kriya.workflow.plan_schema import FileOwnershipRelation

if TYPE_CHECKING:
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.plan_schema import EngineeringPlan

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


class MigrationValidationScope(str, Enum):
    """Which of find_migration_incomplete()'s four requirements are DUE at
    this call - PRV-05 run 7 (2026-08-28): the original (pre-this-fix)
    version asked a TERMINAL question ("is the whole migration done") at
    every non-terminal subtask boundary that merely touched a grounded
    consumer, which fails a perfectly correct intermediate state (s1 fully
    migrates its own consumer; s4, a later dependency-ordered subtask, still
    owns removing the dependency) as if it were a real defect. See this
    module's own top-of-file docstring's PRV-05 run-6 section for the
    sibling identity-resolution defect this is NOT - that one was about WHO
    the source/target are; this one is about WHEN each requirement becomes
    enforceable.

    CURRENT_SUBTASK: only requirements whose implicated file(s) are owned by
    THIS subtask or an already-completed (PAST_ORDERED) one are due; a
    requirement whose only implicated files are owned by a not-yet-reached
    (FUTURE_ORDERED) subtask is PENDING, not FAILED. Requires
    current_subtask_id + engineering_plan; without both (a legacy,
    non-MA6-structured caller), scope degrades to full TERMINAL-equivalent
    behavior - never silently permissive.

    TERMINAL: every requirement is due, unconditionally - the original,
    unscoped behavior, correct once the whole plan has actually executed.
    "an obligation becomes enforceable when its owning stage is reached, and
    globally enforceable at terminal validation" - not "skip the check until
    the end", which would let a plan whose OWNING stage (e.g. s4) botches
    the removal slide all the way to the terminal gate undetected of its own
    stage-local failure."""

    CURRENT_SUBTASK = "current_subtask"
    TERMINAL = "terminal"


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


def _requirement_is_due(
    implicated_paths: List[str],
    *,
    validation_scope: MigrationValidationScope,
    engineering_plan: Optional["EngineeringPlan"],
    current_subtask_id: Optional[str],
) -> bool:
    """Is a currently-unmet requirement enforceable RIGHT NOW, or is it
    legitimately owned by a not-yet-reached subtask (PENDING)? TERMINAL
    scope, a missing plan/subtask_id (legacy caller - see
    MigrationValidationScope's own docstring), or an empty implicated-path
    list (nothing to locate ownership for - don't silently defer) all fall
    back to "due", matching this function's pre-stage-aware behavior
    exactly. Otherwise due unless EVERY implicated path is owned by a
    strictly FUTURE_ORDERED subtask - an UNOWNED path (nothing in the plan
    will ever touch it) is deliberately treated as due, not deferred; a
    mix of one CURRENT/PAST_ORDERED/UNOWNED path and one FUTURE_ORDERED one
    is due too, since at least one instance of the violation is genuinely
    actionable at this point in the plan."""
    if validation_scope == MigrationValidationScope.TERMINAL:
        return True
    if engineering_plan is None or current_subtask_id is None:
        return True
    if not implicated_paths:
        return True
    relations = [
        engineering_plan.classify_file_ownership(current_subtask_id, path)
        for path in implicated_paths
    ]
    return any(relation != FileOwnershipRelation.FUTURE_ORDERED for relation in relations)


def _owner_subtask_id_for_paths(
    engineering_plan: Optional["EngineeringPlan"], paths: List[str],
) -> Optional[str]:
    """The single subtask that owns EVERY path in `paths`, or None when
    there's no plan to consult, no paths, or the paths don't all resolve
    to the same single unambiguous owner (EngineeringPlan.file_owner()
    already returns None for a multi-owner/sequential-chain file - see its
    own docstring; this stays conservative rather than guessing which
    co-owner is "the" owner for obligation-record purposes)."""
    if engineering_plan is None or not paths:
        return None
    owners = {
        owner.id if (owner := engineering_plan.file_owner(path)) else None
        for path in paths
    }
    if len(owners) == 1:
        return next(iter(owners))
    return None


_MIGRATION_OBLIGATION_IDS = {
    "TARGET_DEPENDENCY_MISSING": "migration.target_dependency_present",
    "SOURCE_DEPENDENCY_REMAINS": "migration.source_dependency_absent",
    "SOURCE_USAGE_REMAINS": "migration.source_usage_absent",
    "TARGET_NOT_USED_BY_GROUNDED_OWNER": "migration.grounded_consumer_uses_target",
}


def find_migration_incomplete(
    obligation: MigrationObligation,
    worktree_path: str,
    *,
    current_subtask_id: Optional[str] = None,
    engineering_plan: Optional["EngineeringPlan"] = None,
    validation_scope: MigrationValidationScope = MigrationValidationScope.TERMINAL,
    obligation_ledger: Optional["ObligationLedger"] = None,
    revision: Any = None,
    source: str = "migration.find_migration_incomplete",
) -> Optional[Dict[str, Any]]:
    """Completion check against the CURRENT candidate state (worktree_path -
    the real, fully-applied tree as of this call, matching this module's
    established convention, e.g. static_checks.py's
    run_static_checks(worktree_path, ...) and find_established_stack_drift).
    Returns None (every DUE requirement satisfied) or a dict of unmet
    reason codes plus the evidence behind them.

    validation_scope (PRV-05 run 7, 2026-08-28) - see
    MigrationValidationScope's own docstring for the full incident this
    closes: default TERMINAL preserves this function's exact original
    behavior (every requirement always due) for every existing caller that
    doesn't pass the new keyword-only args - the workflow_controller.py
    terminal global gate, and any pre-this-fix test. Passing
    validation_scope=CURRENT_SUBTASK plus current_subtask_id/
    engineering_plan additionally computes which requirements are DUE at
    THIS subtask boundary (see _requirement_is_due above) versus PENDING
    (a real, currently-unmet condition whose owning subtask hasn't run
    yet) - PENDING requirements never appear in the returned dict's
    reason_codes/evidence and never cause a non-None return on their own;
    they're surfaced separately, in pending_reason_codes, for diagnostics
    only. Uses the validated plan as the authority on ownership - never
    infers it from filenames.

    obligation_ledger (PRV-05 run #8, MA8 - kriya/workflow/obligations.py):
    purely an optional side effect, added without changing any of the
    detection logic above - when supplied, records all FOUR individual
    migration requirements (target present / source absent / source usage
    absent / grounded consumer uses target) as DETERMINISTIC
    ObligationRecords, SATISFIED/VIOLATED/PENDING exactly matching this
    call's own due-ness computation, every time this function runs
    (including the fully-satisfied None-return path, where all four are
    recorded SATISFIED). evidence carries source_identity/target_identity
    so a later, unrelated subsystem (SpecCompliance arbitration,
    attempt.py) can correlate a free-text judgment claim back to a
    specific migration obligation without importing MigrationObligation
    itself."""
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

    # (reason_code, implicated_paths) - the ONLY paths _requirement_is_due()
    # consults for that specific requirement, so a violation whose paths are
    # entirely owned by a not-yet-reached subtask is deferred without ever
    # touching an unrelated requirement's due-ness (e.g. s1's own
    # SOURCE_USAGE_REMAINS over JsonService.java stays due even though
    # SOURCE_DEPENDENCY_REMAINS over pom.xml, owned by s4, is pending).
    candidate_findings = [
        ("TARGET_DEPENDENCY_MISSING", not target_present, manifest_files),
        ("SOURCE_DEPENDENCY_REMAINS", not source_absent, manifest_files),
        ("SOURCE_USAGE_REMAINS", not source_usage_absent, source_usage_files),
        ("TARGET_NOT_USED_BY_GROUNDED_OWNER", not grounded_consumer_uses_target, unmigrated_consumers),
    ]

    due_reason_codes: List[str] = []
    pending_reason_codes: List[str] = []
    due_manifest_files: List[str] = []
    due_source_usage_files: List[str] = []
    due_unmigrated_consumers: List[str] = []
    for code, violated, implicated_paths in candidate_findings:
        if not violated:
            if obligation_ledger is not None:
                obligation_ledger.record(ObligationRecord(
                    id=_MIGRATION_OBLIGATION_IDS[code], kind=ObligationKind.MIGRATION_COMPLETION,
                    status=ObligationStatus.SATISFIED, authority=ObligationAuthority.DETERMINISTIC,
                    description=code, source=source, revision=revision,
                    evidence={
                        "source_identity": obligation.source_identity,
                        "target_identity": obligation.target_identity,
                    },
                    owner_subtask_id=_owner_subtask_id_for_paths(engineering_plan, implicated_paths),
                    terminal_required=True,
                    repair_scope=tuple(implicated_paths),
                ))
            continue
        is_due = _requirement_is_due(
            implicated_paths,
            validation_scope=validation_scope,
            engineering_plan=engineering_plan,
            current_subtask_id=current_subtask_id,
        )
        if obligation_ledger is not None:
            obligation_ledger.record(ObligationRecord(
                id=_MIGRATION_OBLIGATION_IDS[code], kind=ObligationKind.MIGRATION_COMPLETION,
                status=ObligationStatus.VIOLATED if is_due else ObligationStatus.PENDING,
                authority=ObligationAuthority.DETERMINISTIC,
                description=code, source=source, revision=revision,
                evidence={
                    "source_identity": obligation.source_identity,
                    "target_identity": obligation.target_identity,
                    "implicated_paths": list(implicated_paths),
                },
                owner_subtask_id=_owner_subtask_id_for_paths(engineering_plan, implicated_paths),
                terminal_required=True,
                repair_scope=tuple(implicated_paths),
            ))
        if is_due:
            due_reason_codes.append(code)
            if code in ("TARGET_DEPENDENCY_MISSING", "SOURCE_DEPENDENCY_REMAINS"):
                due_manifest_files = manifest_files
            elif code == "SOURCE_USAGE_REMAINS":
                due_source_usage_files = source_usage_files
            elif code == "TARGET_NOT_USED_BY_GROUNDED_OWNER":
                due_unmigrated_consumers = unmigrated_consumers
        else:
            pending_reason_codes.append(code)

    if not due_reason_codes:
        # Every currently-unmet requirement is legitimately owned by a
        # not-yet-reached subtask (or there were none) - satisfied FOR THIS
        # STAGE, not a silent "skip until terminal": TERMINAL-scope callers
        # (the global gate) still see every requirement as due and will
        # correctly fail this same tree if it's the final state.
        return None

    return {
        "reason_codes": due_reason_codes,
        "pending_reason_codes": pending_reason_codes,
        "source_identity": obligation.source_identity,
        "target_identity": obligation.target_identity,
        "grounded_consumers": obligation.grounded_consumers,
        "source_usage_files": due_source_usage_files,
        "unmigrated_consumers": due_unmigrated_consumers,
        "manifest_files": due_manifest_files,
    }


