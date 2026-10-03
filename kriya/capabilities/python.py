"""Python language adapter: how Kriya finds Python sources, moved unchanged
from kriya/tools/validate.py (Capability Adapters R1, Python slice). The walk
skips VCS, Kriya state, dependency/virtualenv and build output directories."""
from __future__ import annotations

import os
from typing import List

from kriya.capabilities.ports import LanguageAdapter

_SKIP_DIRS = frozenset({".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".kriya"})


class PythonLanguageAdapter(LanguageAdapter):
    language = "python"

    def has_sources(self, workspace_root: str) -> bool:
        """Bounded recursive fallback for a Python project with none of the
        standard marker files (e.g. a single-script goal with no packaging
        metadata yet) - stops at the first hit, skips common non-source/
        dependency directories so it doesn't walk a huge vendored tree."""
        for _root, dirs, filenames in os.walk(workspace_root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            if any(f.endswith(".py") for f in filenames):
                return True
        return False

    def source_files(self, workspace_root: str) -> List[str]:
        """Every .py source, workspace-relative, sorted (the same skip set)."""
        sources = []
        for directory, dirnames, names in os.walk(workspace_root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            sources += [os.path.relpath(os.path.join(directory, n), workspace_root)
                        for n in names if n.endswith(".py")]
        return sorted(sources)
