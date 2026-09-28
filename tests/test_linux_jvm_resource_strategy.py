"""LINUX-JVM-RLIMIT-AS-001: a JVM-backed host process is bounded by explicit
JVM memory options derived from the sandbox budget, never by RLIMIT_AS (a
JVM's address-space reservation exceeds any useful RLIMIT_AS on Linux:
"There is insufficient memory for the Java Runtime Environment to continue",
hosted run 36368006232). Every other command keeps RLIMIT_AS; CPU time, the
timeout process-tree kill and containment are unchanged; the strategy is
recorded as execution evidence.
"""
import os
import resource
import shutil
import sys
import time

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import (
    ContainmentProfile,
    NetworkAuthority,
    NullContainmentBackend,
    ResourceLimitSetupError,
    TrustClass,
)
from kriya.tools.process import ProcessController
from kriya.tools.sandbox import (
    ADDRESS_SPACE,
    JAVA_TOOL_OPTIONS,
    JVM_HEAP,
    JVM_MIN_BUDGET_MB,
    UNBOUNDED,
    resource_plan,
)
from kriya.tools.validate import PolymorphicValidator, execution_evidence


def _applied_limits(plan, monkeypatch):
    """The rlimits the plan's preexec_fn sets, captured in-process."""
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, value: calls.append((which, value)))
    preexec = plan.preexec_fn()
    if preexec is not None:
        preexec()
    return dict(calls)


def test_a_non_jvm_command_keeps_rlimit_as(monkeypatch):
    plan = resource_plan(["python3", "-c", "pass"], 60, 1024)
    assert plan.strategy == ADDRESS_SPACE and plan.jvm_options == ()
    limits = _applied_limits(plan, monkeypatch)
    assert limits[resource.RLIMIT_AS] == (1024 * 1024 * 1024,) * 2
    assert limits[resource.RLIMIT_CPU] == (60, 60)
    assert plan.apply_env({"PATH": "/bin"}) == {"PATH": "/bin"}


@pytest.mark.parametrize("command,language", [
    (["mvn", "-q", "compile"], None),
    (["/opt/gradle/bin/gradle", "test"], None),
    (["./mvnw", "test"], None),
    (["java", "-jar", "app.jar"], None),
    (["python3", "-m", "something"], "java"),  # the typed toolchain decides first
])
def test_a_jvm_execution_gets_no_rlimit_as_but_bounded_jvm_options(monkeypatch, command, language):
    plan = resource_plan(command, 60, 4096, language=language)
    assert plan.strategy == JVM_HEAP
    limits = _applied_limits(plan, monkeypatch)
    assert resource.RLIMIT_AS not in limits and limits[resource.RLIMIT_CPU] == (60, 60)
    assert plan.jvm_options == ("-Xmx2048m", "-XX:MaxMetaspaceSize=512m", "-XX:MaxDirectMemorySize=512m")
    env = plan.apply_env({"PATH": "/bin", JAVA_TOOL_OPTIONS: "-Xmx99g -Dkeep=1"})
    # Existing options are kept; Kriya's come last and so win.
    assert env[JAVA_TOOL_OPTIONS] == "-Xmx99g -Dkeep=1 -Xmx2048m -XX:MaxMetaspaceSize=512m -XX:MaxDirectMemorySize=512m"


def test_shell_text_never_classifies_a_command_as_a_jvm():
    """A crafted shell command must not shed its RLIMIT_AS by naming java."""
    assert resource_plan(["/bin/sh", "-c", "java -version; exec evil"], 60, 1024).strategy == ADDRESS_SPACE


@pytest.mark.parametrize("memory_mb,command", [(0, ["python3"]), (-5, ["mvn"]), (JVM_MIN_BUDGET_MB - 1, ["mvn"])])
def test_an_unenforceable_budget_is_refused_never_run_unbounded(memory_mb, command):
    with pytest.raises(ResourceLimitSetupError):
        resource_plan(command, 60, memory_mb)
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=os.getcwd(),
                                 network=NetworkAuthority.UNRESTRICTED, cpu_seconds=60, memory_mb=memory_mb)
    with pytest.raises(ResourceLimitSetupError):
        NullContainmentBackend().prepare(profile, command)


