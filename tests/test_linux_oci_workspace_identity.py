"""LINUX-OCI-WORKSPACE-IDENTITY-001: a container that writes a host mount runs
as the real host identity that owns it (SEC-008's rule), never as root.

With --cap-drop ALL, root inside the container has no CAP_DAC_OVERRIDE, so
on Linux it could neither enter nor write a host-owned workspace ("can't cd
to /kriya/workspace", "cannot create out.txt: Permission denied" - hosted
run 36368006232; Docker Desktop's file sharing hides it on macOS). Kept:
--cap-drop ALL, no-new-privileges, the network decision, no host
chmod/chown, no root or privileged fallback. A non-root process owns its
scratch tmpfs, which is also its working directory when no workspace is
mounted (OCI-NONROOT-TMPFS-WORKDIR-001).
"""
import os
import shutil
import subprocess

import pytest

from kriya.tools.containment import BackendUnavailableError, ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend
from kriya.tools.process import ProcessController

DOCKER = shutil.which("docker")


def _daemon_up():
    if DOCKER is None:
        return False
    probe = subprocess.run([DOCKER, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
    return probe.returncode == 0 and bool(probe.stdout.strip())


needs_docker = pytest.mark.skipif(not _daemon_up(), reason="docker daemon not reachable")


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)  # as private as a pytest tmp dir or a user's repo can be
    return workspace


def _profile(workspace, **fields):
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
                              network=NetworkAuthority.DENIED, **fields)


def _argv(profile, command=("sh", "-c", "true"), monkeypatch=None):
    backend = OCIContainmentBackend()
    if monkeypatch is not None:
        monkeypatch.setattr(backend, "_probe_daemon", lambda _docker: None)
    return backend.prepare(profile, list(command)).command_prefix


def _value(argv, flag):
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


# --- The container's identity (argv; no daemon needed) --------------------------------

pytestmark_docker_cli = pytest.mark.skipif(DOCKER is None, reason="docker CLI not on PATH")


@pytestmark_docker_cli
def test_a_workspace_writing_container_runs_as_the_host_owner_with_capabilities_dropped(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    argv = _argv(_profile(workspace), monkeypatch=monkeypatch)
    uid, gid = os.getuid(), os.getgid()
    assert _value(argv, "--user") == [f"{uid}:{gid}"] and uid != 0
    assert _value(argv, "--cap-drop") == ["ALL"] and "--cap-add" not in argv and "--privileged" not in argv
    assert _value(argv, "--security-opt") == ["no-new-privileges"] and _value(argv, "--network") == ["none"]
    [tmpfs] = _value(argv, "--tmpfs")
    assert tmpfs.endswith(f",uid={uid},gid={gid},mode=0700")
    env = dict(item.split("=", 1) for item in _value(argv, "-e"))
    assert env["HOME"] == "/kriya/tmp" and env["MAVEN_CONFIG"] == "/kriya/tmp/.m2"
    assert env["JAVA_TOOL_OPTIONS"].endswith("-Duser.home=/kriya/tmp")
    # The host workspace itself is never re-owned or re-moded.
    assert (workspace.stat().st_mode & 0o777) == 0o700 and workspace.stat().st_uid == uid


@pytestmark_docker_cli
def test_kriya_running_as_root_or_a_foreign_owned_workspace_fails_closed(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    with pytest.raises(BackendUnavailableError, match=r"is running as root \(uid 0\)"):
        _argv(_profile(workspace), monkeypatch=monkeypatch)
    monkeypatch.setattr(os, "getuid", lambda: 4242)
    with pytest.raises(BackendUnavailableError, match="does not match"):
        _argv(_profile(workspace), monkeypatch=monkeypatch)


@pytestmark_docker_cli
def test_an_explicit_identity_and_a_container_writing_no_host_mount_are_unchanged(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    explicit = _argv(_profile(workspace, run_as_uid=65534, run_as_gid=65534, workspace_write=False),
                     monkeypatch=monkeypatch)
    assert _value(explicit, "--user") == ["65534:65534"]
    assert "HOME=/kriya/tmp" not in _value(explicit, "-e")
    read_only = _argv(_profile(workspace, workspace_write=False), monkeypatch=monkeypatch)
    assert "--user" not in read_only and _value(read_only, "-v") == [f"{workspace}:/kriya/workspace:ro"]


# --- Real containers --------------------------------------------------------------------

def _run(profile, command, workspace, timeout=180):
    return ProcessController().run(command, cwd=str(workspace), timeout=timeout,
                                   containment_profile=profile, containment_backend=OCIContainmentBackend())


@needs_docker
def test_a_private_workspace_compiles_and_its_outputs_belong_to_the_host_owner(tmp_path):
    workspace = _workspace(tmp_path)
    (workspace / "Hello.java").write_text(
        'public class Hello { public static void main(String[] a) { System.out.println("home=" + '
        'System.getProperty("user.home")); } }\n')
    result = _run(_profile(workspace), ["sh", "-c", "javac Hello.java && java Hello && touch made.txt"], workspace)
    assert result.returncode == 0, result.stderr
    assert "home=/kriya/tmp" in result.stdout  # never "?", which Maven resolves inside the workspace
    for produced in ("Hello.class", "made.txt"):
        stat = (workspace / produced).stat()
        assert (stat.st_uid, stat.st_gid) == (os.getuid(), os.getgid()), produced
    assert not (workspace / "?").exists()


@needs_docker
def test_writes_outside_the_authorized_areas_fail(tmp_path):
    workspace = _workspace(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    probe = ("for p in /etc/kriya-probe /usr/kriya-probe /kriya/cache/0/kriya-probe; do "
             "touch $p 2>/dev/null && echo WROTE:$p; done; touch /kriya/workspace/ok /kriya/tmp/ok && echo SCRATCH_OK")
    result = _run(_profile(workspace, dependency_cache_paths=[str(cache)]), ["sh", "-c", probe], workspace)
    assert result.returncode == 0, result.stderr
    assert "WROTE:" not in result.stdout and "SCRATCH_OK" in result.stdout
    assert list(cache.iterdir()) == []


@needs_docker
def test_a_read_only_workspace_stays_read_only(tmp_path):
    workspace = tmp_path / "ro"
    workspace.mkdir()  # a normal 0755 repository directory
    (workspace / "a.txt").write_text("a")
    result = _run(_profile(workspace, workspace_write=False),
                  ["sh", "-c", "cat a.txt && touch b.txt"], workspace)
    assert result.stdout.startswith("a") and result.returncode != 0
    assert not (workspace / "b.txt").exists()


@needs_docker
def test_a_non_root_container_without_a_workspace_has_a_writable_working_directory(tmp_path):
    """OCI-NONROOT-TMPFS-WORKDIR-001: the TOOL-003 shape (no workspace grant,
    run as nobody) - its working directory is the scratch tmpfs it owns."""
    profile = _profile(tmp_path, mount_workspace=False, run_as_uid=65534, run_as_gid=65534)
    result = _run(profile, ["sh", "-c", "pwd && touch here.txt && id -u"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["/kriya/tmp", "65534"]
