"""S7: the real-OCI tier's availability gates. A test that needs REAL containment semantics (a Maven build in the canonical provider image with the canonical cache /
acquisition, anything asserting isolation) is skipped - never faked - when Docker or the canonical image is missing. The skip reason names docker, so tests/conftest.py pins
the test to the single docker xdist worker (their leak checks list every kriya container on the daemon)."""
from __future__ import annotations

import functools
import shutil
import subprocess
import time

import pytest


@functools.lru_cache(maxsize=None)
def docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15, check=False).returncode == 0
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def image_present(image: str) -> bool:
    """Present locally. Docker Desktop (containerd image store) can answer 'No such image' to ``docker image inspect <name:tag>`` for an image its own listing shows (MEASURED
    2026-10-25, intermittent; kriya.tools.containment_oci re-inspects a not-found for a related reason), so the image LISTING is the second witness."""
    if not docker_reachable():
        return False
    for attempt in range(3):
        try:
            if subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=30, check=False).returncode == 0:
                return True
            listed = subprocess.run(["docker", "image", "ls", "-q", image], capture_output=True, text=True, timeout=30, check=False)
            if listed.returncode == 0 and listed.stdout.strip():
                return True
        except Exception:
            pass
        time.sleep(1.0 + attempt)
    return False


REAL_OCI_DAEMON = pytest.mark.skipif(not docker_reachable(), reason="real OCI tier: docker daemon not reachable")

MAVEN_IMAGE = "maven:3.9-eclipse-temurin-17"       # the canonical provider image for the JDK 17 default toolchain (kriya.tools.toolchain_identity)

REAL_OCI_MAVEN = pytest.mark.skipif(not image_present(MAVEN_IMAGE), reason=f"real OCI tier: docker daemon or the canonical image {MAVEN_IMAGE} is not available")


MCP_IMAGE = "python:3.10-slim"                       # the image the MCP containment backend selects for a python3 server command
REAL_OCI_MCP = pytest.mark.skipif(not image_present(MCP_IMAGE), reason=f"real OCI tier: docker daemon or the MCP container image {MCP_IMAGE} is not available")
MCP_RO_PREFIX = "/kriya/mcp/extra/ro/0"              # kriya/mcp/containment_adapter.py: the first additional_read_paths entry, read-only
MCP_RW_PREFIX = "/kriya/mcp/extra/rw/0"              # ... and the first additional_write_paths entry, read-write


def contained_mcp_server(script: str, *, tests_dir: str, log_dir=None, env=None, **capabilities) -> dict:
    """An ``mcp.<name>`` entry for a fixture server that runs CONTAINED in the real OCI backend (the packaged default): the command is the container-side one (python3 + the
    fixture file under ``tests_dir``, mounted read-only at its deterministic container path); ``log_dir`` (optional) is mounted read-write and the server writes
    ``calls.log`` there (host side ``log_dir/calls.log``) - the server-side count of calls stays the evidence, never the client's stdout."""
    caps = {"additional_read_paths": [tests_dir], **capabilities}
    server_env = dict(env or {})
    if log_dir is not None:
        caps["additional_write_paths"] = [str(log_dir)]
        server_env.setdefault("CALL_LOG_FILE", f"{MCP_RW_PREFIX}/calls.log")
    return {"command": "python3", "args": [f"{MCP_RO_PREFIX}/{script}"], "env": server_env, "capabilities": caps}


def leftover_mcp_containers() -> list:
    """Names of every Kriya MCP container (labelled with its capability digest) the daemon still lists, running or not."""
    done = subprocess.run(["docker", "ps", "-a", "--filter", "label=kriya.mcp-capability-digest", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False)
    return [name for name in done.stdout.splitlines() if name.strip()]
