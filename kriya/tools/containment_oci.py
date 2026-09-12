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

SEC-006 (2026-09-12): `NetworkAuthority.DEPENDENCY_REGISTRY_ONLY` is now a
real, enforced mechanism - see `_prepare_registry_scoped` and the module
docstring section below - rather than the honest-refusal stub this module
used to raise. See docs/architecture/SEC006_REGISTRY_SCOPED_EGRESS_DESIGN.md
(if present) / the risk register entry for the full design rationale;
summarized here because this is the one module that actually implements it:

    Kriya registry authority (AutonomyConfig.acquisition_registry_hosts)
        -> a per-RUN Squid forward proxy, hostname-ACL'd to exactly those
           hosts, on a fresh per-run Docker network (never shared across
           concurrent runs - no union ACL, no refcounting, so authority
           isolation between two concurrent runs is structural, not proven
           by a separate check)
        -> the acquisition container's OWN iptables (installed by a
           trusted Kriya-authored setup script running as root, BEFORE
           the untrusted command starts): default-deny, loopback + the
           proxy's fixed IP:port only, DNS ports dropped outright (the
           client never needs to resolve anything - the proxy resolves
           registry hostnames on its own), IPv6 closed via sysctl with an
           ip6tables fallback and a live self-test
        -> privilege drop (`setpriv`, not `su`/setuid - stays compatible
           with `--security-opt no-new-privileges`) to a fixed non-root,
           non-CAP_NET_ADMIN UID before the untrusted mvn/pip command ever
           runs - it structurally cannot alter the rules that constrain it
        -> the untrusted command itself, routed through the proxy (Maven
           needs `MAVEN_OPTS`-based proxy properties, NOT just
           `http_proxy`/`https_proxy` - the JVM does not honor those)

A setup failure inside that script (proxy unreachable, firewall/IPv6
verification failed, self-test detected a live bypass) prints a fixed
sentinel and exits a reserved code - `finalize_registry_acquisition_result`
(called by every acquisition call site right after `controller.run()`)
turns that into a `RegistryAcquisitionSetupError`, never an ordinary
"the build failed" result, so a containment failure can never be
misread as a code-level acquisition failure (or silently ignored).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Dict, List, Optional, Tuple

from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    ContainmentSetupError,
    NetworkAuthority,
    PreparedContainment,
    build_restricted_env,
)
from kriya.tools.process import ProcessResult

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

# Never forwarded into the container even when present in a caller's own
# env_allowlist (SEC-001-P6, found live 2026-09-11 - see prepare()'s own
# comment at the forwarding loop for the incident this closes): every one
# of these is a real HOST FILESYSTEM PATH in AutonomyConfig's own packaged
# `sandbox_env_allowlist` default, written for host-mode execution - under
# containment, the image itself already provides a correct value for each
# (HOME=/root, JAVA_HOME=<image's own JDK>, etc.), and the host's own
# value can only ever be wrong there.
_HOST_ONLY_ENV_VARS = frozenset({
    "PATH", "HOME", "TMPDIR", "TEMP", "TMP",
    "JAVA_HOME", "M2_HOME", "GRADLE_HOME", "VIRTUAL_ENV", "PYTHONPATH",
})


_MAVEN_EXE_RE = re.compile(r"\b(mvn|mvnw|javac|java|jar)\b")
_PYTHON_EXE_RE = re.compile(r"\b(python3?|pytest|pip3?)\b")

# Public (not underscore-prefixed) - kriya/tools/dependency_execution.py
# imports these directly so its own `--dest`/`--find-links` flags always
# agree with wherever THIS module actually mounts the cache, rather than
# two modules each hard-coding the same path string and risking drift.
#
# SEC-006 (2026-09-12): relocated from /root/.m2 and /root/.cache/pip.
# `/root` is mode 0700 - unreachable to the non-root UID acquisition now
# drops privileges to (empirically confirmed during design investigation:
# a non-root user cannot even traverse into /root regardless of ownership
# on a subdirectory beneath it). /kriya/cache/* lives entirely under this
# module's own explicit mount tree, which the setup script chmods
# world-writable before dropping privileges.
MAVEN_CACHE_MOUNT = "/kriya/cache/m2"
PIP_CACHE_MOUNT = "/kriya/cache/pip"


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


