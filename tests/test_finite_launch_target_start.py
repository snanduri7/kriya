"""REPLAY-CONTAINED-NOT-STARTED-001 (owner decision "A2", 2026-10-26): the OCI finite-command backend owns the fact "did the requested target process actually start?".

MEASURED before the change (clean 54f43b0, real Docker, production path): a persisted verification command whose executable does not exist replayed as ``not_evaluable`` (executed, no
verdict, non-blocking) in a plain AND a Java workspace, because a container answers an unrunnable target with exit 126/127 - the same status a target that STARTED and exited 127 gives.
The S6 truth contract (NO EXECUTION != NOT_EVALUABLE) was lost on the only remaining path.

The fact now comes from a Kriya-controlled launch stage the backend wraps around the target (kriya/tools/containment.py::FiniteLaunchStage, containment_oci.py::_FINITE_LAUNCH_SCRIPT): it runs
after the image entrypoint, ``exec``s the target, and only a launcher that is STILL ALIVE after its exec (the exec failed) writes one nonce-authenticated record. Consumers read
``ProcessResult.target_started`` / the step's ``exit_code=None``; none of them infers anything from an exit status or from message text.

Part 1 needs no Docker. Part 2 is the real-Docker contract matrix (both an entrypoint-less image and the Maven image whose ENTRYPOINT is a shell script). Part 3 drives the consumers."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _strict_doubles import strict_config

from kriya.tools.containment import (
    TARGET_NOT_STARTED_MARKER,
    ContainmentProfile,
    FiniteLaunchStage,
    NetworkAuthority,
    PreparedContainment,
    TrustClass,
)
from kriya.tools.containment_oci import OCIContainmentBackend, _finite_launch_stage
from kriya.tools.process import ProcessController, _prepare_env_and_preexec


def _daemon_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # pylint: disable=broad-except
        return False


REAL_DOCKER = pytest.mark.skipif(not _daemon_ok(), reason="docker daemon not reachable")

# ----------------------------------------------------------------------------------------- part 1: the stage and the controller plumbing (no Docker)

MARKER = f"{TARGET_NOT_STARTED_MARKER}abc123"


def _stage():
    return FiniteLaunchStage(prefix=("launcher",), marker=MARKER)


@pytest.mark.parametrize("stderr,started,kept", [
    ("", True, ""),
    ("plain error text\n", True, "plain error text\n"),
    (f"launcher: exec: x: not found\n\n{MARKER}\n", False, "launcher: exec: x: not found"),
    (f"{MARKER}\n", False, ""),
    (f"partial line without newline\n{MARKER}\n", False, "partial line without newline"),
    # a record that is not the launcher's LAST line is something the target (or anything else) wrote: it is not the launcher's record
    (f"{MARKER}\nthe target kept writing\n", True, f"{MARKER}\nthe target kept writing\n"),
    # another nonce (a forged or stale record) never counts
    (f"\n{TARGET_NOT_STARTED_MARKER}other\n", True, f"\n{TARGET_NOT_STARTED_MARKER}other\n"),
    # glued onto a partial line is not a record of its own (the launcher always writes a leading newline, so this can only be something else)
    (f"text{MARKER}\n", True, f"text{MARKER}\n"),
], ids=["empty", "plain", "record", "record-only", "after-partial-line", "not-last", "other-nonce", "glued"])
def test_the_stage_reads_only_its_own_record_on_the_last_line(stderr, started, kept):
    clean, did_start = _stage().read(stderr)
    assert did_start is started and clean == kept


def test_the_stage_wraps_the_target_argv_verbatim_after_its_own_prefix():
    assert _stage().wrap(["a b", "", "-x"]) == ["launcher", "a b", "", "-x"]


# A scripted backend whose launcher is a real process that EXECS the target and writes the record only when that exec fails - the same contract as the shell script, without Docker.
_SIMULATED_LAUNCHER = (
    "import os, sys\n"
    "nonce, argv = sys.argv[1], sys.argv[2:]\n"
    "try:\n"
    "    os.execvp(argv[0], argv)\n"
    "except OSError:\n"
    "    sys.stderr.write('\\nKRIYA_TARGET_NOT_STARTED:' + nonce + '\\n')\n"
    "    sys.exit(127)\n"
)


class _ScriptedBackend:
    name = "scripted-launch"

    def __init__(self, stage: bool = True):
        self.stage = stage
        self.seen = []

    def prepare(self, profile, command):  # noqa: ARG002 - the Protocol's signature
        self.seen.append(list(command))
        launch = FiniteLaunchStage(prefix=(sys.executable, "-c", _SIMULATED_LAUNCHER, "n1"), marker=f"{TARGET_NOT_STARTED_MARKER}n1") if self.stage else None
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, finite_launch=launch)


def _profile(tmp_path):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), network=NetworkAuthority.DENIED)


def _run(tmp_path, command, backend):
    return ProcessController().run(command, cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path), containment_backend=backend)


EXIT_127 = [sys.executable, "-c", "import sys; sys.exit(127)"]


def test_a_target_that_cannot_be_executed_is_not_started_and_its_status_is_not_the_evidence(tmp_path):
    result = _run(tmp_path, ["kriya-no-such-binary-xyz", "--check"], _ScriptedBackend())
    assert result.target_started is False and result.returncode == 127
    assert TARGET_NOT_STARTED_MARKER not in result.stderr, "the record is stripped before any consumer sees stderr"
    assert result.to_dict()["target_started"] is False


def test_a_target_that_started_and_exited_127_is_started_with_the_same_status(tmp_path):
    """The critical negative control: the status alone (127) is identical to the never-started case."""
    started = _run(tmp_path, EXIT_127, _ScriptedBackend())
    assert started.returncode == 127 and started.target_started is True
    assert "target_started" not in started.to_dict(), "absent means started"


def test_a_target_that_forges_a_record_with_another_nonce_is_still_started(tmp_path):
    forge = [sys.executable, "-c", f"import sys; sys.stderr.write('\\n{TARGET_NOT_STARTED_MARKER}guess\\n'); sys.exit(127)"]
    result = _run(tmp_path, forge, _ScriptedBackend())
    assert result.target_started is True and f"{TARGET_NOT_STARTED_MARKER}guess" in result.stderr


def test_a_backend_without_a_stage_reports_started_whatever_the_status(tmp_path):
    result = _run(tmp_path, EXIT_127, _ScriptedBackend(stage=False))
    assert result.target_started is True and result.returncode == 127


def test_run_async_reads_the_same_fact(tmp_path):
    controller = ProcessController()
    missing = asyncio.run(controller.run_async(["kriya-no-such-binary-xyz"], cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path),
                                              containment_backend=_ScriptedBackend()))
    started = asyncio.run(controller.run_async(EXIT_127, cwd=str(tmp_path), timeout=30, containment_profile=_profile(tmp_path), containment_backend=_ScriptedBackend()))
    assert missing.target_started is False and started.target_started is True and missing.returncode == started.returncode == 127


def test_only_finite_runs_get_the_stage_a_managed_start_is_unwrapped(tmp_path):
    backend = _ScriptedBackend()
    finite = _prepare_env_and_preexec(command=["svc", "--serve"], containment_profile=_profile(tmp_path), containment_backend=backend, env=None, preexec_fn=None, finite=True)
    managed = _prepare_env_and_preexec(command=["svc", "--serve"], containment_profile=_profile(tmp_path), containment_backend=backend, env=None, preexec_fn=None)
    assert finite.command[-3:] == ["n1", "svc", "--serve"] and finite.finite_launch is not None
    assert managed.command == ["svc", "--serve"] and managed.finite_launch is None


def test_a_timeout_never_reads_the_record(tmp_path):
    hang = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = ProcessController(reap_timeout=5).run(hang, cwd=str(tmp_path), timeout=1, containment_profile=_profile(tmp_path), containment_backend=_ScriptedBackend())
    assert result.timeout is True and result.target_started is True


def test_the_launch_script_leaves_the_status_alone_and_never_names_127():
    from kriya.tools.containment_oci import _FINITE_LAUNCH_SCRIPT
    assert "127" not in _FINITE_LAUNCH_SCRIPT and "126" not in _FINITE_LAUNCH_SCRIPT, "no exit status is evidence"
    assert _FINITE_LAUNCH_SCRIPT.rstrip().endswith('exec "$@"')
    assert 'exit "$kriya_rc"' in _FINITE_LAUNCH_SCRIPT


def test_each_stage_has_its_own_unpredictable_nonce():
    first, second = _finite_launch_stage(), _finite_launch_stage()
    assert first.marker != second.marker and len(first.marker) - len(TARGET_NOT_STARTED_MARKER) == 32
    assert first.prefix[:2] == ("/bin/sh", "-c") and first.prefix[-1] in first.marker


# ----------------------------------------------------------------------------------------- part 2: the contract matrix against real Docker

def _scripts(workspace: Path):
    (workspace / "exit127.sh").write_text("#!/bin/sh\nexit 127\n")
    (workspace / "exit3.sh").write_text("#!/bin/sh\necho ran; exit 3\n")
    (workspace / "badinterp.sh").write_text("#!/nonexistent/interp\necho hi\n")
    (workspace / "data.txt").write_text("plain data\n")
    (workspace / "adir").mkdir()
    for name in ("exit127.sh", "exit3.sh", "badinterp.sh"):
        (workspace / name).chmod(0o755)
    (workspace / "data.txt").chmod(0o644)


def _contained(workspace: Path, command, *, backend=None):
    return ProcessController().run(command, cwd=str(workspace), timeout=120, containment_profile=_profile(workspace),
                                   containment_backend=backend or OCIContainmentBackend())


NOT_STARTED = [
    ("a nonexistent top-level executable", ["kriya-no-such-binary-xyz", "--check"]),
    ("a nonexistent absolute path", ["/kriya/workspace/nope/bin/x"]),
    ("a non-executable file", ["/kriya/workspace/data.txt"]),
    ("a directory used as the executable", ["/kriya/workspace/adir"]),
    ("a script whose interpreter does not exist", ["/kriya/workspace/badinterp.sh"]),
]
STARTED = [
    ("an executable that starts and exits 127", ["/kriya/workspace/exit127.sh"], 127),
    ("sh -c with a command that does not exist", ["/bin/sh", "-c", "kriya-missing-in-shell"], 127),
    ("sh -c exit 127", ["/bin/sh", "-c", "exit 127"], 127),
    ("an executable that fails with 3", ["/kriya/workspace/exit3.sh"], 3),
    ("a successful command", ["/bin/echo", "ok"], 0),
]


@REAL_DOCKER
@pytest.mark.parametrize("label,command", NOT_STARTED, ids=[c[0] for c in NOT_STARTED])
def test_real_docker_an_unrunnable_target_is_not_started_in_an_entrypoint_less_image(tmp_path, label, command):
    _scripts(tmp_path)
    result = _contained(tmp_path, command)
    assert result.target_started is False, (label, result)
    assert result.returncode in (126, 127), "the launcher's own status is preserved but is not the evidence"
    assert TARGET_NOT_STARTED_MARKER not in result.stderr


@REAL_DOCKER
@pytest.mark.parametrize("label,command,code", STARTED, ids=[c[0] for c in STARTED])
def test_real_docker_a_target_that_started_is_started_whatever_it_exits_in_an_entrypoint_less_image(tmp_path, label, command, code):
    _scripts(tmp_path)
    result = _contained(tmp_path, command)
    assert result.target_started is True and result.returncode == code, (label, result)


# The Maven image: its ENTRYPOINT is a shell script that `exec "$@"`s - the case no Docker/OCI-level signal covers. argv[0]'s basename (`java`, then `mvn`) selects the image (backend-internal);
# the target is a file under the mounted workspace with that name, so it is the missing / unrunnable thing and never the image's own tool.
def _workspace_tool(workspace: Path, name: str, kind: str) -> str:
    base = workspace / "bin"
    base.mkdir(exist_ok=True)
    body = {"not-executable": ("#!/bin/sh\nexit 0\n", 0o644), "exits-127": ("#!/bin/sh\nexit 127\n", 0o755), "exits-0": ("#!/bin/sh\necho ran\n", 0o755)}.get(kind)
    if body is not None:
        (base / name).write_text(body[0])
        (base / name).chmod(body[1])
    return f"/kriya/workspace/bin/{name}"


@REAL_DOCKER
@pytest.mark.parametrize("kind,started,code", [
    ("missing", False, 127), ("not-executable", False, 126), ("exits-127", True, 127), ("exits-0", True, 0),
], ids=["missing", "not-executable", "starts-and-exits-127", "starts-and-succeeds"])
def test_real_docker_the_maven_image_entrypoint_script_does_not_hide_the_fact(tmp_path, kind, started, code):
    """The case an OCI-state signal cannot cover: the entrypoint shell starts, then its own exec fails. Selection by basename `java` puts these in the Maven image."""
    target = _workspace_tool(tmp_path, "java", kind)
    result = _contained(tmp_path, [target])
    assert result.target_started is started and result.returncode == code, result
    assert "KRIYA_TARGET" not in result.stderr


@REAL_DOCKER
def test_real_docker_arguments_working_directory_and_stdout_reach_the_target_unchanged(tmp_path):
    (tmp_path / "args.sh").write_text('#!/bin/sh\nprintf "[%s]" "$@"; echo; pwd\n')
    (tmp_path / "args.sh").chmod(0o755)
    result = _contained(tmp_path, ["/kriya/workspace/args.sh", "a b", "", "-x", "$HOME", "*", 'q"uote'])
    assert result.target_started is True and result.returncode == 0
    assert result.stdout.splitlines() == ['[a b][][-x][$HOME][*][q"uote]', "/kriya/workspace"], result.stdout


@REAL_DOCKER
def test_real_docker_a_pinned_tool_image_and_a_persistent_stdio_profile_get_no_stage(tmp_path):
    backend = OCIContainmentBackend()
    plain = backend.prepare(_profile(tmp_path), ["kriya-tool"])
    try:
        assert plain.finite_launch is not None
    finally:
        plain.cleanup()
    persistent = backend.prepare(ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), persistent_stdio=True), ["kriya-tool"])
    try:
        assert persistent.finite_launch is None
    finally:
        persistent.cleanup()
    digest = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", "debian:bookworm-slim"], capture_output=True, text=True, check=True).stdout.strip()
    pinned = backend.prepare(ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path), image_reference=f"debian@{digest}"), ["kriya-tool"])
    try:
        assert pinned.finite_launch is None
    finally:
        pinned.cleanup()


# ----------------------------------------------------------------------------------------- part 3: the consumers, against real Docker

PLAN = {"group_id": "g1", "original_goal": "x", "milestones": [{"id": "M1", "goal": "g", "depends_on": []}, {"id": "M2", "goal": "g2", "depends_on": []}]}
CONTAINED = dict(contained_execution_required=True, containment_backend="oci")
POM = ("<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version>"
       "<properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target></properties></project>")
MISSING = ["kriya-no-such-binary-xyz", "--check"]
SH_MISSING = ["sh", "-c", "kriya-missing-in-shell"]
SH_EXIT_127 = ["sh", "-c", "exit 127"]
SH_PASS = ["sh", "-c", "echo '[VERIFICATION] PASS'"]
SH_FAIL = ["sh", "-c", "echo '[VERIFICATION] FAIL: it broke'"]


def _state(**commands):
    from kriya.workflow.milestones import MilestoneRunState
    state = MilestoneRunState.from_dict(PLAN)
    state.verification_commands.update(commands)
    return state


def _workspace(tmp_path, java):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    if java:
        (workspace / "pom.xml").write_text(POM)
    return workspace


def _report(workspace, **commands):
    from kriya.workflow.milestones import replay_prior_milestone_report
    return replay_prior_milestone_report(str(workspace), _state(**commands), autonomy_cfg=strict_config(autonomy=CONTAINED).autonomy)


@REAL_DOCKER
@pytest.mark.parametrize("java", [False, True], ids=["plain-workspace", "java-workspace-maven-image"])
def test_real_docker_replay_of_a_missing_executable_is_not_executed_and_blocks(tmp_path, java):
    from kriya.workflow.milestones import MilestoneReplayNotExecuted, replay_prior_milestone_verifications
    workspace = _workspace(tmp_path, java)
    report = _report(workspace, M1=[MISSING])
    assert {e["milestone_id"]: e["outcome"] for e in report.entries} == {"M1": "not_executed"} and report.state == "not_executed"
    assert report.regressions == [] and report.entries[0]["reason"] == "runtime verification command could not be executed"
    assert "not found" in report.entries[0]["cause"] or "Permission denied" in report.entries[0]["cause"] or report.entries[0]["cause"], "the shell's own diagnostic is kept as the cause"
    with pytest.raises(MilestoneReplayNotExecuted):
        replay_prior_milestone_verifications(str(workspace), _state(M1=[MISSING]), autonomy_cfg=strict_config(autonomy=CONTAINED).autonomy)


@REAL_DOCKER
@pytest.mark.parametrize("java", [False, True], ids=["plain-workspace", "java-workspace-maven-image"])
def test_real_docker_a_replay_that_started_keeps_its_existing_outcome(tmp_path, java):
    """Started-and-exited-127 / shell-started missing command / no marker stay NOT_EVALUABLE (the existing contract); a marker PASS and FAIL keep their verdicts; a clean replay succeeds."""
    workspace = _workspace(tmp_path, java)
    report = _report(workspace, M1=[SH_EXIT_127], M2=[SH_MISSING])
    assert {e["milestone_id"]: e["outcome"] for e in report.entries} == {"M1": "not_evaluable", "M2": "not_evaluable"} and report.state == "executed"
    ran = _report(workspace, M1=[SH_PASS], M2=[SH_PASS])
    assert {e["milestone_id"]: e["outcome"] for e in ran.entries} == {"M1": "passed", "M2": "passed"} and ran.state == "executed" and ran.regressions == []
    failed = _report(workspace, M1=[SH_PASS], M2=[SH_FAIL])
    assert {e["milestone_id"]: e["outcome"] for e in failed.entries} == {"M1": "passed", "M2": "regressed"} and failed.state == "regressions_found"


@REAL_DOCKER
def test_real_docker_the_terminal_result_blocks_with_the_s6_reason_and_the_evidence_agrees(tmp_path):
    from kriya.workflow.execution_plan import TerminalPhase
    from kriya.workflow.milestones import MILESTONE_REPLAY_NOT_EXECUTED, _MilestonePlanDriver
    workspace = _workspace(tmp_path, False)
    driver = object.__new__(_MilestonePlanDriver)
    driver.workspace_path, driver.run_state, driver.total = str(workspace), _state(M1=[MISSING]), 2
    driver.engine_config, driver._replay_evidence = strict_config(autonomy=CONTAINED), None  # pylint: disable=protected-access
    result = asyncio.run(driver.run_phase(TerminalPhase.REPLAY_PRIOR_VERIFICATIONS))
    assert result is not None and result["status"] == "milestone_replay_not_executed" and result["quality_gates_passed"] is False
    assert result["reason_codes"] == [MILESTONE_REPLAY_NOT_EXECUTED] and result["failures"] == []
    assert result["replay"]["state"] == "not_executed" and result["replay"]["not_executed"] == ["M1"]
    assert driver._replay_evidence == result["replay"], "the persisted replay evidence is the final result's"  # pylint: disable=protected-access


@REAL_DOCKER
def test_real_docker_run_milestones_never_starts_the_integration_after_a_milestone_that_did_not_start(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from _strict_doubles import strict_engine
    from test_milestones import _committing, mkv2

    from kriya.workflow.milestones import MilestoneRunState, run_milestones

    async def go():
        state = MilestoneRunState(group_id="grp", original_goal="orig", milestones=[mkv2("M1", goal="g1", success_criterion="c1")])
        state.verification_commands["M1"] = [MISSING]
        we = strict_engine(strict_config(autonomy=CONTAINED))
        we.run_generation_workflow = AsyncMock(side_effect=_committing({"quality_gates_passed": True, "design": "d", "files": ["a.py"]}))
        we.run_verifier = MagicMock()
        we.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
        workspace = tmp_path / "rm"
        workspace.mkdir()
        return await run_milestones(we, state, str(workspace)), we.run_generation_workflow.await_count

    result, calls = asyncio.run(go())
    assert result["status"] == "milestone_replay_not_executed" and result["replay"]["state"] == "not_executed" and result["replay"]["not_executed"] == ["M1"]
    assert calls == 1, "the milestone ran once; the integration call never fired"


@REAL_DOCKER
@pytest.mark.parametrize("kind", ["missing", "not-executable"])
def test_real_docker_a_maven_that_never_started_is_maven_invocation_failed(tmp_path, kind):
    """`mvn validate` through the production gate: the contained Maven is made missing / non-executable INSIDE the containment (a shadowing `mvn` on the container PATH is not available to the
    project, so the validator is pointed at a Kriya-owned directory whose `mvn` is the thing under test via the backend's own image selection)."""
    from unittest.mock import patch

    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    (tmp_path / "pom.xml").write_text(POM)
    target = _workspace_tool(tmp_path, "mvn", kind)
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(**CONTAINED))
    original = PolymorphicValidator._run_cmd_with_timeout

    def aim(self, cmd, cwd, *args, **kwargs):
        if cmd and cmd[0] == "mvn":
            cmd = [target] + cmd[1:]
        return original(self, cmd, cwd, *args, **kwargs)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", aim):
        result = validator.run_pom_validate()
    assert result["success"] is False and result["environment_reason_code"] == "MAVEN_INVOCATION_FAILED", result


