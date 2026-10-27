"""D3 (KNOW-A 2026-10-01, run 20261001T141456-fe3a1c9d): runtime verification
establishes its own prerequisites from the CURRENT source.

Measured live and reproduced model-free (evidence/demo-defects-d3-d2b): work
units 1-3 compiled the app; the next work unit's sandbox reset
(`git checkout -f` + `git clean -fd`) deleted the untracked target/; the
verification-only work unit ran the model's `mvn -e exec:java` (no compile)
and failed ClassNotFoundException -> VERIFICATION_INFRASTRUCTURE_FAILURE.
With target/ git-ignored the same run passed - on the previous work unit's
classes. The runtime gate now rebuilds through the existing compile gate
before any command that consumes the compiled classes.

Real Maven (host mode), no model. The POM carries a test-scoped dependency so
the verification-only path keeps the model's Maven command (a POM without
dependencies is grounded to javac/java instead) - and neither compile nor
exec:java needs that test dependency resolved."""
import os
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest
from _real_oci import REAL_OCI_MAVEN
from _strict_doubles import developer_double
from test_workflow import _runtime_verifier_ctx

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.acceptance import RUNTIME_PREREQUISITE_BUILD_FAILED, runtime_verification_infrastructure_reason
from kriya.workflow.attempt import run_attempt
from kriya.workflow.state import GenerationState
from kriya.workflow.worktree import create_git_worktree

