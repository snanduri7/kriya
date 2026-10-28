import os
import shutil
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from _validator_runner import ScriptedRunner, fail, ok

from kriya.config import AppConfig
from kriya.tools.validate import (
    PolymorphicValidator,
    get_pom_dependencies,
    get_pom_own_coordinate,
    get_pom_reactor_modules,
)
from kriya.workflow.acceptance import (
    runtime_application_step_started,
    runtime_verification_infrastructure_reason,
)
from kriya.workflow.failure_grounding import extract_error_source_locations
from kriya.workflow.file_resolution import ground_java_entrypoint_in_no_build_file_projects
from kriya.workflow.verification_authority import (
    deterministic_sequence_kind,
    deterministic_verification_kind,
)


def _venv_creator(workspace):
    """A scripted answer for the project environment's creation that leaves what a real ``python -m venv`` leaves: the interpreter, at its host path."""
    venv_python = os.path.join(str(workspace), ".kriya", "venv", "bin", "python")

    def create(_cmd, _cwd):
        os.makedirs(os.path.dirname(venv_python), exist_ok=True)
        with open(venv_python, "w", encoding="utf-8") as stream:
            stream.write("#!/bin/sh\n")
        return ok()
    return create


def _maven_writes(*relpaths, stdout="BUILD SUCCESS"):
    """What a real Maven leaves in the tree it ran in - the gate view the contained flow builds in, not the workspace: empty files at the given relative paths."""
    def run(_cmd, cwd):
        for rel in relpaths:
            path = os.path.join(cwd, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb"):
                pass
        return ok(stdout)
    return run


def _missing_binary(name):
    def run(_cmd, _cwd):
        raise FileNotFoundError(2, "No such file or directory", name)
    return run


def _pytest_command(runner):
    """The one pytest invocation of a scripted run (the contained flow creates the project environment before it)."""
    assert runner.roles()[-1] == "pytest", runner.commands
    return runner.commands[-1]


def test_polymorphic_stack_detection(tmp_path):
    # 1. Test Python Detection
    (tmp_path / "requirements.txt").write_text("")
    v1 = PolymorphicValidator(str(tmp_path))
    assert v1.stack == "python"
    
    # 2. Test Java Detection
    (tmp_path / "requirements.txt").unlink()
    (tmp_path / "pom.xml").write_text("<project></project>")
    v2 = PolymorphicValidator(str(tmp_path))
    assert v2.stack == "java"
    
    # 3. Test Ruby Detection
    (tmp_path / "pom.xml").unlink()
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'")
    v3 = PolymorphicValidator(str(tmp_path))
    assert v3.stack == "ruby"


def test_explicit_ruby_marker_precedes_residual_standalone_java(tmp_path):
    (tmp_path / "Old.java").write_text("class Old {}")
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'")

    assert PolymorphicValidator(str(tmp_path)).stack == "ruby"


def test_standalone_javac_ignores_contextual_files_absent_from_sandbox(tmp_path):
    (tmp_path / "App.java").write_text("class App {}")
    validator = PolymorphicValidator(str(tmp_path))

    with patch.object(validator, "_run_cmd_with_timeout") as run_cmd:
        run_cmd.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        result = validator.run_compile_check(["App.java", "Earlier.java"])

    assert result["success"] is True
    command = run_cmd.call_args.args[0]
    # S7: the contained flow names the sources workspace-relative (the container sees the tree at its own mount, not at the host path)
    assert "App.java" in command and str(tmp_path / "App.java") not in command
    assert "Earlier.java" not in command and str(tmp_path / "Earlier.java") not in command


def test_polymorphic_stack_detection_unknown_for_unsupported_stack(tmp_path):
    # Regression test: a real JS/TS/Go/Rust/C# project (or any workspace with
    # none of the Java/Python/Ruby markers) used to silently fall back to
    # "python" and get a false-positive "compiled successfully" from a check
    # that matched zero real files. Must be distinguishable as "unknown" now.
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "index.js").write_text("console.log('hi');")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "unknown"


