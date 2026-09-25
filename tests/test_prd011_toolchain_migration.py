"""PRD-011 final review: toolchain conflict semantics.

- repository declaration = baseline toolchain;
- an authorized version change = target toolchain, and candidate gates for
  an authorized migration run under the target;
- a run that needs another version with no authority to change the
  declaration fails closed (TOOLCHAIN_REQUIREMENT_CONFLICT) before any
  candidate command runs.

Authority is the run's structured write scope / approved plan
(kriya/workflow/toolchain.py::toolchain_declaration_mutable), never goal
wording."""
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from kriya.config import AppConfig
from kriya.config.config import AutonomyConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.tools.process import ProcessResult
from kriya.tools.toolchain_identity import (
    TOOLCHAIN_REQUIREMENT_CONFLICT,
    ToolchainRequirementConflictError,
    resolve_toolchain_selection,
)
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.resume_fingerprints import toolchain_fingerprint
from kriya.workflow.toolchain import toolchain_declaration_mutable
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow import WorkflowEngine


def _pom(version):
    return (
        "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId>"
        f"<version>1</version><properties><maven.compiler.release>{version}</maven.compiler.release>"
        "</properties></project>"
    )


def _pyproject(spec):
    return f'[project]\nname = "p"\nrequires-python = "{spec}"\n'


def _contained():
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci")


def _jdk_home(tmp_path, version):
    home = tmp_path / f"jdk{version}"
    home.mkdir(exist_ok=True)
    (home / "release").write_text(f'JAVA_VERSION="{version}.0.1"\n')
    return str(home)


def _workspaces(tmp_path, baseline_files, candidate_files):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for root, files in ((baseline, baseline_files), (candidate, candidate_files)):
        root.mkdir()
        for name, text in files.items():
            (root / name).write_text(text)
    return str(baseline), str(candidate)


# --- authority comes from structured write scope / plan only ---

def test_declaration_authority_is_the_structured_write_scope():
    assert toolchain_declaration_mutable(None, None) is True  # unrestricted direct run
    assert toolchain_declaration_mutable(None, ["src/App.java"]) is False
    assert toolchain_declaration_mutable(None, ["pom.xml"]) is True
    assert toolchain_declaration_mutable("deny_all", ["pom.xml"]) is False
    assert toolchain_declaration_mutable(None, [".python-version"]) is True
    assert toolchain_declaration_mutable(None, ["module/pom.xml"]) is False  # not the root declaration
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[
        Subtask(id="s1", description="bump", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.MODIFY)]),
    ])
    assert toolchain_declaration_mutable("allowlist", ["src/App.java"], plan) is True  # a later owner


# --- 1. ordinary task: the repository's version ---

def test_ordinary_java_task_uses_the_repository_version(tmp_path):
    baseline, candidate = _workspaces(tmp_path, {"pom.xml": _pom(17)}, {"pom.xml": _pom(17), "App.java": "class A{}"})
    for mutable in (False, True):
        identity = resolve_toolchain_selection(candidate, "java", baseline_path=baseline, declaration_mutable=mutable)
        assert identity.runtime_version == "17"
        assert identity.selection["basis"] == "repository_declaration"
        assert identity.selection["baseline"]["runtime_version"] == "17"


# --- 2. authorized Java 17 -> 21 migration ---

def test_authorized_java_migration_verifies_the_candidate_under_the_target(tmp_path):
    baseline, candidate = _workspaces(tmp_path, {"pom.xml": _pom(17)}, {"pom.xml": _pom(21)})
    identity = resolve_toolchain_selection(candidate, "java", baseline_path=baseline, declaration_mutable=True)
    assert (identity.runtime_version, identity.containment_image) == ("21", "maven:3.9-eclipse-temurin-21")
    assert identity.selection["basis"] == "authorized_declaration_change"
    assert identity.selection["baseline"]["runtime_version"] == "17"
    assert identity.selection["target"]["runtime_version"] == "21"


