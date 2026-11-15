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

## Slice A2 (2026-11-15) - semantic contract, generated verifier, attribution

Evidence: `evidence/a2/` (probe P1 to P10 and output; 7 committed tests passed unchanged on this baseline; citation
check). Rows: `AUTH-A2-01..13`, `SM-A2-01`, `MUT-A2-01..06`, `FR-A2-01..11`, `BYP-A2-01..09`.

### Mandatory row `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001`: decision C (the row conflates two issues)

| Half of the row | Finding | Label |
|---|---|---|
| "A model-generated verifier assertion drives repair with no proof that it is grounded" (verifier authority) | Closed by WS3 for every test the derivation can map: `VERIFIER-SEMANTIC-REVIEW-001` and `VERIFIER-SEMANTIC-AUTHORITY-001` are CLOSED in the registry and the code is on this baseline (`AUTH-A2-06` to `AUTH-A2-09`). The run-10 sequence (a never-accepted failing test becomes the repair objective for 7 attempts) cannot occur for a mapped subject: unattended it stops before any retry, attended a person decides first. The row's own status text ("characterization not started; nothing implemented") is stale bookkeeping. **Residual:** unmapped tests still take the pre-WS3 path (`GAP-A2-02`) | `MEASURED` (committed tests, probe) + `TRACED` |
| "The sealed verification contract holds natural-language sentences with claim classes only ... no assertion can be grounded deterministically against it" (semantic-contract authority) | **Still true and untouched by WS3.** WS3 did not create a contract; it put a person behind each generated verifier. `GAP-A2-01` | `MEASURED` (P8, P9) + `TRACED` |

So neither A (stale: WS3 closed it) nor plain B describes the row. The row should be split at Phase D into the
closed verifier-authority issue with its residual (`GAP-A2-02`) and the open semantic-contract issue (`GAP-A2-01`).
No registry edit is made in Phase A.

### GAP-A2-01 - No canonical product-semantic contract exists before the Developer or the verifier is generated

| Field | Content |
|---|---|
| Problem | Before the first model call Kriya seals what each of the user's sentences claims and how the claim could be verified. For a general behaviour statement with no operator artifact it seals nothing about what the product must do: no values, ranges, field semantics or edge cases, and no record that the sentence has more than one reading. When such a run is admitted, the first concrete semantics are written by models (Planner criteria, Developer code, Developer test). The generated verifier is then the first executable statement of the product's semantics, which is the forbidden order of the A2 invariant |
| What WS3 changed and what it did not | WS3 guarantees that a mapped generated verifier never acts as authority without a person. It is a control **after** generation, per verifier file, per run, in memory. It does not create a contract: the person is shown the goal text as the "canonical contract" and judges the model's reading against their own intent; the decision is not stored as contract, is not given to the Developer that writes the production code, and does not survive the run (`MUT-A2-02`). Two verifier files of one run can be accepted on two different readings of the same sentence, and nothing detects it |
| Evidence | `MEASURED` (P8, P9): a sealed entry's fields are text, claim kinds, strength, closers, residual, ambiguity, disposition and bookkeeping; for the percent goal and for a binary-header goal of run 10's shape every behaviour entry is `AUTHORITY_REQUIRED` / GENERAL with no closer. `TRACED`: `AUTH-A2-02` to `AUTH-A2-05`, `AUTH-A2-07`. Preserved: run 10 (`~/kriya-evidence/product-72h-confidence/scenario-a-run10-2026-11-13/CLASSIFICATION.md`, not re-read in A2; cited from the registry row) |
| When it is reachable | (a) policy `record`: always (`BYP-A2-04`, `FR-A2-11`). (b) policy `block`: the goal is refused before any model call unless the operator supplies authority, so the gap becomes "the only way to state semantics is a complete external artifact"; it remains reachable for a test admitted by a structural closer (`BYP-A2-05`). Which policy a production or desktop launch really runs under is a configuration-authority fact for slice A5 |
| Not established | Whether the Planner's criteria reach the Developer's prompt and in what form (slice A4). Whether the desktop launch runs under `block` (slice A5) |
| First wrong owner | None exists. The nearest owners are `contract_compilation.py` (it owns admission and already types the missing authority as `acceptable_authorities` and authority requests) and `requirements.py::behavior_strength` (it already decides that a statement is GENERAL) |
| Invariant violated | "A generated executable verifier must never be the first creator of required product semantics" (owner, A2). "Model text is evidence, never authority" holds formally (nothing model-written closes a requirement) and fails in effect (model-written semantics steer every attempt of the run) |
| Affected flows | Every admitted run whose goal has a general behaviour statement: Planner, Developer, generated verifier, semantic review, attribution, repair |
| Severity | P1 candidate (the registry row is P1 / blocking) |
| Existing canonical seam | The sealed contract and its authority-request mechanism (`authority_request.py`): a typed request for the missing authority is already produced on refusal. The human semantic review is a second seam: it already binds a person's judgement to goal and contract digests |
| Registry rows | `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001` (its second half), `REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001` (the same absence seen from requirement closure), `GOAL-EXAMPLE-COMPILER-COVERAGE-001` (one of the few ways to state semantics is narrow) |
| Reconciliation state | `CONFIRMED`. Contract required in Phase C ("semantic contract authority before Developer / verifier") |

### GAP-A2-02 - Generated-verifier identification fails open when an executed identity does not map

| Field | Content |
|---|---|
| Problem | A failed executed test identity that maps to no run-authored file is treated as a pre-existing test. The derivation cannot tell "the run did not write this test" from "the run wrote it and the identity did not map", and raises an unknown only for double mapping and unreadable files. Two reachable causes: a `src/` layout (the parser drops `src/`, pytest does not) and any test language without a structural grammar |
| Evidence | `MEASURED` (P5, P7; P1 to P4 and P6 are the controls that do map). `TRACED`: `verifier_semantic.py::derive_executed_verifiers`, `code_intel/parsing.py::python_module_name`, `tools/test_execution.py::_identity`. Fall-through to the historical path: `MEASURED` by the committed test `test_a_failure_with_no_run_authored_executed_verifier_keeps_the_historical_retry` |
| Not established | That a live run produced this (no run evidence). The identity Kriya's own gate reports for a `src/` layout (the probe ran plain pytest from the project root) |
| Consequence | For those tests the pre-WS3 behaviour of the registry row is back: an unaccepted model-written assertion is an authoritative failure, attribution (possibly a model tier, `AUTH-A2-10`) picks targets, the Developer may rewrite the test or the code, and a passing one is applied without a semantic decision. No false requirement closure follows (a model-written test closes nothing) |
| First wrong owner | `verifier_semantic.py::derive_executed_verifiers`: "no mapped file" is read as a negative fact about authorship |
| Invariant violated | "Fail closed, never approximate"; "UNKNOWN fails safely" |
| Severity | P2 candidate: a safety owner with a silent hole, on layouts and languages Kriya otherwise supports |
| Existing canonical seam | The derivation already has an `undetermined` result and the seams already stop on it. The write journal knows every run-authored path; the pre-run baseline report knows which identities existed before the run |
| Registry rows | None. New in A2. Residual of `VERIFIER-SEMANTIC-AUTHORITY-001` (CLOSED) and of `WAVE5-O2-GENERATED-TEST-WEAKENING-001` |
| Reconciliation state | `CONFIRMED` at the owner. A deterministic loop-level reproducer (a scripted unit whose test sits under `src/`) is the first step of any fix |

