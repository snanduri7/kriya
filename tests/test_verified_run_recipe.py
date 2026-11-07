"""VERIFIED RUN RECIPE (owner ruling 2026-11-07, "How to run"): derived only from an application runtime check Kriya actually executed and saw succeed.

MEASURED (owner screenshot): a finished run told the user nothing about how to run what Kriya built; the only hint was the approval card's host-interpreter path. The backend now projects a typed recipe - the user's
normal invocation, not Kriya's scratch environment - and says plainly when it has none."""
import pytest

from kriya.workflow.run_recipe import derive_verified_run_recipe

CANON = ".kriya/venv/bin/python"


def _outcome(commands=((CANON, "main.py"),), *, attempt=1, success=True, graded_by="llm", exit_code=0, timed_out=False, as_text=False):
    import json

    steps = [{"command": list(argv), "exit_code": exit_code, "timed_out": timed_out, "stdout": "ok\n"} for argv in commands]
    return {"attempt": attempt, "type": "run_verification", "success": success, "graded_by": graded_by,
            "commands": [list(argv) for argv in commands], "steps": json.dumps(steps) if as_text else steps}


def _recipe(outcomes, workspace, attempt=1):
    return derive_verified_run_recipe(outcomes, attempt, str(workspace))


def test_a_dependency_free_python_run_is_presented_as_the_users_normal_invocation(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n")
    recipe = _recipe([_outcome()], tmp_path)
    assert [s["command"] for s in recipe["steps"]] == ["python3 main.py"] and recipe["steps"][0]["kind"] == "run"
    assert recipe["entrypoint"] == "main.py" and recipe["working_directory"] == "." and recipe["environment_note"] is None
    assert recipe["evidence"]["verified_argv"] == [[CANON, "main.py"]] and recipe["evidence"]["attempt"] == 1
    assert ".kriya" not in " ".join(s["command"] for s in recipe["steps"])          # Kriya's scratch environment is never taught to the user


def test_the_executed_steps_may_arrive_in_their_persisted_text_form(tmp_path):
    assert _recipe([_outcome(as_text=True)], tmp_path)["steps"][0]["command"] == "python3 main.py"


def test_a_requirements_txt_adds_the_install_step_the_verification_environment_had(tmp_path):
    (tmp_path / "requirements.txt").write_text("# deps\nrequests==2.32.0\n")
    recipe = _recipe([_outcome()], tmp_path)
    assert [(s["kind"], s["command"]) for s in recipe["steps"]] == [("setup", "python3 -m pip install -r requirements.txt"), ("run", "python3 main.py")]


def test_a_comment_only_requirements_txt_adds_nothing(tmp_path):
    (tmp_path / "requirements.txt").write_text("# nothing yet\n\n")
    assert [s["command"] for s in _recipe([_outcome()], tmp_path)["steps"]] == ["python3 main.py"]


def test_a_pyproject_only_declaration_is_a_note_not_an_invented_command(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\ndependencies = ["requests"]\n')
    recipe = _recipe([_outcome()], tmp_path)
    assert [s["command"] for s in recipe["steps"]] == ["python3 main.py"] and "pyproject.toml" in recipe["environment_note"]


def test_a_bare_tool_and_a_sequence_are_presented_as_they_ran(tmp_path):
    recipe = _recipe([_outcome([("mvn", "-e", "-q", "compile", "exec:java"), ("java", "-jar", "target/app.jar", "a b")])], tmp_path)
    assert [s["command"] for s in recipe["steps"]] == ["mvn -e -q compile exec:java", "java -jar target/app.jar 'a b'"]


@pytest.mark.parametrize("argv", [("/usr/bin/python3", "main.py"), ("./run.sh",), ("/Users/x/.venv/bin/python3.14", "main.py"), ("bin/tool", "x")])
def test_an_executable_path_that_is_not_a_user_invocation_gives_no_recipe(tmp_path, argv):
    assert _recipe([_outcome([argv])], tmp_path) is None


@pytest.mark.parametrize("kwargs", [dict(success=False), dict(exit_code=1), dict(timed_out=True), dict(graded_by="managed_service_probe"), dict(attempt=2)])
def test_nothing_is_presented_unless_the_run_really_passed_in_the_attempt_that_passed(tmp_path, kwargs):
    assert _recipe([_outcome(**kwargs)], tmp_path, attempt=1) is None


def test_no_application_runtime_check_means_no_recipe(tmp_path):
    assert _recipe([], tmp_path) is None
    assert _recipe([{"attempt": 1, "type": "compile", "success": True}, {"attempt": 1, "type": "targeted_test", "success": True}], tmp_path) is None


def test_the_last_passing_check_of_the_passing_attempt_wins_and_a_malformed_outcome_is_ignored(tmp_path):
    recipe = _recipe(["junk", None, _outcome([(CANON, "old.py")]), _outcome([(CANON, "main.py", "--fast")])], tmp_path)
    assert recipe["steps"][-1]["command"] == "python3 main.py --fast"
    assert _recipe([{**_outcome(), "steps": [{"command": [], "exit_code": 0}]}], tmp_path) is None
    assert _recipe([{**_outcome(), "steps": "not json"}], tmp_path)["steps"][-1]["command"] == "python3 main.py"     # falls back to the recorded commands


def test_the_completed_result_carries_the_recipe_only_for_a_passing_run(tmp_path):
    import test_outcome as to

    state = to._state(attempt_number=1, gate_outcomes=[_outcome()], all_files_written=["main.py"])
    passing = to._completed(state, to._deps(), quality_passed=True, failure_category=None, workspace_path=str(tmp_path))
    failing = to._completed(state, to._deps(), quality_passed=False, workspace_path=str(tmp_path))
    assert passing["verified_run_recipe"]["steps"][0]["command"] == "python3 main.py"
    assert "verified_run_recipe" not in failing
    no_run = to._state(attempt_number=1, gate_outcomes=[{"attempt": 1, "type": "compile", "success": True}])
    assert "verified_run_recipe" not in to._completed(no_run, to._deps(), quality_passed=True, failure_category=None, workspace_path=str(tmp_path))


# ---- the typed field the launch record carries (the desktop reads this and derives nothing) --------------------------------------------------------------------------------------
def _finish(tmp_path, result):
    from kriya.context.workspace_identity import canonical_workspace, workspace_identity
    from kriya.control.launch import LaunchSession

    workspace = tmp_path / "proj"
    workspace.mkdir(parents=True)
    canonical = canonical_workspace(str(workspace))
    session = LaunchSession(canonical, workspace_identity(canonical), "20261107T080000-abcdef01", "goal", {"ready": True}, {})
    session.open()
    session.finish(0, result, None)
    return session._record["ended"]


def _recipe_result(tmp_path):
    (tmp_path / "main.py").write_text("print('x')\n")
    return {"status": "success", "verified_run_recipe": derive_verified_run_recipe([_outcome()], 1, str(tmp_path))}


def test_the_launch_record_carries_the_recipe_typed_and_bounded(tmp_path):
    ended = _finish(tmp_path / "a", _recipe_result(tmp_path))
    recipe = ended["verified_run_recipe"]
    assert recipe["steps"] == [{"kind": "run", "command": "python3 main.py"}] and recipe["entrypoint"] == "main.py" and recipe["attempt"] == 1
    assert recipe["environment_note"] is None and "isolated project environment" in recipe["statement"]
    assert set(recipe) == {"steps", "entrypoint", "environment_note", "statement", "attempt"}        # nothing of the run's internals (argv, evidence ids) travels


def test_the_launch_record_redacts_and_cuts_every_recipe_text(tmp_path):
    secret = "https://user:hunter2@example.com/x"
    result = {"status": "success", "verified_run_recipe": {"steps": [{"kind": "run", "command": f"curl {secret} " + "z" * 900}] * 20, "entrypoint": "e" * 900,
                                                          "environment_note": secret, "evidence": {"statement": "s" * 900, "attempt": 3}}}
    recipe = _finish(tmp_path, result)["verified_run_recipe"]
    assert len(recipe["steps"]) == 8 and all(len(step["command"]) <= 400 for step in recipe["steps"]) and "hunter2" not in str(recipe)
    assert len(recipe["entrypoint"]) == 400 and len(recipe["statement"]) == 400


@pytest.mark.parametrize("result", [{"status": "success"}, {"status": "success", "verified_run_recipe": None}, {"status": "success", "verified_run_recipe": "python3 main.py"},
                                    {"status": "success", "verified_run_recipe": {"steps": []}}, {"status": "success", "verified_run_recipe": {"steps": [{"kind": "evil", "command": "rm -rf /"}]}},
                                    {"status": "success", "verified_run_recipe": {"steps": [{"kind": "run", "command": 7}, "x", None]}}])
def test_a_missing_or_malformed_recipe_adds_no_field_never_an_invented_one(tmp_path, result):
    assert "verified_run_recipe" not in _finish(tmp_path, result)
