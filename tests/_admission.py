"""C-09 (S1; owner ruling 7): the precondition of every production-profile
test that dispatches through a real ``LLMClient``.

Under ``runtime_profile: production`` the client dispatches only an identity
that is QUALIFIED (kriya/core/model_admission.py). A test about another
production rule (the served-window identity, the admission ceiling...) first
qualifies the exact identity its call dispatches, the way an operator would
with ``kriya model qualify``. It never switches admission off: an identity
this helper did not record is still refused.
"""
from typing import Any, Optional

from kriya.core import model_qualification as mq
from kriya.core import model_runtime
from kriya.core.inference_settings import binding_inference_settings


def qualify_dispatched(config: Any, model: Optional[str] = None, *, role: str = "developer") -> Any:
    """Save an all-PASS, current record for the identity ``role`` dispatches
    ``model`` (default: the primary) with under ``config``: its configured
    exact runtime and its own inference settings. Returns the runtime
    fingerprint. The runtime must be exact (a probe stand-in is in place)."""
    model = model or config.llm.model
    binding = model_runtime.require_binding(config, model)
    runtime = model_runtime.resolve_configured_model_runtime(config, model)
    assert runtime.exact, f"{model}: a production test needs an exact runtime ({runtime.missing_components})"
    results = [mq.CaseResult(capability, mq.PASS) for capability in mq.CAPABILITIES]
    mq.save_record(mq.build_record(runtime, results, settings=binding_inference_settings(config, role, binding)))
    return runtime
