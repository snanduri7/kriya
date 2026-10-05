"""Typed reason codes for two stops that exist only as message text
(LR-R1-M1 design D7).

``NO_AUTHORIZED_REPAIR_TARGET`` and ``REGRESSION_UNATTRIBUTED`` end a run
through ``state.environment_failure`` (a message). Their producers also set
``GenerationState.environment_failure_code`` = (code, the exact message it
types), so evidence can name the stop without parsing text. Additive and
observational: messages and decisions are unchanged, and no production
decision may read these codes (structural test in
tests/test_lr_r1_m1_reason_codes.py) - only their producers, the evidence
recorder and tests.
"""

NO_AUTHORIZED_REPAIR_TARGET = "NO_AUTHORIZED_REPAIR_TARGET"
REGRESSION_UNATTRIBUTED = "REGRESSION_UNATTRIBUTED"
