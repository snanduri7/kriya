"""R3 / owner decision (Option A): a runtime-verification session gets its OWN fresh ephemeral dependency layer and fills it by an explicit registry-scoped acquisition.

The runtime step's acquisition used to be plugin-only and RELIED on an earlier TEST gate having warmed the one persistent per-workspace cache with the project's own
dependencies; with per-scope layers (tests/test_r3_dependency_cache_poisoning.py) that left ``mvn exec:java`` without junit (test scope). The runtime acquisition is now two
registry-scoped phases into the SAME session layer - the named plugins' ``help`` in an empty directory, then ``mvn dependency:resolve`` of the project - and the runtime
command itself runs offline. This module proves it in real containment, on the production path (``run_app_sequence`` -> ``_run_maven_cmd``).
Real Docker daemon and registry access required, skipped otherwise."""
import hashlib
import os
import subprocess

import pytest
from test_r3_dependency_cache_poisoning import _ATTACK_TEST, _POM, _cfg, _docker_reachable, _project

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import NetworkAuthority
from kriya.tools.dependency_execution import classify_maven_offline_failure_text
from kriya.tools.validate import PolymorphicValidator

pytestmark = pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")

# The published sha256 of junit:junit:4.13.2 (Maven Central is immutable; also MEASURED in WAVE2_FINAL_CLOSURE_2026-10-28/PASS_A_R3_REPORT.md).
JUNIT_SHA256 = "8e495b634469d64fb8acfa3495a065cbacc8a0fff55ce1e31007be4c16dc57d3"
RUN = ["mvn", "-q", "exec:java", "-Dexec.mainClass=demo.App"]  # the model's command: no compile phase, needs the exec plugin AND the project's test-scope junit
# A candidate whose TEST sources do not compile: a runtime session that went through test-compile would stop here and call it an acquisition problem.
_BROKEN_TEST = "public class BrokenTest { this is not java }\n"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _runtime_project(root):
    _project(root, "BrokenTest", _BROKEN_TEST)
    app = root / "src" / "main" / "java" / "demo"
    app.mkdir(parents=True)
    (app / "App.java").write_text('package demo;\npublic class App { public static void main(String[] a) { System.out.println("RUNTIME_LAYER_OK"); } }\n')
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=r3@x", "-c", "user.name=r3", "commit", "-qm", "base")
    return root


def _jar_sha(layer):
    for directory, _dirs, files in os.walk(layer):
        if "junit-4.13.2.jar" in files:
            with open(os.path.join(directory, "junit-4.13.2.jar"), "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
    return None


def test_a_runtime_session_fills_its_own_fresh_layer_and_runs_offline(tmp_path):
    from unittest.mock import patch

    state = os.path.realpath(os.environ["KRIYA_STATE_DIR"])
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "pom.xml").write_text(_POM)
    # (1) the HISTORICAL persistent per-workspace cache: junit replaced by bytes that are not a jar. Never consulted, never touched.
    legacy_key = hashlib.sha256(os.path.realpath(str(origin)).encode()).hexdigest()[:16]
    legacy = os.path.join(state, "dependency-cache", "maven", legacy_key, "junit", "junit", "4.13.2", "junit-4.13.2.jar")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "wb") as handle:
        handle.write(b"POISONED-LEGACY")

    def validator(root):
        return PolymorphicValidator(str(root), original_workspace_path=str(origin), autonomy_cfg=_cfg())

    calls = []
    real = PolymorphicValidator._run_cmd_with_timeout

    def spy(self, cmd, cwd, **kwargs):
        result = real(self, cmd, cwd, **kwargs)
        layer = kwargs.get("dependency_cache_path")
        calls.append({"gate": self._gate, "cmd": list(cmd), "network": kwargs.get("network", NetworkAuthority.DENIED), "acquisition": bool(kwargs.get("acquisition")),
                      "layer": layer, "junit": _jar_sha(layer) if layer else None, "rc": result["returncode"],
                      "stdout": result.get("stdout", ""), "stderr": result.get("stderr", "")})
        return result

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=spy):
        # (2) ANOTHER gate: a hostile candidate's test gate rewrites junit inside ITS layer, then the layer is removed.
        attacker = _project(tmp_path / "attacker", "AttackTest", _ATTACK_TEST)
        assert validator(attacker).run_tests()["success"] is True
        attack_calls = list(calls)
        calls.clear()
        # (3) the runtime session of the real candidate (its test sources do not even compile).
        runtime = _runtime_project(tmp_path / "runtime")
        result = validator(runtime).run_app_sequence([RUN], timeout=120)

    assert result["success"] is True and "RUNTIME_LAYER_OK" in result["output"], result["output"][-1500:]
    attack_layers = {c["layer"] for c in attack_calls if c["layer"]}
    runtime_layers = {c["layer"] for c in calls if c["layer"]}
    # a fresh layer for the session, none of them an earlier gate's, none the historical persistent one
    assert len(runtime_layers) == 1 and not runtime_layers & attack_layers
    [layer] = runtime_layers
    assert "/gate-caches/" in layer and "/dependency-cache/" not in layer
    # (4) the project's own dependency was acquired INTO that layer, by the registry-scoped phase, and it is the genuine artifact (not the attacker's, not the legacy bytes)
    resolve = [c for c in calls if c["cmd"][-1] == "dependency:resolve"]
    assert len(resolve) == 1 and resolve[0]["acquisition"] and resolve[0]["network"] is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
    assert resolve[0]["rc"] == 0 and resolve[0]["junit"] == JUNIT_SHA256, (resolve[0]["rc"], resolve[0]["junit"], resolve[0]["stdout"][-800:])
    # (5) the runtime command ran OFFLINE after the acquisition, never with registry authority; the candidate's tests never compiled
    exec_calls = [c for c in calls if c["cmd"][-1].startswith("-Dexec.mainClass") and not c["acquisition"]]
    assert exec_calls and {c["network"] for c in exec_calls} == {NetworkAuthority.DENIED} and exec_calls[-1]["rc"] == 0
    assert all("test-compile" not in c["cmd"] and "testCompile" not in " ".join(c["cmd"]) for c in calls)
    assert "BrokenTest" not in "".join(c["stdout"] + c["stderr"] for c in calls), "the candidate's test sources were compiled for a runtime session"
    # (6) the historical cache is untouched and nothing outlived the session
    with open(legacy, "rb") as handle:
        assert handle.read() == b"POISONED-LEGACY"
    gate_caches = os.path.join(state, "gate-caches")
    assert not os.path.isdir(gate_caches) or os.listdir(gate_caches) == []
    assert not os.path.exists(layer)
    # the attacker's layer held junit bytes that were NOT genuine (the poisoning worked inside ITS scope) - and that did not leak
    assert any(c["junit"] not in (None, JUNIT_SHA256) for c in attack_calls)
    assert all(c["junit"] in (None, JUNIT_SHA256) for c in calls)