### GAP-A2-03 - What an unattended run and a direct run can still do once a test is written is decided, but stated nowhere as a product contract

| Field | Content |
|---|---|
| Facts | (1) Unattended, any mapped run-authored test that a gate executes ends the unit for review, passing or failing: an unattended run cannot complete a goal that makes it write and run a test (`FR-A2-01`, `FR-A2-06`). (2) In a direct run, attended or not, a failing mapped run-authored test always ends the run: there is no regeneration and no production owner without a plan (`FR-A2-03`, `FR-A2-04`). The direct path is the desktop path. (3) Only an attended enforce run can repair from a generated test's failure |
| Evidence | `MEASURED` (4 of the 7 committed tests); `TRACED` (`AUTH-A2-08`, `AUTH-A2-09`, `verifier_semantic.py::unit_semantics`) |
| Is it a defect? | No: each fact follows the owner's WS3 rulings ("unattended and undecided ends needs-review"; a direct run has no validated plan owner). What is missing is the stated contract and its consequence for the product: (1) removes "unattended completion with tests" as a capability, (2) makes direct and enforce differ in what a run can recover from |
| Relation | Direct / enforce divergence is the subject of `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001` (slice A4). Together with `AUTH-A2-03` under `block`, an unattended production run can complete only goals that are fully closable by operator authority or anchored grammar and whose candidate contains no executed run-authored test |
| Severity | Unclassified |
| Reconciliation state | `OWNER_RULING` (Phase D item O): confirm these as the intended current contract, or name the capability that must exist before production |

### RECOVERY-UPSTREAM-OWNER-ROUTING-001 (handed over by A1): resolved as superseded

The missing evidence was the attribution tier of unit s2 in C2-S4_A-c3. It cannot be obtained: the preserved
classification (`handover/evidence/backend-final-closure-005-repair-003/final-twelve/C2-S4_A-c3.CLASSIFICATION.md`)
records no tier and states "Authority stores: run id only" (`MEASURED`, file read). It also no longer decides
anything: on this baseline a failing Java test written by unit s2 is a generated-verifier subject, so the unit stops
for review (unattended) or a person decides and the strict owner predicate, not the attribution ladder, names
`PathCompiler.java`'s owner (`AUTH-A2-09`, `FR-A2-02`); and the old stop now requires a deterministic tier
(`ATTRIBUTION-MODEL-CLAIM-HARD-STOP-001`, CLOSED). The cross-unit continuation of `FR-A2-02` was traced, not
executed.

### Documents reconciled in A2

| ID | Document | Claim | Reconciliation (label) |
|---|---|---|---|
| DOC-A2-01 | Registry row `GENERATED-VERIFIER-CONTRACT-AUTHORITY-001`, `closure_evidence` | "characterization of the change not started; nothing implemented" | Stale: WS3-B and WS3-C are implemented and closed under two other rows (`TRACED`) |
| DOC-A2-02 | Same row, `target_scope` | "one self-repair only for NON-semantic construction errors" | Not implemented, and superseded: the committed test `test_a_test_source_compile_failure_establishes_no_subject_and_the_policy_owner_has_no_phase_rule` records the owner's correction of 2026-11-14 that the phase establishes no subject. Construction errors take the ordinary retry (`BYP-A2-03`). Which statement is the current ruling should be confirmed at Phase D (`TRACED`) |
| DOC-A2-03 | `handover/claude-context/requirements-and-brownfield.md`, "Claims (FS-1C1)" and "Claim strength (B2-COV)" | "B3 has no producer yet"; "human approval: not implemented" | Stale sentences: the same file's B3 section and `workflow/acceptance_approval.py`, `contract_compilation.py` (`CLOSER_ACCEPTANCE_APPROVAL`) show it implemented (`TRACED`) |
| DOC-A2-04 | `workflow/verifier_replay.py` module contract | "a replay that FAILS ... is evidence against the owner's repair" | Current, with a precondition the text does not state: it holds as authority only for a verifier a person accepted. For an unmapped verifier (`GAP-A2-02`) a never-accepted test still has that role (`TRACED`) |
| DOC-A2-05 | `handover/claude-context/requirements-and-brownfield.md` as a whole | Describes requirement lineage and closers | Matches the code where A2 read it (`AUTH-A2-01`). It does not describe the sealed verification contract's admission rule or WS3; whether another context file does was not checked in A2 |

## Slice A3 (2026-11-15) - candidate, approval, apply, replay, final result

Evidence: `evidence/a3/` (probes P1 to P8 with output, two committed tests re-run on this baseline). Every gap below
uses `AS-IS -> GAP -> REQUIRED CONTRACT -> IMPLEMENTATION DELTA -> ACCEPTANCE PROOF`. The last three are `PROPOSED`
outlines only: Phase C designs them from the truth owners backward and nothing here is implemented or authorized.

### The four mandatory rows

| Registry row | A3 result | Evidence | Gap |
|---|---|---|---|
| `REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001` | **Confirmed, current.** Under the default policies a direct run reports `quality_gates_passed: True` with its only requirement `unverified`, truth `OPEN_REQUIREMENTS`, and the candidate applied (a substitute `app.py` beside an unchanged `main.py`). Under `requirement_unverified_policy = block` the same goal is refused at admission and nothing is generated. The enforce aggregate and the milestone integration unit follow the same rule | `MEASURED` P8, P4; committed tests `test_F_*` (2 passed); `TRACED` `AUTH-A3-09`, `AUTH-A3-11` | `GAP-A3-01` |
| `MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001` | **Measured; no longer unknown.** (1) A refused final review of an applied unit makes that unit a non-success on the direct and milestone path. (2) It does **not** discard other units' accepted work: earlier units stay VERIFIED and committed, the refused unit's own bytes stay in the workspace, nothing is rolled back. (3) The plan stops: the unit is FAILED, later units are BLOCKED, the milestone is not completed, its checkpoint is deleted. (4) So the review is authoritative for unit status and plan continuation on that path and advisory under enforce, for the identical unit result. The owner ruling the row asks for is still owed | `MEASURED` P1, P2, P3; `TRACED` `AUTH-A3-10`, `SM-A3-05` | `GAP-A3-03` |
| `TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001` | **Confirmed, current.** After the terminal gates the enforce tail has two branches, commit or discard. A gap of any of the eight gates ends the run `failed`, discards every unit's accepted work in the plan sandbox and removes the sandbox. No unit is reopened even when the gap text names a precise defect | `TRACED` `SM-A3-04`, `FR-A3-15` (not executed: an enforce run was not driven) | `GAP-A3-02` |
| `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001` | **Confirmed, current, and wider than the row.** The `approval.decision` event carries `outcome` and `triggers` only. Its consumers are still the two metrics modules. The live decision is bound to bytes only indirectly: the commit equals the pre-approval binding (`AUTH-A3-02`), and no record relates the approval to that binding or to the commit's candidate hash. Five functions digest "the candidate" and yield five unrelated values for one file. A desktop run records the digest of the presented diffs and keeps the diff text; a terminal run records no digest at all | `MEASURED` P5, P6; `TRACED` `AUTH-A3-13` | `GAP-A3-04` |

