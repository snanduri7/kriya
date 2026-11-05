# PRD-035 - rebuilt-baseline preflight (2026-11-05), BEFORE qualification and BEFORE any live matrix

Nothing here ran inference. Only Ollama's read-only `/api/version`, `/api/tags`, `/api/show` and `/api/ps` were contacted. Raw output is in `data/`, the scripts (stored as `.py.txt` so ruff stays at zero) in `scripts/`.

## Baselines
- PRD-034 certified executable: `230e54fa0eb0c6af60a94964b6f99be5fb26b63f`, `kriya/` tree `850b2a1712e36333cc77488c39a3d18ac31f859b`. PRD-034 is CERTIFIED and is not reopened.
- PRD-035 harness-only correction: commit `57b881d` (tests only). `kriya/` and `plugins/` are byte-identical to `230e54f`.
- The model-certification report/record key does NOT contain the Kriya revision (only model, runtime digest, inference-settings digest, environment digest and case-set version; the revision appears only in release-trial mode). The owner command therefore writes `kriya-identity.txt` (HEAD, tree, `kriya/` tree, tracked-dirty state) next to the report.

## Ollama 0.40.14 (was 0.40.13 at the last qualification, 2026-11-01)
Release notes v0.40.13..v0.40.14 (8 commits). One change matters to identity: **PR #19432, `/v1` no longer overrides a model's packaged sampler defaults** - a client that omits `temperature`/`top_p` used to get 1.0/1.0 on `/v1/chat/completions`, `/v1/completions` and `/v1/responses`; those fields are now left unset, so `/v1` behaves like `/api/chat`. The other changes (`ollama launch claude/codex`, cloud web-search decoding, `/v1/responses` input content, ChatGPT retry in the app, MLX top-k, CI buildx pin) do not touch Kriya's path.
- The provider version is part of the runtime fingerprint, so every earlier qualification is STALE: fresh qualification is mandatory and is not waived by the trace below.
- PRD-035 stays on `kriya-ollama-native/3` (`/api/chat`). Historical `/v1` compatibility (openai-compat/2, Ollama 0.34.x) is not reopened; the historical 11/11 is blueprint only.