def test_unknown_stack_compile_check_is_honest_about_no_validation(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")
    validator = PolymorphicValidator(str(tmp_path))
    res = validator.run_compile_check(["main.go"])
    # success: True so the retry loop doesn't fail forever on a gate that can
    # never run for this stack - but the message must not claim a real check
    # happened, unlike the old blind-Python-default false positive.
    assert res["success"] is True
    assert "not confirmed" in res["output"].lower()


def test_unknown_stack_run_tests_is_honest_about_no_validation(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")
    validator = PolymorphicValidator(str(tmp_path))
    res = validator.run_tests()
    assert res["success"] is True
    assert "not confirmed" in res["output"].lower()


def test_java_compile_enrichment_respects_local_only_without_network(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    source = tmp_path / "src" / "main" / "java" / "App.java"
    source.write_text("class App {}")
    cfg = AppConfig()
    cfg.autonomy.egress_policy = "local_only"
    cfg.autonomy.web_lookup_enabled = False
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)

    with patch.object(
        validator,
        "_run_cmd_with_timeout",
        return_value={
            "returncode": 1,
            "stdout": "",
            "stderr": "package com.example.proprietary does not exist",
        },
    ), patch("httpx.Client") as http_client:
        result = validator.run_compile_check(["src/main/java/App.java"])

    assert result["success"] is False
    http_client.assert_not_called()


def test_python_compile_check(tmp_path):
    # Written before construction: stack is now detected from real Python
    # markers (a .py file present, among others), not a blind default.
    (tmp_path / "valid.py").write_text("def ok():\n    pass\n")
    validator = PolymorphicValidator(str(tmp_path))

    res1 = validator.run_compile_check(["valid.py"])
    assert res1["success"] is True
    
    # Invalid Python (SyntaxError)
    (tmp_path / "invalid.py").write_text("def bad()\n    pass\n")
    res2 = validator.run_compile_check(["invalid.py"])
    assert res2["success"] is False
    # S7/P-A: contained, the gate returns the raw `py_compile` report; failure_grounding grounds that native shape.
    assert "SyntaxError" in res2["output"]
    assert extract_error_source_locations(res2["output"]) == [("invalid.py", 1)]

def test_run_app_success(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text("print('[SUCCESS] it worked')\n")

    res = validator.run_app([sys.executable, "app.py"], timeout=10)

    assert res["success"] is True
    assert res["timed_out"] is False
    assert res["returncode"] == 0
    assert "[SUCCESS] it worked" in res["output"]

def test_run_app_nonzero_exit(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text("import sys\nsys.exit(1)\n")

    res = validator.run_app([sys.executable, "app.py"], timeout=10)

    assert res["success"] is False
    assert res["timed_out"] is False
    assert res["returncode"] == 1

def test_run_app_timeout(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text("import time\ntime.sleep(10)\n")

    res = validator.run_app([sys.executable, "app.py"], timeout=1)

    assert res["success"] is False
    assert res["timed_out"] is True

def test_run_app_timeout_kills_child_processes_too(tmp_path):
    """Regression test for a real, live-found bug (2026-08-03): the previous
    subprocess.run()-based timeout handling only killed the DIRECT child
    process - if that process itself forks its own child (exactly what
    `mvn exec:exec` does, launching a separate `java` process to actually
    run the app), the grandchild survived the parent's death entirely.
    Confirmed live: an embedded Qpid broker process leaked this way, stayed
    alive for over an hour after its own run timed out, and broke an
    entirely separate, later validation run that happened to need the same
    port. Spawns a real child process that continuously proves it's alive
    (a heartbeat file) and confirms it actually stops - not just gets
    orphaned and keeps running - once the timed-out parent is killed."""
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "parent.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'child.py'])\n"
        "time.sleep(10)\n"
    )
    (tmp_path / "child.py").write_text(
        "import time\n"
        "for _ in range(100):\n"
        "    with open('heartbeat.txt', 'a') as f:\n"
        "        f.write('x')\n"
        "    time.sleep(0.1)\n"
    )

    res = validator.run_app([sys.executable, "parent.py"], timeout=1)
    assert res["timed_out"] is True

    heartbeat = tmp_path / "heartbeat.txt"
    time.sleep(0.3)
    size_after_kill = heartbeat.stat().st_size if heartbeat.exists() else 0
    time.sleep(1.0)
    size_later = heartbeat.stat().st_size if heartbeat.exists() else 0
    assert size_after_kill == size_later, "child process kept running after the timed-out parent was killed"


def test_run_app_timeout_does_not_hang_forever_if_sigkill_cannot_reap_the_process():
    """Regression test for a finding from the 2026-08-12 SME review: the
    post-SIGKILL process.communicate() call (to reap the process and collect
    whatever output exists) previously had no timeout of its own - a child
    stuck in an uninterruptible I/O sleep (D-state, real under slow/
    networked storage) can't be reaped even by SIGKILL until that I/O
    completes, so this call could hang indefinitely, defeating the whole
    point of the surrounding timeout. Not reproducible with a real
    subprocess (SIGKILL always succeeds immediately for an ordinary
    process), so this mocks subprocess.Popen directly to simulate a second
    (reap) timeout after the first."""
    validator = PolymorphicValidator("/fake/workspace")

    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["sleep", "999"], timeout=1),
        subprocess.TimeoutExpired(cmd=["sleep", "999"], timeout=10, output="partial stdout", stderr="partial stderr"),
    ]

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("os.killpg"), patch("os.getpgid", return_value=12345):
        res = validator._run_cmd_with_timeout(["sleep", "999"], cwd="/fake/workspace", timeout=1)

    assert res["timeout"] is True
    assert res["stdout"] == "partial stdout"
    assert "REAP TIMEOUT" in res["stderr"]
    assert "partial stderr" in res["stderr"]
    assert mock_process.communicate.call_count == 2

def test_run_app_no_command():
    validator = PolymorphicValidator(".")
    res = validator.run_app([])
    assert res["success"] is False

def test_deterministic_verification_classifier_separates_builds_from_runtime():
    assert deterministic_verification_kind(["mvn", "-e", "-q", "compile"]) == "build"
    assert deterministic_verification_kind(["mvn", "-f", "sub/pom.xml", "verify"]) == "test"
    assert deterministic_verification_kind(["./mvnw", "test"]) == "test"
    assert deterministic_verification_kind(["python", "-m", "pytest", "-q"]) == "test"
    assert deterministic_verification_kind(["pytest", "-q"]) == "test"
    assert deterministic_verification_kind(["python", "-m", "unittest"]) == "test"
    assert deterministic_verification_kind(
        ["python", "-m", "django", "test", "customers.tests"]
    ) == "test"
    assert deterministic_verification_kind(["mvn", "test"]) == "test"
    assert deterministic_verification_kind(["python", "-m", "django", "runserver"]) is None
    assert deterministic_verification_kind(["python", "app.py"]) is None
    assert deterministic_verification_kind(["mvn", "spring-boot:run"]) is None
    assert deterministic_verification_kind(["mvn", "exec:java"]) is None
    assert deterministic_verification_kind(["gradle", "run"]) is None


def test_deterministic_verification_sequence_requires_every_step_to_be_authoritative():
    assert deterministic_sequence_kind([["mvn", "compile"], ["mvn", "test"]]) == "test"
    assert deterministic_sequence_kind([["mvn", "compile"], ["java", "App"]]) is None


def test_run_app_sequence_multi_step_success(tmp_path):
    """Regression test for a real bug caught live: a goal like "add an item, then
    list items" needs TWO sequential invocations to demonstrate correctness - a
    single no-argument invocation can only ever show a help/usage message. Confirms
    state (a written file) persists between steps, since they share workspace_path."""
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text(
        "import sys, json\n"
        "path = 'state.json'\n"
        "if sys.argv[1] == 'add':\n"
        "    data = json.load(open(path)) if __import__('os').path.exists(path) else []\n"
        "    data.append(sys.argv[2])\n"
        "    json.dump(data, open(path, 'w'))\n"
        "    print(f'Added {sys.argv[2]}')\n"
        "elif sys.argv[1] == 'list':\n"
        "    data = json.load(open(path)) if __import__('os').path.exists(path) else []\n"
        "    for item in data:\n"
        "        print(item)\n"
    )

    res = validator.run_app_sequence(
        [[sys.executable, "app.py", "add", "Task 1"], [sys.executable, "app.py", "list"]],
        timeout=10,
    )
    # Each step carries its execution evidence. S7: every run is contained, where the resource limits are the container's, so a step carries no host rlimit
    # strategy (LINUX-JVM-RLIMIT-AS-001 evidence exists only on the host path).
    # What a contained step carries instead is its egress evidence: no network was granted to the application.
    steps = [dict(step) for step in res["steps"]]
    for step in steps:
        assert step.pop("egress")["capability"] == "denied"
        step["command"] = [os.path.realpath(step["command"][0]), *step["command"][1:]]      # the interpreter is the same file however the launcher spells it
    interpreter = os.path.realpath(sys.executable)
    assert steps == [
        {
            "command": [interpreter, "app.py", "add", "Task 1"],
            "exit_code": 0,
            "stdout": "Added Task 1\n",
            "stderr": "",
            "timed_out": False,
        },
        {
            "command": [interpreter, "app.py", "list"],
            "exit_code": 0,
            "stdout": "Task 1\n",
            "stderr": "",
            "timed_out": False,
        },
    ]

    assert res["success"] is True
    assert res["timed_out"] is False
    assert "Added Task 1" in res["output"]
    assert "Task 1" in res["output"]
    assert "Step 1/2" in res["output"]
    assert "Step 2/2" in res["output"]


def test_packaged_raw_java_runtime_uses_isolated_classes_and_distinguishes_invalid_input(tmp_path):
    if not shutil.which("javac") or not shutil.which("java"):
        pytest.skip("JDK is not available")
    source = tmp_path / "src/main/java/com/example/App.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package com.example;\n"
        "public class App {\n"
        "  public static void main(String[] args) {\n"
        "    try { int value = Integer.parseInt(args[0]); "
        "System.out.println(\"RESULT=\" + (value * 2)); }\n"
        "    catch (Exception e) { System.err.println(\"INVALID_INPUT\"); System.exit(2); }\n"
        "  }\n"
        "}\n"
    )
    commands = ground_java_entrypoint_in_no_build_file_projects(
        run_commands=[["java", "App", "21"], ["java", "App", "bad"]],
        command_source="inferred",
        files_written=["src/main/java/com/example/App.java"],
        java_main_classes={"src/main/java/com/example/App.java": "com.example.App"},
        jvm_module_flags=[], build_file_content=None,
    )

    result = PolymorphicValidator(str(tmp_path)).run_app_sequence(commands, timeout=10)

    assert commands == [
        ["javac", "-d", ".kriya/runtime-verification/classes", "src/main/java/com/example/App.java"],
        ["java", "-cp", ".kriya/runtime-verification/classes", "com.example.App", "21"],
        ["java", "-cp", ".kriya/runtime-verification/classes", "com.example.App", "bad"],
    ]
    assert result["steps"][0]["exit_code"] == 0
    assert result["steps"][1]["exit_code"] == 0
    assert "RESULT=42" in result["steps"][1]["stdout"]
    assert result["steps"][2]["exit_code"] == 2
    assert "INVALID_INPUT" in result["steps"][2]["stderr"]
    assert runtime_verification_infrastructure_reason(result) is None
    assert runtime_application_step_started(result) is True


def test_jvm_launch_failure_is_not_valid_invalid_input_evidence():
    result = {
        "success": False, "timed_out": False, "returncode": 1,
        "output": "Error: Could not find or load main class com.example.App",
        "steps": [{
            "command": ["java", "-cp", "missing", "com.example.App", "bad"],
            "exit_code": 1, "stdout": "",
            "stderr": "Could not find or load main class com.example.App",
            "timed_out": False,
        }],
    }
    assert runtime_verification_infrastructure_reason(result) is not None
    assert runtime_application_step_started(result) is False

def test_run_app_sequence_continues_after_step_failure_and_reports_it(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text(
        "import sys\n"
        "if sys.argv[1] == 'fail':\n"
        "    sys.exit(1)\n"
        "print('second step ran')\n"
    )

    res = validator.run_app_sequence(
        [[sys.executable, "app.py", "fail"], [sys.executable, "app.py", "ok"]],
        timeout=10,
    )

    # Overall failure (an earlier step failed), but both steps still ran and both
    # are visible in the output as evidence for the grader.
    assert res["success"] is False
    assert "second step ran" in res["output"]
    assert res["returncode"] == 0  # the LAST step's exit code

def test_run_app_sequence_plain_crash_output_feeds_the_real_contract_parser(tmp_path):
    """Deterministic integration check for the attempt.py Finding #1 fix
    (2026-08-15, independent brutal review): no LLM involved at all - this
    exercises the REAL, non-mocked PolymorphicValidator.run_app_sequence()
    against a script that deliberately exits nonzero right after printing a
    "[VERIFICATION] FAIL: <reason>" marker (a plain crash, no hang - the
    exact success=False/timed_out=False shape the buggy branch used to
    special-case into a synthetic "one or more steps failed" message,
    discarding this marker entirely), then feeds the REAL captured output
    through the REAL extract_contract_verdict() (also unmocked) - confirming
    the actual integration boundary attempt.py's fixed branch now relies on,
    not just the branch's own control-flow logic in isolation (already
    covered separately by mocked unit/workflow-level tests)."""
    from kriya.workflow.verification_contract import extract_contract_verdict

    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text(
        "print('decoded=13, original=15')\n"
        "print('[VERIFICATION] FAIL: decoded value did not match original')\n"
        "import sys\n"
        "sys.exit(1)\n"
    )

    res = validator.run_app_sequence([[sys.executable, "app.py"]], timeout=10)

    assert res["success"] is False
    assert res["timed_out"] is False
    assert res["returncode"] == 1

    verdict = extract_contract_verdict(res["output"])
    assert verdict is not None, "the real captured output must carry the marker through intact"
    assert verdict["passed"] is False
    assert "decoded value did not match original" in verdict["reasoning"]

def test_run_app_sequence_stops_on_timeout(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    (tmp_path / "app.py").write_text("import time\ntime.sleep(10)\n")
    (tmp_path / "never_reached.txt").write_text("")

    res = validator.run_app_sequence(
        [[sys.executable, "app.py"], [sys.executable, "-c", "print('should not run')"]],
        timeout=1,
    )

    assert res["success"] is False
    assert res["timed_out"] is True
    assert "should not run" not in res["output"]

def test_run_app_sequence_no_commands():
    validator = PolymorphicValidator(".")
    res = validator.run_app_sequence([])
    assert res["success"] is False

def test_run_app_sequence_uses_isolated_venv_interpreter_for_python(tmp_path, monkeypatch):
    """Regression test for a real bug found live (2026-08-07,
    django_healthcheck_gap): run_tests() got an isolated, dependency-
    installed venv, but run_app_sequence() (Runtime Verification) still ran
    via sys.executable directly - a goal needing a real third-party package
    hit the identical ModuleNotFoundError one gate later, after the goal
    finally got far enough to reach Runtime Verification at all. Both gates
    must resolve to the SAME interpreter. (S7: the process seam is the validator's own choke point, and the interpreter is the project
    environment's, spelled as the contained flow spells it: workspace-relative.)"""
    (tmp_path / "requirements.txt").write_text("django==5.2\n")
    (tmp_path / "manage.py").write_text("print('would run django here')\n")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "python"

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), ok("Successfully installed django"), ok("ok"))
    res = validator.run_app_sequence([["python", "manage.py", "runserver", "--help"]])

    assert res["success"] is True, res["output"]
    assert runner.roles() == ["venv", "pip", "other"], runner.commands  # R3: the scope builds its own venv, never inherits one
    run_cmd = runner.commands[2]
    assert run_cmd[0] == ".kriya/venv/bin/python"
    assert run_cmd[0] != sys.executable
    assert run_cmd[1:] == ["manage.py", "runserver", "--help"]

def test_run_app_sequence_reports_pip_install_failure_without_running_commands(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("this-package-does-not-exist==999.0\n")
    (tmp_path / "manage.py").write_text("pass\n")

    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), fail(stderr="ERROR: No matching distribution found"), strict=True)
    res = validator.run_app_sequence([["python", "manage.py", "runserver"]])

    assert res["success"] is False
    assert "pip install" in res["output"]
    # Only the failed install call should have been made - the run command
    # must never execute against a dependency environment known to be broken.
    assert runner.roles() == ["venv", "pip"], runner.commands


