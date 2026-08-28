"""Tests for kriya/workflow/migration.py - deterministic dependency/
technology migration obligation resolution and terminal completion
validation. Fixtures reproduce the PRV-05 (2026-08-28) incident: a goal
explicitly requesting replacement of the existing JSON serialization
library with the one already approved for the repository, where Quality
Gates PASSED despite the migration never completing.
"""
import os

from kriya.workflow.migration import (
    MigrationObligation,
    MigrationResolutionStatus,
    MigrationValidationScope,
    find_migration_incomplete,
    resolve_migration_resolution,
)
from kriya.workflow.obligations import ObligationKind, ObligationLedger, ObligationStatus
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.triage import ChangeKind

_POM_BOTH = (
    "<project><dependencies>\n"
    "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
    "<version>2.11.0</version></dependency>\n"
    "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
    "<version>2.17.2</version></dependency>\n"
    "</dependencies></project>\n"
)
_POM_JACKSON_ONLY = (
    "<project><dependencies>\n"
    "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
    "<version>2.17.2</version></dependency>\n"
    "</dependencies></project>\n"
)
_GSON_SERVICE = (
    "package com.example;\n"
    "import com.google.gson.Gson;\n"
    "public class JsonService {\n"
    " private final Gson gson=new Gson();\n"
    " public String serialize(Object c){ return gson.toJson(c); }\n"
    "}\n"
)
_JACKSON_SERVICE = (
    "package com.example;\n"
    "import com.fasterxml.jackson.databind.ObjectMapper;\n"
    "public class JsonService {\n"
    " private final ObjectMapper mapper=new ObjectMapper();\n"
    " public String serialize(Object c) throws Exception { return mapper.writeValueAsString(c); }\n"
    "}\n"
)
_GOAL = (
    "Replace the existing JSON serialization library with the JSON library "
    "already approved for this repository."
)
_TARGET_FILES = ["src/main/java/com/example/JsonService.java"]


def _write(root, pom, service_content):
    (root / "pom.xml").write_text(pom)
    svc = root / _TARGET_FILES[0]
    svc.parent.mkdir(parents=True, exist_ok=True)
    svc.write_text(service_content)


def test_resolve_migration_resolution_no_replacement_intent_is_not_applicable(tmp_path):
    """No explicit migration goal -> the validator must not invent an
    obligation, matching this codebase's established narrow-intent-gate
    convention (e.g. file_resolution.py's _goal_explicitly_requests_api_change)."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    resolution = resolve_migration_resolution("Add a displayName field to Customer", str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.NOT_APPLICABLE
    assert resolution.obligation is None


def test_resolve_migration_resolution_grounds_source_and_target_from_repo_state(tmp_path):
    """Neither Gson nor Jackson is literally named in the goal text - source
    is resolved as whichever production-scope dependency is currently used,
    target as whichever OTHER production-scope dependency is used nowhere in
    the baseline repo (PRV-05's own fixture shape: both already declared,
    only one used)."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    resolution = resolve_migration_resolution(_GOAL, str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.RESOLVED
    obligation = resolution.obligation
    assert obligation is not None
    assert obligation.source_identity == "gson"
    assert obligation.target_identity == "jackson-databind"
    assert obligation.grounded_consumers == _TARGET_FILES
    assert obligation.migration_kind == "dependency_or_technology_replacement"


