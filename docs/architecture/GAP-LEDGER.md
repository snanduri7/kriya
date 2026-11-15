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

### Contradictions that need an owner ruling (A1)

| ID | Contradiction | Sources |
|---|---|---|
| H-A1-1 | An existing design document specifies a `Finding` with an ID and a state machine (OPEN, TARGETED, RESOLVED, SUPERSEDED, REFUSED). The WS4 ruling of 2026-11-14 rejected a blocker registry and a typed key as blocker authority. The two cannot both be the architecture | `DOC-A1-02`; sprint log 46 "Rejected / not authorized by the owner" |
| H-A1-2 | `handover/KRIYA_ARCHITECTURE_DECISIONS.md` lists retry state among the identities the system binds; the code persists none | `DOC-A1-04`; `AUTH-A1-09` |
| H-A1-3 | The registry describes one resume gap. The trace shows two different ones (unit and enforce) with different consequences. They may need different dispositions | `GAP-A1-02` |
| H-A1-4 | Is a full fresh unit budget per enforce re-invocation the intended contract? | `GAP-A1-04` |

### Disproved or stale in A1

| Item | Finding | Label |
|---|---|---|
| Sprint log 46: "no other writer zeroes `targeted_retry_count` or clears `fallback_targeted_attempted`" | Confirmed, not disproved: the search found only `retry_policy.reset_scoped_budgets_for_new_family` | `TRACED` |
| `NON-CANDIDATE-FAILURE-SCOPED-RESET-001` (CLOSED) | Holds on this baseline: its 9 tests pass and probe P7 shows a budget refusal does not replace the finding | `MEASURED` |
| `MODULE-CONTRACTS.md` section 12 as a description of current code | Stale as a description: the module does not exist | `TRACED` |
