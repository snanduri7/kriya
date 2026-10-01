"""PRD-013: the exact local model runtime fingerprint.

A friendly tag such as ``qwen3-coder:30b`` is not a stable identity: the
weights, quantization, chat renderer, tool-call parser, tokenizer, runtime
parameters or provider version can all change while the tag stays the same.
``ModelRuntimeFingerprint`` records each of those components as the serving
backend reports it, and ``digest`` is a SHA-256 over the normalized fields.

Rules:
- A component the backend does not report is the explicit string
  ``"unavailable"``. Nothing is inferred from the model name.
- Only identity fields enter the digest. Timestamps (``modified_at``,
  ``created``) and probe diagnostics never do, so two captures of an
  unchanged runtime always agree.
- A fingerprint is ``exact`` only when the artifact digest and the provider
  version are both known. Production trust (PRD-014 qualification) is keyed
  to exact fingerprints only.

Probing uses the configured endpoint only (Ollama's native ``/api/version``,
``/api/tags`` and ``/api/show`` next to its OpenAI-compatible ``/v1``) and
never the internet. A non-local endpoint is never probed a non-local endpoint is never probed at all.
``KRIYA_MODEL_RUNTIME_PROBE=0`` disables probing (the mocked test
suite sets it), which yields an all-unavailable, non-exact fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from kriya.core.inference_runtime import (
    ChatRequest,
    ChatResponse,
    InferenceRuntimePort,
    OpenAICompatibleTransport,
    RawToolCall,
    RuntimeCapabilities,
    RuntimeErrorKind,
    register_runtime_adapter,
)

logger = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"

# Bumped whenever Kriya's own request/response handling for a model changes
# in a way that could change qualification results (message shape, JSON
# mode, reasoning stripping, tool-call normalization, truncation handling).
# /3 (PROVIDER-CONTRACT-001): the wire carries only settings Ollama's /v1
# applies; the served window and server-side sampling are observed, never
# taken from the request.
MODEL_PROTOCOL_ADAPTER_VERSION = "kriya-openai-compat/3"

FINGERPRINT_SCHEMA_VERSION = 1

PROBE_ENV_VAR = "KRIYA_MODEL_RUNTIME_PROBE"
_PROBE_TIMEOUT_SECONDS = 5.0

# Components that must be known for a fingerprint to identify one exact
# served artifact (PRD-014 refuses to qualify anything less).
EXACT_REQUIRED_COMPONENTS: Tuple[str, ...] = ("artifact_digest", "provider_version")


@dataclass(frozen=True)
class ModelRuntimeFingerprint:
    alias: str
    endpoint: str
    provider: str = UNAVAILABLE
    provider_version: str = UNAVAILABLE
    artifact_digest: str = UNAVAILABLE
    weights_digest: str = UNAVAILABLE
    model_format: str = UNAVAILABLE
    family: str = UNAVAILABLE
    parameter_size: str = UNAVAILABLE
    quantization: str = UNAVAILABLE
    chat_template: str = UNAVAILABLE
    tool_call_parser: str = UNAVAILABLE
    tokenizer_identity: str = UNAVAILABLE
    tokenizer_digest: str = UNAVAILABLE
    runtime_parameters_digest: str = UNAVAILABLE
    served_capabilities: Tuple[str, ...] = ()
    # PROVIDER-CONTRACT-001: the served model's own configuration (Ollama
    # /api/show parameters, normalized "key value" pairs): the settings a
    # request cannot carry are these.
    server_parameters: Tuple[str, ...] = ()
    model_context_length: Optional[int] = None
    configured_context_window: Optional[int] = None
    effective_context_window: Optional[int] = None
    kriya_protocol: str = UNAVAILABLE
    adapter_version: str = MODEL_PROTOCOL_ADAPTER_VERSION
    schema_version: int = FINGERPRINT_SCHEMA_VERSION
    # Diagnostics only: never part of the digest.
    probe_errors: Tuple[str, ...] = field(default=(), compare=False)

    def identity_fields(self) -> Dict[str, Any]:
        fields = asdict(self)
        fields.pop("probe_errors")
        fields["served_capabilities"] = sorted(self.served_capabilities)
        fields["server_parameters"] = list(self.server_parameters)
        return fields

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.identity_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def missing_components(self) -> Tuple[str, ...]:
        return tuple(
            name for name, value in self.identity_fields().items()
            if value == UNAVAILABLE or value is None or value == []
        )

    @property
    def exact(self) -> bool:
        return all(getattr(self, name) != UNAVAILABLE for name in EXACT_REQUIRED_COMPONENTS)

    def to_dict(self) -> Dict[str, Any]:
        record = self.identity_fields()
        record["digest"] = self.digest
        record["exact"] = self.exact
        record["missing_components"] = list(self.missing_components)
        record["probe_errors"] = list(self.probe_errors)
        return record


def endpoint_identity(base_url: str) -> str:
    """scheme://host[:port]/path with credentials, query and fragment removed."""
    parsed = urllib.parse.urlsplit(base_url or "")
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc.lower(), parsed.path.rstrip("/"), "", ""))


# --------------------------------------------------------------------------
# The packaged default runtime adapter (INF-001, kriya/core/inference_runtime.py):
# everything Kriya knows about HOW this served runtime takes its per-request
# context window and identifies its model lives here, so generic
# qualification, budgeting, routing and evidence code never names a
# provider. The module functions below are its implementation; generic code
# reaches them through the binding's adapter (runtime_for_binding).
# --------------------------------------------------------------------------

# Request-body path of the per-request context window (Ollama).
CONTEXT_WINDOW_REQUEST_OPTION: Tuple[str, str] = ("options", "num_ctx")
# Providers whose context window can be chosen per request.
_PER_REQUEST_CONTEXT_PROVIDERS = frozenset({"ollama"})


def configured_context_window(extra_body: Optional[Dict[str, Any]]) -> Optional[int]:
    """The per-request context window Kriya actually sends, if any."""
    section, key = CONTEXT_WINDOW_REQUEST_OPTION
    options = (extra_body or {}).get(section) if isinstance(extra_body, dict) else None
    value = options.get(key) if isinstance(options, dict) else None
    return value if isinstance(value, int) and value > 0 else None


def with_context_window(extra_body: Optional[Dict[str, Any]], tokens: int) -> Dict[str, Any]:
    """A copy of ``extra_body`` requesting a ``tokens`` context window."""
    section, key = CONTEXT_WINDOW_REQUEST_OPTION
    body = dict(extra_body or {})
    body[section] = {**dict(body.get(section) or {}), key: int(tokens)}
    return body


