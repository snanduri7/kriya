"""The KUP transport's typed error. Since full modularization D2 the snapshot store itself (paths, manifests, listing, integrity,
acquisition, pruning and the snapshot reader) is STATE's ``kriya/control/history``, reached only through the client
(``client.history``); its refusals arrive as ``ApplicationRefusal`` with the same code, message, database state and extra fields."""
from __future__ import annotations

from typing import Any, Optional


class SnapshotError(Exception):
    """A request the KUP adapter refuses itself (argument validation): ``code`` from kriya/kup/policy.py, mapped onto the error envelope."""

    def __init__(self, code: str, message: str, database_state: Optional[str] = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.database_state = database_state
        self.extra = extra
