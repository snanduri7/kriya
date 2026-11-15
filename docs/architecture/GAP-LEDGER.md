# Gap ledger

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS findings. Nothing here is a fix; `PROPOSED` notes exist only
where a gap cannot be understood without them. A gap is not closed by being described here.

Reconciliation state of a gap: `CONFIRMED` (evidence establishes it), `NEEDS_CHARACTERIZATION` (a measurement is
missing; blocks final closure), `OWNER_RULING` (the facts are established and the classification is the owner's).
The final disposition (`FIX_REQUIRED_BEFORE_PRODUCTION`, `DISPROVED_WITH_EVIDENCE`, `INTENTIONAL_CURRENT_CONTRACT`,
`GENUINE_FUTURE_FEATURE`) is decided at Phase D.

## Slice A1

### GAP-A1-01 - No authority decides blocker continuity; the allowance is granted on a text difference

| Field | Content |
|---|---|
| Problem | The loop grants a fresh targeted and fallback-targeted allowance when the validator's normalised text differs from every earlier one. Nothing establishes that the blocker changed. The open finding, the only object that names the blocker, has no identity and no state, and is rebuilt on every authoritative failure |
| Evidence | `MEASURED` (probe, `evidence/a1/a1_probe_output.txt`): P4 identical failure at attempts 2 and 5 gives two unequal findings; P2/P5 a one-line shift keeps the family and changes the finding; P3 the same assertion text in a different test method is the same family, so a new blocker would get no fresh allowance; P1 a test-compile error and an assertion are different families. `TRACED`: `AUTH-A1-02`, `AUTH-A1-03`, `AUTH-A1-12`, `SM-A1-02`. Preserved: run 10 attempts 1-8 (`~/kriya-evidence/product-72h-confidence/ws4-same-blocker-prefix-2026-11-14/`) |
| Not established | The opposite error (the same blocker read as a new family because its message text changed) is `INFERRED` from the signature definition and was not probed |
| First wrong owner | `workflow/retry_policy.py::observe_failure_family` consumes `failure_grounding.build_failure_signature` as if it were blocker identity. The signature was designed for repeat detection ("content-local identity for retry budgets and repeated-failure checks") |
| Invariant violated | "Failure family / text signature is not blocker authority" (owner, WS4). "Evidence binds to exact identity" (cross-cutting invariant) |
| Affected flows | Every unit retry loop in all three executors; indirectly the enforce owner-recovery comparison, which uses a second, exact-text identity (`AUTH-A1-12`) |
| Severity | P2 (registry). Bounded harm: the global ceiling still ends the unit. The effect is mis-spent or withheld scoped attempts and an unreliable account of what was retried |
| Existing canonical seam | `open_findings.replaces_open_finding` is already the single owner of "does this failure speak about the candidate" and is read by both the finding update and the family inheritance. A typed blocker decision would sit beside it. A designed, unbuilt `Finding` / `FindingState` contract exists in `handover/MODULARIZATION/MODULE-CONTRACTS.md` section 12 (`DOC-A1-02`) |
| Registry rows | `RETRY-BLOCKER-IDENTITY-AUTHORITY-001` (primary), `RETRY-PROGRESS-SAME-BLOCKER-001` (superseded by it), `OPEN-FINDINGS-ORDER-RESUME-001` (its "replacement is not order-aware" half) |
| Reconciliation state | `CONFIRMED` |

### GAP-A1-02 - Retry and recovery state is not durable

| Field | Content |
|---|---|
| Problem | No checkpoint or control-state file holds retry budgets, the open finding, the family history, the progress history, the attempt number, the enforce loop's recovery counters, or the verifier semantic ledger |
| Evidence | `TRACED`: `AUTH-A1-09`, `AUTH-A1-10`, `BYP-A1-03`, `BYP-A1-11`, with the negative-control searches recorded there. Not exercised: no resume was run in A1 |
| Refinement found in A1 | The gap has two different parts. **Unit part:** no checkpoint is written inside the retry loop, so a unit interrupted mid-loop resumes from `design` with a new sandbox. The failing candidate is gone, so the finding and the family history describe nothing that still exists; what is really lost is the count of attempts already spent and the elapsed generation time. **Enforce part:** applied upstream work stays in the workspace and completed subtasks are skipped, but the owner-recovery cycle counter, the one-reopen-per-verification-unit set, the captured pre-repair owners and the VALID verifier decisions start empty. Here state that still describes the workspace is lost |
| First wrong owner | None exists: no module owns the durability of retry or recovery state. `control/state.py::ControlState` is the nearest owner (it already persists `tool_attempts`) |
| Invariant violated | "A local counter must not silently control unrelated global behaviour" is not violated; the violated statement is the architecture-decisions list that names retry state among bound identities (`DOC-A1-04`), and the owner's ruling that this is a known gap |
| Affected flows | Resume of direct, milestone and enforce runs; any future Desktop "resume" or recovery action |
| Severity | P2 (registry). The unit part is bounded by the operator having to ask for each resume. The enforce part can repeat a bounded recovery from zero and can ask a person again for a decision already given |
| Existing canonical seam | `ControlState` for the enforce part; the stage checkpoint payload (`workflow.py` `_save_stage_checkpoint`) for the unit part; the review record for VALID decisions (the ledger's docstring calls it "the durable evidence") |
| Registry rows | `RETRY-STATE-RESUME-CONTINUITY-001` (primary), `OPEN-FINDINGS-ORDER-RESUME-001` (its "not checkpointed" half) |
| Reconciliation state | `CONFIRMED` by trace; a resume exercise is still owed before a fix is designed |

### GAP-A1-03 - Allowance grants and charges leave no per-attempt record

| Field | Content |
|---|---|
| Problem | A scoped-budget reset is a log line. A charge is not recorded. The counter values are in no event or evidence record between attempts |
| Evidence | `TRACED`: `MUT-A1-02`, `MUT-A1-03` and the search recorded in `03-MUTATION-AUTHORITY.md`. Preserved: sprint log 46 needed a model-free reproducer to recover run 10's budget trajectory |
| First wrong owner | `workflow/retry_strategy.py::_record_attempt_failure` (emits no event at the reset) and `workflow/recovery_coordinator.py::_record_recovery_decision` (its payload omits the counters) |
| Invariant violated | Evidence / audit truth: a decision that changes how many attempts a run gets is not reconstructible from the run's own evidence |
| Affected flows | Diagnosis of every retry loop; any future durable retry state would need the same facts |
| Severity | P3 (evidence only; no control effect) |
| Existing canonical seam | The `recovery.decision` evidence record and the `retry.strategy_transition` / `retry.progress_vector` run events |
| Registry rows | None. New in A1. Related: `GUI-TRACE-ATTEMPTS-SEMANTICS-001`, `METRICS-RETRIES-TRIGGERED-COUNTS-TERMINAL-ATTEMPT-001` |
| Reconciliation state | `CONFIRMED` by trace; to be re-checked against the evidence schema in slice A6 |

### GAP-A1-04 - The attempt ceiling is per invocation; nothing bounds attempts per blocker across invocations

| Field | Content |
|---|---|
| Problem | "Global attempt bound" in `retry_policy` means global to one `GenerationState`. The enforce loop re-invokes units and each call has a full ceiling. A blocker that survives an owner recovery is retried under fresh budgets with no link to the attempts already spent on it |
| Evidence | `TRACED`: `AUTH-A1-07`, `AUTH-A1-11`, `BYP-A1-01` |
| First wrong owner | Not a wrong owner. Two owners with different scopes (the unit policy and the enforce loop) and no stated contract between them |
| Invariant violated | None proven. The run is bounded by the cycle caps and the run clock |
| Affected flows | Enforce runs with scope revision, owner recovery, replay or verifier repair |
| Severity | Unclassified |
| Existing canonical seam | The enforce loop's cycle caps |
| Registry rows | None. Related: `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001` (slice A4) |
| Reconciliation state | `OWNER_RULING`: is "full fresh unit budgets per re-invocation, bounded by cycle caps and the run clock" the intended contract? |

### GAP-A1-05 - A configured no-progress limit below 3 is read as 3

| Field | Content |
|---|---|
| Problem | `autonomy.max_consecutive_no_progress_attempts` defaults to 2 and accepts 1. The loop uses `max(3, value)` |
| Evidence | `TRACED`: `workflow/retry_strategy.py::_record_attempt_failure` (the comment states the reason: older configurations used 2 for a different counter); `config/config.py` (the field) |
| First wrong owner | The configuration field: its accepted range and default do not match its effective range |
| Invariant violated | "Fail closed, never approximate": an operator value is silently replaced |
| Affected flows | Every unit retry loop |
| Severity | P3. The effective behaviour is deliberate and documented in code; the mismatch is between the field and its meaning |
| Existing canonical seam | The field's validator in `config/config.py` |
| Registry rows | None. New in A1 |
| Reconciliation state | `OWNER_RULING`; to be re-read with configuration authority in slice A5 |

### GAP-A1-06 - The effective generation budget of a real launch is not established

| Field | Content |
|---|---|
| Problem | The limit is `null` by default; the packaged comment names 3600 for production; the scenario-04 template sets 2400. Which value a desktop or production launch actually runs with was not measured |
| Evidence | `TRACED`: `AUTH-A1-08` |
| Reconciliation state | `NEEDS_CHARACTERIZATION`: a model-free config-resolution probe of the launch path (`cli.py::_workflow_config` and the production profile) is needed. Deferred to slice A5 (configuration authority), where that path is traced |
| Registry rows | `DEMO-TIME-BUDGET-001` (the configuration choice itself, already classified "not a defect") |

## Slice A1b (2026-11-15) - characterization of the A1 gaps, and new gaps

Evidence: `evidence/a1/a1b_probe.py` and its output, `evidence/a1/a1b_tests_output.txt` (7 committed tests passed on
this baseline), and the line-by-line read of the enforce recovery loops recorded in `AUTH-A1-11`, `AUTH-A1-14` and
`SM-A1-04`. The A1 tables above are kept as written; where A1b changes one of them it says so here.

| Gap | A1b result | State |
|---|---|---|
| GAP-A1-01 | The "not established" line is now established: the same assertion with only its reported value changed is a new family (`MEASURED`, probe P11). So the signature errs in both directions: a different blocker with equal text gets no allowance (P3) and the same blocker with changed text gets a fresh one (P11). `SEMANTIC-SAFETY-REPAIR-CONTINUITY-001` is the same gap seen from the model's side: the open set holds one finding, so a staging rejection's prohibition leaves it as soon as any other candidate finding arrives (P10) | `CONFIRMED` |
| GAP-A1-02 | Split, per the owner's instruction. **Unit continuity:** no retry state is restored; the checkpoint payload holds none and `GenerationState` is constructed with default `RetryBudgets` on every invocation (`TRACED`; P14 for the defaults). A resumed unit reuses plan, design or a gate-passed candidate only. No resume was executed end to end: the committed resume tests either stop before any model call (`tests/test_resume_integrity.py`) or use an engine double that replaces the unit (`tests/test_workflow_controller_enforce.py`), so none can show a real unit's budgets after a resume. Missing evidence, exactly: a scripted-model run of `run_generation_workflow` interrupted inside the retry loop and resumed, asserting attempt number and budgets. It needs no code change, only a harness; it is not needed to classify the gap, because the absence of any restore path is a negative-control fact. **Enforce continuity:** the A1 statement ("applied upstream work stays in the workspace and completed subtasks are skipped") is true only for a plan run in place. In production the plan runs in a sandbox, a failed or interrupted run discards every completion, and a resume plans and runs the whole plan again (`MEASURED`, `AUTH-A1-10`). The enforce recovery state of `AUTH-A1-14` is therefore lost together with everything else. The durable-state question for enforce is not "which counters to persist" but "is accepted unit work durable at all before the terminal commit": `GAP-A1-09` | `CONFIRMED` (both parts) |
| GAP-A1-03 | Characterized. Per attempt the evidence holds: the attempt mode, the failure type, the retry action and its reason, the progress classification, the consecutive no-progress count, the changed dimensions of the progress vector (`recovery.decision`, `retry.progress_vector`, `retry.strategy_transition`). It does not hold: whether the failure opened a new family, the family signature or its digest, the counter values after the charge, or any record of the reset. `recovery.decision` is written only when attempt-evidence capture is on. A reader can replay the charge rule from the recorded modes but cannot tell a reset from the record; the trajectory is recoverable only by re-running the reducers over the raw gate output, which is what sprint log 46 did. Related registry rows confirmed by trace: `GUI-TRACE-ATTEMPTS-SEMANTICS-001` (`workflow/run_lifecycle.py` writes `attempts = retry_count + best_of_n_candidates_tried`; `workflow/run_trace.py` writes 0 on early exits) and `METRICS-RETRIES-TRIGGERED-COUNTS-TERMINAL-ATTEMPT-001` (`core/role_metrics.py` `record_attempt` counts every non-passed attempt as a triggered retry) | `CONFIRMED` |
| GAP-A1-04 | Characterized (`BYP-A1-01`): 7 unit invocations and up to 77 Developer attempts for one unresolved blocker in one stage, more with scope revisions, with no time bound unless the operator configured one. The owner's standing ruling is that a fresh full budget per re-invocation is **not** an accepted contract until characterized; it is now characterized and still has no stated contract. The cycle caps bound invocations, not attempts per blocker | `CONFIRMED`; a contract is required (Phase C) |
| GAP-A1-05 | Characterized (`MEASURED`, probe P15): configured 1 and 2 are accepted and read as 3; 5 is read as 5. Second contradiction found: `application/readiness.py` reports the configured value to the operator as the number of attempts after which a run stops. Third: `STRATEGY_TRANSITION_AFTER_NO_PROGRESS` is the constant 2, so "two recovery actions then stop on the third" is the coded contract and the field can only raise the stop, never lower it. The field's lower range (1, 2) has no meaning | `CONFIRMED`; the field contract is decided with configuration authority (A5, Phase C) |
| GAP-A1-06 | Unchanged: owned by slice A5 | `NEEDS_CHARACTERIZATION` |

### GAP-A1-07 - An unlisted non-candidate failure replaces the open finding and replenishes the scoped budgets

| Field | Content |
|---|---|
| Problem | `open_findings.replaces_open_finding` treats a failure type it does not know as a finding about the candidate. The same predicate decides whether the failure inherits the previous family. `no_progress_retry` is raised by Kriya itself before any model call (`workflow/candidate_transaction.py`: the same request would be resent; `workflow/repair_orchestrator.py`: the retry evidence is identical to the previous attempt's) and is in neither exclusion table. It therefore replaces the open finding and, being new text, opens a new family |
| Evidence | `MEASURED` on the pure owners (probe P10): after a staging rejection, a `no_progress_retry` built as its producers build it replaces the open set, and scoped budgets at `targeted=3, fallback one-shot used` become `targeted=0, one-shot available`. `TRACED`: the call order in `retry_strategy._record_attempt_failure`. For the `ANCHOR_CONTEXT_NOT_ESCALATED` variant the forced strategy transition closes the targeted budget again in the same recording, so what remains replenished is the fallback one-shot. Not executed inside a full retry loop. Historical: OPEN-ITEMS OI-78 recorded the replacement on 2026-10-19 |
| Relation to closed work | Same mechanism as `NON-CANDIDATE-FAILURE-SCOPED-RESET-001` (closed for resource, internal and delivery types). That correction enumerated types; this type was not enumerated, and the nine closing tests do not name it |
| First wrong owner | `open_findings.replaces_open_finding`: one predicate with two consumers whose safe defaults are opposite. "Keep an unknown finding" is safe for memory. "An unknown failure may open a family" is unsafe for an allowance. Invariant violated: "fail closed, never approximate" (an unknown type widens a budget) |
| Affected flows | Every unit retry loop |
| Severity | P2 candidate (same class as the run-10 defect; bounded by the unit ceiling) |
| Existing canonical seam | The predicate itself and its exclusion tables |
| Registry rows | None for the allowance effect. `SEMANTIC-SAFETY-REPAIR-CONTINUITY-001` records the finding-replacement half |
| Reconciliation state | `CONFIRMED` at the owner level; a deterministic loop-level reproducer is the first step of any fix |

### GAP-A1-08 - The per-requirement recovery cap cannot fire

| Field | Content |
|---|---|
| Problem | The owner-recovery loop refuses to reopen an owner when the requirement's ledger history has 3 entries. The requirement id changes on every evaluation, so the history read is always empty. The comment above the loop still names this cap as the source of boundedness |
| Evidence | `TRACED` (`AUTH-A1-11`); `MEASURED` (probe P13; the committed test's own docstring and assertion of three distinct requirement ids) |
| Consequence | No unbounded behaviour: the 3-cycle loop and the run cap bound it. The effect is a decision the code and its comments attribute to an owner that never decides, and a second identity for "the same requirement" (the generation-suffixed id) that by design never recurs |
| First wrong owner | `workflow/enforce_loop.py`, the `prior_attempts` check |
| Severity | P3 (dead decision, misleading contract text) |
| Registry rows | None. New in A1b |
| Reconciliation state | `CONFIRMED` |

### GAP-A1-09 - Accepted unit work is not durable before the terminal commit

| Field | Content |
|---|---|
| Problem | In an enforce run every unit's accepted, applied work lives in the plan sandbox until the whole plan passes the terminal gates. When any later stage ends without `completed`, for any reason including the time budget, the plan does not commit, the completions are discarded, and a resume starts the plan again. A unit that stops for a reason that is not a scope conflict has no enforce-level recovery route (`FR-A1-21`, `FR-A1-22`) |
| Evidence | `TRACED`: `MUT-A1-17`, `AUTH-A1-10`. `MEASURED`: the sandbox resume tests. Preserved: `ENFORCE-UPSTREAM-WORK-PRESERVATION-001` records four live occurrences (C2-S2_A, C2-S4_A, C2-S4_B, Scenario-04 run 1 on 2026-11-09, where three accepted units were discarded when the fourth hit the time budget) |
| Is it a defect? | The all-or-nothing commit is a deliberate contract (PRD-004: the real workspace is immutable until every terminal gate passed; PRD-008: a sandbox-only completion is not resumable). What is not decided is whether accepted unit state may be **preserved for review or resume** without being committed. The registry row asks exactly that and has waited for an owner decision since 2026-10-09 |
| First wrong owner | None: two contracts (atomic commit, resumable progress) with no stated relation |
| Severity | P2 (registry): cost and convergence, never a false success |
| Existing canonical seam | The plan sandbox and `ControlState.subtask_completion_scope` |
| Registry rows | `ENFORCE-UPSTREAM-WORK-PRESERVATION-001` (primary); the enforce half of `RETRY-STATE-RESUME-CONTINUITY-001` |
| Reconciliation state | `OWNER_RULING` (a product-policy choice; carried to Phase D item O unless Phase B evidence decides it) |

### Corrections to the A1 checkpoint (`82eb96c`), found in A1b

| A1 statement | What is true | How it was missed |
|---|---|---|
| "The enforce loop passes no `resume` flag to a unit" (`AUTH-A1-10`, lifecycle answer 12) | The flag is forwarded with the caller's keywords (`BYP-A1-12`) | The negative control searched for the literal `resume=` and could not see a forwarded dictionary. Negative controls for "nobody passes X" now also search for keyword forwarding at the call site |
| "Applied upstream work stays in the workspace and completed subtasks are skipped" (`GAP-A1-02`, `SM-A1-03`) | True only for an in-place plan; production discards and reruns (`AUTH-A1-10`) | The resume block was read without the terminal block that discards sandbox completions |
| "Per obligation: prior attempts counted from the obligation ledger's history" listed as a bound (`AUTH-A1-11`) | The check exists and cannot fire (`GAP-A1-08`) | The bound was located by search and not read |
| "the same blocker read as a new family ... `INFERRED`" (`GAP-A1-01`, `SM-A1-02`) | `MEASURED` (probe P11) | Not probed in A1 |

### Contradictions H-A1-1 to H-A1-4: owner rulings received 2026-11-15

| ID | Contradiction | Ruling / result |
|---|---|---|
| H-A1-1 | `MODULE-CONTRACTS.md` section 12 `Finding` / `FindingState` vs the WS4 ruling rejecting a blocker registry | Ruled: section 12 is a stale AS-IS design, not implementation, and is not to be resurrected merely because a document described it. The blocker contract is designed from the truth owner in Phase C |
| H-A1-2 | `KRIYA_ARCHITECTURE_DECISIONS.md` lists retry state among bound identities; the code persists none | Ruled: the documented binding is currently unimplemented. `GAP-A1-02` |
| H-A1-3 | One registry resume gap, two real ones | Ruled: split into unit continuity and enforce continuity. Done above; the enforce half turned out to be `GAP-A1-09` |
| H-A1-4 | Fresh unit budget per enforce re-invocation | Ruled: not an accepted contract until characterized. Characterized in `BYP-A1-01`; contract required in Phase C (`GAP-A1-04`) |

### Disproved or stale in A1

| Item | Finding | Label |
|---|---|---|
| Sprint log 46: "no other writer zeroes `targeted_retry_count` or clears `fallback_targeted_attempted`" | Confirmed, not disproved: the search found only `retry_policy.reset_scoped_budgets_for_new_family` | `TRACED` |
| `NON-CANDIDATE-FAILURE-SCOPED-RESET-001` (CLOSED) | Holds on this baseline: its 9 tests pass and probe P7 shows a budget refusal does not replace the finding | `MEASURED` |
| `MODULE-CONTRACTS.md` section 12 as a description of current code | Stale as a description: the module does not exist | `TRACED` |
