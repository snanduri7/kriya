"""Clock and deadline contracts (MODULE-CONTRACTS §1). A deadline is an
absolute monotonic instant plus the reason it exists; whoever checks it
supplies the clock, so a test drives time with a fake clock."""
from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now_iso(self) -> str: ...


@dataclass(frozen=True)
class Deadline:
    at_monotonic: float
    label: str

    def remaining(self, clock: Clock) -> float:
        return self.at_monotonic - clock.monotonic()

    def expired(self, clock: Clock) -> bool:
        return self.remaining(clock) <= 0


__all__ = ["Clock", "Deadline"]
