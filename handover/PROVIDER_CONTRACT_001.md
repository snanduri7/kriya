# PROVIDER-CONTRACT-001: Kriya ↔ provider inference contract

**Status:** CLOSED 2026-09-30 at product HEAD `1200824` (pushed; fast-forward from `853aa43`). Closure record: `evidence/provider-contract-001/closure/CLOSURE.md`. Architecture ledger: KAD-062 (EXTEND of KAD-029/030/031). No Graphify run was started during this work, and Graphify evidence was left untouched.

## Invariant

Any model or runtime setting that Kriya records, budgets against, qualifies or treats as authoritative must be proven to reach the provider with the intended semantics, or Kriya must fail closed. Desired configuration is not effective inference identity.

## Measured provider facts (Ollama 0.34.4, 2026-09-30, `evidence/provider-contract-001/pre_fix/`)

- **What `/v1` ignores.** It ignores every `options.*` field (`num_ctx`, `top_k`, `top_p`, `think`) and a top-level `top_k`.
- **What `/v1` applies.** It honours top-level `temperature`, `top_p`, `seed`, `max_tokens` and `reasoning_effort`. `"none"` disables thinking. Any string gets HTTP 200, even `"bogus"`. It also honours `stream_options.include_usage`.
- **Served window.** It comes from the model's `PARAMETER num_ctx`, otherwise from the server environment (`OLLAMA_CONTEXT_LENGTH=65536` on this host). `/api/ps` reports the loaded `context_length`, and `/api/show` reports the model's PARAMETERs.
- **Base-model sampling.**
  - qwen3-coder:30b: top_k 20, top_p 0.8, repeat_penalty 1.05, temperature 0.7.
  - qwen3.6: top_k 20, top_p 0.95, min_p 0, presence_penalty 1.5, temperature 1.
- **Native `/api/chat`.** It honours `options.*` and `think`. With the default `truncate` it silently truncates (24,011 → 4,098 tokens). With `truncate:false` it answers HTTP 400.
- **Truncation on `/v1`.** `/v1` silently truncates a prompt larger than the served window.
- **Pre-fix transport.** One call made 3 HTTP requests (SDK retries). The read timeout was 600 s. The SDK inherited `HTTP_PROXY`. A failed stream was resent in another shape.
- **Tokenizer ratios.** 1.5 bytes/token for dense JSON, 3.7–5.4 for code and prose, up to 6.83 for whitespace-heavy text. There is no tokenize endpoint.

## What was built

1. **Provider-neutral contract** (`kriya/core/provider_contract.py`).
   - `Support`: SUPPORTED, SERVER_CONFIG_ONLY, OBSERVABLE_ONLY, UNSUPPORTED.
   - `Provenance`: REQUEST, SERVER_MODEL_CONFIG, SERVER_OBSERVED, INFERRED, UNVERIFIED.
   - `ProviderCapabilities` and `ProviderRequestPlan`:
     - the wire body plus a requested/effective state for every setting;
     - unknown fields and conflicts;
     - `enforce(strict)`.
   - `ContextWindowState` and `context_window_state`, `budget_window`, and `check_prompt_consumption`/`consumption_bytes`.
   - The typed reason codes.
2. **`/v1` adapter** (`OllamaRuntimeAdapter`, `model_runtime.py`).
   - `extra_body` is parsed as Ollama dialect.
   - The wire carries only top-level `top_p`, `seed` and `reasoning_effort` (`reasoning:false` → `"none"`). `options` is never sent.
   - The window, `top_k`, `min_p` and the penalties are SERVER_CONFIG_ONLY and are compared against `fingerprint.server_parameters`.
   - Conflicts, and `reasoning_effort` values outside a vocabulary, are always refused. Unknown fields and not-effective settings are refused under production and recorded otherwise.
   - `/v1` takes no per-request window, so it offers no PRD-016 tiers.
3. **Context.**
   - Requested, served and budget windows are separate. Served is observed via `/api/ps` or read from the served model's configuration; it is never taken from the request.
   - Served < requested is always `SERVED_CONTEXT_BELOW_REQUESTED`. Served ≠ requested under production is `RUNTIME_CONTEXT_IDENTITY_MISMATCH`.
   - Every sizing site uses `budget_window`: capacity, routing, qualification, and the transition profile.
