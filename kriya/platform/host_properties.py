"""The host capability probe (moved from kriya/core/execution_environment.py,
ARCH-PLATFORM-001): the raw, non-volatile hardware properties of this
machine. OS-specific probing lives here, behind one function; the core
module builds the environment identity from the properties it returns.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

UNAVAILABLE = "unavailable"

_COMMAND_TIMEOUT_SECONDS = 5.0

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
