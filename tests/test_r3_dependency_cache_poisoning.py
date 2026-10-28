"""R3 (Wave-2 final closure): candidate-controlled execution must not mutate persistent dependency material that another gate trusts.

MEASURED at f3b7d39 (``WAVE2_FINAL_CLOSURE_2026-10-28/PASS_A_R3_REPORT.md``): an untrusted candidate test rewrote the cached ``junit-4.13.2.jar``
through the read-write cache mount, and the SAME honest failing candidate then reported BUILD SUCCESS in a later gate. This module reproduces that
sequence through the real validator gate entry points (``run_tests`` -> offline attempt -> registry-scoped acquisition -> offline retry), in real
containment, and pins the invariant: the later gate sees genuine dependency bytes, and nothing of the candidate's cache writes outlives its gate.
Real Docker daemon and registry access required, skipped otherwise (a docker-named skip is pinned to the one ``docker`` xdist worker)."""
import os
import shutil
import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001 - an unreachable daemon is the skip condition, whatever the reason
        return False


pytestmark = pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")

_POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
<groupId>r3</groupId><artifactId>r3</artifactId><version>1</version>
<properties><maven.compiler.release>17</maven.compiler.release><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
<dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies>
</project>
"""
# An honest candidate whose only test fails: any later gate that passes it consumed poisoned bytes.
_VICTIM_TEST = ("import org.junit.Test; import static org.junit.Assert.*;\n"
                "public class VictimTest { @Test public void mustFail() { assertTrue(\"honest candidate is broken\", false); } }\n")
# A hostile candidate test: replaces org.junit.Assert in the cached junit jar with a faithful no-op stub (every public static method kept, so the
# runner keeps working) and drops a marker file next to it in the cache.
_ATTACK_TEST = r'''
import org.junit.Test; import static org.junit.Assert.*;
import java.nio.file.*; import java.util.jar.*; import java.util.*; import javax.tools.*; import java.io.*;
public class AttackTest {
  @Test public void poison() throws Exception {
    Path jar = find("junit-4.13.2.jar"), hamcrest = find("hamcrest-core-1.3.jar");
    Path tmp = Files.createTempDirectory("stub");
    Path src = tmp.resolve("org/junit/Assert.java"); Files.createDirectories(src.getParent());
    StringBuilder sb = new StringBuilder("package org.junit; public class Assert { protected Assert(){}\n");
    for (java.lang.reflect.Method m : org.junit.Assert.class.getDeclaredMethods()) {
      int mod = m.getModifiers();
      if (!java.lang.reflect.Modifier.isPublic(mod) || !java.lang.reflect.Modifier.isStatic(mod)) continue;
      Class<?> rt = m.getReturnType();
      sb.append("public static ").append(rt == void.class ? "void" : rt.getCanonicalName()).append(" ").append(m.getName()).append("(");
      Class<?>[] ps = m.getParameterTypes();
      for (int i = 0; i < ps.length; i++) { if (i > 0) sb.append(","); sb.append(ps[i].getCanonicalName()).append(" a").append(i); }
      sb.append("){");
      if (rt != void.class) sb.append(rt.isPrimitive() ? (rt == boolean.class ? "return false;" : "return 0;") : "return null;");
      sb.append("}\n");
    }
    sb.append("}");
    Files.write(src, sb.toString().getBytes("UTF-8"));
    String cp = jar.toString() + ":" + hamcrest.toString();
    if (ToolProvider.getSystemJavaCompiler().run(null, null, null, "-cp", cp, "-d", tmp.toString(), src.toString()) != 0) throw new IllegalStateException("stub");
    Path out = jar.resolveSibling("junit-4.13.2.jar.r3new");
    try (JarFile in = new JarFile(jar.toFile()); JarOutputStream os = new JarOutputStream(Files.newOutputStream(out))) {
      for (Enumeration<JarEntry> e = in.entries(); e.hasMoreElements();) {
        JarEntry je = e.nextElement();
        if (je.getName().equals("org/junit/Assert.class")) continue;
        os.putNextEntry(new JarEntry(je.getName())); try (InputStream is = in.getInputStream(je)) { is.transferTo(os); } os.closeEntry();
      }
      os.putNextEntry(new JarEntry("org/junit/Assert.class")); os.write(Files.readAllBytes(tmp.resolve("org/junit/Assert.class"))); os.closeEntry();
    }
    Files.move(out, jar, StandardCopyOption.REPLACE_EXISTING);
    Files.write(jar.getParent().resolve("R3_MARKER.txt"), "written-by-untrusted-test".getBytes("UTF-8"));
  }
  // the dependency cache is mounted under /kriya/cache/<tool>: Maven's repository layout and Gradle's hashed modules-2 layout are both searched
  static Path find(String name) throws Exception {
    try (java.util.stream.Stream<Path> files = Files.walk(Paths.get("/kriya/cache"))) {
      return files.filter(f -> f.getFileName().toString().equals(name)).findFirst().orElseThrow(() -> new IllegalStateException(name));
    }
  }
}
'''


def _cfg() -> AutonomyConfig:
    return AutonomyConfig(contained_execution_required=True, containment_backend="oci", sandbox_cpu_seconds=240, sandbox_memory_mb=2048)


def _project(root, test_name, test_source):
    root.mkdir(parents=True)
    (root / "pom.xml").write_text(_POM)
    tests = root / "src" / "test" / "java"
    tests.mkdir(parents=True)
    (tests / f"{test_name}.java").write_text(test_source)
    return root


_GRADLE_BUILD = """plugins { id 'java' }
repositories { mavenCentral() }
dependencies { testImplementation 'junit:junit:4.13.2' }
java { sourceCompatibility = JavaVersion.VERSION_17
       targetCompatibility = JavaVersion.VERSION_17 }
