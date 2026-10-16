"""APPLICATION contracts shared by the services (MODULE-CONTRACTS §15; slice B2).

``ApplicationRefusal`` is the one typed refusal a service raises. Its ``reason_code`` is a member of ``REFUSAL_CODES``, a closed
table whose strings are IDENTICAL to the KUP error vocabulary (``kriya/kup/policy.py``): the adapter maps a refusal 1:1 onto its
error envelope (code, message, extra fields), so a client sees exactly what the operation body answered before the extraction.

``TraceHistory`` is the port the history and evidence services read a published trace-history snapshot through. Its implementation
is STATE's snapshot reader (``kriya.control.history.SnapshotTrace``, moved from KUP in D2), built by the services from the published
``Snapshot`` value a request names. The services never open the trace database themselves:
APPLICATION owns no sqlite, http, environment, home or working-directory access (tests/architecture/test_direct_access_owners.py).
"""
from __future__ import annotations

from typing import Any, Callable, ContextManager, Dict, FrozenSet, List, Optional, Protocol, Tuple

from kriya.foundation.errors import KriyaError

INVALID_REQUEST = "INVALID_REQUEST"
EVIDENCE_STORE_UNREADABLE = "EVIDENCE_STORE_UNREADABLE"
EVIDENCE_SCHEMA_UNSUPPORTED = "EVIDENCE_SCHEMA_UNSUPPORTED"
EVIDENCE_STORE_NOT_FOUND = "EVIDENCE_STORE_NOT_FOUND"
EVIDENCE_VERIFY_FAILED = "EVIDENCE_VERIFY_FAILED"
EVIDENCE_HEAD_CHANGED = "EVIDENCE_HEAD_CHANGED"
EVIDENCE_CONTENT_UNAVAILABLE = "EVIDENCE_CONTENT_UNAVAILABLE"
EVIDENCE_BLOB_TOO_LARGE = "EVIDENCE_BLOB_TOO_LARGE"
# B3 (write/run side): the refusals the configuration, project, review and run use cases raise, unchanged on the wire.
CONFIG_LOAD_FAILED = "CONFIG_LOAD_FAILED"
WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"
LAUNCH_NOT_FOUND = "LAUNCH_NOT_FOUND"
REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
REVIEW_DIFF_UNAVAILABLE = "REVIEW_DIFF_UNAVAILABLE"
REVIEW_DIFF_UNVERIFIED = "REVIEW_DIFF_UNVERIFIED"
REVIEW_COMMAND_UNVERIFIED = "REVIEW_COMMAND_UNVERIFIED"
APPROVAL_STALE = "APPROVAL_STALE"
APPROVAL_UNAVAILABLE = "APPROVAL_UNAVAILABLE"
# D2 (the trace-history snapshot store moved to STATE): the store's refusals, unchanged on the wire.
READ_ONLY_UNAVAILABLE = "READ_ONLY_UNAVAILABLE"
STORE_BUSY = "STORE_BUSY"
SNAPSHOT_MISSING = "SNAPSHOT_MISSING"
SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
SNAPSHOT_TOO_LARGE = "SNAPSHOT_TOO_LARGE"
SNAPSHOT_CORRUPT = "SNAPSHOT_CORRUPT"
ACQUISITION_IN_PROGRESS = "ACQUISITION_IN_PROGRESS"
ACQUISITION_REFUSED_RUN_ACTIVE = "ACQUISITION_REFUSED_RUN_ACTIVE"
REFUSAL_CODES: FrozenSet[str] = frozenset({
    INVALID_REQUEST, EVIDENCE_STORE_UNREADABLE, EVIDENCE_SCHEMA_UNSUPPORTED, EVIDENCE_STORE_NOT_FOUND, EVIDENCE_VERIFY_FAILED,
    EVIDENCE_HEAD_CHANGED, EVIDENCE_CONTENT_UNAVAILABLE, EVIDENCE_BLOB_TOO_LARGE, CONFIG_LOAD_FAILED, WORKSPACE_UNAVAILABLE,
    LAUNCH_NOT_FOUND, REVIEW_NOT_FOUND, REVIEW_DIFF_UNAVAILABLE, REVIEW_DIFF_UNVERIFIED, REVIEW_COMMAND_UNVERIFIED, APPROVAL_STALE,
    APPROVAL_UNAVAILABLE, READ_ONLY_UNAVAILABLE, STORE_BUSY, SNAPSHOT_MISSING, SNAPSHOT_UNAVAILABLE, SNAPSHOT_FAILED, SNAPSHOT_TOO_LARGE,
    SNAPSHOT_CORRUPT, ACQUISITION_IN_PROGRESS, ACQUISITION_REFUSED_RUN_ACTIVE})


class ApplicationRefusal(KriyaError):
    """A typed refusal of a use case: ``reason_code`` (from ``REFUSAL_CODES``), a message, and the extra fields the refusal reports."""

    def __init__(self, reason_code: str, message: str, **extra: Any) -> None:
        if reason_code not in REFUSAL_CODES:
            raise ValueError(f"{reason_code!r} is not an application refusal code")
        super().__init__(reason_code, message)
        self.message = message
        self.extra = extra


class TraceHistory(Protocol):
    """One published trace-history snapshot, read-only."""

    snapshot_id: str

    def page(self, limit: Optional[int], cursor: Optional[str]) -> Dict[str, Any]: ...

    def detail(self, run_id: str) -> Dict[str, Any]: ...

    def prompt(self, run_id: str) -> Dict[str, Any]: ...

    def run_events(self, run_id: Any) -> Tuple[bool, Optional[Any]]: ...

    def cursor_after(self, timestamp: Optional[str], run_id: str) -> str: ...

    def events_reader(self) -> ContextManager[Callable[[List[str]], Dict[str, Any]]]: ...


__all__ = ["ACQUISITION_IN_PROGRESS", "ACQUISITION_REFUSED_RUN_ACTIVE", "APPROVAL_STALE", "APPROVAL_UNAVAILABLE", "ApplicationRefusal", "CONFIG_LOAD_FAILED", "EVIDENCE_BLOB_TOO_LARGE",
           "EVIDENCE_CONTENT_UNAVAILABLE", "EVIDENCE_HEAD_CHANGED", "EVIDENCE_SCHEMA_UNSUPPORTED", "EVIDENCE_STORE_NOT_FOUND",
           "EVIDENCE_STORE_UNREADABLE", "EVIDENCE_VERIFY_FAILED", "INVALID_REQUEST", "LAUNCH_NOT_FOUND", "READ_ONLY_UNAVAILABLE", "REFUSAL_CODES",
           "REVIEW_COMMAND_UNVERIFIED", "REVIEW_DIFF_UNAVAILABLE", "REVIEW_DIFF_UNVERIFIED", "REVIEW_NOT_FOUND", "SNAPSHOT_CORRUPT", "SNAPSHOT_FAILED", "SNAPSHOT_MISSING",
           "SNAPSHOT_TOO_LARGE", "SNAPSHOT_UNAVAILABLE", "STORE_BUSY", "TraceHistory",
           "WORKSPACE_UNAVAILABLE"]
