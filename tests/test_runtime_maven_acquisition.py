"""D2 (2026-10-01 live KNOW rehearsal): runtime verification gets a bounded,
registry-scoped Maven acquisition, and an unavailable verification dependency
is a typed infrastructure stop - never a Developer repair.

D2B (KNOW-A 2026-10-01, reproduced model-free with real containers in
evidence/demo-defects-d3-d2b): the D2 acquisition re-ran the runtime goals
with registry network, so the candidate's main() executed with the registry
proxy (HTTP 200 from inside the candidate). The runtime acquisition now
resolves only the plugins the command names (`<plugin>:help`, in an empty
Kriya-owned directory) and the candidate runs once, offline.

Measured at ee65e1c under the production (contained, offline-first) profile:
`mvn -e -q compile exec:exec` failed with "No plugin found for prefix 'exec'"
twice with no acquisition (run_app_sequence called the plain runner, and the
offline classifier did not know the message); the Developer then tried to
edit pom.xml and the run stopped NO_AUTHORIZED_REPAIR_TARGET."""
import os
import shutil
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.config.config import AutonomyConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.tools.containment import NetworkAuthority
from kriya.tools.dependency_execution import OfflineFailureKind, classify_maven_offline_failure_text
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import (
    MAVEN_PLUGIN_UNAVAILABLE,
    RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE,
    runtime_verification_infrastructure_reason,
)
from kriya.workflow.workflow import WorkflowEngine

NO_PLUGIN = ("[ERROR] No plugin found for prefix 'exec' in the current project and in the plugin groups "
             "[org.apache.maven.plugins, org.codehaus.mojo] available from the repositories [local (/kriya/m2)]")
RUNTIME = ["mvn", "-e", "-q", "compile", "exec:exec", "-Dexec.mainClass=com.example.App"]


class _Maven:
    """Scripted process runner: offline attempts answer from ``offline`` in
    order; every call is recorded with its network authority."""

    def __init__(self, *offline, acquisition_rc=0, acquisition_sleep=0.0):
        self.offline, self.calls = list(offline), []
        self.acquisition_rc, self.acquisition_sleep = acquisition_rc, acquisition_sleep

    def __call__(self, cmd, cwd, timeout=300, network=NetworkAuthority.DENIED, acquisition=False, **kwargs):
        mount = kwargs.get("workspace_path")
        self.calls.append({"cmd": cmd, "network": network, "acquisition": acquisition, "timeout": timeout,
                           "cwd": cwd, "mount": mount, "mount_listing": os.listdir(mount) if mount else None})
        if acquisition:
            time.sleep(self.acquisition_sleep)
            return {"returncode": self.acquisition_rc, "stdout": "", "stderr": "", "timeout": False}
        rc, err = self.offline.pop(0)
        return {"returncode": rc, "stdout": "", "stderr": err, "timeout": False}

    @property
    def acquisitions(self):
        return [c for c in self.calls if c["acquisition"]]


def _validator(tmp_path, maven, budget=None, pom="<project/>"):
    (tmp_path / "pom.xml").write_text(pom)
    autonomy = AutonomyConfig(contained_execution_required=True, generation_time_budget_seconds=budget)
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=autonomy)
    patcher = patch.object(validator, "_run_cmd_with_timeout", side_effect=maven)
    patcher.start()
    # These tests are about the runtime command's own acquisition: the D3
    # build prerequisite (its own Maven calls) is covered in
    # tests/test_d3_runtime_prerequisite.py and kept out of this script.
    prepared = patch.object(validator, "_prepare_runtime", return_value=(None, None))
    prepared.start()
    patcher.stop = lambda stop=patcher.stop: (prepared.stop(), stop())
    return validator, patcher


def test_the_offline_classifier_recognizes_an_unresolvable_plugin_prefix():
    assert classify_maven_offline_failure_text(NO_PLUGIN) is OfflineFailureKind.MISSING_DEPENDENCY


