import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

from kriya.config import AppConfig
from kriya.tools.validate import PolymorphicValidator, get_pom_dependencies, get_pom_own_coordinate


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
    assert "Syntax error" in res2["output"]

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

    assert res["success"] is True
    assert res["timed_out"] is False
    assert "Added Task 1" in res["output"]
    assert "Task 1" in res["output"]
    assert "Step 1/2" in res["output"]
    assert "Step 2/2" in res["output"]

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

def test_run_app_sequence_uses_isolated_venv_interpreter_for_python(tmp_path):
    """Regression test for a real bug found live (2026-08-07,
    django_healthcheck_gap): run_tests() got an isolated, dependency-
    installed venv, but run_app_sequence() (Runtime Verification) still ran
    via sys.executable directly - a goal needing a real third-party package
    hit the identical ModuleNotFoundError one gate later, after the goal
    finally got far enough to reach Runtime Verification at all. Both gates
    must resolve to the SAME interpreter."""
    (tmp_path / "requirements.txt").write_text("django==5.2\n")
    (tmp_path / "manage.py").write_text("print('would run django here')\n")
    venv_python = tmp_path / ".kriya" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "python"

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 0
        install_process.communicate.return_value = ("Successfully installed django", "")

        run_process = MagicMock()
        run_process.returncode = 0
        run_process.communicate.return_value = ("ok", "")

        mock_popen.side_effect = [install_process, run_process]
        res = validator.run_app_sequence([["python", "manage.py", "runserver", "--help"]])

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 2
    run_cmd = mock_popen.call_args_list[1].args[0]
    assert run_cmd[0] == str(venv_python)
    assert run_cmd[0] != sys.executable
    assert run_cmd[1:] == ["manage.py", "runserver", "--help"]

def test_run_app_sequence_reports_pip_install_failure_without_running_commands(tmp_path):
    (tmp_path / "requirements.txt").write_text("this-package-does-not-exist==999.0\n")
    (tmp_path / "manage.py").write_text("pass\n")
    venv_python = tmp_path / ".kriya" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 1
        install_process.communicate.return_value = ("", "ERROR: No matching distribution found")
        mock_popen.return_value = install_process

        res = validator.run_app_sequence([["python", "manage.py", "runserver"]])

    assert res["success"] is False
    assert "pip install" in res["output"]
    # Only the failed install call should have been made - the run command
    # must never execute against a dependency environment known to be broken.
    assert mock_popen.call_count == 1

