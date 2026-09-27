# Batch 6: fallback closure, Option A (user decision, 2026-09-27)

Only the demo-03 `config/generate-production.yaml` Developer fallback changed. The previous file is kept as `generate-production.yaml.pre-fallback-fix.bak` next to it, and is copied here with the diff. The primary, the packaged defaults and the PRD-019 routes are unchanged.

**The fallback now reads:**
- `model: qwen3.6:35b-a3b-q4_K_M`
- `context_window: 32768`
- `temperature: 0.7`
- `extra_body: {options: {num_ctx: 32768, top_p: 0.8, top_k: 20}, reasoning_effort: none}`
- The redundant `reasoning: false` is removed. The field defaults to false, and it is only Kriya's budget hint. The one reasoning control is the provider's `reasoning_effort: none`.

**Resolved through Kriya's resolver.** I used `resolve_config_state` plus `AppConfig`, the path `kriya authority inspect` uses. The approval check was not applied, because the durable approval no longer covers the changed `llm_chain`. The result:
- Developer settings digest **sha256:0f1e6b5c…**: temperature 0.7, reasoning false, `extra_body {options:{top_k:20, top_p:0.8}, reasoning_effort:none}`, output ceiling 16384.
- This is exactly the settings digest of the qwen3.6 record that passed 18/18 under policy /3 (record 853402b9, 2026-09-26).
- The window sent is 32768.
- The primary's digest is unchanged: sha256:ed7bfc09….

**Why `num_ctx` is in `extra_body`.** A chain entry's `context_window` is budgeted but never sent. `num_ctx` reaches the provider only from `extra_body.options`, or through a context-tier expansion. The failed record 317afb9a shows the fingerprint with `configured_context_window` / `effective_context_window` missing. The proven identity sent it the same way, from `extra_body`. `num_ctx` is excluded from the settings digest, so adding it does not change the identity. This is recorded separately as FALLBACK-CONTEXT-WINDOW-001, for triage.

**The runtime digest will not equal the proven record's cc523e4c.** The runtime fingerprint includes the resolved capability profile. As a fallback, qwen3.6 resolves `known_production_profile`; as the MODEL-EVAL-001 primary, it resolved explicit capabilities. So `kriya model qualify` has to qualify this exact runtime; the 18/18 record does not carry over.

**Pin test:** `tests/test_production_fallback_identity.py` (2 tests).

**SEC-009.** `llm_chain` is SECURITY_AUTHORITY, so the workspace's durable approval no longer matches the config. Until the user runs `kriya -c ../../config/generate-production.yaml authority inspect` and then `authority approve`, every command using this config is refused.
