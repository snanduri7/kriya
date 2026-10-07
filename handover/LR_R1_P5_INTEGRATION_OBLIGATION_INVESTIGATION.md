# LR-R1-P5: global integration obligation reported missing after every subtask passed

**Status:** root cause CONFIRMED for both live instances by deterministic reconstruction and a reproducer on Arm A's
code. **No fix implemented.**
**Code basis:** `61a867f` (Arm A of the CAGC-v2 experiment, also the common base), branch
`investigation/lr-r1-reliability`.
**Reproducer:** `tests/test_lr_r1_p5_integration_obligation_reproducer.py`: 3 tests, passing on current code (they
pin the defect).
**Live evidence:** copies of the frozen archives in the session scratchpad; the originals were not touched.
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED, UNKNOWN.

## 1. OBSERVE

| Run | Log (MEASURED) | Kriya result | Judge |
|---|---|---|---|
| python-symbol-valuechain B r2 (`20261004T191804-1940a7fa`) | `INTEGRATION_OBLIGATION_VIOLATED id=plan.integration.ir1 consumer=s2 missing=['more_itertools/more.py']` → `TERMINAL OBLIGATIONS UNSATISFIED (MA8 global aggregation check): plan.integration.ir1 (violated, authority=deterministic)` | failed; both subtasks passed their gates | NOT_SOLVED (nothing applied) |
| spring-xml-pettypes-cache A r1 (`20261004T164902-7d309518`) | same, `consumer=s3 missing=['src/main/resources/spring/tools-config.xml']` | failed; all 3 subtasks passed | NOT_SOLVED (nothing applied) |

valuechain B r2 is one of the two UNKNOWN causes behind the INCONCLUSIVE PROTOCOL_v2 result (05c §6.2).

## 2. Reconstruction (read-only, copies of the frozen archives)

1. **Approved plan.** Restored from `.kriya/control/plans/<run>.json` in each run's archive.
   - **valuechain B r2:** s1 modifies `more_itertools/more.py` (provides `value_chain_implementation`); s2 modifies
     `tests/test_more.py` (requires it, depends on s1). `integration_relationships`: `ir1` kind `uses`, producer
     `[s1]`, consumer `[s2]`, no participating artifacts.
   - **spring-xml A r1:** s1 `ClinicServiceImpl.java`; s2 `tools-config.xml`; s3 has `execution_role:
     verification` and `planned_files: []`, and requires both. `ir1` kind `configures`, producer `[s2]`, consumer
     `[s3]`.
2. **Candidate / worktree state.**
   - The archived worktrees (`.kriya/worktree`, `.kriya/worktrees/candidate-*`) are byte-identical to the task base
     in both runs, and in successful runs too. They are reset before archiving.
   - **The candidate bytes at aggregation time are NOT_RECORDED.** This also corrects my 80-run report; an erratum
     was added to `REVIEW/06`, `06b`, `06c` and `07`.
   - Recorded instead (MEASURED): which files each subtask wrote, from the trace rows' `files_modified`.

     | Run | Files written |
     |---|---|
     | valuechain B r2 | s1 `more_itertools/more.py`; s2 **nothing** |
     | spring-xml A r1 | s1 `ClinicServiceImpl.java`; s2 `tools-config.xml`; s3 **nothing** |
3. **Provider/consumer obligations.** `plan.integration.ir1` was seeded PENDING by plan validation
   (`plan_validation.py:901-925`) and evaluated once, after the consumer subtask completed.
4. **The exact inputs the aggregation consumed.** TRACED: `workflow_controller._evaluate_integration_obligations`
   (`workflow_controller.py:2422`), called after each subtask (`:6476`), with:
   - `plan.integration_relationships`;
   - the producer and consumer subtasks' `planned_files`;
   - the PENDING ledger record;
   - **`established_file_context`.** It is filled **only** from `call_result["files"]`, the files the subtask wrote
     in this run (`:6455-6468`; resumed subtasks `:5210-5226`), projected by `project_implementation_source`.

   The verdict: for each producer path, the consumer's established content must contain the producer file's stem
   as a whole word (`_integration_reference_token`: `more`, `tools-config`). Otherwise the obligation is
   VIOLATED, at DETERMINISTIC authority, `terminal_required=True`.
5. **Did the producer's modified file exist at aggregation time?**
   - The producer path was established: s1 and s2 respectively wrote it (MEASURED `files_modified`).
   - Its bytes are NOT_RECORDED (step 2), but they do not matter: the verdict failed on the **consumer** side.
   - The consumer wrote nothing, so the consumer content was the empty string.
   - MEASURED: the unchanged consumer file of valuechain, `tests/test_more.py` at the task base `0054e02`, contains
     the whole word `more` 8 times (e.g. `DocTestSuite('more_itertools.more')`). Had the check read the consumer's
     current file, `ir1` would have been SATISFIED.
