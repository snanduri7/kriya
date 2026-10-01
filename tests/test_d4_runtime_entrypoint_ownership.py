"""D4 (KNOW-A on demo-runtime-2, 2026-10-01, run 20261001T162418-01a90bf2):
runtime entrypoint failures are owned by whoever chose the entrypoint.

Live: the candidate pom.xml configured exec-maven-plugin <mainClass> with a
class the candidate never wrote; the run command's -Dexec.mainClass named the
real one; Maven ran the POM's (configuration beats the user property -
measured model-free, evidence/demo-defect-d4); Kriya reported
VERIFICATION_INFRASTRUCTURE_FAILURE, so nothing could repair the POM. Now the
validator diagnoses the effective entrypoint deterministically (POM as XML,
current source, fresh build) and:
  A candidate config selects a nonexistent class -> candidate defect, pom.xml
    implicated, repaired through the existing grounded owner recovery
  B Kriya's command selects one -> verifier infrastructure, no Developer
  C declared + built, still not loaded -> infrastructure
  D declared, not built -> build diagnosis (infrastructure)"""
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_workflow import _minimal_attempt_ctx, _runtime_verifier_ctx
from test_workflow_controller_enforce import _patched, _workflow_engine

from kriya.config.config import AutonomyConfig
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import (
    CANDIDATE_RUNTIME_ENTRYPOINT_INVALID,
    RUNTIME_COMMAND_ENTRYPOINT_INVALID,
    RUNTIME_ENTRYPOINT_NOT_BUILT,
    RUNTIME_ENTRYPOINT_NOT_LOADABLE,
    classify_runtime_entrypoint,
)
from kriya.workflow.attempt import run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.plan_schema import (
    EngineeringPlan,
    ExecutionMethod,
    ExecutionRole,
    FileAction,
    PlannedFile,
    Subtask,
    VerificationMethod,
    VerificationMethodType,
    VerifierKind,
)
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState
from kriya.workflow.triage import ChangeKind
from kriya.workflow.workflow_controller import WorkflowController
from kriya.workflow.workflow_types import SubtaskStatus

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d4</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release>{properties}</properties>{parent}
<build><plugins><plugin><groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId>
<version>3.1.0</version>{config}</plugin></plugins></build>
<dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version>
<scope>test</scope></dependency></dependencies></project>
"""
SOURCE = "src/main/java/demo/ExistingB.java"
B = 'package demo;\npublic class ExistingB { public static void main(String[] a) { System.out.println("B_RAN"); } }\n'
NOT_FOUND = "java.lang.ClassNotFoundException: {}\n[INFO] BUILD FAILURE"
needs_maven = pytest.mark.skipif(shutil.which("mvn") is None, reason="real Maven is not installed")


def _project(path, config="", properties="", parent=""):
    (path / "src/main/java/demo").mkdir(parents=True, exist_ok=True)
    (path / "pom.xml").write_text(POM.format(config=config, properties=properties, parent=parent))
    (path / SOURCE).write_text(B)
    return PolymorphicValidator(str(path), autonomy_cfg=AutonomyConfig(contained_execution_required=False))


def _diagnosis(validator, cli_class="demo.ExistingB"):
    return validator.diagnose_runtime_entrypoint(["mvn", "-e", "exec:java", f"-Dexec.mainClass={cli_class}"])


# ---------------------------------------------------------------- provenance (static, no Maven run)

@pytest.mark.parametrize(("config", "properties", "parent", "effective", "provenance", "key"), [
    ("<configuration><mainClass>demo.NonexistentA</mainClass></configuration>", "", "",
     "demo.NonexistentA", "CANDIDATE_BUILD_CONFIG", "build/plugins/plugin[exec-maven-plugin]/configuration/mainClass"),
    ("<executions><execution><id>default-cli</id><configuration><mainClass>demo.Cli</mainClass></configuration>"
     "</execution></executions><configuration><mainClass>demo.Plugin</mainClass></configuration>", "", "",
     "demo.Cli", "CANDIDATE_BUILD_CONFIG",
     "build/plugins/plugin[exec-maven-plugin]/executions/execution[default-cli]/configuration/mainClass"),
    ("<configuration><mainClass>${start.class}</mainClass></configuration>", "<start.class>demo.Prop</start.class>", "",
     "demo.Prop", "CANDIDATE_BUILD_CONFIG", "build/plugins/plugin[exec-maven-plugin]/configuration/mainClass"),
    ("<configuration><mainClass>${exec.mainClass}</mainClass></configuration>", "", "",
     "demo.ExistingB", "KRIYA_COMMAND", "build/plugins/plugin[exec-maven-plugin]/configuration/mainClass"),
    ("<configuration><mainClass>${undefined}</mainClass></configuration>", "", "", None, "UNKNOWN", None),
    ("", "", "", "demo.ExistingB", "KRIYA_COMMAND", None),
    ("", "", "<parent><groupId>p</groupId><artifactId>p</artifactId><version>1</version></parent>",
     None, "UNKNOWN", None),  # a parent may configure it: never guessed
])
def test_the_effective_entrypoint_and_who_chose_it(tmp_path, config, properties, parent, effective, provenance, key):
    diagnosis = _diagnosis(_project(tmp_path, config, properties, parent))
    assert diagnosis["requested_entrypoint"] == "demo.ExistingB"
    assert diagnosis["effective_entrypoint"] == effective
    assert diagnosis["effective_entrypoint_provenance"] == provenance
    assert diagnosis["candidate_config_key"] == (key if provenance == "CANDIDATE_BUILD_CONFIG" else None)


def test_a_java_command_is_kriyas_choice_and_a_jar_launch_is_not_diagnosed(tmp_path):
    validator = _project(tmp_path)
    diagnosis = validator.diagnose_runtime_entrypoint(
        ["java", "--add-opens", "java.base/java.nio=ALL-UNNAMED", "-cp", "target/classes", "demo.Wrong", "arg"])
    assert (diagnosis["effective_entrypoint"], diagnosis["effective_entrypoint_provenance"]) == ("demo.Wrong",
                                                                                                 "KRIYA_COMMAND")
    assert validator.diagnose_runtime_entrypoint(["java", "-jar", "target/app.jar"]) is None
    assert validator.diagnose_runtime_entrypoint(["python3", "app.py"]) is None


# ---------------------------------------------------------------- classification

def _diag(provenance, declared, built, effective="demo.X"):
    return {"effective_entrypoint": effective, "effective_entrypoint_provenance": provenance,
            "source_declares_entrypoint": declared, "compiled_artifact_exists": built,
            "candidate_config_source": "pom.xml", "candidate_config_key": "k", "requested_entrypoint": "demo.Y"}


@pytest.mark.parametrize(("provenance", "declared", "built", "expected"), [
    ("CANDIDATE_BUILD_CONFIG", False, False, CANDIDATE_RUNTIME_ENTRYPOINT_INVALID),  # A
    ("KRIYA_COMMAND", False, False, RUNTIME_COMMAND_ENTRYPOINT_INVALID),  # B
    ("CANDIDATE_BUILD_CONFIG", True, True, RUNTIME_ENTRYPOINT_NOT_LOADABLE),  # C: never blamed on the POM
    ("KRIYA_COMMAND", True, True, RUNTIME_ENTRYPOINT_NOT_LOADABLE),
    ("CANDIDATE_BUILD_CONFIG", True, False, RUNTIME_ENTRYPOINT_NOT_BUILT),  # D: never a guessed typo
    ("UNKNOWN", False, False, None),
])
def test_entrypoint_ownership_follows_provenance_and_the_fresh_build(provenance, declared, built, expected):
    assert classify_runtime_entrypoint(_diag(provenance, declared, built), NOT_FOUND.format("demo.X")) == expected


def test_the_output_must_confirm_the_diagnosed_class_and_be_a_launch_failure():
    diagnosis = _diag("CANDIDATE_BUILD_CONFIG", False, False)
    assert classify_runtime_entrypoint(diagnosis, NOT_FOUND.format("demo.Other")) is None
    assert classify_runtime_entrypoint(diagnosis, "Exception in thread main: IllegalStateException") is None
    assert classify_runtime_entrypoint(None, NOT_FOUND.format("demo.X")) is None


# ---------------------------------------------------------------- real Maven, fresh build (D3 runs first)

@needs_maven
def test_the_live_shape_is_a_candidate_defect_on_the_fresh_build(tmp_path):
    validator = _project(tmp_path, "<configuration><mainClass>demo.NonexistentA</mainClass></configuration>")
    (tmp_path / "target/classes/demo").mkdir(parents=True)  # stale output a reset could have left
    (tmp_path / "target/classes/demo/NonexistentA.class").write_bytes(b"stale")
    result = validator.run_app_sequence([["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"]])
    assert result["runtime_prerequisite"] == "mvn clean compile (current source): PASSED"  # D3: rebuilt, stale gone
    diagnosis = result["entrypoint_diagnosis"]
    assert diagnosis["effective_entrypoint"] == "demo.NonexistentA" and diagnosis["compiled_artifact_exists"] is False
    assert diagnosis["source_declares_entrypoint"] is False and diagnosis["candidate_config_source"] == "pom.xml"
    assert classify_runtime_entrypoint(diagnosis, result["output"]) == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID


@needs_maven
def test_a_valid_class_that_still_cannot_load_is_never_the_poms_fault(tmp_path):
    """Case C on a real build: the class is declared and compiled, the load
    fails anyway (here: a static initializer error surfaces as
    NoClassDefFoundError of a second class) - not a candidate POM defect."""
    validator = _project(tmp_path)
    assert validator.run_compile_check([SOURCE])["success"]
    diagnosis = validator.diagnose_runtime_entrypoint(["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"])
    assert diagnosis["source_declares_entrypoint"] is True and diagnosis["compiled_artifact_exists"] is True
    assert classify_runtime_entrypoint(diagnosis, NOT_FOUND.format("demo.ExistingB")) == RUNTIME_ENTRYPOINT_NOT_LOADABLE


# ---------------------------------------------------------------- attempt + retry: ownership routing

def _live_result(diagnosis):
    return {"success": False, "timed_out": False, "returncode": 1, "runtime_prerequisite": "PASSED",
            "output": NOT_FOUND.format(diagnosis["effective_entrypoint"]),
            "steps": [{"command": ["mvn"], "exit_code": 1, "timed_out": False}],
            "entrypoint_diagnosis": diagnosis}


def _verification_only_ctx(tmp_path, developer, run_verifier):
    return _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier,
                                 established_files=["pom.xml", SOURCE])


def _judge(command_class="demo.ExistingB"):
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [["mvn", "-e", "exec:java", f"-Dexec.mainClass={command_class}"]],
        "command_source": "inferred", "success_criteria": "prints B_RAN"})
    run_verifier.grade = AsyncMock(side_effect=AssertionError("a launch failure is never graded"))
    return run_verifier


@pytest.mark.asyncio
async def test_a_verification_only_unit_routes_the_candidate_defect_to_the_poms_owner(tmp_path):
    """The live shape: the unit owns no files. The failure is the candidate's,
    grounded to pom.xml; retry handling turns it into the existing grounded
    cross-owner scope conflict - its own write scope stays empty, no
    Developer call here, no model-based attribution."""
    _project(tmp_path, "<configuration><mainClass>demo.NonexistentA</mainClass></configuration>")
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    ctx = _verification_only_ctx(tmp_path, developer, _judge())
    state = GenerationState()
    diagnosis = _diagnosis(PolymorphicValidator(str(tmp_path)))
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", return_value=_live_result(diagnosis)):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type == "candidate_runtime_entrypoint_invalid"
    assert failure.likely_files == ["pom.xml"]
    assert failure.diagnostics["reason_code"] == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID
    assert failure.diagnostics["entrypoint_diagnosis"]["effective_entrypoint"] == "demo.NonexistentA"
    assert "VERIFICATION_INFRASTRUCTURE_FAILURE" not in failure.message
    with patch("kriya.workflow.retry_strategy.attribute_failure",
               new=AsyncMock(side_effect=AssertionError("deterministic evidence: no model attribution"))):
        should_break = await handle_attempt_failure(state, ctx, raised.value)
    assert should_break is True
    conflict = state.plan_scope_conflict
    assert conflict["reason_code"] == "PLAN_SCOPE_REVISION_REQUIRED"
    assert conflict["required_files"] == ["pom.xml"] and conflict["allowed_files"] == []
    assert conflict["attribution_tier"] == "authoritative_deterministic"
    assert ctx.write_scope_mode is WriteScopeMode.DENY_ALL and list(ctx.allowed_write_relpaths) == []
    assert not developer.run_generation.called


@pytest.mark.asyncio
async def test_a_unit_that_owns_the_pom_repairs_it_in_its_own_scope(tmp_path):
    _project(tmp_path, "<configuration><mainClass>demo.NonexistentA</mainClass></configuration>")
    ctx = _minimal_attempt_ctx(tmp_path, allowed_write_relpaths=["pom.xml", SOURCE], max_retries=4)
    state = GenerationState()
    state.attempt_number = 1
    from kriya.workflow.attempt import (
        _raise_runtime_verification_infrastructure_failure,  # pylint: disable=import-outside-toplevel
    )
    diagnosis = _diagnosis(PolymorphicValidator(str(tmp_path)))
    with pytest.raises(QualityGateFailure) as raised:
        _raise_runtime_verification_infrastructure_failure(state, _live_result(diagnosis), [["mvn", "exec:java"]])
    with patch("kriya.workflow.retry_strategy.attribute_failure", new=AsyncMock(side_effect=AssertionError)):
        await handle_attempt_failure(state, ctx, raised.value)
    assert state.plan_scope_conflict is None  # in scope: an ordinary repair of pom.xml, nothing widened
    assert raised.value.failure.likely_files == ["pom.xml"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("command_class", "declared_and_built", "code"), [
    ("demo.WrongMain", False, RUNTIME_COMMAND_ENTRYPOINT_INVALID),  # B: Kriya's own command
    ("demo.ExistingB", True, RUNTIME_ENTRYPOINT_NOT_LOADABLE),  # C
])
async def test_kriya_owned_entrypoint_failures_stay_infrastructure_with_no_developer(tmp_path, command_class,
                                                                                    declared_and_built, code):
    _project(tmp_path)
    if declared_and_built:
        (tmp_path / "target/classes/demo").mkdir(parents=True)
        (tmp_path / "target/classes/demo/ExistingB.class").write_bytes(b"built")
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("infrastructure: no Developer"))
    ctx = _verification_only_ctx(tmp_path, developer, _judge(command_class))
    diagnosis = _diagnosis(PolymorphicValidator(str(tmp_path)), command_class)
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", return_value=_live_result(diagnosis)):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(GenerationState(), ctx)
    assert raised.value.failure.type == "verification_infrastructure_failure"
    assert raised.value.failure.diagnostics["reason_code"] == code
    assert not developer.run_generation.called


# ---------------------------------------------------------------- controller: the owner repairs, scope stays bounded

@pytest.mark.asyncio
async def test_enforce_reopens_the_pom_owner_only_for_pom_and_reverifies(tmp_path):
    """s1 produced pom.xml, s2 the application, s3 verifies runtime and owns
    nothing. s3's D4 scope conflict reopens s1 with exactly [pom.xml]; s3 is
    re-run with its own empty scope - no unit's authority is widened."""
    plan = EngineeringPlan(plan_id="d4", kind=ChangeKind.TASK, subtasks=[
        Subtask(id="s1", description="create build manifest", execution_method=ExecutionMethod.MODEL,
                planned_files=[PlannedFile(path="pom.xml", action=FileAction.CREATE)], provides=["build.ready"]),
        Subtask(id="s2", description="create application", execution_method=ExecutionMethod.MODEL,
                depends_on=["s1"], requires=["build.ready"], provides=["app.ready"],
                planned_files=[PlannedFile(path=SOURCE, action=FileAction.CREATE)]),
        Subtask(id="s3", description="run the application", execution_method=ExecutionMethod.MODEL,
                execution_role=ExecutionRole.VERIFICATION, planned_files=[], depends_on=["s2"], requires=["app.ready"],
                verification=[VerificationMethod(type=VerificationMethodType.JUDGMENT,
                                                 verifier_kind=VerifierKind.APPLICATION_RUNTIME,
                                                 requires_runtime_execution=True,
                                                 description="observe the application's output")]),
    ])
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        ws = Path(kwargs["workspace_path"])  # where the controller runs this unit (its plan worktree)
        if n == 1:
            (ws / "pom.xml").write_text(POM.format(
                config="<configuration><mainClass>demo.NonexistentA</mainClass></configuration>",
                properties="", parent=""))
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if n == 2:
            (ws / "src/main/java/demo").mkdir(parents=True, exist_ok=True)
            (ws / SOURCE).write_text(B)
            return {"status": "success", "quality_gates_passed": True, "files": [SOURCE]}
        if n == 3:  # what retry_strategy produced for the D4 failure (test above)
            assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
            return {"status": "failed", "quality_gates_passed": False, "files": [],
                    "plan_scope_conflict": {
                        "classification": "PLAN_SCOPE_DEFECT", "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                        "failure_type": "candidate_runtime_entrypoint_invalid", "required_files": ["pom.xml"],
                        "allowed_files": [], "attribution_tier": "authoritative_deterministic",
                        "grounded_owner_files": []}}
        if n == 4:
            (ws / "pom.xml").write_text(POM.format(config="", properties="", parent=""))
            return {"status": "success", "quality_gates_passed": True, "files": ["pom.xml"]}
        if kwargs["write_scope_mode"] != WriteScopeMode.DENY_ALL:  # s2 re-run after the repair
            (ws / SOURCE).write_text(B)
            return {"status": "success", "quality_gates_passed": True, "files": [SOURCE]}
        return {"status": "success", "quality_gates_passed": True, "files": [],  # s3 re-verified, scope still empty
                "verification_results": [{"type": "judgment", "tool_name": None, "passed": True,
                                          "description": "observe the application's output",
                                          "source": "run_verification"}]}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(plan)
    with p1, p2, p3:
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")

    assert result.legacy_result["status"] == "success", result.legacy_result
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    assert [c["allowed_write_relpaths"] for c in calls] == [
        ["pom.xml"], [SOURCE], [],  # s1, s2, s3 (verification-only: owns nothing)
        ["pom.xml"],  # s1 reopened for exactly the implicated file
        [],  # s3 re-verified with its own, still empty scope
    ]
    event = result.legacy_result["plan_recovery_events"][0]
    assert (event["failed_subtask"], event["reopened_owner"], event["required_repair_files"]) == ("s3", "s1",
                                                                                                  ["pom.xml"])
