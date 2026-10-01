# Batch 6 final gate, second pass (2026-09-27, Kriya HEAD a04e8ac)

Config: demo-03 `config/generate-production.yaml`, run from `demo-03-brownfield/workspace/repo`. The outputs were pasted from the user's terminal.

| gate | result |
|---|---|
| PRD027-PRECISION-001 subset | passed (user-reported) |
| full `.venv/bin/pytest` | passed (user-reported) |
| Batch 6 live suite | passed (user-reported). **No evidence directory was written:** `handover/evidence/BATCH6/user-live-4/` does not exist, and no newer `preflight_identity.json` exists anywhere. So the live acceptance, including the PRD-029 targeted case and the new PRD-027 certified check, is not yet evidenced. |
| `context certify` | **PASS.** `certification_record_9d2a3e44.json`: CERTIFIED=true, precision 0.5814 (target 0.5), all 9 classes 1.0. Embedder `configured`, nomic-embed-text runtime aee18382…, index implementation 6865bdb0… (includes the ff6dd3f identity), recorded 2026-09-27T01:52:28Z. |
| qwen3.6 fallback `model qualify` | **FAIL: NOT_QUALIFIED.** Summary: 10 PASS, 7 FAIL, 2 UNAVAILABLE. Record `qwen36_qualification_record_317afb9a.json`, described below. |
| `doctor --production` | **PRODUCTION_READY=false.** `context.recall_certification` **PASS** (status CERTIFIED, read from the stored 9d2a3e44 record, not NOT_APPLICABLE). `model.qualification` **FAIL**, `MODEL_NOT_QUALIFIED`: the qwen3.6 developer fallback. The WARNs are the same as in the first pass (legacy traces db, role independence, Python region precision, fixed-guarantees summary). |

## The qwen3.6 fallback failure (recorded as observed; the config is unchanged)
**Identity under test.**
- Model: qwen3.6:35b-a3b-q4_K_M, runtime 48e3ddd2…
- Inference settings sha256:65e5b10c…: temperature 0.2, `reasoning: false`, `extra_body: {}`.
- Qualification identity: 317afb9a….
- Policy: kriya-qualification/3.
- Environment: darwin/arm64, M1 Max, ollama 0.34.2.

**Failed cases.**
- Required for the developer role: plain_completion, finish_reason_stop, full_file_raw_content, anchored_edit_protocol, malformed_output_recovery and streaming_assembly.
- Not in the developer role's required set: cancellation_semantics ("no streamed output before cancelling", 120 s).

**Observed mechanism.**
- Every failed completion case ended `finish: length / OUTPUT_TRUNCATED` at its case budget (64, 256, 512 and 1024 tokens).
- The measured limits show `reasoning_observed: True` and `reasoning_tokens_max: 899`. So the model reasons silently and uses up the output budget before any answer text.
- The cases with room to spare, or with a structured channel, passed: structured_json, multiline_json (after the 12288-token empty-content floor retry), the three tool-call cases, reasoning_behavior, output_truncation and tokenizer_measurement.

**Why the configured settings allow this.** Kriya's `reasoning` flag is a budget hint, not a provider control (`kriya/core/inference_settings.py`: it "changes Kriya's own budget floor"). With `reasoning: false`, no floor is applied. Nothing in `extra_body` (for example `reasoning_effort`/`think`) turns the model's thinking off. MODEL-QUAL-IDENTITY-001 documents exactly this case: "A qwen3.6 runtime that qualifies with `reasoning_effort: none` fails the same cases at its default reasoning."

**Classification.** This is a qualification gap in the configured fallback identity. It is not a Batch 6 code regression, and nothing indicates a Kriya defect. The user's instruction applies: the fallback settings are not changed inside Batch 6.

**The decision belongs to the user** (tracker: PROD-FALLBACK-QUAL-001):
- qualify a different, explicitly chosen fallback identity (for example the same model with a provider reasoning control); this changes the demo-03 production config;
- remove the fallback from the production config;
- or keep it, and accept PRODUCTION_READY=false for this config.
