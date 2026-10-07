# qwen3.8 PRIMARY CODING MODEL PROFILE COMPARISON - the single authorized Graphify run (evidence only)

Run `20261006T231821-fb31caad`, Kriya a049c02 (venv-gr1), config `qwen38-matched-developer.yaml` 7e595207, fresh workspace
`ws-qwen38/gr1-graphify` @ 67f99bd, qualification view `qual-view-gr1q-v2` (read-only, 3 exact copies), SEC-009 fd515e75.
Pre-run gate: ALL PASS (run/prerun_check.txt). Baseline: run `20261006T204410-5388017f` (qwen3-coder), not rerun.

## Measured
| | qwen3-coder baseline | qwen3.8 candidate |
|---|---|---|
| Terminal | FAILURE, quality_gates_exhausted | FAILURE, regression_unattributed (stop_environment) |
| Attempts | 9 | 2 |
| Wall (generate) | 926 s | 465 s |
| Model calls total | 20 | 7 |
| Developer calls | 11 (2 file-list) | 2 |
| Localization calls | 1 (qwen3-coder) | 1 (qwen3.8) |
| Fallback (qwen3.6) Developer calls | 4 | 0 |
| Valid / malformed Developer responses | 7 edits+1 no_change / 2 MODEL_EDIT_PROTOCOL_INVALID | 1 / 1 INVALID_EDIT_PROTOCOL (`<<<KRIYA:SEARCH>>>`) |
| Anchor failures | 2 ANCHOR_NOT_IN_FILE | 0 |
| No-op edits | 2 | 0 |
| Other refusals | 1 diagnosis_mismatch | 0 |
| Stageable candidates | 2 (same engine.py 8a0f23e9) | 1 (engine.py e47eacba, +8/-1) |
| Deterministic regression | 4 newly failing tests (attributed) | 0 new failures (level 2: 217/217 PRE_EXISTING); level 1 CHANGED_FAILURE blocked |
| Developer prompt tokens (provider) | 3,561-8,111 | 4,786-4,983 |
| Best candidate external | 0/5 (crash) | 3/5 (B fixed; A, E fail) |
| Best candidate reviewed acceptance | 0/5 | 3/5 |
| Best candidate regression 76 / generic_args | 76/76 / 2/6 | 76/76 / 6/6 |
| Workspace after run, external | 2/5 | 2/5 (nothing applied) |
| REQ-1/2/3 | pending (NOT_YET_VERIFIED) | pending (NOT_YET_VERIFIED) |

## OBS-4 (new; reported, not fixed - Kriya changes not authorized)
- MEASURED: candidate full suite = baseline status counts (217 failed / 4896 passed / 257 skipped); level-2 per-test delta =
  217 x PRE_EXISTING_FAILURE, aggregate_drop false; level1 = CHANGED_FAILURE -> blocking -> REGRESSION_UNATTRIBUTED ->
  recovery `stop_environment` after attempt 2 (of up to 9).
- TRACED (a049c02 `validation_baseline.build_validation_outcome`/`classify_baseline_delta`): a non-Maven run's level-1
  fingerprint hashes the WHOLE pytest output (887 KB) after narrow volatile-token stripping; Maven uses the Surefire
  summary (comparison v2). A level-1 change blocks even when level 2 is available and clean.
- UNKNOWN: which bytes differed (raw outputs not persisted). Discriminating check proposed: two pristine-base full-suite
  runs in the same contained toolchain, compare normalized outputs.
- Effect on this comparison: truncated qwen3.8's repair loop (confound for repair effectiveness). Not a false negative:
  the blocked candidate is externally 3/5.
