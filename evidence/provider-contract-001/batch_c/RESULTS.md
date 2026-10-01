# PROVIDER-CONTRACT-001 Batch C - verification record (2026-09-30)

Native Ollama adapter (`inference_runtime: ollama_native`), registered, NOT the default.

- tests/test_provider_contract_native.py: 24 (plan, think/effort mapping, keep_alive, unknown
  field, /v1 parity, not-default, evidence separation, error classes, wire through LLMClient
  against a loopback /api/chat, one-request stream, over-window typed refusal with no
  changed-request retry, server error one request, tool-call normalization both directions,
  truncate:false unconditional).
- Mutations (mutate_c.py.txt): 8 KILLED. One survivor on the first pass (the transport's
  `setdefault("truncate", False)` was redundant with the plan AND overridable by an unknown
  `truncate` field outside production) -> the transport now forces truncate:false and is tested.
- Full pytest (once): 7552 passed, 0 failed (full_pytest.txt).
- Static: ruff + pylint clean.
