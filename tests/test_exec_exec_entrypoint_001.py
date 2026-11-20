"""EXEC-EXEC-ENTRYPOINT-DIAGNOSIS-001 and PLANNED-ENTRYPOINT-CONTEXT-001 (Stage C run 3, 20261119T163313-acea6505).

MEASURED in the sealed run: the plan assigned ``src/main/java/com/example/ProtocolApplication.java`` to unit s6. Unit s1, writing
``pom.xml`` first, was never told and wrote ``<exec.mainClass>com.example.QpidAmqpApp</exec.mainClass>``. The verification unit s8 ran
``mvn -e exec:exec``; the JVM answered ``ClassNotFoundException: com.example.QpidAmqpApp``. The validator diagnoses a launch only for
``exec:java`` and plain ``java``, so there was no diagnosis, the failure was classified verifier infrastructure, and the owner of
``pom.xml`` was never reopened. The sealed oracle never ran.

B (diagnosis, ``PolymorphicValidator.diagnose_runtime_entrypoint``): an ``exec:exec`` whose POM configuration PROVES a Java main class
is diagnosed like every other JVM launch; then the existing D4 classification and the existing cross-owner recovery apply (their own
proof, for the same failure type, is tests/test_d4_runtime_entrypoint_ownership.py - the enforce reopen of the pom's owner included).
A (prevention, ``planning_request.planned_entrypoint_for``): the plan's one entry-point file is a stated fact for the build-file owner
and for the entry-point's own unit.

Sealed bytes: tests/fixtures/stage_c_run3 (digest-checked). No Maven, no container: the diagnosis reads the POM, the sources and the
build output directory; the JVM's answer is the sealed output.
"""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from _strict_doubles import developer_double
from test_d4_runtime_entrypoint_ownership import NOT_FOUND, SOURCE, B, _project
from test_workflow import _runtime_verifier_ctx