def test_python_run_tests_resolves_src_layout_imports(tmp_path):
    # Reproduces a real generation failure: a src/ layout project where the
    # generated test imports the module either bare ("from calculator import x",
    # the src-layout convention) or package-qualified ("from src.calculator import
    # x"). Both must resolve regardless of which one the Developer Agent wrote,
    # since that choice is inconsistent across retries/models.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")
    (src_dir / "calculator.py").write_text("def add(a, b):\n    return a + b\n")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    validator = PolymorphicValidator(str(tmp_path))

    (tests_dir / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    res_bare = validator.run_tests()
    assert res_bare["success"] is True, res_bare["output"]

    (tests_dir / "test_calculator.py").write_text(
        "from src.calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    res_qualified = validator.run_tests()
    assert res_qualified["success"] is True, res_qualified["output"]

def test_python_run_tests_resolves_maven_style_invented_layout(tmp_path):
    """Regression test for a real bug found live (2026-08-07 eval harness,
    python_task_tracker): the Developer Agent invents a Maven/Gradle-style
    src/main/python nesting for pure-Python goals despite an explicit prompt
    instruction against it, and neither of the two existing fallback roots
    (workspace root, workspace/src) reaches a package three levels deep at
    src/main/python/tasks - every attempt failed with ModuleNotFoundError
    regardless of the generated code's actual correctness. Confirmed
    reproducible across 7/7 attempts on two different models before this fix;
    make test collection robust to the specific nesting shape actually
    observed instead of continuing to rely on prompting alone."""
    src_main_python = tmp_path / "src" / "main" / "python"
    tasks_dir = src_main_python / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "store.py").write_text(
        "class TaskStore:\n    def __init__(self):\n        self.tasks = []\n"
    )
    tests_dir = src_main_python / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_store.py").write_text(
        "from tasks.store import TaskStore\n\n"
        "def test_store_constructs():\n    assert TaskStore().tasks == []\n"
    )

    validator = PolymorphicValidator(str(tmp_path))
    res = validator.run_tests()
    assert res["success"] is True, res["output"]

