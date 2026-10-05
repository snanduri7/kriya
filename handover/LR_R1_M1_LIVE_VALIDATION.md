# LR-R1-M1 bounded live validation (Stage 1)

**Result: PASS.** No recorder or explain correctness defect was found. Two runs were executed; the optional capture-off
control was NOT_RUN (harness config error, §5).
**Code under test:** certified executable `ceb9943` (`kriya version`: commit `ceb99435e`, dirty false), installed from
a clean clone into `~/kriya-m1-live/venv`. Nothing in `kriya/` was changed for this validation.
**Date:** 2026-10-05.
**Labels:** MEASURED, TRACED, INFERRED.

Evidence (this commit): `handover/evidence/lr-r1-m1/live/`. Per run it holds goal, run id, wall time, shim timing,
`summary.json`, `verify`/`show`/`explain` JSON and store listings. It also holds the M-1/M-2 offline measurements,
every script, and the config diffs against the operator file. The stores themselves stay in
`~/kriya-m1-live/state/attempt-evidence/` (not committed: full prompt content).

## 1. Setup

- **Configuration.**
  - Each run used the v5 operator config (`~/.kriya/operator/provider-contract-v5-production.yaml`, sha256 `eec4072a…`,
    unchanged).
  - Only `paths.skills`, `paths.state` and `paths.memory` were changed (diffs in `scripts/*.config.diff`).
  - Capture was the default, `full`.
- **SEC-009.**
  - Digest-bound approvals live in `~/kriya-m1-live/authority`; all four were CURRENT for their exact configs before
    any run (`kriya authority inspect`).
  - `~/.kriya/authority` was unchanged: 209 files, none modified after 2026-10-03.
- **Isolation.**
  - State, logs, authority, static-analysis and MCP-approval homes all point under `~/kriya-m1-live` (`env.sh`).
  - Qualification records are read from the default (read-only) home.
- **Models (MEASURED from `model.result`).** Every call was `status OK`, runtime `exact: true`, and every role shared
  inference settings `sha256:482067b2e0b4d3b7d4ebdfbf83ef8aeef79c3f07ec9d3b0e2e38298eb74a3b15`.

  | Role | Model | Runtime |
  |---|---|---|
  | Planner | `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` | `26cb2deb79ae43141f637b99510d292002f4174558d5a2b20d09c2d0a94bdfd3` |
  | Developer, localization, run verifier, spec compliance, reviewer | `qwen3-coder:30b-kriya-e52213655394` | `0769fe6214918434eeb7658144e6821c2d936b9b20c1ef3c2d4de03000363437` |
  | Developer fallback (`llm_chain[0]`) | the qwen3.6 tag above | same as the Planner |

- **Timing harness** (`scripts/sitecustomize.py`, loaded only into the measured `generate` processes through
  `PYTHONPATH`):
  - It wraps the recorder's entry points (`attempt_evidence.scope.*`) and the call-site recording helpers with
    perf_counter accumulators.
  - It counts only the outermost recorder frame per thread.
  - Self-test: every target was wrapped; the `missing_targets` list is empty in both runs.
  - **Not timed:** the generator context managers `run_scope`, `unit_scope`, `attempt_scope`, `tool_unit`,
    `call_scope` and `wire_scope`. Their bodies are the run itself; their own open/close records are emitted through
    `emit` and are therefore counted.
- **Model time** is the sum of `model.result.elapsed_seconds` read from the store. **Wall time** is the `generate`
  process (the analyze step is excluded).

## 2. Runs

### Run 1: known-success task (httpx)

| Field | Value |
|---|---|
| run id | `20261005T132321-13cbc1d7` |
| task | Add `codes.is_valid(value)` to `httpx/_status_codes.py` plus tests (workspace `python-error-invalidurl` @ `3de191518`; a new goal) |
| capture | full |
| wall / model / non-model | 154.21 s / 90.47 s (7 calls) / 63.74 s |
| recorder | 0.0900 s = **0.058 % of wall**, **0.141 % of non-model** |
| `_record_candidate_change` | 1 call, 2.6 ms (5.6 KB file, +11 lines) |
| close / retention | `close_run` 0.47 ms; `prune_after_run` 3.42 ms (2 stores in the root) |
| records / store | 79 records; 70 files, 472 KiB by `du` right after the run |
| terminal | FAILURE, `failure_category regression_unattributed` (`stop_environment`, REGRESSION_UNATTRIBUTED), NOT_COMMITTED |
| verify | VERIFIED, sealed |