def without_context_window(extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A copy of ``extra_body`` without the context-window request field
    (an emptied section is dropped): the window is runtime identity, not an
    inference setting."""
    section, key = CONTEXT_WINDOW_REQUEST_OPTION
    body = dict(extra_body or {}) if isinstance(extra_body, dict) else {}
    options = body.get(section)
    if isinstance(options, dict):
        options = {k: v for k, v in options.items() if k != key}
        if options:
            body[section] = options
        else:
            body.pop(section)
    return body


def requested_context_window(extra_body: Optional[Dict[str, Any]], declared: Optional[int],
                             runtime: Any = None) -> Optional[int]:
    """The context window Kriya budgets a binding at and, where its runtime
    takes one per request, asks it to serve (FALLBACK-CONTEXT-WINDOW-001): an
    explicit provider option in its ``extra_body`` wins, deterministically;
    otherwise its declared, provider-neutral ``context_window``. The one
    definition every budget, fingerprint, qualification and request uses.
    ``runtime`` is the binding's adapter (default: the default runtime)."""
    explicit = _adapter(runtime).configured_context_window(extra_body)
    if explicit is not None:
        return explicit
    return int(declared) if isinstance(declared, int) and declared > 0 else None


def request_extra_body(extra_body: Optional[Dict[str, Any]], declared: Optional[int],
                       runtime: Any = None) -> Optional[Dict[str, Any]]:
    """``extra_body`` as sent: carrying the requested window in the
    runtime's own request representation (a copy when it has to be added;
    ``extra_body`` itself when it already carries it, nothing is declared, or
    the runtime takes no per-request window - its window is then provider
    managed). Config authors declare ``context_window`` once; the adapter
    translates it."""
    adapter = _adapter(runtime)
    window = requested_context_window(extra_body, declared, adapter)
    if window is None or adapter.configured_context_window(extra_body) == window:
        return extra_body
    return adapter.with_context_window(extra_body, window)


def _adapter(runtime: Any) -> Any:
    from kriya.core.inference_runtime import runtime_adapter

    return runtime if runtime is not None else runtime_adapter()


def context_window_overrides(config: Any) -> List[Dict[str, Any]]:
    """Bindings whose explicit provider option differs from an explicitly
    declared ``context_window``: the provider option is what is requested
    and budgeted (requested_context_window); this lists the ignored
    declaration so an operator sees it."""
    from kriya.core.inference_runtime import runtime_for_binding

    overrides = []
    for binding in _all_bindings(config):
        explicit = runtime_for_binding(binding).configured_context_window(getattr(binding, "extra_body", None))
        declared = getattr(binding, "context_window", None)
        if (explicit is not None and "context_window" in getattr(binding, "model_fields_set", ())
                and declared != explicit):
            overrides.append({"model": binding.model, "declared_context_window": declared,
                              "requested_context_window": explicit})
    return overrides


def supports_per_request_context_window(fingerprint: "ModelRuntimeFingerprint", runtime: Any = None) -> bool:
    """Whether this exact runtime takes its context window per request (so a
    PRD-016 context tier can be selected for one request)."""
    return _adapter(runtime).supports_per_request_context_window(fingerprint)


def kriya_protocol_identity(config: Any, model: str) -> str:
    """The Kriya-side protocol selected for this model: the resolved
    capability profile (which decides native tool calls, JSON mode, edit
    protocol and tool-argument limits) plus its provenance."""
    from kriya.core.model_capabilities import resolve_model_capability_profile

    profile = resolve_model_capability_profile(config, model)
    caps = profile.capabilities.model_dump() if hasattr(profile.capabilities, "model_dump") else dict(profile.capabilities)
    canonical = json.dumps({"capabilities": caps, "source": profile.source}, sort_keys=True, separators=(",", ":"))
    return "capabilities-sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _normalized_parameters(text: str) -> Tuple[str, ...]:
    """Ollama's ``parameters`` text, one "key value" pair per line, with
    whitespace collapsed and order normalized (repeated keys such as stop
    tokens stay, sorted)."""
    return tuple(sorted(" ".join(line.split()) for line in (text or "").splitlines() if line.strip()))


def _parameter_int(text: Any, name: str) -> Optional[int]:
    for line in (text or "").splitlines() if isinstance(text, str) else ():
        parts = line.split()
        if len(parts) == 2 and parts[0] == name and parts[1].isdigit():
            return int(parts[1])
    return None


def _modelfile_directives(modelfile: str) -> Dict[str, str]:
    directives: Dict[str, str] = {}
    for line in (modelfile or "").splitlines():
        head, _, rest = line.strip().partition(" ")
        if head in ("FROM", "RENDERER", "PARSER") and rest.strip() and head not in directives:
            directives[head] = rest.strip()
    return directives


def _weights_digest(from_value: str) -> str:
    base = os.path.basename(from_value or "")
    if base.startswith("sha256-") or base.startswith("sha256:"):
        return "sha256:" + base[len("sha256-"):]
    return UNAVAILABLE


def _context_length(model_info: Dict[str, Any]) -> Optional[int]:
    architecture = model_info.get("general.architecture")
    value = model_info.get(f"{architecture}.context_length") if architecture else None
    return value if isinstance(value, int) and value > 0 else None


def _tokenizer(model_info: Dict[str, Any]) -> Tuple[str, str]:
    """(identity, digest). The digest covers the full vocabulary, merges and
    token types (from verbose /api/show); without them it is unavailable."""
    model = model_info.get("tokenizer.ggml.model")
    pre = model_info.get("tokenizer.ggml.pre")
    identity = f"{model}/{pre}" if model and pre else (str(model) if model else UNAVAILABLE)
    tokens = model_info.get("tokenizer.ggml.tokens")
    if not isinstance(tokens, list) or not tokens:
        return identity, UNAVAILABLE
    material = {
        key: model_info.get(key)
        for key in sorted(model_info)
        if key.startswith("tokenizer.")
    }
    return identity, _sha256_json(material)


# --------------------------------------------------------------------------
# Probe transport: one JSON request against the configured endpoint.
# --------------------------------------------------------------------------

Transport = Callable[[str, Optional[Dict[str, Any]], str], Dict[str, Any]]


def _http_json(url: str, payload: Optional[Dict[str, Any]], api_key: str) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error.close()  # its response stream is released now, never left to the garbage collector
        raise ValueError(f"HTTP {error.code} from {url}") from None
    if not isinstance(decoded, dict):
        raise ValueError("endpoint response was not a JSON object")
    return decoded


def probing_enabled() -> bool:
    return os.environ.get(PROBE_ENV_VAR, "1").strip().lower() not in ("0", "false", "no", "off")


def probe_model_runtime(
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    egress_policy: str = "local_only",
    configured_context: Optional[int] = None,
    kriya_protocol: str = UNAVAILABLE,
    transport: Optional[Transport] = None,
) -> ModelRuntimeFingerprint:
    """Build the fingerprint from what the endpoint reports. Never raises."""
    from kriya.core.llm import is_local_url

    base = ModelRuntimeFingerprint(
        alias=(model or "").strip(),
        endpoint=endpoint_identity(base_url),
        configured_context_window=configured_context,
        # Served window: only what the runtime itself reports (below). A
        # requested window is not evidence that it is served.
        effective_context_window=None,
        kriya_protocol=kriya_protocol,
    )
    if transport is None:
        if not probing_enabled():
            return _replace(base, probe_errors=("probing disabled by KRIYA_MODEL_RUNTIME_PROBE",))
        transport = _http_json
    if not is_local_url(base_url):
        # Native metadata endpoints are only ever asked of a local server:
        # never send the API key (or any request) to a remote host for
        # identity, whatever the egress policy allows for completions.
        return _replace(base, probe_errors=("endpoint is not local; runtime metadata is never probed remotely",))

    errors = []
    parsed = urllib.parse.urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        return _replace(base, probe_errors=("endpoint exposes no native metadata API next to /v1",))
    root = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path[:-3], "", "")).rstrip("/")

    fields: Dict[str, Any] = {}
    try:
        version = transport(f"{root}/api/version", None, api_key).get("version")
        if isinstance(version, str) and version:
            fields["provider"] = "ollama"
            fields["provider_version"] = version
    except Exception as error:
        errors.append(f"/api/version: {error}")
        return _replace(base, probe_errors=tuple(errors))

    try:
        tags = transport(f"{root}/api/tags", None, api_key)
        wanted = {model.casefold(), f"{model}:latest".casefold()} if ":" not in model else {model.casefold()}
        entry = next(
            (
                item for item in tags.get("models", [])
                if isinstance(item, dict) and str(item.get("name") or item.get("model") or "").casefold() in wanted
            ),
            None,
        )
        if entry is None:
            errors.append(f"/api/tags: model {model!r} is not served by this endpoint")
        elif entry.get("digest"):
            fields["artifact_digest"] = "sha256:" + str(entry["digest"]).removeprefix("sha256:")
    except Exception as error:
        errors.append(f"/api/tags: {error}")

    try:
        shown = transport(f"{root}/api/show", {"model": model, "verbose": True}, api_key)
        details = shown.get("details") if isinstance(shown.get("details"), dict) else {}
        model_info = shown.get("model_info") if isinstance(shown.get("model_info"), dict) else {}
        directives = _modelfile_directives(shown.get("modelfile", ""))
        fields["weights_digest"] = _weights_digest(directives.get("FROM", ""))
        for key, name in (("format", "model_format"), ("family", "family"),
                          ("parameter_size", "parameter_size"), ("quantization_level", "quantization")):
            if details.get(key):
                fields[name] = str(details[key])
        template = shown.get("template")
        if directives.get("RENDERER"):
            fields["chat_template"] = f"renderer:{directives['RENDERER']}"
        elif isinstance(template, str) and template:
            fields["chat_template"] = "template-" + _sha256_json(template)
        if directives.get("PARSER"):
            fields["tool_call_parser"] = f"parser:{directives['PARSER']}"
        identity, tokenizer_digest = _tokenizer(model_info)
        fields["tokenizer_identity"] = identity
        fields["tokenizer_digest"] = tokenizer_digest
        if isinstance(shown.get("parameters"), str):
            fields["runtime_parameters_digest"] = _sha256_json(_normalized_parameters(shown["parameters"]))
            fields["server_parameters"] = _normalized_parameters(shown["parameters"])
        if isinstance(shown.get("capabilities"), list):
            fields["served_capabilities"] = tuple(sorted(str(c) for c in shown["capabilities"]))
        # PROVIDER-CONTRACT-001: the served window is the model's own
        # num_ctx PARAMETER (its server configuration). Kriya's requested
        # window is never evidence of what is served (the OpenAI-compatible
        # API ignores it); without the PARAMETER the server's default
        # applies, observable only once loaded (observe_served_context).
        context_length = _context_length(model_info)
        served = _parameter_int(shown.get("parameters"), "num_ctx")
        if context_length is not None:
            fields["model_context_length"] = context_length
        if served:
            fields["effective_context_window"] = min(served, context_length) if context_length else served
        else:
            fields["effective_context_window"] = None
    except Exception as error:
        errors.append(f"/api/show: {error}")

    return _replace(base, probe_errors=tuple(errors), **fields)