def test_a_cached_plugin_needs_no_acquisition(tmp_path):
    maven = _Maven((0, ""))
    validator, patcher = _validator(tmp_path, maven)
    try:
        result = validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert result["returncode"] == 0 and maven.acquisitions == []
    assert maven.calls[0]["cmd"][-len(RUNTIME) + 1:] == RUNTIME[1:]  # the exact runtime goals, offline
    assert maven.calls[0]["network"] is NetworkAuthority.DENIED


def test_a_missing_plugin_gets_exactly_one_tooling_only_acquisition_then_the_exact_offline_retry(tmp_path):
    maven = _Maven((1, NO_PLUGIN), (0, ""))
    validator, patcher = _validator(tmp_path, maven)
    pom_before = (tmp_path / "pom.xml").read_bytes()
    try:
        result = validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert result["returncode"] == 0
    assert [c["acquisition"] for c in maven.calls] == [False, True, False]  # offline, ONE acquisition, offline
    acquisition = maven.acquisitions[0]
    assert acquisition["network"] is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY  # registry hosts only (SEC-006)
    # Tooling only: the plugin's inert help goal - never a candidate goal - in
    # an empty Kriya-owned directory mounted instead of the candidate.
    assert acquisition["cmd"][-1] == "exec:help"
    assert not set(RUNTIME[1:]) & set(acquisition["cmd"])
    assert acquisition["mount"] == acquisition["cwd"] != str(tmp_path) and acquisition["mount_listing"] == []
    assert not os.path.exists(acquisition["mount"])  # removed afterwards
    offline = [c for c in maven.calls if not c["acquisition"]]
    assert offline[0]["cmd"] == offline[1]["cmd"] and offline[0]["cmd"][-len(RUNTIME) + 1:] == RUNTIME[1:]
    assert {c["network"] for c in offline} == {NetworkAuthority.DENIED}  # the candidate only ever runs offline
    assert (tmp_path / "pom.xml").read_bytes() == pom_before  # never edited to make Kriya tooling available


def test_a_plugin_the_pom_declares_is_acquired_at_exactly_that_coordinate(tmp_path):
    pom = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><properties><exec.version>3.5.0</exec.version>"
           "</properties><build><plugins><plugin><groupId>org.codehaus.mojo</groupId>"
           "<artifactId>exec-maven-plugin</artifactId><version>${exec.version}</version></plugin></plugins>"
           "</build></project>")
    maven = _Maven((1, NO_PLUGIN), (0, ""))
    validator, patcher = _validator(tmp_path, maven, pom=pom)
    try:
        validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert maven.acquisitions[0]["cmd"][-1] == "org.codehaus.mojo:exec-maven-plugin:3.5.0:help"


