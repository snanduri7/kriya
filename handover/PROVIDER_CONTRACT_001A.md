# PROVIDER-CONTRACT-001A — Provider contract closure hardening

**Status:** implemented locally, awaiting owner review. Not pushed.
**Starting HEAD:** `2f62430` (pushed). **Backup branch:** `backup/provider-contract-001a-start-2f62430`.
**Product commits:** `662feae` (F-1, F-3), `2e8b09f` (F-2 /8 and the native adapter).
**Evidence:** `evidence/provider-contract-001a/`.

This is a post-closure follow-up to PROVIDER-CONTRACT-001. The original closure record (`evidence/provider-contract-001/closure/CLOSURE.md`) is unchanged; this document is its erratum and extension.

An independent review after the closure found four problems:
- F-1: the deadline was never wired into production.
- F-2: the truncation detector was too permissive.
- F-3: a transient observation failure could disable a safety check.
- F-4: the `/v1` capability table needed live verification.

## F-1 — one run-level inference deadline

**Reproduced on `2f62430`** (`f1_deadline/`):
- A model call inside a run with a 600 s budget was unbound: `deadline_bound=false`, read timeout 900 s.
- Every `run_generation_workflow` call restarted the budget clock, so each milestone unit and each enforce subtask got a fresh budget.

