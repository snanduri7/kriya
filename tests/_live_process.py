"""Live-tier subprocess runs that keep their evidence when they time out.

A bare subprocess.TimeoutExpired shows only the command, so a hosted run
cannot tell a slow-but-progressing pipeline from a hang. On timeout this
fails with the elapsed time and the tail of what the process printed.
Imported by bare name (tests/ is on sys.path under pytest's default import
mode).
"""
import subprocess
import time
from typing import Any, List

TAIL_CHARS = 6000


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def run_reporting_timeout(command: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    started = time.monotonic()
    try:
        return subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(
            f"timed out after {time.monotonic() - started:.0f}s (limit {error.timeout}s): {command[:4]}\n"
            f"--- stdout (tail) ---\n{_text(error.stdout)[-TAIL_CHARS:]}\n"
            f"--- stderr (tail) ---\n{_text(error.stderr)[-TAIL_CHARS:]}"
        ) from error
