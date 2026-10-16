"""A refusal of STATE's trace-history store as the application's typed refusal (full modularization D2).

``kriya.control.history.SnapshotError`` (code, message, database state, extra fields) becomes ``ApplicationRefusal`` with exactly the same
code, message and fields (``database_state`` among the extra fields when the store named one), so the adapter's error envelope is
unchanged. Shared by ``HistoryService`` and ``EvidenceService`` (the manifest reads a snapshot's run events).
"""
from __future__ import annotations

import contextlib
from typing import Iterator

from kriya.application.services import ApplicationRefusal
from kriya.control.history import SnapshotError


@contextlib.contextmanager
def store_refusals() -> Iterator[None]:
    try:
        yield
    except SnapshotError as error:
        extra = dict(error.extra)
        if error.database_state is not None:
            extra["database_state"] = error.database_state
        raise ApplicationRefusal(error.code, error.message, **extra) from error


__all__ = ["store_refusals"]