def _replace(fp: ModelRuntimeFingerprint, **changes: Any) -> ModelRuntimeFingerprint:
    from dataclasses import replace

    return replace(fp, **changes)


# --------------------------------------------------------------------------
# Per-process cache: one probe per distinct runtime input.
# --------------------------------------------------------------------------

_CACHE: Dict[Tuple[str, str, str, Optional[int], str], ModelRuntimeFingerprint] = {}
_CACHE_LOCK = threading.Lock()


def resolve_model_runtime(
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    egress_policy: str = "local_only",
    configured_context: Optional[int] = None,
    kriya_protocol: str = UNAVAILABLE,
    fresh: bool = False,
    config: Any = None,
    runtime: Any = None,
) -> ModelRuntimeFingerprint:
    """Cached probe through the binding's runtime adapter (``runtime``,
    default: the default runtime), keyed by the adapter and every input that
    enters the fingerprint. Every result is cached for the process,
    including a non-exact one (an unpulled chain model or an unprobeable
    server must not cost blocking probes on every call); ``fresh=True``
    (doctor, qualify, status) always re-probes."""
    adapter = _adapter(runtime)
    key = (adapter.name, endpoint_identity(base_url), (model or "").casefold(), configured_context, kriya_protocol)
    if not fresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached
    fingerprint = adapter.probe(
        base_url=base_url, model=model, api_key=api_key, egress_policy=egress_policy,
        configured_context=configured_context, kriya_protocol=kriya_protocol,
    )
    with _CACHE_LOCK:
        _CACHE[key] = fingerprint
    if fingerprint.exact:
        record_fingerprint(fingerprint, config)
    return fingerprint


def clear_model_runtime_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def resolve_configured_model_runtime(config: Any, model: Optional[str] = None, *, fresh: bool = False,
                                     base_url: Optional[str] = None, api_key: Optional[str] = None,
                                     extra_body: Optional[Dict[str, Any]] = None) -> ModelRuntimeFingerprint:
    """The fingerprint of ``model`` (default: the primary model) as this
    configuration would call it."""
    from kriya.core.inference_runtime import runtime_for_binding

    model = model or config.llm.model
    binding = _binding_for(config, model)
    if base_url is None or extra_body is None:
        base_url = base_url or binding.get("base_url") or config.llm.base_url
        api_key = api_key if api_key is not None else binding.get("api_key", config.llm.api_key)
        extra_body = extra_body if extra_body is not None else binding.get("extra_body", config.llm.extra_body)
    runtime = runtime_for_binding(binding)
    return resolve_model_runtime(
        base_url=base_url, model=model, api_key=api_key or "",
        egress_policy=config.autonomy.egress_policy,
        # The window this binding's requests carry (its declared window
        # unless extra_body overrides it; none for a runtime without a
        # per-request window, or the embedding model).
        configured_context=runtime.configured_context_window(
            request_extra_body(extra_body, binding.get("context_window"), runtime)),
        kriya_protocol=kriya_protocol_identity(config, model), fresh=fresh, config=config, runtime=runtime,
    )


def binding_object(config: Any, model: str) -> Any:
    """The config object that binds ``model`` (the primary llm, an llm_chain
    entry or an agent_llms role's llm/llm_chain entry; first exact,
    case-folded match), or None."""
    target = (model or "").casefold()
    if config.llm.model.casefold() == target:
        return config.llm
    candidates = list(config.llm_chain)
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        role_cfg = getattr(config.agent_llms, role, None)
        if role_cfg is None:
            continue
        if role_cfg.llm is not None:
            candidates.append(role_cfg.llm)
        candidates.extend(role_cfg.llm_chain)
    return next((candidate for candidate in candidates if candidate.model.casefold() == target), None)