def test_python_run_tests_installs_requirements_into_isolated_venv_before_pytest(tmp_path, monkeypatch):
    """Regression test for a real bug found live (2026-08-07 eval harness,
    django_healthcheck_gap): a goal needing a real third-party package (e.g.
    Django) failed every attempt with ModuleNotFoundError, since
    PolymorphicValidator ran tests via sys.executable - Kriya's OWN
    interpreter, which only has whatever Kriya itself depends on installed -
    with no per-project dependency install step, unlike Ruby's `bundle
    install` fix. A requirements.txt with real content must get installed
    into an ISOLATED project-local venv (not sys.executable directly, which
    would risk breaking Kriya's own environment) before pytest runs, and
    pytest itself must then run under THAT venv's interpreter. (S7: the process seam is the
    validator's choke point; the command is the contained one, so the venv and the requirements file are workspace-relative.)"""
    (tmp_path / "requirements.txt").write_text("django==5.2\n")
    (tmp_path / "app.py").write_text("import django\n")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "python"

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), ok("Successfully installed django"), ok("1 passed"), strict=True)
    res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert runner.roles() == ["venv", "pip", "pytest"], runner.commands  # R3: the scope builds its own venv, never inherits one
    _create_cmd, install_cmd, pytest_cmd = runner.commands
    assert install_cmd == [".kriya/venv/bin/python", "-m", "pip", "install", "-q", "-r", "requirements.txt", "pytest"]
    # pytest itself must run under the VENV's interpreter, not sys.executable -
    # installing into an isolated venv is pointless if the test run doesn't
    # actually use it.
    assert pytest_cmd[0] == ".kriya/venv/bin/python"
    assert pytest_cmd[0] != sys.executable

def test_python_run_tests_reports_pip_install_failure_without_running_pytest(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("this-package-does-not-exist==999.0\n")
    (tmp_path / "app.py").write_text("pass\n")

    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), fail(stderr="ERROR: No matching distribution found for this-package-does-not-exist==999.0"), strict=True)
    res = validator.run_tests()

    assert res["success"] is False
    assert "pip install" in res["output"]
    assert "this-package-does-not-exist" in res["output"]
    # Only the failed install call should have been made - pytest must never
    # run against a dependency environment known to be broken.
    assert runner.roles() == ["venv", "pip"], runner.commands


def test_python_run_tests_creates_venv_from_scratch_when_not_already_present(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "app.py").write_text("import requests\n")
    venv_python_path = str(tmp_path / ".kriya" / "venv" / "bin" / "python")

    def create_venv(_cmd, _cwd):
        # Real venv creation would produce this file - simulate that side
        # effect so PolymorphicValidator's own os.path.exists check (its
        # actual success signal, not just the mocked returncode) passes.
        os.makedirs(os.path.dirname(venv_python_path), exist_ok=True)
        with open(venv_python_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        return ok()

    validator = PolymorphicValidator(str(tmp_path))
    assert not os.path.exists(venv_python_path)

    runner = ScriptedRunner(monkeypatch, create_venv, ok("Successfully installed requests"), ok("1 passed"), strict=True)
    res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert runner.roles() == ["venv", "pip", "pytest"], runner.commands
    assert runner.commands[0][1:] == ["-m", "venv", ".kriya/venv"]


def test_python_run_tests_without_requirements_creates_the_project_environment_with_pytest_only(tmp_path, monkeypatch):
    """The contained counterpart of the two host-only 'skips venv' tests (census class H, deleted in S8): every run is contained, so even a stdlib-only project gets its
    project environment - but the install carries no requirements file, only pytest."""
    (tmp_path / "app.py").write_text("pass\n")
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n")
    venv_python_path = str(tmp_path / ".kriya" / "venv" / "bin" / "python")

    def create_venv(_cmd, _cwd):
        os.makedirs(os.path.dirname(venv_python_path), exist_ok=True)
        with open(venv_python_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        return ok()

    runner = ScriptedRunner(monkeypatch, create_venv, ok(), ok("1 passed"), strict=True)
    res = PolymorphicValidator(str(tmp_path)).run_tests()

    assert res["success"] is True, res["output"]
    assert runner.roles() == ["venv", "pip", "pytest"], runner.commands
    assert runner.commands[1] == [".kriya/venv/bin/python", "-m", "pip", "install", "-q", "pytest"]


def test_java_ruby_compile_invocation(tmp_path, monkeypatch):
    # Test Java compile invocation mocks
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    runner = ScriptedRunner(monkeypatch, _maven_writes("target/classes/UserService.class"))
    res = validator.run_compile_check(["UserService.java"])
    assert runner.calls
    assert res["success"] is True

    # Test Ruby compile invocation mocks
    (tmp_path / "pom.xml").unlink()
    (tmp_path / "Gemfile").write_text("")
    (tmp_path / "test.rb").write_text("puts 123")

    validator_rb = PolymorphicValidator(str(tmp_path))
    assert validator_rb.stack == "ruby"

    res_rb = validator_rb.run_compile_check(["test.rb"])
    assert res_rb["success"] is True


def test_ruby_run_tests_installs_gems_before_rspec(tmp_path, monkeypatch):
    """Regression test for a real bug found live (eval harness batch
    20260804-115621): a fresh sandbox never has gems installed, so `bundle exec
    rspec` fails with 'bundler: command not found: rspec' regardless of whether
    the generated Ruby code is correct - confirmed live burning a full 6-attempt
    retry budget on code that was already right. `bundle install` must run first
    whenever a real Gemfile is present."""
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rspec'\n")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "example_spec.rb").write_text("RSpec.describe 'x' do; end\n")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "ruby"

    runner = ScriptedRunner(monkeypatch, ok("Bundle complete."), ok("1 example, 0 failures"), strict=True)
    res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert len(runner.commands) == 2
    first_cmd, second_cmd = runner.commands
    assert first_cmd == ["bundle", "install", "--path", "vendor/bundle"]
    assert second_cmd[:3] == ["bundle", "exec", "rspec"]


def test_java_run_tests_converts_test_file_path_to_class_name_for_maven(tmp_path, monkeypatch):
    """Regression test for a real bug found live (2026-08-07,
    kriya-protocol-parser-app): extract_target_test() returns a raw file
    path (e.g. "src/test/java/com/example/ProtocolTest.java"), and that
    used to be passed straight into Maven's -Dtest= verbatim - which
    expects a class name, not a path, and matches nothing. Confirmed live:
    "No tests matching pattern ... were executed!" burned the entire retry
    budget on a Kriya-side invocation bug the generated code had no way to
    fix, since the model's own test file was correct the whole time."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    runner = ScriptedRunner(monkeypatch, ok("Tests run: 1"), strict=True)
    res = validator.run_tests(target_test="src/test/java/com/example/ProtocolTest.java")

    assert res["success"] is True
    cmd = runner.commands[0]
    assert "-Dtest=ProtocolTest" in cmd
    assert not any("src/test/java" in arg for arg in cmd)

def test_java_run_tests_converts_test_file_path_to_class_name_for_gradle(tmp_path, monkeypatch):
    (tmp_path / "build.gradle").write_text("")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    runner = ScriptedRunner(monkeypatch, ok("BUILD SUCCESSFUL"), strict=True)
    res = validator.run_tests(target_test="src/test/java/com/example/ProtocolTest.java")

    assert res["success"] is True
    assert runner.commands[0][-2:] == ["--tests", "ProtocolTest"]

def test_java_run_tests_no_target_test_omits_dtest_flag(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, ok("Tests run: 3"), strict=True)
    res = validator.run_tests()

    assert res["success"] is True
    # S7: Maven runs offline against the contained dependency cache; no target means no ``-Dtest`` selector at all.
    assert runner.commands[0] == ["mvn", "-B", "-o", "-Dmaven.repo.local=/kriya/cache/m2", "test"]


# ---------------------------------------------------------------------------
# VAL-001 G1-R3: multi-target selection (ordered sequence -> separate argv
# entries, never a shell-joined string)
# ---------------------------------------------------------------------------

def test_python_run_tests_multi_target_uses_separate_argv_entries_never_joined_string(tmp_path, monkeypatch):
    """G1-R3 PREPARE regression: a multi-target request must reach pytest as separate argv entries, never as one joined string, which pytest would treat as a single (nonexistent)
    path. The empirical confirmation against a real Graphify worktree: 0 collected, "file or directory not found" for the joined-string form."""
    (tmp_path / "app.py").write_text("pass\n")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "python"

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), ok(), ok("2 passed"), strict=True)
    validator.run_tests(target_test=("tests/a.py", "tests/b.py"))

    cmd = _pytest_command(runner)
    assert cmd[-2:] == ["tests/a.py", "tests/b.py"], (
        f"expected the two targets as separate trailing argv entries, got {cmd}"
    )
    assert "tests/a.py tests/b.py" not in cmd, "must never be joined into one string"
    assert not any(" " in arg and "tests/a.py" in arg and "tests/b.py" in arg for arg in cmd)


def test_python_run_tests_multi_target_list_form_equivalent_to_tuple(tmp_path, monkeypatch):
    """A plain list (the shape a YAML-loaded config field actually produces)
    behaves identically to a tuple - both are just "an ordered sequence of
    strings" to run_tests()."""
    (tmp_path / "app.py").write_text("pass\n")
    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), ok(), ok("2 passed"), strict=True)
    validator.run_tests(target_test=["tests/a.py", "tests/b.py"])

    assert _pytest_command(runner)[-2:] == ["tests/a.py", "tests/b.py"]


