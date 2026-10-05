# LR-R1 post-P5 small live success validation

**Question:** can current Kriya (M1 + P1 + LV-2 + P4 + P5) complete useful real tasks end to end?
**Answer from this validation: 0 of 5 succeeded. No false success. All five evidence stores VERIFIED.**

**Important:** the task selection was confounded by my own error (§3). Every workspace started from a non-green test
baseline. The result is truthful, but it is not a clean measure of Kriya's end-to-end capability.

**Code:** `feature/lr-r1-p5` @ `6530138`, installed from a clean clone (`venv-p5`; dirty false, provenance embedded).
**Models (v5 operator config, unchanged):**

| Role | Model |
|---|---|
| Developer, localization, verifiers, reviewer | `qwen3-coder:30b-kriya-e52213655394` |
| Planner, Developer fallback | `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a` |

**Labels:** MEASURED, TRACED, INFERRED.

## 1. Protocol

- **Task selection.** Recorded and committed **before any model run**: `7b10780`,
  `handover/evidence/lr-r1-post-p5/TASK_SELECTION.md`.
  - 2 Python, 2 Java, 1 Spring Boot.
  - Each feature was confirmed absent and its test file present.
  - None comes from the frozen CAGC matrix or earlier LR-R1 runs.
- **Execution.** Each task ran once, in the recorded order. The goal was passed verbatim from the selection file.
  - No rerun, no task change, no gate change, no manual repair.
- **Workspaces and SEC-009.** Fresh workspace copies; each config is the v5 operator config with only
  `paths.skills/state/memory` changed. The owner ran the approvals; I verified all five CURRENT for their exact
  digests, and `~/.kriya/authority` and the operator file were unchanged.
- **Harness.** Evidence was collected with the Stage-1 `run.sh` (analyze, generate under the timing shim, evidence
  verify/show/explain).

## 2. Outcomes (all MEASURED from the evidence stores, `summary.json` per task)

| Task | Lang / framework | Units | Attempts | Model calls | Fallback used | P4 avoided retry | P5 relationship | Terminal | Failure category | Wall s | Model s | Recorder s | Applied | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 httpx `Headers.count` | Python | s1 | 1 | 7 | no | N/A (no verification-only unit) | none in plan | FAILURE | `regression_unattributed` | 184.9 | 122.3 | 0.117 | no | VERIFIED |
| T2 more-itertools `count_unique` | Python | s1, s2 | 5 | 14 | no | N/A | none in plan | FAILURE | `unauthorized_generation_target` (`NO_AUTHORIZED_REPAIR_TARGET`) | 452.5 | 327.5 | 0.377 | no | VERIFIED |
| T3 commons-lang `CharUtils.isAsciiWhitespace` | Java | s1, s2 | 14 | 24 | **yes**: qwen3.6 served 2 `fallback_targeted` attempts, no rejection | N/A | none in plan | FAILURE | `quality_gates_exhausted` | 1520.1 | 594.9 | 0.474 | no | VERIFIED |
| T4 commons-lang `BooleanUtils.countTrue` | Java | s1 | 1 | 3 | no | N/A | none in plan | FAILURE | `context_edit_protocol_unsatisfiable` | 481.8 | 71.3 | 0.086 | no | VERIFIED |
| T5 petclinic `Owner.getPetCount` | Spring Boot | s1, s2 | 2 | 13 | no | N/A | none in plan | FAILURE | `regression_unattributed` | 382.1 | 151.2 | 0.181 | no | VERIFIED |

- **Deterministic verification and judge result.** No candidate passed Kriya's gates, so nothing was applied. A judge
  verdict on candidate correctness was not made (UNKNOWN). No run reported success, so there is **no false success**.
- **Recorder share of non-model time:** 0.021–0.302 %.

**Trajectories (MEASURED, evidence store):**
- **T1.** One attempt staged the change. Then the full regression suite's aggregate changed against the
  PRE-mutation baseline with no attributable test: `REGRESSION_UNATTRIBUTED`.
- **T2.**
  - s1 wrote `more.py` (+21 lines) and passed.
  - s2's tests failed: attempt 1 tests, attempt 2 anchored edit refused, attempt 3 tests.
  - Attempt 4: attribution grounded the failure to `more_itertools/more.py`, s1's file, outside s2's write scope.
  - The typed stop `NO_AUTHORIZED_REPAIR_TARGET` followed.
