"""D2 EXEC-LAYER rebuild S4: runtime / toolchain external-execution ownership.

Three measured defects, each with its original reproducer (2026-10-25):

1. ``check_java_toolchain`` ran ``mvn -version`` in the process's inherited cwd - the target repository whenever Kriya is started inside it - and
   Maven loaded the project's ``.mvn/jvm.config`` there: a project ``-javaagent:`` ran as HOST code during a toolchain probe.
2. The finite-command artifact preparation (``mvn package``) ran on the HOST under mandatory containment, and as the project's own ``./mvnw`` when
   one is executable: a project script ran on the host.
3. R4: the raw-javac fallback compiled with HOST absolute paths, which mean nothing inside the container (``file not found: <view>/A.java``): a
   contained compile of an ordinary candidate failed, so a wrong verdict reached the Developer.

What stays as it was is pinned as firmly: the host path of each is byte for byte unchanged.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import types
from pathlib import Path

import pytest
from test_oi142_gate_view import _repo
from test_s3_execution_views import _contained, _docker_reachable

from kriya.config.config import AutonomyConfig
from kriya.tools.process import ProcessResult
from kriya.tools.service_runtime import (
    ServiceVerificationOutcomeKind,
    _prepare_required_artifact,
)
from kriya.tools.validate import RUNTIME_VERIFICATION_GATE, PolymorphicValidator, check_java_toolchain
from kriya.workflow import runtime_verification as rv

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_SOURCE = ("public class Agent { public static void premain(String a) throws Exception "
                "{ new java.io.File(System.getenv(\"KRIYA_CANARY\")).createNewFile(); } }\n")
POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>d</groupId><artifactId>app</artifactId>'
       '<version>1</version><packaging>jar</packaging><properties><maven.compiler.release>17</maven.compiler.release></properties><build><plugins>'
       '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin>'
       '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-resources-plugin</artifactId><version>3.3.1</version></plugin>'
       '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-jar-plugin</artifactId><version>3.4.1</version></plugin>'
       '</plugins></build></project>')


# ----------------------------------------------------------------- 1. the toolchain probe

def _project_with_jvm_agent(tmp_path, monkeypatch) -> Path:
    """A project whose ``.mvn/jvm.config`` names a ``-javaagent:`` that creates ``$KRIYA_CANARY`` when the JVM starts."""
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "Agent.java").write_text(AGENT_SOURCE)
    (agent / "manifest.txt").write_text("Premain-Class: Agent\n")
    subprocess.run(["javac", "Agent.java"], cwd=agent, check=True, capture_output=True)
    subprocess.run(["jar", "cfm", "agent.jar", "manifest.txt", "Agent.class"], cwd=agent, check=True, capture_output=True)
    project = tmp_path / "project"
    (project / ".mvn").mkdir(parents=True)
    (project / ".mvn" / "jvm.config").write_text(f"-javaagent:{agent / 'agent.jar'}\n")
    monkeypatch.setenv("KRIYA_CANARY", str(tmp_path / "CANARY"))
    return project


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ("mvn", "javac", "jar")), reason="a JDK and Maven are required")
def test_a_project_jvm_agent_does_not_run_during_the_toolchain_probe(tmp_path, monkeypatch):
    project = _project_with_jvm_agent(tmp_path, monkeypatch)
    canary = tmp_path / "CANARY"
    monkeypatch.chdir(project)
    subprocess.run(["mvn", "-version"], capture_output=True, check=True)
    assert canary.exists(), "premise: Maven applies the project's jvm.config to a plain `mvn -version` in its directory"
    canary.unlink()

    toolchain = check_java_toolchain()                       # the process is started INSIDE the project, the way a developer does
    assert toolchain["mvn_found"] is True and toolchain["mvn_java_version"], toolchain
    assert not canary.exists(), "the project's agent ran as host code during the probe"


def _recorded_probes(monkeypatch, *, fail=None):
    seen = []

    def run(cmd, **kwargs):
        cwd = kwargs.get("cwd")
        seen.append({"tool": cmd[0], "cwd": cwd, "was_empty": cwd is not None and os.listdir(cwd) == [], "inherited": os.getcwd()})
        if fail is not None:
            raise fail
        return types.SimpleNamespace(stdout="Java version: 17.0.1\n", stderr='openjdk version "17.0.1"\n')

    monkeypatch.setattr("kriya.tools.validate.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("kriya.tools.validate.subprocess.run", run)
    return seen


def test_both_probes_run_in_one_kriya_owned_empty_directory_that_is_removed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = _recorded_probes(monkeypatch)
    check_java_toolchain()
    assert [probe["tool"] for probe in seen] == ["java", "mvn"]
    cwds = {probe["cwd"] for probe in seen}
    assert len(cwds) == 1 and cwds != {None} and all(probe["was_empty"] for probe in seen), seen
    (cwd,) = cwds
    assert os.path.realpath(cwd) != os.path.realpath(str(tmp_path)), "the inherited cwd is not used"
    assert not os.path.lexists(cwd), "the probe directory is removed with the call"


def test_the_probe_directory_is_removed_when_a_probe_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = _recorded_probes(monkeypatch, fail=OSError("exec format error"))
    result = check_java_toolchain()
    assert result["java_found"] is False and result["mvn_found"] is False and result["mismatch"] is False
    assert seen and not os.path.lexists(seen[0]["cwd"])


# ----------------------------------------------------------------- 2. the artifact preparation

def _maven_project(tmp_path, *, executable_wrapper) -> Path:
    """A Maven project whose ``./mvnw`` (when executable) records that it ran on the HOST and builds the artifact itself."""
    (tmp_path / "pom.xml").write_text("<project/>")
    if executable_wrapper:
        wrapper = tmp_path / "mvnw"
        wrapper.write_text(f"#!/bin/sh\necho ran > '{tmp_path}/HOST_CANARY'\nmkdir -p target && touch target/app.jar\nexit 0\n")
        wrapper.chmod(0o755)
    return tmp_path


class _Controller:
    """A controller double that only records the command it was asked to run."""

    def __init__(self, result=None):
        self.runs = []
        self._result = result or ProcessResult(returncode=0, stdout="", stderr="", timeout=False)

    def run(self, command, **kwargs):
        self.runs.append((list(command), kwargs))
        return self._result


def _builder(project, *, returncode=0, timeout=False, create_jar=True):
    calls = []

    def build(argv, seconds):
        calls.append((list(argv), seconds))
        if create_jar:
            (project / "target").mkdir(exist_ok=True)
            (project / "target" / "app.jar").write_text("jar")
        return {"returncode": returncode, "stdout": "out", "stderr": "err", "timeout": timeout, "toolchain_identity": {"image": "maven"}}

    return build, calls


def test_a_contained_build_never_runs_the_project_wrapper_or_a_host_process(tmp_path):
    project = _maven_project(tmp_path, executable_wrapper=True)
    build, calls = _builder(project)
    controller = _Controller()
    outcome = _prepare_required_artifact(["java", "-jar", "target/app.jar"], str(project), controller=controller, contained_build=build)
    assert outcome.outcome is None and calls == [(["mvn", "package", "-DskipTests"], 300)], (outcome, calls)
    assert controller.runs == [], "no host process"
    assert not (project / "HOST_CANARY").exists(), "the project's ./mvnw did not run"
    assert outcome.toolchain_identity == {"image": "maven"} and outcome.command == ["mvn", "package", "-DskipTests"]


@pytest.mark.parametrize("returncode, timeout, create_jar, expected", [
    (1, False, False, ServiceVerificationOutcomeKind.PREPARATION_FAILED),
    (-1, True, False, ServiceVerificationOutcomeKind.PREPARATION_FAILED),
    (0, True, True, ServiceVerificationOutcomeKind.PREPARATION_FAILED),      # a timeout is a failure even when the exit code says 0 and the artifact exists
    (0, False, False, ServiceVerificationOutcomeKind.ARTIFACT_MATERIALIZATION_FAILED),
])
def test_a_contained_build_maps_its_result_to_the_same_outcomes_as_the_host_build(tmp_path, returncode, timeout, create_jar, expected):
    project = _maven_project(tmp_path, executable_wrapper=False)
    build, _calls = _builder(project, returncode=returncode, timeout=timeout, create_jar=create_jar)
    outcome = _prepare_required_artifact(["java", "-jar", "target/app.jar"], str(project), controller=_Controller(), contained_build=build)
    assert outcome.outcome is expected and outcome.command == ["mvn", "package", "-DskipTests"]
    assert outcome.returncode == returncode and outcome.stdout == "out" and outcome.stderr == "err"


def test_a_contained_build_with_a_host_env_is_a_programming_error_not_a_preparation_failure(tmp_path):
    project = _maven_project(tmp_path, executable_wrapper=False)
    build, _calls = _builder(project)
    with pytest.raises(ValueError):
        _prepare_required_artifact(["java", "-jar", "target/app.jar"], str(project), controller=_Controller(), env={"A": "b"}, contained_build=build)


# ----------------------------------------------------------------- the callers decide by the validator's canonical decision

class _Validator:
    def __init__(self, workspace, *, contained):
        self.workspace_path = str(workspace)
        self._contained = contained
        self.contained_maven_build = lambda argv, seconds: {}

    def external_execution(self):
        return types.SimpleNamespace(contained=self._contained)


def test_the_finite_command_preparation_always_takes_the_validators_contained_build(tmp_path):
    """S8: contained is the only mode - the preparation is handed the validator's contained Maven build and no host env/preexec policy."""
    received = []

    def spy(command, cwd, **kwargs):
        received.append((command, cwd, kwargs))
        return types.SimpleNamespace(outcome=None)

    validator = _Validator(tmp_path, contained=True)
    deps = types.SimpleNamespace(_prepare_required_artifact=spy)
    ctx = types.SimpleNamespace(established_files=[])
    state = types.SimpleNamespace(all_files_written=[])
    rv._prepare_finite_command_runtime_artifacts(deps, [["java", "-jar", "target/app.jar"]], validator, ctx, state)
    (_command, cwd, kwargs), = received
    assert cwd == str(tmp_path)
    assert (kwargs["env"], kwargs["preexec_fn"]) == (None, None) and kwargs["contained_build"] is validator.contained_maven_build