def test_a_fully_qualified_plugin_goal_is_acquired_as_given(tmp_path):
    maven = _Maven((1, NO_PLUGIN), (0, ""))
    validator, patcher = _validator(tmp_path, maven)
    try:
        validator._run_runtime_step(["mvn", "org.codehaus.mojo:exec-maven-plugin:3.6.4:exec"], 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert maven.acquisitions[0]["cmd"][-1] == "org.codehaus.mojo:exec-maven-plugin:3.6.4:help"


def test_a_runtime_command_naming_no_plugin_is_never_acquired_by_running_it(tmp_path):
    """`mvn test` as a runtime command: no plugin to resolve without running
    the candidate's own goals, so no acquisition at all - a typed stop."""
    maven = _Maven((1, NO_PLUGIN))
    validator, patcher = _validator(tmp_path, maven)
    try:
        result = validator._run_runtime_step(["mvn", "-q", "test"], 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert maven.acquisitions == [] and len(maven.calls) == 1
    assert runtime_verification_infrastructure_reason({"output": result["stderr"]}).startswith(
        RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE)


def test_compile_and_test_keep_their_goal_derived_acquisition(tmp_path):
    """D2B is runtime-only: the compile/test gates' acquisition still runs
    their own goals (building/testing candidate code is what they do)."""
    maven = _Maven((1, NO_PLUGIN), (0, ""))
    validator, patcher = _validator(tmp_path, maven)
    try:
        validator._run_maven_cmd(["clean", "compile"], cwd=str(tmp_path))  # pylint: disable=protected-access
    finally:
        patcher.stop()
    acquisition = maven.acquisitions[0]
    assert acquisition["cmd"][-2:] == ["clean", "compile"] and acquisition["mount"] is None


def test_a_plugin_still_missing_after_acquisition_is_a_typed_verification_dependency_stop(tmp_path):
    maven = _Maven((1, NO_PLUGIN), (1, NO_PLUGIN))
    validator, patcher = _validator(tmp_path, maven)
    try:
        result = validator.run_app_sequence([RUNTIME])
    finally:
        patcher.stop()
    assert len(maven.acquisitions) == 1  # never a second acquisition
    reason = runtime_verification_infrastructure_reason(result)
    assert reason.startswith(RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE)


def test_an_exhausted_root_deadline_skips_acquisition_and_never_retries(tmp_path):
    maven = _Maven((1, NO_PLUGIN))
    validator, patcher = _validator(tmp_path, maven, budget=600)
    try:
        with patch.object(validator, "_run_deadline", return_value=time.monotonic() - 1):
            result = validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert maven.acquisitions == [] and len(maven.calls) == 1
    assert runtime_verification_infrastructure_reason({"output": result["stderr"]}).startswith(
        RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE)


def test_a_deadline_that_runs_out_during_acquisition_gets_no_retry(tmp_path):
    maven = _Maven((1, NO_PLUGIN), acquisition_sleep=0.3)
    validator, patcher = _validator(tmp_path, maven, budget=600)
    try:
        with patch.object(validator, "_run_deadline", return_value=time.monotonic() + 0.2):
            result = validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert len(maven.acquisitions) == 1
    assert len([c for c in maven.calls if not c["acquisition"]]) == 1  # no retry after the deadline
    assert maven.acquisitions[0]["timeout"] <= 1  # the acquisition itself was capped by the remaining budget
    assert "MAVEN_ACQUISITION_INCOMPLETE:" in result["stderr"]


def test_the_runtime_step_uses_the_run_root_deadline(tmp_path):
    from kriya.control.run_coordinator import begin_mutating_run, current_run_context

    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(generation_time_budget_seconds=600))
    assert validator._run_deadline() is None  # pylint: disable=protected-access  # outside a run: none
    with begin_mutating_run(str(tmp_path)):
        current_run_context()._lease.generation_clock = 1000.0  # pylint: disable=protected-access
        assert validator._run_deadline() == 1600.0  # pylint: disable=protected-access


def test_a_non_maven_runtime_step_runs_unchanged(tmp_path):
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True))
    with patch.object(validator, "_run_cmd_with_timeout", return_value={"returncode": 0, "stdout": "", "stderr": ""}) as run, \
            patch.object(validator, "_run_maven_cmd") as maven:
        validator._run_runtime_step(["java", "-jar", "app.jar"], 90)  # pylint: disable=protected-access
    maven.assert_not_called()
    run.assert_called_once_with(["java", "-jar", "app.jar"], cwd=str(tmp_path), timeout=90, stdin_payload=None)


