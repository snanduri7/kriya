"""D2 EXEC-LAYER rebuild S4.1: managed-service verification in ONE exact-bound view.

MEASURED before the change (2026-10-25, scratchpad s4/c5..c8), with the REAL service lifecycle:

1. The service ran in the shared candidate worktree, so an ignored file there decided the verdict: a tracked ``server.py`` that imports a module no
   commit holds crashes (honest FAIL), and with an ignored ``helper_generated.py`` lying in the worktree it PASSED - on the host and in the container.
2. Its contained ``mvn package`` had no dependency cache and no network (``network=DENIED``, no cache mount): ``Could not transfer artifact ... from/to
   central`` on every run, so a contained managed service built from source could never be verified.
3. The service inherited Kriya's whole environment on the host (a ``KRIYA_*`` variable reached it); contained, only the allowlist did.

Now the build, the service, its readiness checks and its probes share one view (kriya/tools/gate_view.py, the existing owner), alive until the
process is gone and its output collected; a contained build takes the validator's contained Maven path (registry-scoped acquisition). Finding 3 is
classified, not changed: with containment mandatory the host path is not reachable (``test_*_host_path_*`` below).
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest
from _containment_standin import MountAwareStandIn
from test_oi142_gate_view import _git, _views
from test_runtime_verification import JUDGE_SERVICE
from test_s3_execution_views import _contained, _docker_reachable

from kriya.config.config import AutonomyConfig
from kriya.tools import service_runtime
from kriya.tools.containment import ContainmentProfile, ExternalExecutionRefused, NetworkAuthority, TrustClass
from kriya.tools.process import ProcessController
from kriya.tools.service_runtime import ServiceVerificationOutcomeKind, run_managed_service_verification
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow import attempt, runtime_verification
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.state import GenerationState

SERVER = """import http.server, os, socket, sys
{prelude}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response({status}); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass
s = http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H); s.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.serve_forever()
"""
IMPORTS_UNTRACKED = SERVER.format(prelude="import helper_generated   # no commit holds this module", status=200)
HONEST = SERVER.format(prelude="", status=200)
BROKEN_ON_PURPOSE = SERVER.format(prelude="", status=500)
STATEFUL = SERVER.format(prelude=(
    "state = os.path.join(os.getcwd(), 'runtime-state.txt')\n"
    "if os.path.exists(state):\n    sys.exit('state left by an earlier run')\n"
    "open(state, 'w').write('x')\nopen(os.path.join(os.getcwd(), 'cwd.txt'), 'w').write(os.getcwd())"), status=200)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _worktree(tmp_path: Path, files: dict, *, ignored: dict | None = None, gitignore: str = "") -> Path:
    """A real git worktree holding ``files`` committed, plus ``ignored`` files that only ever lie on disk (git-ignored): the residue."""
    root = tmp_path / "wt"
    root.mkdir(parents=True)
    (root / ".gitignore").write_text(gitignore or "".join(f"{name}\n" for name in (ignored or {})))
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "reviewed")
    for rel, text in (ignored or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


class _Run:
    """The production function ``_execute_managed_service_verification`` over a real validator, a real worktree and REAL processes."""

    def __init__(self, root: Path, *, cfg: AutonomyConfig, command: list, port: int):
        self.root, self.state = root, GenerationState()
        self.state.attempt_number = 1
        self.validator = PolymorphicValidator(str(root), original_workspace_path=str(root), autonomy_cfg=cfg)
        self.ctx = types.SimpleNamespace(
            worktree_path=str(root), runtime_verification_required=True, goal="g", workspace_path=str(root),
            kernel=types.SimpleNamespace(config=types.SimpleNamespace(autonomy=types.SimpleNamespace(mode="guardrails"))))
        self.judgment = {**JUDGE_SERVICE, "managed_service": {
            "service_command": command, "readiness": {"kind": "tcp", "host": "127.0.0.1", "port": port},
            "probe": {"kind": "http", "host": "127.0.0.1", "port": port, "path": "/", "expected_status": 200},
            "startup_timeout_seconds": 20, "probe_timeout_seconds": 10, "shutdown_timeout_seconds": 10}}

    def go(self):
        """-> (outcome value, passed) of the recorded gate outcome, or the raised QualityGateFailure."""
        deps = attempt._runtime_deps()  # pylint: disable=protected-access
        try:
            asyncio.run(runtime_verification._execute_managed_service_verification(  # pylint: disable=protected-access
                deps, self.state, self.ctx, self.judgment, [], self.validator))
        except QualityGateFailure as failure:
            self.failure = failure.failure
            return self.failure.diagnostics, self.state.gate_outcomes[-1]
        self.failure = None
        return None, self.state.gate_outcomes[-1]


def _host_run(tmp_path, files, **kwargs):
    port = _free_port()
    root = _worktree(tmp_path, files, **kwargs)
    return _Run(root, cfg=AutonomyConfig(), command=[sys.executable, "server.py", str(port)], port=port)


def _outcome(result) -> str:
    return result[1].get("managed_service_outcome")


# ----------------------------------------------------------------- OI-142 controls: the service sees the exact bound tree

def test_an_ignored_module_in_the_worktree_cannot_make_a_broken_service_pass(tmp_path):
    run = _host_run(tmp_path, {"server.py": IMPORTS_UNTRACKED}, ignored={"helper_generated.py": "VALUE = 1\n"})
    assert (run.root / "helper_generated.py").exists(), "premise: the residue is on disk in the worktree"
    assert _outcome(run.go()) == ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY.value
    assert run.failure is not None and run.failure.type == "verification_infrastructure_failure"


def test_the_same_module_tracked_in_a_commit_does_pass_so_the_control_is_not_vacuous(tmp_path):
    run = _host_run(tmp_path, {"server.py": IMPORTS_UNTRACKED, "helper_generated.py": "VALUE = 1\n"})
    assert _outcome(run.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value and run.failure is None


def test_an_honest_source_change_does_change_the_verdict(tmp_path):
    passing = _host_run(tmp_path / "a", {"server.py": HONEST})
    failing = _host_run(tmp_path / "b", {"server.py": BROKEN_ON_PURPOSE})
    assert _outcome(passing.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value
    assert _outcome(failing.go()) == ServiceVerificationOutcomeKind.PROBE_FAILED.value and failing.failure.type == "run_verification"


def test_state_a_service_wrote_in_one_run_cannot_influence_the_next_and_the_worktree_stays_clean(tmp_path):
    first = _host_run(tmp_path, {"server.py": STATEFUL})
    before = sorted(p.name for p in first.root.iterdir())
    assert _outcome(first.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value
    second = _Run(first.root, cfg=AutonomyConfig(), command=first.judgment["managed_service"]["service_command"],
                  port=first.judgment["managed_service"]["probe"]["port"])
    assert _outcome(second.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value, "the first run's state file did not survive into the second"
    # R3: the contained verification's project environment (``<worktree>/.kriya/venv``, Kriya's own) lives only as long as the verification that built it, so after
    # the runs nothing is left of it either: no venv, and nothing the service wrote, in the worktree (at most the empty ``.kriya`` directory that held it).
    after = sorted(p.name for p in first.root.iterdir())
    assert [name for name in after if name != ".kriya"] == before, "nothing the service wrote reached the worktree"
    assert not (first.root / ".kriya").exists() or list((first.root / ".kriya").iterdir()) == []


def test_with_the_tree_bound_the_state_a_service_creates_is_runtime_state_not_an_unauthorized_creation(tmp_path):
    """Production binds the tree (the mutating path always has; the direct path now does): the view is then proven against it when the run ends,
    and what the running service created is the gate's ephemeral runtime state - recorded and discarded, never a failure of the candidate."""
    from kriya.workflow.file_integrity import VerificationTreeBinding
    from kriya.workflow.worktree import repository_content_paths

    run = _host_run(tmp_path, {"server.py": STATEFUL})
    run.validator.tree_binding = VerificationTreeBinding(str(run.root), repository_content_paths(str(run.root)), {})
    assert _outcome(run.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value, run.failure and run.failure.message[:800]
    assert not (run.root / "runtime-state.txt").exists()


def test_negative_control_run_in_place_the_second_run_does_see_the_first_ones_state(tmp_path):
    """The pre-change behavior, reproduced with the service layer alone on the worktree (what every managed run used to be)."""
    root = _worktree(tmp_path, {"server.py": STATEFUL})
    port = _free_port()
    spec = service_runtime.ManagedServiceVerificationSpec(
        service_command=[sys.executable, "server.py", str(port)], cwd=str(root),
        readiness=service_runtime.ReadinessSpec(kind="tcp", host="127.0.0.1", port=port),
        probe=service_runtime.ProbeSpec(port=port, path="/", expected_status=200), startup_timeout_seconds=10)
    # the service layer alone, on the worktree itself (no view), through the test-owned containment stand-in - there is no host launch to use any more
    standin = {"containment_profile": ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(root), network=NetworkAuthority.DENIED),
               "containment_backend": MountAwareStandIn()}
    assert run_managed_service_verification(spec, **standin).outcome is ServiceVerificationOutcomeKind.PROBE_PASSED
    assert run_managed_service_verification(spec, **standin).outcome is ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY


