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
