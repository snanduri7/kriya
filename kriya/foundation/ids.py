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


# The CONTROL-PLANE id rule (moved from kriya/control/persistence.py ``_RUN_ID_CHARS``/``_valid_run_id`` in D3, unchanged): the stricter
# rule every control record path, launch/review id and run-log path is built from - letters, digits, '-' and '_' only (no '.' or ':',
# unlike the history/evidence run-id rule above).
CONTROL_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def is_control_id(run_id: str) -> bool:
    return bool(run_id) and set(run_id) <= CONTROL_ID_CHARS


def is_valid_run_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= RUN_ID_MAX_CHARS and bool(RUN_ID_RE.match(value))


__all__ = ["CONTROL_ID_CHARS", "FindingId", "LaunchId", "RUN_ID_MAX_CHARS", "RUN_ID_RE", "RUN_ID_RULE", "RequestId", "RunId", "TransactionId",
           "WorkspaceId", "is_control_id", "is_valid_run_id"]
