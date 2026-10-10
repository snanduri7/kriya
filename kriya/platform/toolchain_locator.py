"""JDK home discovery (PLAT-015, moved from kriya/workflow/toolchain.py).

Where a specific JDK major version lives is platform mechanism: macOS ships
/usr/libexec/java_home, Linux resolves 'java' on PATH through its symlinks.
The workflow asks this locator; it never branches on the OS itself.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)


def jdk_home_for_version(version: str) -> Optional[str]:
    """Resolves the real JDK home directory for a SPECIFIC major version
    number, using whichever mechanism is actually reliable on this platform
    - not one "portable" heuristic, since what 'java' on PATH even points to
    differs fundamentally by OS.

    Confirmed live, 2026-08-07, as a real, damaging bug in the original
    single-heuristic design: on macOS, 'java' on PATH is ALWAYS Apple's own
    dispatcher stub (`/usr/bin/java`) - a real, non-symlinked, root-owned
    file, never a symlink into an actual JDK. Deriving a JDK home by walking
    up from it (dirname(dirname(realpath('java')))) silently produced
    '/usr' - a directory that happened to satisfy the '.../bin/java' layout
    check but obviously isn't a JDK home. Set as JAVA_HOME, it hung a real
    `mvn clean compile` subprocess indefinitely rather than erroring
    cleanly, discovered live via `ps` showing the process stuck with near-
    zero CPU time. macOS ships exactly the right tool for this instead:
    `/usr/libexec/java_home -v <version>`, which resolves a SPECIFIC
    registered JDK version directly - more precise than deriving from
    whatever 'java' happens to point to, and unaffected by 'java' being a
    stub at all.

    On non-macOS platforms (Linux, where 'java' on PATH is typically a real
    symlink chain into an actual JDK install via update-alternatives or
    similar - no equivalent stub layer), falls back to resolving 'java' on
    PATH through any symlinks and relying on the standard
    '<JDK home>/bin/java' layout convention; the caller has already
    confirmed via check_java_toolchain() that 'java' resolves to the wanted
    version before this is ever called, so no version re-validation is
    needed on that path.

    Best-effort and defensive throughout - returns None (never raises) if
    the version can't actually be resolved this way."""
    if sys.platform == "darwin" and os.path.exists("/usr/libexec/java_home"):
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home", "-v", version],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                home = result.stdout.strip()
                return home if home and os.path.isdir(home) else None
        except Exception as e:
            logger.debug(f"Failed to resolve JDK {version} home via /usr/libexec/java_home: {e}")
        return None

    java_path = shutil.which("java")
    if not java_path:
        return None
    try:
        real_path = os.path.realpath(java_path)
        bin_dir = os.path.dirname(real_path)
        if os.path.basename(bin_dir) != "bin":
            return None
        jdk_home = os.path.dirname(bin_dir)
        return jdk_home if os.path.isdir(jdk_home) else None
    except Exception as e:
        logger.debug(f"Failed to resolve JDK home from 'java' on PATH: {e}")
        return None


READINESS_TOOLS = ("python3", "git", "java", "mvn", "gradle", "node")
_RUNNABLE_CHECK = ("python3", "git", "node")  # cheap `--version`; mvn/gradle start a JVM and are only looked up


def _first_runnable(tool: str, search_path: str) -> dict:
    """The first `tool` on ``search_path`` that RUNS (`--version` exits 0). The macOS Xcode shims (/usr/bin/git, /usr/bin/python3 when the developer
    tools are not installed) exist but exit 69, so being on PATH is not being usable. Read-only; the environment is never changed."""
    seen_failure = None
    for directory in search_path.split(os.pathsep):
        candidate = os.path.join(directory, tool)
        if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
            continue
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=5)
        except Exception as error:
            seen_failure = seen_failure or f"{candidate}: {type(error).__name__}"
            continue
        if result.returncode == 0:
            return {"tool": tool, "available": True, "path": candidate, "checked": "ran --version"}
        seen_failure = seen_failure or f"{candidate} exits {result.returncode}"
    if seen_failure:
        return {"tool": tool, "available": False, "path": None, "checked": "ran --version", "reason": f"found but does not run ({seen_failure})"}
    return {"tool": tool, "available": False, "path": None, "checked": "not on PATH"}


def tool_visibility() -> list:
    """Which common build tools THIS process can use (GUI M7 execution readiness), under this process's own PATH - never a login shell, never an
    extended PATH. python3/git/node are checked by running `--version` (a shim that fails is reported unavailable, with the reason); mvn/gradle are
    looked up only; `java` on macOS is whether a JDK is registered with `/usr/libexec/java_home` (the absolute-path mechanism the workflow uses; Apple's
    /usr/bin/java is only a stub). Each entry: {tool, available, path, checked, [reason]}. Whether a goal needs a tool is not knowable before the
    workflow runs, so this reports usability only."""
    search_path = os.environ.get("PATH", "")
    # the `--version` runs are independent: concurrently, the preflight costs the slowest one instead of their sum
    with ThreadPoolExecutor(max_workers=len(_RUNNABLE_CHECK)) as pool:
        runnable = {tool: pool.submit(_first_runnable, tool, search_path) for tool in _RUNNABLE_CHECK}
    report = []
    for tool in READINESS_TOOLS:
        if tool in runnable:
            report.append(runnable[tool].result())
            continue
        found = shutil.which(tool)
        checked = "found on PATH" if found else "not on PATH"
        if tool == "java" and sys.platform == "darwin" and os.path.exists("/usr/libexec/java_home"):
            checked = "registered JDK (java_home)"
            try:
                result = subprocess.run(["/usr/libexec/java_home"], capture_output=True, text=True, timeout=5)
                home = result.stdout.strip() if result.returncode == 0 else ""
                found = os.path.join(home, "bin", "java") if home and os.path.isdir(home) else None
            except Exception as error:
                logger.debug(f"java_home failed: {error}")
                found = None
        report.append({"tool": tool, "available": found is not None, "path": found, "checked": checked})
    return report
