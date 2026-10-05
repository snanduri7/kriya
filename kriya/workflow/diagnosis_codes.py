"""Typed reason codes for two stops that exist only as message text
(LR-R1-M1 design D7).

``NO_AUTHORIZED_REPAIR_TARGET`` and ``REGRESSION_UNATTRIBUTED`` are not
environment failures; they only reuse ``state.environment_failure`` as the
stop channel (a message). Their producers also set
``GenerationState.stop_reason_evidence`` = ``StopReasonEvidence(code, the
exact message it types)``, so evidence can name the stop without parsing
text. Evidence only: messages and decisions are unchanged, and no production
decision may read it (structural test in tests/test_lr_r1_m1_reason_codes.py)
- only its producers, the evidence recorder and tests.
"""
from dataclasses import dataclass

NO_AUTHORIZED_REPAIR_TARGET = "NO_AUTHORIZED_REPAIR_TARGET"
REGRESSION_UNATTRIBUTED = "REGRESSION_UNATTRIBUTED"


@dataclass(frozen=True)
class StopReasonEvidence:
    """Evidence-only: the typed code of a stop and the exact stop message it
    types (a code set for a message since replaced is not reported)."""

    code: str
    message: str
