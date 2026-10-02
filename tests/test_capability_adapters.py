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

from kriya.capabilities import BUILD_ADAPTERS, JAVA, LANGUAGE_ADAPTERS, MAVEN, build_adapter_for_tool
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
    assert BUILD_ADAPTERS == (MAVEN,) and LANGUAGE_ADAPTERS == (JAVA,)
    assert isinstance(MAVEN, BuildAdapter) and isinstance(JAVA, LanguageAdapter)
    assert (MAVEN.build_system, MAVEN.language, JAVA.language) == ("maven", "java", "java")
    assert all(build_adapter_for_tool(tool) is MAVEN for tool in ("mvn", "mvnw", "mvn.cmd"))
    assert build_adapter_for_tool("gradle") is None  # Gradle is still inline (a later slice)


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

_MOVED_SEAMS = ("run_compile_check", "run_tests", "_detect_stack", "_has_any_java_file", "_java_sources")


def test_the_moved_validator_seams_name_no_maven_detail():
    """The Maven pieces of these seams are the adapter's now; a returning
    pom.xml / mvn literal means a second, divergent copy."""
    import textwrap

    def constants(function):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    maven = {"pom.xml", "mvn", "mvnw", "mvn.cmd", "test"}
    for name in _MOVED_SEAMS:
        found = constants(getattr(PolymorphicValidator, name))
        assert not found & {"pom.xml", "mvn", "mvnw", "mvn.cmd"}, (name, found & maven)
        assert not any(str(value).startswith("-Dtest=") for value in found), name
    assert not constants(validate.gate_output_roots) & {"mvn", "mvnw", "mvn.cmd", "pom.xml"}


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