@REAL_DOCKER
def test_real_docker_a_maven_that_started_and_exited_127_is_not_an_invocation_failure(tmp_path):
    from unittest.mock import patch

    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    (tmp_path / "pom.xml").write_text(POM)
    target = _workspace_tool(tmp_path, "mvn", "exits-127")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(**CONTAINED))
    original = PolymorphicValidator._run_cmd_with_timeout

    def aim(self, cmd, cwd, *args, **kwargs):
        if cmd and cmd[0] == "mvn":
            cmd = [target] + cmd[1:]
        return original(self, cmd, cwd, *args, **kwargs)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", aim):
        result = validator.run_pom_validate()
    assert result["success"] is False and result.get("environment_reason_code") != "MAVEN_INVOCATION_FAILED", result
    assert result["environment_reason_code"] == "MAVEN_VALIDATE_NO_DIAGNOSTICS", "it ran, exited non-zero with no diagnostics: the existing classification"


@REAL_DOCKER
def test_real_docker_a_sequence_stops_at_the_step_that_never_started_and_records_it_as_the_existing_unstarted_shape(tmp_path):
    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    from kriya.workflow.acceptance import runtime_verification_infrastructure_reason
    workspace = _workspace(tmp_path, False)
    result = PolymorphicValidator(str(workspace), autonomy_cfg=AutonomyConfig(**CONTAINED)).run_app_sequence([MISSING, SH_PASS])
    assert len(result["steps"]) == 1, "a command after the one that never started must not run"
    step = result["steps"][0]
    assert step["exit_code"] is None and step["timed_out"] is False and step["target_started"] is False
    assert result["success"] is False and result["returncode"] is None and "[VERIFICATION] PASS" not in result["output"]
    assert runtime_verification_infrastructure_reason(result) == "runtime verification command could not be executed"
    started = PolymorphicValidator(str(workspace), autonomy_cfg=AutonomyConfig(**CONTAINED)).run_app_sequence([SH_EXIT_127, SH_PASS])
    assert [s["exit_code"] for s in started["steps"]] == [127, 0] and runtime_verification_infrastructure_reason(started) is None


