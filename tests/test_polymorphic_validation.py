import os
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

    validator = PolymorphicValidator(str(tmp_path))

    mock_process = MagicMock(returncode=0)
    mock_process.communicate.return_value = ("BUILD SUCCESS", "")
    with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
        res = validator.run_compile_check(["App.java"])

    assert res["success"] is True
    invoked_cmd = mock_popen.call_args.args[0]
    assert "-Dmaven.compiler.showWarnings=true" in invoked_cmd
    assert "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked" in invoked_cmd

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

