"""C-09 (S1; owner ruling 7, 2026-11-15): the contract of production model admission.

The run owns production admission; the doctor is a preflight, not authority.
Under ``runtime_profile: production`` every generation binding a run
dispatches is QUALIFIED for the exact identity dispatched (runtime digest +
inference-settings digest + the effective qualification policy) before its
first use; anything else is refused, typed, and nothing is sent. Outside
production the same assessment is recorded and never refuses.

Canonical runtime identity is the full qualified binding identity (provider
and endpoint, served model, window, adapter, inference settings), not the
model-name string alone. The current configuration requires model-name
uniqueness across distinct bindings only because lookup is name-keyed
(``config.binding_alias_conflicts``).

This module is a leaf (it imports nothing of Kriya): the reason codes, the
client purposes, the typed refusal and the enforcement predicate. The
decisions themselves live beside the one assessment they use
(``kriya/core/model_qualification.py``: ``admit_run``, ``admit_dispatch``,
``assess_role_bindings``, ``run_admission``), so no module joins an import
cycle to make them.
"""
from __future__ import annotations

from typing import Any, Dict

MODEL_BINDING_NOT_QUALIFIED = "MODEL_BINDING_NOT_QUALIFIED"
# Closed table (tripwire: tests/test_s1_runtime_identity_admission.py).
ADMISSION_REASON_CODES = frozenset({MODEL_BINDING_NOT_QUALIFIED})
ADMISSION_SCHEMA = "kriya-model-admission/1"

# Why an LLMClient exists. Only PURPOSE_RUN is subject to admission: a
# qualification run and the doctor's connectivity probe must be able to
# call a binding that is not qualified yet (that is what they establish).
PURPOSE_RUN = "run"
PURPOSE_QUALIFICATION = "qualification"
PURPOSE_PREFLIGHT = "preflight"
CLIENT_PURPOSES = frozenset({PURPOSE_RUN, PURPOSE_QUALIFICATION, PURPOSE_PREFLIGHT})


class ModelAdmissionError(RuntimeError):
    """A binding is not QUALIFIED for the identity a production run would
    dispatch. Raised before any inference request."""

    reason_code = MODEL_BINDING_NOT_QUALIFIED

    def __init__(self, message: str, details: Dict[str, Any]) -> None:
        self.details = details
        super().__init__(message)


def enforced(config: Any) -> bool:
    """Admission refuses only under the production runtime profile."""
    return getattr(config, "runtime_profile", None) == "production"


__all__ = [
    "ADMISSION_REASON_CODES", "ADMISSION_SCHEMA", "CLIENT_PURPOSES", "MODEL_BINDING_NOT_QUALIFIED",
    "ModelAdmissionError", "PURPOSE_PREFLIGHT", "PURPOSE_QUALIFICATION", "PURPOSE_RUN", "enforced",
]