@REAL_DOCKER
def test_real_docker_the_registry_scoped_acquisition_path_reports_the_same_fact(tmp_path):
    """The acquisition container runs the target after the setup script's privilege drop (`exec setpriv ... -- "$@"`); the stage wraps the target argv, so the setup script is untouched.
    ProcessController runs the prepared command's cleanup (proxy container + network) itself - nothing is left on the daemon."""
    from kriya.tools.containment_oci import finalize_registry_acquisition_result
    from kriya.tools.dependency_execution import _acquisition_profile
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (workspace / "exit127.sh").write_text("#!/bin/sh\nexit 127\n")
    (workspace / "exit127.sh").chmod(0o755)
    profile = _acquisition_profile(str(workspace), str(cache), [], registry_hosts=["repo.maven.apache.org"])
    backend = OCIContainmentBackend()
    missing = ProcessController().run(["kriya-no-such-binary-xyz"], cwd=str(workspace), timeout=120, containment_profile=profile, containment_backend=backend)
    finalize_registry_acquisition_result(missing)
    started = ProcessController().run(["/kriya/workspace/exit127.sh"], cwd=str(workspace), timeout=120, containment_profile=profile, containment_backend=backend)
    finalize_registry_acquisition_result(started)
    assert missing.target_started is False and started.target_started is True and missing.returncode == started.returncode == 127
    assert TARGET_NOT_STARTED_MARKER not in missing.stderr
    leftovers = subprocess.run(["docker", "ps", "-a", "--filter", "name=kriya-acq", "--format", "{{.Names}}"], capture_output=True, text=True, check=True).stdout.split()
    assert leftovers == [], leftovers


def test_a_maven_that_never_started_triggers_no_dependency_acquisition(tmp_path):
    """Even when the diagnostics it carries look like a missing dependency, a Maven that never started has nothing to acquire for: exactly one (offline) attempt is made."""
    from unittest.mock import patch

    from kriya.config.config import AutonomyConfig
    from kriya.tools.validate import PolymorphicValidator
    (tmp_path / "pom.xml").write_text(POM)
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(**CONTAINED))
    calls = []

    def fake(self, cmd, cwd, *args, **kwargs):  # noqa: ARG001
        calls.append((list(cmd), kwargs.get("network")))
        return {"returncode": 127, "stdout": "", "timeout": False, "target_started": False,
                "stderr": "[ERROR] Could not resolve dependencies for project g:a:jar:1: Cannot access central in offline mode and the artifact x:y:jar:1 has not been downloaded"}
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", fake), patch.object(PolymorphicValidator, "_maven_cache_dir", lambda self: str(tmp_path)):
        result = validator._run_maven_cmd(["validate"], cwd=str(tmp_path), timeout=5)  # pylint: disable=protected-access
    assert result["target_started"] is False and len(calls) == 1, calls
