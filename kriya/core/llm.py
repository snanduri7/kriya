import asyncio
import ipaddress
import json
import logging
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI

from kriya.config import AppConfig
from kriya.core.inference_runtime import ChatRequest, InferenceRuntimePort, RuntimeErrorKind, runtime_for_binding
from kriya.core.inference_settings import request_settings
from kriya.core.model_runtime import context_window_overrides, request_extra_body
from kriya.policy.execution import ExecutionPolicy
from kriya.policy.model import ActionRequest, ActionType

logger = logging.getLogger(__name__)

# A reasoning model's max_tokens covers hidden reasoning and visible output
# together. Without a measured reasoning reserve it never runs below this.
REASONING_MIN_MAX_TOKENS = 12288


def reasoning_max_tokens(output_tokens: int, measured_reasoning_tokens: Optional[int]) -> int:
    """max_tokens of a reasoning identity (PROVIDER-CONTRACT-001): the role's
    visible output budget plus the reasoning reserve this exact identity's
    qualification measured (reasoning_tokens_max); without a measurement,
    the fixed floor REASONING_MIN_MAX_TOKENS. The one rule the request, its
    prompt sizing and its transition profile use."""
    if measured_reasoning_tokens:
        return int(output_tokens) + int(measured_reasoning_tokens)
    return max(int(output_tokens), REASONING_MIN_MAX_TOKENS)

class EgressViolationError(ValueError):
    """Raised when an LLM completion request violates local_only egress policy.

    MA4.3 (control-plane implementation plan, kriya/policy/__init__.py): this
    is Kriya's hard local-only egress boundary and stays authoritative
    independently of kriya/policy/execution.py's ExecutionPolicy - see
    LLMClient._audit_llm_network_access below. Nothing in kriya/policy/ may
    ever replace, gate, or suppress this check."""
    pass

def is_local_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            # Fail CLOSED, not open - found live, 2026-08-12 (SME architecture
            # review): a malformed/typo'd base_url (e.g. missing "http://" in
            # a config file) parses with hostname=None via urlparse, and this
            # branch was previously treating that as local/allowed - the
            # opposite of the fail-closed behavior the except block below
            # already implements for every OTHER failure mode. This function
            # is a hard safety boundary (see kriya/core/llm.py's egress
            # enforcement, and CLAUDE.md's "Egress control" section) - an
            # unparseable hostname is exactly the kind of ambiguous input
            # that should never be treated as "safe."
            logger.debug(f"is_local_url check found no hostname for '{url}', treating as non-local (fail closed)")
            return False

        if hostname.lower() in {"localhost", "127.0.0.1", "[::1]"}:
            return True
        if hostname.lower().endswith(".local"):
            return True
            
        addr_info = socket.getaddrinfo(hostname, None)
        for _family, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
                continue
            else:
                return False
        return True
    except Exception as e:
        logger.debug(f"is_local_url check failed for '{url}', treating as non-local (fail closed): {e}")
        return False

# PROVIDER-CONTRACT-001A: a model call's deadline. The authority is the
# active run's generation budget (run_coordinator.claim_run_generation_clock
# + autonomy.generation_time_budget_seconds); inference_deadline() may only
# narrow it (a local cap). The transport timeout never extends either.
_INFERENCE_DEADLINE: ContextVar[Optional[float]] = ContextVar("kriya_inference_deadline", default=None)

INFERENCE_DEADLINE_EXHAUSTED = "INFERENCE_DEADLINE_EXHAUSTED"  # no time left before dispatch
INFERENCE_DEADLINE_EXCEEDED = "INFERENCE_DEADLINE_EXCEEDED"  # the request outlived the remaining budget
DEADLINE_SOURCE_RUN_BUDGET = "run_generation_budget"
DEADLINE_SOURCE_LOCAL_CAP = "local_cap"

_T = TypeVar("_T")


STRUCTURED_OUTPUT_UNSUPPORTED = "STRUCTURED_OUTPUT_UNSUPPORTED"


class StructuredOutputUnsupportedError(Exception):
    """A schema-constrained request to a runtime that cannot constrain its
    output (RuntimeCapabilities.json_schema_output is not declared)."""

    reason_code = STRUCTURED_OUTPUT_UNSUPPORTED


class InferenceDeadlineError(Exception):
    """A model call refused before dispatch (EXHAUSTED) or stopped
    mid-request (EXCEEDED) by Kriya's own deadline: a typed stop, never a
    provider timeout to retry or escalate."""

    def __init__(self, reason_code: str, details: Dict[str, Any]):
        self.reason_code = reason_code
        self.details = details
        super().__init__(
            f"{reason_code}: the {details.get('deadline_source')} deadline "
            f"left {details.get('remaining_budget_at_dispatch_ms')} ms for this model call"
        )

# Kriya's retry policy alone decides whether another request is sent.
SDK_MAX_RETRIES = 0


@contextmanager
def inference_deadline(deadline: Optional[float]) -> Iterator[None]:
    """Bound every model call inside the block by ``deadline``
    (``time.monotonic()`` seconds); None leaves the configured timeouts."""
    token = _INFERENCE_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _INFERENCE_DEADLINE.reset(token)


def transport_timeout(config: AppConfig, read_seconds: Optional[float] = None) -> httpx.Timeout:
    """The Kriya-owned timeout of one request (llm.transport)."""
    transport = config.llm.transport
    return httpx.Timeout(
        connect=transport.connect_timeout_seconds,
        read=read_seconds if read_seconds is not None else transport.read_timeout_seconds,
        write=transport.write_timeout_seconds, pool=transport.pool_timeout_seconds,
    )


def plan_requested_window(plan: Any) -> Optional[int]:
    state = plan.setting("context_window")
    return state.requested if state is not None else None


def dispatched_bytes(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]) -> int:
    """Size of everything a request asks the model to read, whitespace runs
    counted once (the prompt-consumption check's basis)."""
    from kriya.core.provider_contract import consumption_bytes
    from kriya.core.token_budget import dispatch_text

    return consumption_bytes(dispatch_text(messages, tools))


def _common_prefix_length(a: str, b: str) -> int:
    """Length of the longest common prefix (binary search over C-level slice
    comparisons)."""
    low, high = 0, min(len(a), len(b))
    while low < high:
        middle = (low + high + 1) // 2
        if a[:middle] == b[:middle]:
            low = middle
        else:
            high = middle - 1
    return low