def test_python_run_tests_single_string_target_unchanged_single_argv_entry(tmp_path, monkeypatch):
    """Existing single-target behavior remains compatible: a bare string
    still produces exactly one trailing argv entry, identical to before
    multi-target support existed."""
    (tmp_path / "app.py").write_text("pass\n")
    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, _venv_creator(tmp_path), ok(), ok("1 passed"), strict=True)
    validator.run_tests(target_test="tests/a.py")

    cmd = _pytest_command(runner)
    assert cmd[-1] == "tests/a.py"
    assert cmd.count("tests/a.py") == 1


def test_python_run_tests_multi_target_real_execution_selects_exactly_both_files(tmp_path):
    """End-to-end, unmocked proof (mirrors this file's own existing
    src-layout tests, which run real pytest rather than mocking Popen):
    two real test files, selected together via a tuple, both actually run -
    reproduces the exact 38+38=76 shape G1-R3 PREPARE calibrated against
    the real Graphify worktree, at a scale that doesn't depend on any
    specific real file's content."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_a.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n\ndef test_two():\n    assert 2 + 2 == 4\n"
    )
    (tests_dir / "test_b.py").write_text(
        "def test_three():\n    assert 3 + 3 == 6\n"
    )
    (tests_dir / "test_unselected.py").write_text(
        "def test_never_runs():\n    assert False\n"
    )
    validator = PolymorphicValidator(str(tmp_path))

    res = validator.run_tests(target_test=("tests/test_a.py", "tests/test_b.py"))

    assert res["success"] is True, res["output"]
    assert "3 passed" in res["output"], res["output"]
    assert "test_never_runs" not in res["output"]


def test_java_run_tests_multi_target_honors_only_first_target(tmp_path, monkeypatch):
    """Disclosed, minimal-scope limitation: multi-target selection is a
    Python-stack (pytest) concept today - Java honors only the first target,
    unchanged single-target behavior for the (so far only real) case of one
    string being passed. Never crashes on a multi-element input."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, ok("Tests run: 1"), strict=True)
    res = validator.run_tests(target_test=(
        "src/test/java/com/example/ProtocolTest.java",
        "src/test/java/com/example/OtherTest.java",
    ))

    assert res["success"] is True
    cmd = runner.commands[0]
    assert "-Dtest=ProtocolTest" in cmd
    assert not any("OtherTest" in arg for arg in cmd)


def test_ruby_run_tests_multi_target_extends_argv(tmp_path, monkeypatch):
    """rspec accepts multiple positional path args natively - same
    structural argv-extend treatment as the Python branch, not limited to
    the first target the way Java's -Dtest=/--tests single-class-name
    convention is."""
    (tmp_path / "Rakefile").write_text("")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "ruby"

    runner = ScriptedRunner(monkeypatch, ok("2 examples, 0 failures"), strict=True)
    res = validator.run_tests(target_test=["spec/a_spec.rb", "spec/b_spec.rb"])

    assert res["success"] is True
    assert runner.commands[0][-2:] == ["spec/a_spec.rb", "spec/b_spec.rb"]


def test_ruby_run_tests_reports_bundle_install_failure_without_running_rspec(tmp_path, monkeypatch):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rspec'\n")

    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, fail(stderr="Could not find gem 'rspec'."), strict=True)
    res = validator.run_tests()

    assert res["success"] is False
    assert "bundle install" in res["output"]
    # Only the failed `bundle install` call should have been made - rspec must
    # never run against a gem environment that's known to be broken.
    assert len(runner.commands) == 1


def test_ruby_run_tests_skips_bundle_install_without_a_gemfile(tmp_path, monkeypatch):
    # Stack detection also accepts a bare Rakefile/*.gemspec with no Gemfile -
    # `bundle install` has nothing to act on in that case and must be skipped,
    # not attempted and failed.
    (tmp_path / "Rakefile").write_text("")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "ruby"

    runner = ScriptedRunner(monkeypatch, ok("0 examples, 0 failures"), strict=True)
    res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert len(runner.commands) == 1
    assert runner.commands[0][:3] == ["bundle", "exec", "rspec"]