def test_resolve_migration_resolution_no_pom_is_not_applicable(tmp_path):
    (tmp_path / _TARGET_FILES[0]).parent.mkdir(parents=True)
    (tmp_path / _TARGET_FILES[0]).write_text(_GSON_SERVICE)
    resolution = resolve_migration_resolution(_GOAL, str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.NOT_APPLICABLE


def test_resolve_migration_resolution_not_applicable_when_no_dormant_dependency(tmp_path):
    """Both declared dependencies are already in use somewhere - no dormant
    "already approved" candidate exists at all, so this Maven-migration
    pattern simply doesn't fit (NOT_APPLICABLE), distinct from the genuinely
    ambiguous case below (2+ dormant candidates)."""
    root = tmp_path
    (root / "pom.xml").write_text(_POM_BOTH)
    svc = root / _TARGET_FILES[0]
    svc.parent.mkdir(parents=True)
    svc.write_text(_GSON_SERVICE)
    other = root / "src/main/java/com/example/Other.java"
    other.write_text(
        "package com.example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n"
        "public class Other { ObjectMapper m; }\n"
    )
    resolution = resolve_migration_resolution(_GOAL, str(root))
    assert resolution.status == MigrationResolutionStatus.NOT_APPLICABLE
    assert resolution.obligation is None


def test_resolve_migration_resolution_indeterminate_when_multiple_dormant_candidates(tmp_path):
    """Two production-scope dependencies both look dormant (unused) - the
    goal DOES express replacement intent, so this ambiguity must be reported
    as INDETERMINATE, never silently downgraded to "no obligation applies" -
    PRV-05 run 6 (2026-08-28) found that exact silent downgrade is what let
    a false PASS through both the per-attempt authorization check and the
    terminal gate."""
    pom = (
        "<project><dependencies>\n"
        "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
        "<version>2.11.0</version></dependency>\n"
        "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
        "<version>2.17.2</version></dependency>\n"
        "<dependency><groupId>com.squareup.moshi</groupId><artifactId>moshi</artifactId>"
        "<version>1.15.0</version></dependency>\n"
        "</dependencies></project>\n"
    )
    _write(tmp_path, pom, _GSON_SERVICE)
    resolution = resolve_migration_resolution(_GOAL, str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.INDETERMINATE
    assert resolution.obligation is None


def test_resolve_migration_resolution_excludes_test_scoped_dependency_from_ambiguity(tmp_path):
    """PRV-05 run 6 regression (2026-08-28): a declared but currently-unused
    TEST-scoped dependency (JUnit) must NOT count as a second "dormant"
    candidate and push resolution into ambiguity - it's structurally
    irrelevant to a production library replacement. Reproduces the exact
    live fixture shape: Gson (used, production), Jackson (unused, production
    - the real target), JUnit (unused, declared <scope>test</scope>, no test
    file exists yet). Before this fix, resolve_migration_obligation() found
    TWO unused candidates (Jackson AND JUnit) and returned None, which left
    the dependency-preservation validator fighting the migration for the
    entire live run."""
    pom = (
        "<project><dependencies>\n"
        "<dependency><groupId>com.google.code.gson</groupId><artifactId>gson</artifactId>"
        "<version>2.11.0</version></dependency>\n"
        "<dependency><groupId>com.fasterxml.jackson.core</groupId><artifactId>jackson-databind</artifactId>"
        "<version>2.17.2</version></dependency>\n"
        "<dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId>"
        "<version>5.10.2</version><scope>test</scope></dependency>\n"
        "</dependencies></project>\n"
    )
    _write(tmp_path, pom, _GSON_SERVICE)
    resolution = resolve_migration_resolution(_GOAL, str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.RESOLVED
    assert resolution.obligation.source_identity == "gson"
    assert resolution.obligation.target_identity == "jackson-databind"


def test_resolve_migration_resolution_excludes_test_source_files_from_usage_scan(tmp_path):
    """A test file importing the target library must not make it look
    "used" and knock it out of target-candidacy - matches the real PRV-05
    fixture shape once JsonServiceTest.java (Jackson-based) exists
    alongside a still-Gson-based production JsonService.java."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    test_file = tmp_path / "src/test/java/com/example/JsonServiceTest.java"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "package com.example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n"
        "class JsonServiceTest { ObjectMapper m; }\n"
    )
    resolution = resolve_migration_resolution(_GOAL, str(tmp_path))
    assert resolution.status == MigrationResolutionStatus.RESOLVED
    assert resolution.obligation.target_identity == "jackson-databind"


def _obligation():
    return MigrationObligation(
        migration_kind="dependency_or_technology_replacement",
        source_identity="gson", target_identity="jackson-databind",
        source_artifacts=["com.google.code.gson:gson"],
        target_artifacts=["com.fasterxml.jackson.core:jackson-databind"],
        grounded_consumers=_TARGET_FILES,
    )


def test_find_migration_incomplete_none_when_fully_migrated(tmp_path):
    _write(tmp_path, _POM_JACKSON_ONLY, _JACKSON_SERVICE)
    assert find_migration_incomplete(_obligation(), str(tmp_path)) is None


def test_find_migration_incomplete_when_nothing_changed():
    """The real PRV-05 hardened incident's final shape: both dependencies
    still declared, the grounded owner still on the old library."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        root = Path(d)
        _write(root, _POM_BOTH, _GSON_SERVICE)
        gap = find_migration_incomplete(_obligation(), str(root))
        assert gap is not None
        assert set(gap["reason_codes"]) == {
            "SOURCE_DEPENDENCY_REMAINS", "SOURCE_USAGE_REMAINS", "TARGET_NOT_USED_BY_GROUNDED_OWNER",
        }


def test_find_migration_incomplete_parallel_implementation_does_not_satisfy_migration(tmp_path):
    """A new parallel utility using the target technology must NOT satisfy
    the migration while the grounded existing owner still uses the source -
    the exact PRV-05 review finding: 'a new Jackson JsonUtil must not
    satisfy migration while the existing JsonService still uses Gson'."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    (tmp_path / "src/main/java/com/example/JsonUtil.java").write_text(
        "package com.example;\nimport com.fasterxml.jackson.databind.ObjectMapper;\n"
        "public class JsonUtil { private final ObjectMapper m = new ObjectMapper(); }\n"
    )
    gap = find_migration_incomplete(_obligation(), str(tmp_path))
    assert gap is not None
    assert "TARGET_NOT_USED_BY_GROUNDED_OWNER" in gap["reason_codes"]
    assert gap["unmigrated_consumers"] == _TARGET_FILES


def test_find_migration_incomplete_old_usage_remains_even_if_dependency_removed(tmp_path):
    _write(tmp_path, _POM_JACKSON_ONLY, _GSON_SERVICE)
    gap = find_migration_incomplete(_obligation(), str(tmp_path))
    assert gap is not None
    assert "SOURCE_USAGE_REMAINS" in gap["reason_codes"]


def test_find_migration_incomplete_preserves_public_api_shape_on_pass(tmp_path):
    """Explicit migration replacing the implementation while preserving the
    grounded owner's own public method (serialize(Object) -> String) must
    still PASS - this validator only checks migration completion, not API
    shape (EXISTING_CONTRACT_PRESERVATION, a separate existing gate, owns
    that)."""
    _write(tmp_path, _POM_JACKSON_ONLY, _JACKSON_SERVICE)
    assert "public String serialize(Object c)" in _JACKSON_SERVICE
    assert find_migration_incomplete(_obligation(), str(tmp_path)) is None


def test_find_migration_incomplete_names_the_manifest_when_consumer_is_fully_migrated(tmp_path):
    """Regression test for PRV-05 (2026-08-28 rerun): JsonService fully
    migrated to Jackson (no remaining source usage anywhere), but pom.xml
    still declares Gson. unmigrated_consumers and source_usage_files are
    BOTH correctly empty here - nothing is wrong with any consumer file -
    but the failure must still be able to point somewhere: manifest_files
    names pom.xml, the actual file that needs the fix (typically owned by
    a DIFFERENT, already-completed subtask)."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    gap = find_migration_incomplete(_obligation(), str(tmp_path))
    assert gap is not None
    assert gap["reason_codes"] == ["SOURCE_DEPENDENCY_REMAINS"]
    assert gap["unmigrated_consumers"] == []
    assert gap["source_usage_files"] == []
    assert gap["manifest_files"] == ["pom.xml"]


# --- validation_scope=CURRENT_SUBTASK (PRV-05 run 7, 2026-08-28) ---
# Reproduces the exact validated PRV-05 hardened run-7 plan: s1 owns
# JsonService.java only, s4 (a later, dependency-ordered subtask) owns
# removing the old dependency from pom.xml.

def _prv05_plan():
    s1 = Subtask(
        id="s1", description="identify usages, update imports", execution_method=ExecutionMethod.MODEL,
        depends_on=[],
        planned_files=[PlannedFile(path=_TARGET_FILES[0], action=FileAction.MODIFY)],
    )
    s2 = Subtask(
        id="s2", description="update production code", execution_method=ExecutionMethod.MODEL,
        depends_on=["s1"],
        planned_files=[PlannedFile(path=_TARGET_FILES[0], action=FileAction.MODIFY)],
    )
    s3 = Subtask(
        id="s3", description="update test code", execution_method=ExecutionMethod.MODEL,
        depends_on=["s1"],
        planned_files=[PlannedFile(
            path="src/test/java/com/example/JsonServiceTest.java", action=FileAction.CREATE,
        )],
    )
    s4 = Subtask(
        id="s4", description="remove old dependency", execution_method=ExecutionMethod.MODEL,
        depends_on=["s2", "s3"],
        planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)],
    )
    return EngineeringPlan(plan_id="prv05", kind=ChangeKind.REFACTOR, subtasks=[s1, s2, s3, s4])


def test_find_migration_incomplete_current_subtask_scope_pending_when_manifest_owned_by_future_subtask(tmp_path):
    """Required test 1: s1 owns JsonService.java only; s4 (later,
    dependency-ordered) owns pom.xml. JsonService.java is already fully
    migrated - the ONLY remaining violation is SOURCE_DEPENDENCY_REMAINS,
    whose evidence (pom.xml) is owned by a not-yet-reached subtask - s1 must
    PASS (gap is None), not fail for an obligation it was never responsible
    for. This is the exact PRV-05 run 7 incident."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s1", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
    )
    assert gap is None


def test_find_migration_incomplete_current_subtask_scope_pending_at_s2_and_s3_too(tmp_path):
    """Required tests 1 (continued): s2 and s3 are also upstream of s4 in
    the dependency order - same PENDING treatment."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    for subtask_id in ("s2", "s3"):
        gap = find_migration_incomplete(
            _obligation(), str(tmp_path),
            current_subtask_id=subtask_id, engineering_plan=plan,
            validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
        )
        assert gap is None, f"expected {subtask_id} to PASS (pending, not failed)"


def test_find_migration_incomplete_current_subtask_scope_fails_when_owning_stage_is_reached(tmp_path):
    """Required test 2: s4 IS the dependency-removal stage - the same
    still-declared-Gson condition must FAIL here, with pom.xml as the
    authoritative target (manifest_files), not silently pass through to
    terminal."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s4", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
    )
    assert gap is not None
    assert gap["reason_codes"] == ["SOURCE_DEPENDENCY_REMAINS"]
    assert gap["manifest_files"] == ["pom.xml"]
    assert gap["pending_reason_codes"] == []


def test_find_migration_incomplete_terminal_scope_always_fails_regardless_of_ownership(tmp_path):
    """Required test 3: TERMINAL scope (the global final-state gate) must
    still fail on the exact same tree even when asked "as if" it were s1 -
    validation_scope, not current_subtask_id, is what selects TERMINAL
    behavior; every requirement is due unconditionally."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s1", engineering_plan=plan,
        validation_scope=MigrationValidationScope.TERMINAL,
    )
    assert gap is not None
    assert gap["reason_codes"] == ["SOURCE_DEPENDENCY_REMAINS"]


