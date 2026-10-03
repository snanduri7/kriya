"""Capability Adapters R1: Maven and Java behind BuildAdapter / LanguageAdapter.

The first slice moves existing behaviour unchanged (the validator suites are
the equivalence evidence). These tests pin the adapter contracts the move
relies on - the fall-through of an undecided compile, the two distinct Java
source walks - and the ownership: the moved validator seams name no Maven
detail, and no adapter starts a process of its own.
"""
import ast
import inspect
import os
from unittest.mock import patch

import pytest

from kriya.capabilities import (
    BUILD_ADAPTERS,
    GRADLE,
    JAVA,
    JAVAC,
    LANGUAGE_ADAPTERS,
    MAVEN,
    PIP,
    PYTHON,
    build_adapter_for_tool,
)
from kriya.capabilities.ports import BuildAdapter, LanguageAdapter
from kriya.config import AppConfig
from kriya.tools import validate
from kriya.tools.validate import PolymorphicValidator, gate_output_roots

POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId>" \
      "<version>1</version><dependencies>{deps}</dependencies></project>"
DEP = "<dependency><groupId>org.x</groupId><artifactId>{a}</artifactId><version>1</version></dependency>"


def _write(root, rel, text=""):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def _validator(workspace, original=None):
    return PolymorphicValidator(str(workspace), original_workspace_path=str(original) if original else None,
                                autonomy_cfg=AppConfig().autonomy)


def test_the_registry_is_closed_and_typed():
    assert BUILD_ADAPTERS == (MAVEN, GRADLE, PIP) and LANGUAGE_ADAPTERS == (JAVA, PYTHON)  # order = precedence
    assert all(isinstance(adapter, BuildAdapter) for adapter in BUILD_ADAPTERS)
    assert isinstance(JAVA, LanguageAdapter)
    assert (MAVEN.build_system, MAVEN.language, JAVA.language) == ("maven", "java", "java")
    assert (GRADLE.build_system, GRADLE.language) == ("gradle", "java")
    assert all(build_adapter_for_tool(tool) is MAVEN for tool in ("mvn", "mvnw", "mvn.cmd"))
    assert all(build_adapter_for_tool(tool) is GRADLE for tool in ("gradle", "gradlew", "gradle.bat"))
    assert build_adapter_for_tool("javac") is JAVAC and JAVAC not in BUILD_ADAPTERS  # the fallback, never a marker
    assert (JAVAC.build_system, JAVAC.language) == ("javac", "java") and isinstance(JAVAC, BuildAdapter)


def test_maven_output_roots_are_every_module_target(tmp_path):
    _write(tmp_path, "pom.xml")
    _write(tmp_path, "core/pom.xml")
    _write(tmp_path, "docs/readme.md")
    roots = sorted(os.path.relpath(p, tmp_path) for p in gate_output_roots(["mvn", "-q", "test"], str(tmp_path)))
    assert roots == ["core/target", "target"]
    assert gate_output_roots(["mvnw", "compile"], str(tmp_path)) == MAVEN.output_roots(["mvnw"], str(tmp_path))


def test_the_two_java_source_walks_keep_their_own_skip_sets(tmp_path):
    _write(tmp_path, "build/Generated.java")
    _write(tmp_path, "target/Compiled.java")
    assert JAVA.has_sources(str(tmp_path)) is False  # detection ignores build/ and target/
    assert JAVA.source_files(str(tmp_path)) == ["build/Generated.java"]  # enumeration skips only target/
    _write(tmp_path, "src/App.java")
    assert JAVA.has_sources(str(tmp_path)) is True
    assert JAVA.source_files(str(tmp_path)) == ["build/Generated.java", "src/App.java"]


def test_stack_detection_still_reads_the_maven_marker(tmp_path):
    _write(tmp_path, "pom.xml", POM.format(deps=""))
    assert _validator(tmp_path).stack == "java"
    with patch.object(MAVEN, "detects", return_value=False):
        assert _validator(tmp_path).stack != "java"  # the marker is the adapter's


def test_compile_without_a_pom_is_not_decided_by_maven(tmp_path):
    assert MAVEN.compile(_validator(tmp_path), ["App.java"], deadline=None) is None


