# PRD-022 Coding Agent Handover

## Status
VERIFIED at `ff3e6c9` (batch 5). Focused batch-5 pytest 1962 passed at `023a9cd` (the earlier focused run's 1 failure, an enforce test fixture whose bare MagicMock kernel enabled the PRD-020 verifier, fixed in `7c9da46` together with an unbound `requirement_closure_attempts` on early-stop enforce runs); full `.venv/bin/pytest` 5829 passed, 1 failed at `bcac161` (same fixture cause in `tests/test_workflow_controller.py`, fixed in `ff3e6c9`, module re-run 32/0 under pytest); live `tests/test_live_prd020_024_batch5.py` 5/5 passed, evidence in `handover/evidence/BATCH5/user-live/`; demo-03 production-profile run PASS (REQ-4 closed_by_evidence via MUTATION_SCOPE, authorized = actual = DefaultDriverService.java only, all 4 REQs resolved, all terminal gates passed, 53/53 tests, verifier 1 call 3.19s 1685/86 tokens, baseline source captured), evidence in `handover/evidence/BATCH5/demo03-production/`. Doctor `--production` was PRODUCTION_READY=false only because fallback qwen3.6:35b-a3b-q4_K_M is NOT_QUALIFIED (hidden reasoning exhausts case budgets); the run never needed it.

## Source identity
- Base revision: PRD-021 (`ccb0bcc`, `8718d42`).
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
First-occurrence ownership recovery and reviewer evidence (instruction: `tasks/PRD-022_First-Occurrence_Ownership_Recovery_and_Reviewer_Evidence.md`).

### 1. `INVALIDATION_REPEAT_THRESHOLD` stays 2
No incident argues otherwise. A test pins it, and a spy on the real classifier shows the unchanged behaviour through `WorkflowEngine`:
- first occurrence: no invalidation;
- second occurrence of the same candidate: `ARCHITECTURE_CHOICE_INVALIDATED` against the grounded owner.

### 2. Trace from the first violation to the very next retry request (proof, then one real defect fixed)
Traced through the real `WorkflowEngine`, with a Python repository: `src/pricing.py`, and `tests/test_pricing.py` importing `price`. The first candidate creates `src/discount_pricing.py` and redirects the test to it, so `find_brownfield_test_redirections` rejects it.

**Already true, now protected.** The very next Developer request carries the grounded owner's exact current source (`files_with_current_content`) and targets only the owner. The captured request shows:

| Field | Value |
|---|---|
| `known_target_files` | `src/pricing.py` |
| `implicated_files` | `src/pricing.py` |
| `operation_by_file` | `{src/pricing.py: repair_with_patch}` (patch authority over the owner only) |
| violation evidence | `owner=src/pricing.py, candidate=src/discount_pricing.py, test=tests/test_pricing.py` |
| unrelated files | none (`src/shipping.py` absent) |

No exact-source injection machinery was added.

**The defect.** That retry could not succeed:
- The redirected test and the parallel file from the rejected candidate stayed in the sandbox.
- The retry had no authority over either.
- The same check rejected every retry, even a correct owner fix. Reproduced: 8 attempts, never passing.

That is a guaranteed redundant retry, which the PRD forbids.

**The fix** is the narrowest, reusing the existing `RESTORE_PUBLIC_CONTRACT` precedent (protected callers/tests restored deterministically in the attempt's guarded staged-write batch):
- The violation's evidence is kept on state: redirected tests, abandoned parallel files, owners.
- On the next attempt, `attempt._stage_ownership_redirect_restoration` does both, in the same guarded batch:
  - restores each redirected test to its exact baseline;
  - removes each parallel file this run created. It never removes a file that exists in the workspace or had original content.
- Anything the Developer writes again in that attempt is left as written, so a repeated choice still recurs and still counts toward invalidation.
- The abandoned file also leaves the expected-files set; otherwise "INCOMPLETE GENERATION" would demand it.
- The restoration is recorded as an `ownership.redirect_restored` run event.

Result: the first corrective retry succeeds, 2 Developer calls in total. The test is back to baseline, the parallel file is gone, and the owner carries the change.

**Enforce (review correction).** An approved structured plan may declare the parallel file (a CREATE), and the enforce terminal commit materializes every planned path. A plan-declared file is therefore never deleted or dropped from the expected files. The restored test alone ends the redirect, since the test no longer references the parallel file. Tested directly on the restoration step, both with a plan (test restored, file kept) and without one (both).

### 3. Advisory post-generation near-duplicate findings
- `workflow._record_post_generation_ownership_findings` checks, after each passing candidate, every file the candidate actually created (its real content is the work text) against the grounded owners PRD-021 found before planning.
- Findings not already recorded are added to the ledger as GROUNDED, non-terminal obligations, to `ownership.finding` events and to the result.
- `ownership_findings.ownership_review_evidence(ledger, files)` renders every open finding for the run's files from the ledger, so the direct, milestone and enforce paths behave the same. It is labelled "advisory, GROUNDED suspicion - not a verified defect" and appended to the Reviewer's context: the pre-approval review and the final review.
- Findings never fail a gate or trigger a retry. Tested: the run passes, and a reviewer answering "REJECTED - duplicate" changes nothing in the ledger (still PENDING at GROUNDED authority). Only `settle_findings` changes a finding, so the Reviewer cannot upgrade a suspicion into a fact.

### 4. Human approval
When the approval gate fires anyway (human-in-the-loop, a sensitive path, the risk threshold, a process profile), the open findings are appended to what the approver sees. They never trigger the gate or decide it.

## Decisions to review
1. **Deterministic restoration instead of widening the retry's authority** to the test and the parallel file. The test is evidence and the parallel file is abandoned; neither is the retry's job. This mirrors `RESTORE_PUBLIC_CONTRACT`.
2. **Post-generation findings run where the grounded owners are known** (a run whose Planner ran on a TASK/ENHANCEMENT route). Enforce subtasks run with a predetermined plan: their plan-time findings are in the shared ledger and reach their review, but no second post-generation candidate scan runs per subtask.

## Tests (plain runner; you run pytest)
`tests/test_prd022_ownership_recovery.py`, 6 passed:
- the first corrective retry carries the exact owner source, patch authority on the owner only, and nothing unrelated; that retry succeeds with the test restored and the parallel file removed;
- a repeated violation still invalidates the choice (threshold 2, real classifier spied);
- a near-duplicate reaches the Reviewer as advisory evidence, fails no gate, and a rejecting review does not change the finding;
- a duplicate created without a planned finding is found after generation and reaches review;
- a human approver sees the finding as evidence and approves;
- a parallel file the approved plan declares is kept, and only the test is restored.

Mutation checks, each caught:
- no restoration;
- the abandoned file still expected;
- findings not in the final review;
- findings not in the approval context;
- no post-generation check.

Regression, plain runner:
- `test_architectural_choice` 7/0;
- `test_review_context` 69/0;
- `test_workflow` at baseline (see the batch record).

## Residuals
- **The invalidation instruction never reaches the Developer (pre-existing, not changed).** The invalidation's "abandon X, implement in the owner" instruction is not in the Developer's retry context: `Failure.raw_output` (the evidence line) is what the retry shows, and the gate outcome keeps only that. The retry is still scoped to the owner, and restoration now removes the abandoned file. Surfacing the message would mean widening the error text that attribution parses for file paths, and a candidate path mentioned there could become a target again. That needs its own change.

## Live test
`tests/test_live_prd020_024_batch5.py::test_prd022_duplicate_owner_first_and_second_attempt`: a real local model on the pricing fixture. It records the first and second attempt behaviour (whether the corrective retry edits the owner), and the findings in review context.
