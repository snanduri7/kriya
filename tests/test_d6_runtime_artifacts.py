"""D6 (KNOW B on demo-runtime-4, 2026-10-01, run 20261001T175410-22b89b3e):
application runtime state is not a candidate mutation.

Live: the skill-built Ignite application ran correctly ([VERIFICATION] PASS,
exit 0), but Ignite created its default work directory (ignite/README.txt) in
the working directory, and the post-gate integrity audit stopped the run
(VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE) and offered the file to
recovery. Now, for the application-runtime gate only, untracked files the
application creates are recorded (runtime_artifacts) and discarded; a change
to any tracked or candidate path is still the VerificationTreeMutated stop,
and every other gate keeps the exact-toolchain-output rule."""
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from _strict_doubles import developer_double
from test_workflow import _runtime_verifier_ctx

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator, _verification_gate, execution_evidence
from kriya.workflow.attempt import run_attempt
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_integrity import (
    VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE,
    VERIFICATION_GATE_MUTATED_TRACKED_FILES,
    VerificationGateCreatedFiles,
    VerificationTreeBinding,
    VerificationTreeMutated,
)
from kriya.workflow.state import GenerationState

POM, SOURCE, CANDIDATE = "pom.xml", "src/main/java/demo/App.java", "src/main/resources/app.properties"


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)


def _workspace(tmp_path):
    """A repository with tracked POM and source, and an untracked candidate file."""
    ws = tmp_path / "ws"
    (ws / "src/main/java/demo").mkdir(parents=True)
    (ws / "src/main/resources").mkdir(parents=True)
    (ws / POM).write_text("<project/>\n")
    (ws / SOURCE).write_text("class App {}\n")
    _git(ws, "init", "-q")
    _git(ws, "add", POM, SOURCE)
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    (ws / CANDIDATE).write_text("greeting=hello\n")
    validator = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    validator.tree_binding = VerificationTreeBinding(str(ws), [POM, SOURCE], [CANDIDATE])
    return ws, validator


def _python(code):
    return [sys.executable, "-c", code]


def _writes(path, text="x"):
    return f"import os; os.makedirs(os.path.dirname({path!r}) or '.', exist_ok=True); open({path!r}, 'w').write({text!r})"


# ---------------------------------------------------------------- the runtime gate: record and discard

@pytest.mark.parametrize(("artifact", "new_directory"), [
    ("work/runtime.dat", "work"),  # 1: an ordinary work file
    ("ignite/README.txt", "ignite"),  # 10: the live Ignite shape, no Ignite rule
    ("src/main/java/Evil.java", None),  # 4: a source-looking path is still only runtime state
    ("app.db", None),
])
def test_an_application_created_file_is_recorded_and_discarded(tmp_path, artifact, new_directory):
    ws, validator = _workspace(tmp_path)
    result = validator.run_app(_python(_writes(artifact) + "; print('[VERIFICATION] PASS')"))
    assert result["success"] is True and "[VERIFICATION] PASS" in result["output"]
    assert result["runtime_artifacts"] == [artifact]
    assert execution_evidence(result)["runtime_artifacts"] == [artifact]  # persisted with the gate outcome
    assert not (ws / artifact).exists()  # 2: discarded
    if new_directory:
        assert not (ws / new_directory).exists()  # the directory it created goes with it
    assert (ws / "src/main/java/demo").is_dir()  # pre-existing directories are untouched
    validator.tree_binding.check("terminal", "before")  # 3: the bound tree is exactly what is committed
    assert validator.tree_binding.created() == []


def test_a_run_that_creates_nothing_records_nothing(tmp_path):
    _ws, validator = _workspace(tmp_path)
    assert validator.run_app(_python("print('ok')"))["runtime_artifacts"] == []


