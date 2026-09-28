"""OCI-DAEMON-PROBE-001: `docker info --format` exits 0 with an unreachable
daemon (it prints the connection error to stderr and an empty template), so
the probe must require a reported server version. Otherwise containment
setup "succeeds" and the command later fails as an ordinary command failure
instead of BackendUnavailableError (found by chaos C04 on the hosted runner).
"""
import os
import stat

import pytest

from kriya.tools.containment import BackendUnavailableError
from kriya.tools.containment_oci import OCIContainmentBackend


def _fake_docker(tmp_path, *, stdout, stderr, code):
    path = tmp_path / "docker"
    path.write_text(f"#!/bin/sh\nprintf '%s' '{stdout}'\nprintf '%s' '{stderr}' >&2\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.mark.parametrize("stdout,stderr,code", [
    ("", "Cannot connect to the Docker daemon at unix:///x.sock. Is the docker daemon running?", 0),
    ("\n", "", 0),
    ("", "permission denied", 1),
])
def test_an_unreachable_daemon_is_backend_unavailable(tmp_path, stdout, stderr, code):
    docker = _fake_docker(tmp_path, stdout=stdout, stderr=stderr, code=code)
    with pytest.raises(BackendUnavailableError, match="not reachable"):
        OCIContainmentBackend()._probe_daemon(docker)


def test_a_daemon_reporting_its_version_is_reachable(tmp_path):
    docker = _fake_docker(tmp_path, stdout="27.5.1\n", stderr="", code=0)
    assert OCIContainmentBackend()._probe_daemon(docker) is None
    assert os.path.exists(docker)