## Native `/api/chat` request trace (MEASURED, no inference: `data/trace_native.json`)
The real `LLMClient.complete_result` path with the adapter's `complete()` replaced by a recorder, under the harness configuration (`KRIYA_RELEASE_CONFIG` -> production file). Wire body:
`POST http://localhost:11434/api/chat` - `options = {num_ctx: 32768, top_k: 20, top_p: 0.8, temperature: 0.7, num_predict: 16384}`, `truncate: false`, `think: false`, `stream: false`.
- **Explicit in the request (provenance REQUEST):** `temperature` 0.7, `top_p` 0.8, `top_k` 20, `num_ctx` 32768, `num_predict` 16384, reasoning `none` (`think:false`).
- **Inherited from the served model (provenance SERVER_MODEL_CONFIG):** `repeat_penalty` 1.05 and the stop tokens (`<|endoftext|>`, `<|im_end|>`, `<|im_start|>`). `min_p` and `presence_penalty` are unset and unverified (not sent; presumably the model's default).
- Therefore `temperature` and `top_p` do NOT come from the model on this path; the #19432 behaviour change does not alter the PRD-035 request. (The primary's packaged values happen to equal the explicit ones: temperature 0.7, top_p 0.8, top_k 20.)
- Fallback `qwen3.6...-kriya-620d4d5ce36a` packages `temperature 1, top_p 0.95, presence_penalty 1.5, repeat_penalty 1, min_p 0`; Kriya's request overrides temperature/top_p/top_k explicitly; `presence_penalty 1.5` and `min_p 0` are inherited.
- The fallback entry of the production config declares no `capabilities`. Before qualification its profile was `unverified_conservative_default` (native_tool_calls false, `full_file`); AFTER the owner's qualification (2026-11-05) `model status` reports `qualification_derived` (native_tool_calls false, json_mode true, reliable_multiline_json true, `text_markers` edit protocol), so C6's fallback Developer runs on `text_markers`. This is the production configuration, not a conflict with the C1-C11 case set.

## Harness findings
1. Stock harness (no `KRIYA_RELEASE_CONFIG`) binds `/v1` with no `inference_runtime`, resolves `kriya-openai-compat/3` and fails the identity preflight (`data/preflight1-stock-harness-openai-compat.json`). Resolved by using the canonical production config through `KRIYA_RELEASE_CONFIG` (owner ruling); no second config created.
2. C6's spy was registered over the packaged `ollama` adapter and could not observe a native-bound configuration, so C6 failed by construction. Fixed in `57b881d` (see its message and `tests/test_prd035_harness_controls.py`).

## Identities after the production config (data/preflight2.json)
Production config `~/.kriya/operator/graphify-qwen3coder-native-production.yaml`, sha256 `076e48ac125c8df3a7383c47ba771c03a9cf3a2641f29bd0d368bb5933c83d4e`. Harness-built fingerprints and settings digests equal those computed from the production file itself. No conflict with the signed case set found (the harness takes only the `llm` and first `llm_chain` bindings; case workflow settings stay the cases' own).

| | primary | C6 fallback |
|---|---|---|
| model | `qwen3-coder:30b-kriya-e52213655394` | `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` |
| artifact digest | `sha256:141e95d786b871df832f727594b8229e6bd91097e1701738085661521ab00086` | `sha256:0314ad2492d1fc443c33fc3026689db99374337a7c18887b44a865264b35ab78` |
| runtime fingerprint | `c3df7a695741d406dce387bee68117f4bcf9817be736886b75af021d6bf1420a` | `595304688a6fe3971af9b610c1fc91d0284bb0c838f610c5994913b5ee5d9879` |
| adapter / context | `kriya-ollama-native/3` / 32768 (max_tokens 16384) | `kriya-ollama-native/3` / 32768 |
| inference settings | `sha256:482067b2e0b4d3b7d4ebdfbf83ef8aeef79c3f07ec9d3b0e2e38298eb74a3b15` | same digest |
| qualification | **MISSING** (owner must qualify) | **MISSING** |

Environment digest `sha256:79b6104aefaf54c6484de3be25b8f9eabd3733798742db7dbf1a91bd1d4ab2d9` (darwin/arm64, Apple M1 Max, 64 GiB unified, metal, `ollama/0.40.14`). Case set version 1, 11 cases C1-C11.

## Next (owner)
Qualify both models on Ollama 0.40.14, re-run the preflight, then run the single matrix attempt. Commands are in the hand-over message.

## Owner rulings recorded (2026-11-05)
- **Terminal PRD-035 environment evidence (accepted):** the actual path is `/api/chat`; `temperature`, `top_p`, `top_k`, `num_ctx`, `num_predict` and `think` are explicit in the request; Ollama 0.40.14's `/v1` omitted-sampler change therefore does not alter this PRD-035 native request; the inherited model parameters (`repeat_penalty`, stop tokens; for the fallback also `presence_penalty` and `min_p`) remain part of the served-model/runtime identity (they are in the fingerprint's `server_parameters`). `/v1` compatibility work is not reopened.
- **C6 harness fix (`57b881d`) accepted** as harness-only; offline controls and mutation proof sufficient; PRD-034 remains closed.
- `kriya/` and `plugins/` are byte-identical to the PRD-034 certified product tree (`kriya/` tree `850b2a1712e36333cc77488c39a3d18ac31f859b`, `plugins/` tree `2e9704960ea243568df0e56e62073c10a13914a4`).

## Revision binding (commit `bf018dc`)
Traced: the record key and report named no Kriya revision. The existing evidence extension point is the content-digested report, which the stored record already seals (`record.report_digest`, sealed by `record_digest`). The Kriya identity (commit, tree, `kriya/` tree, `plugins/` tree, tracked-worktree-clean at start and end, sha256 of the production config) is therefore bound INTO the report content, and `scripts/verify_model_certification.py` (run by `scripts/certify_model.sh`) refuses any mismatch. No product change, no record-schema bump, no second identity system. A standalone `kriya-identity.txt` is NOT used. Controls: `tests/test_prd035_revision_binding.py` (23 tests; each negative control changes one field, re-digests and re-seals so only the binding check can catch it; disabling any of 7 verifier checks fails a test).
Known limit (unchanged, product code): `kriya model certification` reports CURRENT by key alone; the revision-bound verdict is `revision-binding.json` from the verifier.

## Final gate before the single matrix
`scripts/preflight3_final_gate.py.txt` prints the expected-values block and `READY_FOR_THE_SINGLE_MATRIX` only when every identity is as expected and both qualifications are QUALIFIED. `data/preflight3-pre-qualification.json` is its output before the owner's qualification: every check true except the two qualifications.

## Qualification (owner-run, Ollama 0.40.14, 2026-11-05): `qualification/primary.json`, `qualification/fallback.json`
`kriya model status` (production config, clean shell) reports every role QUALIFIED for both models. The first attempt stopped at SEC-009 and was classified AUTHORITY APPROVAL REQUIRED, not a model failure: the owner's terminal still carried eight `KRIYA_*` home redirections from the 2026-11-01 Gate-E run, so the authority, qualification and certification stores resolved to the wrong place. They were cleared; the real `~/.kriya/authority` approval (25 fields, exact set digest) was CURRENT and nothing was approved. Finding: `compute_violations` labels `agent_llms.<role>` "unknown/future field" although the role has a deliberate dynamic classification (message only; registry row SEC009-AGENT-ROLE-REASON-TEXT-001, P3, no product change). The final gate (`scripts/preflight3_final_gate.py.txt`) passes every identity check; the PRD-035 attempt count remains zero.
