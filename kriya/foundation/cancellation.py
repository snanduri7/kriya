"""Cancellation contract (MODULE-CONTRACTS §1)."""
from typing import Optional, Protocol


class CancellationToken(Protocol):
    def cancelled(self) -> bool: ...

    def reason(self) -> Optional[str]: ...


__all__ = ["CancellationToken"]
