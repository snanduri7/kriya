# P2: candidate-caused regression reported REGRESSION_UNATTRIBUTED (investigation, no production change)

**Branch:** `feature/lr-r1-p2-inv`, from the certified FS-1A/B lineage `be34772`. **Production code changed: NO.**
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.
**Primary specimen:** R2 T4, run `20261005T195809-6a6add6d` (commons-lang `ArrayFill`). Its M1 store is
`~/kriya-m1-live/state/attempt-evidence/20261005T195809-6a6add6d`, read only; the approved plan is in the workspace's
`.kriya/control/plans/`.

## 1. What happened in T4 (MEASURED, M1 store)

| seq | record | content |
|---|---|---|
| 13 | `unit.opened` s1 | write scope `[ArrayFill.java]`; the plan has two subtasks |
| 147–148 | candidate + compile | attempt 5 (qwen3.6 fallback) staged the `int[]` overload; compile PASS |
| 166 | approval | approved |
| 167 | `gate.result` tests | the full regression FAILED at **testCompile**: `ArrayFillTest.java:[167,40] reference to fill is ambiguous` / `both method fill(char[],int,int,char) … and method fill(int[],int,int,int) in org.apache.commons.lang3.ArrayFill match` (output fixed in `tests/fixtures/p2/t4_full_regression_output.txt`, sha256 `e8bafb06…`) |
| 168 | `validation_baseline.full_regression_delta` | `level1=NEW_FAILURE`, `blocking=True`, **`level2_available=false`** ("no structured per-test parser recognized the POST output") |
| 169/172 | gate outcome / diagnosis | `type=regression_unattributed`, **`file_locations=[]`, `likely_files=[]`**, `evidence_class=UNKNOWN` |
| 175 | `recovery.decision` | `stop_environment`, `stop_reason_code=REGRESSION_UNATTRIBUTED`, `retry=false` |

**The plan (MEASURED).**
- s1: `ArrayFill.java`, provides `array_fill_int_range_method`.
- s2: `ArrayFillTest.java`, requires that capability and depends on s1.

s2 never ran.

**The baseline was GREEN (MEASURED).** The R2 admission gate passed, and Kriya's own comparison says NEW_FAILURE, which
means PRE succeeded and POST failed.

**The failure is candidate-caused (CONFIRMED with real javac).**
- `tests/test_p2_regression_attribution_reproducer.py::test_p2_t4_shape_is_what_javac_reports` compiles the fixture
  files with javac 17.
- The base compiles.
- Adding the goal-mandated `fill(int[],int,int,int)` makes the unchanged line-167 call
  `ArrayFill.fill(null, 0, 0, 'Z')` ambiguous, with T4's message.
- `(char[]) null` compiles.
- No change inside `ArrayFill.java` can avoid it while keeping the goal's signature. The only repair is in
  `ArrayFillTest.java`, which is s2's planned file, outside s1's write scope.

## 2. Trace (TRACED unless marked)

```
validator.run_tests() (full suite)                                 workflow.py:4728
 -> _regression_should_block = not success                          workflow.py:4729
 -> classify_baseline_delta(PRE, POST)                              workflow.py:4757
      level2_available = pre.test_outcomes and post.test_outcomes   validation_baseline.py:756
      (testCompile failed: Surefire never ran, so POST has no per-test outcomes -> False)
 -> confirm_ambiguous_regressions(level2 = {})                      workflow.py:~4910
 -> _confirmed_regression_ids == []  and targeted baseline not blocking
 -> _full_regression_unattributed = True                            workflow.py:4948   <-- information dropped
 -> resolve_future_owner_verification_deferral -> None              workflow.py:4963; attribution.py:922
      (s2's required capability is provided by the CURRENT subtask s1: "self-deferral" refused by design;
       PRV-11 covers the inverse direction - a verification subtask running before its provider)
 -> Failure(type="regression_unattributed", file_locations=[])      workflow.py:4984-5022  (built directly,
      never through _build_quality_gate_failure / locator grounding)
 -> retry_strategy: regression_unattributed -> environment_failure  retry_strategy.py:617
 -> STOP_ENVIRONMENT, REGRESSION_UNATTRIBUTED                       recovery decision
```