4. **Pinning** (`kriya model pin`). It creates a derived Ollama model named `<model>:<tag>-kriya-<12 hex>` carrying only the server-only values the binding declares.
5. **Transport.**
   - One AsyncOpenAI per (endpoint, key): `max_retries=0`, `httpx.AsyncClient(trust_env=False)`.
   - Kriya timeouts come from `llm.transport` (SECURITY_AUTHORITY), are sent per request and are capped by `inference_deadline`.
   - A stream is one request. `aclose()` is deterministic, and the CLI closes every client it opens.
   - Qualification probes get their timeout through `llm.transport` (`qualification_client_factory`).
6. **After the call.**
   - A served-window violation, or under-consumption on an exact runtime, sets `PROVIDER_CONTRACT_VIOLATION`, which is a deterministic `provider_contract` stop.
   - Under-consumption means reported prompt tokens < `consumption_bytes / ceiling`. Whitespace runs are counted once. The ceiling comes from qualification and is never below 8.0.
   - The per-call `provider_contract` telemetry records the settings identity, context state, timeouts, deadline, reported prompt tokens and the consumption verdict. It never records prompt text.
7. **Budgets.**
   - Each role has its own output budget (`agent_llms.<role>.max_output_tokens`).
   - A reasoning identity adds its qualified `reasoning_tokens_max` (`llm.reasoning_max_tokens`); the 12288 floor applies only when that is unmeasured.
   - The output share stays capped at half the window, because a configured output is a ceiling (see Batch B evidence).
8. **Identity and qualification.**
   - Inference settings are at version /3: the wire body plus the requested server-only settings.
   - The qualification policy is at version /4, so every /3 record is STALE.
   - `require_verified_identity` refuses (`QUALIFICATION_IDENTITY_UNVERIFIED`) before any case when a setting is conflicting, unknown, not applied, or unverifiable (for example an unpinned window).
   - The capacity case sends the adapter's wire body and FAILS on a served-window mismatch.
   - The tokenizer case measures the consumption ceiling.
9. **Doctor.** `model.provider_contract` is required. A controlled probe is sent per role model, and its output goes to stderr. The row passes only when every relied-on setting is verified and served == requested.
10. **Native adapter** (`OllamaNativeRuntimeAdapter`, `inference_runtime: ollama_native`, **not the default**).
    - Everything travels per request in `options`, plus `think`, `keep_alive` and `format`.
    - Every request sends `truncate:false`; the provider's 400 becomes a typed `PROVIDER_PROMPT_TRUNCATED` that is never retried.
    - Streaming is NDJSON over one request, and tool calls are normalized.
    - It has its own adapter version, so its evidence is never shared with `/v1`.

## Evidence

- **Batch A** (`batch_a/RESULTS.md`): full pytest once gave 7503 passed and 4 failures. All 4 were classified as test pins or fakes, fixed, and the 3 affected files reran 61/61. 19 mutations KILLED.
- **Batch B** (`batch_b/RESULTS.md`): full pytest once gave 7525 passed, 0 failed. 11 mutations KILLED.
  - Live: the pinned models were requalified under policy /4. The pre-fix run is kept, including its `timeout_semantics` FAIL.
    - qwen3-coder: 18 PASS / 0 FAIL / 1 UNAVAILABLE (server restart, never exercised live).
    - qwen3.6: 15 PASS / 0 FAIL / 4 UNAVAILABLE. The tool cases are not enabled for that binding.
  - Every role is QUALIFIED. `doctor --production` reports `production_ready: true` with the pinned config.
- **Batch C** (`batch_c/RESULTS.md`): full pytest once gave 7552 passed, 0 failed. 8 mutations KILLED.
- **Live L1–L7** (`live/L1_L7.jsonl`, at ca47a3c; the L1 native label was remeasured after its fix).

### Own defects found and fixed in separate commits

- **Pin tag.** The derived pin tag had a second `:` and only 5 hex characters.
- **Qualification timeout probe.** Its SDK timeout was overridden by the per-request Kriya timeout.
- **Doctor JSON.** The doctor's probe usage line broke `--json`.
- **Native window label.** The native window was labelled `server_model_config`.

## Operator notes

- **Production config.** The operator production config (`~/.kriya/operator/prd036-production.yaml`) is **unchanged**, because it is immutable under PRD-036. Its unpinned models are refused under production (served 65536 ≠ 32768).
  - The successor is `~/.kriya/operator/provider-contract-v3-production.yaml` (SHA-256 `667831f0…75e0d8`). It binds the pinned tags and preserves the current resolved role budgets (no tuning), and it has its own SEC-009 approval.
  - A future Graphify run #3 needs a new canonical config derived from this identity.
- **Pinned models created on the local Ollama.**
  - `qwen3-coder:30b-kriya-e52213655394`
  - `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a`
  - Both carry PARAMETERs `num_ctx 32768` and `top_k 20`.
