# LR-R1-P1 bounded live confirmation, and LV-2 closure

**Result:** **LV-2 CLOSED. P1 LIVE CONFIRMATION PASS.** One run, not rerun. **Date:** 2026-10-05.
**Code:** `bb0e29b` = P1 `ec24d78` + certification evidence `90ee863` + LV-2 `41192d9` cherry-picked. It was installed
from a clean clone into `~/kriya-m1-live/venv-p1` (`kriya version`: commit `bb0e29ba9…`, dirty false, provenance
embedded).
**Labels:** MEASURED, TRACED, INFERRED.
Evidence is in `handover/evidence/lr-r1-p1/live/`; the store is `~/kriya-m1-live/state/attempt-evidence/<run id>`
(not committed).

## 1. LV-2: Q4 for an attempt with no Developer call

- **Defect** (live validation, Stage-1 Run 2, attempts 2–4). No Developer call means no candidate can exist, yet Q4
  answered `NOT_RECORDED`. MEASURED.
- **Fix** (explain only, `kriya/core/attempt_evidence/explain.py`). Q4 is
  `NOT_APPLICABLE("no_model_call: no Developer request in this attempt")` when all of these hold:
  - the attempt has no Developer request;
  - it has no candidate record, no parse and no typed refusal;
  - nothing consumed a candidate: it is a verification-only attempt, or no validator gate ran.

  A validator gate in any other attempt ran on a candidate (a deterministic restoration, for example), so a missing
  record there stays `NOT_RECORDED`. No recorder, workflow, retry, fallback, candidate, RunRecord or DecisionLedger
  change.
- **Commits.** M1 lineage `41192d9` (fix + `tests/test_lr_r1_m1_lv2_q4.py`) and evidence `a3e3369`. Cherry-picked
  unchanged onto `feature/lr-r1-p1` as `bb0e29b`: the same two files, no other change.
- **Tests (MEASURED).** Seven tests in `tests/test_lr_r1_m1_lv2_q4.py`. Before the fix, three failed (the live shape,
  verification-only, a non-Developer call in the attempt). After it, all seven pass:

  | Case | Q4 |
  |---|---|
  | No Developer call (the live shape) | NOT_APPLICABLE(no_model_call) |
  | Verification-only attempt | NOT_APPLICABLE(no_model_call) |
  | A triage call only | NOT_APPLICABLE(no_model_call) |
  | Developer call made, candidate evidence missing | NOT_RECORDED |
  | No Developer call but a validator gate ran in an ordinary attempt | NOT_RECORDED |
  | Pre-dispatch typed refusal | `no_model_answer: OutputBudgetUnsatisfiableError` (unchanged) |
  | Staged / refused candidate | RECORDED (unchanged) |

- **Original symptom re-measured (MEASURED).** Re-explaining the real Stage-1 Run-2 store with the fix gives Q4
  `NOT_APPLICABLE(no_model_call)` for attempts 2–4. Attempt 1 and Run 1 are unchanged (RECORDED).
- **Mutants:** 6/6 killed (`handover/evidence/lr-r1-m1/lv2_q4_mutation_results.json`), each over all 236 M1 tests:
  - branch removed;
  - any model call counted as a Developer call;
  - Developer call ignored;
  - gate-consumed candidate ignored;
  - verification-only not recognised;
  - typed as NOT_RECORDED.
- **Re-runs (MEASURED).**
  - M1 lineage: all `test_lr_r1_m1_*` (the explain, T6 and I-2 equivalence tests) pass, 236 (campaign baseline).
  - P1 branch at `bb0e29b`: those 236 plus the P1 focused tests and reproducer, 278 passed.
  - Ruff clean; pylint exit 0.

## 2. P1 live confirmation

### Setup

- **SEC-009.** A fresh workspace copy `ws/p1live-lang-countwords` (commons-lang @ `5cba51c7e`, the Stage-1 Run-2 base;
  Maven seed copied to key `07841c6403920705`).
  - Config `config/p1live-lang-countwords.yaml` (sha256 `6197583a…`) is the v5 operator config with only
    `paths.skills/state/memory` changed (diff in `scripts/`).
  - Owner approval was run by the owner (`approve-p1live.sh`) and verified CURRENT for workspace `07841c64…`.
  - `~/.kriya/authority` was unchanged (209 files, none modified).
- **Goal:** identical to Stage-1 Run 2, "Add a public static method StringUtils.countWords(CharSequence cs) that
  returns the number of whitespace-separated words (Character.isWhitespace), returning 0 for null or empty/blank
  input, and add unit tests for it."

### Preflight (MEASURED, read-only, `p1_preflight.json`)