# --- SEC-006: registry-scoped egress (DEPENDENCY_REGISTRY_ONLY) ---

_PROXY_PORT = 3128
_PROXY_BASE_IMAGE = "debian:bookworm-slim"
_PROXY_IMAGE_TAG = "kriya-acq-proxy-base:1"
_PROXY_DOCKERFILE = f"""FROM {_PROXY_BASE_IMAGE}
RUN apt-get update -qq \\
    && apt-get install -qq -y --no-install-recommends squid netcat-openbsd \\
    && rm -rf /var/lib/apt/lists/*
"""

# Baked into a derived acquisition image (never installed at container-run
# time) - installing at run time would require network access BEFORE the
# firewall is applied, which this design deliberately avoids entirely
# rather than accepting as a trusted-setup-only exception. `util-linux`
# provides `setpriv`; on every base image used here it is already part of
# the minimal base install, but pinning it explicitly keeps this module's
# own requirement visible rather than implicit.
_ACQUISITION_TOOLS_DOCKERFILE = """FROM {base_image}
RUN apt-get update -qq \\
    && apt-get install -qq -y --no-install-recommends iptables iproute2 util-linux netcat-openbsd \\
    && rm -rf /var/lib/apt/lists/*
"""

_SETUP_FAILURE_EXIT_CODE = 97
_SETUP_FAILURE_MARKER = "KRIYA_CONTAINMENT_SETUP_FAILED:"
_STEP_MARKER_RE = re.compile(r"KRIYA_STEP_([A-Z_]+)=(\S+)")

# UID/GID the untrusted acquisition command actually runs as, after setup
# - fixed, not host-UID-matched (no reliable cross-platform way to learn
# the invoking host user's UID from inside this module). Chosen from the
# conventional "nonroot" range (matches distroless's own `nonroot` UID)
# rather than a UID likely to collide with an image's own real accounts.
_ACQUISITION_UID = 65532