class LLMClient:
    """Wrapper around OpenAI-compatible API client for local LLM generation."""
    
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._clients: Dict[Any, AsyncOpenAI] = {}
        self.client = self._client_for(config.llm.base_url, config.llm.api_key)
        self.model = config.llm.model
        self.temperature = config.llm.temperature
        self.max_tokens = config.llm.max_tokens
        for override in context_window_overrides(config):
            logger.warning(
                "%s: extra_body sets a %s-token context window, which is requested and budgeted; its declared "
                "context_window %s is ignored.", override["model"], override["requested_context_window"],
                override["declared_context_window"],
            )
        # MA4.3 - audit-only. See _audit_llm_network_access below; this is
        # never consulted for enforcement, only logged.
        self.execution_policy = ExecutionPolicy()
        # R1 Deliverable 5 (performance telemetry, 2026-09-08) - observational
        # side-channel only: the metadata for the MOST RECENT complete() call,
        # for a caller that wants it (kriya/workflow/attempt.py's Developer-
        # timing wrapper, kriya/workflow/workflow.py's Planner/Architect/
        # Reviewer timing) to read AFTER the call returns. Never read by
        # complete() itself, never influences temperature/max_tokens/retry/
        # model selection or any return value - complete()'s own return
        # contract (a bare str) is completely unchanged. None until the first
        # successful call; overwritten (not appended) on every call, since a
        # caller must read it immediately after its own await completes,
        # before any other concurrent call on the same client could overwrite
        # it - single-flight per client instance is the existing calling
        # convention everywhere in this codebase already (no concurrent
        # complete() calls share one LLMClient), not a new constraint.
        self.last_call_metrics: Optional[Dict[str, Any]] = None
        # PRD-015: the normalized result (kriya.core.completion.CompletionResult)
        # of the most recent call, set before the call returns or raises;
        # None while a call is in flight. Same single-flight convention.
        self.last_completion = None
        # PRD-016: one entry per call whose context window or output budget
        # was automatically enlarged (adaptive budget policy), appended when
        # the call finishes. A workflow drains these into RunEvents so they
        # persist in the run trace (kriya/workflow/state.py
        # drain_budget_expansions); each is also logged.
        self.budget_expansions: List[Dict[str, Any]] = []
        # Prompt-prefix reuse telemetry: the previous (system, user) request
        # text sent to each model - an inference server reuses the KV cache
        # of the longest prefix it shares with the request it last served.
        self._previous_request: Dict[str, Tuple[str, str]] = {}
        # PRD-018: per-role, per-runtime counters of every call through this
        # client (the role comes from kriya.core.role_metrics.model_role).
        from kriya.core.role_metrics import RoleMetrics

        self.role_metrics = RoleMetrics()

    def _client_for(self, base_url: str, api_key: str) -> AsyncOpenAI:
        """PROVIDER-CONTRACT-001: one client per (endpoint, key), reused for
        the life of this LLMClient: never an SDK retry (SDK_MAX_RETRIES), the
        Kriya-owned timeout, and a direct HTTP transport that ignores proxy
        variables inherited from the environment (trust_env=False) - local
        inference traffic never leaves through an operator's proxy."""
        key = (base_url, api_key)
        client = self._clients.get(key)
        if client is None:
            timeout = transport_timeout(self.config)
            client = AsyncOpenAI(
                api_key=api_key, base_url=base_url, max_retries=SDK_MAX_RETRIES, timeout=timeout,
                http_client=httpx.AsyncClient(trust_env=False, timeout=timeout),
            )
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        """Close every transport this client opened (deterministic release of
        its connection pools)."""
        clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            await client.close()

    def _deadline(self) -> Tuple[Optional[float], Optional[str]]:
        """The absolute monotonic deadline of a model call made now, and its
        source: the active run's generation budget, narrowed by an
        inference_deadline() cap; (None, None) when neither applies."""
        from kriya.control.run_coordinator import claim_run_generation_clock

        candidates = []
        started = claim_run_generation_clock()
        budget = self.config.autonomy.generation_time_budget_seconds
        if started is not None and budget is not None:
            candidates.append((started + budget, DEADLINE_SOURCE_RUN_BUDGET))
        cap = _INFERENCE_DEADLINE.get()
        if cap is not None:
            candidates.append((cap, DEADLINE_SOURCE_LOCAL_CAP))
        return min(candidates) if candidates else (None, None)

    def _deadline_record(self) -> Dict[str, Any]:
        """The deadline a request dispatched now is bound by (telemetry and
        the typed error's evidence; never prompt content)."""
        deadline, source = self._deadline()
        configured = self.config.llm.transport.read_timeout_seconds
        record: Dict[str, Any] = {"deadline_bound": deadline is not None, "deadline_source": source,
                                  "configured_transport_timeout_ms": int(configured * 1000)}
        if deadline is not None:
            remaining = max(0.0, deadline - time.monotonic())
            record["remaining_budget_at_dispatch_ms"] = int(remaining * 1000)
            record["effective_request_timeout_ms"] = int(min(configured, remaining) * 1000)
        else:
            record["effective_request_timeout_ms"] = int(configured * 1000)
        return record

    def _require_time_left(self) -> None:
        """INFERENCE_DEADLINE_EXHAUSTED before any provider contact (runtime
        probe, served-context observation or inference request)."""
        record = self._deadline_record()
        if record["deadline_bound"] and record["remaining_budget_at_dispatch_ms"] <= 0:
            raise InferenceDeadlineError(INFERENCE_DEADLINE_EXHAUSTED, record)

    def _request_timeout(self) -> httpx.Timeout:
        """This request's timeout: the configured one, its read timeout cut
        to the remaining deadline. A deadline already passed is
        INFERENCE_DEADLINE_EXHAUSTED before any request."""
        record = self._deadline_record()
        if record["deadline_bound"] and record["remaining_budget_at_dispatch_ms"] <= 0:
            raise InferenceDeadlineError(INFERENCE_DEADLINE_EXHAUSTED, record)
        return transport_timeout(self.config, record["effective_request_timeout_ms"] / 1000)

    async def _within_deadline(self, model: str,
                               request: Callable[[httpx.Timeout], Awaitable[_T]]) -> _T:
        """One request, bounded in total by the deadline: the read timeout
        bounds each wait for bytes, so a streamed answer is also cut at the
        deadline itself. A timeout the deadline caused is
        INFERENCE_DEADLINE_EXCEEDED, never a provider timeout."""
        timeout = self._request_timeout()
        deadline, _source = self._deadline()
        if deadline is None:
            return await request(timeout)
        record = self._deadline_record()
        deadline_binding = record["effective_request_timeout_ms"] < record["configured_transport_timeout_ms"]
        try:
            return await asyncio.wait_for(request(timeout), timeout=max(0.0, deadline - time.monotonic()))
        except Exception as error:
            timed_out = isinstance(error, asyncio.TimeoutError) or (
                self._runtime(model).classify_error(error) is RuntimeErrorKind.TIMEOUT)
            if timed_out and (deadline_binding or time.monotonic() >= deadline):
                raise InferenceDeadlineError(INFERENCE_DEADLINE_EXCEEDED, record) from error
            raise

    def _limits(self, fingerprint: Any, settings: Any) -> Dict[str, Any]:
        from kriya.core.model_qualification import measured_limits_for

        return measured_limits_for(fingerprint, self.config, settings=settings) if fingerprint.exact else {}

    def _contract_record(self, runtime: InferenceRuntimePort, plan: Any, budget: Any) -> Dict[str, Any]:
        """Telemetry of the contract a request was sent under (settings,
        context and transport policy; never prompt content). The response
        identifiers, token counts and finish reason are the result's own
        fields; the prompt consumption verdict is added after the call."""
        transport = self.config.llm.transport
        deadline = self._deadline_record()
        return {
            "adapter": runtime.name,
            "settings": plan.identity(),
            "context": (budget.to_dict() or {}).get("context_state"),
            "timeout_seconds": {"connect": transport.connect_timeout_seconds,
                                "read": transport.read_timeout_seconds},
            "inference_deadline_bound": deadline["deadline_bound"],
            "deadline": deadline,
            "sdk_max_retries": SDK_MAX_RETRIES,
            "proxy_policy": "direct",
        }

    def _strict_identity(self) -> bool:
        """Production requires the exact runtime identity: requested
        settings the provider verifiably does not apply, unknown request
        fields and a served window other than the requested one are refused."""
        return getattr(self.config, "runtime_profile", None) == "production"

    def _provider_plan(self, runtime: InferenceRuntimePort, *, extra_body: Optional[Dict[str, Any]],
                       temperature: Optional[float], is_reasoning: bool, declared_window: Optional[int],
                       fingerprint: Any) -> Any:
        """PROVIDER-CONTRACT-001: this request's settings as the provider
        receives them, refused before inference when they cannot be proven
        (ProviderContractError)."""
        requested = runtime.configured_context_window(extra_body) or declared_window
        plan = runtime.request_plan(extra_body, temperature=temperature, reasoning_flag=is_reasoning,
                                    requested_context_window=requested, fingerprint=fingerprint)
        plan.enforce(strict=self._strict_identity())
        return plan

    async def _observe_served(self, runtime: InferenceRuntimePort, *, model: str, base_url: str,
                              api_key: str) -> Any:
        """One typed observation of the window the runtime has loaded for
        ``model`` right now (model_runtime.ServedContextObservation). Never
        raises: an adapter error is an OBSERVATION_FAILED result."""
        from kriya.core.model_runtime import OBSERVATION_FAILED, ServedContextObservation

        try:
            return await asyncio.to_thread(runtime.observe_served_context_state, base_url=base_url, model=model,
                                           api_key=api_key)
        except Exception as error:
            logger.warning("Served-context observation failed for %s: %s", model, error)
            return ServedContextObservation(OBSERVATION_FAILED, reason=f"{type(error).__name__}: {error}"[:300])

    async def _check_after_call(self, result, runtime: InferenceRuntimePort, *, model: str, base_url: str,
                                api_key: str, requested_window: Optional[int], dispatched_bytes: int,
                                limits: Dict[str, Any], identified: bool) -> None:
        """PROVIDER-CONTRACT-001, after the response: the window the call was
        actually served with, and whether the provider evaluated the whole
        prompt. A violation turns the result into PROVIDER_CONTRACT_VIOLATION
        carrying the typed error (the content is never used)."""
        from kriya.core.completion import CompletionStatus
        from kriya.core.model_runtime import OBSERVED
        from kriya.core.provider_contract import (
            PROVIDER_PROMPT_TRUNCATED,
            SERVED_CONTEXT_UNOBSERVABLE,
            Provenance,
            ProviderContractError,
            check_prompt_consumption,
            context_window_state,
        )

        violation = None
        observation = await self._observe_served(runtime, model=model, base_url=base_url, api_key=api_key)
        contract = result.protocol.setdefault("provider_contract", {})
        contract["served_context_observation"] = observation.to_dict()
        if observation.status == OBSERVED:
            contract["served_context_window_after_call"] = observation.window
            try:
                context_window_state(requested_window, observation.window, Provenance.SERVER_OBSERVED,
                                     exact=self._strict_identity())
            except ProviderContractError as error:
                violation = error
        elif runtime.provider_capabilities.feature("served_context_observation").value != "unsupported":
            # PROVIDER-CONTRACT-001A: the served window this call ran with is
            # not known. Production on an exact runtime refuses the result;
            # otherwise it is recorded as unverified, never as observed.
            if self._strict_identity() and identified:
                violation = ProviderContractError(
                    SERVED_CONTEXT_UNOBSERVABLE,
                    f"the served context window of '{model}' could not be observed after the call "
                    f"({observation.status}: {observation.reason or 'model not loaded'}); production "
                    "requires the exact served identity (a runtime that unloads the model right "
                    "after a request, such as OLLAMA_KEEP_ALIVE=0, cannot provide it)",
                    {"model": model, "observation": observation.to_dict()})
            else:
                contract["served_context_window_after_call_provenance"] = Provenance.UNVERIFIED.value
                logger.warning("Served context window of %s after the call is unverified (%s: %s)",
                               model, observation.status, observation.reason)
        # Provider usage is evidence only from an identified (exact) runtime.
        truncation = check_prompt_consumption(
            dispatched_bytes=dispatched_bytes, reported_prompt_tokens=result.prompt_tokens_reported,
            bytes_per_token_ceiling=limits.get("bytes_per_token_ceiling")) if identified else None
        contract["prompt_tokens_reported"] = result.prompt_tokens_reported
        contract["dispatched_bytes"] = dispatched_bytes
        contract["prompt_consumption"] = (
            "truncated" if truncation is not None
            else "consistent" if identified and result.prompt_tokens_reported else "unverified")
        if truncation is not None:
            violation = ProviderContractError(
                PROVIDER_PROMPT_TRUNCATED,
                f"the provider evaluated {truncation['reported_prompt_tokens']} prompt tokens, fewer than the "
                f"{truncation['minimum_expected_tokens']} a {dispatched_bytes}-byte prompt can tokenize to: input "
                "was dropped", truncation)
        if violation is not None:
            logger.error("%s: %s", model, violation)
            contract["violation"] = {"reason_code": violation.reason_code, **violation.details}
            result.status = CompletionStatus.PROVIDER_CONTRACT_VIOLATION
            result.backend_error = str(violation)[:500]
            result.error = violation

    def _audit_llm_network_access(self, url: str) -> None:
        """MA4.3 - audit-only ExecutionPolicy consultation, wired in front of
        (never in place of) this file's own is_local_url/EgressViolationError
        enforcement below. Per kriya/policy/__init__.py's own principle,
        policy DECIDES, existing mechanisms ENFORCE - and for the LLM egress
        boundary specifically, the existing mechanism remains the sole
        enforcer no matter what policy says. This call can never affect
        whether the real request proceeds: its result is only logged (audit
        mode - see the MA4 design doc's rollout plan), and any exception it
        raises is caught here and logged, never propagated - a bug in the
        still-young policy engine (which default-denies LLM_NETWORK_ACCESS
        today, since MA4.6's real network rules haven't landed yet) must
        never block, alter, or take credit for this file's own unconditional
        egress enforcement."""
        try:
            result = self.execution_policy.evaluate(
                ActionRequest(action_type=ActionType.LLM_NETWORK_ACCESS, network_target=url)
            )
            logger.debug(
                "MA4 policy audit (not enforced): LLM_NETWORK_ACCESS to '%s' -> %s (%s)",
                url, result.decision.value, result.reason_code,
            )
        except Exception as e:
            logger.debug("MA4 policy audit call failed (ignored, audit-only): %s", e)

    # ------------------------------------------------------------------
    # PRD-013/015/016: runtime identity, normalized result, dispatch budget
    # ------------------------------------------------------------------

    def _binding(self, model: str) -> Dict[str, Any]:
        """Config for ``model``: primary llm, an llm_chain entry or an
        agent_llms binding (the first exact, case-folded match), including
        its own sampling settings (``temperature``, ``extra_body``): a call to
        a model executes with that model's settings, never the primary's,
        unless the caller overrides them (MODEL-EVIDENCE-HARDENING-001:
        qualified inference identity == executed inference identity)."""
        from kriya.core.model_runtime import binding_output_tokens

        target = (model or "").casefold()
        cfg = self.config
        if cfg.llm.model.casefold() == target:
            return {"context_window": cfg.llm.context_window, "reasoning": cfg.llm.reasoning,
                    "context_policy": cfg.llm.context_policy, "max_tokens": self.max_tokens,
                    "temperature": self.temperature, "extra_body": cfg.llm.extra_body or None,
                    "inference_runtime": cfg.llm.inference_runtime}
        candidates = list(cfg.llm_chain)
        for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
            role_cfg = getattr(cfg.agent_llms, role, None)
            if role_cfg is None:
                continue
            if role_cfg.llm is not None:
                candidates.append(role_cfg.llm)
            candidates.extend(role_cfg.llm_chain)
        for candidate in candidates:
            if candidate.model.casefold() == target:
                own_temperature = getattr(candidate, "temperature", None)
                return {"context_window": candidate.context_window, "reasoning": candidate.reasoning,
                        "context_policy": candidate.context_policy,
                        "max_tokens": binding_output_tokens(cfg, candidate),
                        "temperature": own_temperature if own_temperature is not None else self.temperature,
                        "extra_body": getattr(candidate, "extra_body", None) or None,
                        "inference_runtime": getattr(candidate, "inference_runtime", None)}
        return {"context_window": cfg.llm.context_window, "reasoning": cfg.llm.reasoning,
                "context_policy": cfg.llm.context_policy, "max_tokens": self.max_tokens,
                "temperature": self.temperature, "extra_body": cfg.llm.extra_body or None,
                "inference_runtime": cfg.llm.inference_runtime}

    def _runtime(self, model: str) -> InferenceRuntimePort:
        """INF-001: the runtime adapter ``model``'s binding selects (cached
        registry lookup; an unknown name is refused)."""
        return runtime_for_binding(self._binding(model))

    async def _runtime_fingerprint(self, model: str, base_url: str, api_key: str,
                                   extra_body: Optional[Dict[str, Any]]):
        """PRD-013: the exact runtime this call goes to (cached per process),
        recorded on the active run. Never fails the call."""

        from kriya.control.run_coordinator import record_model_runtime_use
        from kriya.core.model_runtime import (
            ModelRuntimeFingerprint,
            endpoint_identity,
            kriya_protocol_identity,
            resolve_model_runtime,
        )

        try:
            runtime = self._runtime(model)
            protocol = kriya_protocol_identity(self.config, model)
            fingerprint = await asyncio.to_thread(
                resolve_model_runtime,
                base_url=base_url, model=model, api_key=api_key,
                egress_policy=self.config.autonomy.egress_policy,
                configured_context=runtime.configured_context_window(extra_body),
                kriya_protocol=protocol, config=self.config, runtime=runtime,
            )
        except Exception as error:
            logger.warning("Model runtime fingerprint unavailable for %s: %s", model, error)
            fingerprint = ModelRuntimeFingerprint(
                alias=model, endpoint=endpoint_identity(base_url), probe_errors=(str(error),),
            )
        if fingerprint.exact:
            record_model_runtime_use(fingerprint.digest)
        return fingerprint

    def _dispatch_budget(self, *, model: str, fingerprint, messages: List[Dict[str, Any]],
                         tools: Optional[List[Dict[str, Any]]], max_tokens: int, is_reasoning: bool,
                         base_url: str, api_key: str, settings, expected_output=None,
                         served_context: Optional[int] = None):
        """PRD-016: choose this request's context window and output budget
        (kriya/core/token_budget.py plan_dispatch). Raises
        ContextBudgetUnsatisfiableError / OutputBudgetUnsatisfiableError
        before any inference. ``settings`` are the request's own inference
        settings (MODEL-QUAL-IDENTITY-001): measured limits and qualified
        tiers come only from a record qualified with exactly them."""
        from dataclasses import replace

        from kriya.core.model_qualification import measured_limits_for
        from kriya.core.provider_contract import Provenance, context_window_state
        from kriya.core.token_budget import DEFAULT_REASONING_ALLOWANCE_TOKENS, plan_dispatch

        binding = self._binding(model)
        policy = binding["context_policy"]
        limits = measured_limits_for(fingerprint, self.config, settings=settings) if fingerprint.exact else {}
        requested = fingerprint.configured_context_window or binding.get("context_window")
        runtime = self._runtime(model)
        # PROVIDER-CONTRACT-001: requested, served and budget are separate
        # facts. Served is only what the runtime reports: its loaded window
        # (observed), else the model's own configured window; never the
        # request. A served window below the requested one is refused, and
        # so is a different one under production's exact identity; the
        # budget is never raised above the requested window because the
        # server happens to serve more.
        if served_context is not None:
            served, provenance, source = served_context, Provenance.SERVER_OBSERVED, "served_observed"
        elif runtime.capabilities.per_request_context_window:
            # Sent with the request and applied by this runtime (declared),
            # not yet observed; checked against the served window after the call.
            served, provenance, source = None, Provenance.UNVERIFIED, "requested_per_request"
        elif fingerprint.effective_context_window:
            served, provenance, source = (fingerprint.effective_context_window, Provenance.SERVER_MODEL_CONFIG,
                                          "server_model_config")
        elif runtime.provider_capabilities.feature("served_context_observation").value != "unsupported":
            served, provenance, source = None, Provenance.UNVERIFIED, "requested_unverified"
        else:
            # INF-001: a runtime that takes no per-request window and cannot
            # report one serves whatever it was started with.
            served, provenance, source = None, Provenance.UNVERIFIED, "provider_managed"
        state = context_window_state(requested, served, provenance, exact=self._strict_identity())
        window = state.budget
        reasoning = 0
        reasoning_source = None
        if is_reasoning:
            measured = limits.get("reasoning_tokens_max")
            reasoning = int(measured or DEFAULT_REASONING_ALLOWANCE_TOKENS)
            reasoning_source = "qualified" if measured else "unqualified_default"
            max_tokens = reasoning_max_tokens(max_tokens, measured)
        tokenizer = fingerprint.tokenizer_digest if fingerprint.tokenizer_digest != "unavailable" else None
        from kriya.core.model_qualification import offered_context_tiers

        offer = offered_context_tiers(self.config, model, fingerprint, policy, base_url=base_url, api_key=api_key,
                                      settings=settings)
        tiers, ceiling, note = offer.tiers, offer.ceiling, offer.note
        try:
            decision = plan_dispatch(
                messages=messages, tools=tools, requested_max_tokens=max_tokens,
                context_window=window, window_source=source, tokenizer_digest=tokenizer,
                qualified_bytes_per_token=limits.get("bytes_per_token_floor"),
                qualified_non_ascii_bytes_per_token=limits.get("non_ascii_bytes_per_token_floor"),
                reasoning_allowance=reasoning, tiers=tiers, policy_mode=policy.mode,
                expected_output=expected_output, hard_context_ceiling=ceiling,
                hard_output_ceiling=policy.max_output_tokens,
            )
        except Exception as refusal:
            if note and getattr(refusal, "decision", None) is not None:
                refusal.decision = replace(refusal.decision, tier_note=note)
            raise
        return replace(decision, tier_note=note, context_state=state.to_dict(), reasoning_reserve_source=reasoning_source)

    def _request_options(self, extra_body: Optional[Dict[str, Any]], budget, model: str) -> Optional[Dict[str, Any]]:
        """The request's extra_body asking the runtime for the selected
        context tier (a copy; the configured extra_body is never changed)."""
        if not budget.context_expanded:
            return extra_body
        return self._runtime(model).with_context_window(extra_body, budget.context_window)

    def _note_budget_expansion(self, result, budget, *, reason: Optional[str] = None) -> None:
        """Evidence for an automatic enlargement (appended to
        budget_expansions and logged)."""
        event = {
            "model": result.model,
            "reason": reason or budget.selection_reason,
            "policy_mode": budget.policy_mode,
            "preferred_context_window": budget.preferred_context_window,
            "selected_context_window": budget.context_window,
            "preferred_output_tokens": budget.requested_max_tokens,
            "selected_output_tokens": result.max_tokens,
            "required_prompt_tokens": budget.prompt_tokens,
            "expected_output_tokens": budget.expected_output_tokens,
            "output_grounding": budget.output_grounding,
            "qualification_source": budget.qualification_source,
            "hard_context_ceiling": budget.hard_context_ceiling,
            "hard_output_ceiling": budget.hard_output_ceiling,
            "runtime_fingerprint": result.runtime_fingerprint,
            "elapsed_seconds": round(result.elapsed_seconds, 3),
            "status": result.status.value,
        }
        self.budget_expansions.append(event)
        logger.warning(
            "Adaptive budget (%s): %s context %s -> %s, output %s -> %s (prompt ~%s tokens, expected output %s, "
            "tier source %s).",
            event["reason"], result.model, event["preferred_context_window"], event["selected_context_window"],
            event["preferred_output_tokens"], event["selected_output_tokens"], event["required_prompt_tokens"],
            event["expected_output_tokens"], event["qualification_source"],
        )

    @staticmethod
    def _note_admission_miss(result, error: BaseException, *, budget, messages: List[Dict[str, Any]],
                             tools: Optional[List[Dict[str, Any]]], runtime: InferenceRuntimePort) -> None:
        """PROVIDER-CONTRACT-001A: admission said the prompt fits, the
        provider refused it as over its context. Recorded (never prompt
        text) so the frequency of admission undercounts is measured."""
        from kriya.core.provider_contract import PROVIDER_PROMPT_TRUNCATED
        from kriya.core.role_metrics import current_model_role
        from kriya.core.token_budget import dispatch_text

        if getattr(error, "reason_code", None) != PROVIDER_PROMPT_TRUNCATED or budget is None:
            return
        details = getattr(error, "details", {}) or {}
        prompt_bytes = len(dispatch_text(messages, tools).encode("utf-8"))
        provider_tokens = details.get("provider_prompt_tokens")
        miss = {
            "predicted_prompt_tokens": budget.prompt_tokens,
            "provider_prompt_tokens": provider_tokens,
            "served_context": details.get("provider_context_window") or budget.context_window,
            "prompt_bytes": prompt_bytes,
            "bytes_per_token_observed": round(prompt_bytes / provider_tokens, 4) if provider_tokens else None,
            "counting_method": budget.counting_method,
            "role": current_model_role(), "adapter": runtime.name,
            "model": result.model, "runtime_fingerprint": result.runtime_fingerprint,
        }
        result.protocol.setdefault("provider_contract", {})["admission_miss"] = miss
        logger.warning("Admission miss: %s", miss)

    def _record_deadline_stop(self, result, stopped: "InferenceDeadlineError", *, started: float, budget) -> None:
        """A deadline stop is a recorded TIMEOUT carrying its reason code
        (the typed error is then raised to the caller)."""
        from kriya.core.completion import CompletionStatus

        result.status = CompletionStatus.TIMEOUT
        result.backend_status = "deadline"
        result.backend_error = str(stopped)[:500]
        result.error = stopped
        result.protocol.setdefault("provider_contract", {}).setdefault("deadline", {})["timeout_reason"] = (
            stopped.reason_code)
        self._finish(result, started=started, budget=budget)

    def _finish(self, result, *, started: float, budget) -> None:
        """Common post-call bookkeeping: timing, budget comparison, the
        usage line and the observational metrics."""

        import click

        from kriya.core.token_budget import compare_with_usage

        result.elapsed_seconds = time.time() - started
        deadline = (result.protocol or {}).get("provider_contract", {}).get("deadline")
        if deadline is not None:
            deadline["elapsed_request_ms"] = int(result.elapsed_seconds * 1000)
        role_metrics = getattr(self, "role_metrics", None)
        if role_metrics is not None:
            role_metrics.record_call(
                model=result.model, runtime_digest=result.runtime_fingerprint,
                runtime_exact=result.runtime_fingerprint_exact, status=result.status.value,
                inference_settings_digest=result.inference_settings_digest,
                latency_seconds=result.elapsed_seconds, prompt_tokens=result.prompt_tokens or 0,
                completion_tokens=result.completion_tokens or 0, tokens_estimated=bool(result.tokens_estimated),
            )
        if budget is not None:
            result.budget = budget.to_dict()
            comparison = compare_with_usage(result.budget, result.prompt_tokens, model=result.model)
            if comparison:
                result.budget.update(comparison)
            if budget.expanded:
                self._note_budget_expansion(result, budget)
            if (result.protocol or {}).get("empty_content_floor_retry"):
                self._note_budget_expansion(result, budget, reason="empty_content_floor_retry")
        self.last_completion = result
        if result.error is None:
            click.secho(
                f"\n[Usage: {result.prompt_tokens} input tokens, {result.completion_tokens} output tokens | "
                f"Time: {result.elapsed_seconds:.2f}s | Finish: {result.finish_reason or 'unreported'}"
                f"{'' if result.status.value == 'OK' else ' | ' + result.status.value}]",
                fg="blue", dim=True,
            )
            # R1 Deliverable 5 - observational only. tokens_estimated=True means
            # the server's response carried no usage field for prompt and/or
            # completion tokens, so one or both counts are the char/4 heuristic,
            # never presented as exact. finish_reason (VAL-001 G1-R3) is None
            # when the provider/SDK never reported one - never a fabricated "stop".
            self.last_call_metrics = {
                "model": result.model,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "tokens_estimated": result.tokens_estimated,
                "duration_seconds": result.elapsed_seconds,
                "finish_reason": result.finish_reason,
                "runtime_fingerprint": result.runtime_fingerprint,
                "protocol_status": result.status.value,
                "completion": result.to_telemetry(),
            }

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        json_mode: bool = False,
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        reasoning_override: Optional[bool] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
        expected_output=None,
    ) -> str:
        """Call the local LLM server and return the text completion (supporting streaming and JSON mode).

        PRD-015 compatibility API over ``complete_result``: returns the visible
        content and raises the original exception for a backend error or
        timeout, exactly as before. Capability-sensitive callers read the
        normalized ``self.last_completion`` (a CompletionResult) after the
        call, or call ``complete_result`` directly.

        temperature_override/max_tokens_override/reasoning_override/extra_body_override let
        a caller fully specify an alternate model's real config (not just model/base_url/
        api_key) - without them, is_reasoning falls back to scanning the top-level llm_chain
        for a matching model name (kept for backward compatibility with the existing
        Developer escalation call sites, which only ever pass the first three), and
        extra_body falls back to the PRIMARY model's own extra_body (kriya/config/config.py's
        LLMConfig) - which is only correct when no fallback model is actually in play. A
        caller escalating to a FallbackModelConfig entry should pass its own
        extra_body_override (even an empty dict, to mean "no extra_body for this model"),
        the same way it already passes that entry's model/base_url/api_key/temperature -
        otherwise the primary's own extra_body (e.g. a reasoning_effort tuned for a
        completely different model) silently applies to the fallback call instead."""
        result = await self.complete_result(
            system_prompt, user_prompt, stream_callback=stream_callback, json_mode=json_mode,
            model_override=model_override, base_url_override=base_url_override,
            api_key_override=api_key_override, temperature_override=temperature_override,
            max_tokens_override=max_tokens_override, reasoning_override=reasoning_override,
            extra_body_override=extra_body_override, expected_output=expected_output,
        )
        if result.error is not None:
            logger.error(f"Local LLM call failed: {result.error}", exc_info=result.error)
            raise result.error
        if result.status.value != "OK":
            logger.warning(
                "Completion from '%s' is %s (finish_reason=%s); returned to a compatibility caller as text.",
                result.model, result.status.value, result.finish_reason,
            )
        return result.content

    async def complete_result(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        json_mode: bool = False,
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        reasoning_override: Optional[bool] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
        expected_output=None,
        response_schema: Optional[Dict[str, Any]] = None,
    ):
        """PRD-015: one completion as a normalized ``CompletionResult``.

        ``response_schema`` (Code Intelligence R1 slice 2): a JSON schema the
        provider must constrain the response to. Only a runtime declaring
        ``json_schema_output`` is sent one; any other refuses with
        StructuredOutputUnsupportedError before the provider is contacted, and
        the schema is never dropped on a retry.

        Raises only for policy refusals (egress, CONTEXT_BUDGET_UNSATISFIABLE,
        OUTPUT_BUDGET_UNSATISFIABLE), a Kriya deadline stop
        (InferenceDeadlineError: recorded as TIMEOUT first) and cancellation (recorded as CANCELLED
        first, never swallowed); a backend error or timeout is returned as
        BACKEND_ERROR/TIMEOUT.

        ``expected_output`` (token_budget.OutputExpectation) is a caller's
        GROUNDED estimate of the output this request needs (e.g. the size of
        a file being rewritten); under the adaptive budget policy it may
        enlarge the output budget and select a larger qualified context
        window (PRD-016). Without it the output never grows."""

        from kriya.core.completion import CompletionResult, CompletionStatus, classify, split_reasoning

        # MA4.3 - audit-only, always runs regardless of egress_policy, and can
        # never affect the unconditional enforcement immediately below.
        url_to_check = base_url_override or self.config.llm.base_url
        self._audit_llm_network_access(url_to_check)

        # Validate egress policy (unchanged - the sole enforcement path)
        if self.config.autonomy.egress_policy == "local_only":
            if not is_local_url(url_to_check):
                raise EgressViolationError(
                    f"Egress violation: Request to external API '{url_to_check}' blocked under 'local_only' policy."
                )

        model = model_override or self.model
        client = self.client

        if base_url_override or api_key_override:
            client = self._client_for(base_url_override or self.config.llm.base_url,
                                      api_key_override or self.config.llm.api_key)

        if reasoning_override is not None:
            is_reasoning = reasoning_override
        else:
            is_reasoning = self.config.llm.reasoning
            if model_override:
                for fb in self.config.llm_chain:
                    if fb.model == model_override:
                        is_reasoning = fb.reasoning
                        break

        own = self._binding(model)
        temperature = temperature_override if temperature_override is not None else own["temperature"]
        base_max_tokens = (
            max_tokens_override if max_tokens_override is not None else self._binding(model)["max_tokens"]
        )
        # A reasoning identity's reasoning reserve is added in _dispatch_budget,
        # where its measured limits are known.
        max_tokens = base_max_tokens
        if extra_body_override is not None:
            extra_body = extra_body_override or None
        else:
            extra_body = own["extra_body"]
        # FALLBACK-CONTEXT-WINDOW-001: the request carries the window the
        # budget below plans against (the binding's declared context_window,
        # unless extra_body sets the provider option itself).
        extra_body = request_extra_body(extra_body, own["context_window"], self._runtime(model))
        # Reasoning models are NOT excluded from response_format here - Ollama (at
        # least) keeps a reasoning model's <think>-equivalent output in a separate
        # "reasoning" field and json_object-constrains only the "content" field, so
        # forcing valid JSON and letting the model reason are not mutually exclusive.
        # Without this, a reasoning model has nothing forcing it to ever commit to
        # JSON at all - it can (and, observed live, sometimes does) just respond with
        # plain prose explaining its reasoning instead, which no amount of downstream
        # JSON-extraction fallback can recover since there's no JSON substring in it.
        response_format = {"type": "json_object"} if json_mode else None
        if response_schema is not None:
            if not self._runtime(model).capabilities.json_schema_output:
                raise StructuredOutputUnsupportedError(
                    f"the runtime of '{model}' ({self._runtime(model).name}) does not constrain output to a JSON "
                    "schema; a schema request is never approximated")
            response_format = {"type": "json_schema", "schema": response_schema}

        self.last_call_metrics = None
        self.last_completion = None
        self._require_time_left()
        runtime = self._runtime(model)
        fingerprint = await self._runtime_fingerprint(
            model, url_to_check, api_key_override or self.config.llm.api_key, extra_body,
        )
        plan = self._provider_plan(runtime, extra_body=extra_body, temperature=temperature,
                                   is_reasoning=is_reasoning, declared_window=own["context_window"],
                                   fingerprint=fingerprint)
        # The executed inference identity (the settings the provider receives).
        settings = request_settings(temperature=temperature, reasoning=is_reasoning, extra_body=extra_body,
                                    runtime=runtime)
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        api_key = api_key_override or self.config.llm.api_key
        served = (await self._observe_served(runtime, model=model, base_url=url_to_check, api_key=api_key)).window
        budget = self._dispatch_budget(
            model=model, fingerprint=fingerprint, messages=messages, tools=None,
            max_tokens=max_tokens, is_reasoning=is_reasoning, base_url=url_to_check, api_key=api_key,
            settings=settings, served_context=served,
            expected_output=expected_output,
        )
        max_tokens = budget.max_tokens
        wire_body = plan.wire_body or None
        if budget.context_expanded:
            # The selected tier is a different runtime input (num_ctx), so a
            # different fingerprint: the call is attributed to it.
            extra_body = self._request_options(extra_body, budget, model)
            wire_body = self._request_options(wire_body, budget, model)
            fingerprint = await self._runtime_fingerprint(model, url_to_check, api_key, extra_body)

        logger.info(f"Sending completion request to local LLM [Model: {model}, Stream: {stream_callback is not None}, JSON Mode: {json_mode}, Reasoning: {is_reasoning}]")
        start_time = time.time()
        result = CompletionResult(
            status=CompletionStatus.OK, model=model,
            runtime_fingerprint=fingerprint.digest, runtime_fingerprint_exact=fingerprint.exact,
            inference_settings_digest=settings.digest,
            protocol={"json_mode": json_mode, "json_schema": response_schema is not None,
                      "streaming": stream_callback is not None, "tools": False,
                      "reasoning_model": is_reasoning, "response_format_dropped": False,
                      "empty_content_floor_retry": False,
                      "provider_contract": self._contract_record(runtime, plan, budget)},
            max_tokens=max_tokens,
        )

        try:
            try:
                raw = await self._request_once(
                    client, model, system_prompt, user_prompt, temperature, max_tokens,
                    wire_body, response_format, stream_callback
                )
            except Exception as e:
                # Only reasoning models risk this combination being unsupported by some
                # backend - a plain json_mode call already worked fine unconditionally
                # before this change, so there's no need to retry that case. Also
                # excludes failures that are clearly unrelated to response_format
                # (connection/timeout/auth/rate-limit/server errors, as the
                # runtime adapter classifies them - INF-001).
                if (response_format is not None and response_schema is None and is_reasoning
                        and not isinstance(e, InferenceDeadlineError)
                        and self._runtime(model).classify_error(e) is RuntimeErrorKind.REQUEST):
                    logger.warning(
                        f"Completion request with response_format={response_format} failed for "
                        f"reasoning model '{model}' ({e}) - retrying once without it (this backend/"
                        "model combination may not support JSON mode together with reasoning)."
                    )
                    result.protocol["response_format_dropped"] = True
                    raw = await self._request_once(
                        client, model, system_prompt, user_prompt, temperature, max_tokens,
                        wire_body, None, stream_callback
                    )
                else:
                    raise

            content, hidden = split_reasoning(raw["content"], anywhere=is_reasoning)
            if not is_reasoning and json_mode and not content and max_tokens < REASONING_MIN_MAX_TOKENS:
                # Some models emit hidden <think>...</think> reasoning before ever
                # committing to JSON regardless of Kriya's own is_reasoning
                # classification for them (a static per-model config guess, not a
                # live observation) - when that happens, the reasoning-only 12288-
                # token floor above never applies, so a tight max_tokens can get
                # entirely consumed by hidden reasoning with literally nothing ever
                # written to `content`. Retry once with the same floor reasoning
                # models get, rather than hand-tuning max_tokens_override per
                # affected model as each is found one at a time - confirmed live
                # for two different models this way already (gpt-oss:20b, then
                # qwen3.6:35b-a3b), neither ever classified reasoning=True in this
                # project's own llm_chain config.
                logger.warning(
                    f"JSON-mode completion from '{model}' returned empty content at "
                    f"max_tokens={max_tokens} (likely silent reasoning) - retrying once "
                    "with a 12288-token floor."
                )
                # An ungrounded enlargement: bounded by the selected window
                # and the hard output ceiling, and recorded as an expansion.
                floor = REASONING_MIN_MAX_TOKENS
                if budget.context_window:
                    floor = min(floor, max(max_tokens, budget.context_window - budget.prompt_tokens
                                           - budget.safety_margin))
                if budget.hard_output_ceiling is not None:
                    floor = min(floor, max(max_tokens, budget.hard_output_ceiling))
                result.protocol["empty_content_floor_retry"] = True
                result.max_tokens = floor
                raw = await self._request_once(
                    client, model, system_prompt, user_prompt, temperature, floor,
                    wire_body, response_format, stream_callback
                )
                content, hidden = split_reasoning(raw["content"], anywhere=True)
        except InferenceDeadlineError as stopped:
            self._record_deadline_stop(result, stopped, started=start_time, budget=budget)
            raise
        except asyncio.CancelledError:
            result.status = CompletionStatus.CANCELLED
            result.backend_status = "cancelled"
            self._finish(result, started=start_time, budget=budget)
            raise
        except Exception as e:
            result.status = (CompletionStatus.TIMEOUT if self._runtime(model).classify_error(e) is RuntimeErrorKind.TIMEOUT
                             else CompletionStatus.BACKEND_ERROR)
            result.backend_status = "error"
            result.backend_error = f"{type(e).__name__}: {e}"[:500]
            result.error = e
            self._note_admission_miss(result, e, budget=budget, messages=messages, tools=None, runtime=runtime)
            self._finish(result, started=start_time, budget=budget)
            return result

        reasoning_chars = hidden + raw["reasoning_chars"]
        result.content = content
        result.reasoning_present = reasoning_chars > 0
        result.reasoning_chars = reasoning_chars
        result.reasoning_source = (
            "reasoning_field" if raw["reasoning_chars"] else ("think_tags" if hidden else None)
        )
        result.finish_reason = raw["finish_reason"]
        result.provider_metadata = raw["provider_metadata"]
        result.prefix_reuse = self._prefix_reuse(model, system_prompt, user_prompt)
        logger.info("Prompt to %s: %d chars, %d shared with the previous request to it (first difference: %s); "
                    "prefill %s ms.", model, len(system_prompt) + len(user_prompt),
                    result.prefix_reuse["prefix_shared_chars"], result.prefix_reuse["prefix_break"],
                    result.provider_metadata.get("prompt_eval_ms", "unreported"))
        prompt_tokens, completion_tokens = raw["prompt_tokens"], raw["completion_tokens"]
        result.tokens_estimated = prompt_tokens == 0 or completion_tokens == 0
        result.prompt_tokens = prompt_tokens or int((len(system_prompt) + len(user_prompt)) / 4)
        result.prompt_tokens_reported = prompt_tokens or None
        result.completion_tokens = completion_tokens or int(len(content) / 4)
        result.status, result.parser_status = classify(
            content=content, finish_reason=result.finish_reason, tool_calls=[], structured=json_mode,
        )
        await self._check_after_call(
            result, runtime, model=model, base_url=url_to_check, api_key=api_key,
            requested_window=budget.preferred_context_window or plan_requested_window(plan),
            dispatched_bytes=dispatched_bytes(messages, None), limits=self._limits(fingerprint, settings),
            identified=fingerprint.exact)
        self._finish(result, started=start_time, budget=budget)
        return result

    def _prefix_reuse(self, model: str, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Content-free prefix telemetry: how many leading characters this
        request shares with the previous one sent to ``model``, and in which
        message they first differ ("system", "user" or "none")."""
        previous = self._previous_request.get(model)
        self._previous_request[model] = (system_prompt, user_prompt)
        if previous is None:
            return {"prefix_shared_chars": 0, "prefix_break": "first_request"}
        shared = _common_prefix_length(previous[0], system_prompt)
        if shared < len(system_prompt) or len(previous[0]) != len(system_prompt):
            return {"prefix_shared_chars": shared, "prefix_break": "system"}
        user_shared = _common_prefix_length(previous[1], user_prompt)
        return {"prefix_shared_chars": shared + user_shared,
                "prefix_break": "none" if user_shared == len(user_prompt) == len(previous[1]) else "user"}

    async def _request_once(
        self, client, model, system_prompt, user_prompt, temperature, max_tokens,
        extra_body, response_format, stream_callback
    ) -> Dict[str, Any]:
        """Issues a single completion request (streaming or not) through
        ``model``'s runtime adapter (INF-001) and returns the raw fields the
        normalizer needs: content, reasoning_chars (from a separate provider
        reasoning field), prompt_tokens, completion_tokens, finish_reason and
        provider_metadata. Split out from complete_result() so a reasoning
        model's response_format can be retried once without it."""
        response = await self._within_deadline(model, lambda timeout: self._runtime(model).complete(
            client, ChatRequest(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=temperature, max_tokens=max_tokens, extra_body=extra_body,
                response_format=response_format, stream_callback=stream_callback, timeout=timeout,
            )))
        return response.to_raw()

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Single-turn native tool-calling completion. Unlike complete()'s fixed
        system/user string pair, a tool-calling loop needs to append tool_calls and
        tool results as its own messages between turns - so this takes a full
        OpenAI-style message list and returns raw enough structure (content +
        decoded tool_calls) for the caller to drive that loop itself. One call is
        one model turn; this method does NOT loop turns - see
        kriya/workflow/self_correction.py for the only current caller's turn-budget
        loop.

        PRD-015 compatibility API over ``complete_with_tools_result``: the
        returned dict and raised exceptions are unchanged."""
        result = await self.complete_with_tools_result(
            messages, tools, model_override=model_override, base_url_override=base_url_override,
            api_key_override=api_key_override, temperature_override=temperature_override,
            max_tokens_override=max_tokens_override, extra_body_override=extra_body_override,
        )
        if result.error is not None:
            raise result.error
        return {
            "content": result.content,
            "tool_calls": [
                {key: value for key, value in call.items() if key != "source"} for call in result.tool_calls
            ],
        }

    async def complete_with_tools_result(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
        max_tokens_override: Optional[int] = None,
        extra_body_override: Optional[Dict[str, Any]] = None,
    ):
        """PRD-015: one native tool-calling turn as a CompletionResult.

        Reuses complete()'s own egress check and base_url_override/api_key_override
        client-construction logic unchanged - this is a second call site into the
        same safety boundary, not a parallel one. Native tool calls are
        normalized here; when a backend returned tool calls as text (Hermes
        JSON or Qwen XML ``<tool_call>`` blocks its own parser did not
        convert), they are recovered here too, and every call's arguments go
        through the same capability validation."""

        from kriya.core.completion import (
            CompletionResult,
            CompletionStatus,
            classify,
            parse_textual_tool_calls,
            split_reasoning,
        )

        url_to_check = base_url_override or self.config.llm.base_url
        self._audit_llm_network_access(url_to_check)

        if self.config.autonomy.egress_policy == "local_only":
            if not is_local_url(url_to_check):
                raise EgressViolationError(
                    f"Egress violation: Request to external API '{url_to_check}' blocked under 'local_only' policy."
                )

        model = model_override or self.model
        from kriya.core.model_capabilities import (
            ModelCapabilityError,
            capabilities_for_model,
            validate_tool_call_sample,
        )
        capabilities = capabilities_for_model(self.config, model)
        if not capabilities.native_tool_calls:
            raise ModelCapabilityError(
                f"Model '{model}' is configured without reliable native tool calling. "
                "Use the ordinary operation-specific generation path instead."
            )
        client = self.client
        if base_url_override or api_key_override:
            client = self._client_for(base_url_override or self.config.llm.base_url,
                                      api_key_override or self.config.llm.api_key)

        own = self._binding(model)
        temperature = temperature_override if temperature_override is not None else own["temperature"]
        max_tokens = max_tokens_override if max_tokens_override is not None else own["max_tokens"]
        if extra_body_override is not None:
            extra_body = extra_body_override or None
        else:
            extra_body = own["extra_body"]
        # FALLBACK-CONTEXT-WINDOW-001: the request carries the window the
        # budget below plans against (the binding's declared context_window,
        # unless extra_body sets the provider option itself).
        extra_body = request_extra_body(extra_body, own["context_window"], self._runtime(model))

        self.last_call_metrics = None
        self.last_completion = None
        self._require_time_left()
        fingerprint = await self._runtime_fingerprint(
            model, url_to_check, api_key_override or self.config.llm.api_key, extra_body,
        )
        api_key = api_key_override or self.config.llm.api_key
        runtime = self._runtime(model)
        plan = self._provider_plan(runtime, extra_body=extra_body, temperature=temperature,
                                   is_reasoning=bool(own["reasoning"]), declared_window=own["context_window"],
                                   fingerprint=fingerprint)
        # The executed inference identity, with the binding's reasoning flag
        # as the role identity records it (this path applies no reasoning
        # floor of its own).
        settings = request_settings(temperature=temperature, reasoning=bool(own["reasoning"]), extra_body=extra_body,
                                    runtime=runtime)
        served = (await self._observe_served(runtime, model=model, base_url=url_to_check, api_key=api_key)).window
        budget = self._dispatch_budget(
            model=model, fingerprint=fingerprint, messages=messages, tools=tools,
            max_tokens=max_tokens, is_reasoning=False, base_url=url_to_check, api_key=api_key,
            settings=settings, served_context=served,
        )
        max_tokens = budget.max_tokens
        wire_body = plan.wire_body or None
        if budget.context_expanded:
            extra_body = self._request_options(extra_body, budget, model)
            wire_body = self._request_options(wire_body, budget, model)
            fingerprint = await self._runtime_fingerprint(model, url_to_check, api_key, extra_body)
        start_time = time.time()
        result = CompletionResult(
            status=CompletionStatus.OK, model=model,
            runtime_fingerprint=fingerprint.digest, runtime_fingerprint_exact=fingerprint.exact,
            inference_settings_digest=settings.digest,
            protocol={"json_mode": False, "streaming": False, "tools": True, "tool_count": len(tools),
                      "provider_contract": self._contract_record(runtime, plan, budget)},
            max_tokens=max_tokens,
        )
        try:
            response = await self._within_deadline(model, lambda timeout: runtime.complete_with_tools(
                client, ChatRequest(
                    model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                    extra_body=wire_body, tools=tools, timeout=timeout,
                )))
        except InferenceDeadlineError as stopped:
            self._record_deadline_stop(result, stopped, started=start_time, budget=budget)
            raise
        except asyncio.CancelledError:
            result.status = CompletionStatus.CANCELLED
            result.backend_status = "cancelled"
            self._finish(result, started=start_time, budget=budget)
            raise
        except Exception as e:
            result.status = (CompletionStatus.TIMEOUT if self._runtime(model).classify_error(e) is RuntimeErrorKind.TIMEOUT
                             else CompletionStatus.BACKEND_ERROR)
            result.backend_status = "error"
            result.backend_error = f"{type(e).__name__}: {e}"[:500]
            result.error = e
            self._note_admission_miss(result, e, budget=budget, messages=messages, tools=tools, runtime=runtime)
            self._finish(result, started=start_time, budget=budget)
            return result

        tool_calls = []
        for tc in response.tool_calls:
            raw_arguments = tc.arguments
            try:
                arguments = json.loads(raw_arguments)
            except (json.JSONDecodeError, TypeError):
                # Preserve the established caller contract for malformed local-
                # model JSON: an empty argument object becomes an ordinary,
                # bounded missing-field tool error in the repair loop.
                logger.warning(
                    "Local model returned malformed tool arguments for '%s'; "
                    "using an empty argument object.",
                    tc.name,
                )
                tool_calls.append({
                    "id": tc.id, "name": tc.name, "arguments": {}, "source": "native",
                })
                result.parser_status = "malformed_tool_arguments"
                continue

            sample = validate_tool_call_sample(raw_arguments, capabilities)
            if not sample.compatible:
                logger.warning(
                    "Rejected incompatible local-model tool arguments for '%s': %s",
                    tc.name, "; ".join(sample.violations),
                )
                tool_calls.append({
                    "id": tc.id, "name": tc.name, "arguments": {},
                    "argument_error": "; ".join(sample.violations), "source": "native",
                })
                continue
            tool_calls.append({"id": tc.id, "name": tc.name, "arguments": arguments, "source": "native"})

        content, hidden = split_reasoning(response.content)
        if not tool_calls and "<tool_call>" in content:
            recovered, content, errors = parse_textual_tool_calls(content)
            for call in recovered:
                sample = validate_tool_call_sample(json.dumps(call["arguments"]), capabilities)
                if not sample.compatible:
                    call["argument_error"] = "; ".join(sample.violations)
                    call["arguments"] = {}
            tool_calls.extend(recovered)
            if errors:
                result.parser_status = "malformed_textual_tool_call"
        result.content = content
        result.tool_calls = tool_calls
        result.reasoning_chars = hidden + response.reasoning_chars
        result.reasoning_present = result.reasoning_chars > 0
        result.reasoning_source = (
            "reasoning_field" if response.reasoning_chars else ("think_tags" if hidden else None)
        )
        result.finish_reason = response.finish_reason
        result.provider_metadata = response.provider_metadata
        prompt_tokens, completion_tokens = response.prompt_tokens, response.completion_tokens
        result.tokens_estimated = prompt_tokens == 0 or completion_tokens == 0
        result.prompt_tokens = prompt_tokens or None
        result.prompt_tokens_reported = prompt_tokens or None
        result.completion_tokens = completion_tokens or None
        status, _ = classify(content=content, finish_reason=result.finish_reason, tool_calls=tool_calls,
                             structured=False)
        result.status = status
        if result.parser_status == "not_applicable" and tool_calls:
            result.parser_status = "ok"
        await self._check_after_call(
            result, runtime, model=model, base_url=url_to_check, api_key=api_key,
            requested_window=budget.preferred_context_window or plan_requested_window(plan),
            dispatched_bytes=dispatched_bytes(messages, tools), limits=self._limits(fingerprint, settings),
            identified=fingerprint.exact)
        self._finish(result, started=start_time, budget=budget)
        return result
