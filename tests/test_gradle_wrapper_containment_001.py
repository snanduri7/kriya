"""GRADLE-WRAPPER-CONTAINMENT-001: a supported Gradle project is verifiable in containment through Kriya's authorized
dependency policy; a wrapper that cannot start is an environment outcome, never a compilation failure.

Measured (blind cohort T3, JavaHamcrest, Gradle 8.10.1 wrapper, gradle:8-jdk8 image): every gate ran ``./gradlew`` with
JAVA_TOOL_OPTIONS -Duser.home=/kriya/tmp, no Gradle cache mounted and the network denied; the wrapper tried to download
gradle-8.10.1-bin.zip from services.gradle.org, failed with UnknownHostException at org.gradle.wrapper.Install.forceFetch,
and the adapter typed the trace "Gradle compilation failed" - a new failure family per attempt, budgets reset, the
Developer escalated to the fallback model, five attempts, a correct final candidate rejected. The host's own wrapper
cache held the exact distribution all along (~/.gradle/wrapper/dists/gradle-8.10.1-bin/e90i968nv55tch01zkse4avv3).

Owner-decided order (2026-10-07): Kriya-managed Gradle home -> verified host-cache seed -> one bounded registry-scoped
acquisition (services.gradle.org + plugins.gradle.org approved for the acquisition phase only) -> offline execution.
"""
import hashlib
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest
from test_workflow import _minimal_attempt_ctx

from kriya.capabilities.gradle import GradleBuildAdapter
from kriya.config.config import AutonomyConfig
from kriya.tools import dependency_execution as dep
from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import (
    _GRADLE_IMAGE,
    _SETUP_SCRIPT_TEMPLATE,
    GRADLE_CACHE_MOUNT,
    MAVEN_CACHE_MOUNT,
    _gradle_home_env,
    _select_image_and_cache_mount,
    _toolchain_cache_mount,
)
from kriya.tools.toolchain_identity import ToolchainIdentity
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.attempt import _stop_on_environment_gate_result
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.retry_strategy import handle_attempt_failure
from kriya.workflow.state import GenerationState

T3_URL = "https://services.gradle.org/distributions/gradle-8.10.1-bin.zip"
T3_HASH = "e90i968nv55tch01zkse4avv3"  # MEASURED: the host cache's own directory name for T3_URL
# MEASURED (cohort T3 generate.log): the wrapper's trace before any Gradle code ran.
WRAPPER_TRACE = (
    "Downloading https://services.gradle.org/distributions/gradle-8.10.1-bin.zip\n\n"
    "Exception in thread \"main\" java.net.UnknownHostException: services.gradle.org\n"
    "\tat java.net.AbstractPlainSocketImpl.connect(AbstractPlainSocketImpl.java:220)\n"
    "\tat org.gradle.wrapper.Download.downloadInternal(Download.java:102)\n"
    "\tat org.gradle.wrapper.Install.forceFetch(Install.java:142)\n"
)
MISSING_DEPENDENCY = (
    "FAILURE: Build failed with an exception.\n* What went wrong:\nExecution failed for task ':compileJava'.\n"
    "> Could not resolve all files for configuration ':compileClasspath'.\n"
    "   > Could not resolve org.apache.commons:commons-lang3:3.14.0.\n"
    "      > No cached version of org.apache.commons:commons-lang3:3.14.0 available for offline mode.\n"
)
ORDINARY_FAILURE = "> Task :compileJava FAILED\nBig.java:12: error: cannot find symbol\n"
PROPERTIES = (
    "distributionBase=GRADLE_USER_HOME\ndistributionPath=wrapper/dists\n"
    "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.1-bin.zip\n"
    "networkTimeout=10000\nvalidateDistributionUrl=true\nzipStoreBase=GRADLE_USER_HOME\nzipStorePath=wrapper/dists\n"
)


