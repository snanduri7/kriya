"""D5 (KNOW A on demo-runtime-3, 2026-10-01, run 20261001T170540-70b9ab4d):
a runtime failure that names the candidate resource it failed on is grounded
to that resource, not to the stack frame that loads it.

Live: Spring failed on `class path resource [ignite-config.xml]` (an invented
`gridStartTime` property, written by s2). The only candidate stack frame was
IgniteDemoApplication.java:16, the `new ClassPathXmlApplicationContext(...)`
call, so recovery reopened s3 for a correct file, got identical bytes and
stopped (RECOVERY_NO_PROGRESS). The reopened owner was also shown only the
first 2000 characters of output, all Maven offline warnings.

Fixtures are real output: the runtime-3 s4 failure, and Spring 5.3.39 under
`mvn -o -e exec:java` (evidence/demo-defect-d5; the `file [...]` fixture's
absolute path is rewritten to a neutral outside-workspace path)."""
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_workflow import _runtime_verifier_ctx
from test_workflow_controller_enforce import _patched, _workflow_engine

from kriya.config.config import AutonomyConfig
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import run_attempt
from kriya.workflow.attribution import attribute_failure
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.failure_grounding import (
    _build_quality_gate_failure,
    extract_error_source_locations,
    extract_runtime_resource_references,
    ground_runtime_resource_failure,
    grounded_evidence_excerpt,
)
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

FIXTURES = Path(__file__).parent / "fixtures" / "d5_runtime_resource"
LIVE_CONFIG = "src/main/resources/ignite-config.xml"
LIVE_APP = "src/main/java/com/example/IgniteDemoApplication.java"
LIVE_FILES = ["pom.xml", LIVE_CONFIG, LIVE_APP]
APP, WIDGET = "src/main/java/demo/App.java", "src/main/java/demo/Widget.java"
CONFIG = "src/main/resources/config.xml"
PROBE_FILES = ["pom.xml", APP, WIDGET, "src/main/resources/bad-config.xml", "src/main/resources/importer.xml",
               "src/main/resources/imported-bad.xml", "src/main/resources/widget-config.xml"]


def _fixture(name):
    return (FIXTURES / name).read_text()


def _trace(reference, loader="App.java:7", root_frames="\tat org.springframework.beans.BeanWrapperImpl.x(BeanWrapperImpl.java:243)\n\t... 4 more"):
    """The JVM's own shape (app-printed, common frames elided), as in the live run."""
    return (f"Exception in thread \"main\" org.springframework.beans.factory.BeanCreationException: Error creating bean "
            f"with name 'cfg' defined in {reference}: Error setting property values\n"
            f"\tat org.springframework.beans.factory.support.AbstractBeanFactory.getBean(AbstractBeanFactory.java:208)\n"
            f"\tat demo.App.main({loader})\n"
            f"Caused by: org.springframework.beans.NotWritablePropertyException: Invalid property 'noSuch'\n"
            f"{root_frames}\n")


async def _attribute(output, known, failure_type="run_verification", workspace_root=None):
    failure = _build_quality_gate_failure(failure_type, "RUNTIME VERIFICATION FAILURE", output,
                                          "/nonexistent", known, 1)
    return await attribute_failure(failure, known, 0, [], None, lambda _p: None, workspace_root=workspace_root)


# ---------------------------------------------------------------- the live failure and real Spring output

@pytest.mark.asyncio
async def test_the_live_failure_grounds_the_resource_not_its_loader():
    result = await _attribute(_fixture("live_runtime3_s4.txt"), LIVE_FILES)
    assert (result.tier, result.files) == ("locator", [LIVE_CONFIG])
    assert "ignite-config.xml" in result.reasoning and LIVE_APP in result.reasoning  # loader kept as context only