from kriya.config.config import AutonomyConfig
from kriya.config.resolution import bind_project_identity
from kriya.policy.filesystem import WriteScopeMode
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import (
    CANDIDATE_RUNTIME_ENTRYPOINT_INVALID,
    RUNTIME_COMMAND_ENTRYPOINT_INVALID,
    RUNTIME_ENTRYPOINT_NOT_BUILT,
    RUNTIME_ENTRYPOINT_NOT_LOADABLE,
    classify_runtime_entrypoint,
    runtime_verification_infrastructure_reason,
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
from kriya.workflow.planning_request import (
    PLANNED_ENTRYPOINT_AUTHORITY,
    build_subtask_semantic_context,
    planned_entrypoint_for,
)
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState
from kriya.workflow.triage import ChangeKind

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run3")
SEALED = {
    "pom.s1.xml": "e0ca976fa98393b0ba94a8f157da64bf07f995e38b586b6e49949009a8a9f178",
    "ProtocolApplication.s6.java": "973f513d22bb68b87206cba6bef4775ac05948c68b6a5a3169fe5eba989954ca",
    "s8_launch_failure.output.txt": "4c21bc090827a85e330c11c1189aefe3598cae82001945a651ec78587acf0a39",
}
APP = "src/main/java/com/example/ProtocolApplication.java"
EXEC_EXEC = ["mvn", "-e", "exec:exec"]
WRONG, REAL = "com.example.QpidAmqpApp", "com.example.ProtocolApplication"
PLUGIN = "build/plugins/plugin[exec-maven-plugin]"


def _sealed(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-3 content"
    return data.decode("utf-8")


def _run3_candidate(path, pom=None):
    """The sealed candidate as s8 found it: s1's pom.xml and s6's application class."""
    (path / "src/main/java/com/example").mkdir(parents=True, exist_ok=True)
    (path / "pom.xml").write_text(_sealed("pom.s1.xml") if pom is None else pom, encoding="utf-8")
    (path / APP).write_text(_sealed("ProtocolApplication.s6.java"), encoding="utf-8")
    return PolymorphicValidator(str(path), autonomy_cfg=AutonomyConfig())


def _exec(executable="java", arguments=""):
    return f"<configuration><executable>{executable}</executable><arguments>{arguments}</arguments></configuration>"


def _args(*items):
    return "".join(item if item.startswith("<") else f"<argument>{item}</argument>" for item in items)


# ------------------------------------------------------------------------------------------------ the sealed run-3 failure, replayed

def test_run3_replay_the_pom_named_class_is_diagnosed_and_is_the_candidates_defect(tmp_path):
    """Before the correction ``diagnose_runtime_entrypoint`` returned None for ``exec:exec``, so the classification below was None and
    the run ended as verifier infrastructure."""
    output = _sealed("s8_launch_failure.output.txt")
    diagnosis = _run3_candidate(tmp_path).diagnose_runtime_entrypoint(EXEC_EXEC)
    assert diagnosis is not None
    assert diagnosis["effective_entrypoint"] == WRONG and diagnosis["requested_entrypoint"] is None
    assert diagnosis["effective_entrypoint_provenance"] == "CANDIDATE_BUILD_CONFIG"
    assert diagnosis["candidate_config_source"] == "pom.xml"
    assert diagnosis["candidate_config_key"] == f"{PLUGIN}/configuration/arguments/argument = ${{exec.mainClass}}, properties/exec.mainClass"
    assert diagnosis["source_declares_entrypoint"] is False and diagnosis["compiled_artifact_exists"] is False
    assert f"Could not find or load main class {WRONG}" in output                       # the JVM's own answer, sealed
    assert classify_runtime_entrypoint(diagnosis, output) == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID
    # what the same output was called without a diagnosis (the sealed outcome): infrastructure
    assert classify_runtime_entrypoint(None, output) is None
    assert runtime_verification_infrastructure_reason({"output": output}) == (
        "runtime command could not load its configured application entrypoint")


def test_run3_replay_naming_the_real_class_in_the_pom_is_what_ends_the_defect(tmp_path):
    """The correction of the actual mismatch: the same candidate with the pom's property set to the class s6 really wrote. The class
    the configuration selects is then declared by a source, so a launch failure can no longer be the configuration's defect."""
    corrected = _sealed("pom.s1.xml").replace(f"<exec.mainClass>{WRONG}</exec.mainClass>", f"<exec.mainClass>{REAL}</exec.mainClass>")
    assert corrected != _sealed("pom.s1.xml")
    diagnosis = _run3_candidate(tmp_path, corrected).diagnose_runtime_entrypoint(EXEC_EXEC)
    assert diagnosis["effective_entrypoint"] == REAL and diagnosis["source_declares_entrypoint"] is True
    assert classify_runtime_entrypoint(diagnosis, _sealed("s8_launch_failure.output.txt")) is None     # the output names another class
    assert classify_runtime_entrypoint(diagnosis, NOT_FOUND.format(REAL)) == RUNTIME_ENTRYPOINT_NOT_BUILT   # never the pom's defect
    (tmp_path / "target/classes/com/example").mkdir(parents=True)
    (tmp_path / "target/classes/com/example/ProtocolApplication.class").write_bytes(b"built")
    built = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig()).diagnose_runtime_entrypoint(EXEC_EXEC)
    assert classify_runtime_entrypoint(built, NOT_FOUND.format(REAL)) == RUNTIME_ENTRYPOINT_NOT_LOADABLE


@pytest.mark.asyncio
async def test_run3_replay_the_verification_unit_routes_it_to_the_poms_owner_and_writes_nothing_itself(tmp_path):
    """s8 owns no file. The failure becomes the candidate's, grounded to pom.xml with the diagnosed class named; retry handling turns
    it into the existing cross-owner scope conflict for exactly [pom.xml]. No Developer call from the verification unit, no model
    attribution, and its own write scope stays empty. (That the enforce loop then reopens the pom's owner with exactly that file and
    reverifies is the existing test_enforce_reopens_the_pom_owner_only_for_pom_and_reverifies.)"""
    validator = _run3_candidate(tmp_path)
    output = _sealed("s8_launch_failure.output.txt")
    live = {"success": False, "timed_out": False, "returncode": 1, "runtime_prerequisite": "PASSED", "output": output,
            "steps": [{"command": ["mvn"], "exit_code": 1, "timed_out": False}],
            "entrypoint_diagnosis": validator.diagnose_runtime_entrypoint(EXEC_EXEC)}
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={"should_run": True, "run_commands": [list(EXEC_EXEC)], "command_source": "inferred",
                                                 "success_criteria": "prints the [RESULT] line"})
    run_verifier.grade = AsyncMock(side_effect=AssertionError("a launch failure is never graded"))
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier, established_files=["pom.xml", APP])
    # A hand-built AttemptContext sits below the two places the product binds a configuration to its project (load_config, the run
    # entry), and the store API refuses an unbound one by design. The D4 twin of this test gets the binding from the harness's file
    # list (tests/conftest.py BELOW_PROJECT_BINDING_FILES); here it is stated: the run's workspace, exactly what the run entry binds.
    # MEASURED (owner run 2026-11-19, c3-fix/check.v1): without it the attempt stops with PROJECT_IDENTITY_MISSING before the gate.
    bind_project_identity(ctx.kernel.config, ctx.workspace_path)
    state = GenerationState()
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence", return_value=live):
        with pytest.raises(QualityGateFailure) as raised:
            await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type == "candidate_runtime_entrypoint_invalid" and failure.likely_files == ["pom.xml"]
    assert failure.diagnostics["reason_code"] == CANDIDATE_RUNTIME_ENTRYPOINT_INVALID
    assert "VERIFICATION_INFRASTRUCTURE_FAILURE" not in failure.message
    assert f"selects the runtime main class {WRONG}" in failure.message and "The run command names no other main class" in failure.message
    assert "exec.mainClass=None" not in failure.message
    with patch("kriya.workflow.retry_strategy.attribute_failure",
               new=AsyncMock(side_effect=AssertionError("deterministic evidence: no model attribution"))):
        assert await handle_attempt_failure(state, ctx, raised.value) is True
    conflict = state.plan_scope_conflict
    assert conflict["reason_code"] == "PLAN_SCOPE_REVISION_REQUIRED" and conflict["attribution_tier"] == "authoritative_deterministic"
    assert conflict["required_files"] == ["pom.xml"] and conflict["allowed_files"] == []
    # the reason is the MUST FIX text the reopened owner is shown: it names the file, the class and where, and what makes it right
    assert f"pom.xml selects '{WRONG}'" in conflict["reason"] and "properties/exec.mainClass" in conflict["reason"]
    assert "fully qualified name (package and class) of the class that declares the application's main method" in conflict["reason"]
    assert ctx.write_scope_mode is WriteScopeMode.DENY_ALL and list(ctx.allowed_write_relpaths) == []
    assert not developer.run_generation.called