def write_wrapper(root, properties=PROPERTIES):
    (root / "gradle" / "wrapper").mkdir(parents=True, exist_ok=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(properties)
    (root / "gradlew").write_text("#!/bin/sh\n")
    (root / "build.gradle").write_text("plugins { id 'java' }\n")


def host_cache(home, *, marker=True, extracted=True, zip_bytes=None, dist_hash=T3_HASH):
    dist = home / "wrapper" / "dists" / "gradle-8.10.1-bin" / dist_hash
    (dist / "gradle-8.10.1" / "lib").mkdir(parents=True, exist_ok=True)
    (dist / "gradle-8.10.1" / "bin").mkdir(exist_ok=True)
    if extracted:
        (dist / "gradle-8.10.1" / "lib" / "gradle-core-8.10.1.jar").write_bytes(b"jar")
        (dist / "gradle-8.10.1" / "bin" / "gradle").write_text("#!/bin/sh\n")
    else:
        shutil.rmtree(dist / "gradle-8.10.1")
    if marker:
        (dist / "gradle-8.10.1-bin.zip.ok").write_bytes(b"")
    if zip_bytes is not None:
        (dist / "gradle-8.10.1-bin.zip").write_bytes(zip_bytes)
    return dist


def _contained_validator(tmp_path):
    write_wrapper(tmp_path)
    return PolymorphicValidator(
        str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"))


def _runs(*results):
    """``_run_cmd_with_timeout`` answering these results in order."""
    answers = [{"returncode": rc, "stdout": out, "stderr": ""} for rc, out in results]
    return patch.object(PolymorphicValidator, "_run_cmd_with_timeout", side_effect=answers)


# ---------------------------------------------------------------- the project's own wrapper declaration
def test_01_wrapper_properties_declare_the_distribution_and_gradles_own_hash_names_its_directory(tmp_path):
    write_wrapper(tmp_path)
    distribution = dep.parse_gradle_wrapper_properties(str(tmp_path))
    assert (distribution.url, distribution.name, distribution.version, distribution.sha256) == (
        T3_URL, "gradle-8.10.1-bin", "8.10.1", None)
    assert distribution.url_hash == dep.gradle_distribution_hash(T3_URL) == T3_HASH
    write_wrapper(tmp_path, PROPERTIES + "distributionSha256Sum=ABCDEF0123\n")
    assert dep.parse_gradle_wrapper_properties(str(tmp_path)).sha256 == "abcdef0123"
    # the wrapper would look elsewhere: Kriya does not guess where
    write_wrapper(tmp_path, PROPERTIES.replace("distributionBase=GRADLE_USER_HOME", "distributionBase=PROJECT"))
    assert dep.parse_gradle_wrapper_properties(str(tmp_path)) is None
    write_wrapper(tmp_path, PROPERTIES.replace("gradle-8.10.1-bin.zip", "custom.tgz"))
    assert dep.parse_gradle_wrapper_properties(str(tmp_path)) is None
    assert dep.parse_gradle_wrapper_properties(str(tmp_path / "nowhere")) is None


# ---------------------------------------------------------------- the host-cache seed (identity verified, never by name)
def test_02_the_host_distribution_is_seeded_only_with_its_identity_verified(tmp_path):
    workspace, home, cache = tmp_path / "ws", tmp_path / "home", tmp_path / "cache"
    workspace.mkdir()
    write_wrapper(workspace)
    source = host_cache(home)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["status"] == "seeded" and record["verified"] == ["url_hash_directory", "completion_marker",
                                                                     "version_directory"]
    target = cache / "wrapper" / "dists" / "gradle-8.10.1-bin" / T3_HASH
    assert record["source"] == str(source) and record["target"] == str(target)
    assert (target / "gradle-8.10.1-bin.zip.ok").is_file() and (target / "gradle-8.10.1" / "lib" / "gradle-core-8.10.1.jar").is_file()
    assert not (target / "gradle-8.10.1").is_symlink()
    assert dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))["status"] == "present"
    assert dep.seed_gradle_distribution_from_host(str(tmp_path / "plain"), str(cache))["status"] == "no_wrapper"


@pytest.mark.parametrize("shape, status, reason", [
    (dict(marker=False), "unverifiable", "no gradle-8.10.1-bin.zip.ok completion marker"),
    (dict(extracted=False), "unverifiable", "no extracted gradle-8.10.1/lib"),
    (dict(dist_hash="0000000000000000000000000"), "not_in_host_cache", None),
])
def test_03_an_unverified_host_distribution_is_never_seeded(tmp_path, shape, status, reason):
    workspace, home, cache = tmp_path / "ws", tmp_path / "home", tmp_path / "cache"
    workspace.mkdir()
    write_wrapper(workspace)
    host_cache(home, **shape)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["status"] == status and (reason is None or record["reason"].startswith(reason))
    assert not (cache / "wrapper").exists()


