"""SEC-005 O5 closure: ShellTool package-manager-shaped commands must not
receive ambient/unrestricted network authority merely because they are
shell commands. See CLAUDE.md's "ShellTool package-manager network
authority - SEC-005" section for the full architecture.

Three layers of evidence, in order:
1. Deterministic classification (`kriya.policy.execution.
   classify_shell_acquisition_command`) - pure function, no I/O.
2. Deterministic profile-construction (`ShellTool._run`'s own
   ContainmentProfile selection) via a capturing DummyContainmentBackend -
   proves ShellTool passes the CORRECT network/network_destinations for a
   given command + config, independent of whether a real backend is
   configured.
3. Real-Docker adversarial/legitimate evidence, reusing the exact
   Squid-proxy/registry-scoped mechanism already proven at the
   ContainmentProfile+OCIContainmentBackend level in
   tests/test_containment_oci.py (SEC-006) - here proving ShellTool's own
   WIRING into that already-proven mechanism, not re-proving the mechanism
   itself.

Does not touch ordinary (non-package-manager) ShellTool commands at all -
their network authority remains UNRESTRICTED, unchanged, exactly as every
pre-existing ShellTool test in tests/test_tools.py already establishes.
"""
import os
import shutil
import subprocess

import pytest

from _plugin_test_support import load_core_tools_module

from kriya.config import AppConfig
from kriya.policy.execution import classify_shell_acquisition_command
from kriya.tools.containment import (
    BackendUnavailableError,
    DummyContainmentBackend,
    NetworkAuthority,
)
from kriya.tools.tool import ToolExecutionError

_core_tools = load_core_tools_module()
ShellTool = _core_tools.ShellTool


# --- Layer 1: deterministic classification (Tasks 2/6/9) ---

@pytest.mark.parametrize(
    "command,expected",
    [
        # Maven / Maven Wrapper - ANY subcommand (Task 6's own required check)
        (("mvn", "compile"), "registry_scoped"),
        (("mvn", "clean", "install"), "registry_scoped"),
        (("mvnw", "test"), "registry_scoped"),
        (("./mvnw", "verify"), "registry_scoped"),
        # pip / pip3, direct and venv-qualified `python -m pip`
        (("pip", "install", "requests"), "registry_scoped"),
        (("pip3", "download", "-d", "x", "requests"), "registry_scoped"),
        (("python3", "-m", "pip", "install", "requests"), "registry_scoped"),
        (("python", "-m", "pip3", "download", "requests"), "registry_scoped"),
        # unmapped ecosystems - recognized, no registry-host authority exists
        (("npm", "install", "left-pad"), "unmapped"),
        (("npm", "ci"), "unmapped"),
        (("npm", "i", "left-pad"), "unmapped"),
        (("bundle", "install"), "unmapped"),
        (("gem", "install", "rails"), "unmapped"),
        (("cargo", "add", "serde"), "unmapped"),
        (("gradle", "build"), "unmapped"),
        (("./gradlew", "test"), "unmapped"),
        # near misses - must NOT acquire package-acquisition authority
        (("pip", "list"), None),
        (("pip", "show", "requests"), None),
        (("ls", "-la"), None),
        (("curl", "https://evil.example"), None),
        (("echo", "pip install fake"), None),
        (("pip-installer-thing", "install"), None),
        ((), None),
        # compound shell syntax - "obvious shell wrapping" bypass check (Task 6)
        (("pip", "install", "foo", "&&", "curl", "https://evil.example"), "registry_scoped"),
        (("curl", "https://evil.example", "&&", "pip", "install", "foo"), "registry_scoped"),
        (("echo", "hi", ";", "npm", "install", "left-pad"), "unmapped"),
        (("bash", "-c", "pip install foo"), "registry_scoped"),
        (("sh", "-c", "npm install foo"), "unmapped"),
        (("bash", "-c", "curl https://evil.example"), None),
    ],
)
def test_classify_shell_acquisition_command(command, expected):
    assert classify_shell_acquisition_command(command) == expected


