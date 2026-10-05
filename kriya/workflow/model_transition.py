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
import math
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
    from kriya.core.llm import reasoning_max_tokens
    from kriya.core.model_capabilities import resolve_model_capability_profile
    from kriya.core.model_qualification import assess, measured_limits_for, policy_digest_for, required_capabilities
    from kriya.core.model_runtime import (
        binding_output_tokens,
        endpoint_identity,
        resolve_configured_model_runtime,
    )
    from kriya.core.provider_contract import budget_window
    from kriya.workflow.context_budget import allocation_window

    binding = binding if binding is not None else config.llm
    model = binding.model
    capability = resolve_model_capability_profile(config, model)
    caps = capability.capabilities
    runtime_digest, runtime_exact, qualification, failed = "unavailable", False, "UNAVAILABLE", ()
    served_window = None
    measured_reasoning = None
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
        policy_digest = policy_digest_for(config)
        assessment = assess(fingerprint, required_capabilities(config, DEVELOPER_ROLE, model),
                            settings=binding_inference_settings(config, DEVELOPER_ROLE, binding),
                            policy_digest=policy_digest)
        qualification, failed = assessment.status, tuple(assessment.failed)
        measured_reasoning = measured_limits_for(
            fingerprint, config, settings=binding_inference_settings(config, DEVELOPER_ROLE, binding),
        ).get("reasoning_tokens_max")
        if retry_settings is not None:
            retry_qualification = assess(fingerprint, required_capabilities(config, DEVELOPER_ROLE, model),
                                         settings=retry_settings, policy_digest=policy_digest).status
    except Exception as error:  # a profile is evidence; it never blocks the run itself
        logger.debug("Request profile of %s: runtime unavailable: %s", model, error)
    output = binding_output_tokens(config, binding)
    if binding.reasoning:
        output = reasoning_max_tokens(output, measured_reasoning)
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
        # The window requests are budgeted at (never a larger served one).
        context_window=budget_window(binding.context_window, served_window),
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


# -- LR-R1-P1: one fallback-compatibility decision for routing ------------------------------------
# Option (b), owner decision 2026-10-05: a fallback that deterministically
# cannot serve a patch-only next attempt is treated as UNAVAILABLE for that
# transition, so a remaining primary full-set route is taken instead of a
# dead fallback transition. Patch-only scope only: a run-wide incompatibility
# (a failed Developer case, production without QUALIFIED, no prompt room)
# keeps PRD-017's terminal semantics unchanged.

PATCH_ONLY_PROVEN = "PATCH_ONLY_PROVEN"
PATCH_REQUIREMENT_NOT_DETERMINED = "NOT_DETERMINED"
CANDIDATE_COMPATIBLE = "COMPATIBLE"
CANDIDATE_PATCH_INCOMPATIBLE = "PATCH_INCOMPATIBLE"
CANDIDATE_RUN_WIDE_INCOMPATIBLE = "RUN_WIDE_INCOMPATIBLE"
CANDIDATE_NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class FallbackCandidate:
    model: str
    status: str
    reasons: Tuple[str, ...] = ()
    patch_only_files: Tuple[str, ...] = ()
    runtime_digest: Optional[str] = None
    qualification: Optional[str] = None
    capability_source: Optional[str] = None
    capability_evidence: Optional[Dict[str, Any]] = None
    file_allocation_tokens: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        record = asdict(self)
        record["reasons"] = list(self.reasons)
        record["patch_only_files"] = list(self.patch_only_files)
        return record