def test_a_sequence_keeps_state_between_its_steps_then_discards_it(tmp_path):
    ws, validator = _workspace(tmp_path)
    result = validator.run_app_sequence([
        _python(_writes("data/store.txt", "item")),
        _python("print(open('data/store.txt').read())"),
    ])
    assert result["success"] is True and "item" in result["output"]
    assert result["runtime_artifacts"] == ["data/store.txt"] and not (ws / "data").exists()


@pytest.mark.parametrize(("code", "path"), [
    ("open('pom.xml', 'a').write('<!-- changed -->')", POM),  # 5
    ("open('src/main/java/demo/App.java', 'a').write('// changed')", SOURCE),  # 6
    ("import os; os.remove('src/main/java/demo/App.java')", SOURCE),  # 7
    ("open('src/main/resources/app.properties', 'w').write('greeting=bye')", CANDIDATE),
])
def test_a_bound_path_change_is_still_a_hard_stop(tmp_path, code, path):
    _ws, validator = _workspace(tmp_path)
    with pytest.raises(VerificationTreeMutated) as raised:
        validator.run_app(_python(code))
    assert raised.value.failure.diagnostics["reason_code"] == VERIFICATION_GATE_MUTATED_TRACKED_FILES
    assert raised.value.failure.diagnostics["changed_paths"] == [path]


def test_creating_a_candidate_path_that_did_not_exist_yet_is_a_hard_stop(tmp_path):
    ws, validator = _workspace(tmp_path)
    (ws / CANDIDATE).unlink()
    validator.tree_binding = VerificationTreeBinding(str(ws), [POM, SOURCE], [CANDIDATE])
    with pytest.raises(VerificationTreeMutated):
        validator.run_app(_python(_writes(CANDIDATE)))


def test_a_runtime_file_that_cannot_be_discarded_fails_closed(tmp_path):
    ws, validator = _workspace(tmp_path)
    (ws / "locked").mkdir()
    (ws / "locked/keep").write_text("pre-existing ignored? no: tracked")
    _git(ws, "add", "locked/keep")
    _git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "locked")
    validator.tree_binding = VerificationTreeBinding(str(ws), [POM, SOURCE, "locked/keep"], [CANDIDATE])
    try:
        result = None
        with pytest.raises(VerificationGateCreatedFiles):
            result = validator.run_app(_python(_writes("locked/new.dat") + "; import os; os.chmod('locked', 0o500)"))
        assert result is None
    finally:
        (ws / "locked").chmod(0o700)