def test_resolve_maven_classpath_runs_nothing_and_degrades_to_none_under_the_mandatory_containment(tmp_path, monkeypatch):
    """The contained counterpart of the host-only test above (census class H, deleted in S8): ``-Dmdep.outputFile`` needs a path outside the workspace mount, which a container
    cannot write, so the lookup is not wired onto the contained path - an optional recovery-tool lookup honestly returns nothing and starts no process (documented
    SEC-001-P6 Stage 2 degrade; with containment mandatory it is now the only behaviour)."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))
    runner = ScriptedRunner(monkeypatch, strict=True)
    assert validator.resolve_maven_classpath() is None
    assert not runner.calls


def test_resolve_maven_classpath_returns_none_without_pom(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.resolve_maven_classpath() is None


def test_resolve_maven_classpath_returns_none_on_resolution_failure(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))
    with patch.object(
        validator, "_run_cmd_with_timeout",
        return_value={"returncode": 1, "stdout": "", "stderr": "unresolvable dependency"},
    ):
        assert validator.resolve_maven_classpath() is None


def test_inspect_external_class_returns_public_api_via_javap(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))
    with patch.object(validator, "resolve_maven_classpath", return_value="/fake/ignite-core.jar"), \
         patch.object(
             validator, "_run_cmd_with_timeout",
             return_value={"returncode": 0, "stdout": "public class Ignition {\n  public static Ignite start(String);\n}", "stderr": ""},
         ) as mock_run:
        result = validator.inspect_external_class("org.apache.ignite.Ignition")

    assert result == "public class Ignition {\n  public static Ignite start(String);\n}"
    invoked_cmd = mock_run.call_args.args[0]
    assert invoked_cmd == ["javap", "-public", "-classpath", "/fake/ignite-core.jar", "org.apache.ignite.Ignition"]


def test_inspect_external_class_returns_none_when_classpath_unresolvable(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    with patch.object(validator, "resolve_maven_classpath", return_value=None):
        assert validator.inspect_external_class("org.apache.ignite.Ignition") is None


def test_inspect_external_class_returns_none_when_class_not_on_classpath(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))
    with patch.object(validator, "resolve_maven_classpath", return_value="/fake/ignite-core.jar"), \
         patch.object(
             validator, "_run_cmd_with_timeout",
             return_value={"returncode": 1, "stdout": "", "stderr": "Error: class not found"},
         ):
        assert validator.inspect_external_class("com.nonexistent.Thing") is None


def test_java_compile_check_enables_rawtypes_unchecked_lint_flags(tmp_path, monkeypatch):
    """javac's default one-line "uses unchecked or unsafe operations" summary
    carries no file:line location at all - useless for pointing a retry at the
    actual mistake. showWarnings + compilerArgument are standard, portable
    maven-compiler-plugin CLI properties that turn on full -Xlint:rawtypes,
    unchecked diagnostics with real locations, no target pom.xml cooperation
    needed. Confirmed live as directly relevant: a raw-type cache access
    mistake (`ignite.cache(name)` used without generics) causes a later
    "incompatible types" hard error - the rawtypes warning names the exact
    same root cause precisely, for free, once these flags are on."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "App.java").write_text("class App {}")

    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, _maven_writes("target/classes/App.class"))
    res = validator.run_compile_check(["App.java"])

    assert res["success"] is True
    compile_cmds = [cmd for cmd in runner.commands if "compile" in cmd]
    assert compile_cmds, runner.commands
    assert "-Dmaven.compiler.showWarnings=true" in compile_cmds[-1]
    assert "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked" in compile_cmds[-1]

def test_java_compile_check_catches_maven_false_positive_when_nothing_actually_compiled(tmp_path, monkeypatch):
    """Regression test for a real live incident, 2026-08-22
    (ignite_qpid_protocol milestone 3/4): Maven's default sourceDirectory
    (src/main/java) covered none of this project's actual .java files (they
    lived at the workspace root), so `mvn clean compile` found zero source
    files and reported success anyway - "nothing to compile" isn't a build
    error to Maven. target/classes stayed empty, and the real failure only
    surfaced downstream, at RUNTIME, as a confusing "Could not find or load
    main class". Confirms run_compile_check no longer trusts returncode 0
    unconditionally when it knows about real .java files but finds no
    compiled output for any of them."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "App.java").write_text("class App {}")
    # Deliberately no target/classes directory at all - the real incident's
    # exact condition (Maven never actually compiled anything).

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, ok("BUILD SUCCESS"))
    res = validator.run_compile_check(["App.java"])

    assert res["success"] is False
    assert "zero .class files" in res["output"]
    assert "sourceDirectory" in res["output"]

def test_java_compile_check_trusts_maven_success_when_something_really_compiled(tmp_path, monkeypatch):
    """Sibling of the false-positive regression above: a real compile that
    actually produced .class output must still be trusted as success -
    this check should only catch the "nothing was ever compiled" case."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "App.java").write_text("class App {}")

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, _maven_writes("target/classes/App.class"))
    res = validator.run_compile_check(["App.java"])

    assert res["success"] is True


# --- Maven multi-module reactor compile-check (P7 production-validation, 2026-09-07) -----
# Live incident: a genuine Maven reactor (root pom.xml packaging=pom,
# <modules>core, repository, api</modules>) had Maven correctly report BUILD
# SUCCESS while the single-module check above rejected every one of 16
# attempts identically - it always looked for <workspace>/target/classes,
# which a pom-packaged aggregator root never produces (each module compiles
# into its OWN <module>/target/classes). Bounded audit of the rest of this
# file's Java/Maven path (run_tests, run_pom_validate, resolve_maven_
# classpath, _has_any_java_file, stack detection) found no other root-
# relative single-module assumption - this was the only one.

_REACTOR_POM = """<project>
  <packaging>pom</packaging>
  <modules>
    <module>core</module>
    <module>repository</module>
    <module>api</module>
  </modules>
</project>"""


def test_get_pom_reactor_modules_parses_real_declared_modules(tmp_path):
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    assert get_pom_reactor_modules(str(tmp_path / "pom.xml")) == ["core", "repository", "api"]


def test_get_pom_reactor_modules_empty_for_genuinely_single_module_pom(tmp_path):
    """The exact minimal pom.xml the sibling single-module tests above
    already use - ties this fix's own "single-module is completely
    unaffected" claim directly to the pre-existing regression coverage."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    assert get_pom_reactor_modules(str(tmp_path / "pom.xml")) == []


def test_reactor_compile_check_succeeds_when_owning_modules_have_real_classes(tmp_path, monkeypatch):
    """(1) The exact P7 reproduction shape: root pom.xml declares a real
    reactor; both candidate .java files' owning modules (core, repository)
    have real .class output in their OWN target/classes; the aggregator
    root's own target/classes never exists at all (packaging=pom - a real
    Maven aggregator produces nothing there) - must succeed."""
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    assert not (tmp_path / "target").exists()  # (7) aggregator root has none - still valid

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, _maven_writes("core/target/classes/UserService.class", "repository/target/classes/UserServiceImpl.class"))
    res = validator.run_compile_check([
        "core/src/main/java/com/narendra/app/core/ports/UserService.java",
        "repository/src/main/java/com/narendra/app/serviceImpl/UserServiceImpl.java",
    ])

    assert res["success"] is True


def test_reactor_compile_check_fails_when_owning_module_produced_no_classes(tmp_path, monkeypatch):
    """(3) Maven reports success, but the module that actually owns the
    candidate .java file produced zero .class files - the real live
    failure this fix exists to correctly diagnose (rather than falsely
    accept OR falsely reject) - must fail, and name the real module."""
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    # repository/target/classes deliberately does not exist at all.

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, ok("BUILD SUCCESS"))
    res = validator.run_compile_check([
        "repository/src/main/java/com/narendra/app/serviceImpl/UserServiceImpl.java",
    ])

    assert res["success"] is False
    assert "repository" in res["output"]
    assert "target/classes" in res["output"]


def test_reactor_compile_check_scans_every_owning_module_not_just_the_first(tmp_path, monkeypatch):
    """Candidates span TWO reactor modules (the real P7 shape - it modifies
    Java in both core and repository): core produced real output,
    repository did not. Must not stop after finding the first owning
    module's bytecode - the loop has to keep checking every distinct
    owning module and report exactly the one that's actually missing, not
    just whichever it happens to see first, and not the one that's fine."""
    (tmp_path / "core" / "target" / "classes").mkdir(parents=True)
    (tmp_path / "core" / "target" / "classes" / "A.class").write_bytes(b"")
    # repository/target/classes deliberately does not exist.

    validator = PolymorphicValidator(str(tmp_path))
    missing = validator._java_reactor_modules_missing_compiled_output(
        [
            "core/src/main/java/com/narendra/app/core/A.java",
            "repository/src/main/java/com/narendra/app/serviceImpl/B.java",
        ],
        ["core", "repository", "api"],
    )

    assert missing == ["repository"]

    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    ScriptedRunner(monkeypatch, _maven_writes("core/target/classes/A.class"))     # the contained build runs in the gate view: it is where the classes appear
    res = validator.run_compile_check([
        "core/src/main/java/com/narendra/app/core/A.java",
        "repository/src/main/java/com/narendra/app/serviceImpl/B.java",
    ])
    assert res["success"] is False
    assert "repository" in res["output"]