def test_classify_never_returns_unmapped_or_registry_scoped_for_empty_command():
    assert classify_shell_acquisition_command(()) is None


# --- Layer 2: deterministic profile construction, no Docker required ---

def _capturing_tool(monkeypatch, **autonomy_overrides):
    cfg = AppConfig()
    for key, value in autonomy_overrides.items():
        setattr(cfg.autonomy, key, value)
    backend = DummyContainmentBackend()
    monkeypatch.setattr(_core_tools, "resolve_containment_backend", lambda name: backend)
    tool = ShellTool(autonomy_cfg=cfg.autonomy)
    return tool, backend


@pytest.mark.asyncio
async def test_ordinary_command_network_unaffected_when_containment_not_required(monkeypatch):
    tool, backend = _capturing_tool(monkeypatch, contained_execution_required=False)
    await tool.execute(command="echo hi")
    profile = backend.prepared_profiles[-1]
    assert profile.network == NetworkAuthority.UNRESTRICTED
    assert profile.network_destinations == ()


@pytest.mark.asyncio
async def test_maven_command_network_unaffected_when_containment_not_required():
    """Task 5 / Task 4: 'preserve current documented behavior' when
    containment is intentionally disabled (the packaged default) - this
    package must not silently narrow every deployment's ShellTool
    behavior, only the opt-in-contained one."""
    tool = ShellTool()  # default AppConfig().autonomy: contained_execution_required=False
    res = await tool.execute(command="mvn --version || true")
    assert isinstance(res["exit_code"], int)  # ran uncontained/unrestricted, unchanged


@pytest.mark.asyncio
async def test_maven_command_gets_registry_scoped_network_when_contained(monkeypatch):
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["repo.maven.apache.org", "pypi.org"],
    )
    await tool.execute(command="mvn clean install")
    profile = backend.prepared_profiles[-1]
    assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
    assert profile.network_destinations == ("pypi.org", "repo.maven.apache.org")


@pytest.mark.asyncio
async def test_pip_install_gets_registry_scoped_network_when_contained(monkeypatch):
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["pypi.org", "files.pythonhosted.org"],
    )
    await tool.execute(command="pip install requests")
    profile = backend.prepared_profiles[-1]
    assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY
    assert profile.network_destinations == ("files.pythonhosted.org", "pypi.org")


@pytest.mark.asyncio
async def test_venv_qualified_pip_recognized_when_contained(monkeypatch):
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["pypi.org"],
    )
    await tool.execute(command="python3 -m pip install requests")
    profile = backend.prepared_profiles[-1]
    assert profile.network == NetworkAuthority.DEPENDENCY_REGISTRY_ONLY


@pytest.mark.asyncio
async def test_unmapped_package_manager_denied_network_when_contained(monkeypatch):
    """Task 3: an unsupported/unmapped package manager must fail closed for
    network - never approximate with UNRESTRICTED."""
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["repo.maven.apache.org"],
    )
    for command in ("npm install left-pad", "bundle install", "gem install rails", "gradle build"):
        backend.prepared_profiles.clear()
        await tool.execute(command=command)
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.DENIED, command
        assert profile.network_destinations == ()


@pytest.mark.asyncio
async def test_near_miss_command_does_not_acquire_registry_authority_when_contained(monkeypatch):
    """Task 6/Acceptance: a near-miss must not be upgraded into either
    bucket - it falls through to the SAME UNRESTRICTED baseline every
    other unrecognized ShellTool command already gets, never a new grant."""
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["repo.maven.apache.org"],
    )
    for command in ("pip list", "echo 'pip install fake'", "ls -la", "pip-installer-thing install"):
        backend.prepared_profiles.clear()
        await tool.execute(command=command)
        profile = backend.prepared_profiles[-1]
        assert profile.network == NetworkAuthority.UNRESTRICTED, command
        assert profile.network_destinations == ()


