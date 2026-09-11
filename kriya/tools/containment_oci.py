"""SEC-001-P6: the production OCI/container `ContainmentBackend`.

Translates a backend-independent `ContainmentProfile` (kriya/tools/
containment.py) into a real `docker run` invocation - this is the ONLY
module in Kriya that knows what "docker" or "container" means; every
other module (including `ContainmentProfile`/`ProcessController` itself)
stays mechanism-agnostic, per the design's own charter.

Composed into the common execution boundary via `PreparedContainment.
command_prefix` (a real `docker run [flags] image` argv prepended to the
caller's own command) and `PreparedContainment.cleanup` (an authoritative
`docker rm -f` teardown - see that field's own docstring for why
`ProcessController`'s host-side process-group kill alone is not enough
for a VM-mediated container runtime like Docker Desktop on macOS).

Current development platform is macOS/Apple Silicon, where Docker Desktop
runs containers inside a Linux VM (confirmed live, 2026-09-11: linuxkit
kernel, aarch64) - nothing in this module is macOS-specific, it only ever
calls the `docker` CLI, which is the same interface CI/Linux uses. No
macOS-specific behavior is hard-coded (Invariant: no unsupported macOS
mechanism may become the production security guarantee - this backend's
guarantee comes from the OCI runtime, not from any macOS primitive).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import uuid
from typing import List, Optional, Tuple

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    NetworkAuthority,
    PreparedContainment,
    build_restricted_env,
)

logger = logging.getLogger(__name__)

_CONTAINER_WORKSPACE = "/kriya/workspace"
_CONTAINER_TEMP = "/kriya/tmp"
_CONTAINER_CACHE_PREFIX = "/kriya/cache"

# Fixed, backend-internal choices with no security meaning of their own -
# NOT ContainmentProfile fields (the profile stays tool/mechanism-agnostic;
# these are purely "which image has the right toolchain preinstalled").
# Overridable via env var for a repo that needs a different pinned image,
# without adding new ContainmentProfile/AutonomyConfig surface this pass.
_DEFAULT_IMAGE = os.environ.get("KRIYA_OCI_IMAGE", "debian:bookworm-slim")
_MAVEN_IMAGE = os.environ.get("KRIYA_OCI_MAVEN_IMAGE", "maven:3.9-eclipse-temurin-21")
_PYTHON_IMAGE = os.environ.get("KRIYA_OCI_PYTHON_IMAGE", "python:3.12-slim")

# A fixed PID cap (fork-bomb backstop) - Task B asks for a "process/PID
# limit" but ContainmentProfile has no field for it (not a per-caller
# policy decision the way network/cpu/memory are); a single conservative
# constant here is the narrowest way to provide it without redesigning
# the profile.
_PIDS_LIMIT = "256"

# Coarse rate cap for cpu_seconds (see OCIContainmentBackend.prepare's own
# comment) plus the tmpfs scratch size bound.
_CPU_RATE_CAP = "2"
_TMPFS_SIZE = "512m"


_MAVEN_EXE_RE = re.compile(r"\b(mvn|mvnw|javac|java|jar)\b")
_PYTHON_EXE_RE = re.compile(r"\b(python3?|pytest|pip3?)\b")

# Public (not underscore-prefixed) - kriya/tools/dependency_execution.py
# imports these directly so its own `--dest`/`--find-links` flags always
# agree with wherever THIS module actually mounts the cache, rather than
# two modules each hard-coding the same path string and risking drift.
MAVEN_CACHE_MOUNT = "/root/.m2"
PIP_CACHE_MOUNT = "/root/.cache/pip"


def _select_image_and_cache_mount(command: List[str]) -> Tuple[str, Optional[str]]:
    """Picks a base image (and, for the FIRST declared dependency-cache
    path only, the in-container location the tool actually expects its
    cache at) from the real command about to run - the same "detect from
    real markers, not a guess" spirit `PolymorphicValidator` already uses
    for stack detection, just keyed off argv instead of repo files (this
    module never sees the target repo, only the command).

    Checks argv[0] directly first (validate.py's own real call shape -
    `["mvn", ...]`/`["python3", ...]`), then falls back to a substring
    search over a `/bin/sh -c "..."` wrapper's script text (ShellTool's
    own real call shape) so `sh -c "javac X.java && java X"` still gets a
    JDK-bearing image rather than the toolchain-less default. Falls back
    to the plain default image for anything genuinely unrecognized -
    `/bin/sh` exists on every Debian-family image, so ordinary shell
    commands still work there."""
    exe = os.path.basename(command[0]) if command else ""
    if exe in ("mvn", "mvnw", "mvn.cmd"):
        return _MAVEN_IMAGE, MAVEN_CACHE_MOUNT
    if exe in ("java", "javac", "jar"):
        return _MAVEN_IMAGE, None
    if exe in ("python", "python3", "pytest", "pip", "pip3"):
        return _PYTHON_IMAGE, PIP_CACHE_MOUNT

    script = " ".join(command[2:]) if len(command) >= 3 and command[1] == "-c" else ""
    if script:
        if _MAVEN_EXE_RE.search(script):
            return _MAVEN_IMAGE, MAVEN_CACHE_MOUNT
        if _PYTHON_EXE_RE.search(script):
            return _PYTHON_IMAGE, PIP_CACHE_MOUNT

    return _DEFAULT_IMAGE, None


class OCIContainmentBackend:
    """Real containment via `docker run` - filesystem/network/process/
    resource controls actually enforced by the OCI runtime, not by
    best-effort host rlimits (`NullContainmentBackend`'s ceiling)."""

    name = "oci"

    def __init__(self) -> None:
        self._docker_path = shutil.which("docker")

    def _require_docker(self) -> str:
        if not self._docker_path:
            raise BackendUnavailableError(
                "OCIContainmentBackend requires the 'docker' CLI on PATH - none "
                "found. Refusing to run the command uncontained; this is a "
                "backend-unavailable failure, not an ordinary command failure."
            )
        return self._docker_path

    def _probe_daemon(self, docker_path: str) -> None:
        """A profile requiring containment must not silently degrade to
        uncontained execution just because the docker CLI is present but
        the daemon behind it isn't reachable (Docker Desktop not running,
        VM not started, etc.) - `docker run` would otherwise fail LATER as
        an ordinary nonzero-exit-code command failure (Popen itself
        succeeds; only the container fails to start), which a caller could
        misclassify as "the command failed" rather than "containment was
        never established". Probed explicitly, before ever building the
        real run invocation, so that failure surfaces as
        BackendUnavailableError like every other containment-setup
        failure."""
        try:
            result = subprocess.run(
                [docker_path, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, timeout=10, text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise BackendUnavailableError(f"docker daemon probe failed: {e}") from e
        if result.returncode != 0:
            raise BackendUnavailableError(
                f"docker daemon is not reachable (docker info exited "
                f"{result.returncode}): {result.stderr.strip()[:500]}"
            )

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        docker_path = self._require_docker()

        # NETWORK: only DENIED and UNRESTRICTED have an honest `docker run`
        # implementation this pass. DEPENDENCY_REGISTRY_ONLY (real
        # registry/destination-scoped network authority) would need a
        # proxy or custom network + firewall rules this backend does not
        # implement - equating it with UNRESTRICTED would silently grant
        # full egress to a profile that explicitly asked for less, exactly
        # what Task B's own instruction forbids ("do not equate
        # unrestricted container networking with registry-scoped
        # authority... fail closed for a profile requiring stronger
        # semantics"). Represented honestly: fails closed instead.
        if profile.network is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY:
            raise BackendUnavailableError(
                "OCIContainmentBackend cannot honestly enforce "
                "NetworkAuthority.DEPENDENCY_REGISTRY_ONLY - 'docker run' has "
                "no built-in per-destination network restriction without an "
                "additional proxy/firewall layer this backend does not "
                "implement. Refusing rather than silently running with "
                "unrestricted network access for a profile that asked for "
                "registry-scoped access only. Use NetworkAuthority.DENIED "
                "(fully network-isolated) instead if acquisition happens in "
                "a separate, explicitly UNRESTRICTED step."
            )

        self._probe_daemon(docker_path)

        workspace_host = os.path.abspath(profile.workspace_path)
        if not os.path.isdir(workspace_host):
            raise BackendUnavailableError(
                f"ContainmentProfile.workspace_path {workspace_host!r} does not "
                "exist or is not a directory - refusing to start a container "
                "with no valid workspace mount."
            )

        container_name = f"kriya-oci-{uuid.uuid4().hex[:12]}"
        image, cache_mount_point = _select_image_and_cache_mount(command)

        args: List[str] = [
            docker_path, "run", "--rm", "--name", container_name,
            # Process/resource hardening - real cgroup/namespace controls,
            # not best-effort rlimits.
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", _PIDS_LIMIT,
            "--tmpfs", f"{_CONTAINER_TEMP}:rw,noexec,nosuid,size={_TMPFS_SIZE}",
            # Filesystem: ONLY these mounts ever exist - no host root, no
            # home directory, no Kriya source tree. Minimal and explicit
            # by construction (nothing else is ever added below).
            "-v", f"{workspace_host}:{_CONTAINER_WORKSPACE}:rw",
            "-w", _CONTAINER_WORKSPACE,
        ]

        if profile.temp_path:
            temp_host = os.path.abspath(profile.temp_path)
            if not os.path.isdir(temp_host):
                raise BackendUnavailableError(
                    f"ContainmentProfile.temp_path {temp_host!r} does not exist "
                    "or is not a directory."
                )
            args += ["-v", f"{temp_host}:{_CONTAINER_TEMP}/host:rw"]

        for i, cache_path in enumerate(profile.dependency_cache_paths):
            cache_host = os.path.abspath(cache_path)
            if not os.path.isdir(cache_host):
                raise BackendUnavailableError(
                    f"ContainmentProfile.dependency_cache_paths[{i}] "
                    f"{cache_host!r} does not exist or is not a directory."
                )
            mode = "rw" if profile.dependency_cache_writable else "ro"
            container_path = (
                cache_mount_point if i == 0 and cache_mount_point else f"{_CONTAINER_CACHE_PREFIX}/{i}"
            )
            args += ["-v", f"{cache_host}:{container_path}:{mode}"]

        if profile.network is NetworkAuthority.DENIED:
            args += ["--network", "none"]
        # UNRESTRICTED: no --network flag -> docker's default bridge
        # network, full egress - never used for DEPENDENCY_REGISTRY_ONLY
        # (rejected above), so this never silently under-restricts a
        # profile that asked for something narrower.

        # ENVIRONMENT: explicit allowlist only, baked into argv as
        # `-e KEY=VALUE` (not inherited from the docker CLI's own host
        # environment, which stays untouched/full - the docker CLI is
        # trusted Kriya-invoked tooling, not the sandboxed payload; only
        # names EXPLICITLY on profile.env_allowlist ever reach the
        # container). PATH is deliberately excluded even if present in
        # the allowlist's resolved dict - a host macOS PATH would be
        # meaningless/wrong for a Linux container's own image-provided
        # toolchain PATH.
        if profile.env_allowlist:
            restricted_env = build_restricted_env(profile.env_allowlist)
            for key, value in restricted_env.items():
                if key == "PATH":
                    continue
                args += ["-e", f"{key}={value}"]

        # RESOURCES: --memory is a real, cgroup-enforced hard limit (accurate
        # mapping of profile.memory_mb). --cpus is a RATE cap (cores), not a
        # total CPU-TIME budget - there is no direct `docker run` flag for
        # "N seconds of CPU time total" the way RLIMIT_CPU gives
        # NullContainmentBackend, so profile.cpu_seconds is honored only as
        # a coarse concurrency cap here; the real backstop for "does not run
        # forever" is ProcessController's own wall-clock `timeout`, which
        # applies identically regardless of backend. Not claiming equivalence
        # with RLIMIT_CPU - see this module's docstring / LIMITATIONS.
        if profile.memory_mb is not None:
            mem = f"{profile.memory_mb}m"
            args += ["--memory", mem, "--memory-swap", mem]
        if profile.cpu_seconds is not None:
            args += ["--cpus", _CPU_RATE_CAP]

        args.append(image)

        def _cleanup() -> None:
            # Authoritative teardown - see PreparedContainment.cleanup's
            # own docstring for why --rm alone is not trusted here.
            try:
                subprocess.run(
                    [docker_path, "rm", "-f", container_name],
                    capture_output=True, timeout=15,
                )
            except Exception as e:
                logger.debug("docker rm -f %s failed (best-effort cleanup): %s", container_name, e)

        return PreparedContainment(
            env=None, preexec_fn=None, backend_name=self.name,
            command_prefix=args, cleanup=_cleanup,
            # SEC-001-P6 (managed-service containment): lets a caller
            # (service_runtime.py, for a long-lived container started via
            # start_managed()) run a FOLLOW-UP command inside this exact
            # container after it starts - e.g. a readiness/probe check
            # issued from the host but executed in the container's own
            # network namespace, with zero ports ever published.
            # `-i` (interactive/stdin-attached) is required for a caller's
            # own stdin_payload (a raw HTTP request, for the readiness/probe
            # exec scripts in service_runtime.py) to actually reach the
            # exec'd process - `docker exec` without `-i` leaves stdin
            # unattached, so a `cat >&3` inside the script would read EOF
            # immediately and the request would never be sent (confirmed
            # empirically: readiness/probe hung until ProcessController's
            # own timeout, not a docker-level failure).
            exec_target=[docker_path, "exec", "-i", container_name],
        )