- **T3.**
  - s1 succeeded on its third attempt, after two anchored-edit refusals.
  - s2 (the tests) used 11 attempts:
    - anchored edits refused 4× (`ANCHOR_NOT_IN_FILE`);
    - `ANCHOR_CONTEXT_NOT_ESCALATED` ×2;
    - `structural_corruption` ×1;
    - candidates failing spec compliance or tests.
  - The budget was exhausted on a test failure.
- **T4.** Attempt 1 was refused before any Developer inference: `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`, meaning no
  feasible mutation under the authoritative context for `BooleanUtils.java` (47 KB).
- **T5.**
  - s1 `Owner.java` compiled.
  - s2 `OwnerTests` compiled and its target tests passed.
  - The full regression comparison then gave `REGRESSION_UNATTRIBUTED`.

## 3. Confound: every workspace started from a non-green test baseline (my selection error)

- **What the bases contain (MEASURED).** The PRE-mutation baseline gate recorded by each run failed before any change:

  | Task | Baseline |
  |---|---|
  | T1 httpx | 15 failed / 1414 passed |
  | T2 more-itertools | 1 failed (`ValueChainTests::test_type_error_while_iterating`) / 741 passed |
  | T3, T4 commons-lang | 1 failure / 21,868 |
  | T5 petclinic | 1 error / 80 |

  TRACED: the base commits are the CAGC benchmark task bases ("benchmark task base: X^ + the commit's tests"). They
  carry the original benchmark task's tests, which fail by design.
- **Why I used them.** For their working local build environments and Maven seeds. I did not check their baselines.
  That is a validation-design defect on my side, not a Kriya defect.
- **Kriya's handling was correct (MEASURED).** It treated the pre-existing failures as pre-existing: the producer
  units in T2 and T5 passed with the baseline-identical failure. No failure was converted to success.
- **What the confound does and does not explain.**
  - **Likely affected (INFERRED, not confirmed):** both `REGRESSION_UNATTRIBUTED` stops (T1, T5). A full-regression
    comparison against a non-green baseline is exactly where an aggregate change with no attributable test arises.
  - **Not explained by it:** T2's typed scope stop, T3's edit-protocol rejections and T4's pre-inference
    edit-protocol refusal. All of these happened before any full-regression comparison.
- **Consequence.** "0/5" here does not show that Kriya cannot complete tasks end to end. A clean answer needs the same
  kind of tasks on bases whose suites are green before the change. That is an owner decision; I did not rerun.

## 4. P1 / P4 / P5 paths

| Path | Observed |
|---|---|
| **P1** | Operated correctly where exercised. T3: the qwen3.6 fallback served two `fallback_targeted` attempts (qualification-derived patch capability) with **no rejection**; no false `FALLBACK_MODEL_INCOMPATIBLE` in any run; no routing bypass was needed (no patch-incompatible fallback) |
| **P4** | NOT_EXERCISED: no plan contained a verification-only unit (no `retry.verification_admission` event in any store) |
| **P5** | NOT_EXERCISED: no plan declared an integration relationship (no `integration.obligation` event) |
| **M1** | 5/5 stores VERIFIED and sealed; no blank explain answers; LV-2 typing present (Q4 NOT_APPLICABLE(no_model_call) where no Developer call) |

No new correctness defect was observed in M1, P1, P4 or P5.

## 5. Failures by family

| Family | Tasks |
|---|---|
| P2 `REGRESSION_UNATTRIBUTED` | T1, T5 (both on non-green baselines: §3) |
| P3 Developer/protocol rejection | T3 (the dominant attempt burn: anchored-edit refusals, structural corruption; terminal `quality_gates_exhausted` after a test failure) |
| other typed Kriya stop | T2 (`NO_AUTHORIZED_REPAIR_TARGET`: cross-subtask failure grounded to the producer's file by model attribution); T4 (`CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`, pre-inference) |
| planning / verification / integration / runtime | none as terminal family |

Qualitative only (five runs; no statistical claim, no comparison to the 80-run matrix): no clean success; failures
spread across P2 (on confounded baselines), P3 and two existing typed stops.

## 6. Status

```text
TASKS                         5 planned / 5 executed
SUCCESS                       0/5
FALSE SUCCESSES               0
M1 EVIDENCE                   VERIFIED 5/5
P1                            operated correctly (exercised in T3)
P4                            not exercised (no verification-only unit)
P5                            not exercised (no integration relationship)
CLEAN END-TO-END SUCCESS      NO
```

**Recommendation (not acted on).** Before drawing a product conclusion, repeat this validation once with the same
selection rules **plus a recorded green-baseline check** (the base's own test suite passing before the change) for
each task. The P2 evidence from T1/T5 is still useful input to P2: both stops occurred on a non-green baseline.
