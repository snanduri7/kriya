# LR-R1-P4: verification-only retry with no possible change

**Status:** root cause CONFIRMED by a deterministic reproducer. **No fix implemented.**
**Code basis:** `61a867f`, branch `investigation/lr-r1-reliability`.
**Reproducer:** `tests/test_lr_r1_p4_verification_only_retry_reproducer.py`. It passes on current code and pins the
live mechanism through the real WorkflowController enforce loop and the real workflow; only the model transport and
the compile/test gates are stubbed.
**Labels:** MEASURED, TRACED, INFERRED, CONFIRMED, UNKNOWN.

## 1. OBSERVE (frozen 80-run evidence)

- **6 terminal runs** with `RETRY_NO_PROGRESS_EXHAUSTED`, classification REPEATED_VECTOR: spring-boot-pagesize A r1,
  A r4, A r5, B r6, B r7, and python-symbol-zipb B r7 (MEASURED).
- **The same shape every time:**
  1. the edit subtask(s) pass;
  2. the verification-only subtask (`planned_files: []`, e.g. pagesize `s3`) fails its test verifier at attempt 1;
  3. attempts 2–4 re-run the same verifier;
  4. the progress vector is REPEATED_VECTOR three times;
  5. the run stops.

## 2. MEASURE

| Fact | Value | Source |
|---|---|---|
| Changed dimensions on attempts 2–4 | **`[]`** (nothing changed: not the workspace, not the failure signature, not the evidence fingerprint) | pagesize A r4 `retry.progress_vector` events |
| Attribution | attempt 1: tier `triage` (model judgment), `SOURCE_DEFECT`, high confidence, `OwnerController.java`; attempts 2–4: tier `full_set`, `TEST_DEFECT`, low confidence, no likely files | pagesize A r4 `gate_outcomes` |
| What happened to the attempt-1 target | `RECOVERY_GENERATION_TARGET_REJECTED ... reason=deny_all_scope - dropped from the next generation attempt's targets` (4 pagesize runs) | generate logs |
| Wall time of the three repeated attempts | 68–98 s per run (A r1 68.1, A r4 98.3, A r5 98.3, B r6 97.3, B r7 94.6, zipb B r7 89.8): about 546 s over the 6 runs | `attempt.failed` timestamps |
| Model calls in the repeated attempts | pagesize A r4/A r5/B r6/B r7: one JSON-mode qwen3.6 call after each failed verification attempt (role `unattributed`, 3 per run). Its producer was **not traced** (UNKNOWN). The reproducer makes no model call in s2, a stated difference | role metrics + log timing |
| Developer calls in the verification unit | 0 (MEASURED live and in the reproducer) | role metrics |

## 3. TRACE (61a867f)

```
enforce controller: one run_generation_workflow(current_subtask_id=s3, write_scope_mode=DENY_ALL,
  required_verification=[test]) per subtask, in dependency order; s1/s2 already passed
  -> workflow retry loop -> attempt.run_attempt
       DENY_ALL + directly executable verifier -> _run_verification_only_attempt (no Developer; increments
       attempt_number; runs the verifier) -> QualityGateFailure(type=test)
  -> retry_strategy.handle_attempt_failure
       attribution: tier not in DETERMINISTIC_ATTRIBUTION_TIERS (locator/authoritative_deterministic/
         architectural_owner/subtask_scope/subtask_dependency) -> scope_conflict_is_grounded = False
         -> no plan_scope_conflict (the cross-owner reopen path is not taken)
       DENY_ALL branch: implicated files dropped (RECOVERY_GENERATION_TARGET_REJECTED deny_all_scope)
       NO_AUTHORIZED_REPAIR_TARGET admission gate: ALLOWLIST-only "deliberately NOT DENY_ALL" (comment at
         retry_strategy.py ~1276: "continuing its loop just retries verification itself ... must not change its
         existing should_break=False behavior"); pinned by
         tests/test_workflow.py:15068 test_handle_attempt_failure_never_offers_a_deny_all_target_even_at_low_confidence
       progress vector (retry_progress): same workspace, signature, evidence -> REPEATED_VECTOR
       -> after RETRY_NO_PROGRESS limit (3): RETRY_NO_PROGRESS_EXHAUSTED -> stop
```

## 4. Answers to the required questions

1. **What policy treats a verification-only unit as retryable?**
   - `handle_attempt_failure` returns `should_break=False` for a DENY_ALL unit by explicit design (TRACED: comment and
     pinning test above). The admission gate that would stop an out-of-scope, unrepairable failure applies only to
     ALLOWLIST.
   - The retry policy (`retry_policy.decide_for_state`) has no input saying "this unit cannot mutate".
   - The loop is bounded only by the generic no-progress limit.