def binding_output_tokens(config: Any, binding: Any = None) -> int:
    """PRD-017: the output budget (max_tokens) a call to ``binding`` asks for
    before any reasoning floor and before PRD-016's per-call budgeting: the
    binding's own value; the primary llm.max_tokens for the primary (None);
    the shared DEFAULT_OUTPUT_TOKENS for a binding that leaves it unset -
    never the primary's own override."""
    from kriya.config.config import DEFAULT_OUTPUT_TOKENS

    if binding is None:
        return int(config.llm.max_tokens)
    value = getattr(binding, "max_tokens", None)
    return int(value) if value is not None else DEFAULT_OUTPUT_TOKENS


def _all_bindings(config: Any) -> List[Any]:
    """Every configured model binding, in lookup order: the primary llm,
    llm_chain, then each agent_llms role's llm and llm_chain."""
    bindings = [config.llm, *config.llm_chain]
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        role_cfg = getattr(config.agent_llms, role, None)
        if role_cfg is None:
            continue
        if role_cfg.llm is not None:
            bindings.append(role_cfg.llm)
        bindings.extend(role_cfg.llm_chain)
    return bindings


def _binding_for(config: Any, model: str) -> Dict[str, Any]:
    """The first binding of ``model`` (exact, case-folded); empty for a
    model no chat binding names (the embedding model)."""
    target = (model or "").casefold()
    if config.llm.model.casefold() == target:
        return {"base_url": config.llm.base_url, "api_key": config.llm.api_key, "extra_body": config.llm.extra_body,
                "context_window": config.llm.context_window,
                "inference_runtime": getattr(config.llm, "inference_runtime", None)}
    for candidate in _all_bindings(config)[1:]:
        if candidate.model.casefold() == target:
            return {
                "base_url": candidate.base_url,
                "api_key": getattr(candidate, "api_key", config.llm.api_key),
                "extra_body": getattr(candidate, "extra_body", {}) or {},
                "context_window": getattr(candidate, "context_window", None),
                "inference_runtime": getattr(candidate, "inference_runtime", None),
            }
    return {}


# --------------------------------------------------------------------------
# Durable record: content-addressed, so a RunRecord's fingerprint ids and
# model-use telemetry resolve to the full component list later.
# --------------------------------------------------------------------------

def fingerprint_store_dir(config: Any = None) -> str:
    """``<state dir>/model_runtimes`` (KRIYA_STATE_DIR > paths.state > ~/.kriya/state)."""
    from kriya.core.state_paths import ENV_STATE_DIR, default_state_directory, resolve_state_directory

    if config is not None:
        state = resolve_state_directory(config)[0]
    else:
        state = os.path.realpath(os.environ.get(ENV_STATE_DIR) or default_state_directory())
    return os.path.join(state, "model_runtimes")


def record_fingerprint(fingerprint: ModelRuntimeFingerprint, config: Any = None) -> Optional[str]:
    """Write ``<state>/model_runtimes/<digest>.json`` once. Best effort."""
    try:
        directory = fingerprint_store_dir(config)
        path = os.path.join(directory, f"{fingerprint.digest}.json")
        if os.path.exists(path):
            return path
        os.makedirs(directory, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(fingerprint.to_dict(), stream, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return path
    except Exception as error:
        logger.warning("Could not persist model runtime fingerprint %s: %s", fingerprint.digest, error)
        return None


def load_recorded_fingerprint(digest: str, config: Any = None) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(fingerprint_store_dir(config), f"{digest}.json"), encoding="utf-8") as stream:
            return json.load(stream)
    except Exception:
        return None


# --------------------------------------------------------------------------
# PROVIDER-CONTRACT-001: Ollama's OpenAI-compatible /v1 contract (measured on
# Ollama 0.34.4, evidence/provider-contract-001/pre_fix). A binding's
# extra_body is written in Ollama's dialect (``options.<name>``, or a
# top-level field); this maps it to semantic settings, then to the exact
# wire body, and states what the provider applies for each.
# --------------------------------------------------------------------------

from kriya.core.provider_contract import (  # noqa: E402 - the provider-neutral contract
    Provenance,
    ProviderCapabilities,
    ProviderRequestPlan,
    SettingState,
    Support,
)

# /v1 applies top-level temperature, top_p, seed and reasoning_effort
# (measured); it ignores every ``options`` field and a top-level top_k.
# Everything else is the served model's own configuration (its Modelfile
# PARAMETERs), which Kriya observes and verifies but cannot set per request.
OPENAI_COMPAT_CAPABILITIES = ProviderCapabilities(
    settings={
        "temperature": Support.SUPPORTED, "top_p": Support.SUPPORTED, "seed": Support.SUPPORTED,
        "reasoning": Support.SUPPORTED,
        "context_window": Support.SERVER_CONFIG_ONLY, "top_k": Support.SERVER_CONFIG_ONLY,
        "min_p": Support.SERVER_CONFIG_ONLY, "repeat_penalty": Support.SERVER_CONFIG_ONLY,
        "presence_penalty": Support.SERVER_CONFIG_ONLY, "frequency_penalty": Support.SERVER_CONFIG_ONLY,
        "keep_alive": Support.UNSUPPORTED,
    },
    features={
        "stream_usage": Support.SUPPORTED, "prompt_usage": Support.SUPPORTED,
        "truncate_control": Support.UNSUPPORTED,
        "served_context_observation": Support.OBSERVABLE_ONLY,
        "server_parameter_observation": Support.OBSERVABLE_ONLY,
    },
)

# Ollama dialect name -> semantic setting.
_DIALECT_OPTION_SETTINGS = {
    "num_ctx": "context_window", "temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
    "min_p": "min_p", "repeat_penalty": "repeat_penalty", "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty", "seed": "seed",
}
_DIALECT_TOP_LEVEL_SETTINGS = {
    "top_p": "top_p", "top_k": "top_k", "min_p": "min_p", "seed": "seed", "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty", "frequency_penalty": "frequency_penalty", "keep_alive": "keep_alive",
}
# /v1 accepts ANY reasoning_effort string with HTTP 200 (measured: even
# "bogus"), so an unknown value would be silently ignored: Kriya refuses
# anything outside this vocabulary. Only "none" has a measured effect.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
# The model's own reasoning behaviour (no reasoning field sent).
REASONING_MODEL_DEFAULT = "model_default"
# Server PARAMETER name of each setting a request cannot carry.
_SERVER_PARAMETER_NAMES = {
    "context_window": "num_ctx", "top_k": "top_k", "min_p": "min_p", "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty", "frequency_penalty": "frequency_penalty", "top_p": "top_p",
    "temperature": "temperature", "seed": "seed",
}


def dialect_settings(extra_body: Optional[Dict[str, Any]], *, reasoning_flag: bool
                     ) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """(semantic settings, unknown request fields, conflicts) of a binding's
    ``extra_body`` in Ollama's dialect. ``reasoning_flag`` is the binding's
    ``reasoning``: with no explicit reasoning field, False means reasoning
    off ("none"), True the model's default."""
    settings: Dict[str, Any] = {}
    unknown: List[str] = []
    conflicts: List[str] = []

    def put(name: str, value: Any, where: str) -> None:
        if name in settings and settings[name] != value:
            conflicts.append(f"{name} is set to {settings[name]!r} and {value!r} ({where})")
        settings.setdefault(name, value)

    body = extra_body if isinstance(extra_body, dict) else {}
    for key, value in body.items():
        if key == "options" and isinstance(value, dict):
            for option, option_value in value.items():
                if option in _DIALECT_OPTION_SETTINGS:
                    put(_DIALECT_OPTION_SETTINGS[option], option_value, f"options.{option}")
                elif option == "think":
                    put("reasoning", REASONING_MODEL_DEFAULT if option_value else "none", "options.think")
                else:
                    unknown.append(f"options.{option}")
        elif key in _DIALECT_TOP_LEVEL_SETTINGS:
            put(_DIALECT_TOP_LEVEL_SETTINGS[key], value, key)
        elif key == "reasoning_effort":
            if value not in REASONING_EFFORTS:
                conflicts.append(f"reasoning_effort {value!r} is not one of {REASONING_EFFORTS}")
            put("reasoning", value, "reasoning_effort")
        elif key == "think":
            put("reasoning", REASONING_MODEL_DEFAULT if value else "none", "think")
        else:
            unknown.append(key)
    settings.setdefault("reasoning", REASONING_MODEL_DEFAULT if reasoning_flag else "none")
    return settings, unknown, conflicts


def server_parameters(fingerprint: Any) -> Optional[Dict[str, str]]:
    """The served model's single-valued PARAMETERs, or None when the probe
    could not read them (unverifiable)."""
    pairs = getattr(fingerprint, "server_parameters", None)
    if not pairs and getattr(fingerprint, "runtime_parameters_digest", UNAVAILABLE) == UNAVAILABLE:
        return None
    parameters: Dict[str, str] = {}
    for pair in pairs or ():
        name, _, value = pair.partition(" ")
        parameters.setdefault(name, value.strip())
    return parameters


def _numeric(value: Any) -> Any:
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        return int(number) if number.is_integer() else number
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def openai_compat_request_plan(extra_body: Optional[Dict[str, Any]], *, temperature: Optional[float],
                               reasoning_flag: bool, requested_context_window: Optional[int],
                               fingerprint: Any = None) -> ProviderRequestPlan:
    """The exact /v1 wire body (sent as the SDK's extra_body, i.e. top-level
    JSON fields) and each setting's requested/effective state."""
    settings, unknown, conflicts = dialect_settings(extra_body, reasoning_flag=reasoning_flag)
    if temperature is not None:
        settings["temperature"] = temperature
    if requested_context_window is not None:
        settings["context_window"] = requested_context_window
    served = server_parameters(fingerprint) if fingerprint is not None else None
    wire: Dict[str, Any] = {}
    states: List[SettingState] = []
    for name in sorted(set(settings) | {"top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty"}):
        requested = settings.get(name)
        support = OPENAI_COMPAT_CAPABILITIES.setting(name)
        server_value = (_numeric(served.get(_SERVER_PARAMETER_NAMES[name]))
                        if served is not None and name in _SERVER_PARAMETER_NAMES
                        and _SERVER_PARAMETER_NAMES[name] in served else None)
        if name == "reasoning":
            if requested != REASONING_MODEL_DEFAULT:
                wire["reasoning_effort"] = requested
            states.append(SettingState(name, requested, requested, Provenance.REQUEST, support))
        elif support is Support.SUPPORTED and requested is not None:
            if name != "temperature":  # the SDK's own parameter
                wire[name] = requested
            states.append(SettingState(name, _numeric(requested), _numeric(requested), Provenance.REQUEST, support))
        elif served is None:
            states.append(SettingState(name, _numeric(requested), None, Provenance.UNVERIFIED, support))
        else:
            states.append(SettingState(name, _numeric(requested), server_value,
                                       Provenance.SERVER_MODEL_CONFIG if server_value is not None
                                       else Provenance.UNVERIFIED, support))
    for field_name in unknown:
        if "." not in field_name:
            wire[field_name] = extra_body[field_name]
    return ProviderRequestPlan(wire_body=wire, settings=tuple(states), unknown=tuple(unknown),
                               conflicts=tuple(conflicts))


# PROVIDER-CONTRACT-001A: what one served-context observation established.
# A failure is a fact about that attempt, never about the provider: nothing
# is cached, so the next call observes again.
OBSERVED = "observed"
NOT_LOADED = "not_loaded"  # the runtime is up; the model is not loaded right now
LOADED_WITHOUT_WINDOW = "loaded_without_window"  # loaded, but no usable context length reported
OBSERVATION_FAILED = "observation_failed"  # unreachable, timed out or malformed (after one retry)
NOT_APPLICABLE = "not_applicable"  # probing disabled, non-local or no native root: never observed

# One bounded retry for a transient failure; never a loop.
SERVED_CONTEXT_OBSERVATION_ATTEMPTS = 2


@dataclass(frozen=True)
class ServedContextObservation:
    status: str
    window: Optional[int] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "window": self.window, "reason": self.reason}


