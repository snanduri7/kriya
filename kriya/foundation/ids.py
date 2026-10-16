"""Identifier value types (MODULE-CONTRACTS §1). Plain ``str`` at run time;
distinct names for the type checker so a run id is never passed where a
workspace id is expected."""
from typing import NewType

RunId = NewType("RunId", str)
LaunchId = NewType("LaunchId", str)
WorkspaceId = NewType("WorkspaceId", str)
RequestId = NewType("RequestId", str)
FindingId = NewType("FindingId", str)
TransactionId = NewType("TransactionId", str)

__all__ = ["FindingId", "LaunchId", "RequestId", "RunId", "TransactionId", "WorkspaceId"]
