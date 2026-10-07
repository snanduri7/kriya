"""P2: a full-regression failure whose test COMPILATION broke is attributed to
the candidate from the compiler's own diagnostics.

A captured full-regression baseline judges "is this regression the
candidate's?" per test (PRD-024 level 2). A compilation failure runs no test,
so level 2 is unavailable, and before P2 that was read exactly like "every
per-test failure is pre-existing": REGRESSION_UNATTRIBUTED, a stop with no
repair (R2 T4: the candidate's new ``fill(int[],...)`` overload made an
unchanged test call ambiguous; ``ArrayFillTest.java:[167,40]`` was in the
output and never used).

``attribute_compile_regression`` decides it deterministically, and only when
ALL of these hold:

1. Candidate causality. Level 1 is NEW_FAILURE: the PRE run of the same
   suite in the same recorded environment completed and passed (so every
   source it compiles compiled), and POST failed. Any other level-1 class -
   a red or failing PRE, a different environment (NOT_COMPARABLE),
   infrastructure - is never attributed.
2. Level 2 is unavailable. Per-test evidence, when it exists, keeps its own
   rules (G1-DEVINV2 replay and unattributed stop) unchanged.
3. Compiler errors. The POST output carries at least one compiler ERROR
   diagnostic with a file and line (Maven ``[ERROR] <path>.java:[l,c]``,
   javac/Gradle ``<path>.java:l: error:``); warnings and stack frames never
   count.
4. Every one of them resolves to exactly one file in the candidate tree, by
   path (the repository-relative path is a suffix of the diagnostic's path):
   no unresolved, ambiguous or outside-the-tree diagnostic.

The result only names files and lines: the failure then goes through the
ordinary ``regression_test`` route (locator grounding, attribution, the
existing PLAN_SCOPE_REVISION_REQUIRED escalation for a file outside the
stage's scope). Nothing here grants authority.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from kriya.workflow.validation_baseline import DeltaClassification

_SOURCE = r"(?P<path>[^\s:\[\]]+\.(?:java|kt|groovy|scala))"
_MAVEN_ERROR = re.compile(r"^\[ERROR\]\s+" + _SOURCE + r":\[(?P<line>\d+)(?:,\d+)?\]", re.MULTILINE)
_JAVAC_ERROR = re.compile(r"^(?:\[ERROR\]\s+)?" + _SOURCE + r":(?P<line>\d+):\s*error:", re.MULTILINE)
_EXCLUDED_DIRS = frozenset({".git", ".kriya", ".pytest_cache", "__pycache__", "node_modules", "target", "build",
                            "dist", ".venv", "venv", ".gradle"})


@dataclass(frozen=True)
class CompileRegressionAttribution:
    """The candidate tree's files (and lines) the compiler's errors name."""

    files: Tuple[str, ...]
    locations: Tuple[Tuple[str, int], ...]

    def as_dict(self) -> Dict[str, Any]:
        return {"files": list(self.files),
                "locations": [{"filepath": path, "line": line} for path, line in self.locations]}


def compiler_error_locations(output: str) -> List[Tuple[str, int]]:
    """(path as printed, line) of every compiler ERROR diagnostic, in order,
    without duplicates."""
    found: List[Tuple[str, int]] = []
    for pattern in (_MAVEN_ERROR, _JAVAC_ERROR):
        for match in pattern.finditer(output or ""):
            item = (match.group("path").replace("\\", "/"), int(match.group("line")))
            if item not in found:
                found.append(item)
    return found


def _tree_index(root: str) -> Dict[str, List[str]]:
    """basename -> repository-relative paths in the candidate tree."""
    index: Dict[str, List[str]] = {}
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUDED_DIRS)
        for name in files:
            full = os.path.join(current, name)
            if os.path.isfile(full) and not os.path.islink(full):
                index.setdefault(name, []).append(os.path.relpath(full, root).replace(os.sep, "/"))
    return index


def _resolve(path: str, index: Dict[str, List[str]]) -> Optional[str]:
    """The one tree file the diagnostic path ends with, else None."""
    matches = [rel for rel in index.get(path.rsplit("/", 1)[-1], ())
               if path == rel or path.endswith("/" + rel)]
    return matches[0] if len(matches) == 1 else None


def attribute_compile_regression(
    delta: Any, post_output: str, worktree_path: str,
) -> Optional[CompileRegressionAttribution]:
    """See the module docstring; None = not attributable (the caller keeps
    the REGRESSION_UNATTRIBUTED stop). Candidate files resolve like any
    other file in the tree."""
    if delta is None or delta.level2_available:
        return None
    if delta.level1.classification is not DeltaClassification.NEW_FAILURE:
        return None
    diagnostics = compiler_error_locations(post_output)
    if not diagnostics:
        return None
    index = _tree_index(worktree_path)
    locations: List[Tuple[str, int]] = []
    for path, line in diagnostics:
        resolved = _resolve(path, index)
        if resolved is None:
            return None
        if (resolved, line) not in locations:
            locations.append((resolved, line))
    return CompileRegressionAttribution(files=tuple(sorted({path for path, _ in locations})),
                                        locations=tuple(locations))