# Residual, explicitly-accepted side effect of the non-root requirement
# (ACQUISITION NETWORK step 7: "execute acquisition as non-root"): the
# setup script chmods the bind-mounted workspace/cache trees world-
# writable (permission bits only - ownership is left untouched) so UID
# 65532, which cannot be reliably mapped to the invoking host user's own
# UID from inside this module, can still write into them. This is a
# PERMISSION change, not an OWNERSHIP change, and is scoped to the
# acquisition step only - see this module's own RETURN/DISCOVERIES entry.
_SETUP_SCRIPT_TEMPLATE = r"""#!/bin/sh
set -e
FAIL() {
  echo "KRIYA_CONTAINMENT_SETUP_FAILED: $1" >&2
  exit 97
}

PROXY_IP="__PROXY_IP__"
PROXY_PORT="__PROXY_PORT__"

# --- Step 1: establish proxy connectivity (network still fully open) ---
i=0
until nc -z -w1 "$PROXY_IP" "$PROXY_PORT" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 20 ]; then FAIL "proxy $PROXY_IP:$PROXY_PORT unreachable after 20 attempts"; fi
  sleep 0.5
done
echo "KRIYA_STEP_PROXY_CONNECT=OK"

# --- Steps 2-3: network-layer default-deny, loopback + proxy only ---
iptables -F
iptables -X 2>/dev/null || true
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP
iptables -A OUTPUT -o lo -d 127.0.0.1 -j ACCEPT
iptables -A INPUT -i lo -s 127.0.0.1 -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j DROP
iptables -A OUTPUT -p tcp --dport 53 -j DROP
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT
if ! iptables -C OUTPUT -d "$PROXY_IP" -p tcp --dport "$PROXY_PORT" -j ACCEPT 2>/dev/null; then
  FAIL "firewall rule verification failed - proxy ACCEPT rule not present after insert"
fi
echo "KRIYA_STEP_FIREWALL=OK"

# --- Step 4: explicitly close IPv6 ---
V6_ALL=$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo unknown)
V6_DEFAULT=$(cat /proc/sys/net/ipv6/conf/default/disable_ipv6 2>/dev/null || echo unknown)
V6_VERIFIED=0
if [ "$V6_ALL" = "1" ] && [ "$V6_DEFAULT" = "1" ] && ! ip -6 addr show 2>/dev/null | grep -q "inet6 "; then
  V6_VERIFIED=1
fi
if [ "$V6_VERIFIED" = "1" ]; then
  echo "KRIYA_STEP_IPV6_DISABLE=OK"
else
  if ! ip6tables -P OUTPUT DROP 2>/dev/null; then
    FAIL "IPv6 could not be structurally closed: disable_ipv6 sysctl unverified and ip6tables unavailable"
  fi
  ip6tables -F 2>/dev/null || true
  ip6tables -P INPUT DROP
  ip6tables -P OUTPUT DROP
  ip6tables -P FORWARD DROP
  echo "KRIYA_STEP_IPV6_DISABLE=OK_VIA_IP6TABLES"
fi

# --- Step 5: verify enforcement (live self-test, not an assumption) ---
if nc -z -w2 1.1.1.1 80 2>/dev/null; then
  FAIL "enforcement self-test failed: direct IPv4 egress to 1.1.1.1:80 succeeded"
fi
if nc -6 -z -w2 2606:4700:4700::1111 443 2>/dev/null; then
  FAIL "enforcement self-test failed: direct IPv6 egress succeeded"
fi
if ! nc -z -w2 "$PROXY_IP" "$PROXY_PORT" 2>/dev/null; then
  FAIL "enforcement self-test failed: proxy no longer reachable after firewall setup"
fi
echo "KRIYA_STEP_SELFTEST=OK"

# --- Step 6: drop privileges ---
mkdir -p /kriya/tmp/acqhome
chmod 0777 /kriya/tmp/acqhome
chmod -R a+rwX /kriya/workspace 2>/dev/null || true
chmod -R a+rwX /kriya/cache 2>/dev/null || true

# Maven's own resolver-transport-http does NOT reliably honor
# http.proxyHost/https.proxyHost JVM system properties (confirmed
# empirically, 2026-09-12: MAVEN_OPTS alone still produced a direct
# java.net.UnknownHostException for the registry host - the JVM never
# even attempted to route through the proxy). Maven's OWN documented,
# reliable proxy mechanism is settings.xml's <proxies> block - written
# here, as root, to the image's GLOBAL settings.xml (found from `mvn
# --version`'s own "Maven home" - conditional on the directory existing,
# a no-op for non-Maven images) so no caller-side `-s`/argv change is
# needed. Deliberately NO <mirrors>/<repositories> override: this only
# ever tells Maven HOW to reach a host, never WHICH hosts exist - a
# hostile pom.xml's own <repository> declaration still resolves to
# whatever real host it names, which the firewall+proxy ACL then allows
# or denies exactly as for any other destination (SEC-006 invariant:
# repository content cannot enlarge authority).
if [ -d /usr/share/maven/conf ]; then
  cat > /usr/share/maven/conf/settings.xml <<EOF
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <proxies>
    <proxy>
      <id>kriya-registry-proxy</id>
      <active>true</active>
      <protocol>http</protocol>
      <host>$PROXY_IP</host>
      <port>$PROXY_PORT</port>
      <nonProxyHosts></nonProxyHosts>
    </proxy>
  </proxies>
</settings>
EOF
fi
echo "KRIYA_STEP_PRIVDROP=OK"

export HOME=/kriya/tmp/acqhome
export http_proxy="http://$PROXY_IP:$PROXY_PORT"
export https_proxy="http://$PROXY_IP:$PROXY_PORT"
export HTTP_PROXY="$http_proxy"
export HTTPS_PROXY="$https_proxy"
export MAVEN_OPTS="-Dhttp.proxyHost=$PROXY_IP -Dhttp.proxyPort=$PROXY_PORT -Dhttps.proxyHost=$PROXY_IP -Dhttps.proxyPort=$PROXY_PORT -Dhttp.nonProxyHosts="

# --- Step 7: execute the untrusted acquisition command - non-root, no NET_ADMIN ---
exec setpriv --reuid=__UID__ --regid=__UID__ --clear-groups --no-new-privs -- "$@"
"""


class RegistryAcquisitionSetupError(BackendUnavailableError):
    """A `NetworkAuthority.DEPENDENCY_REGISTRY_ONLY` acquisition container's
    own trusted-setup script (root, running BEFORE the untrusted command)
    could not establish or verify proxy connectivity / the firewall /
    IPv6 closure / privilege drop - raised by
    `finalize_registry_acquisition_result`, which every acquisition call
    site must invoke right after `controller.run()` returns. Deliberately
    a `ContainmentSetupError` subclass, never surfaced as an ordinary
    command failure - a caller must fail the acquisition closed, not
    retry it as if it were a missing-dependency/offline-mode failure."""


