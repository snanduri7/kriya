# FS-1: false success / verification integrity

**Investigation and design. No production code changed.**

| Item | Value |
|---|---|
| Branch | `feature/lr-r1-fs1`, based on `94420ab` (accepted P5 + R2 lineage) |
| Specimen | R2 T3, commons-lang `CharSetUtils.containsOnly`, run `20261005T193554-02bd0660` (preserved; workspace and evidence untouched; read-only copies under `handover/evidence/fs1/specimens/`) |
| Contrast | R2 T5 (genuine success, `20261005T201054-868fc770`); R2 T1 (false negative, `20261005T192055-bd4ff7df`) |
| Labels | OBSERVED, DERIVED, MODEL_CLAIMED and DETERMINISTIC classify evidence; MEASURED, TRACED, INFERRED and CONFIRMED classify my statements |

## 1. Root cause: the exact authorization path of the T3 false success (all MEASURED from the evidence store, TRACED in code)

| # | Transition | Evidence that authorized it | Class |
|---|---|---|---|
| 1 | s1 compile gate PASS (seq 39) | `mvn compile` exit 0 | DETERMINISTIC (true: it compiles) |
| 2 | s1 tests gate PASS (seq 58) | `mvn test` exit 0. `CharSetUtilsTest` ran **11** tests, the same as base; nothing exercised `containsOnly` | DETERMINISTIC, but **vacuous** for the requirement |
| 3 | s1 per-attempt goal spec compliance PASS | the model: "correctly utilizes the isEmpty helper … for null/empty validation". It was false: that helper is what returns true for a null or empty set | MODEL_CLAIMED. It records `GOAL_SPEC_REQUIREMENT` SATISFIED, `authority=JUDGMENT`, non-terminal (`attempt.py` ~10372) |
| 4 | s2 targeted tests PASS (seq 98) | `mvn test -Dtest=CharSetUtilsTest` exit 0. **11** tests executed; the candidate file declares 12 `void test…` methods but has 11 `@Test`. Surefire's own XML lists 11 testcases, **without `testContainsOnly_StringString`** | DETERMINISTIC; the structured contradiction (OBSERVED in the XML) was **discarded** |
| 5 | s2 full regression PASS (seq 117) | 22,042 run, the same as baseline; 0 failures | DETERMINISTIC, vacuous for the requirement |
| 6 | s2 per-attempt spec compliance PASS | the model: "includes all these test cases and passes successfully". False: the method never ran | MODEL_CLAIMED (the verifier is shown file contents only, never test results) |
| 7 | **Original requirement REQ-1 → SATISFIED** (seq 137, `requirement.verdicts`) | `SpecComplianceAgent.check(requirements=)` verdict `satisfied`, reason `VERIFIER_CONFIRMED`, detail "Method … exists … and test method … exists". `gate_evidence` is the fixed string "enforce terminal gates: every subtask verified" | **MODEL_CLAIMED → recorded as SATISFIED** (`terminal_gate_service.py:~118` → `requirements.record_requirement_verdicts`, `authority=JUDGMENT`) |
| 8 | `original_requirements` terminal gate PASS (seq 135) | `requirements.blocking_requirements()`: SATISFIED never blocks. Only VIOLATED, PENDING/UNKNOWN and UNVERIFIED (by policy) block | DERIVED from step 7 |
| 9 | `terminal_obligations` gate PASS | the ORIGINAL_REQUIREMENT obligation SATISFIED (step 7); GOAL_SPEC records are non-terminal | DERIVED from steps 7 and 3/6 |
| 10 | `commit_eligible` → commit → **SUCCESS** (seq 139, `COMMITTED`) | all seven terminal gates passed | DERIVED |

**The exact point where MODEL_CLAIMED evidence becomes success-authorizing state** is
`requirements.record_requirement_verdicts`, called from `terminal_gate_service._verify_original_requirements`
(enforce) and `attempt._record_original_requirement_verdicts` (direct).
- A model verdict `satisfied` is written as an ORIGINAL_REQUIREMENT obligation with status **SATISFIED**
  (`authority=JUDGMENT`).