def test_the_live_reference_is_extracted_as_direct_runtime_evidence():
    references = extract_runtime_resource_references(_fixture("live_runtime3_s4.txt"))
    assert {(r.raw_reference, r.normalized_reference, r.kind, r.source, r.confidence) for r in references} == {
        ("ignite-config.xml", "ignite-config.xml", "classpath", "runtime_exception", "DIRECT")}


@pytest.mark.asyncio
@pytest.mark.parametrize(("fixture", "expected"), [
    ("spring_invalid_property.txt", ["src/main/resources/bad-config.xml"]),  # mvn -e: loader in every cause
    ("spring_import_invalid.txt", ["src/main/resources/imported-bad.xml"]),  # the deepest naming exception decides
])
async def test_real_spring_output_grounds_the_named_resource(fixture, expected):
    assert (await _attribute(_fixture(fixture), PROBE_FILES)).files == expected


@pytest.mark.asyncio
async def test_a_candidate_frame_that_throws_keeps_the_stack_evidence():
    """The XML is named, but the innermost exception was thrown in candidate
    code (a bean constructor): the existing stack locator stands, unchanged."""
    result = await _attribute(_fixture("spring_bean_constructor_throws.txt"), PROBE_FILES)
    assert (result.tier, result.files) == ("locator", [APP, WIDGET])
    assert ground_runtime_resource_failure(_fixture("spring_bean_constructor_throws.txt"), PROBE_FILES) is None


# ---------------------------------------------------------------- resolution rules

@pytest.mark.asyncio
async def test_a_resource_outranks_the_loader_frame():
    result = await _attribute(_trace("class path resource [config.xml]"), [APP, CONFIG])
    assert result.files == [CONFIG]


@pytest.mark.asyncio
@pytest.mark.parametrize(("reference", "known", "expected"), [
    ("class path resource [config.xml]", [APP, CONFIG], [CONFIG]),  # unique basename
    ("class path resource [a/config.xml]", [APP, "src/main/resources/a/config.xml", "src/test/resources/b/config.xml"],
     ["src/main/resources/a/config.xml"]),  # resource-root relative
    ("class path resource [/a/config.xml]", [APP, "src/main/resources/a/config.xml"],
     ["src/main/resources/a/config.xml"]),
    ("file [conf/app.xml]", [APP, "conf/app.xml"], ["conf/app.xml"]),  # exact workspace-relative
    ("URL location [classpath:config.xml]", [APP, CONFIG], [CONFIG]),
    ("class path resource [module/src/main/resources/x.xml]", [APP, "module/src/main/resources/x.xml"],
     ["module/src/main/resources/x.xml"]),
])
async def test_resolution_order(reference, known, expected):
    assert (await _attribute(_trace(reference), known)).files == expected


@pytest.mark.asyncio
async def test_a_duplicate_basename_is_never_guessed():
    known = [APP, "src/main/resources/a/config.xml", "src/test/resources/b/config.xml"]
    grounding = ground_runtime_resource_failure(_trace("class path resource [config.xml]"), known)
    assert grounding.files == () and grounding.ambiguous == (("config.xml", tuple(known[1:])),)
    assert (await _attribute(_trace("class path resource [config.xml]"), known)).files == [APP]  # old behaviour


@pytest.mark.asyncio
async def test_a_partial_path_never_falls_back_to_a_basename_match():
    known = [APP, "src/main/resources/b/config.xml"]
    assert (await _attribute(_trace("class path resource [a/config.xml]"), known)).files == [APP]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [
    "class path resource [org/springframework/beans/factory/xml/spring-beans.xsd]",  # library resource
    "class path resource [META-INF/spring.handlers]",
    "class path resource [config.xml.bak]",  # no fuzzy or substring matching
    "class path resource [onfig.xml]",
])
async def test_a_non_candidate_resource_is_never_a_repair_target(reference):
    assert (await _attribute(_trace(reference), [APP, CONFIG])).files == [APP]


