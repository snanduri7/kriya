import sys
from unittest.mock import MagicMock, patch

from kriya.config import AppConfig
from kriya.tools.validate import PolymorphicValidator


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

def test_python_compile_check(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    
    # Valid Python
    (tmp_path / "valid.py").write_text("def ok():\n    pass\n")
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

def test_java_ruby_compile_invocation(tmp_path):
    # Test Java compile invocation mocks
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")
    
    validator = PolymorphicValidator(str(tmp_path))
    assert validator.stack == "java"
    
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res
        
        res = validator.run_compile_check(["UserService.java"])
        assert mock_run.called
        assert res["success"] is True
        
        # Test Ruby compile invocation mocks
        (tmp_path / "pom.xml").unlink()
        (tmp_path / "Gemfile").write_text("")
        (tmp_path / "test.rb").write_text("puts 123")
        
        validator_rb = PolymorphicValidator(str(tmp_path))
        assert validator_rb.stack == "ruby"
        
        res_rb = validator_rb.run_compile_check(["test.rb"])
        assert res_rb["success"] is True

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

    with patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file or directory", "mvn")) as mock_run:
        res = validator.run_compile_check(["UserService.java"])

    assert res["success"] is False
    assert "Failed to invoke mvn compile" in res["output"]
    assert "No such file or directory" in res["output"]
    # Must not silently fall through to a javac (or gradle) fallback attempt.
    assert mock_run.call_count == 1


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

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res
        res_ok = validator.run_compile_check(["pom.xml"])
        assert res_ok["success"] is True

def test_sandbox_execution_restricts_subprocess_env_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    validator = PolymorphicValidator(str(tmp_path))

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res
        validator.run_compile_check(["UserService.java"])

        assert mock_run.called
        _, kwargs = mock_run.call_args
        assert "KRIYA_TEST_SECRET" not in kwargs["env"]
        assert kwargs["preexec_fn"] is not None

def test_sandbox_execution_disabled_uses_full_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_TEST_SECRET", "super-secret-value")
    (tmp_path / "pom.xml").write_text("<project></project>")
    (tmp_path / "UserService.java").write_text("class UserService {}")

    cfg = AppConfig()
    cfg.autonomy.sandbox_execution = False
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy)

    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res
        validator.run_compile_check(["UserService.java"])

        _, kwargs = mock_run.call_args
        assert kwargs["env"] is None
        assert kwargs["preexec_fn"] is None

