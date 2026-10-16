"""The base of every typed Kriya error (MODULE-CONTRACTS §1). A subclass
names its refusal with a ``reason_code`` from a closed table in its owning
module; the base never invents one."""


class KriyaError(Exception):
    """A typed refusal or failure carrying its ``reason_code``."""

    reason_code: str

    def __init__(self, reason_code: str, message: str = "") -> None:
        if not isinstance(reason_code, str) or not reason_code:
            raise TypeError("KriyaError needs a non-empty reason_code")
        super().__init__(message or reason_code)
        self.reason_code = reason_code


__all__ = ["KriyaError"]