- `requirement_outcomes` keeps SATISFIED as is. Only UNVERIFIED needs deterministic closure
  (`record_requirement_closure`, `authority=DETERMINISTIC`).
- `blocking_requirements` therefore never blocks it.

The test gates contributed only exit codes, which are true but vacuous for the requirement, while the structured
report that contradicted the model's claim was never read.

**Reproducer (Phase 1).**
- `tests/test_fs1_false_success_reproducer.py` (passes; pins the path). It is the same shape through the real enforce
  controller with real pytest gates; only the model transport is scripted:
  - s1 adds `contains_only` with T3's defect (empty `allowed` returns True: **executed**, not asserted from text);
  - s2 adds the check under a name pytest never collects (the Python counterpart of a missing `@Test`; the gate output
    never mentions it);
  - the verifier says REQ satisfied (`VERIFIER_CONFIRMED`);
  - all terminal gates PASS, Kriya reports `success`, and the wrong module is applied.
- `handover/evidence/fs1/test_fs1_required_behaviour.py` asserts the required behaviour: no SUCCESS, nothing
  applied. **It fails today (MEASURED: `'success' != 'success'`).** It runs with the suite's own isolation
  (`-p conftest`; an isolation self-check passes) and moves into `tests/` with the fix (no xfail).

## 2. FS-1A: test execution integrity (CONFIRMED)

| Question | Maven / JUnit (Surefire) | pytest |
|---|---|---|
| 1. Discovered tests known deterministically? | **Yes, available, discarded.** Surefire writes `target/surefire-reports/TEST-<class>.xml` with one `<testcase name classname time>` per executed test (plus skipped/failure/error children). MEASURED in the T3 and T5 worktrees. Kriya reads only the log | **Not captured.** Kriya runs `pytest.main(args)` with no `--junitxml`; the default log names only failures. pytest supports `--junitxml` natively (structured) |
| 2. Executed tests known? | Yes, from the same XML (not read today) | Only when `--junitxml` (or equivalent) is requested; not today |
| 3. Associate new/modified test source with executed ids? | **Yes, deterministically.** The candidate's test file vs its base, parsed by Kriya's own code-intel parser (`code_intel.parsing`, annotation-aware), gives the added/changed test methods; the XML gives `classname` + `name`. Demonstrated on the real specimens (below) | Yes, by the same structural diff (`code_intel` Python parser) against `--junitxml` testcase `classname` + `name` (pytest node ids) |
| 4. Baseline vs candidate inventory comparable? | Yes: the baseline capture runs the same suite and would leave the same reports | Yes, with junitxml on both runs |
| 5. Evidence that exists but is discarded | Surefire XML (all tests, per test); per-class "Tests run:" log lines (only aggregates and failures are parsed: `validation_baseline.parse_surefire_structured_outcomes`) | the pytest log's passed tests (not printed by default); nothing structured requested |
| 6. Adapter extensions needed | `BuildAdapter.run_tests` returns a structured **TestExecutionReport** (executed test ids + status) read from the runner's own reports. Maven/Gradle: the JUnit XML in the reports directory, bound to this run: the directory cleared before the gate and **registered as gate output** (FILE-INTEGRITY 001B). Recorded in the gate outcome and M1 `gate.result` | `PipBuildAdapter` adds `--junitxml=<Kriya-owned path outside the candidate tree>`; same report type |

**The decisive demonstration (MEASURED).** `handover/evidence/fs1/executed_test_evidence_check.py` runs on the real
artifacts, offline and with no production code:

| Specimen | Methods the candidate added to the test file | In Surefire's executed list? | Verdict |
|---|---|---|---|
| T3 | `testContainsOnly_StringString` (no `@Test`) | **NOT_EXECUTED** (11 executed) | INSUFFICIENT |
| T5 | `hasPet_returnsTrueWhenPetExists`, `hasPet_returnsFalseWhenPetDoesNotExist` (`@Test`) | **passed, passed** (6 executed) | SUFFICIENT |

