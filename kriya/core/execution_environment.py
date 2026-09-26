"""Qualification environment identity: the execution environment a model
runtime is served from, as far as it decides capacity.

Functional qualification (does this runtime, called with these settings,
follow Kriya's protocols) does not depend on the machine. Capacity does: whether
a 64K window fits and answers within Kriya's request bound depends on the
accelerator, its memory and the runtime build. ``ExecutionEnvironment`` is the
stable, capability-relevant description of that machine. Environment-dependent
qualification evidence (``ENVIRONMENT_DEPENDENT_CAPABILITIES``) is kept per
environment digest and is only ever used in the environment that produced it.

Rules:
- Only capability classes enter the identity: OS and architecture, the
  inference runtime and version, the accelerator backend, model and count,
  accelerator memory class, system memory class (unified or not), and the
  runtime's parallelism configuration. Memory is a class (``memory_class_gib``),
  never a measurement.
- Never: hostnames, serial numbers, MAC addresses, free memory, temperatures,
  load, or anything else volatile or machine-identifying.
- The environment is observable only when the endpoint is on this machine
  (loopback). A LAN endpoint's machine is not this one, so its environment is
  unavailable and capacity evidence from it is never reused.
- ``exact`` = OS, architecture, backend, system memory class and runtime
  version are all known. Only exact environments reuse capacity evidence.
- Ollama does not report its server-side parallelism (``OLLAMA_NUM_PARALLEL``
  and friends live in the server's environment, not Kriya's), so
  ``runtime_parallelism`` is ``unavailable`` for it; it stays a field so a
  runtime that reports it is identified by it.
- Nothing here is hardware-specific policy: callers only compare digests.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import threading
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

UNAVAILABLE = "unavailable"
ENVIRONMENT_SCHEMA_VERSION = 1
PROBE_ENV_VAR = "KRIYA_EXECUTION_ENVIRONMENT_PROBE"
_COMMAND_TIMEOUT_SECONDS = 5.0
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Qualification cases whose outcome depends on the execution environment.
ENVIRONMENT_DEPENDENT_CAPABILITIES: Tuple[str, ...] = ("context_capacity",)


@dataclass(frozen=True)
class ExecutionEnvironment:
    os: str = UNAVAILABLE
    architecture: str = UNAVAILABLE
    inference_runtime: str = UNAVAILABLE
    accelerator_backend: str = UNAVAILABLE
    accelerator_model: str = UNAVAILABLE
    accelerator_count: int = 0
    accelerator_memory_class_gib: Optional[int] = None
    system_memory_class_gib: Optional[int] = None
    unified_memory: Optional[bool] = None
    runtime_parallelism: str = UNAVAILABLE
    schema_version: int = ENVIRONMENT_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def exact(self) -> bool:
        return (UNAVAILABLE not in (self.os, self.architecture, self.accelerator_backend)
                and self.system_memory_class_gib is not None
                and not self.inference_runtime.endswith("/" + UNAVAILABLE)
                and self.inference_runtime != UNAVAILABLE)

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "digest": self.digest, "exact": self.exact}


def memory_class_gib(total_bytes: Any) -> Optional[int]:
    """Total memory as a class: the nearest multiple of 8 GiB from 16 GiB up
    (a 62.7 GiB Linux MemTotal and a 64 GiB report are both 64; an 80 GiB GPU
    is 80, not 64), the nearest power of two below. Small reporting
    differences never change it; a real capacity change does."""
    try:
        gib = float(total_bytes) / (1 << 30)
    except (TypeError, ValueError):
        return None
    if gib <= 0:
        return None
    if gib >= 12:
        return max(16, int(8 * round(gib / 8)))
    return int(2 ** round(math.log2(gib)))


# Where a tool lives when PATH is minimal (an IDE or launchd start).
_SYSTEM_TOOL_DIRS = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")


def _resolve_tool(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    return next((os.path.join(d, name) for d in _SYSTEM_TOOL_DIRS if os.access(os.path.join(d, name), os.X_OK)), None)


def _run(argv: Sequence[str]) -> Optional[str]:
    tool = _resolve_tool(argv[0])
    if tool is None:
        return None
    try:
        completed = subprocess.run([tool, *argv[1:]], capture_output=True, text=True,
                                   timeout=_COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _linux_memory_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def host_properties(run: Callable[[Sequence[str]], Optional[str]] = _run) -> Dict[str, Any]:
    """The raw capability properties of this machine (no volatile values are
    read at all). ``run`` executes a fixed argv and returns stdout or None."""
    system = platform.system()
    props: Dict[str, Any] = {"os": system.lower() or UNAVAILABLE, "architecture": platform.machine().lower() or UNAVAILABLE}
    if system == "Darwin":
        memory = run(["sysctl", "-n", "hw.memsize"])
        props["memory_bytes"] = int(memory) if memory and memory.isdigit() else None
        props["cpu_model"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        props["gpus"] = []
    elif system == "Linux":
        props["memory_bytes"] = _linux_memory_bytes()
        props["cpu_model"] = None
        gpus: List[Tuple[str, Optional[int]]] = []
        listing = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        for line in (listing or "").splitlines():
            name, _, mib = line.partition(",")
            if name.strip():
                gpus.append((name.strip(), int(mib.strip()) * (1 << 20) if mib.strip().isdigit() else None))
        props["gpus"] = gpus
        props["gpu_backend"] = "cuda" if gpus else ("rocm" if shutil.which("rocm-smi") else None)
    else:
        props["memory_bytes"] = None
        props["gpus"] = []
    return props


def environment_from_properties(props: Dict[str, Any], *, provider: str, provider_version: str) -> ExecutionEnvironment:
    """Map raw host properties to the identity (pure; tested directly)."""
    os_name = str(props.get("os") or UNAVAILABLE)
    arch = str(props.get("architecture") or UNAVAILABLE)
    system_class = memory_class_gib(props.get("memory_bytes"))
    runtime = f"{provider or UNAVAILABLE}/{provider_version or UNAVAILABLE}"
    gpus = [(str(name), mem) for name, mem in props.get("gpus") or []]
    if os_name == "darwin" and arch == "arm64":
        # Apple silicon: the GPU shares system memory (Metal backend).
        return ExecutionEnvironment(
            os=os_name, architecture=arch, inference_runtime=runtime, accelerator_backend="metal",
            accelerator_model=str(props.get("cpu_model") or UNAVAILABLE), accelerator_count=1,
            accelerator_memory_class_gib=system_class, system_memory_class_gib=system_class, unified_memory=True,
        )
    if gpus:
        classes = sorted({memory_class_gib(mem) for _, mem in gpus if mem is not None} - {None})
        return ExecutionEnvironment(
            os=os_name, architecture=arch, inference_runtime=runtime,
            accelerator_backend=str(props.get("gpu_backend") or "cuda"),
            accelerator_model=",".join(sorted({name for name, _ in gpus})), accelerator_count=len(gpus),
            accelerator_memory_class_gib=classes[0] if len(classes) == 1 else None,
            system_memory_class_gib=system_class, unified_memory=False,
        )
    backend = props.get("gpu_backend") or ("cpu" if os_name in ("linux", "darwin") else UNAVAILABLE)
    return ExecutionEnvironment(
        os=os_name, architecture=arch, inference_runtime=runtime, accelerator_backend=str(backend),
        accelerator_model=UNAVAILABLE, accelerator_count=0, system_memory_class_gib=system_class,
        unified_memory=None if backend != "cpu" else False,
    )


def probing_enabled() -> bool:
    return os.environ.get(PROBE_ENV_VAR, "1").strip().lower() not in ("0", "false", "no", "off")


def endpoint_is_this_machine(endpoint: str) -> bool:
    host = (urllib.parse.urlsplit(endpoint or "").hostname or "").lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


_HOST_CACHE: Dict[str, Dict[str, Any]] = {}
_HOST_LOCK = threading.Lock()


def _cached_host_properties() -> Dict[str, Any]:
    with _HOST_LOCK:
        if "host" not in _HOST_CACHE:
            _HOST_CACHE["host"] = host_properties()
        return _HOST_CACHE["host"]


def clear_environment_cache() -> None:
    with _HOST_LOCK:
        _HOST_CACHE.clear()


def execution_environment_for(endpoint: str, *, provider: str, provider_version: str) -> ExecutionEnvironment:
    """The execution environment serving ``endpoint``: this machine's when
    the endpoint is loopback, otherwise unavailable (never guessed)."""
    runtime = f"{provider or UNAVAILABLE}/{provider_version or UNAVAILABLE}"
    if not endpoint_is_this_machine(endpoint) or not probing_enabled():
        return ExecutionEnvironment(inference_runtime=runtime)
    return environment_from_properties(_cached_host_properties(), provider=provider,
                                       provider_version=provider_version)


def environment_for_fingerprint(fingerprint: Any) -> ExecutionEnvironment:
    return execution_environment_for(getattr(fingerprint, "endpoint", ""),
                                     provider=getattr(fingerprint, "provider", UNAVAILABLE),
                                     provider_version=getattr(fingerprint, "provider_version", UNAVAILABLE))


__all__ = [
    "ENVIRONMENT_DEPENDENT_CAPABILITIES", "ExecutionEnvironment", "PROBE_ENV_VAR", "UNAVAILABLE",
    "clear_environment_cache", "endpoint_is_this_machine", "environment_for_fingerprint",
    "environment_from_properties", "execution_environment_for", "host_properties", "memory_class_gib",
]