def test_a_removed_dependency_is_still_a_regression(tmp_path):
    original, candidate = tmp_path / "orig", tmp_path / "cand"
    _write(original, "pom.xml", POM.format(deps=DEP.format(a="kept") + DEP.format(a="gone")))
    _write(candidate, "pom.xml", POM.format(deps=DEP.format(a="kept")))
    result = MAVEN.compile(_validator(candidate, original), ["App.java"], deadline=None)
    assert result["success"] is False
    assert "removed from pom.xml" in result["output"] and "gone" in result["output"] and "kept" not in result["output"]


def test_an_mvn_start_failure_falls_through_exactly_as_before(tmp_path):
    """A generic failure to start mvn is logged and left undecided (the
    validator continues with Gradle / javac); a missing executable is a
    decided failure; a containment setup failure propagates."""
    from kriya.tools.containment import ContainmentSetupError

    _write(tmp_path, "pom.xml", POM.format(deps=""))
    v = _validator(tmp_path)
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=RuntimeError("boom")):
        assert MAVEN.compile(v, ["App.java"], deadline=None) is None
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=FileNotFoundError("mvn")):
        assert MAVEN.compile(v, ["App.java"], deadline=None) == {
            "success": False, "output": "Failed to invoke mvn compile: mvn"}
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=ContainmentSetupError("x")):
        with pytest.raises(ContainmentSetupError):
            MAVEN.compile(v, ["App.java"], deadline=None)


def test_the_maven_test_gate_names_the_bare_test_class(tmp_path):
    _write(tmp_path, "pom.xml", POM.format(deps=""))
    seen = []

    def fake(self, goals, cwd, timeout=300, **_):
        seen.append(goals)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    with patch.object(PolymorphicValidator, "_run_maven_cmd", new=fake):
        result = _validator(tmp_path).run_tests("src/test/java/a/LedgerTest.java")
    assert seen == [["test", "-Dtest=LedgerTest"]] and result["success"] is True


# --- ownership --------------------------------------------------------------------

_MOVED_SEAMS = ("run_compile_check", "run_tests", "_detect_stack", "_has_any_java_file", "_java_sources",
                "_has_any_py_file")


def test_the_moved_validator_seams_name_no_build_system_detail():
    """The Maven, Gradle and pip pieces of these seams are the adapters' now; a
    returning pom.xml / mvn / build.gradle / gradlew / requirements.txt /
    pytest literal means a second, divergent copy."""
    import textwrap

    def constants(function):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    build_literals = {"pom.xml", "mvn", "mvnw", "mvn.cmd",
                      "build.gradle", "build.gradle.kts", "gradle", "gradlew", "./gradlew", "gradle.bat",
                      "compileJava", "--tests",
                      "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
                      "py_compile", "pytest", "py.test", "__pycache__", ".pytest_cache", ".py",
                      "javac", "-proc:none", ".java", "build"}
    for name in _MOVED_SEAMS:
        found = constants(getattr(PolymorphicValidator, name))
        assert not found & build_literals, (name, found & build_literals)
        assert not any(str(value).startswith("-Dtest=") for value in found), name
    assert not constants(validate.gate_output_roots) & build_literals


def test_no_adapter_starts_a_process_of_its_own():
    import kriya.capabilities as package

    root = os.path.dirname(package.__file__)
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(root, name)).read())
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names} | {node.module for node in ast.walk(tree)
                                                 if isinstance(node, ast.ImportFrom) and node.module}
        assert not imported & {"subprocess", "kriya.tools.process", "ProcessController", "asyncio"}, name


def test_a_gradle_project_never_takes_the_maven_test_path(tmp_path):
    _write(tmp_path, "build.gradle", "plugins { id 'java' }\n")
    assert MAVEN.detects(str(tmp_path)) is False
    calls = []
    with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=lambda *a, **k: calls.append(a)), \
         patch.object(PolymorphicValidator, "_run_cmd_with_timeout",
                      return_value={"returncode": 0, "stdout": "BUILD SUCCESSFUL", "stderr": ""}):
        result = _validator(tmp_path).run_tests(None)
    assert calls == [] and result["success"] is True