2. **What could change between retries?**
   - Nothing Kriya controls. There is no Developer step, the write scope is empty (DENY_ALL), the upstream units are
     finished, and the workspace and candidate are unchanged.
   - Only a nondeterministic verifier (a flaky test, environment, toolchain) could produce a different result.
   - MEASURED: `changed_dimensions` is empty on every repeat, live and in the reproducer.
3. **Does a retry re-execute any upstream producer?** No.
   - TRACED: the controller invokes each subtask once; the retry loop lives inside the verification unit's own
     invocation.
   - Reproducer: one Developer call in total, s1's.
4. **Does any retry receive new evidence?** No.
   - Same failure signature and evidence fingerprint; empty `changed_dimensions` (MEASURED).
   - Live attempts 2–4 also carried no likely files.
5. **Why is the failure not attributed back to the producer?**
   - The cross-owner route (`plan_scope_conflict`, PLAN_SCOPE_REVISION_REQUIRED, which reopens the owner) requires
     a grounding in `DETERMINISTIC_ATTRIBUTION_TIERS`.
   - The live attempt-1 attribution was model judgment (tier `triage`), so it was not admitted, and the target was
     only dropped (`deny_all_scope`).
   - Whether the full test output held a deterministic locator Kriya failed to extract is UNKNOWN. The attribution
     reasoning says the stack trace it saw was "cut off". The raw test output given to attribution is not recorded
     in full (LR-R1-M1 closes this).
6. **Meaningful retry or pure repetition?** Pure repetition: the identical verifier on identical inputs (MEASURED;
   CONFIRMED by the reproducer).
7. **What should the typed decision be when a verification-only unit cannot mutate anything?** (Proposal; not
   implemented.)
   - After the first verifier failure, a DENY_ALL unit with no deterministic cross-owner grounding should not retry.
     Nothing it can do changes the inputs.
   - It should stop with a typed reason that names the situation, e.g. `VERIFICATION_UNIT_FAILED_NO_MUTATION_PATH`.
   - Separately, the owner should decide whether a model-attributed (non-deterministic) producer should be reopened
     through the existing plan-revision path. That is a policy change, because today only deterministic grounding
     may reopen an owner.
   - A flaky-verifier allowance, if wanted, should be an explicit bounded re-check, not the generic retry loop.

## 5. HYPOTHESES and DISCRIMINATING CHECK

| # | Hypothesis | Result |
|---|---|---|
| H1 | The repeats re-run the producer or regenerate code (wasted generation) | **Rejected**: 0 Developer calls in the verification unit (live and reproducer) |
| H2 | The repeats see new evidence (e.g. a changing test outcome) | **Rejected**: empty `changed_dimensions` |
| H3 | The repeats are the generic retry loop continuing a unit that cannot change anything, by explicit DENY_ALL design, until the no-progress limit | **CONFIRMED**: the reproducer gives exactly test ×4, PROGRESS + REPEATED_VECTOR ×3 with empty dimensions, RETRY_NO_PROGRESS_EXHAUSTED |

## 6. CONFIRMED ROOT CAUSE

- A verification-only (DENY_ALL) unit is retried by the generic attempt loop after its verifier fails, although no
  input to the next attempt can differ.
- The ALLOWLIST-only `NO_AUTHORIZED_REPAIR_TARGET` stop excludes DENY_ALL by design, on the premise that such retries
  are cheap.
- MEASURED cost against that premise: about 68–98 s of repeated toolchain execution per run (6 runs), plus one model
  call per repeat in 4 runs.
- No progress is possible, and the run always ends at the generic no-progress limit.

## 7. PREDICTED FIX EFFECT (not implemented)

- **Reproducer:** `test_gate_runs` becomes `["s1", "s2"]` (one verification) instead of four, followed by a typed
  terminal reason.
- **Live:** the 6 runs end at attempt 1 of the verification unit with the same FAILED outcome, about 546 s and up to
  12 model calls sooner.
- **Unchanged:** the judge outcome, since the runs fail either way. This is an efficiency and clarity fix, not a
  success-rate fix. A success-rate change would need the plan-revision route (Q7, second point), which is a separate
  owner decision.
- **Mutation:** re-enabling the DENY_ALL retry must make the reproducer fail again.

## 8. Remaining uncertainty

- Why the pagesize verification test still failed after the edit subtasks passed is UNKNOWN. That is candidate
  correctness, and the candidates were not judged (06 §1).
- The producer of the live per-repeat qwen3.6 JSON call is UNKNOWN (not traced).
- Whether the full Maven output held a deterministic locator is UNKNOWN (output truncated before attribution).