@dataclass(frozen=True)
class FallbackCompatibility:
    """The remaining configured fallbacks, in resolve_fallback_model's order,
    each classified against the next attempt's deterministic requirement."""

    requirement: str
    candidates: Tuple[FallbackCandidate, ...]
    targets: Tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Whether routing may treat a fallback as present: every remaining
        candidate except those proven unable to serve the patch-only next
        attempt (run-wide incompatible ones keep PRD-017's semantics)."""
        return any(c.status != CANDIDATE_PATCH_INCOMPATIBLE for c in self.candidates)

    @property
    def patch_excluded(self) -> Tuple[str, ...]:
        return tuple(c.model for c in self.candidates if c.status == CANDIDATE_PATCH_INCOMPATIBLE)

    @property
    def selected(self) -> Optional[str]:
        """The first candidate that can serve, in configured order."""
        return next((c.model for c in self.candidates if c.status in (CANDIDATE_COMPATIBLE, CANDIDATE_NOT_EVALUATED)),
                    None)

    def to_dict(self) -> Dict[str, Any]:
        return {"requirement": self.requirement, "targets": list(self.targets), "selected": self.selected,
                "candidates": [c.to_dict() for c in self.candidates]}


def whole_file_minimum_tokens(size_bytes: int, bytes_per_token_ceiling: float) -> int:
    """A lower bound on the tokens a whole file needs in any request: no
    counter Kriya's dispatch check uses counts fewer than bytes / ceiling
    (PROVIDER-CONTRACT-001's bytes-per-token ceiling)."""
    return math.ceil(size_bytes / bytes_per_token_ceiling)


def _bytes_per_token_ceiling(config: Any, binding: Any) -> float:
    """The binding's qualified bytes-per-token ceiling, never below the
    provider contract's default (a larger ceiling only weakens the bound)."""
    from kriya.core.inference_settings import binding_inference_settings
    from kriya.core.model_qualification import measured_limits_for
    from kriya.core.model_runtime import resolve_configured_model_runtime
    from kriya.core.provider_contract import DEFAULT_BYTES_PER_TOKEN_CEILING

    measured = None
    try:
        fingerprint = resolve_configured_model_runtime(
            config, binding.model, base_url=binding.base_url, api_key=binding.api_key,
            extra_body=binding.extra_body or {})
        measured = measured_limits_for(fingerprint, config, settings=binding_inference_settings(
            config, DEVELOPER_ROLE, binding)).get("bytes_per_token_ceiling")
    except Exception as error:  # the default ceiling is the conservative bound
        logger.debug("bytes-per-token ceiling of %s: default (%s)", binding.model, error)
    return max(DEFAULT_BYTES_PER_TOKEN_CEILING, float(measured or 0.0))


def patch_only_files(config: Any, binding: Any, target_sizes: Dict[str, int]) -> Tuple[Tuple[str, ...], int]:
    """The targets whose whole content can never be shown in a request to
    ``binding``: the token lower bound exceeds the binding's whole prompt
    room (its RequestCapacity: budget window minus its own output reserve,
    framing and safety margin - an upper bound on what any file can get).
    Equal is not proof. Returns (files, that room)."""
    from kriya.workflow.context_budget import request_capacity

    room = request_capacity(config, binding).tokens
    ceiling = _bytes_per_token_ceiling(config, binding)
    return tuple(sorted(path for path, size in target_sizes.items()
                        if whole_file_minimum_tokens(size, ceiling) > room)), room


def existing_target_sizes(worktree_path: Optional[str], targets: Iterable[str]) -> Dict[str, int]:
    """Byte sizes of the targets that exist as regular files now."""
    import os

    sizes: Dict[str, int] = {}
    if not worktree_path:
        return sizes
    for path in targets or ():
        full = os.path.join(worktree_path, path)
        if os.path.isfile(full) and not os.path.islink(full):
            sizes[path] = os.path.getsize(full)
    return sizes


def resolve_fallback_compatibility(config: Any, chain: List[Any], *, retry_count: int,
                                   run_wide_rejections: Optional[Dict[str, List[str]]] = None,
                                   target_sizes: Optional[Dict[str, int]] = None) -> FallbackCompatibility:
    """The one compatibility decision every fallback routing consumer reads.
    Candidates are ``chain[min(retry_count - 1, len - 1):]`` - exactly
    attribution.resolve_fallback_model's ladder, never reordered. A target
    is patch-only for a candidate only when whole-file representation is
    impossible in that candidate's request (``patch_only_files``); when no
    target is, the requirement is NOT_DETERMINED and nothing is pre-rejected
    (the call-time D1 check stays the authority). Run-wide rejections
    already recorded for the run are carried as such."""
    run_wide_rejections = run_wide_rejections or {}
    target_sizes = target_sizes or {}
    if not chain or retry_count <= 0:
        return FallbackCompatibility(PATCH_REQUIREMENT_NOT_DETERMINED, (), tuple(sorted(target_sizes)))
    remaining = chain[min(retry_count - 1, len(chain) - 1):]
    proofs = [patch_only_files(config, binding, target_sizes) if target_sizes else ((), None)
              for binding in remaining]
    if not any(files for files, _room in proofs):
        return FallbackCompatibility(
            PATCH_REQUIREMENT_NOT_DETERMINED,
            tuple(FallbackCandidate(
                model=b.model,
                status=CANDIDATE_RUN_WIDE_INCOMPATIBLE if b.model in run_wide_rejections else CANDIDATE_NOT_EVALUATED,
                reasons=tuple(run_wide_rejections.get(b.model, ())), file_allocation_tokens=room)
                for b, (_files, room) in zip(remaining, proofs, strict=True)),
            tuple(sorted(target_sizes)))
    from kriya.core.model_capabilities import resolve_model_capability_profile

    candidates = []
    for binding, (files, room) in zip(remaining, proofs, strict=True):
        profile = resolve_request_profile(config, binding)
        capability = resolve_model_capability_profile(config, binding.model)
        run_wide = list(run_wide_rejections.get(binding.model, ())) or fallback_incompatibilities(config, profile)
        patch = [reason for reason in fallback_incompatibilities(config, profile, patch_required_files=files)
                 if reason not in run_wide] if files else []
        status = (CANDIDATE_RUN_WIDE_INCOMPATIBLE if run_wide
                  else CANDIDATE_PATCH_INCOMPATIBLE if patch else CANDIDATE_COMPATIBLE)
        candidates.append(FallbackCandidate(
            model=binding.model, status=status, reasons=tuple(run_wide or patch), patch_only_files=files,
            runtime_digest=profile.runtime_digest, qualification=profile.qualification,
            capability_source=profile.capability_source, capability_evidence=capability.evidence,
            file_allocation_tokens=room))
    return FallbackCompatibility(PATCH_ONLY_PROVEN, tuple(candidates), tuple(sorted(target_sizes)))


def fallback_routing_for_state(state: Any, config: Any, chain: List[Any],
                               worktree_path: Optional[str]) -> FallbackCompatibility:
    """The routing view of the next fallback-targeted attempt (the attempt
    whose deterministic targets are the failure's implicated files), shared
    by every retry-policy call site so none reads ``bool(chain)`` as
    "a usable fallback exists"."""
    return resolve_fallback_compatibility(
        config, chain, retry_count=1, run_wide_rejections=dict(getattr(state, "incompatible_fallbacks", {}) or {}),
        target_sizes=existing_target_sizes(worktree_path, getattr(state, "last_implicated_files", None) or ()))


def fallback_routing_for_context(state: Any, ctx: Any) -> FallbackCompatibility:
    """``fallback_routing_for_state`` for an attempt context; with no
    configured chain there is nothing to resolve (no config is read)."""
    chain = list(getattr(ctx, "chain", None) or ())
    if not chain:
        return FallbackCompatibility(PATCH_REQUIREMENT_NOT_DETERMINED, ())
    return fallback_routing_for_state(state, ctx.kernel.config, chain, ctx.worktree_path)