### GAP-A3-01 - "Success" and "applied" do not mean the original requirements were verified

| | |
|---|---|
| AS-IS | A unit succeeds when its gates passed, its candidate was committed and its review was performed (`AUTH-A3-10`). A run succeeds when its units and terminal gates passed and the commit completed (`AUTH-A3-11`). Requirement outcomes block only where the policy says so: VIOLATED always; PENDING, UNKNOWN and UNVERIFIED only under the `block` policies (`AUTH-A3-09`). The defaults are `record`; the production profile sets `block`. The typed truth is computed once and reported beside the status; "it never changes the lifecycle status". `TerminalGateReport.requirements_verified` is evidence only and does not enter `commit_eligible` |
| Evidence | `MEASURED` P8 (success with `OPEN_REQUIREMENTS`, wrong file applied), P4 (`commit_eligible` True with `requirements_verified` False; PENDING blocks only under `unknown_policy = block`); `TRACED` both reporters |
| GAP | (1) The words SUCCESS and APPLIED are read as "the request was satisfied" and the code does not establish that. (2) The lifecycle has no state for "applied, requirements open, needs review" (registry `RUN-TERMINAL-NEEDS-REVIEW-AFTER-APPLY-NOT-REPRESENTABLE-001`). (3) Under `record`, UNKNOWN fails open for the result status. (4) Which policy a production and a desktop launch really run under is not established (handed to A5 by A2) |
| First wrong owner | The status rule of `AUTH-A3-10` / `AUTH-A3-11`: it is the one place that turns evidence into the word a person reads, and it does not consume requirement truth |
| Violated invariant | The final result consumes verified evidence and never implies more than was established |
| Affected stages | STAGE-FINAL-VERIFICATION, STAGE-TERMINAL; all three executors |
| REQUIRED CONTRACT (`PROPOSED`) | The terminal status must be a function of commit state **and** requirement truth, with a distinct terminal value when the candidate is applied and a mandatory requirement is open; or `block` must be the only supported policy. The choice is the owner's (the registry row records that `block` "would fail nearly every demo run today") |
| IMPLEMENTATION DELTA (`PROPOSED`) | One owner (`requirement_truth`) already exists; the delta is in the two status rules and the lifecycle vocabulary, not in a new authority |
| ACCEPTANCE PROOF (`PROPOSED`) | The P8 scenario must not end in a status a person reads as verified; mutation: removing the truth input from the status rule must fail a test on each executor |
| Registry rows | `REQUIREMENT-CLOSURE-UNVERIFIED-RECORDED-AS-SUCCESS-001`, `INTEGRATION-PASS-REQUIREMENT-POLICY-001` (same root, milestone executor), and from A2 `REQUIREMENT-BEHAVIOR-CLAIM-NO-DETERMINISTIC-CLOSER-001` (why almost every behaviour requirement stays open) |
| Severity (candidate) | P1: product truth. Owner policy choice required (Phase D item) |

### GAP-A3-02 - Nothing can repair a candidate that fails an enforce terminal gate

| | |
|---|---|
| AS-IS | `SM-A3-04`: terminal gates run once, after the last unit. A gap discards the plan sandbox and every unit's accepted work (`GAP-A1-09`) |
| Evidence | `TRACED` |
| GAP | A defect that only the whole-candidate check can see (an original requirement, a migration residue, a terminal obligation) has no route back to its owning unit. The run cost is lost; a re-run starts from the workspace's original state. Fail closed, never a false success |
| First wrong owner | None is wrong: the terminal gate decides correctly. The missing piece is a transition from a terminal gap to an owner, which no state machine defines |
| REQUIRED CONTRACT (`PROPOSED`) | Either state that a terminal gap is final (intentional contract, documented), or define one bounded re-entry: a typed gap, an owner resolved by the existing owner authority, a charge against an allowance, and the terminal gates re-run on the new candidate. It depends on `GAP-A1-01` (blocker identity) and `GAP-A1-09` (durability of accepted unit work) |
| ACCEPTANCE PROOF (`PROPOSED`) | A scripted enforce run whose only defect is a terminal requirement gap: either a typed, documented final stop, or one repair and a second terminal pass; never an unbounded loop |
| Registry rows | `TERMINAL-REQUIREMENT-FAILURE-NO-REPAIR-001` |
| Severity (candidate) | P2: efficiency and completion, not safety |

### GAP-A3-03 - The final review decides unit status on two executors and is advisory on the third

| | |
|---|---|
| AS-IS | `final_workflow_quality_passed` folds in "no final-review refusal". Direct and milestone read that field; enforce reads `enforce_unit_gates_accepted`, which accepts an applied unit whose only missing part is the review (owner ruling R2, 2026-11-11, scoped to enforce) |
| Evidence | `MEASURED` P1 (the fold), P2 (same result, two answers), P3 (milestone plan stops, earlier unit stays VERIFIED) |
| GAP | One truth ("was this unit's work accepted?") has two rules. On the milestone path the workspace holds the unit's committed bytes while the run record says the unit FAILED and the milestone is not completed; a later `--from-milestones` run meets committed paths that no completed milestone accounts for (the ledger does record the commits with a failed loop outcome). The Reviewer's verdict text never decides anything on any path; only the absence of the call does |
| First wrong owner | `RunState.final_workflow_quality_passed`: it mixes "the candidate is committed and verified" with "an advisory call completed" |
| REQUIRED CONTRACT (`PROPOSED`) | One predicate for "unit accepted" used by every executor, with the review's absence reported and not folded in; or an explicit owner ruling that the review is authoritative on the direct and milestone paths, written as a contract. Open owner ruling (the registry row) |
| ACCEPTANCE PROOF (`PROPOSED`) | P2 must print the same answer for both readers; P3 must either complete unit 2 with an advisory note or stop with a documented, typed reason |
| Registry rows | `MILESTONE-UNIT-REVIEW-REFUSAL-FOLD-UNMEASURED-001` |
| Severity (candidate) | P2 |

### GAP-A3-04 - A candidate has five identities and no record links an approval to the bytes committed

| | |
|---|---|
| AS-IS | `MEASURED` P6: the approval diff digest (launch record, decision file), the verification binding digest (memory), the commit candidate hash (RunRecord cycle, commit evidence), the checkpoint candidate digest and the per-file staged digests are five different values computed by five functions from different serializations. The commit guard compares binding with binding and is sound. The approval is tied to the binding only by ordering (`BYP-A3-15`) and, in held mode, by the guard. The `approval.decision` event has no digest (P5) |
| GAP | (1) Audit: after the fact nobody can show from the records which bytes a person approved in a terminal run, or that the desktop-approved diff is the committed batch. (2) Authority, narrow: the approved digest is never compared with what is verified or committed; the residual window needs the sandbox to change and change back. (3) The diff shown is text and does not show a mode or binary change that the commit carries (`BYP-A3-16`) |
| First wrong owner | `stage_apply.py::approval_gate`: it re-reads the sandbox to build the diff instead of deriving the approval subject from the bound batch, and it records no identity |
| REQUIRED CONTRACT (`PROPOSED`) | One candidate identity (the verification binding) is the approval subject: the diff is rendered from the bound batch, the approval record carries the binding digest, and the commit evidence carries the same digest, so approved = verified = committed is checkable from records. The registry row's own revisit condition ("if the trace must show WHICH bytes were approved") is met by the documentation standard of this closure |
| IMPLEMENTATION DELTA (`PROPOSED`) | A field on the event and on the commit evidence, and the diff source; no new owner |
| ACCEPTANCE PROOF (`PROPOSED`) | From a stored run alone: the approval record's digest equals the commit evidence's digest. Negative control: change one sandbox byte between the bind and the diff and the approval is refused as stale |
| Registry rows | `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001`, `GUI-REVIEW-APPLY-BINDING-001` |
| Severity (candidate) | P2 (audit), P3 (the residual window) |