def test_authorized_goal_target_applies_before_the_declaration_edit_lands(tmp_path):
    baseline, candidate = _workspaces(tmp_path, {"pom.xml": _pom(17)}, {"pom.xml": _pom(17)})
    identity = resolve_toolchain_selection(
        candidate, "java", baseline_path=baseline, java_home_override=_jdk_home(tmp_path, 21),
        declaration_mutable=True,
    )
    assert identity.runtime_version == "21"
    assert identity.selection["basis"] == "authorized_goal_requirement"
    assert identity.selection["baseline"]["runtime_version"] == "17"


@pytest.mark.asyncio
async def test_authorized_migration_run_executes_its_gates_in_the_target_image(tmp_path, monkeypatch):
    """End to end: a direct (unrestricted-scope) run on a Java 17 repository
    whose candidate moves the declaration to 21 - no containment-setup
    failure, and every contained gate command runs in the Java 21 image."""
    (tmp_path / "pom.xml").write_text(_pom(17))
    profiles = []

    def run(*_args, containment_profile=None, **_kwargs):
        profiles.append(containment_profile)
        return ProcessResult(returncode=0, stdout="", stderr="", timeout=False)

    monkeypatch.setattr("kriya.tools.validate.ProcessController.run", run)
    monkeypatch.setattr("kriya.workflow.attempt._check_java_toolchain_mismatch", lambda stack: None)
    cfg = _run_cfg()
    engine = _engine(cfg, [{"filepath": "pom.xml", "content": _pom(21)},
                           {"filepath": "src/main/java/App.java", "content": "public class App {}"}])
    res = await engine.run_generation_workflow(goal="Move the build to the current LTS", workspace_path=str(tmp_path))

    assert _failure_category(cfg) != "containment_setup_failed"
    contained = [p for p in profiles if p is not None and p.toolchain_identity is not None]
    assert contained, res
    assert {p.toolchain_identity.runtime_version for p in contained} == {"21"}
    # Every gate that compares against the baseline records the authorized 17 -> 21 change.
    gated = [p.toolchain_identity.selection for p in contained if p.toolchain_identity.selection["baseline"]]
    assert gated and all(
        (s["basis"], s["baseline"]["runtime_version"], s["target"]["runtime_version"])
        == ("authorized_declaration_change", "17", "21") for s in gated
    )


# --- 3. unauthorized conflict fails before candidate execution ---

def test_goal_requirement_without_declaration_authority_is_a_typed_conflict(tmp_path):
    baseline, candidate = _workspaces(tmp_path, {"pom.xml": _pom(17)}, {"pom.xml": _pom(17)})
    with pytest.raises(ToolchainRequirementConflictError) as conflict:
        resolve_toolchain_selection(
            candidate, "java", baseline_path=baseline, java_home_override=_jdk_home(tmp_path, 21),
            declaration_mutable=False,
        )
    assert conflict.value.reason_code == TOOLCHAIN_REQUIREMENT_CONFLICT
    assert str(conflict.value).startswith(TOOLCHAIN_REQUIREMENT_CONFLICT)


def test_declaration_change_without_authority_is_a_typed_conflict(tmp_path):
    baseline, candidate = _workspaces(tmp_path, {"pom.xml": _pom(17)}, {"pom.xml": _pom(21)})
    with pytest.raises(ToolchainRequirementConflictError):
        PolymorphicValidator(candidate, original_workspace_path=baseline, autonomy_cfg=_contained())


@pytest.mark.asyncio
async def test_scoped_run_needing_another_jdk_stops_before_any_candidate_command(tmp_path, monkeypatch):
    """A run scoped to one source file (no authority over pom.xml) whose goal
    requires Java 21 on a Java 17 repository: typed stop, nothing executed."""
    (tmp_path / "pom.xml").write_text(_pom(17))
    monkeypatch.setattr("kriya.workflow.attempt._resolve_java_home_override", lambda goal: _jdk_home(tmp_path, 21))
    monkeypatch.setattr("kriya.workflow.attempt._check_java_toolchain_mismatch", lambda stack: None)
    ran = []
    monkeypatch.setattr(
        "kriya.tools.validate.ProcessController.run",
        lambda *a, **k: ran.append(a) or ProcessResult(returncode=0, stdout="", stderr="", timeout=False),
    )
    cfg = _run_cfg()
    engine = _engine(cfg, [{"filepath": "src/main/java/App.java", "content": "public class App {}"}])
    res = await engine.run_generation_workflow(
        goal="Add App", workspace_path=str(tmp_path), allowed_write_relpaths=["src/main/java/App.java"],
    )

    assert res["quality_gates_passed"] is False
    assert _failure_category(cfg) == "containment_setup_failed"
    assert res["environment_failure"].startswith(f"CONTAINMENT_SETUP_FAILED: {TOOLCHAIN_REQUIREMENT_CONFLICT}:")
    assert ran == []