def test_reactor_compile_check_stale_unrelated_module_classes_do_not_grant_false_success(tmp_path, monkeypatch):
    """(4) core has real, stale .class output; repository (the module that
    actually owns the ONLY candidate file) has none - the fix must not be
    satisfiable by ANY declared module having classes, only the module(s)
    that genuinely own a candidate in this change set."""
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    # repository/target/classes deliberately does not exist.

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, _maven_writes("core/target/classes/Unrelated.class"))   # core's output exists in the view the build ran in; repository's does not
    res = validator.run_compile_check([
        "repository/src/main/java/com/narendra/app/serviceImpl/UserServiceImpl.java",
    ])

    assert res["success"] is False
    assert "repository" in res["output"]


def test_reactor_compile_check_does_not_require_every_module_to_produce_classes(tmp_path, monkeypatch):
    """(2)/(7) A module (api) that owns NO candidate file in this change
    set must never be required to have compiled output - api has no
    target/classes at all here, and is correctly never mentioned, because
    only core (which owns the one real candidate) is checked."""
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)
    # repository/ and api/ have no target/classes at all, and own no candidate.

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, _maven_writes("core/target/classes/Foo.class"))
    res = validator.run_compile_check(["core/src/main/java/com/narendra/app/core/Foo.java"])

    assert res["success"] is True


def test_reactor_compile_check_maven_failure_still_fails(tmp_path, monkeypatch):
    """(5) A genuine Maven compile failure (non-zero exit) must still be a
    failure for a reactor exactly as it already is for a single-module
    project - the reactor-awareness only changes how a returncode==0
    result is double-checked, never bypasses a real Maven-reported error."""
    (tmp_path / "pom.xml").write_text(_REACTOR_POM)

    validator = PolymorphicValidator(str(tmp_path))
    ScriptedRunner(monkeypatch, fail(stderr="COMPILATION ERROR"))
    res = validator.run_compile_check([
        "repository/src/main/java/com/narendra/app/serviceImpl/UserServiceImpl.java",
    ])

    assert res["success"] is False
    assert "COMPILATION ERROR" in res["output"]


def test_java_compile_check_reports_missing_mvn_without_javac_fallback(tmp_path, monkeypatch):
    # Regression test: previously a missing 'mvn' binary was silently logged at
    # debug level and execution fell through to the raw javac fallback below -
    # for any project with real Maven dependencies (e.g. Ignite/Qpid), this
    # produces a misleading "cannot find symbol" error that looks exactly like
    # a code/import bug, sending the retry loop hunting for something that was
    # never there. Confirmed the real failure shape via golden-use-case
    # validation testing.
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    runner = ScriptedRunner(monkeypatch, _missing_binary("mvn"))
    res = validator.run_compile_check(["UserService.java"])

    assert res["success"] is False
    assert "Failed to invoke mvn compile" in res["output"]
    assert "No such file or directory" in res["output"]
    # Must not silently fall through to a javac (or gradle) fallback attempt.
    assert len(runner.commands) == 1


def test_run_pom_validate_catches_wrong_root_element(tmp_path, monkeypatch):
    """Regression test for a real live incident, 2026-08-16 (ignite_qpid_person,
    run b-6): a generated pom.xml with root element <plugin> instead of
    <project> is perfectly well-formed XML - find_structural_corruption()'s
    own check passes it cleanly - and was only ever caught by paying for the
    full compile gate's dependency resolution + javac invocation, after every
    other file in the batch had already been written for nothing. Confirmed
    directly against a real `mvn validate` invocation (not just this mocked
    unit test) before writing this: real Maven's own error text is exactly
    "Expected root element 'project' but found 'plugin'"."""
    (tmp_path / "pom.xml").write_text(
        '<?xml version="1.0"?>\n<plugin xmlns="http://maven.apache.org/POM/4.0.0">'
        "<modelVersion>4.0.0</modelVersion></plugin>\n"
    )
    validator = PolymorphicValidator(str(tmp_path))

    runner = ScriptedRunner(monkeypatch, fail(stdout="[ERROR] Malformed POM ...: Expected root element 'project' but found 'plugin'"), strict=True)
    res = validator.run_pom_validate()

    assert res["success"] is False
    assert "Expected root element 'project' but found 'plugin'" in res["output"]
    assert runner.commands[0][0] == "mvn" and runner.commands[0][-1] == "validate", runner.commands
    assert "-o" in runner.commands[0]          # S7: offline against the contained dependency cache