# ------------------------------------------------------------------------- what an exec:exec configuration proves, and what it does not

@pytest.mark.parametrize(("config", "properties", "cli", "effective", "provenance"), [
    # the run-3 shape: options, a classpath element as the value of -classpath, then the property-named class
    (_exec(arguments=_args("-Dx=1", "-classpath", "<classpath/>", "${exec.mainClass}")), "<exec.mainClass>demo.NonexistentA</exec.mainClass>",
     (), "demo.NonexistentA", "CANDIDATE_BUILD_CONFIG"),
    (_exec(arguments=_args("-cp", "<classpath/>", "demo.Literal")), "", (), "demo.Literal", "CANDIDATE_BUILD_CONFIG"),
    (_exec("/usr/lib/jvm/17/bin/java", _args("-cp", "<classpath/>", "demo.Literal")), "", (), "demo.Literal", "CANDIDATE_BUILD_CONFIG"),
    # an option taking a value consumes it; an option that merely CONTAINS a reference is still an option
    (_exec(arguments=_args("--add-opens", "java.base/java.nio=ALL-UNNAMED", "-Dp=${q}", "demo.Literal", "program-arg")), "", (),
     "demo.Literal", "CANDIDATE_BUILD_CONFIG"),
    # a whole-argument reference that resolves to an option is an option
    (_exec(arguments=_args("${jvm.flag}", "demo.Literal")), "<jvm.flag>-Xmx64m</jvm.flag>", (), "demo.Literal", "CANDIDATE_BUILD_CONFIG"),
    # the command's own -D property wins over the POM's: then the choice is Kriya's, not the candidate's
    (_exec(arguments=_args("-cp", "<classpath/>", "${exec.mainClass}")), "<exec.mainClass>demo.FromPom</exec.mainClass>",
     ("-Dexec.mainClass=demo.FromCli",), "demo.FromCli", "KRIYA_COMMAND"),
    # ---- nothing is proven: UNKNOWN, never a guess from position
    (_exec("bash", _args("-c", "demo.NotAClass")), "", (), None, "UNKNOWN"),                       # not the Java launcher
    (_exec("${java.launcher}", _args("demo.Literal")), "<java.launcher>java</java.launcher>", (), None, "UNKNOWN"),
    (_exec(arguments=_args("-jar", "target/app.jar")), "", (), None, "UNKNOWN"),                   # launches no named class
    (_exec(arguments=_args("-m", "demo/demo.Main")), "", (), None, "UNKNOWN"),
    (_exec(arguments=_args("<classpath/>", "demo.Literal")), "", (), None, "UNKNOWN"),             # a classpath where the class would be
    (_exec(arguments=_args("-cp", "<classpath/>", "${undefined}")), "", (), None, "UNKNOWN"),
    (_exec(arguments=_args("-cp", "<classpath/>", "${pkg}.Main")), "<pkg>demo</pkg>", (), None, "UNKNOWN"),   # a partial reference
    (_exec(arguments=_args("-cp", "<classpath/>", "-Donly=options")), "", (), None, "UNKNOWN"),
    ("<configuration><mainClass>demo.ForExecJava</mainClass></configuration>", "", (), None, "UNKNOWN"),      # no <arguments> at all
    ("", "", (), None, "UNKNOWN"),
    (_exec(arguments=_args("-cp", "<classpath/>", "demo.Literal")), "", ("-Dexec.args=-version",), None, "UNKNOWN"),   # replaced on the command line
    (_exec(arguments=_args("-cp", "<classpath/>", "demo.Literal")), "", ("-Dexec.executable=echo",), None, "UNKNOWN"),
])
def test_what_an_exec_exec_configuration_proves(tmp_path, config, properties, cli, effective, provenance):
    diagnosis = _project(tmp_path, config, properties).diagnose_runtime_entrypoint([*EXEC_EXEC, *cli])
    assert diagnosis["effective_entrypoint"] == effective and diagnosis["effective_entrypoint_provenance"] == provenance
    assert (diagnosis["candidate_config_source"] == "pom.xml") is (provenance == "CANDIDATE_BUILD_CONFIG")
    if provenance == "UNKNOWN":      # the caller's existing classification stands: nothing is blamed on the candidate
        assert classify_runtime_entrypoint(diagnosis, NOT_FOUND.format("demo.Literal")) is None


