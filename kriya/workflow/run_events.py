"""Typed, append-only events for the generation and repair runtime.

The event log is deliberately local process state.  It is never transmitted to
live lookup and contains no behavior that can weaken Kriya's local-only policy.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional


class EventAuthority(str, Enum):
    """Whether an event may replace the run's active failure."""

    AUTHORITATIVE = "authoritative"
    ADVISORY = "advisory"
    AUXILIARY = "auxiliary"


@dataclass(frozen=True)
class RunEvent:
    """One immutable runtime fact.

    ``details`` must be trace-safe local data.  Egress code must never consume
    this object directly; outward lookup accepts a separate sanitized request.
    """

    kind: str
    attempt: int
    source: str
    authority: EventAuthority
    message: str = ""
    failure_type: Optional[str] = None
    operation: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["authority"] = self.authority.value
        return value


@dataclass
class FailureLedger:
    """Enforces primary-failure ownership for one run.

    Optional systems may report incidents, but only authoritative validation,
    edit-transaction, environment, or orchestration failures can become the
    primary failure used for retry decisions.
    """

    primary: Optional[RunEvent] = None
    secondary: List[RunEvent] = field(default_factory=list)

    def record(self, event: RunEvent) -> None:
        if event.authority is EventAuthority.AUTHORITATIVE:
            self.primary = event
        else:
            self.secondary.append(event)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": [event.to_dict() for event in self.secondary],
        }
