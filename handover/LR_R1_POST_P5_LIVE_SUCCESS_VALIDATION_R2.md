# LR-R1 post-P5 live success validation R2 (proven-green baselines)

**Question:** can current Kriya (M1 + P1 + LV-2 + P4 + P5) complete useful real tasks end to end?

**Answer from R2:**
- **1 clean, genuine end-to-end success** (T5, Spring XML).
- **1 FALSE SUCCESS** (T3, commons-lang): Kriya reported SUCCESS and applied code that violates an explicit requirement of the goal.
- **3 failures.**
- All 5 evidence stores VERIFIED.

The false success is the most important finding of this validation (§4).

**R1 record (unchanged; its evidence is not altered):** POST-P5 LIVE VALIDATION R1: SUCCESS 0/5, FALSE SUCCESS 0, M1 VERIFIED
5/5, END-TO-END CONCLUSION INCONCLUSIVE, CONFOUND = ALL FIVE BASELINES NON-GREEN.
R1's failures stay diagnostic evidence (T1/T5 → P2, T3 → P3, T2 → NO_AUTHORIZED_REPAIR_TARGET, T4 →
CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE) and were not rerun.

**Code and models.**
- `feature/lr-r1-p5` @ `6530138`, the clean-clone `venv-p5` build (dirty false), unchanged since R1.
- v5 production operator config: Developer `qwen3-coder:30b-kriya-e52213655394`; Planner and Developer fallback
  `qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a`.
- Each workspace config changes only `paths.*`; the owner's SEC-009 approvals were verified CURRENT.

**Labels:** MEASURED, TRACED, INFERRED.

## 1. Admission and selection (both committed before any model run: `9071bf7`)

- **The gate.** Each candidate's full regression suite ran at its exact base, the way Kriya's own baseline capture runs
  it (`PolymorphicValidator(…).run_tests()` under the production config: contained, offline Maven / venv). Admission
  required failed = 0 and errors = 0. Details: `evidence/lr-r1-post-p5-r2/TASK_SELECTION_R2.md` and `admission/*.json`.
- **Admitted (6 GREEN):**

  | Candidate | Result |
  |---|---|
  | inflect `262a247` | 214 passed / 16 xfailed |
  | cssselect2 `dc2690c` | 446 passed / 9 xfailed |
  | commons-lang `4ee346e59` (two workspaces) | 22,042 run, 0 failures, 0 errors, 19 skipped |
  | commons-cli `d95484f` (backup) | 994 run, 0 failures, 0 errors, 61 skipped |
  | spring-framework-petclinic `09351b3` | 75 run, 0 failures, 0 errors |

- **Rejected (3 RED, not repaired):**

  | Candidate | Reason |
  |---|---|
  | httpx `b5addb6` | 4 failed |
  | more-itertools `1ea82a7` | the suite cannot run under Kriya: `No module named 'pytest'` |
  | tomli `8479ed2` | same |

  **Environment finding (TRACED, `kriya/capabilities/pip.py`):** Kriya installs Python test dependencies only from a
  root `requirements.txt` or `pyproject.toml` runtime dependencies. A Python project with neither cannot run its suite
  under Kriya.

## 2. Results