def test_the_service_runs_in_the_view_and_the_view_is_gone_when_the_run_returns(tmp_path):
    run = _host_run(tmp_path, {"server.py": STATEFUL})
    assert not os.path.isdir(_views()) or os.listdir(_views()) == []
    run.go()
    assert not os.listdir(_views()) if os.path.isdir(_views()) else True, "the view was removed"
    assert run.validator.workspace_path == str(run.root), "the validator is back on the worktree"
    assert not (run.root / "cwd.txt").exists()          # the service wrote its cwd into ITS directory: the view, not the worktree


@pytest.mark.parametrize("raising", [asyncio.CancelledError(), KeyboardInterrupt(), RuntimeError("the runtime died")])
def test_the_view_is_removed_and_the_validator_restored_when_the_lifecycle_is_interrupted(tmp_path, monkeypatch, raising):
    run = _host_run(tmp_path, {"server.py": HONEST})
    seen = {}

    def interrupted(spec, **_kwargs):
        seen["cwd"] = spec.cwd
        assert os.path.isdir(spec.cwd), "the view is alive while the lifecycle runs"
        raise raising
    deps = dataclasses.replace(attempt._runtime_deps(), run_managed_service_verification=interrupted)  # pylint: disable=protected-access
    with pytest.raises(type(raising)):
        asyncio.run(runtime_verification._execute_managed_service_verification(  # pylint: disable=protected-access
            deps, run.state, run.ctx, run.judgment, [], run.validator))
    assert seen["cwd"] != str(run.root) and not os.path.lexists(seen["cwd"]), "the view is gone"
    assert run.validator.workspace_path == str(run.root)


