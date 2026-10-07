# LR-R1-P5: integration obligations judged on provider provenance and the consumer's current artifacts

**Branch:** `feature/lr-r1-p5`, based on the accepted P4 lineage `6bc47d2`.
**Commits:**
- `2628c87`: implementation and tests;
- `4db9d21`: the M1 T6-H fixture, adapted;
- `f2bb41b`: a union-of-writes guard test.

**Not pushed, not merged.** The frozen CAGC-v2 result is not touched and stays **INCONCLUSIVE**. P5 reproduces and
explains the valuechain B r2 mechanism; it recomputes nothing.
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED.

## 1. Phase 1: reproduced and traced on the accepted lineage

- **Reproducer.** `tests/test_lr_r1_p5_integration_obligation_reproducer.py` from `4283539`, unmodified: **3 passed at
  `6bc47d2`** (MEASURED). It still pins the defect.
- **Lifecycle (TRACED, `kriya/workflow/workflow_controller.py`):**

| Step | Where | Fact |
|---|---|---|
| obligation created | `plan_validation.validate_plan` | `plan.integration.<id>`, PENDING, DETERMINISTIC, `terminal_required` |
| provider/consumer ownership | plan schema (existing, structural) | `integration_relationships[].producer_subtask_ids` / `consumer_subtask_ids` / `participating_artifacts`; each subtask's `planned_files` |
| evidence collected | `established_file_context`, filled at subtask completion (`call_result["files"]`), on resume (completed subtasks' planned files) and on owner recovery | content only, no writer and no bytes identity |
| evaluated | `_evaluate_integration_obligations`, once, when the consumer completes | provider: path ∈ `established_file_context`; consumer: whole-word stem reference in `established_file_context[consumer planned paths]` |
| **where the artifact is lost** | the consumer content is joined only from paths the consumer wrote this run | a NO_CHANGE or verification-only consumer has empty content, so every producer counts as "missing" |
| decided | terminal obligations gate | VIOLATED → `TERMINAL OBLIGATIONS UNSATISFIED` |

The same trace shows two weaknesses on the provider side. The check never verified *who* established the provider
path, and never checked that the bytes the provider established were still the final ones.

- **The end-to-end scenario, before** (MEASURED, `handover/evidence/lr-r1-p5/phase1_before.json`; the measurement
  harness is `p5_measure_harness.py`, evidence only):

| Fact | Value |
|---|---|
| provider / consumer | s1 (`shop/service.py`) → s2 (`shop/controller.py`), relationship `ir1` kind `uses` |
| consumer dependency | `controller.py` (unchanged) imports `shop.service` |
| files written | s1 `shop/service.py`; s2 **none** (verified NO_CHANGE) |
| local outcomes | s1 completed, s2 completed |
| integration inputs | provider: `shop/service.py` established; consumer: **empty** |
| integration decision | **VIOLATED**, `missing=['shop/service.py']` |
| terminal | `TERMINAL OBLIGATIONS UNSATISFIED ... plan.integration.ir1`; run **failed**; nothing applied |
| model calls | 3 (Developer 2, Reviewer 1) |
| M1 | the integration evidence is not in the store; Q9 shows only the failed `terminal_obligations` gate |

**Root cause: CONFIRMED, unchanged** from the investigation: the consumer evidence is "files the consumer wrote this
run", not "the consumer's artifacts".

## 2. The fix (no schema, recorder, authority or planner change)

- **`EstablishedProvenance`.** For each artifact it records which subtask established it and its raw sha256, in the
  plan workspace. It is filled at the same three sites as `established_file_context` (completion, resume, owner
  recovery), and both production calls pass it (tripwire test). These are existing deterministic facts: the writing
  subtask, the file and the bytes.
- **Provider side.** Each required producer artifact (the producer's planned files, narrowed by
  `participating_artifacts`) must be:
  - established by a **declared producer** of this relationship (`established_by_non_provider` fails);
  - **unchanged since** (`invalidated` fails);
  - established at all (`not_established` fails).

  A provider's local pass alone is never enough.
- **Consumer side.** The **unchanged reference criterion** is applied over the consumer's **current** planned
  artifacts: written this run, else read unchanged from the workspace with the same projection. It is scoped to this
  relationship's consumer, never a union of the run's writes.
- **Consumer with no planned artifact** (verification-only). There is nothing to reference. The relationship is judged
  by the valid provider artifacts plus the consumer's own declared verification, which has passed when the check runs:
  the check runs only after the consumer completed. **With no declared verification it fails closed.**
- **Unchanged.** Callers without provenance (the pre-existing unit tests) keep the old semantics; a consumer that does
  not reference the producer still fails; write authority, planner ownership and the evaluation timing (once, at
  consumer completion) are untouched.
- **Evidence.**
  - `ObligationRecord.evidence` (an existing field) gains `provider_evidence` (state and establishing subtask per
    artifact), `consumer_evidence` (paths, `written_this_run` / `current_workspace` / `absent`, declared
    verification), `evaluation` and `failure_reason`.
  - The same decision is an ordinary mirrored run event, `integration.obligation`. Explain **Q9** lists it as
    `integration_obligations` (explain-only).
  - No DecisionLedger entry is added and no recorder kind changes.

**Semantic point for review.** For a verification-only consumer, the evidence is "valid provider artifact + the
consumer's passed declared verification". Before the fix such a relationship was unsatisfiable by construction. This
is the investigation's option B, which it flagged as an owner decision.

## 3. Before / after (same scenario, same harness; `phase1_before.json` / `phase3_after.json`)

| | Before (`6bc47d2`) | After (`2628c87`) |
|---|---|---|
| subtasks passed | 2/2 | 2/2 |
| provider evidence considered | `shop/service.py` present in established content (writer and bytes unchecked) | `shop/service.py`: established by **s1** (declared producer), state **valid** (bytes unchanged) |
| consumer evidence considered | none (s2 wrote nothing) | `shop/controller.py` from the **current workspace** (references `shop.service`) |
| final integration evidence | empty consumer content | provider valid + consumer reference found |
| global obligation | VIOLATED, missing `shop/service.py` | **SATISFIED**, missing `[]` |
| terminal run result | failed (terminal obligations), nothing applied | success; candidate applied |
| model calls | 3 (Developer 2, Reviewer 1) | 3 (Developer 2, Reviewer 1) |
| M1 | no integration evidence; Q9: failed gate only | Q9 `integration_obligations` names the obligation, provider and consumer evidence, and the result |

## 4. Tests

- **Reproducer** (Case 1, plus the verification-only shape), flipped before the fix. With behaviour-neutral name
  scaffolding, all 3 fail on pre-fix code on behaviour (MEASURED): the two unit cases come out VIOLATED, and end to
  end the run ends `TERMINAL OBLIGATIONS UNSATISFIED`. After the fix, 3 passed.
- **`tests/test_lr_r1_p5_integration_evidence.py`:** 16 tests.

| Case | Test |
|---|---|
| 2 provider artifact never established (even if referenced) → FAIL | `test_case2_…` |
| 3 the provider wrote another artifact → FAIL | `test_case3_…` |
| 4 NO_CHANGE consumer on its current file → PASS; with an invalid provider artifact → FAIL | `test_case4_…` |
| 5 verification-only consumer: needs a valid provider artifact; no verification → fails closed | `test_case5_…` (×2) |
| 6 multiple providers: only the relationship's providers count; one unreferenced → FAIL | `test_case6_…` |
| 7 an unrelated sibling writing the provider path does not count | `test_case7_…` |
| 8 provider artifact removed or rewritten after its local pass → FAIL | `test_case8_…` (×2) |
| 9 consumer = provider: same verdict as the pre-P5 check | `test_case9_…` |
| 10 no integration relationship: ledger untouched | `test_case10_…` |
| not a union of all writes | `test_another_subtasks_written_reference_is_not_the_consumers_evidence` |
| consumer written this run; consumer not referencing still fails | two tests |
| production calls pass provenance; three capture sites | tripwire |
| M1: real store, real explain, Q9 decision and evidence | `test_q9_explains_the_integration_decision` |

**M1 test fixture adapted (`4db9d21`, separate commit).** `test_lr_r1_m1_t6_fixes.py::test_h_a_failed_terminal_gate_outranks_a_successful_last_unit`
used P5's defect to make the terminal obligations gate fail; its docstring said "LR-R1-P5's shape". After the fix it
failed (MEASURED).
- **Attempt 1, rejected.** Reversing the relationship is refused by plan validation (consumer before producer).
- **Final fixture.** The same NO_CHANGE consumer, with a controller that satisfies the test without ever using the
  service the relationship names. That is a genuine violation, and it fails the gate **on pre-fix code too**
  (MEASURED).
- The precedence assertions are unchanged.

## 5. Mutation

`p5_mutation_campaign.py` → `p5_mutation_results.json` / `.txt`, at `f2bb41b`. **14 run, 14 killed, 0 survivors:**
- consumer writes only;
- all subtask writes blindly unioned;
- provider identity ignored / unrelated sibling accepted (one code path: the writer-is-a-producer check);
- wrong provider accepted (any subtask's planned files count);
- provider local PASS implies global PASS (reference check removed);
- final workspace state ignored;
- NO_CHANGE consumer treated as missing evidence;
- verification-only consumer treated as missing;
- verification-only consumer without verification passes;
- missing artifact converted to success;
- production call passes no provenance;
- resume provenance unrecorded;
- decision not mirrored to M1;
- Q9 drops integration decisions.

## 6. Gates

| Gate | Result |
|---|---|
| P5 reproducer | fail-before / pass-after |
| focused P5 | 16 passed |
| adjacent (enforce controller, controller, plan validation, planner file ownership, superseded obligations, verification scope, PRD-030 terminal services, verified-no-change, all M1, all P1, all P4, PRD-017, PRD-026, state-machine tier, `test_workflow.py`) | **1779 passed** |
| I-2 | 12 passed |
| P4 regression | PASS (all `test_lr_r1_p4_*` in the batch) |
| P1 regression | PASS (all `test_lr_r1_p1_*` + PRD-017 in the batch) |
| Ruff | All checks passed |
| Pylint | exit 0 |
| full suite | **8739 passed, 0 failed, 0 errors** (14 warnings), 530 s, at `f2bb41b`, clean tracked tree (`full_suite.txt`) |

## 7. Non-interference

- **Production change:** only `workflow_controller.py` (the provenance object, the integration evidence, and the three
  capture sites plus two calls) and `explain.py` (the Q9 view). MEASURED: `git diff 6bc47d2..HEAD` touches no P1 or P4
  module, no policy and no attempt code.
- **Unchanged:** local subtask verification, write authority, planner ownership, D1, RunRecord and the DecisionLedger
  (no new entries). Ordinary obligations without a dependency edge (Case 10), and failure on a genuinely missing or
  invalid artifact (Cases 2, 3, 7, 8), are covered by the tests above.

## 8. Remaining limits (stated, not changed)

- **Evaluated once.** The relationship is still evaluated once, at consumer completion (existing monotonic design). An
  artifact invalidated *after* that evaluation is not re-judged by this check; Case 8 covers invalidation before it.
- **The reference criterion is unchanged** (a whole-word stem proxy), with its documented coarseness.