test { useJUnit() }
"""


def _gradle_project(root, test_name, test_source):
    root.mkdir(parents=True)
    (root / "settings.gradle").write_text("rootProject.name = 'r3'\n")
    (root / "build.gradle").write_text(_GRADLE_BUILD)
    tests = root / "src" / "test" / "java"
    tests.mkdir(parents=True)
    (tests / f"{test_name}.java").write_text(test_source)
    return root


def _markers(state_root):
    return [os.path.join(d, f) for d, _, fs in os.walk(state_root) for f in fs if f == "R3_MARKER.txt"]


def _assert_nothing_outlives_the_gates(state_root):
    assert _markers(state_root) == [], "the candidate's cache write outlived its gate"
    gate_caches = os.path.join(os.path.realpath(state_root), "gate-caches")
    assert not os.path.isdir(gate_caches) or os.listdir(gate_caches) == [], "an ephemeral dependency layer was left behind"
    leftovers = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, check=True).stdout.split()
    assert [name for name in leftovers if name.startswith(("kriya-oci-", "kriya-acq-", "kriya-proxy"))] == [], leftovers


def test_a_later_maven_gate_never_consumes_dependency_bytes_an_earlier_candidate_wrote(tmp_path):
    """Control, attack, consumer - all three through ``run_tests`` of validators that share one original workspace (so, before the fix, one
    persistent per-workspace cache). The attack runs in the offline execution AND in the registry-scoped acquisition that precedes it (the same goals)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "pom.xml").write_text(_POM)  # the baseline declares the same toolchain as the candidates (else TOOLCHAIN_REQUIREMENT_CONFLICT)
    state_root = os.environ["KRIYA_STATE_DIR"]

    def validator(root):
        return PolymorphicValidator(str(root), original_workspace_path=str(origin), autonomy_cfg=_cfg())

    victim = _project(tmp_path / "victim", "VictimTest", _VICTIM_TEST)
    control = validator(victim).run_tests()
    assert control["success"] is False, ("the honest failing candidate must fail against genuine dependency bytes", control["output"][-800:])

    attacker = _project(tmp_path / "attacker", "AttackTest", _ATTACK_TEST)
    attack = validator(attacker).run_tests()
    assert attack["success"] is True, ("the hostile test must have run (it passes by construction)", attack["output"][-800:])

    consumer = validator(victim).run_tests()
    assert consumer["success"] is False, ("a later gate consumed dependency bytes the candidate rewrote: the honest failing candidate passed",
                                          consumer["output"][-800:])
    _assert_nothing_outlives_the_gates(state_root)