# --- 4. Python equivalents ---

def test_python_authorized_migration_and_unauthorized_conflict(tmp_path):
    baseline, candidate = _workspaces(
        tmp_path, {"pyproject.toml": _pyproject("==3.11.*")}, {"pyproject.toml": _pyproject("==3.12.*")},
    )
    identity = resolve_toolchain_selection(candidate, "python", baseline_path=baseline, declaration_mutable=True)
    assert (identity.runtime_version, identity.selection["basis"]) == ("3.12", "authorized_declaration_change")
    assert identity.selection["baseline"]["runtime_version"] == "3.11"
    with pytest.raises(ToolchainRequirementConflictError):
        resolve_toolchain_selection(candidate, "python", baseline_path=baseline, declaration_mutable=False)
    ordinary = resolve_toolchain_selection(baseline, "python", baseline_path=baseline)
    assert (ordinary.runtime_version, ordinary.selection["basis"]) == ("3.11", "repository_declaration")


def test_python_spec_edits_that_keep_the_toolchain_are_not_a_migration(tmp_path):
    baseline, candidate = _workspaces(
        tmp_path, {"pyproject.toml": _pyproject(">=3.9")}, {"pyproject.toml": _pyproject(">=3.10,<4")},
    )
    identity = resolve_toolchain_selection(candidate, "python", baseline_path=baseline, declaration_mutable=False)
    assert identity.runtime_version == "3.12"


# --- 5. resume reuses migration gates only while the target identity matches ---

def test_migration_resume_fingerprint_follows_the_target_toolchain_image(tmp_path, monkeypatch):
    from kriya.tools import containment_oci

    (tmp_path / "pom.xml").write_text(_pom(17))
    digests = {"maven:3.9-eclipse-temurin-17": "sha256:" + "1" * 64, "maven:3.9-eclipse-temurin-21": "sha256:" + "2" * 64}
    monkeypatch.setattr(containment_oci, "local_image_content_digest", lambda image: digests.get(image))
    migrated = {"pom.xml": _pom(21), "src/App.java": "class App {}"}

    def fingerprint(files):
        return toolchain_fingerprint(str(tmp_path), _contained(), candidate_files=files, declaration_mutable=True)

    at_checkpoint = fingerprint(migrated)
    assert at_checkpoint.available
    assert fingerprint(migrated) == at_checkpoint  # same target image: gates reusable
    assert fingerprint(None).value != at_checkpoint.value  # a non-migrated candidate is a different identity

    digests["maven:3.9-eclipse-temurin-17"] = "sha256:" + "3" * 64  # the baseline image is irrelevant
    assert fingerprint(migrated) == at_checkpoint
    digests["maven:3.9-eclipse-temurin-21"] = "sha256:" + "4" * 64  # the target image changed
    assert fingerprint(migrated).value != at_checkpoint.value

    unauthorized = toolchain_fingerprint(str(tmp_path), _contained(), candidate_files=migrated)
    assert not unauthorized.available  # a conflict never matches


# --- helpers ---

def _run_cfg():
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    return cfg


def _engine(cfg, developer_files):
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write " + " and ".join(item["filepath"] for item in developer_files),
        json.dumps(developer_files),
    ] + ["Review: Approved"] * 6)
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.reviewer.run = AsyncMock(return_value="Review: Approved")
    return engine


def _row(cfg, column):
    conn = sqlite3.connect(trace_db_path(cfg))
    try:
        return conn.execute(f"SELECT {column} FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()


def _failure_category(cfg):
    return _row(cfg, "failure_category")