def _calls_in(path, function, callee):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    (node,) = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function]
    return [call for call in ast.walk(node) if isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", None)) == callee]


def test_the_finite_command_build_is_launched_by_one_function_that_asks_the_owner_and_nothing_else_reaches_it():
    prep = _calls_in("kriya/workflow/runtime_verification.py", "_prepare_finite_command_runtime_artifacts", "_prepare_required_artifact")
    assert len(prep) == 1 and "contained_build" in {k.arg for k in prep[0].keywords}
    managed = _calls_in("kriya/workflow/runtime_verification.py", "_execute_managed_service_verification", "run_managed_service_verification")
    assert len(managed) == 1 and "containment_profile" in {k.arg for k in managed[0].keywords}      # the managed path stays on the validator's profile
    callers = set()
    for path in sorted((REPO_ROOT / "kriya").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in (
                    "_prepare_required_artifact", "run_managed_service_verification"):
                callers.add(rel)
    # service_runtime itself (the managed lifecycle prepares its own artifact) and runtime_verification (the two callers above); attempt.py only wires names
    assert callers == {"kriya/tools/service_runtime.py", "kriya/workflow/runtime_verification.py"}, callers


def test_contained_maven_build_runs_the_validators_own_maven_path_and_only_mvn(tmp_path, monkeypatch):
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    seen = []
    monkeypatch.setattr(PolymorphicValidator, "_run_maven_cmd", lambda self, goals, cwd, timeout=300, **kw: seen.append((goals, cwd, timeout)) or {"returncode": 0})
    assert validator.contained_maven_build(["mvn", "package", "-DskipTests"], 300) == {"returncode": 0}
    assert seen == [(["package", "-DskipTests"], str(tmp_path), 300)]
    for refused in (["./mvnw", "package"], ["java", "-jar", "x"], []):
        with pytest.raises(ValueError):
            validator.contained_maven_build(refused, 5)


# ----------------------------------------------------------------- the exec-into-container sites cannot be given a host target

def test_only_the_oci_backend_produces_an_exec_target():
    producers = set()
    for path in sorted((REPO_ROOT / "kriya").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "PreparedContainment" and any(
                    k.arg == "exec_target" for k in node.keywords):
                producers.add(path.relative_to(REPO_ROOT).as_posix())
    assert producers == {"kriya/tools/containment_oci.py"}, producers


# ----------------------------------------------------------------- 3. R4: javac paths inside the container

def _write(root, rel, text):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def _javac_commands(validator, files, monkeypatch):
    seen = []
    monkeypatch.setattr(PolymorphicValidator, "_run_cmd_with_timeout",
                        lambda self, cmd, cwd=None, **_kw: seen.append((list(cmd), cwd)) or {"returncode": 0, "stdout": "", "stderr": ""})
    validator.run_compile_check(files)
    return seen


def test_contained_javac_gets_workspace_relative_paths(tmp_path, monkeypatch):
    _write(tmp_path, "src/App.java", "class App {}")
    _write(tmp_path, "src/Util.java", "class Util {}")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_contained())
    seen = _javac_commands(validator, ["src/App.java", "src/Missing.java", str(tmp_path / "src/Util.java")], monkeypatch)
    assert [cmd for cmd, _cwd in seen if cmd[0] == "javac"] == [["javac", "-proc:none", "-d", "build", "src/App.java", "src/Util.java"]], seen
    assert os.path.isdir(tmp_path / "build")


@pytest.mark.skipif(shutil.which("javac") is None, reason="a JDK is required")
def test_a_relative_javac_destination_is_registered_as_the_views_output_root(tmp_path):
    """``javac -d out`` names a destination relative to the command's cwd (the view): it is the gate's declared output, not an unauthorized creation."""
    origin = _repo(tmp_path, extra={"A.java": "public class A { public static void main(String[] a) { } }\n"})
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=AutonomyConfig())
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):
        validator._run_cmd_with_timeout(["javac", "-d", "out", "A.java"], cwd=validator.workspace_path)  # pylint: disable=protected-access
        assert "out" in validator._gate_view.output_roots, validator._gate_view.output_roots  # pylint: disable=protected-access