@pytest.mark.asyncio
async def test_a_resource_outside_the_workspace_is_never_authorized(tmp_path):
    outside = _fixture("spring_file_resource_outside.txt")
    known = [APP, "conf/fs-config.xml"]
    assert (await _attribute(outside, known, workspace_root=str(tmp_path))).files == [APP]
    assert (await _attribute(outside, known, workspace_root=None)).files == [APP]
    assert (await _attribute(_trace("file [../conf/fs-config.xml]"), known, workspace_root=str(tmp_path))).files == [APP]
    for root in (str(tmp_path), None):  # an absolute path is never read as workspace-relative
        assert (await _attribute(_trace("file [/conf/fs-config.xml]"), known, workspace_root=root)).files == [APP]
    (tmp_path / "conf").mkdir()
    (tmp_path / "conf/fs-config.xml").write_text("<beans/>")
    inside = _trace(f"file [{tmp_path / 'conf/fs-config.xml'}]")
    assert (await _attribute(inside, known, workspace_root=str(tmp_path))).files == ["conf/fs-config.xml"]


# ---------------------------------------------------------------- existing grounding unchanged

@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", ["compile", "test", "regression_test"])
async def test_only_a_runtime_failure_uses_resource_evidence(failure_type):
    assert (await _attribute(_trace("class path resource [config.xml]"), [APP, CONFIG], failure_type)).files == [APP]


@pytest.mark.asyncio
async def test_runtime_output_without_a_resource_reference_is_unchanged():
    output = "Exception in thread \"main\" java.lang.IllegalStateException: boom\n\tat demo.App.main(App.java:7)\n"
    assert (await _attribute(output, [APP, CONFIG])).files == [APP]


def test_the_existing_locator_shapes_are_unchanged():
    text = ("src/main/java/demo/App.java:[3,5] error: cannot find symbol\n\tat demo.Widget.<init>(Widget.java:9)\n"
            "Syntax error in tool.py line 4: invalid syntax")
    assert extract_error_source_locations(text) == [("App.java", 3), ("Widget.java", 9), ("tool.py", 4)]


# ---------------------------------------------------------------- the evidence a reopened owner is shown

def test_the_reopened_owner_sees_the_live_exception():
    excerpt = grounded_evidence_excerpt(_fixture("live_runtime3_s4.txt"), [LIVE_CONFIG])
    assert len(excerpt) == 2000 and "gridStartTime" in excerpt and "NotWritablePropertyException" in excerpt
    assert "gridStartTime" not in _fixture("live_runtime3_s4.txt")[:2000]  # what it was shown before D5


def test_the_evidence_head_is_kept_when_it_already_names_the_file():
    output = "Picked up JAVA_TOOL_OPTIONS\nerror in config.xml line 3\n" + "x" * 5000 + "\nconfig.xml again"
    assert grounded_evidence_excerpt(output, [CONFIG]) == output[:2000]
    assert grounded_evidence_excerpt("short config.xml", [CONFIG]) == "short config.xml"
    assert grounded_evidence_excerpt("y" * 3000, [CONFIG]) == "y" * 2000  # not named anywhere: unchanged
    assert grounded_evidence_excerpt("y" * 3000 + "\nmyconfig.xml", [CONFIG]) == "y" * 2000  # token boundary


# ---------------------------------------------------------------- attempt + retry: the verification-only unit

def _live_run_result():
    return {"success": False, "timed_out": False, "returncode": 0, "runtime_prerequisite": "PASSED",
            "output": _fixture("live_runtime3_s4.txt"),
            "steps": [{"command": ["mvn"], "exit_code": 0, "timed_out": False}]}