def test_a_parent_pom_may_configure_it_and_the_default_cli_execution_wins(tmp_path):
    parent = "<parent><groupId>p</groupId><artifactId>p</artifactId><version>1</version></parent>"
    assert _project(tmp_path, "", "", parent).diagnose_runtime_entrypoint(EXEC_EXEC)["effective_entrypoint_provenance"] == "UNKNOWN"
    config = ("<executions><execution><id>default-cli</id>" + _exec(arguments=_args("-cp", "<classpath/>", "demo.Cli"))
              + "</execution></executions>" + _exec(arguments=_args("-cp", "<classpath/>", "demo.Plugin")))
    diagnosis = _project(tmp_path, config).diagnose_runtime_entrypoint(EXEC_EXEC)
    assert diagnosis["effective_entrypoint"] == "demo.Cli"
    assert diagnosis["candidate_config_key"] == f"{PLUGIN}/executions/execution[default-cli]/configuration/arguments/argument"


def test_the_negative_controls_a_valid_entry_point_kriyas_own_choice_and_an_unrelated_missing_class(tmp_path):
    """demo.ExistingB is declared by the project. None of these may be called the candidate configuration's defect."""
    config = _exec(arguments=_args("-cp", "<classpath/>", "${exec.mainClass}"))
    validator = _project(tmp_path, config, "<exec.mainClass>demo.ExistingB</exec.mainClass>")
    valid = validator.diagnose_runtime_entrypoint(EXEC_EXEC)
    assert valid["effective_entrypoint"] == "demo.ExistingB" and valid["source_declares_entrypoint"] is True
    assert classify_runtime_entrypoint(valid, NOT_FOUND.format("demo.ExistingB")) == RUNTIME_ENTRYPOINT_NOT_BUILT        # build, not pom
    assert classify_runtime_entrypoint(valid, NOT_FOUND.format("org.apache.qpid.SomethingElse")) is None                # unrelated class
    assert classify_runtime_entrypoint(valid, "Exception in thread main: IllegalStateException") is None                # not a launch failure
    kriya = validator.diagnose_runtime_entrypoint([*EXEC_EXEC, "-Dexec.mainClass=demo.WrongFromCommand"])
    assert classify_runtime_entrypoint(kriya, NOT_FOUND.format("demo.WrongFromCommand")) == RUNTIME_COMMAND_ENTRYPOINT_INVALID
    assert (tmp_path / SOURCE).read_text() == B                                                                          # nothing is written