def test_run_pom_validate_passes_a_valid_pom(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text(
        '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<modelVersion>4.0.0</modelVersion></project>\n"
    )
    validator = PolymorphicValidator(str(tmp_path))

    ScriptedRunner(monkeypatch, ok(), strict=True)
    res = validator.run_pom_validate()

    assert res["success"] is True

def test_run_pom_validate_noop_when_no_pom_exists(tmp_path, monkeypatch):
    # Safe to call unconditionally - never invokes a subprocess if there's
    # genuinely nothing to validate.
    validator = PolymorphicValidator(str(tmp_path))
    runner = ScriptedRunner(monkeypatch, strict=True)
    res = validator.run_pom_validate()
    assert res["success"] is True
    assert not runner.calls

def test_run_pom_validate_reports_missing_mvn_honestly(tmp_path, monkeypatch):
    # Same reasoning as test_java_compile_check_reports_missing_mvn_without_
    # javac_fallback above - a missing 'mvn' binary is a toolchain problem,
    # must be returned as a real failure, not silently treated as "valid."
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    ScriptedRunner(monkeypatch, _missing_binary("mvn"))
    res = validator.run_pom_validate()

    assert res["success"] is False
    assert "Failed to invoke mvn validate" in res["output"]


def test_check_java_toolchain_detects_mismatch(monkeypatch):
    from kriya.tools.validate import check_java_toolchain

    def fake_run(cmd, **kwargs):
        res = MagicMock()
        if cmd[0] == "java":
            res.stdout, res.stderr = "", 'openjdk version "17.0.10" 2024-01-16\n'
        else:
            res.stdout = "Apache Maven 3.9.16\nJava version: 26.0.1, vendor: Homebrew\n"
            res.stderr = ""
        return res

    monkeypatch.setattr("kriya.tools.validate.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("kriya.tools.validate.subprocess.run", fake_run)

    result = check_java_toolchain()
    assert result == {
        "java_found": True, "java_version": "17",
        "mvn_found": True, "mvn_java_version": "26",
        "mismatch": True,
    }


def test_check_java_toolchain_no_mismatch_when_versions_match(monkeypatch):
    from kriya.tools.validate import check_java_toolchain

    def fake_run(cmd, **kwargs):
        res = MagicMock()
        if cmd[0] == "java":
            res.stdout, res.stderr = "", 'openjdk version "17.0.10" 2024-01-16\n'
        else:
            res.stdout = "Apache Maven 3.9.16\nJava version: 17.0.10, vendor: Eclipse Adoptium\n"
            res.stderr = ""
        return res

    monkeypatch.setattr("kriya.tools.validate.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("kriya.tools.validate.subprocess.run", fake_run)

    result = check_java_toolchain()
    assert result["mismatch"] is False


def test_check_java_toolchain_neither_found(monkeypatch):
    from kriya.tools.validate import check_java_toolchain

    monkeypatch.setattr("kriya.tools.validate.shutil.which", lambda name: None)
    result = check_java_toolchain()
    assert result == {
        "java_found": False, "java_version": None,
        "mvn_found": False, "mvn_java_version": None,
        "mismatch": False,
    }


def test_get_pom_dependencies_is_module_level_and_reusable(tmp_path):
    """Promoted from a PolymorphicValidator method to a module-level function so
    callers that need it without constructing a validator (the Developer retry
    loop's pre-generation dependency checklist) can reuse the exact same
    parsing logic, not a re-implementation."""
    pom = tmp_path / "pom.xml"
    pom.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
    </dependencies>
</project>""")
    assert get_pom_dependencies(str(pom)) == ["org.apache.ignite:ignite-core"]

def test_get_pom_dependencies_missing_file_returns_empty():
    assert get_pom_dependencies("/nonexistent/pom.xml") == []

def test_get_pom_own_coordinate_reads_top_level_groupid_artifactid(tmp_path):
    """Must read the PROJECT'S OWN top-level <groupId>/<artifactId> (direct
    children of <project>), not a <dependency>'s - confirmed distinct via a pom
    that has both, to catch a regression that accidentally matched the wrong
    element."""
    pom = tmp_path / "pom.xml"
    pom.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<project>
    <groupId>com.example</groupId>
    <artifactId>ignite-qpid-integration</artifactId>
    <version>1.0-SNAPSHOT</version>
    <dependencies>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
    </dependencies>
</project>""")
    assert get_pom_own_coordinate(str(pom)) == "com.example:ignite-qpid-integration"

def test_get_pom_own_coordinate_missing_file_returns_none():
    assert get_pom_own_coordinate("/nonexistent/pom.xml") is None

def test_java_dependency_regression(tmp_path):
    orig_dir = tmp_path / "orig"
    new_dir = tmp_path / "new"
    orig_dir.mkdir()
    new_dir.mkdir()

    orig_pom_content = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.apache.qpid</groupId>
            <artifactId>qpid-jms-client</artifactId>
            <version>1.9.0</version>
        </dependency>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
    </dependencies>
</project>"""
    (orig_dir / "pom.xml").write_text(orig_pom_content)

    # 1. Test case: dependency missing in new pom.xml
    new_pom_missing = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
    </dependencies>
</project>"""
    (new_dir / "pom.xml").write_text(new_pom_missing)

    validator = PolymorphicValidator(str(new_dir), original_workspace_path=str(orig_dir))
    res = validator.run_compile_check(["pom.xml"])
    assert res["success"] is False
    assert "Dependency regression: The following dependencies were removed from pom.xml: org.apache.qpid:qpid-jms-client" in res["output"]

    # 2. Test case: all dependencies preserved
    new_pom_preserved = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>org.apache.qpid</groupId>
            <artifactId>qpid-jms-client</artifactId>
            <version>1.9.0</version>
        </dependency>
        <dependency>
            <groupId>org.apache.ignite</groupId>
            <artifactId>ignite-core</artifactId>
            <version>2.18.0</version>
        </dependency>
        <dependency>
            <groupId>org.apache.activemq</groupId>
            <artifactId>artemis-server</artifactId>
            <version>2.31.2</version>
        </dependency>
    </dependencies>
</project>"""
    (new_dir / "pom.xml").write_text(new_pom_preserved)

    # F9: this half used to patch ``subprocess.Popen`` (a host-era seam: Maven runs contained now). The mock made the Maven step raise inside the contained
    # launch, the broad ``except`` swallowed it into "Maven did not decide", and the javac no-source success ("No Java files to compile.") made the assertion pass
    # WITHOUT Maven ever having compiled anything - it passed on the very defect F9 closed. Now Maven answers through its documented seam and only a Maven that
    # ran and exited 0 is a pass.
    seen = []

    def maven_ran_and_passed(_validator, goals, cwd, **_kwargs):
        seen.append(list(goals))
        return {"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""}

    with patch.object(PolymorphicValidator, "_run_maven_cmd", new=maven_ran_and_passed):
        res_ok = validator.run_compile_check(["pom.xml"])
    assert res_ok["success"] is True and res_ok["output"] == "Maven compilation succeeded."
    assert seen and seen[0][:2] == ["clean", "compile"], "Maven itself must have been asked to compile"


def test_run_app_sequence_multi_package_test_file_target_hits_module_not_found(tmp_path):
    """Deterministic reproducer for a real live-evidence incident (VER-005/
    RECV-002 E4 campaign, 2026-09-13, see docs/assurance/
    KRIYA_VER005_RECV002_LIVE_EVIDENCE.md, run_id 20260913T230532-ea0f24f7):
    a correct, real multi-package Python candidate (real sibling top-level
    packages, no CLI/service entrypoint) failed terminal verification twice
    live, on two different goals, because RunVerifierAgent's own judgment
    (contrary to its own system prompt's explicit "do NOT return ... a test
    command as proof of observable runtime behavior" instruction) selected a
    test file as the runtime-verification target, and Kriya's execution
    layer then ran it via a bare `python <relative-path>` subprocess - which
    only puts the script's OWN containing directory on sys.path[0], never
    the process cwd/repository root, so any test file importing a sibling
    top-level package fails with ModuleNotFoundError regardless of whether
    that package's own content is correct.

    This test exercises the REAL, non-mocked PolymorphicValidator.
    run_app_sequence() (no model involved - the judge's own selection isn't
    deterministically reproducible; what's pinned here is that WHEN this
    exact command shape occurs against a real multi-package layout, it (a)
    genuinely reproduces the failure and (b) is still correctly classified
    as a verifier-infrastructure failure, not an application-logic defect -
    exactly as observed live, so a real correct candidate is never
    misattributed to the Developer.

    VER-005 implementation (2026-09-13): prevention of this exact incident
    now lives UPSTREAM of run_app_sequence() - see kriya/workflow/
    file_resolution.py::ground_python_runtime_target() and kriya/workflow/
    attempt.py's own two call sites, which deterministically reject a
    test-shaped verifier target before it ever reaches here (proven in
    tests/test_ver005_python_runtime_target_grounding.py). This test is kept
    UNCHANGED and still passes - it documents that IF this exact bad command
    is ever handed to run_app_sequence() directly (e.g. a future bypass of
    the upstream grounding), the underlying OS-level failure mode and its
    correct infrastructure classification are still exactly as described
    above; it is no longer the only thing standing between a bad target and
    a spurious candidate failure."""
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "__init__.py").write_text("")
    (tmp_path / "validation" / "email_rules.py").write_text(
        "def is_valid_email(email):\n"
        "    return bool(email) and '@' in email\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_email_rules.py").write_text(
        "from validation.email_rules import is_valid_email\n"
        "\n"
        "def test_valid_email():\n"
        "    assert is_valid_email('user@example.com')\n"
    )

    validator = PolymorphicValidator(str(tmp_path))
    result = validator.run_app_sequence(
        [[sys.executable, "tests/test_email_rules.py"]], timeout=10
    )

    assert result["success"] is False
    assert "ModuleNotFoundError" in result["output"]
    assert "No module named" in result["output"]
    assert "validation" in result["output"]

    reason = runtime_verification_infrastructure_reason(result)
    assert reason is not None, (
        "a sibling-package ModuleNotFoundError from a direct-script-executed "
        "test file must classify as verifier infrastructure, not application "
        "logic - misclassifying this would wrongly attribute a real "
        "verifier-invocation defect to the Developer's own candidate code"
    )
    assert runtime_application_step_started(result) is False