def test_04_a_declared_checksum_is_verified_on_the_zip_or_the_seed_is_refused(tmp_path):
    workspace, home, cache = tmp_path / "ws", tmp_path / "home", tmp_path / "cache"
    workspace.mkdir()
    zip_bytes = b"gradle distribution bytes"
    write_wrapper(workspace, PROPERTIES + f"distributionSha256Sum={hashlib.sha256(zip_bytes).hexdigest()}\n")
    host_cache(home)  # no zip kept (the modern wrapper deletes it after unpacking, MEASURED on this host)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["status"] == "unverifiable" and "keeps no zip" in record["reason"] and not (cache / "wrapper").exists()
    host_cache(home, zip_bytes=b"tampered")
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["status"] == "refused" and "!= declared" in record["reason"] and not (cache / "wrapper").exists()
    host_cache(home, zip_bytes=zip_bytes)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["status"] == "seeded" and record["verified"][-1] == "sha256"


# ---------------------------------------------------------------- what the tool's own text means (classification only)
def test_05_wrapper_start_and_offline_misses_are_missing_dependency_shapes_everything_else_is_ordinary():
    assert dep.gradle_wrapper_start_failed(WRAPPER_TRACE)
    assert dep.classify_gradle_offline_failure_text(WRAPPER_TRACE) is dep.OfflineFailureKind.MISSING_DEPENDENCY
    assert not dep.gradle_wrapper_start_failed(MISSING_DEPENDENCY)
    assert dep.classify_gradle_offline_failure_text(MISSING_DEPENDENCY) is dep.OfflineFailureKind.MISSING_DEPENDENCY
    assert dep.classify_gradle_offline_failure_text(ORDINARY_FAILURE) is dep.OfflineFailureKind.ORDINARY_FAILURE
    assert dep.classify_gradle_offline_failure_text("Plugin [id: 'biz.aQute.bnd.builder', version: '6.4.0'] was not found") \
        is dep.OfflineFailureKind.MISSING_DEPENDENCY


# ---------------------------------------------------------------- the validator's two-phase state machine
def test_06_host_mode_is_a_byte_exact_pass_through(tmp_path):
    write_wrapper(tmp_path)
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig())
    with _runs((0, "")) as run:
        validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path), timeout=120)
    run.assert_called_once_with(["./gradlew", "compileJava"], cwd=str(tmp_path), timeout=120)


def test_07_contained_offline_success_never_acquires_and_mounts_the_kriya_managed_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "no_wrapper"}) as seed, \
            _runs((0, "BUILD SUCCESSFUL")) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert result["returncode"] == 0 and run.call_count == 1 and seed.call_count == 1
    call = run.call_args
    assert call.args[0] == ["./gradlew", "--offline", "--no-daemon", "--console=plain", "compileJava"]
    assert call.kwargs["network"] is NetworkAuthority.DENIED and "acquisition" not in call.kwargs
    cache = call.kwargs["dependency_cache_path"]
    assert cache.startswith(os.path.realpath(str(tmp_path / "state"))) and "/dependency-cache/gradle/" in cache
    assert os.path.isdir(cache) and call.kwargs["dependency_cache_writable"] is True
    assert seed.call_args.args == (str(tmp_path), cache)  # the seed lands in the mounted home, before any run
    assert result["gradle_user_home"] == GRADLE_CACHE_MOUNT and result["gradle_distribution"] == {"status": "no_wrapper"}


def test_08_a_missing_distribution_gets_one_registry_scoped_acquisition_then_the_offline_run_decides(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "not_in_host_cache", "url": T3_URL}), \
            _runs((1, WRAPPER_TRACE), (0, "acquired"), (0, "BUILD SUCCESSFUL")) as run, \
            patch("kriya.tools.validate.log_acquisition_outcome") as logged:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert run.call_count == 3 and result["returncode"] == 0 and result["stdout"] == "BUILD SUCCESSFUL"
    offline1, acquisition, offline2 = run.call_args_list
    assert acquisition.args[0] == ["./gradlew", "--no-daemon", "--console=plain", "compileJava"]  # the same tasks, never --offline
    assert acquisition.kwargs["network"] is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY and acquisition.kwargs["acquisition"] is True
    assert acquisition.kwargs["dependency_cache_path"] == offline1.kwargs["dependency_cache_path"]
    assert offline2.kwargs["network"] is NetworkAuthority.DENIED and offline2.args[0][1] == "--offline"
    assert logged.call_args.args[:2] == ("gradle", "./gradlew compileJava")
    assert "environment_reason_code" not in result