def test_a_later_gradle_gate_never_consumes_dependency_bytes_an_earlier_candidate_wrote(tmp_path):
    """The Gradle equivalent (the Gradle user home was the same persistent writable mount): the poisoned jar sits in Gradle's hashed ``modules-2``."""
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "build.gradle").write_text(_GRADLE_BUILD)
    (origin / "settings.gradle").write_text("rootProject.name = 'r3'\n")
    state_root = os.environ["KRIYA_STATE_DIR"]

    def validator(root):
        return PolymorphicValidator(str(root), original_workspace_path=str(origin), autonomy_cfg=_cfg())

    victim = _gradle_project(tmp_path / "victim", "VictimTest", _VICTIM_TEST)
    control = validator(victim).run_tests()
    assert control["success"] is False, ("the honest failing candidate must fail against genuine dependency bytes", control["output"][-800:])
    attacker = _gradle_project(tmp_path / "attacker", "AttackTest", _ATTACK_TEST)
    attack = validator(attacker).run_tests()
    assert attack["success"] is True, ("the hostile test must have run (it passes by construction)", attack["output"][-800:])
    consumer = validator(victim).run_tests()
    assert consumer["success"] is False, ("a later Gradle gate consumed dependency bytes the candidate rewrote", consumer["output"][-800:])
    _assert_nothing_outlives_the_gates(state_root)


def test_a_later_authority_bundle_run_never_consumes_dependency_bytes_an_earlier_candidate_wrote(tmp_path):
    """The authority/oracle consumer: ``run_authority_bundle`` ran its prepare (registry-scoped) and verify phases against the same persistent cache. An
    honest failing candidate must be an authority FAIL after an attacker candidate ran through the same workspace's oracle."""
    from kriya.workflow.authority_bundle import (
        AUTHORITY_FAILED,
        AUTHORITY_PASSED,
        AuthorityBundle,
        run_authority_bundle,
    )

    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "pom.xml").write_text(_POM)
    state_root = os.environ["KRIYA_STATE_DIR"]
    verify = ("mvn", "-B", "-o", "-Dmaven.repo.local=/kriya/cache/m2", "test")

    def bundle(prepare):
        return AuthorityBundle(
            digest="d" * 64, stored_dir=str(tmp_path / "stored"), source_path="bundle.json", authority_id="a" * 16, goal_sha256="g" * 64,
            requirement_set_sha256="r" * 64, base_revision="b" * 40, assets={}, prepare=prepare, verify=verify, timeout_seconds=300,
            language="java", build_tool="maven", visibility="visible", covers={})

    def run(candidate, prepare):
        return run_authority_bundle(bundle(prepare), str(candidate), state_root=state_root, validator_factory=lambda root: PolymorphicValidator(
            root, original_workspace_path=str(origin), autonomy_cfg=_cfg()))

    victim = _project(tmp_path / "victim", "VictimTest", _VICTIM_TEST)
    attacker = _project(tmp_path / "attacker", "AttackTest", _ATTACK_TEST)
    control = run(victim, ("mvn", "-B", "test-compile"))
    assert control.reason_code == AUTHORITY_FAILED, (control.reason_code, control.reason)
    attack = run(attacker, ("mvn", "-B", "test"))
    assert attack.reason_code == AUTHORITY_PASSED, (attack.reason_code, attack.reason)  # the attacker's own tests pass by construction
    consumer = run(victim, ("mvn", "-B", "test-compile"))
    assert consumer.reason_code == AUTHORITY_FAILED, ("the oracle consumed dependency bytes an earlier candidate wrote", consumer.reason_code, consumer.reason)
    _assert_nothing_outlives_the_gates(state_root)
