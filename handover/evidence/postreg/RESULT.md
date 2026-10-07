# POST-REG-R1 PRIMARY CODING PROFILE COMPARISON - STOPPED AFTER ARM A: FALSE NEGATIVE

Kriya 69465d3 (clean non-editable install `venv-postreg`, build info dirty=false, dependencies pinned identical to
venv-gr1), Graphify base 67f99bd, frozen goal / contract / acceptance / B3 / auto-skill (b4a91e1a). No model call
outside the arm's run. Arm B (qwen3.8) was prepared (workspace, config 6bed23be, view, SEC-009 943011b6) but NOT run:
the owner's stop rule fired on Arm A.

## Arm A - qwen3-coder (canonical qualified primary profile)

Profile (config postreg-a.yaml 7dc51dc9 = gr1-graphify.yaml 4ebd97b4 with paths only): qwen3-coder:30b-kriya-e52213655394
(artifact sha256:141e95d7...), ollama_native, temperature 0.7, reasoning false, num_ctx 32768, num_predict 16384,
top_p 0.8, top_k 20 (repeat_penalty 1.05 = served Modelfile default), small_native_tools, fallback llm_chain qwen3.6;
Planner qwen3.6; all other roles follow llm (primary placement, fingerprint 0769fe62). Every role identity QUALIFIED from
a read-only view (1c8936a7, b0ed2c61). REG-R1 smoke before the run: NO REGRESSION (217/217 PRE_EXISTING, 2 replays).

| Metric | Value |
|---|---|
| Run id | 20261007T071630-e21ca9b2 |
| Terminal | FAILURE - REGRESSION_UNATTRIBUTED (`stop_environment`), subtask s1 of 2, nothing applied |
| Wall | 431 s generate (+213 s analyze) |
| Model calls | 7: Planner 1, localization 1 (qwen3-coder), Developer 2, run_verifier 1, spec_compliance 1, reviewer 1 |
| Fallback calls | 0 |
| Auto-skill model calls | 0 (analyze made 0 LLM calls) |
| Attempt 1 | valid edits (2), REFUSED `diagnosis_mismatch` |
| Attempt 2 | valid edits (2), STAGED engine.py 1adbc4ba (+10), compile PASS |
| Malformed / anchor failures / no-ops | 0 / 0 / 0 |
| Developer prompt tokens (provider) | 5351, 4849 |
| Regression decision | per-test authority; 216 PRE_EXISTING, 1 STABILITY_UNRESOLVED (blocking) |
| Stability replays | 2 (same-context full suite, once per context) |
| REQ-1/2/3 | pending (terminal gates not reached) |
| M1 | VERIFIED, sealed, 110 records |
| Staged candidate (independent, post-freeze) | **external 5/5 (A-E PASS), reviewed acceptance 5/5, regression 76/76** |
| Final workspace | external 2/5 (nothing applied), regression 76/76 |
| Trajectory | candidate 1 (attempt 2): best external 5/5 |

## Why the correct candidate was rejected (MEASURED / TRACED)

- MEASURED: in this live run the same-context full-suite baseline observations of the pre-existing failing
  `test_ts_normalizer_scales_linearly_on_large_files` were FAIL (original), PASS (replay 1; observation digest
  d7038c64..., the passing observation), FAIL (replay 2). The candidate's own POST observation: FAIL AssertionError, the
  same outcome and type as the original baseline observation; only its message/body differed (they differ every run).
  In the earlier no-model smoke/replay runs the test failed in 9/9 full-suite runs: its outcome is load-dependent
  (INFERRED: the live run's replays ran while the model runtime was resident).
- TRACED (5c55630, owner's critical safety rule): `classify_baseline_observations` -> BASELINE_OUTCOME_UNSTABLE ->
  message/body UNRESOLVED -> STABILITY_UNRESOLVED (blocking, unattributed) -> REGRESSION_UNATTRIBUTED stop.
- Classification per the experiment's stop rule: **FALSE NEGATIVE / AUTHORIZATION DEFECT**. Kriya implemented the
  specified rule exactly; the rule itself rejects every candidate whenever an untouched pre-existing failure is
  outcome-flaky and its message differs, even when the candidate's observation of that test has the identical
  outcome and type as the original baseline observation. Not a false success: nothing was applied.

## Comparison

Not performed: Arm B was not run (stop rule). Profile comparison: INCONCLUSIVE.