def test_find_migration_incomplete_current_subtask_scope_without_plan_defaults_to_due(tmp_path):
    """Backward compatibility: a caller with no engineering_plan/
    current_subtask_id (a non-MA6-structured caller) must see the exact
    original always-due behavior, never silently permissive, even when it
    explicitly asks for CURRENT_SUBTASK scope."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
    )
    assert gap is not None
    assert gap["reason_codes"] == ["SOURCE_DEPENDENCY_REMAINS"]


def test_find_migration_incomplete_current_subtask_still_enforces_its_own_due_obligation(tmp_path):
    """The explicit non-goal from the PRV-05 run 7 fix design: stage-
    awareness must NOT become "skip SOURCE_DEPENDENCY_REMAINS until final
    gate" - s1's OWN obligation (has JsonService.java actually stopped
    using Gson?) must still FAIL at s1 even while the unrelated, future-
    owned SOURCE_DEPENDENCY_REMAINS is correctly PENDING."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)  # JsonService.java NOT migrated yet
    plan = _prv05_plan()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s1", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
    )
    assert gap is not None
    assert "SOURCE_USAGE_REMAINS" in gap["reason_codes"]
    assert "TARGET_NOT_USED_BY_GROUNDED_OWNER" in gap["reason_codes"]
    assert "SOURCE_DEPENDENCY_REMAINS" not in gap["reason_codes"]
    assert "SOURCE_DEPENDENCY_REMAINS" in gap["pending_reason_codes"]