def test_a_failing_runtime_gate_still_discards_its_artifacts(tmp_path):
    ws, validator = _workspace(tmp_path)

    class Probe(PolymorphicValidator):
        @_verification_gate("runtime_verification")
        def crash(self):
            (ws / "logs").mkdir()
            (ws / "logs/app.log").write_text("boom")
            raise RuntimeError("the application crashed")

    probe = Probe(str(ws), autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    probe.tree_binding = validator.tree_binding
    with pytest.raises(RuntimeError, match="crashed"):
        probe.crash()
    assert not (ws / "logs").exists()


# ---------------------------------------------------------------- 8: every other gate keeps the exact rule

@pytest.mark.parametrize("gate", ["compile", "tests", "pom_validate", "classpath_inspection"])
def test_other_gates_still_refuse_the_same_untracked_file(tmp_path, gate):
    ws, validator = _workspace(tmp_path)

    class Probe(PolymorphicValidator):
        @_verification_gate(gate)
        def make(self):
            (ws / "work").mkdir()
            (ws / "work/runtime.dat").write_text("x")
            return {"success": True}

    probe = Probe(str(ws), autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    probe.tree_binding = validator.tree_binding
    with pytest.raises(VerificationGateCreatedFiles) as raised:
        probe.make()
    assert raised.value.failure.diagnostics["reason_code"] == VERIFICATION_GATE_CREATED_UNAUTHORIZED_FILE
    assert (ws / "work/runtime.dat").exists()  # never silently removed outside the runtime gate


def test_between_gates_a_new_file_is_still_refused(tmp_path):
    ws, validator = _workspace(tmp_path)
    (ws / "stray.txt").write_text("x")
    with pytest.raises(VerificationGateCreatedFiles):
        validator.run_app(_python("print('ok')"))  # the runtime gate's own "before" check


# ---------------------------------------------------------------- 9: through the attempt, never a recovery target

def _ctx(tmp_path, code, passed):
    developer = developer_double()
    developer.run_generation = AsyncMock(side_effect=AssertionError("verification-only: no Developer"))
    run_verifier = AsyncMock()
    run_verifier.judge = AsyncMock(return_value={"should_run": True, "run_commands": [_python(code)],
                                                 "command_source": "inferred", "success_criteria": "prints PASS"})
    run_verifier.grade = AsyncMock(return_value={"passed": passed, "reasoning": "graded", "likely_files": []})
    return _runtime_verifier_ctx(tmp_path, developer=developer, run_verifier=run_verifier,
                                 established_files=[POM, SOURCE])


def _attempt_workspace(tmp_path):
    (tmp_path / "src/main/java/demo").mkdir(parents=True)
    (tmp_path / POM).write_text("<project/>\n")
    (tmp_path / SOURCE).write_text("class App {}\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", POM, SOURCE)
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")


@pytest.mark.asyncio
async def test_a_passing_application_with_runtime_state_passes_the_attempt(tmp_path):
    _attempt_workspace(tmp_path)
    state = GenerationState()
    ctx = _ctx(tmp_path, _writes("ignite/README.txt") + "; print('Retrieved value from cache: Hello')", passed=True)
    await run_attempt(state, ctx)
    outcome = next(o for o in state.gate_outcomes if o.get("type") == "run_verification")
    assert outcome["success"] is True and outcome["runtime_artifacts"] == ["ignite/README.txt"]
    assert not (tmp_path / "ignite").exists()
    assert "ignite/README.txt" not in state.all_files_written


@pytest.mark.asyncio
async def test_a_failing_application_never_offers_its_runtime_state_to_recovery(tmp_path):
    _attempt_workspace(tmp_path)
    state = GenerationState()
    ctx = _ctx(tmp_path, _writes("ignite/README.txt") + "; print('cache returned nothing'); raise SystemExit(1)",
               passed=False)
    with pytest.raises(QualityGateFailure) as raised:
        await run_attempt(state, ctx)
    failure = raised.value.failure
    assert failure.type != "verification_tree_mutated"
    assert "ignite/README.txt" not in failure.likely_files
    assert not (tmp_path / "ignite").exists()


# ---------------------------------------------------------------- 10: the real Ignite application (KNOW B's candidate)

_IGNITE = Path.home() / ".m2/repository/org/apache/ignite/ignite-spring/2.18.0"
needs_ignite = pytest.mark.skipif(shutil.which("mvn") is None or not _IGNITE.exists(),
                                  reason="real Maven with Ignite 2.18 in the local repository is not available")


@needs_ignite
def test_the_real_ignite_application_passes_and_its_work_directory_is_discarded(tmp_path):
    ws = tmp_path / "ws"
    shutil.copytree(Path(__file__).parent / "fixtures" / "d6_knowb_candidate", ws)
    _git(ws, "init", "-q")
    candidate = [POM, "src/main/resources/ignite-config.xml", "src/main/java/com/example/IgniteDemoApp.java"]
    validator = PolymorphicValidator(str(ws), autonomy_cfg=AutonomyConfig(contained_execution_required=False))
    validator.tree_binding = VerificationTreeBinding(str(ws), [], candidate)
    result = validator.run_app_sequence(
        [["mvn", "-e", "-q", "compile", "exec:exec", "-Dexec.mainClass=com.example.IgniteDemoApp"]])
    assert result["success"] is True
    assert "Ignite node started OK" in result["output"] and "[VERIFICATION] PASS" in result["output"]
    assert result["runtime_artifacts"] == ["ignite/README.txt"] and not (ws / "ignite/README.txt").exists()