def test_exec_java_and_plain_java_are_diagnosed_exactly_as_before(tmp_path):
    validator = _project(tmp_path, "<configuration><mainClass>demo.NonexistentA</mainClass></configuration>")
    java_goal = validator.diagnose_runtime_entrypoint(["mvn", "-e", "exec:java", "-Dexec.mainClass=demo.ExistingB"])
    assert (java_goal["effective_entrypoint"], java_goal["requested_entrypoint"], java_goal["candidate_config_key"]) == (
        "demo.NonexistentA", "demo.ExistingB", f"{PLUGIN}/configuration/mainClass")
    plain = validator.diagnose_runtime_entrypoint(["java", "-cp", "target/classes", "demo.Wrong"])
    assert (plain["effective_entrypoint"], plain["effective_entrypoint_provenance"]) == ("demo.Wrong", "KRIYA_COMMAND")
    assert validator.diagnose_runtime_entrypoint(["mvn", "-q", "test"]) is None and validator.diagnose_runtime_entrypoint(["java", "-jar", "a.jar"]) is None


# ------------------------------------------------------------------------------------- A: the plan's entry point reaches the build file

def _unit(sid, path, depends_on=()):
    return Subtask(id=sid, description=f"write {path}", execution_method=ExecutionMethod.MODEL, depends_on=list(depends_on),
                   planned_files=[PlannedFile(path=path, action=FileAction.CREATE)])


def _verify(sid, depends_on):
    return Subtask(id=sid, description="run the application", execution_method=ExecutionMethod.MODEL,
                   execution_role=ExecutionRole.VERIFICATION, planned_files=[], depends_on=list(depends_on),
                   verification=[VerificationMethod(type=VerificationMethodType.JUDGMENT, verifier_kind=VerifierKind.APPLICATION_RUNTIME,
                                                    requires_runtime_execution=True, description="observe the application's output")])


def _plan(*subtasks):
    return EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=list(subtasks))


def _run3_plan():
    """The sealed run-3 plan's units and files (ids, paths and dependencies as the run logged them at plan validation)."""
    return _plan(
        _unit("s1", "pom.xml"),
        _unit("s2", "src/main/resources/qpid-initial-config.json", ["s1"]),
        _unit("s3", "src/main/resources/system.properties", ["s1"]),
        _unit("s4", "src/main/java/com/example/Protocol.java", ["s1"]),
        _unit("s5", "src/main/java/com/example/ProtocolParser.java", ["s1", "s4"]),
        _unit("s6", APP, ["s1", "s2", "s3", "s4", "s5"]),
        _unit("s7", "src/test/java/com/example/ProtocolParserTest.java", ["s1", "s5"]),
        _verify("s8", ["s1", "s6"]),
    )


def _context(plan, sid):
    return json.loads(build_subtask_semantic_context(plan, plan.subtask_by_id(sid)).split("\n", 1)[1])


def test_run3_plan_the_pom_owner_and_the_entry_point_owner_are_told_the_same_one_fact():
    plan = _run3_plan()
    fact = {"file": APP, "planned_by": "s6", "java_main_class": REAL, "authority": PLANNED_ENTRYPOINT_AUTHORITY}
    assert _context(plan, "s1")["planned_entrypoint"] == fact                # s1 writes exec.mainClass: it now knows the class
    assert _context(plan, "s6")["planned_entrypoint"] == fact                # the same object, from the same plan
    assert "exactly this one" in PLANNED_ENTRYPOINT_AUTHORITY and "do not invent another name" in PLANNED_ENTRYPOINT_AUTHORITY
    for sid in ("s2", "s3", "s4", "s5", "s7", "s8"):                         # it concerns nobody else: their context is as it was
        assert "planned_entrypoint" not in build_subtask_semantic_context(plan, plan.subtask_by_id(sid)), sid
    assert WRONG not in json.dumps(_context(plan, "s1"))                     # nothing about this project is built in


