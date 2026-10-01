# PROVIDER-CONTRACT-001: pre-fix evidence (2026-09-30, HEAD 853aa43, Ollama 0.34.4)

Live probes run against the local Ollama with nothing else loaded. Transport measurements used a local fake HTTP server and a fake proxy (no network, no model).

| # | Issue | Status | Evidence |
|---|---|---|---|
| P0-1 | `/v1` ignores nested `options.num_ctx` | CONFIRMED (MEASURED) | L1: `options.num_ctx=8192` on `/v1`, yet `/api/ps` `context_length=65536`. The server runs with `OLLAMA_CONTEXT_LENGTH=65536` (`ollama_server_env.txt`). |
| P0-2 | Requested context recorded as served | CONFIRMED (TRACED + MEASURED) | `model_runtime.probe_model_runtime` sets `effective_context_window` from Kriya's own `configured_context`, and LLMClient labels it `served_num_ctx`. Qualification records carry `configured=effective=32768` while the server serves 65536. |
| P0-3 | `top_k`/`top_p` transport mismatch | CONFIRMED (MEASURED, stochastic supplementary) | L3: `top_k=1` at temperature 1.5 gave 4 distinct outputs both nested and top-level on `/v1`, and 1 distinct output natively. Production sends `top_p`/`top_k` only nested in `options`. |
| P0-4 | `reasoning:false` does not disable thinking | CONFIRMED (MEASURED) | L4 (qwen3.6, `/v1`): default = 616 reasoning chars; `reasoning_effort:"none"` = 0; `options.think:false` = 814; top-level `think:false` = 606; native `think:false` = 0. The flag controls Kriya's budgeting only. |
| P0-5 | Hidden SDK retries and 600 s timeout | CONFIRMED (MEASURED) | `AsyncOpenAI` `max_retries=2`, `Timeout(connect=5, read=600)`. One call against a 500 server produced 3 HTTP requests. |
| P0-6 | Local traffic inherits proxy environment | CONFIRMED (MEASURED) | `trust_env=True`. With `HTTP_PROXY` set, the proxy got 1 hit and the local server 0. |
| P0-7 | Blind streaming resend | CONFIRMED (MEASURED) | One streaming call (SDK retries 0) after a transient 500 produced 2 differently shaped requests (`stream_options`, then none). Any exception triggers it. |
| P0-8 | Silent provider prompt truncation | CONFIRMED (MEASURED + TRACED) | L2: native default truncated 24,011 tokens to 4,098 with `done_reason=stop` and no error; native `truncate:false` returned HTTP 400 `exceed_context_size_error`. L2b: a `/v1` prompt of about 72K tokens was reported as 32,770 prompt tokens, with no error. `token_budget.compare_with_usage` only checks over-consumption. |
| P0-9 | Qualification binds desired, not effective, settings | CONFIRMED (TRACED + MEASURED) | Records bind the fingerprint (`configured=effective=32768`) and a settings digest over `extra_body` including `options.top_k`/`top_p`, which `/v1` never applies (P0-1, P0-3). |