def compute_authority_id(hosts: Tuple[str, ...]) -> str:
    """Canonical identity for a set of authorized registry hosts - used
    for observability/labeling only (docker labels, log lines), NEVER for
    infrastructure reuse/refcounting across runs (SEC-006's own explicit
    instruction: no global union ACL, no authority-keyed shared proxy).
    Each real acquisition run still gets its own fresh proxy/network
    regardless of whether another concurrent run shares the same
    authority_id - see `_prepare_registry_scoped`."""
    canonical = ",".join(sorted({h.strip().lower().rstrip(".") for h in hosts if h.strip()}))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _acquisition_image_tag(base_image: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", base_image)
    return f"kriya-acq-tools--{safe}:1"


def _ensure_image(docker_path: str, tag: str, dockerfile: str, *, purpose: str) -> None:
    """Idempotent: builds `tag` from `dockerfile` (piped via stdin - no
    local build context needed, nothing here ever COPY/ADDs a local
    file) only if it doesn't already exist locally. A build failure fails
    the whole acquisition CLOSED (`BackendUnavailableError`, host-side,
    before any container is spawned) - never falls back to running the
    untrusted command in a toolless image or without the firewall
    tooling this image exists to provide."""
    inspect = subprocess.run(
        [docker_path, "image", "inspect", tag], capture_output=True, timeout=10,
    )
    if inspect.returncode == 0:
        return
    build = subprocess.run(
        [docker_path, "build", "-q", "-t", tag, "-"],
        input=dockerfile, capture_output=True, timeout=300, text=True,
    )
    if build.returncode != 0:
        raise BackendUnavailableError(
            f"Failed to build the {purpose} image {tag!r} required for "
            f"NetworkAuthority.DEPENDENCY_REGISTRY_ONLY: {build.stderr.strip()[:800]}"
        )


_NET_ADMIN_AVAILABLE: Optional[bool] = None


def _check_net_admin_available(docker_path: str, acquisition_image: str) -> None:
    """A capability/setup check this module can run entirely host-side,
    before any real acquisition container starts (fail closed BEFORE
    spawn, not after a confusing in-container failure). Some restricted
    CI/container runtimes accept `--cap-add=NET_ADMIN` on `docker run` and
    then still deny the actual `iptables` syscalls - this probes the real
    capability, not just whether the flag was accepted. Cached
    module-wide (NET_ADMIN availability is a property of the docker
    daemon/host, not of any specific profile/run) so this ~1s probe only
    ever runs once per process lifetime, not once per acquisition call."""
    global _NET_ADMIN_AVAILABLE
    if _NET_ADMIN_AVAILABLE is True:
        return
    probe = subprocess.run(
        [
            docker_path, "run", "--rm", "--cap-drop", "ALL", "--cap-add", "NET_ADMIN",
            "--security-opt", "no-new-privileges", acquisition_image, "iptables", "-L", "-n",
        ],
        capture_output=True, timeout=30,
    )
    if probe.returncode != 0:
        _NET_ADMIN_AVAILABLE = False
        raise BackendUnavailableError(
            "NetworkAuthority.DEPENDENCY_REGISTRY_ONLY requires real CAP_NET_ADMIN capability "
            "for the acquisition container's own firewall setup, but a live probe "
            "(`docker run --cap-add=NET_ADMIN ... iptables -L -n`) failed even though the "
            "capability flag was accepted - this docker runtime denies the underlying syscall "
            f"(common on some restricted/rootless CI runtimes): {probe.stderr.decode(errors='replace')[:500]}"
        )
    _NET_ADMIN_AVAILABLE = True


def _render_squid_conf(hosts: Tuple[str, ...]) -> str:
    """Hostname ACL, not resolved-IP - the design investigation's own
    empirical finding: Maven Central/PyPI both terminate at CDN edges with
    large, rotating IP ranges, so a static IP allowlist would be fragile/
    unsafe (this risk's own explicit instruction). `dstdomain` with an
    EXACT hostname (no leading dot) matches that host only, never a
    wildcard subtree - matches AutonomyConfig's own validator, which
    already rejects wildcard/leading-dot entries before they ever reach
    here. Default-deny, no unrestricted CONNECT: only `kriya_allowed`
    hosts are ever permitted for either plain HTTP or HTTPS CONNECT."""
    host_list = " ".join(hosts)
    return (
        f"http_port {_PROXY_PORT}\n"
        f"acl kriya_allowed dstdomain {host_list}\n"
        "http_access allow kriya_allowed\n"
        "http_access deny all\n"
        "cache deny all\n"
        # Squid drops privileges internally to the 'proxy' user
        # (cache_effective_user) and refuses outright to run as root
        # (confirmed empirically: `stdio:/dev/stdout`/`stdio:/dev/stderr`
        # both fail with "Permission denied" since /dev/std{out,err}
        # inside the container are root-owned, and forcing
        # cache_effective_user=root makes squid itself refuse to start
        # with "Don't run Squid as root!"). /var/log/squid/ is already
        # proxy:proxy-owned by the package install, so real log FILES
        # there work - `cleanup()` reads access.log via `docker exec cat`
        # (not `docker logs`) for denied-hostname observability. Default
        # squid access-log format for CONNECT already carries only
        # `host:port` (no path/query); plain HTTP GET lines can carry a
        # full URL including query string - `_extract_denied_hosts` below
        # only ever extracts the HOST, never forwards the raw log line.
        "access_log stdio:/var/log/squid/access.log\n"
        "cache_log /var/log/squid/cache.log\n"
    )


def _container_ip(docker_path: str, container_name: str, network_name: str) -> str:
    inspect = subprocess.run(
        [docker_path, "inspect", container_name, "--format",
         f'{{{{(index .NetworkSettings.Networks "{network_name}").IPAddress}}}}'],
        capture_output=True, timeout=10, text=True,
    )
    ip = inspect.stdout.strip()
    if inspect.returncode != 0 or not ip:
        raise BackendUnavailableError(
            f"Could not determine the proxy container's IP address on network "
            f"{network_name!r}: {inspect.stderr.strip()[:300]}"
        )
    return ip


_DENIED_LOG_RE = re.compile(r"TCP_DENIED\S*\s+\d+\s+(?:CONNECT|GET|POST|HEAD)\s+(\S+)")


def _extract_denied_hosts(proxy_log: str) -> List[str]:
    """Host-only extraction (never the raw log line/full URL) - a plain
    HTTP GET's logged target can carry a query string, which is not
    guaranteed secret-free; only the hostname is ever surfaced."""
    hosts = set()
    for m in _DENIED_LOG_RE.finditer(proxy_log):
        target = m.group(1)
        host = target.split("/", 1)[0].split(":", 1)[0]
        if host:
            hosts.add(host)
    return sorted(hosts)


def _render_setup_script(proxy_ip: str) -> str:
    return (
        _SETUP_SCRIPT_TEMPLATE
        .replace("__PROXY_IP__", proxy_ip)
        .replace("__PROXY_PORT__", str(_PROXY_PORT))
        .replace("__UID__", str(_ACQUISITION_UID))
    )


def finalize_registry_acquisition_result(result: ProcessResult) -> None:
    """Every `NetworkAuthority.DEPENDENCY_REGISTRY_ONLY` acquisition call
    site (kriya/tools/dependency_execution.py, kriya/tools/validate.py)
    MUST call this immediately after `controller.run()` returns - a
    containment-setup failure inside the acquisition container's own
    trusted setup script is otherwise indistinguishable from an ordinary
    mvn/pip failure (same nonzero returncode shape). Raises
    `RegistryAcquisitionSetupError` (never returns a value) if the
    script's own fixed failure sentinel/exit code is present; otherwise
    logs the setup steps that DID complete, for observability, and
    returns normally."""
    combined = f"{result.stdout}\n{result.stderr}"
    if result.returncode == _SETUP_FAILURE_EXIT_CODE or _SETUP_FAILURE_MARKER in combined:
        reason = "unknown (sentinel exit code seen without a matching failure line)"
        for line in combined.splitlines():
            if _SETUP_FAILURE_MARKER in line:
                reason = line.split(_SETUP_FAILURE_MARKER, 1)[1].strip()
                break
        logger.warning("Registry-scoped acquisition containment setup FAILED: %s", reason)
        raise RegistryAcquisitionSetupError(
            "NetworkAuthority.DEPENDENCY_REGISTRY_ONLY containment setup failed inside the "
            f"acquisition container before the untrusted command could run: {reason}"
        )
    steps = dict(_STEP_MARKER_RE.findall(combined))
    if steps:
        logger.info("Registry-scoped acquisition containment steps completed: %s", steps)


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

        if profile.network is NetworkAuthority.DEPENDENCY_REGISTRY_ONLY:
            self._probe_daemon(docker_path)
            return self._prepare_registry_scoped(docker_path, profile, command)

        # UNRESTRICTED/DENIED - unchanged from the original SEC-001-P6 shape.
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
        # (handled in its own branch above), so this never silently
        # under-restricts a profile that asked for something narrower.

        # ENVIRONMENT: explicit allowlist only, baked into argv as
        # `-e KEY=VALUE` (not inherited from the docker CLI's own host
        # environment, which stays untouched/full - the docker CLI is
        # trusted Kriya-invoked tooling, not the sandboxed payload; only
        # names EXPLICITLY on profile.env_allowlist ever reach the
        # container). `_HOST_ONLY_ENV_VARS` is excluded even when present
        # in the allowlist's resolved dict - found live, 2026-09-11: this
        # host's own `JAVA_HOME` (a macOS JDK path,
        # `sandbox_env_allowlist`'s own packaged default includes
        # "JAVA_HOME") was forwarded into a Maven container verbatim,
        # breaking Maven's own launcher script ("JAVA_HOME environment
        # variable is not defined correctly") since that path does not
        # exist inside the container at all. `sandbox_env_allowlist`'s
        # default list was written for host-mode execution, where
        # forwarding these is exactly right; every name in it is a real
        # host FILESYSTEM PATH with its own correct, image-provided
        # default already set inside any real toolchain container (HOME,
        # JAVA_HOME, etc.) - forwarding the host's own value can only ever
        # be wrong there, never merely redundant, so these never reach the
        # container regardless of what a caller's env_allowlist contains
        # (Invariant: host paths/interpreters must not leak accidentally
        # into container execution).
        for key, value in build_restricted_env(profile.env_allowlist).items():
            if key in _HOST_ONLY_ENV_VARS:
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
            # scripts in service_runtime.py) to actually reach the
            # exec'd process - `docker exec` without `-i` leaves stdin
            # unattached, so a `cat >&3` inside the script would read EOF
            # immediately and the request would never be sent (confirmed
            # empirically: readiness/probe hung until ProcessController's
            # own timeout, not a docker-level failure).
            exec_target=[docker_path, "exec", "-i", container_name],
        )

    def _prepare_registry_scoped(
        self, docker_path: str, profile: ContainmentProfile, command: List[str],
    ) -> PreparedContainment:
        """SEC-006: the real `NetworkAuthority.DEPENDENCY_REGISTRY_ONLY`
        implementation - see this module's own docstring for the full
        `authority -> proxy -> forced routing -> privilege drop` chain.
        Every check that CAN be done host-side, before any container that
        will run the untrusted command exists, is done here - a failure
        here is always `BackendUnavailableError` (fail closed, before
        spawn), never an in-container ambiguity."""
        hosts: Tuple[str, ...] = tuple(sorted(set(profile.network_destinations)))
        if not hosts:
            raise BackendUnavailableError(
                "NetworkAuthority.DEPENDENCY_REGISTRY_ONLY requires a non-empty "
                "network_destinations set - refusing to start an acquisition container with "
                "no authorized registry destinations at all (this would either be a silent "
                "full-deny that looks like a hang, or a configuration bug that must be fixed "
                "in AutonomyConfig.acquisition_registry_hosts, not worked around here)."
            )

        workspace_host = os.path.abspath(profile.workspace_path)
        if not os.path.isdir(workspace_host):
            raise BackendUnavailableError(
                f"ContainmentProfile.workspace_path {workspace_host!r} does not exist or is "
                "not a directory - refusing to start a container with no valid workspace mount."
            )

        authority_id = compute_authority_id(hosts)
        image, cache_mount_point = _select_image_and_cache_mount(command)
        acq_image_tag = _acquisition_image_tag(image)
        _ensure_image(
            docker_path, acq_image_tag, _ACQUISITION_TOOLS_DOCKERFILE.format(base_image=image),
            purpose="acquisition firewall-tooling",
        )
        _ensure_image(docker_path, _PROXY_IMAGE_TAG, _PROXY_DOCKERFILE, purpose="registry proxy")
        _check_net_admin_available(docker_path, acq_image_tag)

        run_id = uuid.uuid4().hex[:12]
        network_name = f"kriya-acq-net-{run_id}"
        proxy_name = f"kriya-acq-proxy-{run_id}"
        container_name = f"kriya-oci-{run_id}"

        logger.info(
            "Registry-scoped acquisition starting: authority_id=%s allowed_host_count=%d "
            "allowed_hosts=%s run_id=%s", authority_id, len(hosts), list(hosts), run_id,
        )

        net_create = subprocess.run(
            [docker_path, "network", "create", "--label", f"kriya.authority={authority_id}", network_name],
            capture_output=True, timeout=15, text=True,
        )
        if net_create.returncode != 0:
            raise BackendUnavailableError(
                f"Failed to create the per-run acquisition network {network_name!r}: "
                f"{net_create.stderr.strip()[:400]}"
            )

        def _remove_network() -> None:
            subprocess.run([docker_path, "network", "rm", network_name], capture_output=True, timeout=15)

        try:
            squid_conf = _render_squid_conf(hosts)
            proxy_run = subprocess.run(
                [
                    docker_path, "run", "-d", "--name", proxy_name,
                    "--label", f"kriya.authority={authority_id}",
                    "--network", network_name, "--cap-drop", "ALL",
                    # Squid drops ITS OWN privileges internally (root ->
                    # 'proxy' user, via setuid/setgid) at startup and
                    # refuses to run as root - confirmed empirically:
                    # --cap-drop ALL alone makes that internal drop fail
                    # ("setgid: Operation not permitted") and squid
                    # crash-loops (exit 139). SETUID/SETGID are the only
                    # two capabilities added back - this container is
                    # trusted Kriya-managed infrastructure (runs a fixed,
                    # Kriya-authored config, never untrusted repo content),
                    # not the payload NetworkAuthority constrains.
                    "--cap-add", "SETUID", "--cap-add", "SETGID",
                    "--security-opt", "no-new-privileges",
                    "--entrypoint", "sh", _PROXY_IMAGE_TAG,
                    "-c", f"printf '%s' {_shell_quote(squid_conf)} > /etc/squid/kriya.conf && "
                          f"exec squid -N -f /etc/squid/kriya.conf",
                ],
                capture_output=True, timeout=20, text=True,
            )
            if proxy_run.returncode != 0:
                _remove_network()
                raise BackendUnavailableError(
                    f"Failed to start the registry proxy {proxy_name!r}: {proxy_run.stderr.strip()[:400]}"
                )
        except BackendUnavailableError:
            raise
        except Exception as e:
            _remove_network()
            raise BackendUnavailableError(f"Failed to start the registry proxy: {e}") from e

        def _remove_proxy_and_log_denied() -> None:
            try:
                logs = subprocess.run(
                    [docker_path, "exec", proxy_name, "cat", "/var/log/squid/access.log"],
                    capture_output=True, timeout=10, text=True,
                )
                denied = _extract_denied_hosts(logs.stdout or "")
                if denied:
                    logger.warning(
                        "Registry-scoped acquisition (authority_id=%s): proxy denied requests to "
                        "unauthorized host(s): %s - not auto-authorized; add to "
                        "autonomy.acquisition_registry_hosts if this is a legitimate registry "
                        "dependency.", authority_id, denied,
                    )
            except Exception as e:
                logger.debug("Could not read proxy logs for %s: %s", proxy_name, e)
            subprocess.run([docker_path, "rm", "-f", proxy_name], capture_output=True, timeout=15)

        try:
            # Host-side readiness probe (fail closed if the proxy never
            # comes up - never spawn the acquisition container against an
            # unready/misconfigured proxy).
            ready = False
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                probe = subprocess.run(
                    [docker_path, "exec", proxy_name, "nc", "-z", "-w1", "localhost", str(_PROXY_PORT)],
                    capture_output=True, timeout=5,
                )
                if probe.returncode == 0:
                    ready = True
                    break
                time.sleep(0.5)
            if not ready:
                raise BackendUnavailableError(
                    f"Registry proxy {proxy_name!r} did not become ready within 15s - refusing "
                    "to start acquisition (proxy readiness is a fail-closed precondition, never "
                    "an unrestricted-fallback trigger)."
                )
            proxy_ip = _container_ip(docker_path, proxy_name, network_name)
        except BackendUnavailableError:
            _remove_proxy_and_log_denied()
            _remove_network()
            raise
        except Exception as e:
            _remove_proxy_and_log_denied()
            _remove_network()
            raise BackendUnavailableError(f"Registry proxy readiness check failed: {e}") from e

        setup_script = _render_setup_script(proxy_ip)

        args: List[str] = [
            docker_path, "run", "--rm", "--name", container_name,
            "--label", f"kriya.authority={authority_id}",
            "--network", network_name,
            # NET_ADMIN: the trusted setup script's own iptables/ip6tables
            # calls. SETUID/SETGID: that SAME script's privilege-drop
            # (`setpriv --reuid/--regid`) needs them to change UID/GID
            # away from root at all (confirmed empirically: without these,
            # setpriv itself fails with "setresuid failed: Operation not
            # permitted", before the untrusted command ever runs). Neither
            # capability survives the UID change into the untrusted
            # command's own process: Linux clears a process's effective/
            # permitted capability sets on a UID transition away from root
            # unless the caller explicitly asks to keep them (`setpriv
            # --keep-caps`, never used here) - verified live via
            # adversarial test M (untrusted process cannot regain
            # NET_ADMIN/root).
            "--cap-drop", "ALL", "--cap-add", "NET_ADMIN",
            "--cap-add", "SETUID", "--cap-add", "SETGID",
            "--security-opt", "no-new-privileges",
            "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
            "--sysctl", "net.ipv6.conf.default.disable_ipv6=1",
            "--pids-limit", _PIDS_LIMIT,
            "--tmpfs", f"{_CONTAINER_TEMP}:rw,size={_TMPFS_SIZE}",
            "-v", f"{workspace_host}:{_CONTAINER_WORKSPACE}:rw",
            "-w", _CONTAINER_WORKSPACE,
        ]

        for i, cache_path in enumerate(profile.dependency_cache_paths):
            cache_host = os.path.abspath(cache_path)
            if not os.path.isdir(cache_host):
                _remove_proxy_and_log_denied()
                _remove_network()
                raise BackendUnavailableError(
                    f"ContainmentProfile.dependency_cache_paths[{i}] {cache_host!r} does not "
                    "exist or is not a directory."
                )
            mode = "rw" if profile.dependency_cache_writable else "ro"
            container_path = (
                cache_mount_point if i == 0 and cache_mount_point else f"{_CONTAINER_CACHE_PREFIX}/{i}"
            )
            args += ["-v", f"{cache_host}:{container_path}:{mode}"]

        for key, value in build_restricted_env(profile.env_allowlist).items():
            if key in _HOST_ONLY_ENV_VARS:
                continue
            args += ["-e", f"{key}={value}"]

        if profile.memory_mb is not None:
            mem = f"{profile.memory_mb}m"
            args += ["--memory", mem, "--memory-swap", mem]
        if profile.cpu_seconds is not None:
            args += ["--cpus", _CPU_RATE_CAP]

        args += ["--entrypoint", "/bin/sh", acq_image_tag, "-c", setup_script, "kriya-acq-cmd"]

        def _cleanup() -> None:
            try:
                subprocess.run([docker_path, "rm", "-f", container_name], capture_output=True, timeout=15)
            except Exception as e:
                logger.debug("docker rm -f %s failed (best-effort cleanup): %s", container_name, e)
            _remove_proxy_and_log_denied()
            _remove_network()

        return PreparedContainment(
            env=None, preexec_fn=None, backend_name=self.name,
            command_prefix=args, cleanup=_cleanup,
        )


def _shell_quote(text: str) -> str:
    """POSIX single-quote a string for embedding into a `sh -c "..."`
    argument built in Python (the squid.conf content, written into the
    proxy container by its own startup `sh -c`) - handles literal single
    quotes in `text` (none exist in a generated squid.conf today, but this
    stays correct if that ever changes) without needing an external
    library."""
    return "'" + text.replace("'", "'\\''") + "'"