The ordinary branch for the same output is `_build_quality_gate_failure("regression_test", …)` (workflow.py:5029). It
reaches `retry_strategy.handle_attempt_failure`:
- the repository locator re-grounding (`resolve_repository_locator_files`, retry_strategy.py:941);
- the deterministic `locator` attribution tier;
- the PLAN_SCOPE_DEFECT escalation (`scope_conflict_is_grounded`, retry_strategy.py:1137 → `PLAN_SCOPE_REVISION_REQUIRED`,
  :1165).

**MEASURED on the T4 output:**
- `resolve_repository_locator_files(output, worktree, [ArrayFill.java])` returns exactly
  `['src/test/java/org/apache/commons/lang3/ArrayFillTest.java']`.
- `_resolve_file_locations(output, [ArrayFill.java])` returns `[]`, because the locator's file is not a candidate file.
- With `ArrayFillTest.java` added to the known files, it returns `ArrayFillTest.java:167`.

## 3. Answers

1. **Did Kriya already parse the compiler diagnostic?** No. The unattributed branch builds its Failure directly
   (`file_locations=[]`). The parsers exist and resolve this output: `resolve_repository_locator_files` (MEASURED) and
   `_resolve_file_locations` once the repository file is known.
2. **Did the diagnosis identify a candidate-mutated symbol or file?**
   - Yes. The message names `fill(int[],int,int,int) in org.apache.commons.lang3.ArrayFill`, the method the candidate
     added.
   - Its locator names `ArrayFillTest.java:167`, an existing, unchanged file that compiled at the green PRE baseline.
3. **Why was that not enough for attribution?** The decision at workflow.py:4948 consults only level-2 per-test ids.
   - A test-compile failure has none: `level2_available=false`. "No per-test evidence" is treated exactly like "per-test
     evidence shows only pre-existing failures", the G1-DEVINV2 case the branch was built for.
   - The stop message even claims "every per-test failure matches the PRE-mutation baseline". In T4 there were no
     per-test results at all.
4. **Is regression attribution test-name based when compiler attribution exists?** Yes, once a full-regression baseline
   is captured (`brownfield_full_regression_baseline_policy` required, or auto-triggered as in T4). Without a captured
   baseline, the same output takes the ordinary locator-based route.
5. **Which existing authority can authorize the repair?** The deterministic `locator`-tier PLAN_SCOPE_DEFECT escalation.
   - `retry_strategy.handle_attempt_failure` sets `PLAN_SCOPE_REVISION_REQUIRED`.
   - The controller then runs its validated plan revision, `revise_plan_for_grounded_scope_owner`
     (workflow_controller.py:5827). It moves the grounded file from its owner into the failed stage, then calls
     `validate_plan`. This is the existing route for a grounded owner downstream of the failed stage, as tested by
     `test_enforce_revises_service_scope_to_grounded_controller_and_continues`.
   - PRV-11's future-owner deferral does not apply (Q8 below), and no new authority is needed.
6. **Which subtask and file should receive repair authority?**
   - The file is `ArrayFillTest.java` (the call at line 167, which needs a `(char[])` cast).
   - The authority goes to s1 after the plan revision merges s2's planned file into it. It is then the same stage that
     owns the overload.
   - MEASURED in a throwaway prototype: the revised plan re-invokes s1 with
     `authorized_write_scope=[ArrayFill.java, ArrayFillTest.java]`.