# ----------------------------------------------------------------- the same through the REAL OCI runtime

@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_real_oci_a_contained_javac_fallback_compiles_directly_in_a_view_and_in_a_runtime_sequence(tmp_path):
    origin = _repo(tmp_path, extra={"A.java": "public class A { public static void main(String[] a) { System.out.println(\"hi\"); } }\n"})
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    assert validator.stack == "java" and validator.external_execution().contained
    direct = validator.run_compile_check(["A.java"])
    assert direct["success"] is True, direct["output"]
    with validator.verification_session("compile"):
        viewed = validator.run_compile_check(["A.java"])
    assert viewed["success"] is True, viewed["output"]
    broken = tmp_path / "broken"
    shutil.copytree(origin, broken)
    (broken / "A.java").write_text("public class A { this does not compile }\n")
    failing = PolymorphicValidator(str(broken), original_workspace_path=str(broken), autonomy_cfg=_contained()).run_compile_check(["A.java"])
    assert failing["success"] is False and "error:" in failing["output"] and "file not found" not in failing["output"], failing["output"]
    run = validator.run_app_sequence([["javac", "-d", "out", "A.java"], ["java", "-cp", "out", "A"]])
    assert run["success"] is True and "hi" in run["output"], run["output"]


@pytest.mark.skipif(not _docker_reachable() or shutil.which("git") is None, reason="docker daemon not reachable")
def test_real_oci_the_finite_command_preparation_builds_in_the_container_and_never_runs_the_project_wrapper(tmp_path):
    """The original reproducer: a Maven project with an executable ./mvnw. Under mandatory containment the wrapper used to run on the host."""
    origin = _repo(tmp_path, extra={"pom.xml": POM, "src/main/java/d/A.java": "package d; public class A { }\n"})
    wrapper = origin / "mvnw"
    wrapper.write_text(f"#!/bin/sh\necho ran > '{tmp_path}/HOST_CANARY'\nexit 1\n")
    wrapper.chmod(0o755)
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "wrapper"], check=True)
    validator = PolymorphicValidator(str(origin), original_workspace_path=str(origin), autonomy_cfg=_contained())
    deps = types.SimpleNamespace(_prepare_required_artifact=_prepare_required_artifact)
    ctx = types.SimpleNamespace(established_files=[])
    state = types.SimpleNamespace(all_files_written=[], attempt_number=1, record_gate_outcome=lambda *_: None)
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):
        rv._prepare_finite_command_runtime_artifacts(deps, [["java", "-jar", "target/app-1.jar"]], validator, ctx, state)
        assert os.path.isfile(os.path.join(validator.workspace_path, "target", "app-1.jar")), "the contained build produced the artifact in the view"
        assert "target" in validator._gate_view.output_roots, "its output root is registered as the gate's own"  # pylint: disable=protected-access
    assert not (tmp_path / "HOST_CANARY").exists(), "the project's ./mvnw ran on the host"