def observe_served_context_state(*, base_url: str, model: str, api_key: str = "",
                                 transport: Optional[Transport] = None) -> ServedContextObservation:
    """The context window the server has loaded for ``model`` (Ollama
    /api/ps ``context_length``) as a typed observation. A failed probe is
    retried once; a failure is reported, never remembered. Never raises."""
    from kriya.core.llm import is_local_url

    if transport is None:
        if not probing_enabled():
            return ServedContextObservation(NOT_APPLICABLE, reason="probing_disabled")
        transport = _http_json
    if not is_local_url(base_url):
        return ServedContextObservation(NOT_APPLICABLE, reason="non_local_endpoint")
    # The /v1 and native adapters share one server: /api/ps sits at its root.
    root = _native_root(base_url)
    failure = None
    for _attempt in range(SERVED_CONTEXT_OBSERVATION_ATTEMPTS):
        try:
            response = transport(f"{root}/api/ps", None, api_key)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"[:300]
            continue
        loaded = response.get("models") if isinstance(response, dict) else None
        if not isinstance(loaded, list):
            failure = "malformed /api/ps response (no models list)"
            continue
        return _served_context_from(loaded, model)
    logger.warning("Served-context observation failed at %s for %s: %s", base_url, model, failure)
    return ServedContextObservation(OBSERVATION_FAILED, reason=failure)


def _served_context_from(loaded: List[Any], model: str) -> ServedContextObservation:
    wanted = {model.casefold(), f"{model}:latest".casefold()} if ":" not in model else {model.casefold()}
    for item in loaded:
        name = str(item.get("name") or item.get("model") or "").casefold() if isinstance(item, dict) else ""
        if name not in wanted:
            continue
        value = item.get("context_length")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return ServedContextObservation(OBSERVED, window=value)
        return ServedContextObservation(LOADED_WITHOUT_WINDOW, reason="no usable context_length for the model")
    return ServedContextObservation(NOT_LOADED)


def observe_served_context(*, base_url: str, model: str, api_key: str = "",
                           transport: Optional[Transport] = None) -> Optional[int]:
    """The observed served window, or None (see observe_served_context_state)."""
    return observe_served_context_state(base_url=base_url, model=model, api_key=api_key,
                                        transport=transport).window