# S7: a real Maven build belongs to the REAL-OCI tier (owner decision): it needs the canonical provider image and cache, which no stand-in simulates.
pytestmark = REAL_OCI_MAVEN

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>demo</groupId><artifactId>d3</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release>
<project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
<dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version>
<scope>test</scope></dependency></dependencies></project>
"""
SOURCE = "src/main/java/demo/App.java"
CLASS = "target/classes/demo/App.class"
RUN = ["mvn", "-q", "exec:java", "-Dexec.mainClass=demo.App"]  # the model's command: no compile phase


def _app(message):
    return f'package demo;\npublic class App {{ public static void main(String[] a) {{ System.out.println("{message}"); }} }}\n'


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(ws, message):
    (ws / SOURCE).write_text(_app(message))
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.email=d@x", "-c", "user.name=d", "commit", "-qm", message)


def _workspace(tmp_path, ignored):
    ws = tmp_path / "workspace"
    (ws / "src/main/java/demo").mkdir(parents=True)
    (ws / "pom.xml").write_text(POM)
    if ignored:
        (ws / ".gitignore").write_text("target/\n")
    _git(ws, "init", "-q")
    _commit(ws, "MSG_V1")
    return ws


def _validator(path):
    return PolymorphicValidator(str(path), autonomy_cfg=AutonomyConfig())


def _warm_cache(path):
    """The workflow's earlier TEST gate. The contained build resolves the project's dependencies into its managed cache there, and runtime verification (D2B: its acquisition
    resolves only the plugins the runtime command names, never the project's dependencies) relies on that - the host `~/.m2` that used to hold junit is not the contained cache."""
    result = _validator(path).run_tests()
    assert result["success"], result["output"]


def _compile_in_new_work_unit(ws):
    """An earlier work unit's compile leaves V1's classes in the sandbox. OI-142: the compile GATE now runs in its own view and leaves nothing, so the residue
    this scenario is about (build output anything earlier left behind, which the reset keeps or deletes by Git semantics) is planted the way any earlier run
    of a build tool would have left it; the gate itself must still pass."""
    sandbox = create_git_worktree(str(ws))
    assert _validator(sandbox).run_compile_check([SOURCE])["success"]
    assert not os.path.exists(os.path.join(sandbox, CLASS))  # the gate left no output in the shared sandbox
    _warm_cache(sandbox)
    subprocess.run(["javac", "-d", os.path.join(sandbox, "target", "classes"), os.path.join(sandbox, SOURCE)], check=True, capture_output=True)
    assert os.path.exists(os.path.join(sandbox, CLASS))
    return sandbox


@pytest.mark.parametrize("ignored", [False, True], ids=["target-untracked", "target-ignored"])
def test_runtime_verification_rebuilds_the_current_source_after_the_work_unit_reset(tmp_path, ignored):
    ws = _workspace(tmp_path, ignored)
    sandbox = _compile_in_new_work_unit(ws)
    assert create_git_worktree(str(ws)) == sandbox  # the next work unit's reset of the same sandbox
    # Git semantics, unchanged: `git clean -fd` deletes an untracked target/, keeps an ignored one.
    assert os.path.exists(os.path.join(sandbox, CLASS)) is ignored
    result = _validator(sandbox).run_app_sequence([RUN])
    assert result["success"], result["output"]
    assert "MSG_V1" in result["output"]
    assert result["runtime_prerequisite"] == "mvn clean compile (current source): PASSED"
    assert runtime_verification_infrastructure_reason(result) is None


def test_stale_classes_from_an_earlier_source_revision_never_reach_runtime_verification(tmp_path):
    """target/ ignored: the reset keeps the previous work unit's classes; the
    source has moved on. The run must show the current source's behavior."""
    ws = _workspace(tmp_path, ignored=True)
    sandbox = _compile_in_new_work_unit(ws)
    _commit(ws, "MSG_V2")
    create_git_worktree(str(ws))
    with open(os.path.join(sandbox, SOURCE)) as handle:
        assert "MSG_V2" in handle.read()
    assert os.path.exists(os.path.join(sandbox, CLASS))  # the stale class is really there
    result = _validator(sandbox).run_app_sequence([RUN])
    assert result["success"], result["output"]
    assert "MSG_V2" in result["output"] and "MSG_V1" not in result["output"]


def test_a_prerequisite_build_failure_stops_before_anything_runs(tmp_path):
    ws = _workspace(tmp_path, ignored=False)
    sandbox = _compile_in_new_work_unit(ws)
    create_git_worktree(str(ws))
    with open(os.path.join(sandbox, SOURCE), "w") as handle:
        handle.write("package demo;\npublic class App { not java }\n")
    validator = _validator(sandbox)
    with patch.object(validator, "_run_runtime_step", side_effect=AssertionError("ran after a failed build")) as step:
        result = validator.run_app_sequence([RUN])
    step.assert_not_called()
    assert result["success"] is False and result["prerequisite_failed"] is True
    assert result["output"].startswith("RUNTIME_PREREQUISITE_FAILED:")
    assert runtime_verification_infrastructure_reason(result).startswith(RUNTIME_PREREQUISITE_BUILD_FAILED)


def test_the_prerequisite_build_is_bounded_by_the_runs_root_deadline(tmp_path):
    ws = _workspace(tmp_path, ignored=False)
    sandbox = create_git_worktree(str(ws))
    _warm_cache(sandbox)
    validator = _validator(sandbox)
    root_deadline = time.monotonic() + 3600.0     # a REAL deadline: the contained flow enforces it (a past one would cap every step to a second and skip acquisition)
    with patch.object(validator, "_run_deadline", return_value=root_deadline), \
            patch.object(validator, "_run_maven_cmd", wraps=validator._run_maven_cmd) as maven:  # pylint: disable=protected-access
        assert validator.run_app_sequence([RUN])["success"]
    prerequisite, runtime = maven.call_args_list  # one _run_maven_cmd call each (the offline-first acquisition happens inside it)
    assert prerequisite.args[0][:2] == ["clean", "compile"] and prerequisite.kwargs["deadline"] == root_deadline
    assert runtime.args[0] == RUN[1:] and runtime.kwargs["deadline"] == root_deadline


def test_an_interpreted_python_runtime_gets_no_build_prerequisite(tmp_path):
    (tmp_path / "app.py").write_text("print('PY_RAN')\n")
    validator = _validator(tmp_path)
    with patch.object(validator, "run_compile_check", side_effect=AssertionError("no compile for Python")):
        result = validator.run_app_sequence([[sys.executable, "app.py"]])
    assert result["success"] and "PY_RAN" in result["output"] and result["runtime_prerequisite"] is None


@pytest.mark.asyncio
async def test_a_verification_only_work_unit_runs_the_current_application(tmp_path):
    """The live shape end to end: no candidate files, no target/ (the reset
    removed it), the model's Maven command without `compile`. The application
    runs and its output is graded; the Developer is never invoked."""
    (tmp_path / "src/main/java/demo").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(POM)
    (tmp_path / SOURCE).write_text(_app("D3_VERIFIED"))
    _warm_cache(tmp_path)
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={
        "should_run": True, "run_commands": [RUN], "command_source": "inferred", "success_criteria": "prints D3_VERIFIED",
    })
    run_verifier.grade = AsyncMock(return_value={"passed": True, "reasoning": "printed", "likely_files": []})
    ctx = _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier,
                                established_files=["pom.xml", SOURCE])
    await run_attempt(GenerationState(), ctx)
    assert not developer.run_generation.called
    run_verifier.grade.assert_awaited_once()
    assert "D3_VERIFIED" in str(run_verifier.grade.await_args)


def test_a_validator_built_before_the_pom_existed_still_rebuilds(tmp_path):
    """The stack is decided when the validator is built; a project that only
    later gained its pom.xml is still a Maven project at runtime."""
    (tmp_path / "requirements.txt").write_text("")  # looked like Python when the validator was built
    validator = _validator(tmp_path)
    assert validator.stack == "python"
    (tmp_path / "src/main/java/demo").mkdir(parents=True)
    (tmp_path / "pom.xml").write_text(POM)
    (tmp_path / SOURCE).write_text(_app("LATE_POM"))
    _warm_cache(tmp_path)
    result = validator.run_app_sequence([RUN])
    assert result["success"], result["output"]
    assert "LATE_POM" in result["output"]
    assert result["runtime_prerequisite"] == "mvn clean compile (current source): PASSED"