# --- Gradle (second slice) ---------------------------------------------------------

def _gradle_runs(tmp_path, *, wrapper=False, result=None, side_effect=None, pom=False):
    _write(tmp_path, "build.gradle", "plugins { id 'java' }\n")
    if wrapper:
        _write(tmp_path, "gradlew", "#!/bin/sh\n")
    if pom:
        _write(tmp_path, "pom.xml", POM.format(deps=""))
    seen = []

    def fake(self, cmd, cwd, **_):
        seen.append(list(cmd))
        if side_effect is not None:
            raise side_effect
        return result or {"returncode": 0, "stdout": "BUILD SUCCESSFUL", "stderr": ""}
    return seen, patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=fake)


def test_gradle_detects_the_groovy_root_script_only(tmp_path):
    """R1 behaviour, pinned: a Kotlin-DSL-only root is a recorded capability
    gap (GRADLE-KOTLIN-DSL-001), not detected."""
    _write(tmp_path, "build.gradle.kts")
    assert GRADLE.detects(str(tmp_path)) is False
    _write(tmp_path, "build.gradle")
    assert GRADLE.detects(str(tmp_path)) is True


def test_gradle_output_roots_are_every_project_build_dir_and_the_root_cache(tmp_path):
    _write(tmp_path, "build.gradle")
    _write(tmp_path, "app/build.gradle.kts")
    _write(tmp_path, "docs/readme.md")
    roots = sorted(os.path.relpath(p, tmp_path) for p in gate_output_roots(["./gradlew", "test"], str(tmp_path)))
    assert roots == [".gradle", "app/build", "build"]
    assert gate_output_roots(["gradle", "build"], str(tmp_path)) == GRADLE.output_roots(["gradle"], str(tmp_path))


@pytest.mark.parametrize("wrapper,expected", [(True, "./gradlew"), (False, "gradle")])
def test_gradle_compile_uses_the_project_wrapper_when_it_ships_one(tmp_path, wrapper, expected):
    seen, patched = _gradle_runs(tmp_path, wrapper=wrapper)
    with patched:
        result = _validator(tmp_path).run_compile_check(["src/main/java/App.java"])
    assert seen == [[expected, "compileJava"]]
    assert result["success"] is True and "Gradle compilation succeeded." in result["output"]


def test_a_gradle_compile_failure_is_decided_with_its_output(tmp_path):
    seen, patched = _gradle_runs(tmp_path, result={"returncode": 1, "stdout": "e: App.java:3", "stderr": "FAILED"})
    with patched:
        result = _validator(tmp_path).run_compile_check(["App.java"])
    assert seen == [["gradle", "compileJava"]]
    assert result["success"] is False and "Gradle compilation failed:\ne: App.java:3\nFAILED" in result["output"]


def test_a_gradle_start_failure_behaves_exactly_as_before(tmp_path):
    """A missing executable is a decided failure (never the misleading javac
    fallback); a containment setup failure propagates; any other failure to
    start is left undecided."""
    from kriya.tools.containment import ContainmentSetupError

    _, patched = _gradle_runs(tmp_path, wrapper=True, side_effect=FileNotFoundError("gradlew"))
    with patched:
        assert GRADLE.compile(_validator(tmp_path), ["App.java"], deadline=None) == {
            "success": False, "output": "Failed to invoke ./gradlew compileJava: gradlew"}
    _, patched = _gradle_runs(tmp_path, side_effect=ContainmentSetupError("x"))
    with patched, pytest.raises(ContainmentSetupError):
        GRADLE.compile(_validator(tmp_path), ["App.java"], deadline=None)
    _, patched = _gradle_runs(tmp_path, side_effect=RuntimeError("boom"))
    with patched:
        assert GRADLE.compile(_validator(tmp_path), ["App.java"], deadline=None) is None
    assert GRADLE.compile(_validator(tmp_path / "empty"), ["App.java"], deadline=None) is None


def test_the_gradle_test_gate_names_the_bare_test_class(tmp_path):
    seen, patched = _gradle_runs(tmp_path, wrapper=True)
    with patched:
        result = _validator(tmp_path).run_tests("src/test/java/a/LedgerTest.java")
    assert seen == [["./gradlew", "test", "--tests", "LedgerTest"]] and result["success"] is True
    seen, patched = _gradle_runs(tmp_path)
    with patched:
        _validator(tmp_path).run_tests(None)
    assert seen == [["./gradlew", "test"]]