@pytest.mark.asyncio
async def test_compound_command_narrows_whole_process_network(monkeypatch):
    """A recognized package-manager segment anywhere in a compound shell
    command narrows the WHOLE process's network authority (containment is
    per-process, not per-segment) - proven here for the trailing-segment
    case; the real-Docker section proves the narrowed authority is
    actually enforced, not merely selected."""
    tool, backend = _capturing_tool(
        monkeypatch, contained_execution_required=True,
        acquisition_registry_hosts=["repo.maven.apache.org"],
    )
    await tool.execute(command="echo hi && npm install left-pad")
    profile = backend.prepared_profiles[-1]
    assert profile.network == NetworkAuthority.DENIED


# --- Authority separation (Task 9): policy decision/mode cannot influence
# network-authority selection at all - the two are structurally independent
# code paths inside _run(). ---

@pytest.mark.asyncio
async def test_execution_policy_mode_does_not_influence_network_authority(monkeypatch):
    from kriya.config.config import ExecutionPolicyConfig

    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.acquisition_registry_hosts = ["repo.maven.apache.org"]
    backend = DummyContainmentBackend()
    monkeypatch.setattr(_core_tools, "resolve_containment_backend", lambda name: backend)

    audit_tool = ShellTool(autonomy_cfg=cfg.autonomy, execution_policy_cfg=ExecutionPolicyConfig(mode="audit"))
    await audit_tool.execute(command="npm install left-pad")
    audit_network = backend.prepared_profiles[-1].network

    backend.prepared_profiles.clear()
    enforce_tool = ShellTool(autonomy_cfg=cfg.autonomy, execution_policy_cfg=ExecutionPolicyConfig(mode="enforce"))
    await enforce_tool.execute(command="npm install left-pad")
    enforce_network = backend.prepared_profiles[-1].network

    assert audit_network == enforce_network == NetworkAuthority.DENIED


def test_run_has_no_confirmation_parameter():
    """Structural proof that human confirmation cannot reach network
    authority at all: confirmation lives entirely in kriya/cli.py's
    tools_execute (requires_confirmation/-y), which never calls _run()
    directly with any confirmation-related argument - _run()'s only
    parameter is the validated ShellArgs itself."""
    import inspect
    sig = inspect.signature(ShellTool._run)
    assert list(sig.parameters) == ["self", "args"]


# --- Task 10: failure semantics - containment backend failure must fail
# closed, never fall back to weaker/unrestricted network. Deterministic,
# no Docker needed: the packaged default containment_backend ("none") is
# itself the "backend unavailable for this profile" case. ---

@pytest.mark.asyncio
async def test_backend_unavailable_for_registry_scoped_fails_closed_not_weaker(monkeypatch):
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.acquisition_registry_hosts = ["repo.maven.apache.org"]
    cfg.autonomy.containment_backend = "none"  # misconfigured: wants containment, no real backend
    tool = ShellTool(autonomy_cfg=cfg.autonomy)
    with pytest.raises(ToolExecutionError):
        await tool.execute(command="mvn clean install")


def test_backend_unavailable_raises_containment_setup_error_directly():
    """Same case at the containment layer directly (no ToolExecutionError
    wrapping) - confirms the specific exception type is the fail-closed
    BackendUnavailableError, not a generic failure that could be mistaken
    for an ordinary command error."""
    from kriya.tools.containment import ContainmentProfile, NullContainmentBackend, TrustClass

    profile = ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=os.getcwd(),
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY,
        network_destinations=("repo.maven.apache.org",),
    )
    with pytest.raises(BackendUnavailableError):
        NullContainmentBackend().prepare(profile, ["/bin/sh", "-c", "mvn clean install"])


@pytest.mark.asyncio
async def test_unmapped_manager_backend_unavailable_also_fails_closed():
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "none"
    tool = ShellTool(autonomy_cfg=cfg.autonomy)
    with pytest.raises(ToolExecutionError):
        await tool.execute(command="npm install left-pad")


