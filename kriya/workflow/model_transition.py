"""PRD-017: capability-aware Developer model transitions.

When the Developer's model changes between attempts (a fallback hop, or a
return to the primary), everything the request depends on is resolved again
for the model actually called: its exact runtime and qualification, its
capability profile (native tools, JSON mode, edit protocol, streaming),
reasoning, its served and prompt-allocation windows and its own output
budget. ``ModelRequestProfile`` is that resolution; ``profile_changes`` is
what differs between two attempts (recorded as the ``model.transition`` run
event); ``fallback_incompatibilities`` says, from evidence only, why a
fallback cannot do what the attempt requires.

Evidence only: a runtime with no qualification record (MISSING), one whose
identity is not exact, or a STALE record is recorded, never refused - that is
the default for every local setup that has not run ``kriya model qualify``.
A fallback is refused when a qualification record for its exact runtime has
a FAILED Developer case, when the production runtime profile requires a
QUALIFIED runtime and it is not, or when the attempt needs an anchored patch
(the completeness gate authorized nothing wider) and the fallback's profile
can only return whole files. A larger model is never assumed to be more
capable. The escalation order (attribution.resolve_fallback_model) is kept:
an incompatible fallback is skipped, with its reasons recorded, for the
next configured one that can serve the attempt (attempt.py
_select_developer_fallback before the prompt is built,
_substitute_for_required_patch at the call), never reordered by preference
or metrics; the attempt ends with the typed FALLBACK_MODEL_INCOMPATIBLE
failure only when no remaining configured fallback can.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

FALLBACK_MODEL_INCOMPATIBLE = "FALLBACK_MODEL_INCOMPATIBLE"
# MODEL-EVIDENCE-HARDENING-001: a production Developer retry whose
# retry-temperature inference identity is not QUALIFIED (refused before any request).
RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED = "RETRY_INFERENCE_IDENTITY_NOT_QUALIFIED"

# Edit protocols that can only return whole files (model_capabilities).
FULL_FILE_ONLY_PROTOCOLS = frozenset({"full_file", "full_file_text"})

DEVELOPER_ROLE = "developer"


@dataclass(frozen=True)
class ModelRequestProfile:
    """Everything a Developer request to one model binding depends on."""

    model: str
    endpoint: str
    runtime_digest: str
    runtime_exact: bool
    qualification: str
    failed_cases: Tuple[str, ...]
    capability_source: str
    native_tool_calls: bool
    json_mode: bool
    reliable_multiline_json: bool
    streaming: bool
    edit_protocol: str
    reasoning: bool
    context_window: Optional[int]
    allocation_window: int
    output_tokens: int
    context_policy: str
    # MODEL-EVIDENCE-HARDENING-001: a differing llm.retry_temperature is its
    # own inference identity; None when a retry is the same identity.
    retry_inference_settings_digest: Optional[str] = None
    retry_qualification: Optional[str] = None

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["failed_cases"] = list(self.failed_cases)
        data["digest"] = self.digest
        return data


def resolve_request_profile(config: Any, binding: Any = None) -> ModelRequestProfile:
    """The request profile of a Developer call to ``binding`` (the primary
    ``config.llm`` by default, or an ``llm_chain`` entry). Never raises: an
    unresolvable runtime is recorded as unavailable."""
    from kriya.core.inference_settings import binding_inference_settings, retry_inference_settings
    from kriya.core.llm import REASONING_MIN_MAX_TOKENS
    from kriya.core.model_capabilities import resolve_model_capability_profile
    from kriya.core.model_qualification import assess, required_capabilities
    from kriya.core.model_runtime import (
        binding_output_tokens,
        endpoint_identity,
        resolve_configured_model_runtime,
    )
    from kriya.workflow.context_budget import allocation_window

    binding = binding if binding is not None else config.llm
    model = binding.model
    capability = resolve_model_capability_profile(config, model)
    caps = capability.capabilities
    runtime_digest, runtime_exact, qualification, failed = "unavailable", False, "UNAVAILABLE", ()
    served_window = None
    retry_settings = retry_inference_settings(config, DEVELOPER_ROLE, binding)
    retry_digest: Optional[str] = retry_settings.digest if retry_settings is not None else None
    retry_qualification: Optional[str] = "UNAVAILABLE" if retry_settings is not None else None
    try:
        fingerprint = resolve_configured_model_runtime(
            config, model, base_url=binding.base_url, api_key=binding.api_key,
            extra_body=binding.extra_body or {},
        )
        runtime_exact = bool(fingerprint.exact)
        runtime_digest = fingerprint.digest if runtime_exact else "unavailable"
        served_window = fingerprint.effective_context_window
        assessment = assess(fingerprint, required_capabilities(config, DEVELOPER_ROLE, model),
                            settings=binding_inference_settings(config, DEVELOPER_ROLE, binding))
        qualification, failed = assessment.status, tuple(assessment.failed)
        if retry_settings is not None:
            retry_qualification = assess(fingerprint, required_capabilities(config, DEVELOPER_ROLE, model),
                                         settings=retry_settings).status
    except Exception as error:  # a profile is evidence; it never blocks the run itself
        logger.debug("Request profile of %s: runtime unavailable: %s", model, error)
    output = binding_output_tokens(config, binding)
    if binding.reasoning:
        output = max(output, REASONING_MIN_MAX_TOKENS)
    return ModelRequestProfile(
        model=model,
        endpoint=endpoint_identity(binding.base_url),
        runtime_digest=runtime_digest,
        runtime_exact=runtime_exact,
        qualification=qualification,
        failed_cases=failed,
        capability_source=capability.source,
        native_tool_calls=bool(caps.native_tool_calls),
        json_mode=bool(caps.json_mode),
        reliable_multiline_json=bool(caps.reliable_multiline_json),
        streaming=bool(caps.streaming),
        edit_protocol=str(caps.preferred_edit_protocol),
        reasoning=bool(binding.reasoning),
        context_window=served_window or binding.context_window,
        allocation_window=allocation_window(config, binding),
        output_tokens=int(output),
        context_policy=binding.context_policy.mode,
        retry_inference_settings_digest=retry_digest,
        retry_qualification=retry_qualification,
    )


def profile_changes(before: Optional[ModelRequestProfile],
                    after: ModelRequestProfile) -> Dict[str, Dict[str, Any]]:
    """Field-by-field difference between two request profiles ({} when equal;
    every field when there is no previous profile)."""
    changes: Dict[str, Dict[str, Any]] = {}
    for item in fields(ModelRequestProfile):
        new = getattr(after, item.name)
        old = getattr(before, item.name) if before is not None else None
        if before is None or old != new:
            changes[item.name] = {
                "from": list(old) if isinstance(old, tuple) else old,
                "to": list(new) if isinstance(new, tuple) else new,
            }
    return changes


def fallback_incompatibilities(config: Any, profile: ModelRequestProfile, *,
                               patch_required_files: Iterable[str] = ()) -> List[str]:
    """Why the fallback ``profile`` cannot serve this attempt ([] when it
    can). Only evidence counts; see the module docstring."""
    from kriya.core.model_qualification import QUALIFIED

    reasons: List[str] = []
    if profile.failed_cases:
        reasons.append(
            f"qualification of runtime {profile.runtime_digest[:12]} failed Developer case(s): "
            + ", ".join(profile.failed_cases)
        )
    if getattr(config, "runtime_profile", None) == "production" and profile.qualification != QUALIFIED:
        reasons.append(
            f"the production runtime profile requires a QUALIFIED Developer runtime; {profile.model} is "
            f"{profile.qualification}"
        )
    if (getattr(config, "runtime_profile", None) == "production" and profile.retry_qualification is not None
            and profile.retry_qualification != QUALIFIED):
        reasons.append(
            f"the production runtime profile requires the Developer retry identity (retry_temperature "
            f"{config.llm.retry_temperature}) to be QUALIFIED; {profile.model} is {profile.retry_qualification}"
        )
    patch_files = sorted(set(patch_required_files))
    if patch_files and profile.edit_protocol in FULL_FILE_ONLY_PROTOCOLS:
        reasons.append(
            f"the attempt may only patch {', '.join(patch_files)} (no complete current source was "
            f"shown), but {profile.model}'s capability profile ({profile.capability_source}) returns "
            f"whole files only (edit protocol {profile.edit_protocol})"
        )
    if patch_files and "anchored_edit_protocol" in profile.failed_cases:
        reasons.append(f"{profile.model} failed the anchored_edit_protocol qualification case, "
                       f"and {', '.join(patch_files)} may only be patched")
    if profile.allocation_window <= 0:
        reasons.append(
            f"{profile.model}'s window ({profile.context_window}) leaves no room for a prompt beside its "
            f"output budget ({profile.output_tokens} tokens)"
        )
    return reasons