### GAP-A3-05 - Under enforce, source approval is per unit: no plan-level subject, and the size trigger can be split

| | |
|---|---|
| AS-IS | `BYP-A3-01`, `BYP-A3-02`. Each unit's gate measures that unit's diff. The terminal commit asks nobody |
| Evidence | `TRACED` (not executed) |
| GAP | In `guardrails` mode the "large change needs a person" rule depends on how the Planner cut the work: the same total change asks under a direct run and not under an enforce plan of small units. In human-in-the-loop mode a person sees N unit diffs and never the whole change that is committed |
| First wrong owner | None in code: the gate does what it was written to do. The plan-level approval subject does not exist |
| REQUIRED CONTRACT (`PROPOSED`) | Owner policy: is the approval subject the unit or the plan's terminal candidate? If the plan: the triggers are evaluated on the terminal batch before `MUT-A3-04` |
| Registry rows | None found; new in A3 |
| Severity (candidate) | P2: authority. Owner policy choice (Phase D item) |

### GAP-A3-06 - The review text attached to a result can describe something other than the final state

| | |
|---|---|
| AS-IS | (1) `FR-A3-11`, `BYP-A3-14`: a pre-approval review (pre-build in held mode) is returned as the final review, including after the candidate failed terminal regression, with `review_included_in_approval: True`. (2) `stage_review.py` attaches whatever the Reviewer returned and never looks at a truncation signal, so a review cut at the output cap is shown to the approver as a complete report (the registry row measured one such run) |
| Evidence | `TRACED`; golden pin for (1); negative-control search for (2) |
| GAP | Advisory text only, but it is the text a person approves from |
| REQUIRED CONTRACT (`PROPOSED`) | A review is labelled with the candidate state it saw (pre-build, post-gates, rejected) and with whether it is complete |
| Registry rows | `KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001`, `REVIEWER-OUTPUT-CAP-RUNAWAY-LATENCY-001` (the attachment half; the latency half is slice A6) |
| Severity (candidate) | P3 |

### GAP-A3-07 - In held mode the attempt's closing evidence is written before its executing gates run

| | |
|---|---|
| AS-IS | `attempt.closed` and the obligations snapshot are emitted when `run_attempt` returns (`attempt.py`: `run_attempt` is wrapped by `_attempt_evidence_boundary`); the held gates run later inside `accept_candidate`. The gate outcomes themselves are recorded when they run |
| Evidence | `TRACED` (decorator position and call order); not executed |
| GAP | The attempt's closing record reads "candidate gates not passed" for an attempt whose gates later passed. Observational |
| Registry rows | `EVIDENCE-HELD-GATES-001` |
| Severity (candidate) | P3 |

### Registry rows of slice A3 that did not need a gap

| Row | Result (label) |
|---|---|
| `HITL-RESUME-DEFERRED-GATES-001` (handed over by A1) | Characterized (`TRACED`, `SM-A3-01` resume paragraph, `FR-A3-20`): the checkpoint follows the held gates, so a resume cannot skip an unrun gate; a crash during the review loses the candidate; a resumed held candidate is approved again, POM validation runs again, the executing gates are skipped only on matching fingerprints, closers and terminal regression run again. No committed test of the combined path was found by name. The row's Best-of-N remark was not re-traced (default 1) |
| `GUI-REVIEW-LOCAL-TRUST-001` | Stale as written: decisions no longer travel through a file. They arrive over the launch's private review channel with a one-shot in-memory capability; the decision file is audit output and is never read; there is no `launch-decision` command at the baseline (search). The channel's own claim excludes ptrace and account compromise (`AUTH-A3-04`) |
| `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001` | Confirmed current (`BYP-A3-06`); open owner decision |
| `MANAGED-SERVICE-APPROVAL-001` | Fail-closed constant present; owner-approved deferral; `BYP-A3-17` notes the non-held modes |
| `DESKTOP-LOOKUP-QUERY-APPROVAL-001` | Confirmed current (`BYP-A3-11`): four review kinds, no lookup kind |
| `ENFORCE-CLOSURE-STABILITY-CACHE-REUSE-001` | Confirmed current: `TerminalGateRequest` has no baseline or cache field (`TRACED`); an optimization |
| `REVIEW-DIFF-SIDECAR-RETENTION-001` | Confirmed current: `control/retention.py` has no launch handling (search); `MUT-A3-07` |
| `RUN-ATTEMPT-GATE-EXTRACTION-001` | The gate sequence lives in `gate_orchestrator.py`; self-correction is still interleaved and writes candidate files (`MUT-A3-02`). A decomposition item, not a defect |
| `STATIC-RULE-ID-IN-EVIDENCE-001` | Confirmed current: no `rule_id` anywhere in `core/attempt_evidence` (search) |
| `MANAGED-SERVICE-ENV-STATE-BINDING-001`, `OPTIONAL-RUNTIME-GRADED-FAILURE-POLICY-001`, `RUNTIME-LINE-EXPECTED-NONZERO-001`, `APPLICATION-OUTPUT-PROGRAM-STREAM-001` | The symbols each row cites exist at the baseline; the behaviour was not re-traced in A3. Each is a recorded owner decision or a presentation item, not a new authority fact |

Handed to later slices with the reason:

| Row | To | Why A3 could not settle it |
|---|---|---|
| `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` | A4 | The decision is made in plan normalization (`plan_normalization.py::scope_verification_to_requirements`) from `acceptance.py::goal_requires_runtime_behavior`: plan validation scope |
| `RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001`, `WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001`, `WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001` | A5 | One owner, the contained project environment (`tools/validate.py::_ensure_project_venv_impl`); tools and containment scope. Their mechanism needs a container, which a model-free probe cannot supply: A5 must state whether the characterization is possible under the freeze |
| `KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001` | A5 | Root and write-gate authority |
| `GATE-RESULT-PURPOSE-ABSENT-ON-TESTS-GATE-001`, `TECHNICAL-VERIFICATION-BARE-ROWS-001` | A6 | Evidence records and their desktop projection. A3 found the purpose is declared by a decorator on three orchestrating functions (`core/attempt_evidence/scope.py::declares_gate_purpose`); which entry runs the tests gate outside them was not found |

### Open items A3 hands on

- A4: the direct and enforce paths close requirements at different places with different inputs. At the enforce
  terminal the `file_creation` and `runtime_stdout` closers are called with `None` and close nothing ("the units'
  own pre-apply boundary is where these two are decided"), while an enforce unit carries no requirement goal
  (`plan_executor.py::WorkUnitInvocation.requirement_goal` is None for it). Whether those two closers can ever fire
  under enforce is the direct/enforce convergence question (`ENFORCE-EXECUTE-PLAN-CONVERGENCE-001`).
- A5: writers into the plan sandbox and the workspace that are not a unit's commit (tools, MCP), which decide whether
  `BYP-A3-01`'s "by construction" holds; `BYP-A3-07`; `BYP-A3-09` (does self-correction write and execute before a
  review); the policy a real launch runs under (`GAP-A3-01` item 4).