def test_a_failed_dependency_acquisition_is_a_typed_no_verdict_never_a_source_defect(tmp_path):
    """A project that declares a dependency that does not exist: the runtime acquisition cannot fetch it, the offline run cannot resolve it, and the result is the
    ``MAVEN_ACQUISITION_INCOMPLETE:`` marker (an infrastructure no-verdict) - with the application and the tests never executed."""
    from kriya.workflow.acceptance import runtime_verification_infrastructure_reason

    pom = _POM.replace("<artifactId>junit</artifactId>", "<artifactId>kriya-definitely-not-a-real-artifact</artifactId>").replace("<groupId>junit</groupId>", "<groupId>org.kriya.nonexistent</groupId>")
    root = _runtime_project(tmp_path / "runtime")
    (root / "pom.xml").write_text(pom)
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci", sandbox_cpu_seconds=240, sandbox_memory_mb=2048)
    result = PolymorphicValidator(str(root), autonomy_cfg=cfg).run_app_sequence([RUN], timeout=120)
    assert result["success"] is False
    assert "RUNTIME_LAYER_OK" not in result["output"]
    assert "MAVEN_ACQUISITION_INCOMPLETE:" in result["output"], result["output"][-1500:]
    assert runtime_verification_infrastructure_reason(result) is not None  # an infrastructure no-verdict, nothing the Developer can repair
    assert classify_maven_offline_failure_text(result["output"]) is not None


_GRADLE_BUILD = ("plugins { id 'java' }\nrepositories { mavenCentral() }\n"
                 "java { sourceCompatibility = JavaVersion.VERSION_17\n       targetCompatibility = JavaVersion.VERSION_17 }\n")


def test_the_gradle_runtime_consumer_mounts_no_dependency_layer_so_r3_changes_nothing_for_it(tmp_path):
    """Gradle runtime verification never had a cache consumer: a Gradle project's classes are built by the compile gate (its own scope, its own layer, its own
    acquisition) and the runtime command (``java`` on ``build/classes``, or the ``gradle`` binary itself) runs through the plain contained runner with NO dependency
    directory and no network. Real containment: the prerequisite compile, then the run."""
    from unittest.mock import patch

    root = tmp_path / "gradle"
    (root / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (root / "settings.gradle").write_text("rootProject.name = 'r3'\n")
    (root / "build.gradle").write_text(_GRADLE_BUILD)
    (root / "src" / "main" / "java" / "demo" / "App.java").write_text(
        'package demo;\npublic class App { public static void main(String[] a) { System.out.println("GRADLE_RUNTIME_OK"); } }\n')
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=r3@x", "-c", "user.name=r3", "commit", "-qm", "base")
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci", sandbox_cpu_seconds=240, sandbox_memory_mb=2048,
                         acquisition_cpu_seconds=240, acquisition_memory_mb=2048)
    calls = []
    real = PolymorphicValidator._run_cmd_with_timeout

    def spy(self, cmd, cwd, **kwargs):
        result = real(self, cmd, cwd, **kwargs)
        calls.append({"cmd": list(cmd), "network": kwargs.get("network", NetworkAuthority.DENIED), "layer": kwargs.get("dependency_cache_path"), "rc": result["returncode"]})
        return result

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=spy):
        result = PolymorphicValidator(str(root), autonomy_cfg=cfg).run_app_sequence([["java", "-cp", "build/classes/java/main", "demo.App"]], timeout=120)
    assert result["success"] is True and "GRADLE_RUNTIME_OK" in result["output"], result["output"][-1500:]
    java = [c for c in calls if c["cmd"][0] == "java"]
    assert len(java) == 1 and java[0]["layer"] is None and java[0]["network"] is NetworkAuthority.DENIED
    builds = [c for c in calls if c["cmd"][0] != "java"]
    assert builds and all(c["layer"] is None or "/gate-caches/" in c["layer"] for c in builds)  # the prerequisite build's layer is a scope layer, never persistent
