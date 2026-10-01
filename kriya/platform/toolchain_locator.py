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