def test_a_service_command_naming_the_worktree_is_refused_typed_and_nothing_starts(tmp_path, monkeypatch):
    port = _free_port()
    root = _worktree(tmp_path, {"server.py": HONEST})
    run = _Run(root, cfg=AutonomyConfig(), command=[sys.executable, str(root / "server.py"), str(port)], port=port)
    started = []
    monkeypatch.setattr(ProcessController, "start_managed", lambda *_a, **_k: started.append(1))
    _diagnostics, outcome = run.go()
    assert started == [] and run.failure.type == "verification_infrastructure_failure"
    assert "MANAGED_SERVICE_CONTRACT_INVALID" in run.failure.message and "shared worktree" in run.failure.message


# ----------------------------------------------------------------- the host path is not reachable when containment is mandatory

def test_with_containment_mandatory_the_service_is_launched_only_through_a_profile_and_a_backend(tmp_path, monkeypatch):
    launched = []

    def refusing_start(_self, _command, **kwargs):
        launched.append((kwargs.get("containment_profile") is not None, kwargs.get("containment_backend") is not None))
        raise RuntimeError("stop: nothing is started in this test")
    monkeypatch.setattr(ProcessController, "start_managed", refusing_start)
    port = _free_port()
    run = _Run(_worktree(tmp_path, {"pom.xml": "<project/>"}), cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci"),
               command=["java", "-cp", "classes", "Main"], port=port)     # a JVM command: no project venv is prepared first
    monkeypatch.setattr(PolymorphicValidator, "build_containment_profile_and_backend",
                        lambda self, **_kw: (types.SimpleNamespace(), types.SimpleNamespace()))   # the real one needs a daemon; the decision is what is pinned
    run.go()
    assert launched == [(True, True)], "a mandatory policy launches through a profile and a backend, never bare"


