"""D2 (2026-10-01 live KNOW rehearsal): runtime verification gets the same
bounded, registry-scoped Maven acquisition as compile/test, and an
unavailable verification dependency is a typed infrastructure stop - never a
Developer repair.

Measured at ee65e1c under the production (contained, offline-first) profile:
`mvn -e -q compile exec:exec` failed with "No plugin found for prefix 'exec'"
twice with no acquisition (run_app_sequence called the plain runner, and the
offline classifier did not know the message); the Developer then tried to
edit pom.xml and the run stopped NO_AUTHORIZED_REPAIR_TARGET."""
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
        self.calls.append({"cmd": cmd, "network": network, "acquisition": acquisition, "timeout": timeout})
        if acquisition:
            time.sleep(self.acquisition_sleep)
            return {"returncode": self.acquisition_rc, "stdout": "", "stderr": "", "timeout": False}
        rc, err = self.offline.pop(0)
        return {"returncode": rc, "stdout": "", "stderr": err, "timeout": False}

    @property
    def acquisitions(self):
        return [c for c in self.calls if c["acquisition"]]


def _validator(tmp_path, maven, budget=None):
    (tmp_path / "pom.xml").write_text("<project/>")
    autonomy = AutonomyConfig(contained_execution_required=True, generation_time_budget_seconds=budget)
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=autonomy)
    patcher = patch.object(validator, "_run_cmd_with_timeout", side_effect=maven)
    patcher.start()
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


def test_a_missing_plugin_gets_exactly_one_registry_scoped_acquisition_then_the_exact_retry(tmp_path):
    maven = _Maven((1, NO_PLUGIN), (0, ""))
    validator, patcher = _validator(tmp_path, maven)
    try:
        result = validator._run_runtime_step(RUNTIME, 90)  # pylint: disable=protected-access
    finally:
        patcher.stop()
    assert result["returncode"] == 0
    assert len(maven.acquisitions) == 1
    assert maven.acquisitions[0]["network"] is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY  # the existing authority
    offline = [c for c in maven.calls if not c["acquisition"]]
    assert len(offline) == 2 and offline[0]["cmd"] == offline[1]["cmd"]  # the exact command, retried once
    # No other network path: only the offline runs and the one registry-scoped acquisition.
    assert {c["network"] for c in maven.calls} == {NetworkAuthority.DENIED, NetworkAuthority.DEPENDENCY_REGISTRY_ONLY}


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