| | T1 | T2 | T3 | T4 | T5 |
|---|---|---|---|---|---|
| repository | jaraco/inflect | Kozea/cssselect2 | apache/commons-lang | apache/commons-lang | spring-framework-petclinic |
| language / framework | Python | Python | Java | Java | Spring XML (Java) |
| base commit | 262a247d2d | dc2690c6b4 | 4ee346e59e | 4ee346e59e | 09351b3ee0 |
| baseline gate | GREEN | GREEN | GREEN | GREEN | GREEN |
| goal | `engine.count_noun` | `ElementWrapper.depth` | `CharSetUtils.containsOnly` | `ArrayFill.fill(int[],int,int,int)` | `Owner.hasPet` |
| units | s1 impl, s2 tests | s1 | s1 impl, s2 tests | s1 | s1 impl, s2 tests |
| attempts | 4 | 7 | 2 | 5 | 4 |
| model calls | 13 | 12 | 10 | 9 | 15 |
| fallback used | no | **yes**: 3 attempts on qwen3.6, no rejection | no | **yes**: 2 attempts on qwen3.6, no rejection | no |
| terminal result | FAILURE | FAILURE | **SUCCESS (FALSE)** | FAILURE | **SUCCESS** |
| failure category | `unauthorized_generation_target` (NO_AUTHORIZED_REPAIR_TARGET) | `unauthorized_generation_target` (NO_AUTHORIZED_REPAIR_TARGET) | none reported | `regression_unattributed` | none |
| compile / static | pass (s1, s2) | no candidate ever staged | pass | pass | pass |
| targeted tests | pass (s1); s2 its own tests pass | n/a | "pass" (the new test never ran: no `@Test`) | n/a | pass (OwnerTests 6 run, base 4) |
| full regression | not reached (spec-compliance stop) | n/a | pass | **FAIL**: test compilation error | pass (77 run, base 75) |
| global obligations / terminal gates | n/a | n/a | all 7 passed | n/a | all 7 passed |
| candidate applied | no | no | **yes (incorrect)** | no | **yes** |
| wall / model / recorder s | 251.6 / 189.2 / 0.275 | 260.3 / 241.0 / 0.308 | 1055.3 / 183.7 / 0.228 | 720.4 / 222.1 / 0.245 | 426.4 / 264.1 / 0.322 |
| evidence | VERIFIED | VERIFIED | VERIFIED | VERIFIED | VERIFIED |

## 3. Trajectories (MEASURED from the stores; diffs in `<ws>/applied_workspace.diff` and the store blobs)

- **T1 (inflect).**
  - s1 staged `count_noun` on attempt 3, after two `ANCHOR_NOT_IN_FILE` refusals. It is
    `return f"{count} {self.plural(noun, count)}"`, with compile and tests passing, and spec compliance PASSED it.
  - s2 staged `tests/test_count_noun.py`, and its tests passed.
  - Spec compliance then **failed the same s1 code** with "count_noun method must use the engine's existing plural
    method". The code does exactly that. **That is a false negative from the model-judged verifier.**
  - It was attributed to s1's file, outside s2's scope: `NO_AUTHORIZED_REPAIR_TARGET`. A correct candidate was not
    applied.
