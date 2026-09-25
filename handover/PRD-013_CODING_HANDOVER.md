# PRD-013 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION. This is batch 3 (PRD-013 to PRD-016), with one pytest stop for the whole batch.

## Scope
Exact local model runtime fingerprint (`kriya/core/model_runtime.py`).

| Requirement | Implementation |
|---|---|
| Fingerprint components | alias, endpoint (credentials stripped), provider + `/api/version`, artifact digest (`/api/tags`), weights blob digest (modelfile `FROM`), format/family/parameter size/quantization, chat template (`RENDERER`, else template digest), tool-call parser (`PARSER`), tokenizer identity + digest of the full vocabulary/merges (verbose `/api/show`), normalized runtime parameters digest, served capabilities, trained context length, configured and effective (served `num_ctx`) window, Kriya protocol (resolved capability profile + provenance), `MODEL_PROTOCOL_ADAPTER_VERSION`. |
| Explicit unavailable | Every unreported component is `"unavailable"`; nothing is inferred from the name. The effective window is unknown (None) unless `num_ctx` is sent or set in the Modelfile - the trained maximum is never mistaken for it. |
| Stable digest | SHA-256 over normalized identity fields; timestamps (`modified_at`, `created`) and probe diagnostics never enter it. `exact` = artifact digest and provider version known. |
| Persisted | Every `LLMClient` call: `last_call_metrics.runtime_fingerprint`, `last_completion`; the active run's `RunRecord.model_runtime_fingerprint_ids` (`run_coordinator.record_model_runtime_use`); content-addressed `<state dir>/model_runtimes/<digest>.json`; the run's `model.runtime` event (primary fingerprint + Developer qualification state); Developer `generation_timings`/`model_use`. |
| Qualification keyed to it | PRD-014 records are keyed by the fingerprint digest; the resume `model_runtime` fingerprint (was UNAVAILABLE "until PRD-013") now binds every role model's exact runtime + the model-owned config fields, UNAVAILABLE unless all are exact. |
| Tag drift | A re-pulled tag changes the artifact digest, so the old record no longer applies (MISSING for the new identity; STALE if compared directly). |

The doctor's earlier PRD-010 fingerprint hashed `modified_at` and the raw `/v1/models` entry (`created`): it could change
with no runtime change. It is replaced by this fingerprint (`probe_llm_runtime` now returns it).

Probing: once per process per runtime input (only exact results are cached), 5 s timeout, never for a non-local
endpoint under `local_only`. `KRIYA_MODEL_RUNTIME_PROBE=0` disables it; `tests/conftest.py` sets that for every
non-`live_model` test, so the mocked suite can never reach a developer's running Ollama (probe tests inject a
transport).

## Files
- New: `kriya/core/model_runtime.py`
- Changed: `kriya/core/llm.py`, `kriya/control/run_coordinator.py`, `kriya/workflow/resume_fingerprints.py`,
  `kriya/workflow/workflow.py`, `kriya/workflow/attempt.py`, `kriya/production_doctor.py`, `kriya/cli.py`
  (`kriya model fingerprint`), `tests/conftest.py`
- Tests: `tests/test_prd013_model_runtime.py` (new), `tests/test_prd008_resume_fingerprints.py`,
  `tests/test_production_doctor.py`, `tests/test_live_production_doctor.py`, `tests/test_prd012_network_inventory.py`
  (new outbound site classified)

## Evidence (plain runner, no pytest)
- `test_prd013_model_runtime`: all pass (27 incl. 8 parametrized component-drift cases).
- Metadata-only live probe against the local Ollama 0.34.2 (no inference): `qwen3-coder:30b` and `qwen3.5:9B` both
  exact, stable across two captures, renderer/parser/tokenizer identities captured.

## Live test
`tests/test_live_prd013_016_model_runtime.py::test_prd013_fingerprint_is_stable_and_drift_invalidates_qualification`
(see PRD-014 handover for the command).

## Residuals
- Only Ollama's native API is fingerprinted exactly. Another OpenAI-compatible server yields a non-exact fingerprint
  (disclosed; such a runtime cannot be qualified, so it cannot be production-ready).