def test_with_containment_mandatory_and_no_isolating_backend_the_run_is_refused_typed_before_any_process(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(ProcessController, "start_managed", lambda *_a, **_k: spawned.append("start_managed"))
    monkeypatch.setattr(ProcessController, "run", lambda *_a, **_k: spawned.append("run"))
    port = _free_port()
    run = _Run(_worktree(tmp_path, {"pom.xml": "<project/>"}), cfg=AutonomyConfig(contained_execution_required=True, containment_backend="none"),
               command=["java", "-cp", "classes", "Main"], port=port)
    with pytest.raises(ExternalExecutionRefused):
        run.go()
    assert spawned == []


# ----------------------------------------------------------------- the fresh-cache defect and the contained service, through the REAL OCI runtime

_JAVA_POM = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>d</groupId><artifactId>app</artifactId>'
             '<version>1</version><packaging>jar</packaging><properties><maven.compiler.release>17</maven.compiler.release></properties>{dependencies}'
             '<build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin>'
             '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-resources-plugin</artifactId><version>3.3.1</version></plugin>'
             '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-jar-plugin</artifactId><version>3.4.1</version>'
             '<configuration><archive><manifest><mainClass>d.App</mainClass></manifest></archive></configuration></plugin></plugins></build></project>')
_JAVA_APP = ('package d;\nimport com.sun.net.httpserver.HttpServer;\nimport java.net.InetSocketAddress;\npublic class App {\n'
             '  public static void main(String[] a) throws Exception {\n'
             '    HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 8080), 0);\n'
             '    s.createContext("/", e -> { byte[] b = "ok".getBytes(); e.sendResponseHeaders(200, b.length); e.getResponseBody().write(b); e.close(); });\n'
             '    s.start();\n  }\n}\n')
_JAVA_FILES = {"pom.xml": _JAVA_POM.format(dependencies=""), "src/main/java/d/App.java": _JAVA_APP}
_JAR = "target/app-1.jar"
docker = pytest.mark.skipif(not _docker_reachable() or shutil.which("git") is None, reason="docker daemon not reachable")


def _java_run(tmp_path, files=None, ignored=None):
    root = _worktree(tmp_path, files or _JAVA_FILES, ignored=ignored, gitignore="target/\n.mvn/maven.config\n")
    return _Run(root, cfg=_contained(), command=["java", "-jar", _JAR], port=8080)