No count heuristic is involved: the check is per added test method, against the runner's own per-test record. A
modified existing test is handled the same way (it must appear as executed); a count increase is neither required
nor sufficient.

## 3. FS-1B: model authorization boundary (CONFIRMED)

1. **Can model-only PASS satisfy `original_requirements`? Yes.** A `satisfied` verdict is recorded as a SATISFIED
   ORIGINAL_REQUIREMENT, and `blocking_requirements` passes it (step 7 → 8).
2. **Can model-only evidence move a requirement from PENDING to verified? Yes.** PENDING → SATISFIED with
   `VERIFIER_CONFIRMED`. The deterministic closure path (`record_requirement_closure`: named tests unchanged by the
   candidate, migration gate, mutation scope) is needed only for UNVERIFIED, and a SATISFIED verdict bypasses it.
3. **Can a model reviewer claim tests passed without test-result evidence? Yes.** The verifier receives only file
   contents (`spec_compliance.check(goal, files_written, file_contents, requirements, baseline_contents)`), never gate
   results. T3's verifier wrote "passes successfully" about a test that never ran, and that text is the recorded
   verdict detail.
4. **Downstream consumers of that PASS:**
   - `requirement_outcomes` → `blocking_requirements` → `TerminalGateService._requirement_gap` → `commit_eligible`;
   - → `commit_service.commit_verified_candidate` → RunRecord SUCCESS;
   - plus the `terminal_obligations` aggregation;
   - per-attempt: `GOAL_SPEC_REQUIREMENT` SATISFIED (non-terminal; also used for the re-check skip, "settled goal
     spec").
5. **Would removing positive model authority break T5? Yes, by itself.** MEASURED: T5's REQ-1 was also satisfied
   **only** by `VERIFIER_CONFIRMED` ("Method 'hasPet' exists … test methods exist"). No deterministic closure was
   recorded.
   - With model positives demoted to UNVERIFIED and the production policy (`requirement_unverified_policy: block`),
     T5 would fail.
   - None of the existing closure methods applies to T5: its named test file is changed by the candidate, and there is
     no migration or scope requirement.
   - So T5 stays eligible **only if FS-1A supplies a new deterministic closure.**

## 4. T1: the false negative and the same boundary

T1's implementation was correct: `count_noun` returns `f"{count} {self.plural(noun, count)}"`, and the deterministic
compile and test gates passed. Then:
- the per-attempt spec-compliance verifier claimed it "must use the engine's existing plural method" (MODEL_CLAIMED,
  false);
- it raised a `goal_spec_compliance` QualityGateFailure;
- attribution grounded it to s1's file (outside s2's scope), giving the **deterministic stop
  `NO_AUTHORIZED_REPAIR_TARGET`**.

The same boundary is at work in the opposite direction: a model judgment, here negative, becomes authoritative input
to an attribution, and so to a typed stop. Under the governing invariant, a veto may request deterministic checking
or a repair, but its *attribution* should not ground a cross-owner stop. Not fixed here, as instructed.

## 5. Proposed minimum safe design (for owner review; not implemented)

### A. Deterministic test-execution evidence (a new cross-language adapter contract)

- **`TestExecutionReport`.** Executed test ids, status (passed/failed/error/skipped) and source location when the
  runner provides one. It is produced by each `BuildAdapter.run_tests` from the runner's **own structured report**:
  JUnit XML for Maven and Gradle, `--junitxml` for pytest; never log regex.
  - The report directory/file is Kriya-owned per gate run: cleared before the gate and registered as gate output under
    FILE-INTEGRITY 001B, so a stale report can never be read.
  - It is recorded on the gate outcome and in M1 `gate.result` (the payload already carries arbitrary gate fields;
    whether that counts as a "schema change" is for the owner).
- **`TestSourceDelta`** (deterministic). For every test file the candidate created or modified, the test methods
  added or changed: base vs candidate, through `code_intel.parsing`, annotation-aware for Java (JUnit `@Test`,
  `@ParameterizedTest`, …) and with the runner's collection rules for pytest.