def test_a_workspace_declaring_both_builds_stays_maven(tmp_path):
    """Precedence is the registry order: Maven decides compile and test."""
    maven, gradle = [], []
    seen, patched = _gradle_runs(tmp_path, pom=True)

    def fake_mvn(self, goals, cwd, timeout=300, **_):
        maven.append(goals)
        return {"returncode": 0, "stdout": "ok", "stderr": ""}
    with patched, patch.object(PolymorphicValidator, "_run_maven_cmd", new=fake_mvn):
        v = _validator(tmp_path)
        assert v.stack == "java"
        v.run_tests(None)
        v.run_compile_check(["pom.xml"])
    gradle.extend(seen)
    assert maven == [["test"], ["clean", "compile", "-Dmaven.compiler.showWarnings=true",
                                "-Dmaven.compiler.compilerArgument=-Xlint:rawtypes,unchecked"]]
    assert gradle == []


def test_stack_detection_reads_the_gradle_marker_from_the_adapter(tmp_path):
    _write(tmp_path, "build.gradle")
    assert _validator(tmp_path).stack == "java"
    with patch.object(GRADLE, "detects", return_value=False):
        assert _validator(tmp_path).stack != "java"


def test_the_toolchain_fact_gate_reads_the_build_adapters(tmp_path):
    from kriya.workflow.toolchain import _goal_or_repo_targets_java

    _write(tmp_path, "build.gradle")
    assert _goal_or_repo_targets_java("add a feature", str(tmp_path)) is True
    with patch.object(GRADLE, "detects", return_value=False):
        assert _goal_or_repo_targets_java("add a feature", str(tmp_path)) is False
    assert _goal_or_repo_targets_java("a gradle build", str(tmp_path / "none")) is True


# --- Python / pip (third slice) ---------------------------------------------------------

@pytest.mark.parametrize("marker", ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile"])
def test_every_python_marker_is_the_pip_adapters(tmp_path, marker):
    _write(tmp_path, marker)
    assert PIP.detects(str(tmp_path)) and _validator(tmp_path).stack == "python"
    with patch.object(PIP, "detects", return_value=False):
        # nothing else names the marker (setup.py is itself a source the language adapter finds)
        assert _validator(tmp_path).stack == ("python" if marker.endswith(".py") else "unknown")


def test_a_marker_free_python_source_is_found_by_the_language_adapter(tmp_path):
    _write(tmp_path, "venv/lib/site.py")
    _write(tmp_path, "build/gen.py")
    assert PYTHON.has_sources(str(tmp_path)) is False  # skipped directories only
    assert _validator(tmp_path).stack == "unknown"
    _write(tmp_path, "app/main.py")
    assert PYTHON.has_sources(str(tmp_path)) is True and _validator(tmp_path).stack == "python"
    assert PYTHON.source_files(str(tmp_path)) == ["app/main.py"]


def test_a_python_marker_never_outranks_a_java_build(tmp_path):
    _write(tmp_path, "pom.xml", POM.format(deps=""))
    _write(tmp_path, "requirements.txt")
    assert _validator(tmp_path).stack == "java"


def test_pip_output_roots_are_every_source_dirs_pycache_and_the_pytest_cache(tmp_path):
    _write(tmp_path, "pkg/mod.py")
    _write(tmp_path, "docs/readme.md")
    expected = sorted([os.path.join(str(tmp_path), "pkg", "__pycache__"), os.path.join(str(tmp_path), ".pytest_cache")])
    for tool in ("python3", "python3.12", "python", "pytest", "py.test"):
        assert build_adapter_for_tool(tool) is PIP
        assert sorted(gate_output_roots([tool, "-m", "pytest"], str(tmp_path))) == expected
    assert build_adapter_for_tool("pythonista") is PIP  # the inline prefix rule, unchanged
    assert build_adapter_for_tool("pip") is None and gate_output_roots(["pip", "install"], str(tmp_path)) == []


def test_the_pip_compile_gate_reports_a_syntax_error_and_passes_valid_source(tmp_path):
    _write(tmp_path, "requirements.txt")
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "bad.py", "def f(:\n")
    v = _validator(tmp_path)
    assert v.run_compile_check(["ok.py"]) == {"success": True, "output": "Python files compiled successfully."}
    bad = v.run_compile_check(["ok.py", "bad.py", "missing.py"])
    assert bad["success"] is False and bad["output"].startswith("Syntax error in bad.py line 1")


