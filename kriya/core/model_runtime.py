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
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"

# Bumped whenever Kriya's own request/response handling for a model changes
# in a way that could change qualification results (message shape, JSON
# mode, reasoning stripping, tool-call normalization, truncation handling).
MODEL_PROTOCOL_ADAPTER_VERSION = "kriya-openai-compat/2"

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
# Runtime adapter contract (INF-001 seam). Everything Kriya knows about HOW a
# served runtime takes its per-request context window lives here, so generic
# qualification, budgeting, routing and evidence code never names a provider.
# Today's only adapter is Ollama (the ``options.num_ctx`` request option); a
# future vLLM/other adapter (INF-001) replaces these functions, not callers.
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


def supports_per_request_context_window(fingerprint: "ModelRuntimeFingerprint") -> bool:
    """Whether this exact runtime takes its context window per request (so a
    PRD-016 context tier can be selected for one request)."""
    return bool(fingerprint.exact) and fingerprint.provider in _PER_REQUEST_CONTEXT_PROVIDERS


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
    with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as response:
        decoded = json.loads(response.read().decode("utf-8"))
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
        effective_context_window=configured_context,
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
        if isinstance(shown.get("capabilities"), list):
            fields["served_capabilities"] = tuple(sorted(str(c) for c in shown["capabilities"]))
        # The served window is num_ctx: Kriya's own request value, else a
        # Modelfile PARAMETER. With neither, the server's default applies
        # and is not reported, so the effective window stays unknown rather
        # than being mistaken for the model's trained maximum.
        context_length = _context_length(model_info)
        served = configured_context or _parameter_int(shown.get("parameters"), "num_ctx")
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

_CACHE: Dict[Tuple[str, str, Optional[int], str], ModelRuntimeFingerprint] = {}
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
) -> ModelRuntimeFingerprint:
    """Cached probe, keyed by every input that enters the fingerprint. Every
    result is cached for the process, including a non-exact one (an unpulled
    chain model or a non-Ollama server must not cost blocking probes on every
    call); ``fresh=True`` (doctor, qualify, status) always re-probes."""
    key = (endpoint_identity(base_url), (model or "").casefold(), configured_context, kriya_protocol)
    if not fresh:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached is not None:
            return cached
    fingerprint = probe_model_runtime(
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
    model = model or config.llm.model
    if base_url is None or extra_body is None:
        binding = _binding_for(config, model)
        base_url = base_url or binding.get("base_url") or config.llm.base_url
        api_key = api_key if api_key is not None else binding.get("api_key", config.llm.api_key)
        extra_body = extra_body if extra_body is not None else binding.get("extra_body", config.llm.extra_body)
    return resolve_model_runtime(
        base_url=base_url, model=model, api_key=api_key or "",
        egress_policy=config.autonomy.egress_policy,
        configured_context=configured_context_window(extra_body),
        kriya_protocol=kriya_protocol_identity(config, model), fresh=fresh, config=config,
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


def _binding_for(config: Any, model: str) -> Dict[str, Any]:
    target = (model or "").casefold()
    if config.llm.model.casefold() == target:
        return {"base_url": config.llm.base_url, "api_key": config.llm.api_key, "extra_body": config.llm.extra_body}
    candidates = list(config.llm_chain)
    for role in ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance"):
        role_cfg = getattr(config.agent_llms, role, None)
        if role_cfg is None:
            continue
        if role_cfg.llm is not None:
            candidates.append(role_cfg.llm)
        candidates.extend(role_cfg.llm_chain)
    for candidate in candidates:
        if candidate.model.casefold() == target:
            return {
                "base_url": candidate.base_url,
                "api_key": getattr(candidate, "api_key", config.llm.api_key),
                "extra_body": getattr(candidate, "extra_body", {}) or {},
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


__all__ = [
    "EXACT_REQUIRED_COMPONENTS", "FINGERPRINT_SCHEMA_VERSION", "MODEL_PROTOCOL_ADAPTER_VERSION",
    "ModelRuntimeFingerprint", "PROBE_ENV_VAR", "UNAVAILABLE", "clear_model_runtime_cache",
    "CONTEXT_WINDOW_REQUEST_OPTION", "configured_context_window", "endpoint_identity", "fingerprint_store_dir",
    "kriya_protocol_identity", "supports_per_request_context_window", "with_context_window",
    "without_context_window",
    "load_recorded_fingerprint", "probe_model_runtime", "probing_enabled", "record_fingerprint",
    "resolve_configured_model_runtime", "resolve_model_runtime",
]
