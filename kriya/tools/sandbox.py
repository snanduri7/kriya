import logging
import os
import sys
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

    SEC-001 fail-closed correction (2026-09-11): a `setrlimit` failure here
    now RAISES instead of being logged and swallowed - the prior behavior
    let a subprocess start completely unbounded whenever limit application
    failed for any reason, silently defeating the one resource control this
    function exists to provide. `subprocess.Popen`/`asyncio.create_subprocess_exec`
    both catch any exception raised inside `preexec_fn` and re-raise it in
    the PARENT as `subprocess.SubprocessError("Exception occurred in
    preexec_fn.")` (confirmed empirically, both sync and async - the
    original exception type/message do not survive the errpipe, only a
    generic SubprocessError does) - `ProcessController` is the layer that
    catches that specific exception and converts it into a real, typed
    `ContainmentSetupError` (kriya/tools/containment.py), which callers can
    distinguish from an ordinary command/compile/test failure. This
    function does not change on the happy path - only the failure path
    changes, from "continue unprotected" to "abort before the command runs".

    Still best-effort in what it can PROTECT, not in what it now REPORTS -
    and the two are handled differently on purpose:
    - RLIMIT_CPU is reliably settable and enforced on both Linux and macOS
      (confirmed empirically on this host) - a failure here fails closed.
    - RLIMIT_AS (address space) is reliably settable on Linux, but on macOS
      `setrlimit(RLIMIT_AS, ...)` itself can outright FAIL - not merely be
      weakly enforced once set - confirmed empirically on real macOS
      26.6.2/arm64: the call raises `ValueError("current limit exceeds
      maximum limit")` even for an ordinary, well-formed value, because the
      platform's reported RLIMIT_AS hard limit is RLIM_INFINITY in a way
      that rejects lowering it via this call. Failing closed on this
      specific, platform-known limitation would break every real macOS
      caller (ShellTool, PolymorphicValidator's compile/test gates) out of
      the box - the opposite of this fix's own intent. So ONLY on macOS,
      an RLIMIT_AS failure is caught and logged (matching the design's own
      explicit "memory containment stays advisory-only on macOS regardless
      of backend" conclusion) - everywhere else (Linux, where RLIMIT_AS is
      reliably settable), the same failure fails closed like RLIMIT_CPU
      does, since there it's a genuine, actionable signal.
    - Deliberately does not set RLIMIT_NPROC: on Linux it's a per-EUID limit (shared
      across every process the user owns, not just this subprocess tree), so a low
      value risks interfering with unrelated processes running under the same account.
    """
    if sys.platform == "win32":
        return None

    def _set_limits() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

        memory_bytes = memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError) as e:
            if sys.platform == "darwin":
                logger.debug(
                    f"RLIMIT_AS could not be set on macOS (known platform "
                    f"limitation, memory containment stays advisory-only "
                    f"here): {e}"
                )
            else:
                raise

    return _set_limits