def _native_root(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    path = parsed.path.rstrip("/")
    path = path[:-3] if path.endswith("/v1") else path
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def server_pin_parameters(extra_body: Optional[Dict[str, Any]], *,
                          requested_context_window: Optional[int]) -> Dict[str, Any]:
    """The server PARAMETERs that fix a binding's server-only settings: only
    values the binding itself sets (never a default Kriya invents). A
    conflicting binding pins nothing (PROVIDER_SETTING_CONFLICT)."""
    from kriya.core.provider_contract import PROVIDER_SETTING_CONFLICT, ProviderContractError

    settings, _unknown, conflicts = dialect_settings(extra_body, reasoning_flag=True)
    if conflicts:
        raise ProviderContractError(PROVIDER_SETTING_CONFLICT, "; ".join(conflicts), {"conflicts": conflicts})
    parameters: Dict[str, Any] = {}
    if requested_context_window is not None:
        parameters["num_ctx"] = int(requested_context_window)
    for name, value in sorted(settings.items()):
        if (name != "context_window" and value is not None
                and OPENAI_COMPAT_CAPABILITIES.setting(name) is Support.SERVER_CONFIG_ONLY):
            parameters[_SERVER_PARAMETER_NAMES[name]] = _numeric(value)
    return parameters


def pinned_model_name(model: str, parameters: Dict[str, Any]) -> str:
    """A derived model name bound to the base model and the exact pinned
    parameters: another parameter set is another name, never an overwrite."""
    base, _, tag = model.partition(":")
    digest = _sha256_json({"from": model, "parameters": parameters}).partition(":")[2][:12]
    return f"{base}:{tag or 'latest'}-kriya-{digest}"


def pin_served_configuration(*, base_url: str, model: str, extra_body: Optional[Dict[str, Any]],
                             requested_context_window: Optional[int], api_key: str = "", create: bool = True,
                             transport: Optional[Transport] = None) -> Dict[str, Any]:
    """Create (``create``) the derived Ollama model serving ``model`` with the
    binding's server-only settings as PARAMETERs (/api/create ``from`` +
    ``parameters``). Local endpoints only."""
    from kriya.core.llm import is_local_url
    from kriya.core.provider_contract import PROVIDER_SETTING_UNSUPPORTED, ProviderContractError

    parameters = server_pin_parameters(extra_body, requested_context_window=requested_context_window)
    if not parameters:
        raise ProviderContractError(PROVIDER_SETTING_UNSUPPORTED,
                                    f"{model}: the binding sets no server-only setting to pin")
    name = pinned_model_name(model, parameters)
    pin = {"model": name, "base_model": model, "parameters": parameters, "created": False}
    if create:
        if not is_local_url(base_url):
            raise ProviderContractError(PROVIDER_SETTING_UNSUPPORTED,
                                        f"{base_url} is not a local endpoint; a served model is pinned only locally")
        (transport or _http_json)(f"{_native_root(base_url)}/api/create",
                                  {"model": name, "from": model, "parameters": parameters, "stream": False}, api_key)
        pin["created"] = True
    return pin


class OllamaRuntimeAdapter(OpenAICompatibleTransport):
    """The packaged default runtime (INF-001): its OpenAI-compatible chat API
    (the transport), its native identity endpoints (probe_model_runtime) and
    its per-request context window option (CONTEXT_WINDOW_REQUEST_OPTION).
    The module functions are looked up at call time, so their existing test
    doubles still apply."""

    name = "ollama"
    # PROVIDER-CONTRACT-001: /v1 takes no per-request context window (it
    # ignores options.num_ctx); the window is the served model's own.
    capabilities = RuntimeCapabilities(per_request_context_window=False, native_identity_probe=True, stream_usage=True)
    provider_capabilities = OPENAI_COMPAT_CAPABILITIES

    def request_plan(self, extra_body: Optional[Dict[str, Any]], *, temperature: Optional[float],
                     reasoning_flag: bool, requested_context_window: Optional[int],
                     fingerprint: Any = None) -> ProviderRequestPlan:
        return openai_compat_request_plan(
            extra_body, temperature=temperature, reasoning_flag=reasoning_flag,
            requested_context_window=requested_context_window, fingerprint=fingerprint)

    def observe_served_context_state(self, *, base_url: str, model: str,
                                     api_key: str = "") -> ServedContextObservation:
        return observe_served_context_state(base_url=base_url, model=model, api_key=api_key)

    def pin_served_configuration(self, *, base_url: str, model: str, extra_body: Optional[Dict[str, Any]],
                                 requested_context_window: Optional[int], api_key: str = "",
                                 create: bool = True) -> Dict[str, Any]:
        return pin_served_configuration(base_url=base_url, model=model, extra_body=extra_body,
                                        requested_context_window=requested_context_window, api_key=api_key,
                                        create=create)

    def configured_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Optional[int]:
        return configured_context_window(extra_body)

    def with_context_window(self, extra_body: Optional[Dict[str, Any]], tokens: int) -> Dict[str, Any]:
        return with_context_window(extra_body, tokens)

    def without_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return without_context_window(extra_body)

    def supports_per_request_context_window(self, fingerprint: Any) -> bool:
        # PROVIDER-CONTRACT-001: /v1 ignores a per-request window, so no
        # PRD-016 tier can be selected through it (the native adapter can).
        return (self.capabilities.per_request_context_window and bool(fingerprint.exact)
                and fingerprint.provider in _PER_REQUEST_CONTEXT_PROVIDERS)

    def probe(self, *, base_url: str, model: str, api_key: str, egress_policy: str,
              configured_context: Optional[int], kriya_protocol: str,
              transport: Optional[Callable[..., Any]] = None) -> "ModelRuntimeFingerprint":
        return probe_model_runtime(
            base_url=base_url, model=model, api_key=api_key, egress_policy=egress_policy,
            configured_context=configured_context, kriya_protocol=kriya_protocol, transport=transport,
        )


register_runtime_adapter(OllamaRuntimeAdapter(), default=True)


# --------------------------------------------------------------------------
# PROVIDER-CONTRACT-001 Batch C: Ollama's native chat API (/api/chat).
# Registered as "ollama_native"; never the default until qualified. Every
# sampling setting and the context window travel per request in
# ``options`` (measured: honoured natively), reasoning as ``think`` and the
# model's residency as ``keep_alive``; every request sends
# ``truncate: false`` (measured: the native default silently truncates an
# over-window prompt, false answers HTTP 400 instead).
# --------------------------------------------------------------------------

# /2 (PROVIDER-CONTRACT-001A): an over-context refusal carries the
# provider's exact prompt count, and an answer carrying tool calls reports
# finish_reason "tool_calls" (Ollama's done_reason says "stop"; measured).
NATIVE_PROTOCOL_ADAPTER_VERSION = "kriya-ollama-native/2"
NATIVE_RUNTIME_NAME = "ollama_native"

OLLAMA_NATIVE_CAPABILITIES = ProviderCapabilities(
    settings={
        "context_window": Support.SUPPORTED, "temperature": Support.SUPPORTED, "top_p": Support.SUPPORTED,
        "top_k": Support.SUPPORTED, "min_p": Support.SUPPORTED, "repeat_penalty": Support.SUPPORTED,
        "presence_penalty": Support.SUPPORTED, "frequency_penalty": Support.SUPPORTED, "seed": Support.SUPPORTED,
        "reasoning": Support.SUPPORTED, "keep_alive": Support.SUPPORTED,
    },
    features={
        "stream_usage": Support.SUPPORTED, "prompt_usage": Support.SUPPORTED,
        "truncate_control": Support.SUPPORTED,
        "served_context_observation": Support.OBSERVABLE_ONLY,
        "server_parameter_observation": Support.OBSERVABLE_ONLY,
    },
)
# Semantic setting -> native ``options`` key.
_NATIVE_OPTION_NAMES = {
    "context_window": "num_ctx", "temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
    "min_p": "min_p", "repeat_penalty": "repeat_penalty", "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty", "seed": "seed",
}
# Reasoning effort -> native ``think`` (None: not sent, the model's default).
_NATIVE_THINK = {"none": False, "low": "low", "medium": "medium", "high": "high", REASONING_MODEL_DEFAULT: None}
# An over-window prompt refused under truncate:false (measured error text).
_CONTEXT_EXCEEDED_MARKERS = ("exceed_context_size", "exceeds the context", "context length")


def _context_exceeded_counts(message: str) -> Dict[str, int]:
    """The provider's exact prompt token count and context size from an
    over-context refusal (Ollama 0.34.4 nests a JSON error object carrying
    ``n_prompt_tokens`` and ``n_ctx`` inside the error string, measured);
    empty when the answer carries none."""
    counts: Dict[str, int] = {}
    for key, name in (("n_prompt_tokens", "provider_prompt_tokens"), ("n_ctx", "provider_context_window")):
        match = re.search(rf'"{key}"\s*:\s*(\d+)', message)
        if match:
            counts[name] = int(match.group(1))
    return counts


class NativeRuntimeError(RuntimeError):
    """An HTTP error answer of the native API."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def native_request_plan(extra_body: Optional[Dict[str, Any]], *, temperature: Optional[float],
                        reasoning_flag: bool, requested_context_window: Optional[int],
                        fingerprint: Any = None) -> ProviderRequestPlan:
    """The exact native body fields (``options``, ``think``, ``keep_alive``,
    ``truncate``) and each setting's state. Everything requested is carried
    by the request (REQUEST); settings not requested are the served model's
    own (SERVER_MODEL_CONFIG, when readable)."""
    settings, unknown, conflicts = dialect_settings(extra_body, reasoning_flag=reasoning_flag)
    if temperature is not None:
        settings["temperature"] = temperature
    if requested_context_window is not None:
        settings["context_window"] = requested_context_window
    served = server_parameters(fingerprint) if fingerprint is not None else None
    options: Dict[str, Any] = {}
    wire: Dict[str, Any] = {"truncate": False}
    states: List[SettingState] = []
    for name in sorted(set(settings) | {"top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty"}):
        requested = settings.get(name)
        support = OLLAMA_NATIVE_CAPABILITIES.setting(name)
        if name == "reasoning":
            if requested not in _NATIVE_THINK:
                conflicts.append(f"reasoning_effort {requested!r} has no native think equivalent")
            elif _NATIVE_THINK[requested] is not None:
                wire["think"] = _NATIVE_THINK[requested]
            states.append(SettingState(name, requested, requested, Provenance.REQUEST, support))
        elif name == "keep_alive":
            wire["keep_alive"] = requested
            states.append(SettingState(name, requested, requested, Provenance.REQUEST, support))
        elif requested is not None:
            if name != "temperature":  # carried from the request's own temperature
                options[_NATIVE_OPTION_NAMES[name]] = _numeric(requested)
            states.append(SettingState(name, _numeric(requested), _numeric(requested), Provenance.REQUEST, support))
        else:
            server_value = (_numeric(served.get(_SERVER_PARAMETER_NAMES[name]))
                            if served is not None and _SERVER_PARAMETER_NAMES.get(name) in served else None)
            states.append(SettingState(name, None, server_value,
                                       Provenance.SERVER_MODEL_CONFIG if server_value is not None
                                       else Provenance.UNVERIFIED, support))
    if options:
        wire["options"] = options
    for field_name in unknown:
        if "." not in field_name:
            wire[field_name] = extra_body[field_name]
    return ProviderRequestPlan(wire_body=wire, settings=tuple(states), unknown=tuple(unknown),
                               conflicts=tuple(conflicts))


def _native_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """OpenAI-shaped messages as the native API takes them (a tool call's
    arguments are an object, not a JSON string)."""
    out = []
    for message in messages:
        converted = {k: v for k, v in message.items() if k in ("role", "content", "tool_name", "images")}
        converted.setdefault("content", message.get("content") or "")
        calls = []
        for call in message.get("tool_calls") or []:
            function = (call or {}).get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"_raw": arguments}
            calls.append({"function": {"name": function.get("name"), "arguments": arguments or {}}})
        if calls:
            converted["tool_calls"] = calls
        out.append(converted)
    return out


class OllamaNativeRuntimeAdapter(InferenceRuntimePort):
    """Ollama's native /api/chat (PROVIDER-CONTRACT-001 Batch C). Uses the
    same Kriya-owned HTTP transport as every other call (the client
    LLMClient built: direct, no retry, Kriya timeouts); one request per
    call, streamed as NDJSON when asked."""

    name = NATIVE_RUNTIME_NAME
    capabilities = RuntimeCapabilities(per_request_context_window=True, native_identity_probe=True, stream_usage=True)
    provider_capabilities = OLLAMA_NATIVE_CAPABILITIES

    def request_plan(self, extra_body: Optional[Dict[str, Any]], *, temperature: Optional[float],
                     reasoning_flag: bool, requested_context_window: Optional[int],
                     fingerprint: Any = None) -> ProviderRequestPlan:
        return native_request_plan(extra_body, temperature=temperature, reasoning_flag=reasoning_flag,
                                   requested_context_window=requested_context_window, fingerprint=fingerprint)

    def observe_served_context_state(self, *, base_url: str, model: str,
                                     api_key: str = "") -> ServedContextObservation:
        return observe_served_context_state(base_url=base_url, model=model, api_key=api_key)

    def pin_served_configuration(self, *, base_url: str, model: str, extra_body: Optional[Dict[str, Any]],
                                 requested_context_window: Optional[int], api_key: str = "",
                                 create: bool = True) -> Dict[str, Any]:
        from kriya.core.provider_contract import PROVIDER_SETTING_UNSUPPORTED, ProviderContractError

        raise ProviderContractError(PROVIDER_SETTING_UNSUPPORTED,
                                    "the native API carries every setting per request; nothing to pin")

    def configured_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Optional[int]:
        return configured_context_window(extra_body)

    def with_context_window(self, extra_body: Optional[Dict[str, Any]], tokens: int) -> Dict[str, Any]:
        return with_context_window(extra_body, tokens)

    def without_context_window(self, extra_body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return without_context_window(extra_body)

    def supports_per_request_context_window(self, fingerprint: Any) -> bool:
        return bool(fingerprint.exact) and fingerprint.provider in _PER_REQUEST_CONTEXT_PROVIDERS

    def probe(self, *, base_url: str, model: str, api_key: str, egress_policy: str,
              configured_context: Optional[int], kriya_protocol: str,
              transport: Optional[Callable[..., Any]] = None) -> "ModelRuntimeFingerprint":
        fingerprint = probe_model_runtime(
            base_url=base_url, model=model, api_key=api_key, egress_policy=egress_policy,
            configured_context=configured_context, kriya_protocol=kriya_protocol, transport=transport,
        )
        # Its own protocol identity (never sharing evidence with /v1), and the
        # window every request carries is the window served (per request).
        return _replace(fingerprint, adapter_version=NATIVE_PROTOCOL_ADAPTER_VERSION,
                        effective_context_window=configured_context or fingerprint.effective_context_window)

    # -- transport ----------------------------------------------------------
    @staticmethod
    def _http(client: Any) -> Any:
        http = getattr(client, "_client", None)  # the httpx client LLMClient gave the SDK client
        if http is None:
            raise TypeError("the native adapter needs LLMClient's HTTP transport")
        return http

    @staticmethod
    def _payload(request: ChatRequest, *, stream: bool) -> Dict[str, Any]:
        body = dict(request.extra_body or {})
        options = dict(body.pop("options", None) or {})
        if request.temperature is not None:
            options["temperature"] = request.temperature
        options["num_predict"] = int(request.max_tokens)
        payload: Dict[str, Any] = {"model": request.model, "messages": _native_messages(request.messages),
                                   "stream": stream, "options": options, **body}
        # Never silent truncation, whatever the body asked (an unknown
        # "truncate" field included): the provider refuses instead.
        payload["truncate"] = False
        if request.response_format and request.response_format.get("type") == "json_object":
            payload["format"] = "json"
        if request.tools:
            payload["tools"] = request.tools
        return payload

    @staticmethod
    def _url(client: Any) -> str:
        return f"{_native_root(str(client.base_url))}/api/chat"

    @staticmethod
    def _headers(client: Any) -> Dict[str, str]:
        key = getattr(client, "api_key", "") or ""
        return {"Authorization": f"Bearer {key}"} if key else {}

    @staticmethod
    def _raise_for(status: int, text: str) -> None:
        from kriya.core.provider_contract import PROVIDER_PROMPT_TRUNCATED, ProviderContractError

        try:
            message = str(json.loads(text).get("error") or text)
        except (ValueError, AttributeError):
            message = text
        if status == 400 and any(marker in message for marker in _CONTEXT_EXCEEDED_MARKERS):
            # truncate:false made the provider refuse instead of dropping input;
            # its exact prompt count and window travel with the refusal.
            raise ProviderContractError(PROVIDER_PROMPT_TRUNCATED,
                                        f"the prompt exceeds the served context window: {message[:300]}",
                                        {"provider_status": status, **_context_exceeded_counts(message)})
        raise NativeRuntimeError(status, message[:500])

    @staticmethod
    def _read(out: ChatResponse, chunk: Dict[str, Any]) -> None:
        if chunk.get("done"):
            out.prompt_tokens = _int(chunk.get("prompt_eval_count"))
            out.completion_tokens = _int(chunk.get("eval_count"))
            reason = chunk.get("done_reason")
            out.finish_reason = reason if isinstance(reason, str) and reason else None
        model = chunk.get("model")
        if isinstance(model, str) and model and "model" not in out.provider_metadata:
            out.provider_metadata["model"] = model

    async def complete(self, client: Any, request: ChatRequest) -> ChatResponse:
        http = self._http(client)
        timeout = request.timeout if request.timeout is not None else http.timeout
        out = ChatResponse(content="")
        if request.stream_callback is None:
            response = await http.post(self._url(client), json=self._payload(request, stream=False),
                                       headers=self._headers(client), timeout=timeout)
            if response.status_code >= 400:
                self._raise_for(response.status_code, response.text)
            data = response.json()
            message = data.get("message") or {}
            out.content = (message.get("content") or "").strip()
            out.reasoning_chars = len(message.get("thinking") or "")
            self._read(out, {**data, "done": True})
            return out
        # Exactly one request, streamed as NDJSON.
        chunks: List[str] = []
        async with http.stream("POST", self._url(client), json=self._payload(request, stream=True),
                               headers=self._headers(client), timeout=timeout) as response:
            if response.status_code >= 400:
                self._raise_for(response.status_code, (await response.aread()).decode("utf-8", "replace"))
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    self._raise_for(500, str(chunk["error"]))
                message = chunk.get("message") or {}
                out.reasoning_chars += len(message.get("thinking") or "")
                piece = message.get("content") or ""
                if piece:
                    chunks.append(piece)
                    request.stream_callback(piece)
                self._read(out, chunk)
        out.content = "".join(chunks).strip()
        return out

    async def complete_with_tools(self, client: Any, request: ChatRequest) -> ChatResponse:
        http = self._http(client)
        timeout = request.timeout if request.timeout is not None else http.timeout
        response = await http.post(self._url(client), json=self._payload(request, stream=False),
                                   headers=self._headers(client), timeout=timeout)
        if response.status_code >= 400:
            self._raise_for(response.status_code, response.text)
        data = response.json()
        message = data.get("message") or {}
        out = ChatResponse(content=message.get("content") or "", reasoning_chars=len(message.get("thinking") or ""))
        for index, call in enumerate(message.get("tool_calls") or []):
            function = (call or {}).get("function") or {}
            arguments = function.get("arguments")
            out.tool_calls.append(RawToolCall(
                str(call.get("id") or f"call_{index}"), str(function.get("name") or ""),
                arguments if isinstance(arguments, str) else json.dumps(arguments or {})))
        self._read(out, {**data, "done": True})
        if out.tool_calls and out.finish_reason == "stop":
            out.finish_reason = "tool_calls"  # the /v1 normalized meaning
        return out

    def classify_error(self, error: BaseException) -> RuntimeErrorKind:
        import asyncio

        import httpx

        from kriya.core.provider_contract import ProviderContractError

        if isinstance(error, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
            return RuntimeErrorKind.TIMEOUT
        if isinstance(error, httpx.TransportError):
            return RuntimeErrorKind.CONNECTION
        if isinstance(error, ProviderContractError):
            return RuntimeErrorKind.SERVER  # never retried with a changed request
        if isinstance(error, NativeRuntimeError):
            if error.status in (401, 403):
                return RuntimeErrorKind.AUTHENTICATION
            if error.status == 429:
                return RuntimeErrorKind.RATE_LIMITED
            if error.status >= 500:
                return RuntimeErrorKind.SERVER
        return RuntimeErrorKind.REQUEST

    def list_models(self, base_url: str, api_key: str,
                    fetch_json: Callable[..., Dict[str, Any]]) -> List[Dict[str, Any]]:
        listing = fetch_json(f"{_native_root(base_url)}/api/tags", api_key=api_key)
        return [{"id": item.get("name") or item.get("model")} for item in listing.get("models", [])
                if isinstance(item, dict)]


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


register_runtime_adapter(OllamaNativeRuntimeAdapter())


__all__ = [
    "EXACT_REQUIRED_COMPONENTS", "FINGERPRINT_SCHEMA_VERSION", "MODEL_PROTOCOL_ADAPTER_VERSION",
    "ModelRuntimeFingerprint", "PROBE_ENV_VAR", "UNAVAILABLE", "clear_model_runtime_cache",
    "CONTEXT_WINDOW_REQUEST_OPTION", "configured_context_window", "context_window_overrides", "endpoint_identity",
    "OllamaRuntimeAdapter", "fingerprint_store_dir", "kriya_protocol_identity", "request_extra_body",
    "requested_context_window",
    "supports_per_request_context_window", "with_context_window", "without_context_window",
    "load_recorded_fingerprint", "probe_model_runtime", "probing_enabled", "record_fingerprint",
    "resolve_configured_model_runtime", "resolve_model_runtime",
]