- **Rule E (execution integrity).** Every added or changed test method in the candidate must appear as **executed and
  passed** in the candidate's targeted or full test report.
  - Otherwise the gate fails with a typed, repairable reason, e.g. `TEST_NOT_EXECUTED` naming the method. This is
    actionable for the Developer (add `@Test` / rename to the collected form).
  - A test file the candidate did not touch is unaffected, so existing behaviour is unchanged.
  - Unknown runner or no structured report: **INDETERMINATE**, never PASS. It can never authorize closure (below).

### B. Requirement-success authorization (a change of spec-compliance authority)

- **The evidence order** as you specified it: compiler/parser/LSP → deterministic assertions → executed tests →
  regression gates → runtime evidence → policy → MODEL_CLAIMED semantic assessment.
- **A model `satisfied` verdict is recorded as UNVERIFIED, never SATISFIED** (an existing state with its existing
  production policy, `block`). The model's text is kept as enrichment.
- **A requirement reaches CLOSED_BY_EVIDENCE** (an existing outcome) only through deterministic closure for the exact
  candidate. That means the existing methods (named tests, migration gate, mutation scope) plus **one new method:
  `EXECUTED_CANDIDATE_TESTS`**. Its conditions are:
  - the plan or goal requires tests, or the candidate added or changed tests;
  - Rule E holds (every added/changed test method executed and passed);
  - the full regression is green against a green or comparable baseline;
  - no deterministic counter-evidence.

  The model's positive verdict is neither necessary nor sufficient. The closure is recorded with
  `record_requirement_closure` (existing, `authority=DETERMINISTIC`).
- **A model negative (`missing`) stays a veto:** VIOLATED blocks (existing). FS-1 does not change it. The T1 question
  (should a model veto ground a cross-owner stop?) is separate.
- **Deterministic contradiction outranks the model:** Rule E failing, or executed counter-evidence, decides whatever
  the verifier said.

### Predicted effects

| Case | Under the design | Today |
|---|---|---|
| **T3** | Rule E fails (`testContainsOnly_StringString` NOT_EXECUTED) → typed repairable gate failure. If not repaired, REQ-1 stays UNVERIFIED (blocked) → **no SUCCESS, nothing applied** | SUCCESS + COMMIT |
| **T5** | Rule E holds (both added `@Test` methods executed and passed); full regression green (77/77) → REQ-1 CLOSED_BY_EVIDENCE (`EXECUTED_CANDIDATE_TESTS`) → **still SUCCESS** | SUCCESS |
| **Reproducer** | pytest junitxml lacks `check_contains_only`, so the required-behaviour test passes | the required-behaviour test fails |
| **Goals with no tests and no deterministic closure** | UNVERIFIED → blocked under the production policy (`needs_review` semantics). That is the stated principle ("do not manufacture VERIFIED from model PASS"); it is a real behaviour change for model-only-verified goals and must be owner-accepted | model PASS can authorize success |

**Residual (stated).** Executed, passing candidate-written tests prove those tests ran and passed. They do not prove
the tests express the goal: a candidate could write weak tests. Closing that needs requirement-to-test binding, e.g.
the goal's explicit cases. That is beyond the minimum design and is flagged, not claimed.

## 6. Why no small safe fix was taken

Both halves fall in the authorization list that requires stopping:
- **A** is a new cross-language test-execution contract (adapter outputs, gate evidence, the M1 payload).
- **B** changes the authority of spec compliance and the requirement-outcome semantics.

There is no narrower deterministic defect with an obvious existing-authority fix: the existing closure machinery
cannot see test execution at all.

## 7. Evidence (`handover/evidence/fs1/`)

| Path | Contents |
|---|---|
| `specimens/` | T3 and T5 Surefire XML for the relevant class, and their test files at base and in the candidate. Read-only copies; the originals are untouched |
| `executed_test_evidence_check.py` | the deterministic demonstration (T3 INSUFFICIENT, T5 SUFFICIENT) |
| `test_fs1_required_behaviour.py` | the required-behaviour test (fails today) |
| `tests/test_fs1_false_success_reproducer.py` | the pinning reproducer (passes) |