- **T2 (cssselect2).** 7 attempts, all Developer/protocol rejections:

  | Rejection | Count |
  |---|---|
  | `ANCHOR_NOT_IN_FILE` | 2 |
  | prose contamination | 3 |
  | `ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT` | 1 |
  | `ACTUAL_MUTATION_SHAPE_AUTHORITY_REJECTED` (included a write to the tests file outside s1's scope) | 1 |

  The last attempt ended `NO_AUTHORIZED_REPAIR_TARGET`. The qwen3.6 fallback served attempts 5–7 (P1 operating).
- **T3 (commons-lang): false success.** Section 4.
- **T4 (commons-lang).**
  - Attempts 1–4 were edit-protocol rejections: `ANCHOR_NOT_IN_FILE`, `ANCHOR_CONTEXT_NOT_ESCALATED` ×2,
    `ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT`.
  - Attempt 5 (qwen3.6 fallback, full-set) staged a correct-looking overload, and it compiled.
  - The full regression then failed at **test compilation**: `ArrayFillTest.java:[167,40] reference to fill is
    ambiguous — both fill(char[],int,int,char) and fill(int[],int,int,int) match` (the existing call
    `ArrayFill.fill(null, 0, 0, 'Z')`).
  - **This is a real, candidate-caused regression** that the gate rightly caught. Kriya nonetheless classified it
    `REGRESSION_UNATTRIBUTED` and stopped without a repair, although the compiler names the file, the line and the
    conflicting new method.
- **T5 (Spring XML petclinic): a genuine success.**
  - s1 added `hasPet`, with the same loop and case-insensitive match as `getPet(name, false)`: equivalent to
    `getPet(name) != null`, as the goal requires (TRACED).
  - s2 added two `@Test` methods on attempt 3, after two `ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT` refusals.
  - The gate ran them: OwnerTests 6 run (base 4); full suite 77 run (base 75), 0 failures, 0 errors.
  - All seven terminal gates passed, and the result was COMMITTED and applied.

## 4. The false success (T3), MEASURED

- **What Kriya reported.** Terminal SUCCESS / COMMITTED. All seven terminal gates passed, including
  `original_requirements`. The workspace now holds the candidate.
- **The goal requires** `containsOnly` to "return false for a non-empty str when set is null or empty".
- **The applied implementation** starts `if (isEmpty(str, set)) return true;`. `CharSetUtils.isEmpty` is
  `StringUtils.isEmpty(str) || deepEmpty(set)`, true for a null or empty set.
- **Executed against the applied sources** (javac over the workspace's `src/main/java`; harness
  `r2c-java-lang-a/false_success_check_Check.java`):

  | Call | Actual | Goal |
  |---|---|---|
  | `containsOnly("hello", (String) null)` | **true** | false |
  | `containsOnly("hello", "")` | **true** | false |
  | `containsOnly("hello", "a-z")` | true | true |
  | `containsOnly("hello", "a-d")` | false | false |
  | `containsOnly(null, "a")` | true | true |

- **Why the gates passed it:**
  1. The new test `testContainsOnly_StringString()` has **no `@Test` annotation**, so JUnit never ran it. Its
     `assertFalse(containsOnly("hello", null))` would have failed. The test gates passed vacuously for the new
     feature.
  2. The model-judged spec-compliance verifier PASSED both units with claims that are false: the implementation
     "correctly utilizes the isEmpty helper … for null/empty validation", and the tests "include all these test cases
     and pass successfully". Nothing deterministic checked that the new test executed or that the stated null/empty
     behaviour held.
- **Classification.** A verification-integrity defect (the model-judged acceptance plus no executed-test evidence for
  new tests). It is **not** an M1/P1/P4/P5 regression: M1 recorded every step faithfully (store VERIFIED).
- **R2's stop rule names only M1/P1/P4/P5,** so the remaining runs continued; the T3 workspace is left as applied,
  as evidence. I judge it more serious than any failure family here.

## 5. P1 / P4 / P5 / M1

| | Result |
|---|---|
| **P1** | **correct**: the qwen3.6 fallback (qualification-derived patch capability) served live in T2 (3 attempts) and T4 (2 attempts) with no incompatibility rejection; no false `FALLBACK_MODEL_INCOMPATIBLE` in any run |
| **P4** | **not exercised** (no verification-only unit in any plan) |
| **P5** | **not exercised** (no integration relationship in any plan; all terminal obligations passed where reached) |
| **M1** | 5/5 VERIFIED, sealed, no blank explain answers |

No correctness regression in M1, P1, P4 or P5 was observed.

## 6. Failures by family (the terminal family, with the dominant attempt-level cause)

| Family | Tasks |
|---|---|
| P2 `REGRESSION_UNATTRIBUTED` | T4 (on a GREEN baseline; the regression was candidate-caused and the compiler's locator was available) |
| P3 Developer/protocol reliability | T2 (every attempt; terminal typed as NO_AUTHORIZED_REPAIR_TARGET); also the dominant attempt burn in T4 (4 of 5 attempts) and present in T1 and T5 |
| NO_AUTHORIZED_REPAIR_TARGET | T1 (caused by a false-negative spec-compliance verdict on correct code), T2 (after P3 exhaustion) |
| CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE | none |
| verification | T1 (false negative), **T3 (false positive → false success)** |
| planning / integration / runtime-environment | none (the environment finding about Python test dependencies affected admission only) |

## 7. Status

```text
TASKS                              5 planned / 5 executed
GREEN BASELINES                    5/5
SUCCESS                            1/5 genuine (T5); Kriya reported 2/5
FALSE SUCCESS                      1 (T3) - requirement violated, vacuous test, model-judged acceptance
CLEAN END-TO-END SUCCESS OBSERVED  YES (T5; evidence packet preserved: reference-success-T5/)
M1 EVIDENCE                        VERIFIED 5/5
```

**Reference success, preserved.** `handover/evidence/lr-r1-post-p5-r2/reference-success-T5/`:
- the full evidence store `store-20261005T201054-868fc770` (167 files; `store_manifest.sha256`; the preserved copy
  re-verifies VERIFIED and sealed);
- the generate log;
- `r2c-spring-xml/{summary,explain,verify,show}.json`;
- `applied_workspace.diff`.