def test_no_entry_point_is_required_of_a_library_and_an_ambiguous_plan_says_nothing():
    library = _plan(_unit("s1", "pom.xml"), _unit("s2", "src/main/java/com/example/ProtocolParser.java", ["s1"]))
    assert planned_entrypoint_for(library, library.subtask_by_id("s1")) is None
    assert "planned_entrypoint" not in build_subtask_semantic_context(library, library.subtask_by_id("s1"))
    two = _plan(_unit("s1", "pom.xml"), _unit("s2", APP, ["s1"]), _unit("s3", "src/main/java/com/example/BrokerServer.java", ["s1"]))
    assert all(planned_entrypoint_for(two, unit) is None for unit in two.subtasks)


def test_one_entry_point_file_planned_by_two_units_is_still_one_entry_point():
    """PLANNED-ENTRYPOINT-ONE-FILE-SEVERAL-UNITS-001: a file is counted once however many units plan it. Two DIFFERENT entry-point
    files stay ambiguous (the test above)."""
    plan = _plan(_unit("s1", "pom.xml"), _unit("s2", APP, ["s1"]), _unit("s3", "src/main/java/com/example/Protocol.java", ["s1"]),
                 _unit("s4", APP, ["s1", "s2"]))
    fact = {"file": APP, "planned_by": "s2", "java_main_class": REAL, "authority": PLANNED_ENTRYPOINT_AUTHORITY}
    assert [planned_entrypoint_for(plan, plan.subtask_by_id(sid)) for sid in ("s1", "s2", "s4")] == [fact, fact, fact]
    assert planned_entrypoint_for(plan, plan.subtask_by_id("s3")) is None                 # it concerns nobody else


def test_run7_plan_the_pom_owner_is_told_the_entry_point_two_units_plan():
    """MEASURED (Stage C run 20261120T102800-54953f31, the saved plan): s5 and s7 both plan ProtocolApplication.java; s1 wrote the pom
    without the fact and named com.example.QpidApplication. Now s1, s5 and s7 get the same one fact; no other unit does."""
    saved = json.loads((Path(__file__).parent / "fixtures" / "stage_c_run7" / "plan.json").read_text(encoding="utf-8"))
    plan = EngineeringPlan.model_validate(saved)
    owners = [unit.id for unit in plan.subtasks if any(f.path == APP for f in unit.planned_files)]
    assert owners == ["s5", "s7"] and [f.path for f in plan.subtask_by_id("s1").planned_files] == ["pom.xml"]
    fact = {"file": APP, "planned_by": "s5", "java_main_class": REAL, "authority": PLANNED_ENTRYPOINT_AUTHORITY}
    for sid in ("s1", "s5", "s7"):
        assert _context(plan, sid)["planned_entrypoint"] == fact, sid
    for sid in ("s2", "s3", "s4", "s6", "s8"):
        assert "planned_entrypoint" not in build_subtask_semantic_context(plan, plan.subtask_by_id(sid)), sid
    assert "QpidApplication" not in json.dumps(_context(plan, "s1"))                    # nothing about the run's wrong name is built in


def test_only_a_java_source_under_the_maven_root_implies_a_class_name():
    python = _plan(_unit("s1", "pyproject.toml"), _unit("s2", "src/tool/main.py", ["s1"]))
    assert planned_entrypoint_for(python, python.subtask_by_id("s1")) == {
        "file": "src/tool/main.py", "planned_by": "s2", "authority": PLANNED_ENTRYPOINT_AUTHORITY}
    elsewhere = _plan(_unit("s1", "pom.xml"), _unit("s2", "tools/Runner.java", ["s1"]))
    assert "java_main_class" not in planned_entrypoint_for(elsewhere, elsewhere.subtask_by_id("s1"))
    default_package = _plan(_unit("s1", "pom.xml"), _unit("s2", "src/main/java/Application.java", ["s1"]))
    assert planned_entrypoint_for(default_package, default_package.subtask_by_id("s1"))["java_main_class"] == "Application"