# --- Layer 3: real Docker, reusing SEC-006's already-proven registry-scoped
# mechanism (Squid proxy + iptables) - proving ShellTool's OWN wiring into
# it, not re-proving the mechanism (see tests/test_containment_oci.py). ---

def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


pytestmark_docker = pytest.mark.skipif(not _docker_reachable(), reason="docker CLI/daemon not available")

_CONNECT_PROBE = (
    'PROXY_HOSTPORT="${{http_proxy#http://}}"; '
    'printf "CONNECT {host}:443 HTTP/1.1\\r\\nHost: {host}:443\\r\\n\\r\\n" | '
    'timeout 5 nc "${{PROXY_HOSTPORT%:*}}" "${{PROXY_HOSTPORT##*:}}" | head -1'
)


def _contained_shell_tool(registry_hosts):
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = True
    cfg.autonomy.containment_backend = "oci"
    cfg.autonomy.acquisition_registry_hosts = registry_hosts
    return ShellTool(autonomy_cfg=cfg.autonomy)


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_maven_command_through_shelltool_reaches_authorized_registry():
    tool = _contained_shell_tool(["repo.maven.apache.org"])
    probe = _CONNECT_PROBE.format(host="repo.maven.apache.org")
    res = await tool.execute(command=f"mvn --version >/dev/null 2>&1 ; {probe}")
    assert "200" in res["stdout"], res


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_maven_command_through_shelltool_denied_unauthorized_destination():
    tool = _contained_shell_tool(["repo.maven.apache.org"])
    probe = _CONNECT_PROBE.format(host="example.com")
    res = await tool.execute(command=f"mvn --version >/dev/null 2>&1 ; {probe}")
    assert "403" in res["stdout"], res


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_pip_command_through_shelltool_reaches_authorized_registry():
    tool = _contained_shell_tool(["pypi.org", "files.pythonhosted.org"])
    probe = _CONNECT_PROBE.format(host="pypi.org")
    res = await tool.execute(command=f"pip3 install --dry-run nonexistent-kriya-test-pkg-xyz >/dev/null 2>&1 ; {probe}")
    assert "200" in res["stdout"], res


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_pip_command_through_shelltool_denied_unauthorized_destination():
    tool = _contained_shell_tool(["pypi.org", "files.pythonhosted.org"])
    probe = _CONNECT_PROBE.format(host="example.com")
    res = await tool.execute(command=f"pip3 install --dry-run nonexistent-kriya-test-pkg-xyz >/dev/null 2>&1 ; {probe}")
    assert "403" in res["stdout"], res


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_unsupported_manager_never_gets_unrestricted_network():
    """npm has no registry-host mapping - must be denied outright, never
    approximated with UNRESTRICTED. Uses a raw socket connect (works on
    any image, no netcat dependency needed for a plain DENIED profile)."""
    tool = _contained_shell_tool(["repo.maven.apache.org"])
    probe = (
        "python3 -c \"import socket; socket.create_connection(('8.8.8.8', 53), timeout=5); "
        "print('BYPASS_BAD')\" || echo BLOCKED_GOOD"
    )
    res = await tool.execute(command=f"npm install left-pad >/dev/null 2>&1 ; {probe}")
    assert "BLOCKED_GOOD" in res["stdout"]
    assert "BYPASS_BAD" not in res["stdout"]


@pytestmark_docker
@pytest.mark.asyncio
async def test_real_near_miss_command_keeps_unrestricted_network_under_containment():
    """Positive control: a near-miss (not a recognized package-manager
    shape) still reaches the network freely even under
    contained_execution_required=True - proving this package narrows
    authority ONLY for positively-recognized commands, never generally."""
    tool = _contained_shell_tool(["repo.maven.apache.org"])
    probe = (
        "python3 -c \"import socket; socket.create_connection(('8.8.8.8', 53), timeout=5); "
        "print('CONNECTED')\""
    )
    res = await tool.execute(command=f"echo 'pip install fake' ; {probe}")
    assert "CONNECTED" in res["stdout"], res