Q1–Q8 (one attempt): Q1–Q6 and Q8 are RECORDED. Q7 is NOT_APPLICABLE: "no later attempt in this unit invocation". Q9
is RECORDED. No blank answers.

### Run 2: retry/failure-shaped brownfield task (commons-lang, 402 KB target)

| Field | Value |
|---|---|
| run id | `20261005T133256-87d73817` |
| task | Add `StringUtils.countWords(CharSequence)` plus unit tests (commons-lang @ `5cba51c7e`; a new goal) |
| capture | full |
| wall / model / non-model | 555.45 s / 104.46 s (4 calls) / 451.0 s |
| recorder | 0.1383 s = **0.025 % of wall**, **0.031 % of non-model** |
| `_record_candidate_change` | 0 calls: no candidate was ever staged (the only `candidate.change` is a REFUSED record from `scope.py:920`) |
| close / retention | `close_run` 0.46 ms; `prune_after_run` 8.63 ms (3 stores) |
| records / store | 122 records; 89 files, 624 KiB by `du` |
| terminal | FAILURE, `failure_category fallback_model_incompatible`, NOT_COMMITTED |
| verify | VERIFIED, sealed |

Trajectory (MEASURED from the store; TRACED against the run log):

| Attempt | Mode | Outcome |
|---|---|---|
| 1 | full-set | anchored edit on `StringUtils.java` refused (`ANCHOR_NOT_IN_FILE`, MEASURED) |
| 2 | targeted | `ANCHOR_CONTEXT_NOT_ESCALATED` (no model call) |
| 3 | targeted | `ANCHOR_CONTEXT_NOT_ESCALATED` (no model call), then `REPEATED_ACTION` → `fallback_targeted` |
| 4 | fallback_targeted | Q8: escalation selected qwen3.6; at the call phase it was rejected (whole files only for a patch-only target) with `selected: null` → terminal `FALLBACK_MODEL_INCOMPATIBLE` |

This is exactly the live P1 mechanism of `handover/LR_R1_P1_FALLBACK_INCOMPATIBILITY_INVESTIGATION.md`, so the
fallback path was exercised.

Answers across its four attempts:
- Attempt 1: Q1–Q8 RECORDED.
- Attempts 2–3:
  - Q1 and Q3 are NOT_APPLICABLE ("no_model_call").
  - Q4 is NOT_RECORDED ("no candidate was staged or refused in this attempt").
  - Q2 and Q5–Q8 are RECORDED.
- Attempt 4:
  - Q1–Q3 are NOT_APPLICABLE ("no_model_call").
  - Q4 is NOT_RECORDED (same reason).
  - Q7 is NOT_APPLICABLE ("no later attempt").
  - Q5, Q6 and Q8 are RECORDED.
- Q9 is RECORDED, with `terminal_cause.failure_category = fallback_model_incompatible` and the last recovery decision
  `stop_environment`.
- No answer is blank.

### Run 3

NOT_RUN. It was conditional on runs 1–2 not exercising the fallback; Run 2 exercised it.

### Every NOT_RECORDED value and its reason

| Run | Attempt | Q | Reason |
|---|---|---|---|
| 2 | 2, 3, 4 | Q4 | "no candidate was staged or refused in this attempt" (true: those attempts were refused before any Developer call) |

There are no other NOT_RECORDED values.

## 3. Live overhead (MEASURED)

| Run | Wall | Recorder | % wall | % non-model |
|---|---|---|---|---|
| 1 | 154.2 s | 0.090 s | 0.058 % | 0.141 % |
| 2 | 555.5 s | 0.138 s | 0.025 % | 0.031 % |

- **Biggest recorder items (Run 1):** `mirror_event` 23 ms over 27 calls; `record_wire_response` 16 ms and
  `record_wire_request` 16 ms over 7 calls each; `_record_gate_result` 10 ms.