7. **Would the repair stay within existing D1 and write-scope rules?** Yes, once the grounded locus is carried. Without
   it, no (MEASURED).
   - The re-invocation is a fresh `GenerationState`, so `state.last_failure` and `state.edit_anchor_loci` are empty.
   - The controller never seeds them (TRACED: no reference to them outside attempt.py/state.py).
   - `_edit_capability_loci` (attempt.py:2415) therefore has no locus for `ArrayFillTest.java`. That file is not shown
     whole, so `operations=[]` and the request is refused before inference: `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`.
   - With locus 167 carried, the unchanged CONTEXT-EDIT-PROTOCOL rules offer `anchored_edit` (skeleton plus exact
     window). The repair is staged, the full regression goes green, and the run ends SUCCESS. Nothing is widened: the
     window comes from the existing derived-window rule.
8. **What is the earliest deterministic point?** workflow.py:4948, at the `_full_regression_unattributed` decision,
   right after `classify_baseline_delta` reports `level2_available=False`. All of these are known at that point:
   - PRE success (a green baseline);
   - the POST output's compiler locators;
   - the repository file they resolve to.

## 4. Root cause: CONFIRMED

When a full-regression baseline is captured, the decision "is this regression attributable?" uses only level-2
per-test identities. A candidate-caused **test-compilation** failure produces none (`level2_available=False`). It is
then classified REGRESSION_UNATTRIBUTED and stopped, and its deterministic compiler locator is never passed to the
existing locator attribution or scope-escalation path.

**Discriminating evidence:**
- **MEASURED:** the same output resolves to the exact file with the existing resolver.
- **MEASURED (prototype, deleted):** gating only that decision on "level 2 unavailable + PRE green + a resolvable
  repository compiler locator" turns T4 into a `locator`-grounded `regression_test`, routed `PLAN_SCOPE_REVISION_REQUIRED`.
- **MEASURED (prototype, deleted):** the G1-DEVINV2 negative control still passes, along with the
  baseline/attribution/reason-code suites (104 passed) and the workflow regression tests (6 passed).

**Second link, the same chain (MEASURED/TRACED).** A plan-scope re-invocation does not carry the originating failure's
grounded loci. Without them the authorized repair cannot get an edit capability for a file that is not shown whole.
Both links are needed for "attribution → authorized repair → SUCCESS".

**Competing explanations considered and rejected:**
- **A non-green baseline (the R1 confound).** Ruled out: T4 was GREEN.
- **Environment drift (NOT_COMPARABLE).** Ruled out: the PRE and POST environment identities are equal in the delta
  event.
- **Future-owner deferral should have caught it.** Ruled out by design: it covers the inverse direction (attribution.py:922).
- **Attempt budget exhausted.** Not causal: the plan-scope route does not consume the attempt budget. The prototype
  reached SUCCESS through a fresh invocation.

## 5. Reproducer: READY

