"""EFFECTIVE ARGV AUTHORITY (owner ruling 2026-11-07; registry RUNTIME-APPROVED-ARGV-VS-EXECUTED-INTERPRETER-001).

MEASURED (7+ real desktop runs): the runtime-command approval card showed ``.../Kriya-product-72h/.venv/bin/python3.14 main.py`` (``_resolve_run_command`` swapping Kriya's own interpreter in for a bare ``python``), and
``PolymorphicValidator._substitute_python_interpreter`` then rewrote the executable to the project environment's ``.kriya/venv/bin/python`` AFTER the person approved - so what was approved was not what ran.

The invariant under test: logical command -> ONE canonical, side-effect-free effective argv (``_resolve_run_command`` -> ``kriya.tools.validate.effective_command``) -> human approval -> fingerprint -> execution of
EXACTLY that argv. After approval a mismatch is a typed refusal with nothing executed; the interpreter is never chosen or rewritten later. Every equivalence is narrow: ``python main.py`` authored by the user is the
canonical project interpreter's invocation of ``main.py`` and of nothing else.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from _validator_runner import ScriptedRunner, ok

from kriya.config.environment import PROJECT_PYTHON_INTERPRETER
from kriya.policy.approval import build_subject, commands_authored_in_goal
from kriya.tools.validate import (
    RUNTIME_VERIFICATION_GATE,
    PolymorphicValidator,
    detect_workspace_stack,
    effective_command,
)
from kriya.workflow import file_resolution, milestones, runtime_verification
from kriya.workflow.failure import QualityGateFailure

CANON = ".kriya/venv/bin/python"


@pytest.fixture
def py_workspace(tmp_path):
    (tmp_path / "main.py").write_text('print("hi")\n')
    return tmp_path


def test_the_canonical_interpreter_is_the_project_environment_relative_path():
    assert PROJECT_PYTHON_INTERPRETER == CANON


# ---- the one effective-argv function --------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("launcher", ["python", "python3", sys.executable])
def test_a_python_launcher_in_a_python_workspace_is_the_project_interpreter(py_workspace, launcher):
    assert detect_workspace_stack(str(py_workspace)) == "python"
    assert effective_command([launcher, "main.py", "--x", "1"], str(py_workspace)) == [CANON, "main.py", "--x", "1"]


@pytest.mark.parametrize("executable", ["/usr/bin/python3", "/tmp/planted/python", "./python", "python3.14", CANON + "3", ".kriya/venv/bin/pythonw", "bash", "pytest", "mvn", "node", "Python"])
def test_nothing_but_a_bare_launcher_is_canonicalised(py_workspace, executable):
    assert effective_command([executable, "main.py"], str(py_workspace)) == [executable, "main.py"]


def test_a_non_python_workspace_and_an_empty_command_are_untouched(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    assert effective_command(["python", "x.py"], str(tmp_path)) == ["python", "x.py"]
    assert effective_command([], str(tmp_path)) == []


def test_resolving_an_effective_argv_is_pure(py_workspace):
    before = sorted(os.listdir(py_workspace))
    effective_command(["python", "main.py"], str(py_workspace))
    file_resolution._resolve_run_command(["python", "main.py"], str(py_workspace))
    assert sorted(os.listdir(py_workspace)) == before and not (py_workspace / ".kriya").exists()


def test_resolve_run_command_is_the_single_seam_and_returns_the_effective_argv(py_workspace, monkeypatch):
    monkeypatch.setattr(file_resolution.shutil, "which", lambda name: None if name == "python" else "/usr/bin/" + name)   # no bare python on PATH: Kriya's own interpreter was substituted for it
    for given in (["python", "main.py"], ["python3", "main.py"], [sys.executable, "main.py"]):
        assert file_resolution._resolve_run_command(given, str(py_workspace)) == [CANON, "main.py"]
    assert file_resolution._resolve_run_command(["python", "main.py"]) == [sys.executable, "main.py"]     # no workspace to ask about: the pre-existing behaviour, unchanged


# ---- approval and fingerprint bind the effective argv ------------------------------------------------------------------------------------------------------------------------
def _subject(commands, workspace):
    return build_subject(unit_id=None, criterion="c", working_directory=str(workspace), commands=commands, input_channel="none", stdin_payload=None, reason="r")


def test_the_fingerprint_binds_the_effective_argv_and_every_difference_changes_it(py_workspace):
    approved = _subject([effective_command(["python3", "main.py"], str(py_workspace))], py_workspace)
    assert approved.commands == ((CANON, "main.py"),)
    same = _subject([file_resolution._resolve_run_command(["python", "main.py"], str(py_workspace))], py_workspace)
    assert same.fingerprint == approved.fingerprint                                    # python and python3 are ONE effective invocation
    for different in ([CANON, "other.py"], [CANON, "main.py", "--extra"], [sys.executable, "main.py"], ["/tmp/planted/python", "main.py"], [CANON + "3", "main.py"], ["bash", "-c", "main.py"]):
        assert _subject([different], py_workspace).fingerprint != approved.fingerprint, different


# ---- narrow authored equivalence ----------------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("goal", ["Run `python main.py` and check the output.", "Verify it by running python main.py when done.", "It must work: python3 main.py"])
def test_the_users_own_python_invocation_is_the_canonical_interpreters_invocation_of_the_same_script(goal):
    assert commands_authored_in_goal([[CANON, "main.py"]], goal) is True


@pytest.mark.parametrize("commands", [
    [[CANON, "other.py"]],                                   # a different script
    [[CANON, "main.py", "--extra"]],                         # an added argument
    [[CANON, "-c", "print(1)"]],                             # an interpreter given code
    [[CANON + "3", "main.py"]],                              # a different executable
    [["./python", "main.py"]], [["/tmp/planted/python", "main.py"]], [["/usr/bin/python3", "main.py"]],
    [[".kriya/venv/bin/pip", "main.py"]], [["bash", "main.py"]],
])
def test_nothing_else_is_equivalent_to_the_users_python_invocation(commands):
    assert commands_authored_in_goal(commands, "Run `python main.py` and check the output.") is False


def test_an_authored_command_is_not_equivalent_when_the_goal_forbids_it_or_names_only_the_tool():
    assert commands_authored_in_goal([[CANON, "main.py"]], "Never run python main.py in this project.") is False
    assert commands_authored_in_goal([[CANON, "main.py"]], "Use python for this app.") is False


# ---- after approval: a changed argv is a typed refusal and nothing executes ----------------------------------------------------------------------------------------------------
def _state():
    gate_outcomes = []
    return SimpleNamespace(attempt_number=1, record_gate_outcome=gate_outcomes.append, gate_outcomes=gate_outcomes)


def test_preflight_accepts_the_effective_argv_and_refuses_anything_else(py_workspace):
    ctx = SimpleNamespace(worktree_path=str(py_workspace))
    runtime_verification._assert_effective_argv(_state(), ctx, [[CANON, "main.py"], ["bash", "-c", "true"]])      # effective: identity
    for bare in ([["python", "main.py"]], [["python3", "main.py"]], [[sys.executable, "main.py"]], [[CANON, "main.py"], ["python", "x.py"]]):
        state = _state()
        with pytest.raises(QualityGateFailure) as stop:
            runtime_verification._assert_effective_argv(state, ctx, bare)
        assert "EFFECTIVE_ARGV_NOT_RESOLVED" in str(stop.value) and state.gate_outcomes


def test_the_validator_refuses_a_bare_launcher_and_a_mismatched_environment_and_runs_nothing(py_workspace, monkeypatch):
    runner = ScriptedRunner(monkeypatch, ok("noop"))
    validator = PolymorphicValidator(str(py_workspace))
    bare = validator.run_app_sequence([["python", "main.py"]])
    assert bare["success"] is False and "EFFECTIVE_ARGV_NOT_RESOLVED" in bare["output"]
    monkeypatch.setattr(PolymorphicValidator, "_resolve_python_interpreter", lambda self: ("/evil/bin/python", None))
    mismatch = PolymorphicValidator(str(py_workspace)).run_app_sequence([[CANON, "main.py"]])
    assert mismatch["success"] is False and "EFFECTIVE_ARGV_INTERPRETER_MISMATCH" in mismatch["output"]
    assert not [cmd for cmd in runner.commands if cmd[:1] in (["python"], [CANON], ["/evil/bin/python"])], runner.commands


# ---- replay uses the same effective argv, never a rewritten one ---------------------------------------------------------------------------------------------------------------
def test_a_milestone_replay_plan_is_derived_through_the_same_seam_so_the_approved_argv_is_the_executed_argv(py_workspace):
    run_state = SimpleNamespace(verification_commands={"m1": [["python3", "main.py"]], "m2": [[CANON, "main.py", "--again"]]}, replay_evidence_unavailable={})
    plan = milestones._derive_replay_plan(str(py_workspace), run_state)
    assert [item.commands for item in plan.items] == [((CANON, "main.py"),), ((CANON, "main.py", "--again"),)]
    assert plan.digest == milestones._derive_replay_plan(str(py_workspace), run_state).digest
    changed = SimpleNamespace(verification_commands={"m1": [["python3", "other.py"]], "m2": [[CANON, "main.py", "--again"]]}, replay_evidence_unavailable={})
    assert milestones._derive_replay_plan(str(py_workspace), changed).digest != plan.digest      # a changed command is a changed approval subject


# ---- the interpreter path cannot be planted or replaced to widen authority -----------------------------------------------------------------------------------------------------
def test_a_model_cannot_write_the_project_interpreter(py_workspace):
    from kriya.policy.filesystem import AuthorizedFileWriter

    writer = AuthorizedFileWriter(str(py_workspace))
    for relative in (CANON, ".kriya/venv/bin/python3", ".kriya/venv/pyvenv.cfg", ".kriya/venv/lib/site.py"):
        assert writer.authorize(os.path.join(str(py_workspace), relative)).decision == "deny", relative


def test_a_repository_cannot_smuggle_an_interpreter_into_the_bound_tree(tmp_path):
    from kriya.repository.bound_tree import bound_tree_paths

    def git(*args):
        subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / ".kriya" / "venv" / "bin").mkdir(parents=True)
    (tmp_path / ".kriya" / "venv" / "bin" / "python").write_text("#!/bin/sh\necho planted\n")
    git("add", "-f", "main.py", ".kriya/venv/bin/python")
    git("commit", "-q", "-m", "x")
    assert bound_tree_paths(str(tmp_path)) == frozenset({"main.py"})                 # a tracked .kriya/venv/bin/python is never project content


def test_a_planted_environment_is_discarded_by_the_verification_scope_never_reused(py_workspace, monkeypatch):
    """The scope that prepares the project environment removes whatever sits at ``.kriya/venv`` (it was not made by Kriya in this scope) and creates the environment from nothing: a planted interpreter is never
    the one a verified command runs under (registry: R3 'the scope builds its own venv, never inherits one'; PROJECT_VENV_NOT_DISCARDED is the typed refusal if it cannot be removed)."""
    planted = py_workspace / ".kriya" / "venv" / "bin" / "python"
    planted.parent.mkdir(parents=True)
    planted.write_text("#!/bin/sh\necho planted\n")
    from test_polymorphic_validation import _venv_creator

    runner = ScriptedRunner(monkeypatch, _venv_creator(py_workspace), ok("done"), ok("ok"))
    validator = PolymorphicValidator(str(py_workspace))
    with validator.verification_session(RUNTIME_VERIFICATION_GATE):
        assert validator._ensure_project_venv([]) == (CANON, None)
        assert "venv" in runner.roles()                                               # created from nothing inside the scope
        assert planted.read_text() == "#!/bin/sh\n"                                    # what Kriya's creation step wrote, not the planted script
