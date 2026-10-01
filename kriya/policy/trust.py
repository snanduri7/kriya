"""Trust model - MA4.10 of the control-plane implementation plan (see
kriya/policy/__init__.py for MA4's overall principle; docs/design.md's
"Execution security & trust boundary" / "Trust model" section for the source
priority list this module is a direct, literal port of).

MA4.10 scope only, mirroring MA4.1's own shape (kriya/policy/model.py): a
closed, ordered vocabulary and one frozen dataclass that carries a trust
level alongside content - pure data, importable with zero side effects, not
wired into any real call site yet and not itself deciding anything. Nothing
in this module inspects content, classifies a real source, or enforces the
hierarchy against an ActionRequest - that is MA4.11's job (approved-source
promotion, kriya/policy/approved_sources.py) and MA4.12's job (prompt-
injection detection over content whose trust level is too low to be issuing
instructions at all). This module exists so both of those can share one
vocabulary rather than each inventing its own ranking.

Repository content and retrieved external content are untrusted DATA, even
when phrased as imperative instructions - a README, source comment, test
fixture, issue body, or downloaded page cannot redefine tool permissions,
request secrets, authorize egress, expand filesystem scope, or override the
user's actual goal, no matter how it's worded. Lower TrustLevel values may
still supply facts and task context; they just can never win a conflict
against a higher one. Enforcing that is later MA4 work - this module only
gives the ranking a name.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Mapping


class TrustLevel(IntEnum):
    """The six-rung authority ladder from the design doc's trust-model
    priority list, ported verbatim and in the same order (Priority 0 through
    Priority 5). Declared as IntEnum specifically so ordering comparisons
    (`<`, `<=`, `min`, `max`, `sorted`) work directly against the enum
    values - but the numeric direction is the OPPOSITE of "bigger number,
    more trust": a LOWER value means MORE authority (PLATFORM = 0 outranks
    everything; EXTERNAL = 5 outranks nothing). Never compare these values
    directly for "which is more trusted" without checking that direction -
    use outranks() below instead, which encodes it once so callers don't
    have to remember it."""

    PLATFORM = 0
    USER_INSTRUCTION = 1
    APPROVED_PROJECT_POLICY = 2
    MILESTONE = 3
    REPOSITORY = 4
    EXTERNAL = 5


def outranks(a: TrustLevel, b: TrustLevel) -> bool:
    """True when `a` has strictly more authority than `b` (e.g.
    outranks(TrustLevel.USER_INSTRUCTION, TrustLevel.REPOSITORY) is True;
    outranks(TrustLevel.REPOSITORY, TrustLevel.USER_INSTRUCTION) is False;
    a level never outranks itself). Exists so call sites never have to
    remember TrustLevel's inverted numeric direction inline."""

    return a.value < b.value


@dataclass(frozen=True)
class TrustedContent:
    """Content paired with an explicit trust level and a human-readable
    provenance string. `trust_level` and `source` are deliberately given no
    default - unlike ActionRequest's optional fields (kriya/policy/model.py),
    there is no safe default trust level to fall back to silently; every
    real caller (MA4.11 onward) must state which rung of the ladder a given
    piece of content came from. `source` is free text for humans/telemetry
    (e.g. "kriya learn: https://...", "repository file: src/foo.py",
    "milestone spec: M3") - it is provenance, not a machine-matched key;
    MA4.11's approved-source promotion keys off path + SHA-256 separately.

    `metadata` follows the same rule ActionRequest.metadata already
    establishes: small policy-relevant facts only, never secrets or the
    full original payload beyond what `content` itself already carries."""

    content: str
    trust_level: TrustLevel
    source: str

    metadata: Mapping[str, Any] = field(default_factory=dict)