def test_09_a_wrapper_that_still_cannot_start_is_an_environment_outcome_never_a_compile_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    provenance = {"status": "not_in_host_cache", "url": T3_URL}
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value=provenance), \
            _runs((1, WRAPPER_TRACE), (1, "denied"), (1, WRAPPER_TRACE)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert run.call_count == 3 and result["environment_reason_code"] == dep.GRADLE_DISTRIBUTION_UNAVAILABLE
    assert result["stderr"].startswith("GRADLE_DISTRIBUTION_UNAVAILABLE: the Gradle wrapper could not obtain its distribution")
    assert result["gradle_distribution"] is provenance
    # through the adapter: the gate verdict carries the typed code and the provenance, not "Gradle compilation failed"
    with patch.object(PolymorphicValidator, "_run_gradle_cmd", return_value=result):
        verdict = GradleBuildAdapter().compile(validator, [], deadline=None)
    assert verdict["success"] is False and verdict["environment_reason_code"] == dep.GRADLE_DISTRIBUTION_UNAVAILABLE
    assert verdict["output"].startswith("GRADLE_DISTRIBUTION_UNAVAILABLE: Gradle could not be started")
    assert verdict["gradle_distribution"] is provenance and "Gradle compilation failed" not in verdict["output"]


def test_10_a_dependency_still_missing_after_acquisition_is_the_marker_a_code_failure_never_acquires(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "no_wrapper"}), \
            _runs((1, MISSING_DEPENDENCY), (1, "denied"), (1, MISSING_DEPENDENCY)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert run.call_count == 3 and result["stderr"].startswith("GRADLE_ACQUISITION_INCOMPLETE: ")
    assert "environment_reason_code" not in result  # repair-eligible, like Maven's marker
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "no_wrapper"}), \
            _runs((1, ORDINARY_FAILURE)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert run.call_count == 1 and result["stdout"] == ORDINARY_FAILURE and "environment_reason_code" not in result


def test_11_the_adapter_runs_tests_through_the_same_two_phase_command(tmp_path):
    validator = _contained_validator(tmp_path)
    seen = []

    def fake(self, cmd, tasks, cwd, timeout=600, deadline=None):
        seen.append((cmd, tasks))
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    with patch.object(PolymorphicValidator, "_run_gradle_cmd", new=fake):
        GradleBuildAdapter().run_tests(validator, "FooTest")
    assert seen == [("./gradlew", ["test", "--tests", "FooTest"])]


# ---------------------------------------------------------------- attribution: the gate stops the loop, no retry, no escalation
@pytest.mark.asyncio
async def test_12_an_environment_gate_result_is_the_typed_infrastructure_stop(tmp_path):
    state = GenerationState()
    state.attempt_number = 1
    state.last_attempt_mode = "full_set"
    gate = {"success": False, "output": "GRADLE_DISTRIBUTION_UNAVAILABLE: Gradle could not be started\n" + WRAPPER_TRACE,
            "environment_reason_code": dep.GRADLE_DISTRIBUTION_UNAVAILABLE, "gradle_distribution": {"status": "not_in_host_cache"}}
    assert _stop_on_environment_gate_result(state, {"success": False, "output": ORDINARY_FAILURE}, "compile") is None
    with pytest.raises(QualityGateFailure) as raised:
        _stop_on_environment_gate_result(state, gate, "compile")
    failure = raised.value.failure
    assert failure.type == "verification_infrastructure_failure"
    assert failure.message.startswith("VERIFICATION_INFRASTRUCTURE_FAILURE: the compile gate's verification tool could not start "
                                      "(GRADLE_DISTRIBUTION_UNAVAILABLE)")
    assert failure.diagnostics["reason_code"] == dep.GRADLE_DISTRIBUTION_UNAVAILABLE
    assert state.gate_outcomes[-1]["type"] == "verification_infrastructure_failure"
    ctx = _minimal_attempt_ctx(tmp_path, max_retries=4)
    should_break = await handle_attempt_failure(state, ctx, raised.value)
    assert should_break is True
    assert state.environment_failure.startswith("VERIFICATION_INFRASTRUCTURE_FAILURE:")
    assert state.budgets.last_failure_signature[0] == "verification_infrastructure_failure"  # never a "compile" family


# ---------------------------------------------------------------- containment plumbing for Gradle's JVMs
def test_13_gradle_finds_the_mounted_home_and_the_acquisition_proxy_reaches_its_jvms():
    identity = ToolchainIdentity("java", "jdk", "17", "gradle", "8", "gradle:8-jdk17", "build.gradle")
    assert _toolchain_cache_mount(identity) == GRADLE_CACHE_MOUNT
    gradle_profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/w",
                                        dependency_cache_paths=["/host/cache"], toolchain_identity=identity)
    assert _gradle_home_env(gradle_profile, GRADLE_CACHE_MOUNT) == {"GRADLE_USER_HOME": GRADLE_CACHE_MOUNT}
    assert _gradle_home_env(gradle_profile, MAVEN_CACHE_MOUNT) == {}
    bare = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path="/w")
    assert _gradle_home_env(bare, GRADLE_CACHE_MOUNT) == {}
    assert _select_image_and_cache_mount(["./gradlew", "test"]) == (_GRADLE_IMAGE, GRADLE_CACHE_MOUNT)
    assert 'export GRADLE_OPTS="$MAVEN_OPTS"' in _SETUP_SCRIPT_TEMPLATE
    assert 'export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} $MAVEN_OPTS"' in _SETUP_SCRIPT_TEMPLATE
    assert "services.gradle.org" not in _SETUP_SCRIPT_TEMPLATE  # HOW to reach a host only; WHICH hosts is the ACL's