6. **Which of the listed causes?**

   | Candidate cause | Finding | Evidence |
   |---|---|---|
   | stale plan state | No | the plan read is the approved plan; the relationship matches it |
   | wrong workspace | No | contexts are read from `plan_workspace_path` |
   | wrong subtask state | No | the consumer completed; the check ran at its completion |
   | wrong revision | No | — |
   | incorrect provider/consumer mapping | No | the mapping is exactly the plan's |
   | **correct semantics** | **No** | **the evidence source is wrong for consumers that write nothing**, below |

   The consumer's evidence is "files the consumer wrote in this run", not "the consumer's artifacts". So:
   - **(a)** a consumer that is legitimately verified as needing no change (valuechain s2: `attempt.passed`, nothing
     written) can never satisfy any relationship, even when its unchanged file already references the producer;
   - **(b)** a consumer with **no planned files** (verification-only, spring-xml s3) has no content by construction,
     so the relationship is unsatisfiable. Plan validation nevertheless admits a relationship whose consumer owns no
     artifact.

## 3. HYPOTHESES → DISCRIMINATING CHECK → CONFIRM

| # | Hypothesis | Check | Result |
|---|---|---|---|
| H1 | The producer's change was missing (not written, or not established) at aggregation time | `files_modified` of the producer rows | **Rejected**: the producer wrote the file (MEASURED) |
| H2 | The consumer genuinely does not reference the producer (a true integration gap) | the consumer file's content at base | **Rejected for valuechain** (8 whole-word references). For spring-xml, not applicable: there is no consumer file |
| H3 | The consumer evidence is restricted to files written in this run, so a consumer that writes nothing has empty evidence | reproducer test 1, unit level: the same reference is VIOLATED when unwritten and SATISFIED when written (negative control) | **CONFIRMED** |
| H4 | A relationship whose consumer has no planned files is unsatisfiable | reproducer test 2: verification-only consumer → VIOLATED with the producer listed missing | **CONFIRMED** |
| H5 | End to end on the real enforce controller, a verified no-change consumer ends TERMINAL OBLIGATIONS UNSATISFIED with every subtask completed | reproducer test 3: s1 completed, s2 completed (VERIFIED_NO_CHANGE); `INTEGRATION_OBLIGATION_VIOLATED ... consumer=s2 missing=['shop/service.py']`; `TERMINAL OBLIGATIONS UNSATISFIED ... plan.integration.ir1`; nothing applied | **CONFIRMED (live shape reproduced)** |

## 4. CONFIRMED ROOT CAUSE

The MA8 integration check (`_evaluate_integration_obligations`) judges a relationship using only the content the
consumer subtask **wrote** in the current run. A consumer that writes nothing is treated as referencing nothing, and
the relationship is VIOLATED at deterministic authority, which is terminal-required. Two cases trigger this:
- a no-change consumer (valuechain B r2);
- a verification-only consumer with no planned files (spring-xml A r1).

In valuechain B r2 the verdict contradicts the repository: the consumer's current file references the producer.

## 5. Consequence for the CAGC-v2 record (not a reclassification)

- The reproducer runs on `61a867f`, which is Arm A's exact code. Under PROTOCOL_v2 §6, a failure "reproduced
  deterministically on Arm A's code" is `PRE_EXISTING_KRIYA_DEFECT`, so valuechain B r2 now has a deterministic
  mechanism.
- **The frozen result is not rewritten, and it would not change.** Rule 3 also has valuechain B r7 UNKNOWN. Its first
  incorrect state, a test failure at attempt 1, is not reproduced. Its terminal fallback refusal *is* reproduced by
  LR-R1-P1.
- Rule 3 therefore still fails, and the result stays **INCONCLUSIVE**. Any reclassification belongs to a new protocol
  version and owner decision (task constraints: no acceptance-criteria change, no rewrite of the original analysis).

## 6. PREDICTED FIX EFFECT (not implemented; options)

| Option | Change | Predicted effect |
|---|---|---|
| A | The consumer evidence is the consumer's planned artifacts' **current** content: written this run, else the current workspace/candidate bytes, read through the same projection | Test 1: the unwritten case becomes SATISFIED. Test 3: the run ends SUCCESS. Live valuechain B r2: the terminal obligation would pass. That run's judge outcome is UNKNOWN (candidate NOT_RECORDED). The negative control (a consumer whose content lacks the token) must stay VIOLATED |
| B | Plan validation refuses (or plan repair rewrites) an integration relationship whose consumer has no planned artifact, or the check treats a verification consumer as verifying by gate outcome rather than by text reference | Test 2: no unsatisfiable relationship survives validation, or it is satisfied by the consumer's passing verification gate. Live spring-xml A r1: no terminal violation. That needs an owner decision on what a `configures`/`verifies` relationship to a verification unit means |

Both options touch a deterministic, terminal-required obligation, so each needs:
- a mutation that restores the empty-evidence behaviour and fails the tests;
- the original-symptom re-run (test 3);
- the adjacent `tests/test_workflow_controller_enforce.py` integration-relationship suite.

## 7. Remaining uncertainty

- Whether either live candidate was correct is UNKNOWN: the candidate bytes are NOT_RECORDED. Recording them is a
  M1 deliverable.
- Whether other relationship kinds or text-reference shapes produce false VIOLATED verdicts for consumers that did
  write files is not covered here. The stem/whole-word proxy is coarse by its own documentation.
