"""Java language adapter: how Kriya finds Java sources, moved unchanged from
kriya/tools/validate.py (Capability Adapters R1). Two walks with two skip
sets, exactly as before: detection skips build/dependency/virtualenv
directories; enumeration skips only VCS, Kriya state and Maven output."""
from __future__ import annotations

import os
from typing import List

from kriya.capabilities.ports import LanguageAdapter

_DETECTION_SKIP_DIRS = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", "target", ".kriya",
})
_ENUMERATION_SKIP_DIRS = (".git", ".kriya", "target")


class JavaLanguageAdapter(LanguageAdapter):
    language = "java"

    def has_sources(self, workspace_root: str) -> bool:
        """Recognize standalone Java sources without requiring build metadata."""
        for _root, dirs, filenames in os.walk(workspace_root):
            dirs[:] = [name for name in dirs if name not in _DETECTION_SKIP_DIRS]
            if any(name.endswith(".java") for name in filenames):
                return True
        return False

    def source_files(self, workspace_root: str) -> List[str]:
        """Every .java source of the project, workspace-relative (build output,
        VCS and Kriya state excluded)."""
        sources = []
        for directory, dirnames, names in os.walk(workspace_root):
            dirnames[:] = [d for d in dirnames if d not in _ENUMERATION_SKIP_DIRS]
            sources += [os.path.relpath(os.path.join(directory, n), workspace_root)
                        for n in names if n.endswith(".java")]
        return sorted(sources)