def test_the_contained_compile_gate_runs_py_compile_through_the_validator(tmp_path):
    _write(tmp_path, "requirements.txt")
    _write(tmp_path, "pkg/a.py", "x = 1\n")
    v = _validator(tmp_path)
    v.autonomy_cfg.contained_execution_required = True
    seen = []

    def fake(self, cmd, cwd, **_):
        seen.append(cmd)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=fake):
        assert v.run_compile_check(["pkg/a.py", "notes.txt", "gone.py"])["success"] is True
        assert v.run_compile_check(["notes.txt"]) == {"success": True, "output": "No Python files to compile."}
    assert seen == [["python3", "-m", "py_compile", "pkg/a.py"]]


def test_the_pytest_gate_passes_each_target_as_its_own_argument(tmp_path):
    _write(tmp_path, "requirements.txt")
    _write(tmp_path, "src/x.py")
    v = _validator(tmp_path)
    seen = []

    def fake(self, cmd, cwd, **_):
        seen.append(cmd)
        return {"returncode": 5, "stdout": "no tests ran", "stderr": ""}

    with patch.object(PolymorphicValidator, "_resolve_python_interpreter", return_value=("py-under-test", None)), \
         patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=fake):
        result = v.run_tests(["tests/test_a.py", "tests/test b.py"])
    assert result["success"] is True  # pytest exit 5 (no tests) passes, as before
    cmd = seen[0]
    assert cmd[0] == "py-under-test" and cmd[-3:] == ["--", "tests/test_a.py", "tests/test b.py"]
    assert repr([str(tmp_path), os.path.join(str(tmp_path), "src")]) in cmd[2]
    with patch.object(PolymorphicValidator, "_resolve_python_interpreter", return_value=("x", "pip install failed")):
        assert v.run_tests(None) == {"success": False, "output": "pip install failed"}


# --- javac fallback (Capability Adapters R1, javac slice) ---------------------


def _javac_runs(result=None, side_effect=None):
    seen = []

    def run(_validator, cmd, cwd=None, **_kwargs):
        seen.append((list(cmd), cwd))
        if side_effect is not None:
            raise side_effect
        return dict(result or {"returncode": 0, "stdout": "", "stderr": ""})

    return seen, patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=run)


def test_a_plain_java_workspace_compiles_with_the_javac_fallback(tmp_path):
    _write(tmp_path, "src/App.java", "class App {}")
    _write(tmp_path, "src/Util.java", "class Util {}")
    seen, patched = _javac_runs()
    with patched:
        result = _validator(tmp_path).run_compile_check(["src/App.java", "src/Missing.java", "README.md",
                                                         "src/Util.java"])
    build = os.path.join(str(tmp_path), "build")
    assert seen == [(["javac", "-proc:none", "-d", build, os.path.join(str(tmp_path), "src/App.java"),
                      os.path.join(str(tmp_path), "src/Util.java")], str(tmp_path))]
    assert os.path.isdir(build)
    assert result["success"] is True and result["output"] == "Java classes compiled successfully."


def test_no_existing_java_file_compiles_nothing(tmp_path):
    _write(tmp_path, "src/App.java", "class App {}")
    seen, patched = _javac_runs()
    with patched:
        result = _validator(tmp_path).run_compile_check(["src/Gone.java", "notes.txt"])
    assert seen == [] and result == {"success": True, "output": "No Java files to compile."}
    assert not os.path.exists(os.path.join(str(tmp_path), "build"))


