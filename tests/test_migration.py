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
    find_migration_incomplete,
    resolve_migration_resolution,
)

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
