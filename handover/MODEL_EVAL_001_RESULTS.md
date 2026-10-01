# MODEL-EVAL-001: results (user-run, 2026-09-26)

**Status:** COMPLETE: 9/9 timed runs plus qualification. The experiment design was APPROVED as-is (user review, 2026-09-26). **Recommendation: no default or routing change.** Packaged defaults are unchanged.

**Gate (user review):** no packaged-default or production-routing change based on MODEL-EVAL-001 until **MODEL-QUAL-IDENTITY-001** (`DEFECT_MODEL_QUAL_IDENTITY_001.md`) is fixed and the affected qualification records are revalidated.

## Exact runtime identity per arm (all `exact: true`, Ollama 0.34.2, GGUF Q4_K_M, 32K)

| arm | runtime digest | Ollama artifact (image) | weights blob |
|---|---|---|---|
| qwen3-coder:30b | `ea90552d45f9c181a06512eb628adf89e138f25ce58b38ff54c0789e58264276` | `sha256:06c1097efce0431c2045fe7b2e5108366e43bee1b4603a7aded8f21689e90bca` | `sha256:1194192cf2a187eb02722edcc3f77b11d21f537048ce04b67ccf8ba78863006a` |
| qwen3.6:35b-a3b-q4_K_M | `cc523e4c9b6508b99e24b8bc9b8842946fd7c5285fcb3a4a5b0d69adb24c7e6f` | `sha256:07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522` | `sha256:f5ee307a2982106a6eb82b62b2c00b575c9072145a759ae4660378acda8dcf2d` |
| qwen3.8:27b | `3b84735255dfe12988bf0a55bdf5554d11365d0de04f256508dd1e0592ab5e29` | `sha256:22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643` (= image ID `22130167c4c2`, the intended Q4_K_M image) | `sha256:f5f1dd8920d417aac2718b0bda3403da274301efdd6760b4f0f4b864ff2ad57d` |

qwen3.8 at 64K is a separate runtime (`2241d06be2ee…`), used for qualification only.

**Evidence:** `~/kriya-live-demo/demo-03-brownfield/model-eval-001/evidence/` (`comparison.md`/`.json`, `qualification/`, `runs/<arm>/run-N/`). The settings are described in `MODEL_EVAL_001_PREP.md`: 32K, production profile, no fallback, `reasoning_effort: none` on the thinking models.

## Correction to the first report (my bug, fixed and re-extracted)

The first `comparison.md` merged rows across runs, so its model-call times and token counts were wrong: each arm listed all three runtimes, and model-call time exceeded workflow time.

- **Cause:** `extract-run.py` used the `traces.db` row count as its baseline, but each run row is inserted and then replaced, so rowids advance by 2 per run.
- **Fix:** select rows by time window. My first attempt at the fix mis-indented the event collection, which I caught on the next check.
- **Re-extraction:** all 9 runs were re-extracted offline. The diff grades were kept from run time, because the workspace has been reset since.
- **Unaffected:** wall-clock time, diff grades and the independent re-checks never depended on these rows.
- **Consistency check after the fix:**
  - each run has exactly one model;
  - model-call time is below workflow time;
  - the per-run model-call times sum to the same 1930.7 s total.

## Qualification (setup evidence, exact runtimes)

| runtime | result |
|---|---|
| qwen3-coder:30b @32K (`ea90552d45f9`) | 18 PASS, 1 UNAVAILABLE (endpoint restart) |
| qwen3.6:35b-a3b @32K (`cc523e4c9b65`), `reasoning_effort: none` | 18 PASS, 1 UNAVAILABLE. **Qualifies now.** Before, it failed on budget exhaustion from hidden reasoning. `reasoning_observed: false`. |
| qwen3.8:27b @32K (`3b84735255df`), `reasoning_effort: none` | 18 PASS, 1 UNAVAILABLE |
| qwen3.8:27b @64K (`2241d06be2ee`) | 17 PASS, **context_capacity FAIL**: the near-window request timed out after 613 s (`APITimeoutError`). It cannot serve 64K within Kriya's request bound on this machine, so the 64K tier is **not qualified**. |

## Timed A/B: demo-03, 3 runs per arm

| | qwen3-coder:30b | qwen3.6:35b-a3b | qwen3.8:27b |
|---|---|---|---|
| SUCCESS (Kriya + correct diff + independent 53/53) | **3/3** | **1/3** | **3/3** |
| Diff grades | 3× CORRECT_MINIMAL | 1× CORRECT_MINIMAL, 2× NO_CHANGE | 3× CORRECT_MINIMAL |
| Developer first attempt / retries | 3/3, 0 | 2/2 attempted, 0 | 3/3, 0 |
| Protocol, schema or gate failures in role metrics | 0 | 0 (see findings 2–3) | 0 |
| Median model-call time | **81 s** | 81 s | 510 s |
| Median workflow time | **231 s** | 317 s | 784 s (3.4×) |
| Developer s/call, output tok/s | 20.6 s, 38.3 | 28.7 s, 30.5 | 106.5 s, 8.2 |
| Planner s/call | 28.4 | 32.8 | 173.6 |
| Reviewer s/call | 10.6 | 18.8 | 133.2 |
| spec_compliance s/call | 3.1 | 4.7 | 25.4 |

**qwen3.6 failures.** Kriya's gates caught both; nothing was applied.

- **Run 1:** code was generated, but the verifier returned no usable per-requirement verdict. All four REQs were UNKNOWN, and production blocks on UNKNOWN, so the result was `REQUIREMENTS_UNRESOLVED` and the candidate was not applied. The mutation-scope evidence itself was clean: only the target file was touched.
- **Run 3:** the Planner produced an unsafe structured plan three times (`VERIFICATION_EVIDENCE_PATH_MISSING` ×2, `STRUCTURED_PLAN_SCHEMA_INVALID`), so plan repair ran out.