@pytest.mark.parametrize(("output", "code"), [
    (NO_PLUGIN, MAVEN_PLUGIN_UNAVAILABLE),
    ("MAVEN_ACQUISITION_INCOMPLETE: offline execution ...", RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE),
])
@pytest.mark.asyncio
async def test_an_unavailable_runtime_dependency_never_reaches_the_developer(tmp_path, output, code):
    """The live failure path, end to end: the runtime verification command
    cannot obtain its Maven plugin. The run stops typed - no grading, no
    second Developer call, no pom.xml repair."""
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=["Step 1: Write code", "Design: Write app.py", "Review: Approved"])
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.developer.run_generation = AsyncMock(return_value=[{"filepath": "app.py", "content": "print('ok')\n"}])
    engine.run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [[sys.executable, "app.py"]],
        "command_source": "goal_explicit", "success_criteria": "prints ok"})
    engine.run_verifier.grade = AsyncMock()
    with patch("kriya.tools.validate.PolymorphicValidator.run_app_sequence",
               return_value={"success": False, "timed_out": False, "returncode": 1, "output": output}):
        result = await engine.run_generation_workflow(goal="Run with python app.py; it prints ok",
                                                      workspace_path=str(tmp_path))
    assert result["quality_gates_passed"] is False
    assert engine.developer.run_generation.await_count == 1  # generation only; never a repair attempt
    engine.run_verifier.grade.assert_not_awaited()
    assert result["environment_failure"].startswith("VERIFICATION_INFRASTRUCTURE_FAILURE")
    assert code in result["environment_failure"]
    assert not (tmp_path / "pom.xml").exists()


def _docker_reachable() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


APP_PROBE = r'''package demo;
import java.io.FileWriter;
import java.net.HttpURLConnection;
import java.net.URL;
public class App {
  public static void main(String[] a) throws Exception {
    String http;
    try {
      HttpURLConnection c = (HttpURLConnection) new URL("https://repo.maven.apache.org/maven2/").openConnection();
      c.setConnectTimeout(5000); c.setReadTimeout(5000);
      http = "HTTP " + c.getResponseCode();
    } catch (Exception e) { http = "no network"; }
    try (FileWriter w = new FileWriter("ran.log", true)) {
      w.write("proxy=" + System.getProperty("https.proxyHost") + " registry=" + http + "\n");
    }
  }
}
'''


@pytest.mark.skipif(shutil.which("docker") is None or not _docker_reachable(), reason="docker daemon not reachable")
def test_the_candidate_never_runs_with_registry_authority_real_containers(tmp_path):
    """The measured D2B scenario with real OCI containment and a fresh Maven
    cache (the exec plugin genuinely missing). Before the fix the candidate's
    main() ran twice - once inside the acquisition, seeing the registry proxy
    and reaching the registry (HTTP 200). Now: once, offline."""
    ws = tmp_path / "ws"
    (ws / "src/main/java/demo").mkdir(parents=True)
    pom = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><modelVersion>4.0.0</modelVersion><groupId>demo"
           "</groupId><artifactId>d2b</artifactId><version>1</version><properties><maven.compiler.release>17"
           "</maven.compiler.release></properties></project>")
    (ws / "pom.xml").write_text(pom)
    (ws / "src/main/java/demo/App.java").write_text(APP_PROBE)
    validator = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig(
        contained_execution_required=True, containment_backend="oci"))
    assert validator.run_compile_check(["src/main/java/demo/App.java"])["success"]
    assert not (ws / ".kriya/m2_cache/org/codehaus/mojo/exec-maven-plugin").exists()
    result = validator._run_runtime_step(["mvn", "-q", "exec:java", "-Dexec.mainClass=demo.App"], 120)  # pylint: disable=protected-access
    assert result["returncode"] == 0, result["stdout"] + result["stderr"]
    assert (ws / "ran.log").read_text().splitlines() == ["proxy=null registry=no network"]
    assert (ws / "pom.xml").read_text() == pom


def test_the_tooling_acquisition_container_mounts_only_the_empty_directory(tmp_path):
    """The profile behind the tooling-only acquisition mounts the empty
    Kriya-owned directory - so no candidate POM, `.mvn/extensions.xml` or
    class is visible to Maven - and every other profile still mounts the
    candidate workspace."""
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(
        contained_execution_required=True, containment_backend="oci"))
    empty = tmp_path / "empty"
    empty.mkdir()
    tooling, _backend = validator.build_containment_profile_and_backend(
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, acquisition=True, workspace_path=str(empty))
    default, _backend = validator.build_containment_profile_and_backend()
    assert tooling.workspace_path == str(empty)
    assert default.workspace_path == validator.workspace_path == str(tmp_path)