def test_14_the_approved_hosts_are_exact_and_only_the_registry_list_reaches_a_profile():
    hosts = AutonomyConfig().acquisition_registry_hosts
    assert "services.gradle.org" in hosts and "plugins.gradle.org" in hosts and not any("*" in h for h in hosts)
    profile = dep._acquisition_profile("/w", "/c", [], registry_hosts=hosts)
    assert profile.network is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY and profile.network_destinations == tuple(sorted(set(hosts)))
    assert dep._execution_profile("/w", "/c", [], None, None).network is NetworkAuthority.DENIED


# ---------------------------------------------------------------- real containment: the authorized policy verifies a Gradle project
def _docker_reachable() -> bool:
    try:
        return shutil.which("docker") is not None and subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


@pytest.mark.xdist_group("docker")
@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_15_a_gradle_project_with_a_registry_dependency_compiles_offline_after_one_bounded_acquisition(tmp_path, monkeypatch):
    """Real containment (image gradle:8-jdk17, no wrapper: the image's own Gradle): the dependency is not cached, the
    offline run misses it, ONE registry-scoped acquisition through the proxy resolves it from repo.maven.apache.org,
    the offline run then compiles - all under the Kriya-managed Gradle home, with the network denied at execution."""
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "project"
    (project / "src" / "main" / "java" / "demo").mkdir(parents=True)
    (project / "settings.gradle").write_text("rootProject.name = 'demo'\n")
    (project / "build.gradle").write_text(
        "plugins { id 'java' }\nrepositories { mavenCentral() }\n"
        "dependencies { implementation 'org.apache.commons:commons-lang3:3.14.0' }\n"
        "java { sourceCompatibility = JavaVersion.VERSION_17\n       targetCompatibility = JavaVersion.VERSION_17 }\n")
    (project / "src" / "main" / "java" / "demo" / "Demo.java").write_text(
        "package demo;\nimport org.apache.commons.lang3.StringUtils;\n"
        "public class Demo { public static String up(String s) { return StringUtils.upperCase(s); } }\n")
    validator = PolymorphicValidator(str(project), autonomy_cfg=AutonomyConfig(
        contained_execution_required=True, containment_backend="oci", sandbox_cpu_seconds=600, sandbox_memory_mb=2048,
        acquisition_cpu_seconds=600, acquisition_memory_mb=2048))
    assert validator.stack == "java" and validator.toolchain_identity.containment_image == "gradle:8-jdk17"
    calls = []
    real = PolymorphicValidator._run_cmd_with_timeout

    def spy(self, cmd, cwd, **kwargs):
        calls.append((list(cmd), kwargs.get("network"), kwargs.get("acquisition")))
        return real(self, cmd, cwd, **kwargs)

    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=spy):
        verdict = validator.run_compile_check(["src/main/java/demo/Demo.java"])
    assert verdict["success"] is True, verdict["output"]
    networks = [(network, acquisition) for _cmd, network, acquisition in calls]
    assert networks == [(NetworkAuthority.DENIED, None), (NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, True),
                        (NetworkAuthority.DENIED, None)], calls
    assert all(cmd[0] == "gradle" and "--no-daemon" in cmd for cmd, _n, _a in calls)
    home = validator._dependency_cache_dir("gradle")
    assert any("commons-lang3" in root for root, _dirs, _files in os.walk(os.path.join(home, "caches")))