- **Caveat on the boundary:** these two runs measure overhead; they say nothing about variance. The context-manager
  scopes listed in §1 are not individually timed.

## 4. M-1 and M-2

### M-1: candidate diff construction (`_record_candidate_change`)

- **Live (MEASURED):** 2.6 ms for one 5.6 KB file in Run 1. In Run 2 the 402 KB file was never staged, so the
  large-file live path is NOT_EXERCISED, and per the rules it was not rerun.
- **Offline (MEASURED, `m1_offline.json`, labelled as such):** the real `attempt._record_candidate_change` at
  ceb9943 with `emit` stubbed, on the real `StringUtils.java` (402,131 bytes, 9,384 lines):

  | Case | Construction (median) | gzip of the three blobs | Diff size |
  |---|---|---|---|
  | 30-line insert mid-file | 9.9 ms | 11.0 ms | 1,191 bytes |
  | Every line changed | 9.1 ms | 22.4 ms | 832,516 bytes |

- **Assessment (INFERRED from these measurements):** about 20–35 ms per staged large file per attempt is about
  0.005 % of Run 2's wall time. M-1 stays a correct MEDIUM code-quality finding: it is synchronous and runs even in
  `digest_only`. Its measured cost does not justify an optimization now. Not optimized, as instructed.

### M-2: retention scan at close (`prune_after_run` → `prune_evidence`)

- **Live (MEASURED):** 3.4 ms with 2 stores and 8.6 ms with 3 stores.
- **Offline scaling (MEASURED, `m2_scaling.json`, labelled as such):** the real `prune_evidence` at the packaged
  defaults (keep_runs 200, 5 GiB), dry run, over N copies of the two live stores:

  | N | 1 | 10 | 50 | 100 | 200 | 400 |
  |---|---|---|---|---|---|---|
  | median (s) | 0.0018 | 0.018 | 0.099 | 0.205 | 0.438 | 1.401 |

- **Shape:** linear at about 2.2 ms per store up to 200 stores. At 400 it rises to about 3.5 ms per store, a
  page-cache effect (INFERRED).
- **Steady state:** at the default cap of about 200 retained stores, each run close costs about 0.44 s, under the
  workspace lock (INFERRED from the scan cost plus `keep_runs`). That is 0.08–0.28 % of the two runs' wall time.
- **Assessment:** M-2 stays MEDIUM. The cost is bounded by `keep_runs` and small next to a run. Not optimized, as
  instructed.

## 5. New findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| LV-1 | LOW (usability/docs) | YAML 1.1 parses an unquoted `capture: off` as boolean `False`. The literal rejects it, so config load fails closed with `Input should be 'full', 'digest_only', 'full_with_reasoning' or 'off'`. `docs/user_guide.md:903` lists the value unquoted (`off`). This is why the authorized capture-off control did not run (my harness config wrote it unquoted). Re-quoting it would change the approved config digest, so per the approval rule it was not changed. No model call was made and nothing was written (MEASURED: `ctrl.driver.txt`) | recorded, not fixed (no M1 change during validation) |
| LV-2 | LOW (explain typing) | In Run 2, attempts that made no Developer call report Q4 as NOT_RECORDED rather than NOT_APPLICABLE. The reason is accurate and non-blank (invariant I-3 holds), and Q1/Q3 in the same attempts are typed NOT_APPLICABLE | recorded, not fixed |

- **Evidence-integrity defects:** none. Both stores are VERIFIED and sealed, and every explain answer agrees with the
  run log (TRACED).
- **Product outcomes (not M1):**
  - Run 1's `REGRESSION_UNATTRIBUTED` stop is a verification-policy outcome on a goal the spec verifier judged met.
    Its cause is not investigated here; classification is UNKNOWN (model/environment/test-suite). It was not rerun.
  - Run 2's terminal is the confirmed P1 defect.

## 6. Capture-off control

NOT_RUN (LV-1). No coarse comparison is available. The recorder's measured share (≤ 0.141 % of non-model time) is
the only overhead figure. It is not a statistical estimate.
