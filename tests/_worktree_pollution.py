"""TEST-WORKTREE-POLLUTION-001: the names a mis-scoped package-manager
command leaves in the process working directory. Shared by the per-test
tripwire in tests/conftest.py and the canonical full-suite harness's
post-check (scripts/run_full_suite.py keeps its own copy; the regression
test asserts the two agree)."""
import os
from typing import Iterable, List

PACKAGE_ARTIFACT_NAMES = ("package.json", "package-lock.json", "node_modules")


def present_package_artifacts(root: str) -> List[str]:
    return [name for name in PACKAGE_ARTIFACT_NAMES if os.path.lexists(os.path.join(root, name))]


def new_package_artifacts(root: str, before: Iterable[str]) -> List[str]:
    """The artifacts present under ``root`` now that were absent in ``before``."""
    earlier = set(before)
    return [name for name in present_package_artifacts(root) if name not in earlier]


# OI-45 (E14 preflight): the AppConfig default ``paths.memory = ./memory`` is relative, so a test that builds an
# AppConfig-default config and runs the engine opens/creates <cwd>/memory/*.db - the checkout, when pytest runs there.
MEMORY_DIR_NAME = "memory"


def memory_snapshot(root: str):
    """None when ``<root>/memory`` is absent, else its entries as sorted (name, size, mtime_ns) - a created directory, a
    new store or a write to an existing one changes it (a read-only open of an existing store does not)."""
    path = os.path.join(root, MEMORY_DIR_NAME)
    if not os.path.isdir(path):
        return None
    out = []
    for entry in sorted(os.scandir(path), key=lambda e: e.name):
        stat = entry.stat(follow_symlinks=False)
        out.append((entry.name, stat.st_size, stat.st_mtime_ns))
    return out