def test_run_app_sequence_without_requirements_txt_uses_sys_executable(tmp_path):
    # No requirements.txt (e.g. a stdlib-only Python goal) - must behave
    # exactly as before this fix, sys.executable, no venv machinery invoked.
    (tmp_path / "app.py").write_text("print('ok')\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("ok", "")
        mock_popen.return_value = process

        res = validator.run_app_sequence([["python", "app.py"]])

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 1
    run_cmd = mock_popen.call_args_list[0].args[0]
    assert run_cmd[0] == sys.executable

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

def test_python_run_tests_installs_requirements_into_isolated_venv_before_pytest(tmp_path):
    """Regression test for a real bug found live (2026-08-07 eval harness,
    django_healthcheck_gap): a goal needing a real third-party package (e.g.
    Django) failed every attempt with ModuleNotFoundError, since
    PolymorphicValidator ran tests via sys.executable - Kriya's OWN
    interpreter, which only has whatever Kriya itself depends on installed -
    with no per-project dependency install step, unlike Ruby's `bundle
    install` fix. A requirements.txt with real content must get installed
    into an ISOLATED project-local venv (not sys.executable directly, which
    would risk breaking Kriya's own environment) before pytest runs, and
    pytest itself must then run under THAT venv's interpreter."""
    (tmp_path / "requirements.txt").write_text("django==5.2\n")
    (tmp_path / "app.py").write_text("import django\n")
    venv_python = tmp_path / ".kriya" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")  # simulates an already-created venv

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "python"

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 0
        install_process.communicate.return_value = ("Successfully installed django", "")

        pytest_process = MagicMock()
        pytest_process.returncode = 0
        pytest_process.communicate.return_value = ("1 passed", "")

        mock_popen.side_effect = [install_process, pytest_process]
        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 2
    install_cmd = mock_popen.call_args_list[0].args[0]
    pytest_cmd = mock_popen.call_args_list[1].args[0]
    assert install_cmd == [str(venv_python), "-m", "pip", "install", "-q", "-r", str(tmp_path / "requirements.txt"), "pytest"]
    # pytest itself must run under the VENV's interpreter, not sys.executable -
    # installing into an isolated venv is pointless if the test run doesn't
    # actually use it.
    assert pytest_cmd[0] == str(venv_python)
    assert pytest_cmd[0] != sys.executable

def test_python_run_tests_reports_pip_install_failure_without_running_pytest(tmp_path):
    (tmp_path / "requirements.txt").write_text("this-package-does-not-exist==999.0\n")
    (tmp_path / "app.py").write_text("pass\n")
    venv_python = tmp_path / ".kriya" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 1
        install_process.communicate.return_value = ("", "ERROR: No matching distribution found for this-package-does-not-exist==999.0")
        mock_popen.return_value = install_process

        res = validator.run_tests()

    assert res["success"] is False
    assert "pip install" in res["output"]
    assert "this-package-does-not-exist" in res["output"]
    # Only the failed install call should have been made - pytest must never
    # run against a dependency environment known to be broken.
    assert mock_popen.call_count == 1

def test_python_run_tests_skips_venv_without_requirements_txt(tmp_path):
    # No requirements.txt at all (e.g. python_task_tracker/python_greeter,
    # stdlib-only goals) - must reproduce the exact pre-existing behavior,
    # sys.executable, no venv/pip machinery invoked at all.
    (tmp_path / "app.py").write_text("pass\n")
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("1 passed", "")
        mock_popen.return_value = process

        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 1
    cmd = mock_popen.call_args_list[0].args[0]
    assert cmd[0] == sys.executable

def test_python_run_tests_skips_venv_for_comments_only_requirements_txt(tmp_path):
    # A requirements.txt with no real entries isn't worth a venv+pip round
    # trip over - must behave exactly like having no requirements.txt at all.
    (tmp_path / "requirements.txt").write_text("# no external deps needed\n\n")
    (tmp_path / "app.py").write_text("pass\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("1 passed", "")
        mock_popen.return_value = process

        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 1
    cmd = mock_popen.call_args_list[0].args[0]
    assert cmd[0] == sys.executable

def test_python_run_tests_creates_venv_from_scratch_when_not_already_present(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "app.py").write_text("import requests\n")
    venv_python_path = str(tmp_path / ".kriya" / "venv" / "bin" / "python")

    def _popen_side_effect(cmd, **kwargs):
        process = MagicMock()
        process.returncode = 0
        if "venv" in cmd:
            # Real venv creation would produce this file - simulate that side
            # effect so PolymorphicValidator's own os.path.exists check (its
            # actual success signal, not just the mocked returncode) passes.
            os.makedirs(os.path.dirname(venv_python_path), exist_ok=True)
            with open(venv_python_path, "w") as f:
                f.write("#!/bin/sh\n")
            process.communicate.return_value = ("", "")
        elif "pip" in cmd:
            process.communicate.return_value = ("Successfully installed requests", "")
        else:
            process.communicate.return_value = ("1 passed", "")
        return process

    validator = PolymorphicValidator(str(tmp_path))
    assert not os.path.exists(venv_python_path)

    with patch("subprocess.Popen") as mock_popen:
        mock_popen.side_effect = _popen_side_effect
        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 3
    venv_cmd = mock_popen.call_args_list[0].args[0]
    assert venv_cmd == [sys.executable, "-m", "venv", str(tmp_path / ".kriya" / "venv")]

def test_java_ruby_compile_invocation(tmp_path):
    # Test Java compile invocation mocks
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")
    
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    (tmp_path / "target" / "classes").mkdir(parents=True)
    (tmp_path / "target" / "classes" / "UserService.class").write_bytes(b"")

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process

        res = validator.run_compile_check(["UserService.java"])
        assert mock_popen.called
        assert res["success"] is True
        
        # Test Ruby compile invocation mocks
        (tmp_path / "pom.xml").unlink()
        (tmp_path / "Gemfile").write_text("")
        (tmp_path / "test.rb").write_text("puts 123")
        
        validator_rb = PolymorphicValidator(str(tmp_path))
        assert validator_rb.stack == "ruby"
        
        res_rb = validator_rb.run_compile_check(["test.rb"])
        assert res_rb["success"] is True

def test_ruby_run_tests_installs_gems_before_rspec(tmp_path):
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

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 0
        install_process.communicate.return_value = ("Bundle complete.", "")

        rspec_process = MagicMock()
        rspec_process.returncode = 0
        rspec_process.communicate.return_value = ("1 example, 0 failures", "")

        mock_popen.side_effect = [install_process, rspec_process]

        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 2
    first_cmd = mock_popen.call_args_list[0].args[0]
    second_cmd = mock_popen.call_args_list[1].args[0]
    assert first_cmd == ["bundle", "install", "--path", "vendor/bundle"]
    assert second_cmd[:3] == ["bundle", "exec", "rspec"]


def test_java_run_tests_converts_test_file_path_to_class_name_for_maven(tmp_path):
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

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("Tests run: 1", "")
        mock_popen.return_value = process

        res = validator.run_tests(target_test="src/test/java/com/example/ProtocolTest.java")

    assert res["success"] is True
    cmd = mock_popen.call_args_list[0].args[0]
    assert "-Dtest=ProtocolTest" in cmd
    assert not any("src/test/java" in arg for arg in cmd)

def test_java_run_tests_converts_test_file_path_to_class_name_for_gradle(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("BUILD SUCCESSFUL", "")
        mock_popen.return_value = process

        res = validator.run_tests(target_test="src/test/java/com/example/ProtocolTest.java")

    assert res["success"] is True
    cmd = mock_popen.call_args_list[0].args[0]
    assert cmd[-2:] == ["--tests", "ProtocolTest"]

def test_java_run_tests_no_target_test_omits_dtest_flag(tmp_path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        process = MagicMock()
        process.returncode = 0
        process.communicate.return_value = ("Tests run: 3", "")
        mock_popen.return_value = process

        res = validator.run_tests()

    assert res["success"] is True
    cmd = mock_popen.call_args_list[0].args[0]
    assert cmd == ["mvn", "test"]

def test_ruby_run_tests_reports_bundle_install_failure_without_running_rspec(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rspec'\n")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        install_process = MagicMock()
        install_process.returncode = 1
        install_process.communicate.return_value = ("", "Could not find gem 'rspec'.")
        mock_popen.return_value = install_process

        res = validator.run_tests()

    assert res["success"] is False
    assert "bundle install" in res["output"]
    # Only the failed `bundle install` call should have been made - rspec must
    # never run against a gem environment that's known to be broken.
    assert mock_popen.call_count == 1


def test_ruby_run_tests_skips_bundle_install_without_a_gemfile(tmp_path):
    # Stack detection also accepts a bare Rakefile/*.gemspec with no Gemfile -
    # `bundle install` has nothing to act on in that case and must be skipped,
    # not attempted and failed.
    (tmp_path / "Rakefile").write_text("")

    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "ruby"

    with patch("subprocess.Popen") as mock_popen:
        rspec_process = MagicMock()
        rspec_process.returncode = 0
        rspec_process.communicate.return_value = ("0 examples, 0 failures", "")
        mock_popen.return_value = rspec_process

        res = validator.run_tests()

    assert res["success"] is True, res["output"]
    assert mock_popen.call_count == 1
    assert mock_popen.call_args_list[0].args[0][:3] == ["bundle", "exec", "rspec"]


def test_resolve_maven_classpath_writes_and_reads_the_output_file(tmp_path):
    """resolve_maven_classpath() must consume dependency:build-classpath via
    -Dmdep.outputFile, not stdout (which is full of noisy [INFO] lines around
    the actual classpath string) - simulates the real subprocess side effect
    (writing to the output file) since subprocess.Popen itself is mocked."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    def fake_run(cmd, cwd, timeout=300):
        output_flag = next(a for a in cmd if a.startswith("-Dmdep.outputFile="))
        output_file = output_flag.split("=", 1)[1]
        with open(output_file, "w") as fh:
            fh.write("/home/user/.m2/repository/org/apache/ignite/ignite-core/2.18.0/ignite-core-2.18.0.jar\n")
        return {"returncode": 0, "stdout": "", "stderr": ""}

    with patch.object(validator, "_run_cmd_with_timeout", side_effect=fake_run):
        classpath = validator.resolve_maven_classpath()

    assert classpath == "/home/user/.m2/repository/org/apache/ignite/ignite-core/2.18.0/ignite-core-2.18.0.jar"


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


def test_java_compile_check_enables_rawtypes_unchecked_lint_flags(tmp_path):
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
    (tmp_path / "target" / "classes").mkdir(parents=True)
    (tmp_path / "target" / "classes" / "App.class").write_bytes(b"")

    validator = PolymorphicValidator(str(tmp_path))

    mock_process = MagicMock(returncode=0)
    mock_process.communicate.return_value = ("BUILD SUCCESS", "")
    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        res = validator.run_compile_check(["App.java"])

    assert res["success"] is True
    invoked_cmd = mock_popen.call_args.args[0]
    assert "-Dmaven.compiler.showWarnings=true" in invoked_cmd
    assert "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked" in invoked_cmd

def test_java_compile_check_catches_maven_false_positive_when_nothing_actually_compiled(tmp_path):
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
    mock_process = MagicMock(returncode=0)
    mock_process.communicate.return_value = ("BUILD SUCCESS", "")
    with patch("subprocess.Popen", return_value=mock_process):
        res = validator.run_compile_check(["App.java"])

    assert res["success"] is False
    assert "zero .class files" in res["output"]
    assert "sourceDirectory" in res["output"]

def test_java_compile_check_trusts_maven_success_when_something_really_compiled(tmp_path):
    """Sibling of the false-positive regression above: a real compile that
    actually produced .class output must still be trusted as success -
    this check should only catch the "nothing was ever compiled" case."""
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "App.java").write_text("class App {}")
    (tmp_path / "target" / "classes").mkdir(parents=True)
    (tmp_path / "target" / "classes" / "App.class").write_bytes(b"")

    validator = PolymorphicValidator(str(tmp_path))
    mock_process = MagicMock(returncode=0)
    mock_process.communicate.return_value = ("BUILD SUCCESS", "")
    with patch("subprocess.Popen", return_value=mock_process):
        res = validator.run_compile_check(["App.java"])

    assert res["success"] is True

def test_java_compile_check_reports_missing_mvn_without_javac_fallback(tmp_path):
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

    with patch("subprocess.Popen", side_effect=FileNotFoundError(2, "No such file or directory", "mvn")) as mock_popen:
        res = validator.run_compile_check(["UserService.java"])

    assert res["success"] is False
    assert "Failed to invoke mvn compile" in res["output"]
    assert "No such file or directory" in res["output"]
    # Must not silently fall through to a javac (or gradle) fallback attempt.
    assert mock_popen.call_count == 1


def _mock_popen_result(returncode, stdout="", stderr=""):
    mock_process = MagicMock()
    mock_process.communicate.return_value = (stdout, stderr)
    mock_process.returncode = returncode
    return mock_process


def test_run_pom_validate_catches_wrong_root_element(tmp_path):
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

    mock_process = _mock_popen_result(
        1, stdout="[ERROR] Malformed POM ...: Expected root element 'project' but found 'plugin'"
    )
    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        res = validator.run_pom_validate()

    assert res["success"] is False
    assert "Expected root element 'project' but found 'plugin'" in res["output"]
    assert mock_popen.call_args[0][0] == ["mvn", "validate"]

def test_run_pom_validate_passes_a_valid_pom(tmp_path):
    (tmp_path / "pom.xml").write_text(
        '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">'
        "<modelVersion>4.0.0</modelVersion></project>\n"
    )
    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen", return_value=_mock_popen_result(0)):
        res = validator.run_pom_validate()

    assert res["success"] is True

def test_run_pom_validate_noop_when_no_pom_exists(tmp_path):
    # Safe to call unconditionally - never invokes a subprocess if there's
    # genuinely nothing to validate.
    validator = PolymorphicValidator(str(tmp_path))
    with patch("subprocess.Popen") as mock_popen:
        res = validator.run_pom_validate()
    assert res["success"] is True
    assert mock_popen.call_count == 0

def test_run_pom_validate_reports_missing_mvn_honestly(tmp_path):
    # Same reasoning as test_java_compile_check_reports_missing_mvn_without_
    # javac_fallback above - a missing 'mvn' binary is a toolchain problem,
    # must be returned as a real failure, not silently treated as "valid."
    (tmp_path / "pom.xml").write_text("<project></project>")
    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen", side_effect=FileNotFoundError(2, "No such file or directory", "mvn")):
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

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process
        res_ok = validator.run_compile_check(["pom.xml"])
        assert res_ok["success"] is True

def test_sandbox_execution_restricts_subprocess_env_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process
        validator.run_compile_check(["UserService.java"])

        assert mock_popen.called
        _, kwargs = mock_popen.call_args
        assert "KRIYA_TEST_SECRET" not in kwargs["env"]
        assert kwargs["preexec_fn"] is not None

def test_sandbox_execution_disabled_uses_full_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    cfg = AppConfig()
    cfg.autonomy.sandbox_execution = False
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process
        validator.run_compile_check(["UserService.java"])

        _, kwargs = mock_popen.call_args
        assert kwargs["env"] is None
        assert kwargs["preexec_fn"] is None

def test_java_home_override_forces_subprocess_to_the_specified_jdk(tmp_path, monkeypatch):
    """Regression test for a real, live-diagnosed gap: pom.xml's
    maven.compiler.source/target controls what Java LANGUAGE version javac
    targets, not which JDK 'mvn' itself actually runs under - a goal saying
    "targeting Java 17" has no way to make Maven honor that if 'mvn'
    defaults to a different, genuinely incompatible JDK on this machine
    (confirmed live: JDK 26 broke a Qpid Broker-J API call with no
    connection to anything the generated code controls). java_home_override
    must force every subprocess this validator launches onto the specified
    JDK via JAVA_HOME (the same mechanism Maven's own launcher script uses)
    and PATH, on top of whatever env would otherwise apply."""
    monkeypatch.setenv("KRIYA_TEST_MARKER", "still-here")
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    cfg = AppConfig()
    cfg.autonomy.sandbox_execution = False
    validator = PolymorphicValidator(
        str(tmp_path), autonomy_cfg=cfg.autonomy,
        java_home_override="/opt/jdk-17",
    )

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process
        validator.run_compile_check(["UserService.java"])

        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["JAVA_HOME"] == "/opt/jdk-17"
        assert kwargs["env"]["PATH"].startswith("/opt/jdk-17/bin" + os.pathsep)
        # The rest of the inherited environment must survive untouched.
        assert kwargs["env"]["KRIYA_TEST_MARKER"] == "still-here"

def test_java_home_override_also_applies_under_sandbox_execution(tmp_path):
    # Must layer on top of (not bypass) the sandbox-restricted env, same as
    # the unsandboxed case above.
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    validator = PolymorphicValidator(str(tmp_path), java_home_override="/opt/jdk-17")

    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate.return_value = ("", "")
        mock_popen.return_value = mock_process
        validator.run_compile_check(["UserService.java"])

        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["JAVA_HOME"] == "/opt/jdk-17"
        assert kwargs["preexec_fn"] is not None  # sandboxing still applied too