| | Primary `qwen3-coder:30b-kriya-e52213655394` | Fallback `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` |
|---|---|---|
| runtime digest | `0769fe62…3437` (exact) | `26cb2deb79ae43141f637b99510d292002f4174558d5a2b20d09c2d0a94bdfd3` (exact), **identical to Stage 1, so the fingerprint is unchanged** |
| qualification record key | `1c8936a7…` | `b0ed2c615189aaa7490c96f45672fff68392af0053e2f948eb661e8365a4d010` (current) |
| Developer qualification | QUALIFIED | **QUALIFIED** |
| declared profile | `explicit_primary`, small_native_tools | `unverified_conservative_default`, full_file, tools/JSON off |
| effective profile | `explicit_primary` (unchanged) | **`qualification_derived`**: `text_markers` (patch), native tools **false**, JSON true, multi-line JSON true, streaming true |
| derivation evidence | none | mapping `qualification-derived/1`, role developer, settings `sha256:482067b2…`, policy `sha256:d34ab460…`; `anchored_edit_protocol` PASS → patch proven; native tool cases UNAVAILABLE → not proven |

### Run `20261005T151242-6123e867` (MEASURED)

| Attempt | Mode | Model | Outcome |
|---|---|---|---|
| 1 | full_set | primary | anchored edit refused, `ANCHOR_NOT_IN_FILE` |
| 2 | targeted | (none) | `ANCHOR_CONTEXT_NOT_ESCALATED`, no model call |
| 3 | targeted | (none) | `ANCHOR_CONTEXT_NOT_ESCALATED`, then `fallback_targeted` |
| 4 | fallback_targeted | **qwen3.6** | selected at escalation **and at the call, no rejection**; an anchored patch, **STAGED** +26/−0 on `StringUtils.java`; compile PASS; tests FAIL → `REGRESSION_UNATTRIBUTED` (stop_environment) |

- **Same trajectory as Stage 1 up to the fallback.** Before P1, the same point ended in a call-time rejection,
  "returns whole files only", followed by terminal `FALLBACK_MODEL_INCOMPATIBLE`. With P1 the fallback served the
  patch-only attempt.
- **The executed request profile** (trace `model.transition`, attempt 4): qwen3.6, `capability_source
  qualification_derived`, `edit_protocol text_markers`, `native_tool_calls false`, `json_mode true`, `qualification
  QUALIFIED`, runtime `26cb2deb…`.
- **Patch-only determination: PATCH_ONLY_PROVEN.**
  - `StringUtils.java` is 402,131 bytes, so its lower bound is 50,267 tokens, against qwen3.6's prompt room of
    16,096 tokens.
  - Candidate status: COMPATIBLE, so it was selected; it is the only candidate considered.
  - This is recomputed with the same resolver at the same base (`routing_recomputed.json`, TRACED). The store has no
    routing record because nothing was excluded (see P1-L2).
- **Q8.**
  - Attempt 4: escalation, requested = selected = qwen3.6, `patch_rejected: []`.
  - Call: fallback true, `requested_rejection: []`, selected qwen3.6.
  - Attempts 1–3: the call-phase decision selected the primary.
- **Q9.** Terminal FAILURE, `failure_category regression_unattributed`, NOT_COMMITTED; last recovery decision
  `stop_environment` (REGRESSION_UNATTRIBUTED); `last_fallback_routing: null` (no routing decision was needed).
- **Q4 (the LV-2 fix, live).** Attempts 2–3: NOT_APPLICABLE(no_model_call). Attempts 1 and 4: RECORDED.
- **Calls and timing.**
  - 8 model calls, every one `exact: true` and on settings `sha256:482067b2…`:
    - qwen3.6: Planner ×2 and Developer ×1;
    - qwen3-coder: localization, Developer, run verifier, spec compliance and reviewer, ×1 each.
  - Wall 1032.4 s; model 175.4 s; recorder 0.218 s = 0.021 % of wall, 0.025 % of non-model.
  - `_record_candidate_change` 32.0 ms: the 402 KB file, live. That is the first live measurement of M-1's
    large-file path, close to the offline 20–35 ms.
  - `close_run` 0.48 ms; `prune_after_run` 17.1 ms (4 stores).
- **Evidence.** 153 records, 904 KiB, VERIFIED and sealed; no blank answers.
- **Outcome classification.** The terminal `REGRESSION_UNATTRIBUTED` is a verification-policy outcome on a staged
  candidate (the same class as Stage-1 Run 1). It is a genuine gate failure, not a P1 matter. Its cause is not
  investigated here (UNKNOWN), and the run was not rerun.

## 3. Findings (LOW, observability; not correctness, not fixed)

| ID | Finding | Status |
|---|---|---|
| P1-L1 | At run start the log line "Model capability profile resolved [… qwen3.6 …, Source: unverified_conservative_default …]" is the DECLARED profile, looked up for the fingerprint's protocol identity: `declared_capability_profile` logs through `_log_resolution_once` (TRACED). The effective `qualification_derived` resolution is logged later, and the executed profile is recorded in `model.transition`. An operator reading only the first line could be misled | reported |
| P1-L2 | A `PATCH_ONLY_PROVEN` determination is recorded (`fallback.decision` phase `routing`, `model.fallback_routing`) only when some candidate is excluded. When every candidate is compatible, the determination is not in the store (recomputed here) | reported |
| LV-1 | (unchanged) unquoted YAML `off` documentation trap | open, not in scope |

## 4. Status

```text
LV-2
CLOSED

M1
CERTIFIED

P1 DETERMINISTIC CERTIFICATION
PASS

P1 LIVE CONFIRMATION
PASS

P1 OPEN CORRECTNESS DEFECTS
0

READY FOR P4
YES
```