## Verdict in the review's priority order (qwen3.8 against each baseline)

| priority | vs qwen3-coder | vs qwen3.6 |
|---|---|---|
| 1. correctness/completion | 3/3 vs 3/3: **equal** | 3/3 vs 1/3: **better** |
| 2. false success / protocol failures | 0/0 vs 0/0: equal | 0/0 vs 0/0: equal |
| 3. first-attempt success | 3 vs 3: equal | 3 vs 2: better |
| 4. retries | 0 vs 0: equal | 0 vs 0: equal |
| 5. total workflow time (median) | 784 s vs 231 s: **3.4× worse** | 784 s vs 317 s: 2.5× worse |
| 6. tokens/latency (model-call median) | 510 s vs 81 s: worse | 510 s vs 81 s: worse |

False success means Kriya reported success but the diff grade or the independent re-check disagreed. There were none in any arm.

**Architect promotion evidence = INSUFFICIENT.** demo-03 runs in enforce mode, which makes no Architect call, so the Architect has qualification evidence only. That would change only if a separate real Architect task were run.

## Against the promotion rule, per role

- **qwen3.8 vs qwen3-coder** (the actual default for every role):
  - Correctness is equal: 3/3, first attempt, no retries, no protocol failures.
  - It is 5–8× slower per call in every role and 3.4× slower end to end.
  - **There is no measurable advantage in any role, so the rule is not met. Keep qwen3-coder.**
- **qwen3.8 vs qwen3.6** (the fallback slot):
  - qwen3.8 completed 3/3 against 1/3. But qwen3.6's two failures were in the **Planner and verifier roles**.
  - The fallback slot is only used for Developer escalation, and no run needed a Developer retry.
  - So this A/B **does not test the fallback role**; do not swap the fallback on this evidence.
  - For production, the practical point is that qwen3.6 now **qualifies** with `reasoning_effort: none`. The demo-03 fallback config needs that setting (re-approval required), and finding 1 should be fixed first.
- **Planner/Reviewer/verifier:** qwen3.8 is correct but far slower, with no advantage. qwen3.6's Planner and verifier failed once each out of 3, which is weak evidence against routing those roles to qwen3.6.
- **Limits of this evidence:**
  - n = 3 on one easy task; no run exercised repair.
  - No Architect call happens in enforce mode, so the Architect comparison comes from qualification only: all three PASS on structured and multiline JSON.

## Follow-up: MODEL-EVIDENCE-HARDENING-001 (2026-09-26, VERIFIED)

- **The gate is closed.** MODEL-QUAL-IDENTITY-001 and findings 2–4 are VERIFIED (full pytest green), and the qualification records are revalidated.
- **All three arms re-qualify at 32K** under their exact executed inference identities and the M1 Max environment:
  - qwen3-coder `ca28639b8c1b`;
  - qwen3.6 `853402b9ba78` (with `reasoning_effort: none`);
  - qwen3.8 `ec37fc4f0381`.

  All are QUALIFIED, 18 PASS / 1 UNAVAILABLE each. Evidence: `evidence/requalification/`.
- **The recommendation is unchanged:** no default or routing change.
- **Next:** changing the demo-03 fallback to `reasoning_effort: none` can now be proposed separately, since qwen3.6 is qualified under that exact setting.

### Original follow-up notes

- **Fixes.** Findings 1–4 below are fixed in `6c163f2` (identity) and `596bfbe` (telemetry). See `MODEL_EVIDENCE_HARDENING_001.md`.
- **What this means for the recorded results:**
  - They stand as recorded, and the 9 runs were not re-extracted.
  - The extractor's row selection moved into the bundle's `trace_window.py`, with a regression test (`test_trace_window.py`).
  - Its tightened window selects the same rows for all 9 runs.
- **qwen3.8:27b @64K is NOT_QUALIFIED** for this exact runtime (`2241d06be2ee…`), this hardware (M1 Max) and this config (Ollama 0.34.2, `reasoning_effort: none`): context_capacity timed out at 613 s. Under policy /3 that record reads STALE; it is not re-qualified to make it pass, and it still keeps 65536 out of PRD-016 adaptive selection (tested).
- **Revalidation** (user-run, after pytest is green): `./setup.sh requalify`, all three arms at 32K.
- **Still gated.** No packaged-default or routing change is based on this campaign until both items are VERIFIED and the requalification is recorded.

## Kriya findings from this campaign (fixed by MODEL-EVIDENCE-HARDENING-001, pending verification)

1. **PRD-013 identity gap** (reported earlier). `reasoning_effort` and the sampling options are not part of the runtime fingerprint. The qualification that qwen3.6 now passes with `none` would also show QUALIFIED for a config without it, which reasons by default and fails.
2. **A run that fails in planning leaves no metrics.** Enforce qwen3.6 run 3 wrote no `traces.db` runs row, so its three Planner calls appear nowhere in the role metrics (model-call 0 s, Planner calls under-counted). This is a PRD-018 completeness gap.
3. **Structured-plan failures are not counted as Planner schema failures.** `record_schema_failure` is only called from `agent.py`'s escalation path. The enforce controller's `STRUCTURED_PLAN_SCHEMA_INVALID` does not count, so the Planner's `schema_failures` under-reports, and PRD-019 routing ranks on that value.
4. **Why a requirement ended UNKNOWN is not recorded.** The enforce terminal verifier persists neither the raw verdict nor the reason. Run 1's all-UNKNOWN outcome cannot be diagnosed beyond "no usable per-id verdict".