**Mechanism:**
- `run_coordinator.claim_run_generation_clock()` gives one monotonic clock per mutating run. It starts with the run's first model call or generation pass and is never reset.
- The admission check (`attempt._ensure_generation_time_budget`) reads it as `GenerationState.budget_started_monotonic`. The per-invocation wall-time metrics keep their own clock.
- Every model call's deadline is the run clock plus `autonomy.generation_time_budget_seconds`, narrowed by an `inference_deadline()` cap.
- Request timeout = `min(llm.transport read timeout, remaining)`. The request is also bounded in total by `asyncio.wait_for`, so a streamed answer cannot outlive the deadline (httpx's read timeout applies per chunk).

**Typed outcomes:**
- `InferenceDeadlineError` is `INFERENCE_DEADLINE_EXHAUSTED` before any provider contact (probe, observation or request) and `INFERENCE_DEADLINE_EXCEEDED` mid-request.
- It is never escalated to a fallback or resent.
- Inside an attempt it is the existing `time_budget_exhausted` stop (`failure_category: generation_budget_exhausted`).
- At the final review it is a typed refusal that keeps the committed state (`final_review_refused`).
- With no budget configured, it is reported as `deadline_bound=false`.

**Telemetry** (`provider_contract.deadline`): `deadline_bound`, `deadline_source`, `remaining_budget_at_dispatch_ms`, `configured_transport_timeout_ms`, `effective_request_timeout_ms`, `elapsed_request_ms`, `timeout_reason`.

**Qualification:** it runs outside a mutating run, so it has no run deadline. A test proves no deadline leaks out of a run or a cap.

**Operational note (spec §25):** the whole run now shares one budget. Before a live demo, measure representative demo tasks and choose `generation_time_budget_seconds` deliberately.

## F-3 — served-context observation is typed and fail-closed

**Reproduced on `2f62430`** (`f3_observation/`):
- One `/api/ps` failure blacklisted the endpoint for the process.
- A production call on an exact runtime whose served window could not be observed returned OK.

**Mechanism** (`model_runtime.observe_served_context_state`):
- The outcome is one of `observed`, `not_loaded`, `loaded_without_window`, `observation_failed` or `not_applicable`.
- A failure gets one bounded retry. Nothing is cached; `_UNOBSERVABLE_ENDPOINTS` is removed.

**Behaviour:**
- Strict production on an exact runtime that declares observation: no observed window after the call is `SERVED_CONTEXT_UNOBSERVABLE`.
- An unloaded model before the call is not a failure.
- Outside production the window is recorded as unverified, with a WARNING.
- `doctor --production` loads the model through the same client check, so a deployment that unloads right after a request (e.g. `OLLAMA_KEEP_ALIVE=0`) fails `model.provider_contract`. Kriya never changes keep-alive itself.

## F-2 — production uses the native adapter, which refuses over-context prompts

**Measured on Ollama 0.34.4** (`f2_truncation/FINDINGS.md`, `native_parity/`):
- `/v1` answers every prompt larger than the served window from exactly window/2 + 2 tokens (16,386 for 32,768), from 1.02x to 4x the window, for code and dense text. No prompt below the window was truncated.
- The byte check missed real truncation: code at 1.02x the window, and dense text at about 2x.
- Admission undercounts high-entropy text: about 1.47 bytes/token against the 2.11 qualified floor.
- Native `/api/chat` with `truncate:false` refuses with HTTP 400 carrying the exact `n_prompt_tokens`.

**Live parity** (pinned models, production settings, only the adapter switched; N1–N8):
- Native equals `/v1` for plain, streamed, reasoning-off, JSON mode, tool calls (exact arguments), the run deadline and served-context observation.
- On over-context, `/v1` returned OK from a truncated prompt, while native refused, typed, with the exact count 39,643.

**Decision: PROMOTE_NATIVE.**
- The native adapter is `/2`: the refusal carries `provider_prompt_tokens`/`provider_context_window`, and an answer with tool calls reports `finish_reason: tool_calls`.
- `provider_contract.admission_miss` records, content-free, each time admission said a prompt fits and the provider refused it as over-context. This measures how often admission undercounts.

**Qualification `/8`:**
- Every role requires `over_context_refusal`: a prompt above the served window, sent through the model's own adapter and production wire body, must be refused with a typed `PROVIDER_PROMPT_TRUNCATED`.
- An answer from a truncated prompt FAILS. The live `/v1` negative control fails: answered from 16,386 tokens.
- `/7` records are STALE and kept.
- Both pinned models are QUALIFIED on native under `/8`: qwen3-coder 19/0/1 (all seven roles), qwen3.6 16/0/4 (developer fallback). The UNAVAILABLE cases are the same as under `/7`.

**Production config:**
- `~/.kriya/operator/provider-contract-v4-production.yaml` (sha256 `474346b7…`) equals v3 except `inference_runtime: ollama_native` on both bindings. Pinned tags are unchanged.
- SEC-009 approval set digest `242af965…` is for the workspace `~/kriya-live-validation/provider-contract-001a/ws`. v3 and its approval are unchanged.
- `doctor --production`: `production_ready=true`; `model.provider_contract` PASS on `ollama_native` for both models.

**Deferred (owner decision):**
- C(i)/C(ii), the admission estimator. Undercounting now wastes one refused request instead of authorizing a truncated answer, and `admission_miss` measures how often.
- The optional one-refit.

`/v1` remains supported for compatibility. Its limitation is explicit: it cannot pass `/8`.

## F-4 — `/v1` capabilities, measured (`f4_v1_capabilities/`)

| Field | `/v1` | Native (control) | Table |
|---|---|---|---|
| `keep_alive` | not applied (residency stays 5 min) | applied (30 min) | `/v1` UNSUPPORTED is correct |
| `frequency_penalty` | applied per request (greedy, seeded output changes) | applied | `/v1` SERVER_CONFIG_ONLY is too strict. Not widened: source-level proof is still needed. |
| `presence_penalty` | no effect (0, 2, 10) | **no effect either** (`repeat_penalty` control does change output) | **The native table's SUPPORTED claim is not borne out.** Narrowing it changes request handling, so it needs native adapter `/3` and requalification. Owner decision. No production binding requests it. |

No capability table was changed.

## Out of scope (unchanged)

qwen3.6 `presence_penalty=1.5` (Q-1), per-role output budgets, Graphify, FILE-INTEGRITY behaviour.

## Closure follow-up (2026-10-01): an unproven setting is never labelled effective

Product commit `382a270`. The earlier sections stand as written.

**Finding (MEASURED, F-4 probes):** `presence_penalty` has no observable effect at 0, 2 or 10 on either adapter, while `repeat_penalty` does. The native table nevertheless declared it SUPPORTED and `/v1` declared it SERVER_CONFIG_ONLY. As a result qwen3.6's Modelfile value (1.5) was shown and persisted as the *effective* setting.

**Fix:**
- `presence_penalty` is UNSUPPORTED on both adapters. It is never sent, and its effective value is unverified.
- The served model's configured value is kept as `server_config_observed`: an observation of the server, never the effective value. Production doctor now shows qwen3.6's `presence_penalty` as `{effective: null, provenance: unverified, server_config_observed: 1.5, support: unsupported}`.
- Strict production refuses any *requested* setting the adapter declares UNSUPPORTED (`PROVIDER_SETTING_UNSUPPORTED`). Outside production it is recorded and not sent. Before this, `enforce` refused only settings proven ineffective, so a requested-but-unprovable setting (e.g. `/v1` `keep_alive`) passed production silently.
- The port's default request plan carries a setting only as the adapter's own `provider_capabilities` declare it. An adapter that declares nothing proves nothing and is refused in production, consistent with qualification's `require_verified_identity`.

**Qualification impact (TRACED, spec A4):**

| Question | Answer |
|---|---|
| Does an effective setting of either production binding change? | **No.** Neither requests `presence_penalty`. The settings digest is the wire body plus the requested server-only settings, and stays `sha256:482067b2…` (pinned by `test_the_production_bindings_inference_identity_is_unchanged`). |
| Does what a PASS means change? | **No.** No case touches the setting. |
| Does the runtime fingerprint change? | **No.** The served PARAMETERs are observed facts. |
| Adapter version? | **Not bumped.** Request handling for the current bindings is unchanged. A binding that did request it gets a different wire/settings digest, so its records go stale by construction. |

So the policy stays `/8`, there is no requalification, and the `/8` records stay current: `doctor --production` `model.qualification` PASS at `3d2bfd2`.

**Not changed:** qwen3.6's inherited 1.5 (Q-1). Whether it, or any presence-penalty-like setting, affects code quality stays the deferred experiment `NATIVE-PRESENCE-PENALTY-001`.

**Verification:**
- Focused tests: `tests/test_provider_contract_001a.py`.
- Mutation: 15/15 killed with Part B (`mutation/mutation_ab.txt`).
- Full parallel suite at `3d2bfd2`: 7886 passed, 0 failed, 0 skipped.
- `doctor --production` (v4): `production_ready=true` (`doctor/doctor_production_v4_3d2bfd2.json`).
