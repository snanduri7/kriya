# PROVIDER-CONTRACT-001: Kriya ↔ provider inference contract

Status: IN PROGRESS. Batch A has started; no Graphify run during this work. Pre-fix evidence: `evidence/provider-contract-001/pre_fix/` (CLASSIFICATION.md).

## Invariant

Any model or runtime setting that Kriya records, budgets against, qualifies or treats as authoritative must be proven to reach the provider with the intended semantics, or Kriya must fail closed. Desired configuration is not effective inference identity.

## Measured provider facts (Ollama 0.34.4, 2026-09-30)

- `/v1` ignores `options.*` completely (`num_ctx`, `top_k`, `top_p`, `think`). It also ignores top-level `top_k`.
- `/v1` honours top-level `temperature`, `top_p`, `max_tokens`, `reasoning_effort` (`"none"` disables thinking and is accepted by non-thinking models), `stream_options.include_usage` and `seed`.
- Served context comes from the model's Modelfile `PARAMETER num_ctx`. That overrides the server default (`OLLAMA_CONTEXT_LENGTH`), which is otherwise used. `/api/ps` reports the loaded `context_length`, and `/api/show` reports the model's server parameters.
- Base models carry their own server-side sampling:
  - qwen3-coder:30b: top_k 20, top_p 0.8, repeat_penalty 1.05, temperature 0.7.
  - qwen3.6: top_k 20, **top_p 0.95**, min_p 0, **presence_penalty 1.5**, temperature 1.
- Native `/api/chat` honours `options.*` and `think`. With the default `truncate` it **silently truncates** (24,011 tokens became 4,098). With `truncate:false` it returns HTTP 400 `exceed_context_size_error`.
- `/v1` also truncates silently when the prompt exceeds the served window (about 72K tokens were reported as 32,770).
- Tokenizer bytes per token on generic text: 1.5 for dense JSON, 3.7–5.4 for code and prose, up to 6.83 for whitespace-heavy text. Ollama has no tokenize endpoint.

## Design (extends INF-001; KAD-029/030/031, EXTEND)

1. **Provider capability contract** (`kriya/core/provider_contract.py`, provider-neutral).
   - Every semantic setting is SUPPORTED (per request), SERVER_CONFIG_ONLY (must be verified against the server's model parameters), OBSERVABLE_ONLY or UNSUPPORTED.
   - Semantic settings: context_window, temperature, top_p, top_k, min_p, repeat_penalty, presence_penalty, frequency_penalty, seed, reasoning, keep_alive, plus features (stream_usage, truncate_control, served_context_observation, server_parameter_observation).
   - Typed reason codes: `PROVIDER_SETTING_UNSUPPORTED`, `PROVIDER_SETTING_NOT_EFFECTIVE`, `PROVIDER_SETTING_UNKNOWN`, `SERVED_CONTEXT_BELOW_REQUESTED`, `RUNTIME_CONTEXT_IDENTITY_MISMATCH`, `PROVIDER_PROMPT_TRUNCATED`, `QUALIFICATION_IDENTITY_UNVERIFIED`.
2. **The adapter owns the config dialect and the wire.** Only the adapter parses a binding's `extra_body` into semantic settings and maps them to the wire (INF-001 seam: provider names stay in `model_runtime.py`).
   - For Ollama `/v1`:
     - top-level `temperature`, `top_p`, `seed`, `presence_penalty`, `frequency_penalty` and `reasoning_effort`;
     - `options` is never sent;
     - the context window, `top_k`, `min_p` and `repeat_penalty` are SERVER_CONFIG_ONLY, verified against `/api/show` parameters.
   - `reasoning:false` becomes `reasoning_effort:"none"`.
   - Unknown `extra_body` fields are refused in production and recorded otherwise.
3. **Context: requested, served and budget.**
   - *Requested* is the binding's window.
   - *Served* is observed from `/api/ps` after the model is loaded (SERVER_OBSERVED), or unverified.
   - *Budget* is at most served and never above requested. `served < requested` is `SERVED_CONTEXT_BELOW_REQUESTED`.
   - `served != requested` under production's exact policy is `RUNTIME_CONTEXT_IDENTITY_MISMATCH`.
   - PRD-016 per-request tiers apply only where the context window is SUPPORTED per request.
4. **Pinned server identity** for SERVER_CONFIG_ONLY settings.
   - A derived Ollama model is created from the binding's approved values (`kriya model pin`).
   - Its tag and digest are the runtime identity, and the doctor verifies its parameters and served context.
5. **Transport ownership.**
   - One client per (endpoint, key) per LLMClient, with `max_retries=0`, explicit connect/read/write timeouts from configuration, an httpx client with `trust_env=False`, and a deterministic `aclose()`.
   - Streaming sends `stream_options` only when the adapter declares stream usage, and never resends.
6. **Truncation.** Prevention: a prompt predicted with the conservative floor ratio fits a verified served window. Detection:
   - provider-reported prompt tokens below `dispatch_bytes / ceiling` is `PROVIDER_PROMPT_TRUNCATED`;
   - `ceiling` is the qualified bytes-per-token ceiling when measured, else a conservative default above the calibrated 6.83;
   - native requests always send `truncate:false`.
7. **Identity and qualification.**
   - Inference settings become *effective* settings with provenance (REQUEST, SERVER_MODEL_CONFIG, SERVER_OBSERVED, UNVERIFIED).
   - The adapter protocol version and qualification policy version are bumped, so older records are STALE.
   - Qualification refuses a role whose required settings are unverifiable (`QUALIFICATION_IDENTITY_UNVERIFIED`).
