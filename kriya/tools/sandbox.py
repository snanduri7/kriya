import os
import sys
import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_restricted_env(allowlist: List[str]) -> Dict[str, str]:
    """Builds a subprocess environment containing only allowlisted variable names
    from the current process environment (plus PATH, always included).

    This blocks the common case of a subprocess inheriting secrets (API keys,
    cloud credentials, SSH agent sockets, etc.) that happen to be sitting in the
    parent shell environment. It does not stop a subprocess from reading
    credential files directly off disk, or from reaching the network - it only
    narrows what's handed to it via the environment.
    """
    restricted = {"PATH": os.environ.get("PATH", "")}
    for key in allowlist:
        if key in os.environ:
            restricted[key] = os.environ[key]
    return restricted


def posix_resource_limits_preexec_fn(cpu_seconds: int, memory_mb: int) -> Optional[Callable[[], None]]:
    """Returns a preexec_fn that caps CPU time and address space for a subprocess,
    or None on non-POSIX platforms (preexec_fn isn't supported on Windows).

    Best-effort, not a hard security boundary:
    - RLIMIT_CPU is well-enforced on Linux and macOS.
    - RLIMIT_AS (address space) is well-enforced on Linux but historically weakly
      enforced by the Darwin/XNU kernel - treat the memory cap as advisory on macOS.
    - Deliberately does not set RLIMIT_NPROC: on Linux it's a per-EUID limit (shared
      across every process the user owns, not just this subprocess tree), so a low
      value risks interfering with unrelated processes running under the same account.
    """
    if sys.platform == "win32":
        return None

    def _set_limits() -> None:
        import resource

        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except (ValueError, OSError) as e:
            logger.debug(f"Failed to set RLIMIT_CPU: {e}")

        try:
            memory_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError) as e:
            logger.debug(f"Failed to set RLIMIT_AS: {e}")

    return _set_limits
