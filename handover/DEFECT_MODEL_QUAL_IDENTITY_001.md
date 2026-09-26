# MODEL-QUAL-IDENTITY-001: qualification identity omits behaviour-affecting inference settings

## Status

FIXED in `6c163f2`, READY_FOR_PYTEST_VERIFICATION (2026-09-26); revalidation (live requalification at 32K) is user-run after pytest. Design, decisions and residuals are in `MODEL_EVIDENCE_HARDENING_001.md` §1. Raised 2026-09-26 by the MODEL-EVAL-001 review.

**Decisions made by the fix:**
- **Separate qualification identity.** The identity is runtime digest + inference-settings digest. The runtime fingerprint and the adapter version are unchanged, so the keys of role independence, metrics and routing are too.
- **Temperature is per role.** It is the temperature each role's calls actually send, so it is part of the identity.
- **max_tokens is metadata only.**
- **Policy bumped to /3.** /2 records read STALE, never MISSING.
- **Revalidation is 32K only.** 64K stays NOT_QUALIFIED.

The text below is the original defect report.

- It does **not** block MODEL-EVAL-001, whose settings were fixed and equivalent wherever the model supports them.
- It **blocks** any packaged-default or production-routing change based on MODEL-EVAL-001.
- Before such a change: fix this defect, then revalidate every qualification record the identity change affects.

## Problem

A qualification record is keyed by the runtime fingerprint digest (PRD-013/014). `kriya/core/model_runtime.py` takes only `num_ctx` from the binding's `extra_body` (`configured_context_window(extra_body)`), and nothing else from the request. The following inference settings change model behaviour but are not part of the identity:

- `reasoning_effort` (and Ollama's `think`);
- `options.top_p`, `options.top_k`, `options.min_p`, `options.repeat_penalty`, `options.presence_penalty`;
- `temperature`;
- any other `extra_body` field.

`runtime_parameters_digest` covers the parameters Ollama *serves* (the Modelfile defaults), not what Kriya *sends* per request.

## Evidence (MODEL-EVAL-001, 2026-09-26)

- **Before:** `qwen3.6:35b-a3b-q4_K_M` without a reasoning setting was NOT_QUALIFIED. Hidden reasoning used up the case budgets: plain_completion, finish_reason_stop, full_file_raw_content, anchored_edit_protocol and malformed_output_recovery all FAILED.
- **After:** with `extra_body.reasoning_effort: none`, the same model qualified at 32K (18 PASS, `reasoning_observed: false`).
- **The gap:** that outcome depends on a setting that is not part of the identity. A config that drops the setting would reason at the template default (`xhigh` for qwen3.8) and fail, yet it would resolve to the same identity and show **QUALIFIED**.
  - `doctor --production`, the PRD-017 fallback selection and PRD-019 routing would all trust that record.
  - Here the two digests differ only because other fingerprint inputs changed between the runs. The two settings are not separated by design.

## Required fix

- **Identity.** Add a normalized **request-settings digest** to the Kriya-side fingerprint inputs, computed over the behaviour-affecting request settings of the exact binding: `extra_body` minus `options.num_ctx` (already a separate input), plus temperature and max_tokens.
  - Canonical JSON, with keys sorted.
  - Name it explicitly in the fingerprint, so `kriya model fingerprint` shows it.
- **Invalidation.** Bump `MODEL_PROTOCOL_ADAPTER_VERSION` (or the qualification policy version), so every existing record goes STALE rather than being silently reinterpreted.
- **Tests:**
  - two bindings that differ only in `reasoning_effort`, `top_p`, `top_k` or `temperature` have different fingerprints;
  - a record for one is not valid for the other;
  - `num_ctx` handling is unchanged;
  - the digest ignores key order.
  - Also: the resume `model_runtime` fingerprint (STATE-001) changes when a request setting changes. That's intended, and must be documented.
- **Revalidation after the fix.** Re-qualify every runtime whose record matters: the demo-03 production models and the MODEL-EVAL-001 arms, if a routing or default change is still wanted.

## Scope notes

- The per-role binding matters. PRD-019 places a candidate in a role (`--role`), and the role's own `extra_body` or temperature must be part of that identity.
- Temperature is an open design question, because roles use different temperatures (for example `reviewer_temperature`). Either include it in the identity per role, or document why qualification is temperature-independent. Decide explicitly; don't leave it out by accident.