# ---------------------------------------------------------------- review reconciliation (2026-10-08)
LAUNCHER_FRAMES_FAILURE = (
    "FAILURE: Build failed with an exception.\n* What went wrong:\nExecution failed for task ':compileJava'.\n"
    "> Compilation failed; see the compiler error output for details.\n* Exception is:\n"
    "org.gradle.api.tasks.TaskExecutionException: Execution failed for task ':compileJava'.\n"
    "\tat org.gradle.wrapper.BootstrapMainStarter.start(BootstrapMainStarter.java:37)\n"
    "\tat org.gradle.wrapper.WrapperExecutor.execute(WrapperExecutor.java:108)\n"
    "\tat org.gradle.wrapper.GradleWrapperMain.main(GradleWrapperMain.java:67)\n"
)


def test_16_an_ordinary_failure_carrying_the_launcher_frames_is_never_a_distribution_failure(tmp_path, monkeypatch):
    """Review 4.1: the wrapper launcher frames appear in any --stacktrace build error; only the install/download
    stage proves the distribution could not be obtained."""
    assert not dep.gradle_wrapper_start_failed(LAUNCHER_FRAMES_FAILURE)
    assert dep.classify_gradle_offline_failure_text(LAUNCHER_FRAMES_FAILURE) is dep.OfflineFailureKind.ORDINARY_FAILURE
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "no_wrapper"}), \
            _runs((1, LAUNCHER_FRAMES_FAILURE)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path))
    assert run.call_count == 1 and "environment_reason_code" not in result  # no acquisition of candidate code either


def test_17_a_wrapper_failure_under_an_exhausted_deadline_is_still_the_environment_outcome(tmp_path, monkeypatch):
    """Review 4.2: the deadline branches must not fold a wrapper that could not start into a compile failure."""
    import time
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    validator = _contained_validator(tmp_path)
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "not_in_host_cache", "url": T3_URL}), \
            _runs((1, WRAPPER_TRACE)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path), deadline=time.monotonic() - 1)
    assert run.call_count == 1 and result["environment_reason_code"] == dep.GRADLE_DISTRIBUTION_UNAVAILABLE
    with patch.object(dep, "seed_gradle_distribution_from_host", return_value={"status": "no_wrapper"}), \
            _runs((1, MISSING_DEPENDENCY)) as run:
        result = validator._run_gradle_cmd("./gradlew", ["compileJava"], cwd=str(tmp_path), deadline=time.monotonic() - 1)
    assert run.call_count == 1 and result["stderr"].startswith("GRADLE_ACQUISITION_INCOMPLETE:") and "environment_reason_code" not in result


def test_18_the_seed_records_how_strongly_it_verified(tmp_path):
    """Review 4.4: without a declared checksum the seed is shape-verified (Gradle's own layout), never a digest."""
    workspace, home, cache = tmp_path / "ws", tmp_path / "home", tmp_path / "cache"
    workspace.mkdir()
    write_wrapper(workspace)
    host_cache(home)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["verification_strength"] == "shape" and "sha256" not in record["verified"]
    zip_bytes = b"dist"
    write_wrapper(workspace, PROPERTIES + f"distributionSha256Sum={hashlib.sha256(zip_bytes).hexdigest()}\n")
    host_cache(home, zip_bytes=zip_bytes)
    import shutil as _sh
    _sh.rmtree(cache)
    record = dep.seed_gradle_distribution_from_host(str(workspace), str(cache), host_gradle_home=str(home))
    assert record["verification_strength"] == "declared_sha256"


def test_19_the_runtime_verification_table_knows_the_gradle_marker():
    """Review 4.3: the Gradle acquisition-incomplete marker has the same runtime-verification consumer as Maven's."""
    from kriya.workflow.acceptance import runtime_verification_infrastructure_reason
    reason = runtime_verification_infrastructure_reason({"output": "GRADLE_ACQUISITION_INCOMPLETE: offline execution for tasks ['run'] reports a missing dependency"})
    assert reason and reason.startswith("RUNTIME_VERIFICATION_DEPENDENCY_UNAVAILABLE")