# --- MA8 (PRV-05 run #8, 2026-08-28): obligation_ledger wiring -
# find_migration_incomplete() reports all FOUR individual migration
# obligations, not just an aggregate PASS/FAIL. ---

def test_find_migration_incomplete_records_all_four_obligations_when_satisfied(tmp_path):
    _write(tmp_path, _POM_JACKSON_ONLY, _JACKSON_SERVICE)
    ledger = ObligationLedger()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path), obligation_ledger=ledger, revision="terminal",
    )
    assert gap is None
    records = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
    assert len(records) == 4
    assert all(r.status == ObligationStatus.SATISFIED for r in records)
    assert {r.id for r in records} == {
        "migration.target_dependency_present",
        "migration.source_dependency_absent",
        "migration.source_usage_absent",
        "migration.grounded_consumer_uses_target",
    }
    assert all(r.evidence.get("source_identity") == "gson" for r in records)
    assert all(r.evidence.get("target_identity") == "jackson-databind" for r in records)


def test_find_migration_incomplete_records_pending_at_future_owner_stage(tmp_path):
    """Required test: future pom.xml owner -> source dependency absence
    remains PENDING in the ledger, not VIOLATED."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    ledger = ObligationLedger()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s1", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
        obligation_ledger=ledger, revision=1,
    )
    assert gap is None
    rec = ledger.current("migration.source_dependency_absent")
    assert rec.status == ObligationStatus.PENDING


def test_find_migration_incomplete_records_violated_when_owning_stage_reached(tmp_path):
    """Required test: manifest-owner stage reached -> it becomes
    enforceable (VIOLATED, not PENDING)."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    ledger = ObligationLedger()
    find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s4", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
        obligation_ledger=ledger, revision=4,
    )
    rec = ledger.current("migration.source_dependency_absent")
    assert rec.status == ObligationStatus.VIOLATED


