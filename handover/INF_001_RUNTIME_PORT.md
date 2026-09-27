# INF-001: pluggable inference runtime framework (scoped deliverable)

## Status
COMPLETE for the scoped runtime-framework deliverable (Backlog 6.5, 2026-09-27). It awaits the user's pytest run and the live identity check below.

Real vLLM support is not implemented. It is an extension point that needs separate approval.

Commits:
- abbdb4b: golden parity test.
- bda7e85: port, default adapter, config selection, contract suite.
- This docs commit.

## Architecture
`workflow / agents -> LLMClient (the inference service) -> InferenceRuntimePort -> runtime adapter`

**Kriya keeps everything above the port.** All of it stays in `kriya/core/llm.py` and its callers:
- routing (PRD-019), qualification (PRD-014), fallback selection (PRD-017) and retries;
- context and output budgets (PRD-016, FALLBACK-CONTEXT-WINDOW-001, PROMPT-BUDGET-FIT-001);
- egress (PRD-012) and the rest of authority;
- evidence, metrics, verification and run state;
- the reasoning `response_format` retry, the empty-content floor retry and `split_reasoning`;
- tool-argument validation and textual tool-call recovery.

**An adapter owns only what differs between runtimes** (`kriya/core/inference_runtime.py`):
| Port method | Responsibility |
|---|---|
| `complete`, `complete_with_tools` | Transport: translate a `ChatRequest` into a `ChatResponse` / `RawToolCall`s. Streaming goes through `stream_callback`. `CancelledError` always propagates. |
| `configured_context_window`, `with_context_window`, `without_context_window`, `supports_per_request_context_window`, `capabilities.per_request_context_window` | How a per-request context window is expressed, and whether the runtime takes one. |
| `probe` | The exact runtime fingerprint qualification is keyed by. |
| `classify_error` → `RuntimeErrorKind` | TIMEOUT, CONNECTION, AUTHENTICATION, RATE_LIMITED, SERVER, or REQUEST. Only REQUEST earns Kriya's changed-request retry. |
| `list_models` | Health and discovery, fetched with the caller's JSON fetcher, which owns egress and timeouts. |

**Adapters:**
- **`OllamaRuntimeAdapter`** (`kriya/core/model_runtime.py`) is the packaged default and is registered there. `model_runtime.py` is still the only module that names a provider, its window field or its native API; the existing tripwire test is unchanged.
  - It extends the shared `OpenAICompatibleTransport`, which is the only caller of `chat.completions.create` in `kriya/` (tested).
  - It wraps the unchanged `probe_model_runtime` and `options` window functions. They are looked up at call time, so their test doubles still apply.
- **`FakeRuntimeAdapter`** (`tests/_fake_inference_runtime.py`) is deterministic and registered by tests only. `kriya/` registers only the default adapter (tested), so no configuration can select canned answers in production.

**Selection.**
- A model binding (`llm`, any `llm_chain` entry, or an `agent_llms` role's llm/chain) sets `inference_runtime`; `None` means the default.
- Lookup goes through a process-wide registry (≈0.08 µs).
- An unregistered name, `vllm` included, raises `UnknownRuntimeAdapterError`. It is never served by the default.
- **SEC-009:** `llm.inference_runtime` is SECURITY_AUTHORITY, and an `agent_llms` role naming it is too. The field decides which native identity endpoints are probed and whether a window is sent. `llm_chain` was already SECURITY_AUTHORITY as a whole. A demo-03-shaped config does not set the field, so its security-field records are unchanged and no re-approval is triggered.

## What the directive required, and where it is proven
| Requirement | Evidence |
|---|---|
| Existing Ollama behaviour parity | `tests/test_inf001_runtime_parity.py`, pinned at 3c9f7db before the port existed. The exact `create()` kwargs for plain, JSON-mode, fallback-override, tools and streaming (with the `stream_options` retry), and the runtime digest of a fixed Ollama probe. |
| Fake runtime contract suite | `tests/test_inf001_runtime_port.py` (25 tests) |
| Primary/fallback through the abstraction | `test_primary_and_fallback_requests_go_through_their_bindings_adapter` |
| Context propagation | per-request window sent in the adapter's own form; no per-request window means none sent and the served window is budgeted; unreported means `provider_managed` |
| Reasoning/settings propagation | `test_settings_and_reasoning_controls_reach_the_adapter_unchanged` (and the digest) |
| Tools / JSON / streaming | tool calls normalized and validated by Kriya (malformed → `{}`); `response_format`; streamed tokens |
| Timeout / cancel / error normalization | classified TIMEOUT/SERVER; `CancelledError` propagates with `CANCELLED` recorded; the retry only for REQUEST |
| Fingerprint changes when the runtime materially changes | `provider_version` → a new digest |
| Same weights, different runtime → distinct qualification | same `weights_digest`, different digest and qualification identity |
| No provider-specific branching in workflow/agents | layering tests: `kriya/workflow` and `kriya/agents` name no adapter, transport or SDK; plus the existing INF-001 tripwire |
| Adapter, fingerprint and qualification lookups cached; no probe per call | 5 calls → 1 probe; fingerprint cache keyed by adapter (two runtimes at one endpoint never share a fingerprint); qualification records parsed once while unchanged, re-read when changed |
| Overhead measured independently of inference | `handover/evidence/BACKLOG_6_5/inf001_overhead.txt`: port dispatch ≈0.5 µs/call on top of the adapter call (20,000 calls); registry lookup ≈0.08 µs |

Environment and capacity evidence stays separate from functional qualification. The PRD-014 environment identity is unchanged and still derived from the fingerprint's provider and version.

## Changed behaviour (deliberate)
- **Qualification capacity case.** It now sends its probe through the adapter's text transport (same request shape as any text request, `response_format=None`), not a hand-built `create()` call.
- **Capacity-case wording.** "no larger tier … on an exact Ollama runtime" now reads "… on an exact runtime whose adapter takes it per request".
- **Qualification record reads are cached per process.** They are revalidated by the file's (mtime, size): never stale, never re-parsed while unchanged. Before this, `offered_context_tiers` re-read every record on every request under the adaptive policy.

## Identity preservation (the real proof is live)
- **What the tests show.** For the default adapter the wire and the runtime digest are byte-identical: `MODEL_PROTOCOL_ADAPTER_VERSION` was not bumped, and neither the adapter name nor the new config field enters `identity_fields`.
- **Checkpoints.** The new config leaf changes the resume `config` fingerprint of checkpoints saved before it. Every code change already does this through `kriya_runtime`.
- **Live check (user):** from demo-03 `workspace/repo`, run `kriya -c ../../config/generate-production.yaml model fingerprint` and `model status`. The primary must still read ea90552d… QUALIFIED and the qwen3.6 fallback fc063b9e… QUALIFIED.

## Extension point: VllmRuntimeAdapter (documented only)
1. Subclass `OpenAICompatibleTransport`: vLLM serves the same chat API.
2. Set `capabilities.per_request_context_window=False`. `max_model_len` is fixed at server start, so `with_context_window` returns the body unchanged, and the dispatch budget labels the window `provider_managed` unless the probe reports it.
3. Implement `probe` from the server's version and model endpoints. An exact artifact digest and server version make an exact fingerprint; without them it stays non-exact and is never QUALIFIED.
4. Register it under its own name.

The same weights served by vLLM are a different runtime digest, so they never share qualification evidence with the default runtime. Nothing above the port changes.