@pytest.mark.asyncio
async def test_a_verification_only_unit_grounds_the_resource_owner_with_its_scope_empty(tmp_path):
    """The live shape end to end through run_attempt and handle_attempt_failure:
    the scope conflict names exactly the resource, the evidence carries the
    exception, the unit's own scope stays empty, no model attribution."""
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [["mvn", "-e", "exec:java", "-Dexec.mainClass=com.example.IgniteDemoApplication"]],
        "command_source": "inferred", "success_criteria": "prints the cached value"})
    run_verifier.grade = AsyncMock(return_value={
        "passed": False, "reasoning": 'the program printed "[VERIFICATION] FAIL"', "likely_files": []})
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier, established_files=LIVE_FILES)
    state = GenerationState()
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", return_value=_live_run_result()):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    assert raised.value.failure.type == "run_verification"
    with patch("kriya.workflow.attribution._tier_triage", new=AsyncMock(side_effect=AssertionError("no model"))):
        should_break = await handle_attempt_failure(state, ctx, raised.value)
    assert should_break is True
    conflict = state.plan_scope_conflict
    assert conflict["reason_code"] == "PLAN_SCOPE_REVISION_REQUIRED"
    assert (conflict["required_files"], conflict["allowed_files"], conflict["attribution_tier"]) == (
        [LIVE_CONFIG], [], "locator")
    assert "gridStartTime" in conflict["raw_evidence"]
    assert ctx.write_scope_mode is WriteScopeMode.DENY_ALL and list(ctx.allowed_write_relpaths) == []
    assert not developer.run_generation.called


# ---------------------------------------------------------------- controller: the resource owner, exactly its file

def _plan():
    def unit(sid, path, provides, **extra):
        return Subtask(id=sid, description=f"create {path}", execution_method=ExecutionMethod.MODEL,
                       planned_files=[PlannedFile(path=path, action=FileAction.CREATE)], provides=[provides], **extra)
    return EngineeringPlan(plan_id="d5", kind=ChangeKind.TASK, subtasks=[
        unit("s1", "pom.xml", "build.ready"),
        unit("s2", CONFIG, "config.ready", depends_on=["s1"], requires=["build.ready"]),
        unit("s3", APP, "app.ready", depends_on=["s2"], requires=["config.ready"]),
        Subtask(id="s4", description="run the application", execution_method=ExecutionMethod.MODEL,
                execution_role=ExecutionRole.VERIFICATION, planned_files=[], depends_on=["s3"], requires=["app.ready"],
                verification=[VerificationMethod(type=VerificationMethodType.JUDGMENT,
                                                 verifier_kind=VerifierKind.APPLICATION_RUNTIME,
                                                 requires_runtime_execution=True,
                                                 description="observe the application's output")]),
    ])


_D5_CONFLICT = {"classification": "PLAN_SCOPE_DEFECT", "reason_code": "PLAN_SCOPE_REVISION_REQUIRED",
                "failure_type": "run_verification", "required_files": [CONFIG], "allowed_files": [],
                "attribution_tier": "locator", "grounded_owner_files": [],
                "raw_evidence": "defined in class path resource [config.xml]: Invalid property 'noSuch'"}
_VERIFIED = [{"type": "judgment", "tool_name": None, "passed": True,
              "description": "observe the application's output", "source": "run_verification"}]


async def _run_controller(tmp_path, repaired_config):
    we = _workflow_engine()
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        n, ws = len(calls), Path(kwargs["workspace_path"])
        if n <= 3:
            path = ["pom.xml", CONFIG, APP][n - 1]
            (ws / path).parent.mkdir(parents=True, exist_ok=True)
            (ws / path).write_text({1: "<project/>", 2: "<beans><bad/></beans>", 3: "class App {}"}[n])
            return {"status": "success", "quality_gates_passed": True, "files": [path]}
        if n == 4:
            assert kwargs["write_scope_mode"] == WriteScopeMode.DENY_ALL
            return {"status": "failed", "quality_gates_passed": False, "files": [], "plan_scope_conflict": _D5_CONFLICT}
        if n == 5:
            (ws / CONFIG).write_text(repaired_config)
            return {"status": "success", "quality_gates_passed": True, "files": [CONFIG]}
        return {"status": "success", "quality_gates_passed": True, "files": [], "verification_results": _VERIFIED}

    we.run_generation_workflow = fake_run
    p1, p2, p3 = _patched(_plan())
    with p1, p2, p3:
        result = await WorkflowController(we).execute("goal", str(tmp_path), migration_mode="enforce")
    return result, calls