def test_find_migration_incomplete_terminal_requires_no_pending_obligation(tmp_path):
    """Required test: terminal state -> no required obligation may remain
    pending/violated. Same tree as above, checked at TERMINAL scope -
    must FAIL, matching the terminal gate's own behavior."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    ledger = ObligationLedger()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        validation_scope=MigrationValidationScope.TERMINAL,
        obligation_ledger=ledger, revision="terminal",
    )
    assert gap is not None
    rec = ledger.current("migration.source_dependency_absent")
    assert rec.status == ObligationStatus.VIOLATED
    assert ledger.violated_ids(ObligationKind.MIGRATION_COMPLETION) == ["migration.source_dependency_absent"]


def test_find_migration_incomplete_detects_true_regression(tmp_path):
    """Required test: a previously-SATISFIED migration requirement later
    becomes deterministically VIOLATED - the ledger must detect the
    regression (a real, non-timing-related defect, unlike the PENDING/
    FUTURE_ORDERED case above)."""
    ledger = ObligationLedger()
    _write(tmp_path, _POM_JACKSON_ONLY, _JACKSON_SERVICE)
    gap1 = find_migration_incomplete(
        _obligation(), str(tmp_path), obligation_ledger=ledger, revision=1,
    )
    assert gap1 is None
    assert ledger.current("migration.source_dependency_absent").status == ObligationStatus.SATISFIED

    # Simulate a later attempt where Gson got reintroduced into pom.xml.
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    gap2 = find_migration_incomplete(
        _obligation(), str(tmp_path), obligation_ledger=ledger, revision=2,
    )
    assert gap2 is not None
    assert "SOURCE_DEPENDENCY_REMAINS" in gap2["reason_codes"]
    regressed_ids = [r.obligation_id for r in ledger.regressions]
    assert "migration.source_dependency_absent" in regressed_ids


# --- Batch 1 (spec §12/§27): owner_subtask_id/terminal_required/
# repair_scope on migration obligation records. ---

def test_migration_obligations_carry_terminal_required():
    ledger = ObligationLedger()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        _write(Path(d), _POM_JACKSON_ONLY, _JACKSON_SERVICE)
        gap = find_migration_incomplete(_obligation(), d, obligation_ledger=ledger, revision=1)
        assert gap is None
        recs = ledger.current_by_kind(ObligationKind.MIGRATION_COMPLETION)
        assert recs and all(r.terminal_required for r in recs)


def test_migration_obligation_owner_and_repair_scope_resolve_to_owning_stage(tmp_path):
    """s1 owns JsonService.java (already migrated); s4 (dependency-ordered
    later) owns pom.xml. The one still-unsatisfied requirement's owner
    must resolve to s4, and its repair_scope must be exactly pom.xml - not
    an arbitrary/empty scope, and not JsonService.java (s1's own file)."""
    _write(tmp_path, _POM_BOTH, _JACKSON_SERVICE)
    plan = _prv05_plan()
    ledger = ObligationLedger()
    gap = find_migration_incomplete(
        _obligation(), str(tmp_path),
        current_subtask_id="s1", engineering_plan=plan,
        validation_scope=MigrationValidationScope.CURRENT_SUBTASK,
        obligation_ledger=ledger, revision=1,
    )
    assert gap is None  # PENDING, owned by future s4 - s1 passes
    rec = ledger.current("migration.source_dependency_absent")
    assert rec.status == ObligationStatus.PENDING
    assert rec.owner_subtask_id == "s4"
    assert rec.repair_scope == ("pom.xml",)


def test_migration_obligation_owner_none_when_no_engineering_plan_supplied():
    """Legacy/non-MA6 callers pass no engineering_plan - owner_subtask_id
    must degrade to None, not raise or guess."""
    ledger = ObligationLedger()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        _write(Path(d), _POM_BOTH, _GSON_SERVICE)
        find_migration_incomplete(_obligation(), d, obligation_ledger=ledger, revision=1)
        rec = ledger.current("migration.source_dependency_absent")
        assert rec.owner_subtask_id is None