- A6: `GAP-A3-07` and the evidence rows above; the Reviewer latency half of `GAP-A3-06`.
- Phase D (owner policy): `GAP-A3-01` (terminal truth), `GAP-A3-03` (review authority), `GAP-A3-05` (approval
  subject), `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`.

### Documents reconciled in A3

| ID | Document | Claim | Classification (label) |
|---|---|---|---|
| DOC-A3-01 | `handover/claude-context/generation-pipeline.md`: stage 6 (approval gate and outcome contract), stage 8 (final review refusal), "Verification binding", "Commit authority", "Terminal eligibility", "Enforce terminal requirement truth", "Enforce unit acceptance" | As the document states them | CURRENT AND CONSISTENT with the code A3 read (`TRACED`; the sentences beyond the compared paragraphs were not checked). Its closing remark that the milestone path's use of the pass field is unmeasured is now out of date: measured in A3 (P1-P3) |
| DOC-A3-02 | `docs/architecture/CORR016_AUTHORIZED_CONTRACT_EVOLUTION_DESIGN.md`, status line "DESIGN ONLY ... Not implemented" | Nothing of it is implemented | PARTIALLY CURRENT. `workflow/contract_authority.py` states that the DIRECT authorization of this design (Revision 2) is implemented and DERIVED is designed and deferred (`TRACED` from that module's contract; its code was not read). The status line is stale; the design text is a historical proposal for the DERIVED half |
| DOC-A3-03 | Registry `KERNEL-FINAL-REVIEW-REUSES-PRE-BUILD-REVIEW-001` ("INFERRED, not confirmed") versus `OPEN-ITEMS.md` OI-42 ("E4 MEASURED") | Evidence status of the same finding | The registry text is STALE; A3 confirms the behaviour by trace and the golden pin |
| DOC-A3-04 | Registry `GUI-REVIEW-LOCAL-TRUST-001` and `OPEN-ITEMS.md` OI-102 | A same-user actor can write a decision file or run `kriya launch-decision` | STALE / CONTRADICTED BY CODE (see the table above) |
| DOC-A3-05 | Registry `KERNEL-APPROVAL-RECORD-NO-CANDIDATE-DIGEST-001`, `closure_evidence` | The event's only consumers are two metrics modules; the live approval is bound to candidate bytes | First half CURRENT (search). Second half PARTIALLY CURRENT: the commit is bound to the verified bytes; the approval is not bound to them by any comparison (`GAP-A3-04`) |
| DOC-A3-06 | `workflow/stage_apply.py::_abort_without_applying`, in-place restore branch and its comment | Restores the user's real files when there is no worktree isolation | DOCUMENTED BEHAVIOUR NOT REACHABLE at the baseline: the direct path refuses to run in place (`MUT-A3-05`) |

## Slice A4 (2026-11-15) - goal, knowledge, Skills, plan, executor convergence

Evidence: `evidence/a4/` (probes P1 to P10 with output; two committed test files re-run on this baseline, 37 passed).
Every gap below uses `AS-IS -> GAP -> REQUIRED CONTRACT -> IMPLEMENTATION DELTA -> ACCEPTANCE PROOF`. The last three
are `PROPOSED` outlines only: Phase C designs them from the truth owners backward and nothing here is implemented or
authorized.

### The mandatory row

| Registry row | A4 result | Evidence | Gap |
|---|---|---|---|
| `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001` | **Confirmed, current, and larger than "orchestration only".** The row's own statement holds: the enforce loop orders, resumes and completes its subtasks itself; a STRUCTURED `WorkUnitInvocation` is provenance and is not executed by `execute_plan`. The question asked of this slice was whether the two executors recompute or diverge on plan truth. They do: seven result-bearing facts are decided differently (table in `GAP-A4-01`), four of them measured. What they share is real and should be kept: the unit primitive, `TerminalGateService`, the commit service, the tool-membership validator, the skill selector and renderer | `TRACED` (`AUTH-A4-14`); `MEASURED` P2, P3, P10; A3's P1 to P3 and P8 | `GAP-A4-01` |

### GAP-A4-01 - Two executors establish different truths for the same words

| | |
|---|---|
| AS-IS | `AUTH-A4-04`, `AUTH-A4-14`. The same goal in the same product runs the direct shape in a folder with no `kriya.yaml` and the enforce shape after Project Setup (`MEASURED` P10). The divergences: |
| | **(a) Plan validity.** Enforce: `validate_plan` and six further checks. Direct: schema, path authority, tool membership, action against the project view. A cyclic plan with two owners of one file is `complete` on direct and has six reason codes on enforce (`MEASURED` P3). |
| | **(b) Where requirements are verified.** Direct: inside the unit, before its apply, where the retry loop can still act. Enforce: once, at the terminal, with no route back (`AUTH-A4-15`, `GAP-A3-02`). |
| | **(c) Two closers are unreachable under enforce.** The unit-level closure block runs only `if requirement_set is not None` (`development_loop.py::_close_and_check_requirements`), and an enforce unit has none (`MEASURED` P2). The terminal calls `close_requirements_by_file_creation` with `created_paths=None` and `close_requirements_by_runtime_stdout` with `gate_outcomes=None`; its comment says they "close nothing here (fail closed; the units' own pre-apply boundary is where these two are decided)". For an enforce unit that boundary never decides them. A FILE_CREATION or runtime-stdout requirement can therefore be closed by its deterministic record on direct and never on enforce (`TRACED`; the closers were not executed). |
| | **(d) Final review.** Refusal fails an applied unit on direct and milestone, advisory on enforce (`GAP-A3-03`, `MEASURED` A3). |
| | **(e) Approval subject.** Direct: the whole change, once. Enforce: each unit; no plan-level subject (`GAP-A3-05`). |
| | **(f) When the workspace changes.** Direct and milestone: each unit commits to the real workspace as it passes; a later failure leaves earlier commits. Enforce: one terminal commit; a failure discards everything (`GAP-A1-09`). |
| | **(g) Durable unit identity and resume.** Direct and milestone: a unit record, checkpoint selection, validated completion reuse. Enforce: no unit record (`work_unit_record` returns None), its own `ControlState`, and a resume that in production reuses nothing (`AUTH-A1-10`, `GAP-A4-06`). |
| | Also by shape: the skills run shadow exists only on enforce (`BYP-A4-07`); the Current Project View "before" reaches only the direct Reviewer and spec check (registry `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001`). |
| Evidence | `MEASURED` P2, P3, P10 and A3 P1 to P3, P8; `TRACED` for (b), (c), (f), (g) |
| GAP | "The plan was valid", "the requirements were checked", "the change was approved", "success" and "nothing was applied" each mean something different depending on a configuration flag. No document states the differences as a contract, and two sources disagree about which shape the product uses (`DOC-A4-02`). |
| First wrong owner | No single function is wrong. The missing owner is the statement of result truth above both executors: each executor is the only authority for its own run and nothing requires them to agree |
| Violated invariant | Direct and enforce establish compatible truth (Phase B cross-slice check) |
| Affected stages | STAGE-PLAN-VALIDATION, STAGE-SUBTASK-SELECTION, STAGE-FINAL-VERIFICATION, STAGE-REVIEW, STAGE-APPLY, STAGE-CHECKPOINT, STAGE-TERMINAL |
| Bypass paths | `BYP-A4-01`, `BYP-A4-14`, `BYP-A3-01`, `BYP-A3-12` |
| REQUIRED CONTRACT (`PROPOSED`) | One table of result truths that both executors must satisfy: what must hold before "valid plan", "requirements verified", "approved", "applied", "success"; each truth with one deciding owner that both executors call. Merging the executors is one way to get there and not the requirement. Items (c), (d) and (e) need a decision each; (b) and (f) are legitimate design differences that must be stated, with the consequence for repair and for partial application |
| IMPLEMENTATION DELTA (`PROPOSED`) | Smallest first: (c) give the enforce terminal the evidence the two closers need (per-unit created paths and runtime outcomes are already produced by the units) or state that those claims are closed only by other methods under enforce; then (d) and (e) per the owner's rulings. No new executor |
| ACCEPTANCE PROOF (`PROPOSED`) | One scripted goal run through both shapes yields the same requirement truth and the same status word; negative control: a FILE_CREATION requirement closed on direct is closed on enforce; mutation: removing the shared owner from either executor fails a test |
| Registry rows | `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001`, `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001` |
| Severity (candidate) | P1 for the truth divergence (the enforce shape is what Project Setup turns on); the executor merge itself stays a P2 architecture item. Dependencies: `GAP-A3-01`, `GAP-A3-02`, `GAP-A3-03`, `GAP-A3-05`, `GAP-A1-09` |

### GAP-A4-02 - Skill relevance has no authority below the goal, and the Planner and a unit can select different skills

| | |
|---|---|
| AS-IS | `AUTH-A4-08`, `AUTH-A4-09`. One predicate, two input texts: the user's goal for the Planner, the composed unit goal for each unit. A selected skill is rendered whole into every request |
| Evidence | `MEASURED` P4; registry `SKILL-RELEVANCE-CONTEXT-SCOPE-001` (`MEASURED` in run 10, preserved) |
| GAP | (1) The stated invariant "the Planner sees the same applicable skills the Developers will be given" does not hold when the Planner's own text names another skill, tag or library version. A cross-unit decision made by the Planner can then be contradicted by a rule the Planner never saw, or a unit can lose a skill the plan relied on. (2) No rule-level or unit-level applicability exists, so a test unit receives every production rule and the block is mandatory text in every request |
| First wrong owner | `stage_analysis.py::analyze_work_unit` passes `ctx.goal` (the composed unit goal) to the selector; the selector itself is sound |
| Violated invariant | One truth, one owner: skill applicability for a run is decided twice from different evidence; model text is evidence, never authority |
| REQUIRED CONTRACT (`PROPOSED`) | The set of applicable skills is decided once per run from the goal of record and repository facts, and every unit receives a projection of that set. Applicability of a rule to a unit is declared by the skill (owner ruling 2026-11-14: authored, not lexical, filename or embedding retrieval, and not truncation) |
| IMPLEMENTATION DELTA (`PROPOSED`) | Pass the goal of record (already available as `grounding_goal`) to the selector for an enforce unit; the authored relevance is a later, separate feature |
| ACCEPTANCE PROOF (`PROPOSED`) | P4 as a test: the unit's selection equals the Planner's; mutation: selecting on the composed goal fails it |
| Registry rows | `SKILL-RELEVANCE-CONTEXT-SCOPE-001` |
| Severity (candidate) | P2 |

### GAP-A4-03 - The knowledge gate takes a folder name as proof of skill coverage

| | |
|---|---|
| AS-IS | `AUTH-A4-05`: `os.listdir` of the skills directory, substring match of a library alias in the folder name |
| Evidence | `MEASURED` P5: an empty folder `my-notes-on-ignite-core-outage` removes the gap for `org.apache.ignite:ignite-core`; `OPEN-ITEMS.md` OI-81 records the same measurement. `MEASURED` P11: the template Kriya itself scaffolds (`generate_skill_template`: no rule, unverified) removes it too |
| GAP | A human gate is opened by something that is neither a loaded skill nor verified. Kriya creates such a folder itself: after a knowledge gap the CLI offers to scaffold one, and auto-accrual scaffolds one after a run that used a suggested dependency (`MUT-A4-11`), so the gate closes itself for later runs without any knowledge having been added (`BYP-A4-19`). When `paths.skills` is inside the workspace the folder is project-controlled, so a repository can suppress the gate that asks a person to confirm a knowledge risk. The unit-level analysis does detect "no skill exists" on loaded skills (`missing_skill_candidates`), but that only prompts, it does not stop |
| First wrong owner | `KnowledgeGuard.check_goal` (it owns the coverage decision and does not consult the skill store) |
| Violated invariant | Evidence binds to identity, nothing is transferred by name; UNKNOWN fails safely |
| REQUIRED CONTRACT (`PROPOSED`) | Coverage = a loaded skill whose tags name the library and whose verification is Kriya's (`AUTH-A4-07`); anything else is "not covered" |
| IMPLEMENTATION DELTA (`PROPOSED`) | The guard receives the engine's skills instead of a directory path. Scenario 04's reliance on the current behaviour with `offline_mode` (owner-accepted for that scenario) must be re-decided |
| ACCEPTANCE PROOF (`PROPOSED`) | P5 as a test: an empty or unverified folder leaves the gap; a verified covering skill clears the structural gap |
| Registry rows | `KNOWLEDGEGUARD-SKILL-IDENTITY-001` |
| Severity (candidate) | P2 (registry P3): it is an authority input controlled by workspace bytes |

### GAP-A4-04 - Text fetched from the web reaches model requests outside the untrusted fence

| | |
|---|---|
| AS-IS | `AUTH-A4-06`. Two routes. (1) Retry: `live_lookup.py::_augment_error_with_live_lookup` appends up to 2,000 characters of a fetched page per term to `state.error_context` under a plain "Reference material found" banner. (2) Skills: rules that SkillGapAgent extracted from a fetched page (or a human-supplied URL) are written into the skill and rendered in the conventions block, labelled "Unverified Rules" |
| Evidence | `MEASURED` P7 for route 1 (no fence marker; the page text is outside any fence). Route 2 `TRACED` (`stage_analysis.py`, `render_skill_conventions`) |
| GAP | The cross-cutting rule "learned or retrieved text stays inside the untrusted-context fencing" is not applied to live-lookup text. Route 1 places attacker-controllable page text at the head of the next Developer request. Route 2 passes it through a model first and persists it as skill rules |
| First wrong owner | `_augment_error_with_live_lookup` (route 1). Route 2: `stage_analysis.py` renders newly extracted rules into `convention_prompt` |
| Violated invariant | Model and retrieved text is evidence, never authority; untrusted text is fenced |
| Bounded by | Web lookup is off by default and in the Project Setup profile; every query needs an authorization; route 2 also needs the person's review of the found references |
| REQUIRED CONTRACT (`PROPOSED`) | Any text whose origin is a fetched page enters a request only through `untrusted_context.py::fence_untrusted_reference`; extracted rules stay unverified and fenced until promoted by a person or proven by a committed run |
| IMPLEMENTATION DELTA (`PROPOSED`) | Fence the appended material in route 1; for route 2 decide whether same-run folding of new rules is kept (fenced) or dropped |
| ACCEPTANCE PROOF (`PROPOSED`) | P7 as a test: the page text is inside the fence; the inventory test for bytes sources lists live lookup |
| Registry rows | None (new). Related: `DESKTOP-LOOKUP-QUERY-APPROVAL-001` (A3) |
| Severity (candidate) | P2 security, opt-in path. P1 if web lookup is part of a production profile (owed by A5) |

### GAP-A4-05 - "Does the request require execution?" is a pattern match whose miss deletes verification

| | |
|---|---|
| AS-IS | `AUTH-A4-13`, `FR-A4-17` |
| Evidence | `TRACED`; the fixed owner goal `MEASURED` by 7 committed tests |
| GAP | The specific defect of `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` is fixed at the baseline: the owner goal is read as requiring runtime behaviour and the application is executed. The class remains: the reading has no UNKNOWN, a miss removes a Planner verifier that was right, and the result does not say "the application was not run" in a way that affects status. Run-verification without the requirement is advisory by design |
| First wrong owner | None wrong for the measured goals; the authority for "what the user required" is a regular expression set because no semantic contract exists (`GAP-A2-01`) |
| REQUIRED CONTRACT (`PROPOSED`) | Removal of a runtime verifier is recorded as a typed, result-visible fact ("runtime behaviour not required by the request: not executed"), and the semantic contract of `GAP-A2-01`, once it exists, is the input instead of the pattern set |
| ACCEPTANCE PROOF (`PROPOSED`) | A corpus of goal wordings with expected readings (the registry row asks for it); a result field for the not-executed case |
| Registry rows | `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` (stale for its stated defect, `DOC-A4-03`) |
| Severity (candidate) | P2 |

### GAP-A4-06 - An enforce resume compares the hash of a fresh Planner answer

| | |
|---|---|
| AS-IS | `SM-A4-01`: every enforce invocation calls the Planner again; subtask states are reused only if the new raw plan's `content_hash()` equals the stored `current_plan_hash`. The hash covers every field, including `plan_id`, which is the run id (`build_engineering_plan_from_planner_output(plan_id=run_id)`) |
| Evidence | `MEASURED` P9 (same subtasks, another `plan_id`: different hash); `TRACED` for the rest |
| GAP | Reuse requires a model to reproduce its earlier plan byte for byte, and the resuming run to carry the same run id. Together with `AUTH-A1-10` (a production enforce run reuses nothing unless the earlier run already committed the whole plan) the subtask-resume path of enforce is not reachable in practice. It fails safe: the plan runs fresh |
| UNKNOWN | Whether a resumed enforce run keeps the earlier run id was not established (`run_coordinator` not read) |
| REQUIRED CONTRACT (`PROPOSED`) | Either state "enforce does not resume within a plan" as the contract and remove the unreachable branch, or persist the validated plan and resume against it (depends on `GAP-A1-02`, `GAP-A1-09`) |
| Registry rows | None new; belongs with `GAP-A1-02` |
| Severity (candidate) | P3 (dead capability, safe) |

### GAP-A4-07 - Goal ambiguity is not represented

| | |
|---|---|
| AS-IS | `AUTH-A4-03` |
| Evidence | `TRACED`; `MEASURED` P1 ("Make it better." is `REQ-1`); negative control: no producer of clarifications |
| GAP | Nothing can say "this request has two readings" before a model picks one. The first place a reading is examined by a person is the verifier semantic review, after generation, and only when a typed channel exists |
| First wrong owner | None exists; this is the same absence as `GAP-A2-01`, seen from intake |
| REQUIRED CONTRACT (`PROPOSED`) | Part of the canonical semantic contract: an explicit "unresolved" state that blocks or records per policy, and the existing `clarifications` input as the resolution channel |
| Registry rows | None new |
| Severity (candidate) | P2, owner policy (whether Kriya asks, refuses or records) |

### GAP-A4-08 - Planner-authored integration decisions bind other units with no review

| | |
|---|---|
| AS-IS | `AUTH-A4-12`, `BYP-A4-16`: a recorded decision is "AUTHORITATIVE for this stage" and overrides an applicable skill's alternatives |
| Evidence | `TRACED` |
| GAP | This is the one place Planner text is given authority over Developer output by design (SHARED-DECISION-AUTHORITY-001, owner ruling 2026-11-10). It is validated for form (a selection from its own menu, no conflict) and not for substance; no person sees it; attribution of a failure caused by a wrong decision is a known limit of the pipeline document |
| REQUIRED CONTRACT (`PROPOSED`) | Record it as an intentional contract with its limits, or make the decision part of the semantic contract a person can review. Owner policy |
| Registry rows | None |
| Severity (candidate) | P3; intentional by owner ruling, listed so the authority matrix is complete |

### Updates to gaps of earlier slices

| Gap | What A4 adds |
|---|---|
| `GAP-A3-05` (P1 authority-bypass candidate by owner ruling of 2026-11-15) | **No aggregate authority in A4's scope.** Plan acceptance involves no person and no size or risk measure (`AUTH-A4-10`; search of `enforce_loop.py` between validation and the first unit). `risk_threshold_lines` has exactly one reader, `stage_apply.py`, evaluated per unit invocation. The decomposition that sets the subject of that trigger is authored by the Planner (`BYP-A4-10`). Reachability: only with `autonomy.mode: guardrails`; the packaged default and the Project Setup profile are human-in-the-loop (`MEASURED` P10), where every unit that wrote source is approved, still per unit and with no plan-level subject. Slice A5 still owes the tool, MCP and configuration side before the classification is final |
| `GAP-A3-04` | The owner's split is recorded for Phase B: (A) runtime authorization binding, (B) audit binding; a sound commit guard does not weaken either |
| `GAP-A3-01` | The Project Setup profile sets no requirement policy, so a desktop project created by Setup runs enforce with `record` / `record` (`MEASURED` P10). The production profile is owed by A5 |
| `GAP-A3-02` | `GAP-A4-01` (b) and (c): the enforce terminal is the only place requirements are verified and two closers cannot fire there |
| `GAP-A2-01` | `GAP-A4-05` and `GAP-A4-07` are the same absence seen from plan normalization and from intake |

### Registry rows of slice A4 that did not need a gap of their own

| Row | Result (label) |
|---|---|
| `PLAN-PRESERVED-REFERENCE-SOURCE-ATTRIBUTION-001` | Confirmed as a design limit, not a defect: per-source attribution in `planning_request.py::find_missing_grounded_production_artifacts`; Kriya refused and applied nothing. Function located, body not read (`TRACED` at the symbol) |
| `PLAN-FORM-NORMALISATION-001` | Confirmed current: four normalizers exist (`plan_normalization.py`), none derives a unique requires-to-provides edge or the plan kind; `REFACTOR_BASELINE_MISSING` is still raised for a refactor route (`plan_validation.py`). `TRACED` |
| `PLAN-EXTERNAL-PACKAGE-CAPABILITY-001` | Primary cause is model behaviour; the secondary repair-feedback fix is pinned by `tests/test_plan_external_package_capability_001.py` (`MEASURED`: 30 tests passed at the baseline). Still not exercised live, as the row says |
| `ENFORCE-FALSE-PREMISE-MUTATION-GUARD-001` | Confirmed current: `REJECTED_FALSE_PREMISE` is read by `requirement_disposition.py`, `authority_request.py` and the CLI option only; no planning or validation code reads a disposition (search). `TRACED`. Belongs with `GAP-A4-01` and the verified-no-change rows of A2 |
| `SKILL-WORKSPACE-PERSISTENT-VERIFICATION-ATTESTATION-001` | Confirmed: a workspace skill's verification is run-local (`MEASURED` P8, `SM-A4-04`). Future feature by owner decision |
| `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` | Confirmed current (`MEASURED` P8): `BYP-A4-06`. The effect is prompt labelling; it grants no write, tool or approval authority. It is still model-visible instruction text from the repository outside the fence |
| `SKILL-QPID-RULE-DOCTOR-ADVICE-STALE-001` | Confirmed current: `skills/qpid/rules.txt` line 20 still says "or run `kriya doctor` if none is given". A skill-content item |
| `GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001` | Confirmed current: `workflow/toolchain.py` states the goal-stated JDK handed to the toolchain owner is "Always None today"; `jdk_home_for_version` has no caller (search). An ownership gap by owner ruling: no heuristic. Slice A5 owns toolchain authority |
| `JAVA-DEVELOPER-WRONG-PACKAGE-PATH-001` | Confirmed: no check of a Java package declaration against its directory exists (search for a reason code: none). Model behaviour; Kriya failed closed. A candidate improvement, not a defect |
| `LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001` | Not characterized further: `graph_retrieval.py::_code_intelligence_candidates` shows no fold by lookup key in its first 30 lines; the registry itself says no Developer-path loss was measured. Retrieval is slice A6 material |
| `GUI-SKILLS-BUILD-001`, `GUI-SKILLS-MUTATION-001` | Confirmed current: KUP exposes `skills.list` and `skills.describe` only; `skills/service.py` is read-only; creation, approval, promotion and unverify are CLI commands (`cli.py`). Future features |
| `KERNEL-ATTEMPT-CONTEXT-PLACEMENT-001` | Confirmed: `AttemptContext` is in `workflow/attempt.py`. A decomposition item (`OPEN-ITEMS.md` OI-46) |
| `CLI-RESUME-HINT-DESIGN-STAGE-STOP-TEXT-001` | Confirmed current: `cli.py` still prints "Checkpoint saved" and "[GENERATION BUDGET EXHAUSTED]" in the failure branches (strings present at lines 3699, 3769, 4852, 4880); the conditions were not re-read. Message accuracy |
| `LSP-GROUNDING-CONSUMER-CHAIN-DEAD-001` | Confirmed: `tools/lsp.py::JdtlsClient.start` is the typed refusal. Dormant by owner decision |

Rows handed over by A3 and settled here:

| Row | Result |
|---|---|
| `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` | **Stale for its stated defect** (`MEASURED`: the row's own reproducer file now asserts the opposite and passes, 7 tests). The class is `GAP-A4-05` |
| `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001` (an A2 row) | Belongs to `GAP-A4-01`. Its "blocking" text ("the desktop path is the direct path; the enforce path is not used by the demo") is contradicted by the Project Setup profile (`DOC-A4-02`) |

### Open items A4 hands on

- A5: `BYP-A4-07` (skill writes and Git commits into a skills root inside the workspace on the direct and milestone
  shapes; whether the workspace write gate covers the skills directory); whether a desktop launch can confirm a
  knowledge gap; the requirement policy and web-lookup setting of the production profile; the rest of the
  `GAP-A3-05` trace (tools, MCP, configuration); `GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001`.
- A6: STAGE-GENERATION, the Developer request itself, including where `error_context` and the conventions block sit
  (`BYP-A4-04`); `LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001`; an audit event per outbound lookup query.
- Phase B: whether a resumed enforce run keeps its run id (`GAP-A4-06`).
- Phase D (owner policy), added by A4: `GAP-A4-01` items (c), (d), (e); `GAP-A4-07`; `GAP-A4-08`; the Scenario-04
  reliance in `GAP-A4-03`.

### Documents reconciled in A4

| ID | Document | Claim | Classification (label) |
|---|---|---|---|
| DOC-A4-01 | `handover/claude-context/generation-pipeline.md`, "Shared decisions across subtasks" and the comment in `skill_conventions.py` | The Planner sees the same applicable skills the Developers are given | PARTIALLY CURRENT: the same two functions are used (`TRACED`); the same set is not guaranteed because the inputs differ (`MEASURED` P4) |
| DOC-A4-02 | Registry `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001` ("the desktop path is the direct path") versus `application/project_init.py` ("this profile turns the enforced workflow on") | Which shape the desktop product runs | The registry text is STALE / CONTRADICTED BY CODE for a folder initialized by Project Setup; it holds for a folder with no configuration (`MEASURED` P10) |
| DOC-A4-03 | Registry `GREENFIELD-RUNTIME-VERIFICATION-DROPPED-001` and `OPEN-ITEMS.md` OI-93 ("reproduced ... the run ends success with nothing executed") | The owner goal is read as not requiring runtime behaviour | IMPLEMENTED THEN SUPERSEDED: the reproducer test asserts the corrected reading and passes (`MEASURED`) |
| DOC-A4-04 | Registry `ENFORCE-EXECUTE-PLAN-CONVERGENCE-001` ("not a defect ... class B, orchestration only, no parallel decider") versus `OPEN-ITEMS.md` OI-113 ("authority / security defect (Class A) ... owner decision: one authoritative execution path; measured duplicate order/lifecycle/dependency/terminal logic") | The class of the convergence item | The two documents CONTRADICT each other. Code: the terminal commit decision is shared (the registry is right about that one decider), and seven result-bearing facts differ (`GAP-A4-01`), which supports OI-113's class. Neither is rewritten here |
| DOC-A4-05 | `handover/claude-context/generation-pipeline.md` line 20 ("ONE executor, `plan_executor.execute_plan()`" for direct and milestone; the enforce subtask loop passes its `WorkUnitInvocation`) | Executor ownership | CURRENT AND CONSISTENT (`TRACED`); it does not claim that enforce runs through `execute_plan` |
| DOC-A4-06 | `kriya/workflow/terminal_gate_service.py` comment at the `file_creation` / `runtime_stdout` closers ("the units' own pre-apply boundary is where these two are decided") | Where those two claims are closed under enforce | CONTRADICTED BY CODE for enforce units: they have no requirement set, so their pre-apply boundary runs no closer (`GAP-A4-01` (c)) |
| DOC-A4-07 | `kriya/config/config.py::WorkflowControllerConfig` docstring ("TOOL-tagged subtasks are refused outright") | Enforce and TOOL subtasks | STALE: TOOL subtasks execute through the governed path since TOOL-001 (`enforce_loop.py` comment at the validation call). Slice A5 owns the tool path |
| DOC-A4-08 | `docs/user_guide.md` (requirements: "The Planner and the Architect are shown the list and asked to cite the ids they serve") | Requirement lineage | CURRENT AND CONSISTENT with `stage_planning.py` and the enforce request (`TRACED`); the Architect half was not read |