@pytest.mark.asyncio
async def test_enforce_reopens_the_resource_owner_for_exactly_that_file(tmp_path):
    result, calls = await _run_controller(tmp_path, "<beans/>")
    assert result.legacy_result["status"] == "success", result.legacy_result
    assert all(item.status == SubtaskStatus.COMPLETED for item in result.subtask_results)
    assert [c["allowed_write_relpaths"] for c in calls] == [["pom.xml"], [CONFIG], [APP], [], [CONFIG], []]
    event = result.legacy_result["plan_recovery_events"][0]
    assert (event["failed_subtask"], event["reopened_owner"], event["required_repair_files"]) == ("s4", "s2", [CONFIG])


@pytest.mark.asyncio
async def test_identical_bytes_from_the_right_owner_are_still_no_progress(tmp_path):
    result, calls = await _run_controller(tmp_path, "<beans><bad/></beans>")
    assert len(calls) == 5  # the verifier is never re-run off a no-progress "repair"
    assert result.legacy_result["status"] != "success"
    event = result.legacy_result["plan_recovery_events"][0]
    assert (event["reopened_owner"], event["owner_recovery_passed"]) == ("s2", False)
    owner = next(item for item in result.subtask_results if item.subtask_id == "s2")
    assert owner.status == SubtaskStatus.NEEDS_REVIEW and "PLAN_RECOVERY_OWNER_FAILED" in owner.reason_codes


# ---------------------------------------------------------------- real Spring + Maven, through the validator

_SPRING = Path.home() / ".m2/repository/org/springframework/spring-context/5.3.39/spring-context-5.3.39.jar"
needs_spring = pytest.mark.skipif(shutil.which("mvn") is None or not _SPRING.exists(),
                                  reason="real Maven with Spring 5.3.39 in the local repository is not available")


@needs_spring
@pytest.mark.asyncio
async def test_a_real_spring_failure_through_the_validator_grounds_the_xml(tmp_path):
    (tmp_path / "src/main/java/demo").mkdir(parents=True)
    (tmp_path / "src/main/resources").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>demo</groupId>'
        '<artifactId>d5</artifactId><version>1</version><properties><maven.compiler.release>17'
        '</maven.compiler.release></properties><dependencies><dependency><groupId>org.springframework</groupId>'
        '<artifactId>spring-context</artifactId><version>5.3.39</version></dependency></dependencies><build><plugins>'
        '<plugin><groupId>org.codehaus.mojo</groupId><artifactId>exec-maven-plugin</artifactId><version>3.1.0'
        '</version></plugin></plugins></build></project>')
    (tmp_path / APP).write_text(
        "package demo;\npublic class App {\n  public static void main(String[] a) {\n"
        '    new org.springframework.context.support.ClassPathXmlApplicationContext("bad-config.xml").close();\n'
        "  }\n}\n")
    (tmp_path / "src/main/resources/bad-config.xml").write_text(
        '<beans xmlns="http://www.springframework.org/schema/beans" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.springframework.org/'
        'schema/beans http://www.springframework.org/schema/beans/spring-beans.xsd"><bean class="java.util.ArrayList">'
        '<property name="notARealProperty" value="1"/></bean></beans>')
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    result = validator.run_app_sequence([["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.App"]])
    assert result["success"] is False and "notARealProperty" in result["output"]
    known = ["pom.xml", APP, "src/main/resources/bad-config.xml"]
    assert (await _attribute(result["output"], known, workspace_root=str(tmp_path))).files == [
        "src/main/resources/bad-config.xml"]
