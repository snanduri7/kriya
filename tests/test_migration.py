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
    find_migration_incomplete,
    resolve_migration_obligation,
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


def test_resolve_migration_obligation_no_replacement_intent_returns_none(tmp_path):
    """No explicit migration goal -> the validator must not invent an
    obligation, matching this codebase's established narrow-intent-gate
    convention (e.g. file_resolution.py's _goal_explicitly_requests_api_change)."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    assert resolve_migration_obligation(
        "Add a displayName field to Customer", str(tmp_path), _TARGET_FILES,
    ) is None


def test_resolve_migration_obligation_grounds_source_and_target_from_repo_state(tmp_path):
    """Neither Gson nor Jackson is literally named in the goal text - source
    is resolved as whatever the grounded consumer currently imports, target
    as whichever OTHER declared dependency is used nowhere in the baseline
    repo (PRV-05's own fixture shape: both already declared, only one used)."""
    _write(tmp_path, _POM_BOTH, _GSON_SERVICE)
    obligation = resolve_migration_obligation(_GOAL, str(tmp_path), _TARGET_FILES)
    assert obligation is not None
    assert obligation.source_identity == "gson"
    assert obligation.target_identity == "jackson-databind"
    assert obligation.grounded_consumers == _TARGET_FILES
    assert obligation.migration_kind == "dependency_or_technology_replacement"


def test_resolve_migration_obligation_no_pom_returns_none(tmp_path):
    (tmp_path / _TARGET_FILES[0]).parent.mkdir(parents=True)
    (tmp_path / _TARGET_FILES[0]).write_text(_GSON_SERVICE)
    assert resolve_migration_obligation(_GOAL, str(tmp_path), _TARGET_FILES) is None


def test_resolve_migration_obligation_ambiguous_when_no_dormant_dependency(tmp_path):
    """Both declared dependencies are already in use somewhere - no single
    dormant "already approved" candidate, so this must not guess."""
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
    assert resolve_migration_obligation(_GOAL, str(root), _TARGET_FILES) is None


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