@docker
def test_real_oci_a_fresh_cache_contained_service_builds_in_the_view_runs_there_and_leaves_the_worktree_untouched(tmp_path, monkeypatch):
    networks = []
    real_run = ProcessController.run

    def spy(self, command, **kwargs):
        profile = kwargs.get("containment_profile")
        if profile is not None:
            networks.append(profile.network)
        return real_run(self, command, **kwargs)
    monkeypatch.setattr(ProcessController, "run", spy)
    wrapper_canary = tmp_path / "WRAPPER_RAN"
    run = _java_run(tmp_path)
    (run.root / "mvnw").write_text(f"#!/bin/sh\necho ran > {wrapper_canary}\nexit 1\n")
    (run.root / "mvnw").chmod(0o755)
    _git(run.root, "add", "-A")
    _git(run.root, "commit", "-qm", "wrapper")
    before = sorted(p.name for p in run.root.iterdir())
    result = run.go()
    assert _outcome(result) == ServiceVerificationOutcomeKind.PROBE_PASSED.value, run.failure and run.failure.message[:1500]
    assert any(str(n).endswith("DEPENDENCY_REGISTRY_ONLY") for n in networks), "the build acquired its dependencies under the registry policy"
    assert not wrapper_canary.exists(), "the project's ./mvnw never ran"
    assert sorted(p.name for p in run.root.iterdir()) == before and not (run.root / "target").exists(), "the artifact lives in the view only"
    # the warm cache: the second run builds offline and gives the same verdict
    again = _Run(run.root, cfg=_contained(), command=["java", "-jar", _JAR], port=8080)
    assert _outcome(again.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value


@docker
def test_real_oci_ignored_build_configuration_and_a_forged_artifact_in_the_worktree_cannot_influence_the_contained_service(tmp_path):
    forged_jar = "not a jar"
    run = _java_run(tmp_path, ignored={".mvn/maven.config": "-Dmaven.main.skip=true\n", _JAR: forged_jar})
    assert (run.root / _JAR).read_text() == forged_jar and (run.root / ".mvn/maven.config").exists(), "premise: both residues lie in the worktree"
    assert _outcome(run.go()) == ServiceVerificationOutcomeKind.PROBE_PASSED.value, run.failure and run.failure.message[:1500]
    assert (run.root / _JAR).read_text() == forged_jar, "the worktree's forged artifact was not touched"


@docker
def test_real_oci_an_ignored_module_cannot_make_a_broken_contained_service_pass_and_nothing_survives(tmp_path):
    port = 8000
    root = _worktree(tmp_path, {"server.py": IMPORTS_UNTRACKED}, ignored={"helper_generated.py": "VALUE = 1\n"})
    run = _Run(root, cfg=_contained(), command=["python3", "server.py", str(port)], port=port)
    assert _outcome(run.go()) == ServiceVerificationOutcomeKind.SERVICE_EXITED_BEFORE_READY.value
    leftovers = subprocess.run(["docker", "ps", "-a", "--filter", "name=kriya-oci-", "--format", "{{.Names}}"], capture_output=True, text=True, check=True).stdout
    assert leftovers.strip() == "", "no container outlives the run"
    assert not os.listdir(_views()) if os.path.isdir(_views()) else True


@docker
def test_real_oci_unavailable_acquisition_is_a_typed_preparation_failure_after_a_policy_scoped_attempt(tmp_path, monkeypatch):
    """The dependency lives only in a registry outside the acquisition's allowlist: the build acquires under the registry policy, cannot fetch it,
    and the outcome is the typed PREPARATION_FAILED infrastructure failure with the build's evidence - not an exception, not an internal error, not
    the unconditional failure every contained managed build used to end in (no cache, no network) whether or not the dependency was available."""
    networks = []
    real_run = ProcessController.run

    def spy(self, command, **kwargs):
        profile = kwargs.get("containment_profile")
        if profile is not None:
            networks.append(str(profile.network))
        return real_run(self, command, **kwargs)
    monkeypatch.setattr(ProcessController, "run", spy)
    unreachable = ("<repositories><repository><id>outside</id><url>https://registry.kriya-unreachable.invalid/maven2</url></repository></repositories>"
                   "<dependencies><dependency><groupId>org.kriya.outside</groupId><artifactId>thing</artifactId><version>1.0</version></dependency></dependencies>")
    run = _java_run(tmp_path, files={**_JAVA_FILES, "pom.xml": _JAVA_POM.format(dependencies=unreachable)})
    result = run.go()
    assert _outcome(result) == ServiceVerificationOutcomeKind.PREPARATION_FAILED.value
    assert run.failure.type == "verification_infrastructure_failure" and "DependencyResolutionException" in run.failure.message
    assert any(n.endswith("DEPENDENCY_REGISTRY_ONLY") for n in networks), networks      # acquisition was attempted, under the registry-scoped policy
    assert "failed to invoke" not in run.failure.message


def test_a_containment_setup_failure_of_the_contained_build_propagates_as_itself(tmp_path):
    from kriya.tools.containment import ContainmentSetupError

    (tmp_path / "pom.xml").write_text("<project/>")

    def failing(_argv, _timeout):
        raise ContainmentSetupError("the registry-scoped acquisition container could not be set up")
    with pytest.raises(ContainmentSetupError):
        service_runtime._prepare_required_artifact(  # pylint: disable=protected-access
            ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(), contained_build=failing)


def test_the_acquisition_marker_is_named_in_the_preparation_failure(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")

    def incomplete(_argv, _timeout):
        return {"returncode": 1, "stdout": "", "stderr": "MAVEN_ACQUISITION_INCOMPLETE: offline execution still lacks it", "timeout": False}
    outcome = service_runtime._prepare_required_artifact(  # pylint: disable=protected-access
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(), contained_build=incomplete)
    assert outcome.outcome is ServiceVerificationOutcomeKind.PREPARATION_FAILED and "MAVEN_ACQUISITION_INCOMPLETE" in outcome.reasoning
    plain = service_runtime._prepare_required_artifact(  # pylint: disable=protected-access
        ["java", "-jar", "target/app.jar"], str(tmp_path), controller=ProcessController(),
        contained_build=lambda *_: {"returncode": 1, "stdout": "", "stderr": "compile error", "timeout": False})
    assert "MAVEN_ACQUISITION_INCOMPLETE" not in plain.reasoning