| File | Role | Today |
|---|---|---|
| `tests/_p2_t4_shape.py` | fixtures (see below) | |
| `tests/test_p2_regression_attribution_reproducer.py` | `test_p2_t4_shape_is_what_javac_reports` (real javac 17; skipped without javac) and `test_p2_today_…` (pin of today's path) | 2 passed |
| `handover/evidence/p2/test_p2_required_behaviour.py` | desired behaviour; run with `PYTHONPATH=$PWD:$PWD/tests pytest -p conftest handover/evidence/p2/test_p2_required_behaviour.py` | FAILS (requirement 1: a REGRESSION_UNATTRIBUTED stop) |

**Fixtures (`tests/_p2_t4_shape.py`):**
- the T4 plan, goal and paths;
- the real call at line 167;
- a deterministic `mvn` oracle replacing only `_run_maven_cmd`. On ambiguity it returns the measured T4 output and writes
  no Surefire report; otherwise it returns BUILD SUCCESS with Surefire XML. `run_tests`, the Maven adapter, the FS-1A
  report binding and Rule E all stay real.

**The desired test requires:**
1. no REGRESSION_UNATTRIBUTED stop or gate outcome;
2. a `regression_test` diagnosis located at `ArrayFillTest.java:167`, with a deterministic attribution tier recorded for
   that failure;
3. a later Developer request authorized to write `ArrayFillTest.java`;
4. SUCCESS with the cast applied, the overload kept and the last full regression green.

**Prototype result (both changes, from the probe's records, not this test file):** requirements 1, 3 and 4 MEASURED; requirement 2's file location is present.
The attribution tier on the M1 `diagnosis` record is null, because that record is written at raise time, before
attribution runs. The fix must also record the attribution result at that point. See the next section.

## 6. Fix boundary: READY (not implemented)

**Minimum fix:**
1. **`workflow.run_generation_workflow`, full-regression block (workflow.py ~4908–4950, 5029).** Set
   `_full_regression_unattributed` only when level 2 was *available*.
   - When it is unavailable, PRE succeeded, and `resolve_repository_locator_files(POST output, worktree, files)` returns
     at least one repository file: build the ordinary `regression_test` Failure. Its known files must include those
     files, so `file_locations` carries the compiler locator.
   - Otherwise keep the unattributed stop unchanged: no locator, a red or unknown PRE, or ambiguous resolution.
2. **Carry the grounded loci across the plan-scope re-invocation.**
   - In `retry_strategy.handle_attempt_failure`, where `PLAN_SCOPE_REVISION_REQUIRED` is built (:1159–1185), add
     `grounded_locations`: `failure.file_locations` restricted to `outside_scope`, plus each file's raw revision.
   - `WorkflowController._run_structured_enforce` passes them to the re-invoked `run_generation_workflow` (a new keyword).
   - That call seeds `state.edit_anchor_loci`, only for a file whose current raw revision equals the recorded one; a
     stale locus is dropped.
   - `attempt._edit_capability_loci` is unchanged.
3. **M1 attribution.** The `diagnosis` record for a re-attributed failure must carry the final attribution tier, or a
   recorded follow-up. This uses the existing M1 diagnosis mechanism; no schema change.

**Negative controls (each must stay as today):**
- **The G1-DEVINV2 case** (`test_validation_baseline.py::test_full_regression_unattributed_stops_after_one_developer_call`):
  level 2 available, only pre-existing failures. Still REGRESSION_UNATTRIBUTED after one Developer call.
- **A red PRE baseline with a compile error.** Still unattributed: no attribution from a non-green baseline.
- **No locator in the POST output**, for example a Surefire fork crash with no file:line. Still unattributed.
- **A locator resolving to no file, several files, or a file outside the workspace.** Still unattributed.
- **A locator in the candidate's own file.** An ordinary in-scope repair, with no plan revision.
- **A carried locus for a file whose revision changed.** Dropped; no capability from stale evidence.
- **The PRV-11 inverse case (a verification subtask before its provider).** Still deferred, unchanged.

**Mutation targets:**
- drop the `level2_available` guard;
- drop the PRE-success guard;
- omit the locator files from the known files;
- carry no loci;
- carry loci without the revision check;
- accept an ambiguous resolution;
- record the attribution as non-deterministic (an LLM tier).

**Non-interference (must not change):**
- P1 fallback/capability routing;
- P4 verification-only retry admission;
- P5 integration provenance;
- FS-1A test-execution evidence and Rule E (an attempt-gate concern, untouched);
- FS-1B requirement semantics;
- the M1 record schema and the `record_gate_outcome` inventory pin (88; the fix should add no new sites);
- `classify_baseline_delta` / PRD-024 comparison semantics;
- `resolve_future_owner_verification_deferral`;
- `revise_plan_for_grounded_scope_owner`;
- CONTEXT-EDIT-PROTOCOL capability rules (loci sources unchanged; only their persistence across the re-invocation is
  added).

## 7. Observations outside P2 (not acted on)

- **Harness artifact (INFERRED).** In the reproducer the first invocation closes `final_review_refused`: the final review
  request (~9361 tokens) does not fit the chaos runtime's 8192-token window. The category label is replaced, and the
  controller still revises the plan. Live T4 used 32k.
- **P3 relevance.** Without link 2 the authorized repair would stop as `CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE`, which is a
  P3-family stop. P3 decomposition should treat "loci lost across a re-invocation" as a distinct cause, not a model
  failure.