def test_a_javac_failure_is_decided_with_the_enriched_compiler_output(tmp_path):
    _write(tmp_path, "App.java", "class App {")
    seen, patched = _javac_runs({"returncode": 1, "stdout": "", "stderr": "App.java:1: error: reached end"})
    with patched, patch("kriya.tools.resolver.enrich_java_compiler_errors",
                        side_effect=lambda text, allow_external_lookup: text + f"\n[enriched {allow_external_lookup}]"):
        result = _validator(tmp_path).run_compile_check(["App.java"])
    assert result["success"] is False
    assert result["output"].startswith("Java compilation failed:\nApp.java:1: error: reached end")
    assert result["output"].endswith("[enriched False]")  # local_only egress: no external lookup
    with patched, patch("kriya.tools.resolver.enrich_java_compiler_errors", side_effect=RuntimeError("down")):
        unenriched = _validator(tmp_path).run_compile_check(["App.java"])
    assert unenriched["success"] is False and unenriched["output"] == \
        "Java compilation failed:\nApp.java:1: error: reached end"


def test_javac_start_failures_behave_exactly_as_before(tmp_path):
    from kriya.tools.containment import ContainmentSetupError

    _write(tmp_path, "App.java", "class App {}")
    _, patched = _javac_runs(side_effect=FileNotFoundError("javac"))
    with patched:
        assert _validator(tmp_path).run_compile_check(["App.java"]) == {
            "success": False, "output": "Javac compilation tool invocation failed: javac"}
    _, patched = _javac_runs(side_effect=ContainmentSetupError("no backend"))
    with patched, pytest.raises(ContainmentSetupError):
        _validator(tmp_path).run_compile_check(["App.java"])


@pytest.mark.parametrize("marker", ["pom.xml", "build.gradle"])
def test_a_maven_or_gradle_workspace_never_reaches_javac(tmp_path, marker):
    _write(tmp_path, marker, POM.format(deps="") if marker == "pom.xml" else "")
    _write(tmp_path, "src/main/java/App.java", "class App {}")
    seen, patched = _javac_runs()
    with patched, patch.object(PolymorphicValidator, "_run_maven_cmd",
                               return_value={"returncode": 0, "stdout": "", "stderr": ""}):
        _validator(tmp_path).run_compile_check(["src/main/java/App.java"])
    assert not any(cmd[0] == "javac" for cmd, _cwd in seen)
    assert JAVAC.detects(str(tmp_path)) is False


def test_javac_runs_only_after_every_build_adapter_left_the_compile_undecided(tmp_path):
    """Maven, then Gradle, then javac: an mvn start failure (undecided) falls
    through to javac, exactly as before."""
    _write(tmp_path, "pom.xml", POM.format(deps=""))
    _write(tmp_path, "App.java", "class App {}")
    order = []
    seen, patched = _javac_runs()
    real_maven, real_gradle = MAVEN.compile, GRADLE.compile

    def maven(*args, **kwargs):
        order.append("maven")
        with patch.object(PolymorphicValidator, "_run_maven_cmd", side_effect=RuntimeError("boom")):
            return real_maven(*args, **kwargs)

    def gradle(*args, **kwargs):
        order.append("gradle")
        return real_gradle(*args, **kwargs)

    with patched, patch.object(MAVEN, "compile", new=maven), patch.object(GRADLE, "compile", new=gradle):
        result = _validator(tmp_path).run_compile_check(["App.java"])
    assert order == ["maven", "gradle"] and [cmd[0] for cmd, _ in seen] == ["javac"]
    assert result["success"] is True


def test_javac_output_roots_are_exactly_its_destination(tmp_path):
    assert gate_output_roots(["javac", "-proc:none", "-d", "/ws/build", "/ws/A.java"], "/ws") == ["/ws/build"]
    assert gate_output_roots(["javac", "-d", "out", "A.java"], "/ws") == ["out"]
    assert gate_output_roots(["javac", "A.java"], "/ws") == []
    assert gate_output_roots(["javac", "A.java", "-d"], "/ws") == []  # a trailing -d names no destination


def test_a_plain_java_workspace_has_no_test_gate(tmp_path):
    _write(tmp_path, "src/App.java", "class App {}")
    assert _validator(tmp_path).run_tests("src/AppTest.java") == {
        "success": True, "output": "No Java test config found (pom.xml/gradle). Skipping."}
