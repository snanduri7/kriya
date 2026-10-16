"""Identifier value types (MODULE-CONTRACTS §1). Plain ``str`` at run time;
distinct names for the type checker so a run id is never passed where a
workspace id is expected.

The run-id rule (B2: moved from ``kriya/kup/inspect.py`` unchanged, so the KUP
adapter and the application services apply ONE rule; the Electron host mirrors
it in ``ui/standalone/src/main/ipc_contract.ts``): first character
alphanumeric, then ``[A-Za-z0-9._:-]``, at most 256 characters. It is the whole
path-safety rule for a run id: a leading ``.`` (retention staging) or any
separator is refused before a path is built from it."""
import re
from typing import Any, NewType

RunId = NewType("RunId", str)
LaunchId = NewType("LaunchId", str)
WorkspaceId = NewType("WorkspaceId", str)
RequestId = NewType("RequestId", str)
FindingId = NewType("FindingId", str)
TransactionId = NewType("TransactionId", str)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
RUN_ID_MAX_CHARS = 256
RUN_ID_RULE = "run_id must match [A-Za-z0-9][A-Za-z0-9._:-]* and be at most 256 characters"


def is_valid_run_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= RUN_ID_MAX_CHARS and bool(RUN_ID_RE.match(value))


__all__ = ["FindingId", "LaunchId", "RUN_ID_MAX_CHARS", "RUN_ID_RE", "RUN_ID_RULE", "RequestId", "RunId", "TransactionId",
           "WorkspaceId", "is_valid_run_id"]