def test_no_memory_budget_keeps_the_existing_unlimited_contract(monkeypatch):
    plan = resource_plan(["mvn"], 60, None)
    assert plan.strategy == UNBOUNDED and plan.jvm_options == ()
    assert _applied_limits(plan, monkeypatch) == {resource.RLIMIT_CPU: (60, 60)}


# --- Real JVMs ---------------------------------------------------------------------------

JAVA = shutil.which("java")
needs_java = pytest.mark.skipif(JAVA is None, reason="JDK not on PATH")

PARENT = """
public class Parent {
    public static void main(String[] args) throws Exception {
        System.out.println("parent=" + Runtime.getRuntime().maxMemory() / (1024 * 1024));
        Process child = new ProcessBuilder("java", args[0]).inheritIO().start();
        System.exit(child.waitFor());
    }
}
"""
CHILD = """
public class Child {
    public static void main(String[] args) {
        System.out.println("child=" + Runtime.getRuntime().maxMemory() / (1024 * 1024));
    }
}
"""


def _jvm_profile(tmp_path, memory_mb=1024, cpu_seconds=120):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(tmp_path),
                              network=NetworkAuthority.UNRESTRICTED, cpu_seconds=cpu_seconds, memory_mb=memory_mb)


@needs_java
def test_a_forked_jvm_observes_the_bound_and_the_evidence_reports_it(tmp_path):
    (tmp_path / "Parent.java").write_text(PARENT)
    (tmp_path / "Child.java").write_text(CHILD)
    result = ProcessController().run(
        ["java", "Parent.java", str(tmp_path / "Child.java")], cwd=str(tmp_path), timeout=120,
        containment_profile=_jvm_profile(tmp_path), containment_backend=NullContainmentBackend(),
    )
    assert result.returncode == 0, result.stderr
    maxima = dict(line.split("=") for line in result.stdout.split())
    # -Xmx512m for a 1024 MB budget, in the launcher AND the JVM it forked.
    assert 0 < int(maxima["parent"]) <= 512 and 0 < int(maxima["child"]) <= 512
    assert result.resources["strategy"] == JVM_HEAP and result.resources["memory_budget_mb"] == 1024
    assert result.resources["address_space_limit_mb"] is None
    assert "-Xmx512m" in result.resources["jvm_options"]
    assert result.to_dict()["resources"] == result.resources


SLEEPER = """
public class Sleeper {
    public static void main(String[] args) throws Exception {
        Process child = new ProcessBuilder("sleep", "300").start();
        java.nio.file.Files.writeString(java.nio.file.Path.of(args[0]), Long.toString(child.pid()));
        Thread.sleep(300_000);
    }
}
"""


@needs_java
def test_the_timeout_still_kills_the_jvm_process_tree(tmp_path):
    (tmp_path / "Sleeper.java").write_text(SLEEPER)
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    result = ProcessController().run(
        ["java", "Sleeper.java", str(pid_file)], cwd=str(tmp_path), timeout=15,
        containment_profile=_jvm_profile(tmp_path), containment_backend=NullContainmentBackend(),
    )
    assert result.timeout and time.monotonic() - started < 60
    assert pid_file.exists(), result.stderr
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"the JVM's child {child} survived the timeout")


def test_the_validator_records_the_strategy_with_the_gate_evidence(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(sandbox_memory_mb=2048))
    assert validator.stack == "java"
    result = validator._run_cmd_with_timeout([sys.executable, "-c", "print('ok')"], cwd=str(tmp_path))
    assert result["returncode"] == 0
    assert result["resources"]["strategy"] == JVM_HEAP and result["resources"]["memory_budget_mb"] == 2048
    assert execution_evidence(result)["resources"] == result["resources"]
    python_validator = PolymorphicValidator(str(tmp_path / "py"), autonomy_cfg=AutonomyConfig())
    assert python_validator.host_resource_plan(["python3"]).strategy == ADDRESS_SPACE
    off = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(sandbox_execution=False))
    assert off.host_resource_plan(["mvn"]) is None and off.build_subprocess_env_and_preexec(["mvn"]) == (None, None)
