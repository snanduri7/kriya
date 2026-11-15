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

## Slice A5 (2026-11-15) - workspace, root, tools, MCP, mutation, private state, configuration

Evidence: `evidence/a5/` (probes P1 to P8 with output; the citation check with its negative controls). Every gap below
uses `AS-IS -> GAP -> REQUIRED CONTRACT -> IMPLEMENTATION DELTA -> ACCEPTANCE PROOF`. The last three are `PROPOSED`
outlines only: Phase C designs them from the truth owners backward and nothing here is implemented or authorized.
Owner rulings received during this slice (acceptance of A4, 2026-11-15) are applied in "Updates to gaps of earlier
slices".

### The mandatory row

| Registry row | A5 result | Evidence | Gap |
|---|---|---|---|
| `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001` | **Characterized; stays OPEN. The required invariant does not hold.** The row asked which supported path writes or reads the shared location and whether cross-project retrieval is reachable. (1) The location: with no `paths.memory` in the project's configuration every project resolves the installation's `memory` directory, and so does a folder initialized by Project Setup, whose file has no `paths` section. The same holds for `paths.skills`. (2) The key: the code index, the learned-knowledge store and the release cache carry no project, root or identity; rows are keyed by repository-relative path. (3) Writers of the code index: `kriya analyze` and the opt-in auto-index, nothing else; the desktop has no indexing operation. So a desktop-only installation at the packaged defaults holds an empty index and no cross-project read occurs, which matches the registry's measurement (no `retrieval.*` events in ten desktop runs). (4) Once any unconfigured folder was indexed from the CLI, a run in another project reads that index as its own: the "already indexed" test is true, symbol lookups return the other project's paths, retrieval matches the other project's files and stores its chunk text in this run's trace row. The structural readers that use `current_view()` are immune, and the Planner's assembled block omits files the project does not hold. (5) Indexing the second project deletes the first project's index. (6) The same default makes skills global: a scaffold written by one project's run removes another project's knowledge gap. (7) Control state, approvals and launch records are per project by identity; run history is one database for all projects, attributed through evidence manifests | `MEASURED` P1, P2, P7, P8; `TRACED` readers, writers and controls in `AUTH-A5-09`, `SM-A5-04`, `MUT-A5-08..13` | `GAP-A5-01` |

### GAP-A5-01 - Retrieval, knowledge and skill state has no project owner: the packaged defaults are one installation-wide namespace

| | |
|---|---|
| AS-IS | `AUTH-A5-09`: location by configuration, packaged default resolved against the installation directory; no store records which project its content came from |
| Evidence | `MEASURED` P1 (same `paths.memory` and `paths.skills` for two bare folders and a Project Setup folder; neither is inside the project), P2 (what project B reads of project A; B's indexing deletes A's), P7 (same learned-knowledge path), P8 (A's scaffold closes B's knowledge gate; the engine's refusal exists but the scaffold writer does not consult it). Controls: no workspace key in the three store modules; two index writers; no desktop indexing operation; the shared-install predicate absent from the scaffold, staged-fact and extraction writers |
| GAP | Four distinct failures of one missing owner. (a) **Read:** another project's index answers for this project wherever a reader does not rebuild the view from the project's own bytes: the legacy graph readers (`investigation.py` `find_symbol` / `find_callers`, `triage.py` blast radius, the class-location index of the duplicate-type gate), the vector leg of retrieval and the "already indexed" test. (b) **Disclosure:** the other project's chunk text is persisted in this run's trace row and its file paths reach this run's analysis as matched files. (c) **Write:** a run or an `analyze` in one project deletes or replaces another project's index; a run writes skill scaffolds, rules, provenance and staged facts into the directory every project loads, and that directory is the one whose skills Kriya trusts by location. (d) **Installation tree:** the default locations are inside the installation, so normal operation writes there (an editable checkout's repository; a read-only installation would fail instead). The desktop is the product surface where this is the default: Project Setup neither sets the paths nor says they are shared |
| First wrong owner | `config/resolution.py` (the default's anchor) for the location; no owner at all for attribution |
| Violated invariant | The owner's isolation invariant (state or retrieval of project A is never returned, written or reused for project B through any explicit, fallback, default, global, legacy or missing-key path); evidence binds to exact identity |
| REQUIRED CONTRACT (`PROPOSED`) | One owner of project-scoped derived state. Every store that holds project content (code index, vector index, staged run facts, conflict registry of project skills) lives under a location derived from the workspace identity, in Kriya's state directory, never in the installation and never selected by a default shared between projects. A store records the identity it was built for, and a reader refuses a store of another identity (typed, "no index for this project"), never reads it. Genuinely global knowledge (the shipped skill library, the public release-date cache) stays global, is read-only to a run, and is named as global. A project's explicit `paths.memory` / `paths.skills` remains possible and is bound the same way |
| IMPLEMENTATION DELTA (`PROPOSED`) | A state-path function beside `resolve_state_directory` returning `<state>/projects/<workspace identity>/memory`; the default of `paths.memory` becomes "project state" instead of `./memory`; the index manifest gains the workspace identity and readers check it; the skills default splits into the read-only library and a per-project writable root; every skill writer goes through the engine's location rule. No migration of an existing shared index (it has no attribution to migrate by): it is ignored and reported |
| ACCEPTANCE PROOF (`PROPOSED`) | P1, P2 and P8 as tests with inverted expectations: two unconfigured projects resolve different memory locations; after indexing A, every reader called for B answers "no index"; indexing B leaves A's index intact; A's scaffold does not change B's knowledge gate. Negative controls: a store whose recorded identity is altered is refused; removing the identity check makes the P2 test fail |
| Registry rows | `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001` (owner-classified P1); related: `HISTORY-STATE-DIR-PER-PROJECT-001`, `GUI-RUN-ATTRIBUTION-001` (the same missing attribution, for run history) |
| Severity (candidate) | **P1**, as the owner classified the row. Reach today: any machine on which `kriya analyze` (or auto-index) ran in a folder without its own `paths.memory`; for skills, any run that scaffolds or learns. Disposition candidate: FIX_REQUIRED_BEFORE_PRODUCTION |
| Limits of this characterization | No real desktop run was made. The effect of a foreign index on plan quality or on a Developer request was not measured (A6 owns the request). Which reader created the empty database the registry found in the installation tree is `INFERRED` |

### GAP-A5-02 - The write gate does not know which workspace paths are authority inputs of later runs

| | |
|---|---|
| AS-IS | `AUTH-A5-04`: the gate refuses paths outside the root, control paths, credential paths, the goal file and paths outside a unit's scope. Everything else inside the candidate is allowed |
| Evidence | `MEASURED` P5: `kriya.yaml`, `kriya.yml`, `skills/local/rules.txt`, `skills/new-skill/skill.yaml`, `.github/workflows/ci.yml`, `.gitignore`, `.gitattributes` are allowed. Absent `kriya.yaml` in `policy/filesystem.py`. P6: the fields a repository file may set without approval include the run budget at any value. End to end (candidate to owner workspace) `INFERRED`: no path-specific exclusion found in the apply path |
| GAP | "Model text is evidence, never authority" names a configuration value among the things model text must not become. A candidate may contain the project's `kriya.yaml`; once applied, its `REPOSITORY_SAFE` fields are the next run's configuration without any authority decision (`BYP-A5-01`), and workspace skill files are the next run's conventions and its knowledge-gate coverage (`BYP-A5-02`). The configuration authority cannot see this: it classifies sources, and the file's source is "the repository". The only person in the path is the unit's diff approval, which is unconditional in human-in-the-loop mode and absent in guardrails mode unless the diff is large |
| First wrong owner | `AuthorizedFileWriter.authorize` (no class for Kriya's own project-level inputs); behind it the classification premise in `config/authority.py` that repository bytes are written by the repository's author |
| Violated invariant | Model text is evidence, never authority; one authoritative owner per decision |
| REQUIRED CONTRACT (`PROPOSED`) | Kriya's project-level inputs (the canonical configuration file names; the workspace skills root; any future project-local Kriya input) are a named path class. A model-directed write to that class is refused by the write gate unless the user's goal itself asks for it, in which case it requires an explicit human approval of that file in every autonomy mode. Whether CI workflow files and Git attribute files join the class is an owner policy choice (they change what runs elsewhere, not what Kriya decides) |
| IMPLEMENTATION DELTA (`PROPOSED`) | A closed table of project-authority relpaths beside `TRUSTED_CONTROL_NAMES`; a new reason code; the approval trigger for the goal-requested case in `stage_apply.py`. No change to SEC-009 |
| ACCEPTANCE PROOF (`PROPOSED`) | P5 as a test: the two configuration names and a path under the workspace skills root are denied with the new reason code in every scope mode; a scripted unit that emits `kriya.yaml` ends refused and the owner workspace's configuration is byte-identical. Mutation: removing the table entry makes the test fail |
| Registry rows | None existing (new) |
| Severity (candidate) | P1 candidate in guardrails mode (no person in the path), P2 in human-in-the-loop (a person sees the diff but is not told the file is Kriya's own input). Disposition candidate: FIX_REQUIRED_BEFORE_PRODUCTION |
| Limits | Not exercised end to end. No live run is known in which a model wrote `kriya.yaml`; the goal-file incident recorded in the writer's own comment (a generated subtask overwrote the goal file) is the precedent for this class |

### GAP-A5-03 - A planned MCP call with a write grant changes the owner workspace outside the candidate

| | |
|---|---|
| AS-IS | `AUTH-A5-07`, `BYP-A5-03`, `MUT-A5-06` |
| Evidence | `TRACED`: `MCPManager.start_all` and the approval resolver bind `os.path.realpath(os.getcwd())`; `map_capability_profile_to_containment` mounts that path read-write when `workspace_write` is granted; `MCPTool` declares `GOVERNED_SIDE_EFFECT`, returns its arguments unchanged from `authorize_model_invocation` and never reads the invocation's roots; the enforce loop is the only supplier of roots. No MCP server was run |
| GAP | Every other model-originated change reaches the owner workspace only through a candidate, its gates, its approval and the commit transaction. This one does not: the effect is immediate, in the owner workspace, and survives a failed or refused run. The governance is real (two operator approvals, containment), but it authorizes the tool, not the change: nothing shows the person what the call wrote, and the run's result, evidence and rollback do not include it. The subsystem note states "every planned file write is refused"; that holds for the filesystem tool and not for an MCP tool with this grant (`DOC-A5-02`) |
| First wrong owner | `MCPManager.start_all` (the workspace an MCP server sees is the process directory, not a root the run owns) |
| Violated invariant | Every mutation has one authority path; a run never edits the owner workspace before its commit transaction |
| REQUIRED CONTRACT (`PROPOSED`) | Two valid alternatives, an owner policy choice: (A) a planned MCP call never receives a writable workspace mount: during a run the workspace grant is mounted read-only whatever the profile says, and write-capable MCP tools stay operator-only; or (B) a planned MCP call's writable workspace is the run's candidate, so its effect joins the candidate's diff, gates and approval. (B) needs a per-run server lifetime; (A) is a few lines |
| IMPLEMENTATION DELTA (`PROPOSED`) | (A): the planned-governance resolution treats an MCP tool whose server holds a workspace or additional write grant as operator-only (a fact of the bound capability profile, not of the tool's description). Recommended as the minimal correction |
| ACCEPTANCE PROOF (`PROPOSED`) | With the stand-in MCP fixture: a planned call to a tool of a write-granted server is refused pre-effect with a typed reason and the workspace is byte-identical; the same tool through `kriya tools execute` still runs. Mutation: restoring `GOVERNED_SIDE_EFFECT` for it makes the test fail |
| Registry rows | None existing (new). Related: `TOOL-RUNPHASE-PRE-EFFECT-DENIAL-TERMINAL-001` |
| Severity (candidate) | P2: reachable only after two explicit operator approvals, and only for a server the operator gave a write grant. Listed under owner policy for Phase D |

### GAP-A5-04 - Outside the production profile a repository may raise or remove the run's time bound (title and part (b) corrected in Phase B)

| | |
|---|---|
| AS-IS | `AUTH-A5-12` |
| Evidence | `MEASURED` P6: a repository file sets 999999 or `null` with no violation; production refuses 7200 and `null`, accepts 2400. **Corrected in Phase B:** the number bounds the whole top-level run, one clock on the run lease shared by every unit invocation (`AUTH-A1-08`; `MEASURED` B-P2). The A5 statement that it bounds each unit invocation misread slice A1b, which is about attempt budgets |
| GAP | (a) The bound on how long Kriya works on one request is a tuning field any repository may raise or delete; the Project Setup profile's 900 is therefore advisory against the project's own file (and `BYP-A5-01` lets a run rewrite it). (b) **Withdrawn in Phase B:** the run clock has an owner and is run-wide. What stays true: the packaged default configures no bound, a resumed run starts a new clock, and nothing bounds the amount of work (attempt budgets are per unit invocation and multiply with re-invocations, `GAP-A1-04`). The answer to "which layer owns effective run budgets": the configuration owns the value, the run lease owns the clock, nothing owns the value's authority outside production. 2400 and 3600 do not conflict (a scenario's value under the production ceiling) |
| First wrong owner | `config/authority.py` (class of the field); the enforce loop for (b), already recorded by A1b |
| Violated invariant | A configuration source may only set what it cannot use to gain resource authority (the containment bounds follow this rule: a repository may lower them, never raise them) |
| REQUIRED CONTRACT (`PROPOSED`) | The budget becomes value-bounded like the containment bounds: a repository may lower it below the operator's or the profile's value, never raise or remove it. A run-level deadline is defined once (Phase C, with the retry-allowance contract of A1) |
| IMPLEMENTATION DELTA (`PROPOSED`) | An entry in `_VALUE_BOUNDED_FIELDS` semantics with the ceiling taken from the trusted layer; no change to the production seal |
| ACCEPTANCE PROOF (`PROPOSED`) | P6 as a test: a repository raise and a repository `null` are violations; a repository value below the trusted one is accepted |
| Registry rows | `DEMO-TIME-BUDGET-001` (slice A1) is the adjacent row; none for the authority class |
| Severity (candidate) | P2 |

### GAP-A5-05 - Skill learning is a second write path into a repository, outside the write gate and the candidate

| | |
|---|---|
| AS-IS | `MUT-A5-08`, `BYP-A5-07` (`BYP-A4-07` handed over) |
| Evidence | `TRACED`: eleven writers, none constructs the write gate (control); most call `git_commit_if_tracked`. The run shadow has one activator, the enforce loop (A4 control, re-run) |
| GAP | On the direct and milestone shapes, model-extracted rules and Kriya's own scaffolds are written and Git-committed into whatever repository holds the skills directory, during analysis, before a candidate exists and whatever the run's outcome. Inside the workspace repository that moves HEAD outside the commit transaction; in the installation directory it is `GAP-A5-01` (c). Enforce defers the writes through the shadow, so the Project Setup shape is covered for a skills root inside the workspace repository only (the shadow covers directories inside the bound candidate repository; the installation directory is not one) |
| First wrong owner | The Skills owner: "the one way the Skills owner writes a skill file" (`_write_skill_file`) is not the only way a skill file is written |
| Violated invariant | Every mutation has one authority path |
| REQUIRED CONTRACT (`PROPOSED`) | One skill-write entry on the engine, used by every writer, which applies the location rule and the run-shadow rule on every shape |
| IMPLEMENTATION DELTA (`PROPOSED`) | Route the knowledge guard's scaffold, the staged-fact writer and the extraction writers through the engine; activate the shadow for every shape, or make run-time skill writes project-state writes under `GAP-A5-01` |
| ACCEPTANCE PROOF (`PROPOSED`) | The control `open(` under the skill modules narrows to the engine; a direct-shape scripted run that fails leaves the skills directory and the repository HEAD unchanged |
| Registry rows | None existing; adjacent: `GUI-SKILLS-MUTATION-001`, `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` (slice A4) |
| Severity (candidate) | P2 |

### Updates to gaps of earlier slices

| Gap | Update from A5 |
|---|---|
| `GAP-A3-05` (P1 candidate, owner ruling) | The owed trace is complete. **No aggregate plan-level or run-level approval or size authority exists anywhere**: not in planning (A4), and not in tools, MCP, configuration or the application layer (A5, item 10 of the A5 mapping in `01-AUTHORITY-MATRIX.md`, with the controls). Every approval binds one subject: one unit's candidate digest, one tool identity, one configuration field set, one runtime command list. Per the owner's rule this is proposed as FIX_REQUIRED_BEFORE_PRODUCTION for Phase B; reachability is unchanged (guardrails mode only; `autonomy.mode` is `SECURITY_AUTHORITY`, so leaving human-in-the-loop needs an operator approval, `MEASURED` P6) |
| `GAP-A4-03` (P1 knowledge-authority candidate, owner ruling) | Within A5's scope **no later authority validates the Skill facts that satisfy the gate**: the gate reads a directory listing; the directory is by default global and writable by any project's run (`GAP-A5-01`); the scaffold writer has no validation and no location rule; and a candidate may write a covering folder into the workspace (`BYP-A5-02`). A desktop launch cannot confirm a knowledge gap (`BYP-A5-11`), so on the desktop the gate can only stop the run or be closed by a folder. What remains for A6: whether the Developer request treats an unverified or empty covering skill differently (STAGE-GENERATION). Unless A6 finds such a check, the classification is FIX_REQUIRED_BEFORE_PRODUCTION |
| `GAP-A3-04` | Unchanged. A5 adds that the launch approval record does carry a candidate digest per review (`launch_approval.py::build_pending`), which Phase B must place in its split (A: runtime authorization binding; B: audit binding) |
| `GAP-A1-06`, `BYP-A1-12` | The identity half is answered: a plan sandbox is its own authority root (`AUTH-A5-01`). Survival of unit checkpoints across the sandbox reset stays open for Phase B |
| `BYP-A1-13`, `BYP-A2-08`, `BYP-A3-09` | Bounded: the self-correction loop is off unless an operator enabled it, and its writes pass the write gate (`MUT-A5-05`, `BYP-A5-13`) |
| `BYP-A3-01` | Writers into the plan sandbox other than a unit's batch: the pom correction and the self-correction loop, both gated; no tool, no MCP server. So the terminal batch is the chain of unit commits plus those two deterministic or gated writers. The MCP route writes the owner workspace instead (`GAP-A5-03`) |
| `BYP-A3-07` | Closed at the producer (`BYP-A5-12`) |
| `BYP-A4-18` | Closed for the CLI entry points (`BYP-A5-10`) |
| `GAP-A3-01` | The production profile blocks on unverified and unknown requirements; the Project Setup profile does not set either policy (`AUTH-A5-11`). The desktop product therefore runs with `record` / `record` unless the operator's configuration says otherwise |

### Registry rows of slice A5 that did not need a gap of their own

| Row | Result (label) |
|---|---|
| `HISTORY-STATE-DIR-PER-PROJECT-001` | Confirmed current: history is a property of the state directory (`resolve_state_directory`; one directory for all projects, `MEASURED` P7); the preflight reports when a run would record where the cockpit does not read (`application/run.py`, `observable_by_cockpit`). Same missing attribution owner as `GAP-A5-01`; design decision owed (`TRACED`) |
| `HISTORY-SNAPSHOT-STALE-ON-REOPEN-001` | Not re-traced (UI session code); the registry's own authority is OI-91. Kept as stated: desktop behaviour, no kernel fact contradicts it |
| `TOOL-RUNPHASE-PRE-EFFECT-DENIAL-TERMINAL-001` | Intentional current contract, confirmed: `_authority_phase` is the one place a refusal is proven pre-effect; anything after `_run_model_authored` is entered may have effected (`TRACED`, `SM-A5-02`). Per-tool tagging is a future feature |
| `GUI-TOOLS-HEALTH-001` | Confirmed: the application's tool catalogue (`application/catalog.py::ToolCatalogService`) lists and describes; nothing in `kriya/application` starts a server. A bounded health read is a future feature (`TRACED` at the class) |
| `DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001` | Confirmed current for the two sites re-read (`BYP-A5-08`); the rest of the census is cited, not repeated. P3 correctness pass, none decides a result |
| `GIT-ICASE-PATHSPECS-UNQUALIFIED-001` | Still UNKNOWN by construction on this host: `platform/__init__.py` strips `GIT_ATTR_SOURCE` and `GIT_LITERAL_PATHSPECS` and records `GIT_ICASE_PATHSPECS` as unknown. Cannot be characterized on a case-insensitive filesystem; a declared qualification item for a Linux host, not a blocker of this closure |
| `SEC009-AGENT-ROLE-REASON-TEXT-001` | Confirmed current (`TRACED`): `compute_violations` chooses the reason with `is_known_field`, which ignores the classification override. Message only |
| `CLI-ADMISSION-REFUSAL-TEXT-MIDRUN-AUTHORITY-UNAVAILABLE-001` | Confirmed current (`TRACED`, the sentence is in `cli.py::_echo_admission_refusal`). Display only |
| `PLAT-WINDOWS-POLICY-PARITY-001`, `PLAT-WINDOWS-FS-GIT-001`, `PLAT-WINDOWS-PROVIDERS-001`, `PLAT-PORTABILITY-P3-001` | Not defects of the supported platform (registry classification). A5 adds one fact: the path decisions of this slice use `path_relation` / `path_identity`, not string logic, so the Windows work is providers and policy patterns, not a rewrite of the gates. Future platform program |
| `GUI-EXEC-GIT-SHIM-001`, `GUI-EXEC-TOOLCHAIN-PATH-001` | Confirmed: the desktop child environment is a closed key list (`child_env.ts`, `KRIYA_CHILD_ENV_KEYS`: `PATH`, `PYTHONDONTWRITEBYTECODE`, `HOME`, `KRIYA_STATE_DIR`). Environment prerequisite and an owner PATH decision; intentional contract today |
| `GUI-LAUNCH-RETENTION-001` | Confirmed current (control: no `launch` in `control/retention.py`). Small fix at the retention owner |
| `GUI-RUN-ATTRIBUTION-001` | Confirmed current: the `runs` table has no workspace; attribution is the evidence manifest's identity (`application/history.py`). Same missing owner as `HISTORY-STATE-DIR-PER-PROJECT-001` |
| `FINDER-ALIAS-BLIND-001` | Partly confirmed: `tests/architecture/access_sites.py` resolves `asname` in one finder (the HTTP-module finder) only; the working-directory, environment and home finders were not re-read. Test-harness enforcement gap, authoritative in OI-01 |
| `DOCKER-BIND-SOURCE-REFUSED-AFTER-PATHNAME-RECREATION-001`, `WAVE8-DOCKER-VIEW-CLEANUP-RACE-001`, `CERT-OWNER-DEATH-MASK-SCRATCH-001`, `DEMO-RESET-RM-WORKSPACE-RACES-DOCKER-RELEASE-001` | One family: Docker Desktop holds or refuses a path around a contained gate. Product behaviour is fail-closed and cleanup is not gate truth (`FR-A5-18`, `FR-A5-19`). Not characterizable under the freeze without a loaded real daemon; the registry's measurements stand. Monitored environment items, plus optional hygiene |
| `RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001`, `WAVE8-PY-RUNTIME-ONLY-VENV-BOOTSTRAP-001`, `WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001` (handed over by A3) | One owner, `tools/validate.py::_ensure_project_venv_impl`, with bounded creation attempts and the typed `PYTHON_ENVIRONMENT_UNAVAILABLE` (`TRACED` at the owner). The mechanisms the rows leave open (why one gate's venv lacked a dependency; a daemon refusal read as a False verdict) need a real container run and are **not characterizable under the freeze**; they do not block other slices. The architectural statement stands without them: an environment refusal must be a typed environment verdict, never a gate verdict (Phase C, with the failure-class table) |
| `KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001` (handed over by A3) | Pinned by a committed test as today's behaviour: refused by the guarded commit, untyped (`FR-A5-05`). Fail closed; a typed refusal is the correction |
| `GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001` (handed over by A4) | A4's reading stands. A5 adds the authority candidates the row asks about: a configuration field would be classified by SEC-009 (operator authority), a requirement-contract field by slice A2's contract authority; a goal-text regex is neither. Owner policy choice; a genuine future feature until a project needs it |
| `PRODUCT-CANONICAL-SPELLING-LISTING-001`, `PRODUCT-GIT-SUBPROCESS-COST-001` | Performance observations on identity-bearing or membership-bearing functions (`canonical_spelling`, the Git reads of `AUTH-A5-02`). No correctness fact. Future performance work on production measurement only |
| `PROCESS-DIAGNOSTIC-PROBE-REAL-AUTHORITY-HOME-001` | Confirmed as a process rule (`BYP-A5-16`): the authority home is independent of the state directory by design |
| `CERT-HISTORICAL-FIXTURE-EVIDENCE-EXPOSURE-001` | The guard test and the manifest exist at the baseline (files present; not run). The remaining question (published history) is an owner disposition outside the architecture |

### Open items A5 hands on

- A6: STAGE-GENERATION and the Developer request (unchanged from A4), now also: where retrieval hits and skill text
  of a foreign index or a shared skill would sit in that request (`GAP-A5-01`); whether the run budget is part of
  qualification identity (`GAP-A5-04`); the class-D environment evidence of `FR-A5-18`; `GAP-A4-04` per the owner's
  ruling (full context path before any downgrade).
- Phase B: survival of unit checkpoints across a sandbox reset (`BYP-A5-14`); the split of `GAP-A3-04` including the
  launch record's digest; `GAP-A3-05` classification.
- Phase D (owner policy), added by A5: the alternative in `GAP-A5-03` (A or B); whether CI workflow and Git attribute
  files join the protected project-input class (`GAP-A5-02`); whether the desktop's Setup profile should set the
  requirement policies (`AUTH-A5-11`); `GOAL-STATED-JAVA-VERSION-TOOLCHAIN-AUTHORITY-001`; per-project history
  ownership (`HISTORY-STATE-DIR-PER-PROJECT-001`).

### Documents reconciled in A5

| ID | Document | Claim | Classification (label) |
|---|---|---|---|
| DOC-A5-01 | `handover/claude-context/kernel-mcp-and-tool-authority.md` | Tool governance, MCP invocation and capability authority, the TOOL lifecycle | CURRENT AND CONSISTENT for every fact A5 read in code (`TRACED`), except `DOC-A5-02` |
| DOC-A5-02 | The same note, TOOL-001 honest security statement ("every planned file write is refused (OI-128)") | Planned writes | PARTIALLY CURRENT: true for the filesystem tool; an MCP tool of a write-granted server is a planned write path into the owner workspace (`GAP-A5-03`) |
| DOC-A5-03 | `handover/claude-context/config-resolution-and-authority.md` | Layering, SEC-009 classes, the state-location authority, private stores | CURRENT AND CONSISTENT for what A5 read. It states that the packaged defaults resolve against the installation directory and does not state the consequence for the memory and skills stores (`GAP-A5-01`) |
| DOC-A5-04 | Comments in `workflow/worktree.py::create_git_worktree` ("silently falling back to the unisolated real workspace ... at this function's only caller") | Sandbox failure behaviour | STALE: the function returns a sandbox or raises, and it has four callers (`development_loop.py`, which also asserts that the result is not the workspace and refuses "to generate directly in the application workspace"; `enforce_loop.py`; `best_of_n.py`; `production_doctor.py`). Comments in `file_resolution.py` and `context_source.py` repeat the fallback wording |
| DOC-A5-05 | `docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md` | Execution surface and containment design | HISTORICAL PROPOSAL, IMPLEMENTED THEN SUPERSEDED: its own header records the S7 supersession (containment mandatory, no host opt-out); its inventory rows describe the pre-containment state (for example ShellTool with `os.getcwd()` and unrestricted network). Not authoritative for AS-IS; the subsystem notes are |
| DOC-A5-06 | Registry `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001` ("Unknown: whether any supported product path can populate or read that shared location across different projects") | Reachability | SUPERSEDED BY THIS CHARACTERIZATION: reachable, with the exact writers and readers (`GAP-A5-01`). The registry is not edited here |
| DOC-A5-07 | `kriya/application/project.py` `CONFIG_NOT_APPLIED` ("a kriya.yaml inside the opened folder is not applied to these reads") | Which configuration desktop reads use | CURRENT for reads (the cockpit's own configuration); a launched run uses the folder's configuration (`_launched_generate` runs in the workspace). Both statements are needed to read `AUTH-A5-09` |
| DOC-A5-08 | `DOC-A4-07` (config docstring "TOOL-tagged subtasks are refused outright"), handed over | Enforce and TOOL subtasks | Confirmed STALE (`AUTH-A5-06`, `SM-A5-02`) |

## Slice A6 (2026-11-15) - runtime, model, qualification, fallback, Developer request, evidence, extensibility

Evidence: `evidence/a6/` (probes P1 to P7 with output; the citation check with 47 negative controls). Every gap
below uses `AS-IS -> GAP -> REQUIRED CONTRACT -> IMPLEMENTATION DELTA -> ACCEPTANCE PROOF`. The last three are
`PROPOSED` outlines only: Phase C designs them from the truth owners backward and nothing here is implemented or
authorized. The owner's rulings on accepting A5 (2026-11-15) are applied in "Updates to gaps of earlier slices".
No live model, qualification, certification or container ran in this slice.

### GAP-A6-01 - A production-profile run never checks that its own models are qualified

| | |
|---|---|
| AS-IS | `AUTH-A6-06`, `FR-A6-14`, `BYP-A6-01`. The production profile makes a run refuse an unqualified Developer **fallback** and an unqualified Developer **retry identity**. It does not look at the primary Developer model or at the model of any other role |
| Evidence | `TRACED`: `generation_service.py::_enter_developer_model` evaluates `fallback_incompatibilities` only when `is_fallback`; `workflow.py::_primary_model_runtime_details` assesses the primary at run start and its docstring says "Evidence only; never blocks a run"; control `assess(` lists every assessor; control `run_production_doctor(` shows no run invokes the doctor; no application or KUP module reads a qualification |
| GAP | The statement "qualified inference identity equals executed inference identity" holds for the settings of a call. Whether the identity that executes **was** qualified is checked, during a run, only for a fallback. So a production run can execute every role on a model whose record is missing, stale (the runtime was upgraded, the policy changed) or failed, while the same run would refuse that model as a fallback. The check that covers all roles is `doctor --production`, a separate command whose result no run reads; and the provider-contract checks that do run prove the request reached the provider as intended, not that the model can do the role |
| First wrong owner | `generation_service.py::_enter_developer_model` (the rule is scoped to fallbacks); no owner at all for the other roles |
| Violated invariant | Evidence binds to exact identity; an unknown capability is a typed refusal, never a silent proceed (under the profile that states it) |
| REQUIRED CONTRACT (`PROPOSED`) | Under `runtime_profile: production`, a role's model is admitted to a run only with a current QUALIFIED record for the role's exact identity; the check has one owner and one result that the run records. Outside production nothing changes (recorded, never refused) |
| IMPLEMENTATION DELTA (`PROPOSED`) | One admission step where the routed configuration is final (`cli.py::_workflow_config`, and the application layer's run entry): `assess` each role model exactly as the doctor's `model.qualification` check does (shared function), typed refusal `MODEL_NOT_QUALIFIED_FOR_ROLE` before the engine is built. `_enter_developer_model` keeps its fallback rule |
| ACCEPTANCE PROOF (`PROPOSED`) | A production configuration whose primary has no record is refused before any model call; the same configuration outside production starts; a stale record refuses; mutation: removing the admission makes the test fail. The doctor and the run agree on the same fixture |
| Registry rows | None existing (new) |
| Severity (candidate) | P2. Owner policy item for Phase D: whether the operator's doctor run is the intended admission (then the asymmetry with the fallback rule should be stated as the contract), or the run must check. My recommendation: the run checks; a profile named production should not depend on a command nobody is forced to run |
| Limits | Not exercised: no production configuration was launched. The doctor's check was read at its call to `assess`, not in full |

### GAP-A6-02 - An unknown model name is served with the primary model's binding

| | |
|---|---|
| AS-IS | `AUTH-A6-01`, `FR-A6-03` |
| Evidence | `MEASURED` P7: `LLMClient._binding("a-model-no-binding-names")` equals the primary's binding (settings, window, adapter). `TRACED`: the reasoning flag is matched exactly, the binding case-folded |
| GAP | A fallback by default where every neighbouring decision refuses (an unknown adapter name, an owned request field). The call would carry the primary's sampling, window and adapter under another model name, and its qualification identity would be computed from settings the model was never bound to |
| First wrong owner | `core/llm.py::LLMClient._binding` |
| Violated invariant | Fail closed, never approximate; nothing is transferred by name |
| REQUIRED CONTRACT (`PROPOSED`) | A model name no binding names is a typed refusal before any provider contact |
| IMPLEMENTATION DELTA (`PROPOSED`) | Replace the final `return` with a refusal (`MODEL_BINDING_UNKNOWN`); one matching rule for the binding and the reasoning flag |
| ACCEPTANCE PROOF (`PROPOSED`) | P7 as a test with the inverted expectation; every model name the configuration, the chains and the routes can produce still resolves |
| Registry rows | None (new) |
| Severity (candidate) | P3: no caller that passes a name outside the configuration was found (reachability not established) |

### GAP-A6-03 - The records the desktop presents as a run's outcome are best-effort and unbound; "verified" means self-consistent

| | |
|---|---|
| AS-IS | `AUTH-A6-11`, `AUTH-A6-12`, `SM-A6-03`, `BYP-A6-12..14` |
| Evidence | `MEASURED` P5: a second write of a run id replaces the history row (`FAILURE` then `SUCCESS` leaves `SUCCESS`); the row has no workspace, digest or writer column. `MEASURED` P4: one edited record is `CHAIN_BROKEN`; all records and the seal rewritten consistently is `VERIFIED` with `run.closed` reading `SUCCESS`. `TRACED`: a history write that fails is logged and the run's result is unchanged; the evidence recorder never raises into the run; the RunRecord is the only record with revisioned, ownership-bound storage |
| GAP | Three separate statements. (a) **Authority:** the run's outcome has one durable authority, the RunRecord; the desktop's history list and detail show the history row's `status`, which is a copy written best-effort by the caller and replaceable by any later write of that id. Nothing reconciles the two. (b) **Completeness:** a run can commit to the workspace and leave no history row and no evidence store (a failed write, capture off); the desktop then has nothing to show for a change that happened. (c) **Meaning of VERIFIED:** the chain detects damage and partial edits; it does not prove the store is the one the run wrote. That is consistent with a local single-user product whose state directory is private, and it is exactly what the subsystem note calls "observational", but no document or screen says so, and the word shown is "verified" |
| First wrong owner | None wrong inside its own contract. The gap is that no contract states which record a reader may treat as the outcome |
| Violated invariant | The final result consumes verified evidence and never recomputes truth (Phase B cross-slice check); one truth, one owner |
| REQUIRED CONTRACT (`PROPOSED`) | (1) The RunRecord is the outcome of record; any surface that states a run's outcome either reads it or labels what it shows as the history copy. (2) The history row carries the workspace identity and the RunRecord revision it was written from, so a reader can detect a row that does not match its record (also closes the attribution half of `GUI-RUN-ATTRIBUTION-001`). (3) The documentation and the desktop wording state that evidence verification is an integrity check against damage, not an authenticity proof. An authenticated store is a future feature, not a production blocker for a local single-user tool |
| IMPLEMENTATION DELTA (`PROPOSED`) | Two columns on the `runs` table written by `TraceLogger.log_run` from the run context; the history detail compares them with the RunRecord when the workspace is at hand and reports `consistent` / `differs` / `record unavailable`; wording changes |
| ACCEPTANCE PROOF (`PROPOSED`) | P5 as a test: a row whose status differs from its RunRecord is reported as differing; a row of another workspace is not attributed to this one. No change to P4's behaviour, which becomes a documented limit with a test pinning it |
| Registry rows | Related: `GUI-RUN-ATTRIBUTION-001`, `HISTORY-STATE-DIR-PER-PROJECT-001` (slice A5), `GUI-EVIDENCE-POLL-COST-001`, `GUI-EVIDENCE-LARGE-RUN-LOAD-001` (both exist because every read re-verifies) |
| Severity (candidate) | P2 (audit truth). Disposition candidate: (1) and (2) FIX_REQUIRED_BEFORE_PRODUCTION as part of the final-result contract of `GAP-A3-01`; (3) INTENTIONAL_CURRENT_CONTRACT once stated. Owner policy item for Phase D |
| Limits | The desktop's wording was not read. Whether `launch.status` is the surface the result card uses for a live launch (it reads the RunRecord) was not traced in the UI |

### Updates to gaps of earlier slices

| Gap | Update from A6 |
|---|---|
| `GAP-A4-04` (P1 security / prompt-trust-boundary, owner ruling of 2026-11-15 applied) | **Trace complete; provenance is lost before dispatch, so the gap is carried as P1 SECURITY / PROMPT-TRUST-BOUNDARY.** The full path is the step table of `AUTH-A6-09` (`MEASURED` P1). Beyond what A4 measured: (1) the page text keeps its own fence END marker and its own `=== Task ===` heading verbatim (no neutralization, because it never passes the fence function); (2) with a retry package it is relabelled "Advisory reference evidence" and kept out of `prior_error_context`; without one it is the error text itself and Kriya's own scaffold instructions are derived from it; (3) on a rejected candidate it is placed in the Reviewer's **system** prompt inside the block headed "AUTHORITATIVE RUN DISPOSITION (deterministic control-plane fact ...)"; (4) it steers one deterministic selection, the targeted test; (5) it is an input of the lesson extraction, whose output is staged as learned knowledge. It does **not** reach the write gate, the candidate, a gate verdict or an approval. First wrong owner, restated: `state.error_context` is one untyped string that validator output, Kriya's contract headers and fetched text are concatenated into, and ten modules read it as validator evidence (control listing). REQUIRED CONTRACT (`PROPOSED`, replaces A4's outline): fetched text never enters `state.error_context`; it is a separate, typed advisory value that reaches a request only through `fence_untrusted_reference`, and no consumer other than the Developer prompt builder receives it (not the Reviewer prompt, not the test selection, not the scaffolds, not the lesson extraction). ACCEPTANCE PROOF: P1 as tests with inverted expectations for (a) to (e), plus the bytes-source inventory listing the live lookup. Disposition candidate: FIX_REQUIRED_BEFORE_PRODUCTION. Reach is unchanged: off by default, off in the Setup profile, offline sealed in production, an approval per query |
| `GAP-A4-03` (P1 knowledge-authority, owner ruling) | **No later authority repairs the false closure; classification FIX_REQUIRED_BEFORE_PRODUCTION (proposed).** The Developer request and the generation service never read the knowledge gate's result or the unresolved gaps (four controls). What reaches the request is the conventions text of the skills that loaded; a covering folder with no rule contributes nothing, and nothing notices that the "covered" library has no text in the request. Skill facts are split trusted / unverified at rendering (`skill_conventions.py::render_skill_conventions`), which is a label, not a validation of applicability, provenance or relevance to the project (`GAP-A4-02`, `GAP-A5-01` C). The result reports `unresolved_skill_gaps` only for gaps the unit analysis found itself |
| `GAP-A5-03` (elevated to P1 authority / transaction bypass by the owner) | **No independently authoritative transaction exists for MCP workspace mutation** (`BYP-A6-18`, the comparison table in `05-INTEGRATION-BYPASS-MAP.md`): no exact mutation scope, an approval that binds the tool identity and not the change, no isolation, no rollback, an audit record without files or digests, and no integration in the final result. Under the ruling: FIX_REQUIRED_BEFORE_PRODUCTION (proposed). The earlier A/B alternative stays the design choice for Phase C; severity is no longer P2 |
| `GAP-A5-05` (elevated to P1 mutation-authority by the owner) | A6 adds one input fact: the lesson-extraction writer (`MUT-A6-12`) is fed the retry error context, which may hold fetched text. The questions the ruling lists (who decides a skill is learned, the writer, transaction, rollback, audit, visibility to source approval, final-result accounting, per-shape behaviour) are Phase B's; nothing in the runtime or evidence layers supplies a transaction for it (the evidence store records `mirror.*` and `candidate.change` only for candidate writes) |
| `GAP-A3-05` (P1, owner ruling) | **No later aggregate mutation authority exists** in the runtime, control, metrics or evidence layers (`BYP-A6-17`, three controls). With A4 (planning) and A5 (tools, MCP, configuration, application) the search is exhaustive over `kriya/`. Phase B classifies FIX_REQUIRED_BEFORE_PRODUCTION and must first define the approval subject (unit, candidate, plan, or cumulative run delta), as the owner required |
| `GAP-A5-04` (budget authority) | The budget is in no model identity (four controls, `AUTH-A6-10`): qualification and certification are not tied to 2,400 or 3,600. The owners the ruling asks for, as the code has them: generation budget = the configuration value read through the run's clock, **one clock per top-level run** (corrected in Phase B, `MEASURED` B-P2); total across resumes and total work = no owner; recovery budget = the enforce-level bounds of `AUTH-A1-11` (counts, not time); operator / profile maximum = the production seal (at most 3,600, `AUTH-A5-11`); repository-requested value = `REPOSITORY_SAFE` at any value outside production. **Corrected in Phase B:** the sentence that each unit invocation claims a fresh clock was wrong (it repeated the A5 misreading). Under the production cap one top-level run is bounded to an hour of generation time whatever the number of units and recovery rounds. The remaining gap is in root `R-08` |
| `GAP-A5-01` (split by state class, owner ruling) | A6 contributes the request-side facts Phase B needs for each class. Code index: graph context is rendered from the project's own files and is unfenced T-REPO text; the vector leg's foreign chunk text is stored in the trace row (`retrieved_chunks`) and its paths are matched files (A5), so a foreign index reaches the request as wrong targets and wrong context, not as fenced reference. Learned knowledge: always fenced (`stage_analysis.py`). Skills: unfenced conventions with a "Rules" / "Unverified Rules" split. Release cache: not in any request (dependency resolution only; not re-traced here) |
| `GAP-A3-07` | Unchanged: in held mode `attempt.concluded` is recorded before the deferred executing gates run. A6 confirms where the consequence lands: evidence only (the store's attempt records), no decision reads them (`AUTH-A6-11`) |
| `GAP-A1-02` | A6 adds the fallback state to the list of what an invocation discards (`SM-A6-04`) |
| `BYP-A4-04` | Refined (`BYP-A6-10`): the Planner block reads the caller's error argument at planning time; fetched text was not shown to reach it |

### Reconciliation items the owner listed for A6

| Item | Result (label) |
|---|---|
| Native Ollama 65K qualification | Consistent with the code. Section 42 (`MEASURED` 2026-11-14, preserved): both 65,536 pins QUALIFIED on the native adapter (primary for all seven roles, fallback for the Developer). The mechanics that make this the only qualifiable path are `AUTH-A6-04` and `AUTH-A6-06`. Not re-run |
| Compat truncation disqualification | Consistent: the OpenAI-compatible path truncates an over-window prompt silently (81,920 tokens answered from 32,770, section 42); every role requires `over_context_refusal`; the default adapter cannot pass it. A property of that provider API, recorded as `FR-A6-09`. The packaged default adapter is therefore the one no production configuration can use: INTENTIONAL_CURRENT_CONTRACT, with a documentation item (`DOC-A6-04`) |
| Role independence warning | Intentional current contract: WARN unless `model_policy.independent_roles` names roles, then FAIL and a refused command (`AUTH-A6-08`) |
| Semantic precision boundary | Intentional current contract: `semantic.precision_boundary` is WARN unless `autonomy.semantic_region_enforcement_required`, then FAIL when the workspace holds a language without the editable-region capability (`production_doctor.py::_check_precision_boundary`). The production profile does not force region enforcement |
| Remaining class-D setup-error evidence (`FR-A5-18`; section 42: 8 setup errors in `tests/state_machine` in one parallel adjacent run, their text lost, the same tests green serially and in the tier) | **Still unresolved and not characterizable here**: the only evidence is a count; reproducing it needs a 30-minute loaded parallel run without the documented file-descriptor limit, which is not a focused test. Owner class: test harness / environment (`INFERRED`: descriptor exhaustion, since the run lacked `ulimit -n 256`). It cannot change an architecture decision: no product path is involved. Carried to Phase D as a declared, non-blocking item with the exact missing evidence (the error text of one reproduction) |

### The two container rows A5 declared uncharacterizable (owner ruling 10)

| Row | Missing evidence | Could the unknown change a security class, an authority owner or the production decision? |
|---|---|---|
| `RUNTIME-GATE-VENV-LACKS-PROJECT-TEST-DEPENDENCY-001` | Why one gate's project environment lacked a test dependency in a real contained run (the container's install log and the venv listing of that gate) | Authority owner: no (`tools/validate.py::_ensure_project_venv_impl` owns it either way). Security: no. Production decision: only through verification truth, which the next row covers. **Non-blocking** |
| `WAVE8-DOCKER-REFUSAL-BECOMES-FALSE-VERDICT-001` | Whether a daemon refusal on a loaded host is still read as a gate verdict of False (one real refused launch with its typed result) | **Yes for verification truth**: if a refusal is read as "the candidate failed", an environment event is charged to the candidate (retry budget, attribution); if it could be read as passed it would be a false success. The code read in A5 types the environment-unavailable case; the row's residual is whether every refusal path reaches that type. Carried as a **declared Phase-D blocker for the verification-truth statement only**, with the acceptance test named: a scripted daemon refusal at each launch site yields a typed environment verdict |

### Registry rows of slice A6 that did not need a gap of their own

| Row | Result (label) |
|---|---|
| `TELEMETRY-FALLBACK-ROLE-ATTRIBUTION-001` | Producer traced (`AUTH-A6-08`, `BYP-A6-16`): calls of the knowledge channels run outside any role scope (absent `model_role` under `kriya/knowledge`); the lesson extraction after a retried success uses the last Developer model, a fallback when the fallback fixed it. That this is the `unattributed` bucket measured in PRD-035 C6 is `INFERRED`. Observation only; a one-line scope at the caller |
| `GUI-PHASE-OBSERVABILITY-001` | Confirmed current: the evidence vocabulary has `phase.opened` / `phase.closed` (`attempt_evidence/model.py::KINDS`) and the reader derives phases from role or attempt scope where none is explicit (`reader.py`, `ROLE_PHASES`). Knowledge-gate and commit boundaries are not recorded. Future feature by owner decision |
| `GUI-EVIDENCE-POLL-COST-001`, `GUI-EVIDENCE-LARGE-RUN-LOAD-001` | Confirmed as design: every page is served from a chain verified in that read (`application/evidence.py::_verified_chain`). Not a defect at measured sizes; a verified-head cache is a future optimisation that must not weaken the check. Related to `GAP-A6-03` (what the check proves) |
| `GUI-M1-STORED-PROMPT-001` | Confirmed: `history.prompt` still exists as a KUP operation (`kup/serve.py`), the cockpit's context view reads `evidence.call_content`. Legacy rows only; future feature if legacy history matters |
| `GUI-RUN-REPORT-001` | Future feature. A6 adds the constraint: a report states outcome from the RunRecord (`GAP-A6-03`) |
| `GUI-KRIYA-LOG-NOT-PERSISTED-001` | **Disproved** (`MEASURED`): the Kriya Log is the run's own log file `<log dir>/runs/<run id>/kriya.log` (`application/run_log.py`, `core/logging_setup.py::run_log`). For run `20261113T060030-40a14267` that file exists on the development machine and holds the 06:04:43 `kriya.tools.gate_view` warning at line 81 (67 WARNING lines in all). The registry search looked in the application's stderr file and under the state root; the log directory is a separate location (default `~/.kriya/logs`). Residual, true as stated: the log is not part of the evidence store (no chain, no seal, no retention owner found, default file mode) |
| `LEAK-CHECK-SHARED-LINE-FALSE-POSITIVE-001` | Not re-traced (`attempt_evidence/leak_check.py` is an operator tool, authoritative in OI-107). Tooling defect of a diagnostic, false positives only; no run decision reads it |
| `GUI-APPEARANCE-001`, `GUI-NOTIFICATION-VISUAL-001` | Desktop presentation; no kernel fact. Future feature / a recorded limit of test automation |
| `GUI-NATIVE-MEMORY-DRIFT-001` | Not characterizable here (needs the two-hour soak, OI-108). Desktop resource observation; no architecture decision depends on it |
| `GUI-REASON-CODES-IN-ERROR-STATES-001` | Desktop wording. Not re-read. Cosmetic; belongs with the result-wording work of `GAP-A3-01` |
| `TEST-RESOURCEWARNING-HYGIENE-001`, `TEST-DEADLINE-TIMING-001`, `TEST-LAUNCH-APPROVAL-EXPIRY-TIMING-001`, `KERNEL-GOLDEN-HARNESS-VOLATILITY-001`, `KERNEL-GOLDEN-RUNID-PATH-NORMALIZATION-001`, `KERNEL-RUNTIME-TRACE-PATH-LENGTH-DEPENDENT-001`, `KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001`, `DEMO-V2-FIXED-STUB-PORT-PARALLEL-001`, `CERT-DEMO-V2-IDLE-STUB-XDIST-001`, `TEST-KERNEL-GOLDEN-ORDER-DEPENDENCE-IN-AD-HOC-MIX-001` | Test-harness hazards (timing under load, shared fixed resources, checkout-dependent goldens). No product path. Not re-run (a loaded parallel run is not a focused test). They matter to one architecture fact only: the certification gate is one full-suite run with no retry for luck, so a harness race can turn certification red. `KERNEL-TESTS-CHECKOUT-MEMORY-MORE-FILES-001` and part of `KERNEL-GOLDEN-HARNESS-VOLATILITY-001` are the test-side face of `GAP-A5-01` (the packaged default memory location is inside the checkout). Confirmed still present by reading: no `xdist_group` pin in `tests/test_demo_v2_owner_state.py` |
| `KERNEL-GOLDEN-DATE-DEPENDENT-001` | Stale bookkeeping: the golden holds no `added_at` value and the harness handles the calendar day (`tests/state_machine/_kernel_golden.py`, OI-53 note; `test_sm_kernel_golden.py` simulates the day) (`TRACED`) |
| `TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001` | Stale bookkeeping for the test: it now runs in a fresh Git work tree (`tests/test_tools_command.py::_hermetic_workspace`, which names this row). The design question the row raises (a repository that carries committed Kriya state archives makes mutating tools refuse there) is answered by slice A5: fail-closed by design |
| `TEST-SLICE-A-OCI-STALE-NESTED-COMMAND-STRING-001` | Confirmed current: the string `kriya runs tool retry` is still in the test file (one occurrence). Text only |
| `UI-SCALE-TEST-TIMEOUT-001` | Stale bookkeeping: the test carries the 30 s margin with the measurement in its comment |
| `WAVE8-S7-PYTHONPATH-INHERITANCE-001` | Stale bookkeeping: the test removes `PYTHONPATH` (`monkeypatch.delenv`, with the 2026-11-13 measurement in its comment) |
| `WAVE8-TRACKED-BYTECODE-001` | Confirmed current: three `.pyc` files are tracked under `handover/BACKEND-READINESS-V2/gate-certification/__pycache__/` (`MEASURED`, `git ls-files`). Hygiene outside every product tree; touching it is outside the freeze |
| `CERT-CI-PLAIN-JOBS-LOCKFILE-GROUP-001` | Hosted CI; the open residual (one timing bound on a hosted runner) cannot be characterized locally. Monitored item; no architecture fact |
| `TEST-BASELINE-THREE-STALE-FAILURES-AT-132884B-001`, `TEST-REPAIR-003-F1-RESIDUAL-VENV-CREATION-COUNT-001`, `TEST-CERT-TMP-GROWTH-TWO-STALE-ALLOWLISTS-001` | Stale bookkeeping by preserved measurement: the full suite on `82436eb` (an ancestor of the baseline, checked) ended exit 0 with 21,539 passed and 7 skipped (sprint log section 41), after these rows were registered as deterministic failures. Not re-run at the baseline: the commits after `82436eb` have had no full suite |
| `TEST-S7-H-LEDGER-STALE-CONTROL-REFERENCE-001`, `TEST-S7-H-LEDGER-OI142-CONTROL-MISSING-001` | Stale bookkeeping and duplicates of each other: the ledger entry names the renamed control `test_the_scopes_venv_source_is_mounted_into_the_view_read_only_and_writable_only_when_asked`, which exists (`tests/containment_h_ledger.py`, `tests/test_s3_execution_views.py`) |
| `TEST-CERTIFICATION-WALL-CLOCK-001` | Confirmed as registered (3h27m on `82436eb`). Owner-ruled optimisation after the confidence milestone. The project instructions' "about 8 minutes" is stale (`DOC-A6-05`) |
| `GATE-RESULT-PURPOSE-ABSENT-ON-TESTS-GATE-001` (handed over by A3) | Producer traced (`BYP-A6-15`): a purpose is declared by the candidate-gate orchestrator (three decorated functions) and the named-test oracle; the terminal verification coordinator, the development loop's baseline and regression runs, the requirement closers, regression attribution and the self-correction loop run the tests gate with none, and the closed vocabulary has no member for them. Which of them produced the record in the registry's scenario is `INFERRED` (the terminal regression or the baseline). Diagnostic only |
| `TECHNICAL-VERIFICATION-BARE-ROWS-001` (handed over by A3) | Cannot be characterized from code: the cause of the bare rows the owner saw is unknown and the registry's own closing condition is the owner's next look at the rebuilt app (or a captured DOM). It cannot change an architecture decision (a rendering of recorded gate types). Declared Phase-D item, non-blocking |
| `LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001` (handed over by A4) | Confirmed current: `graph_retrieval.py::_code_intelligence_candidates` builds one candidate per hit; `fold_overloads` exists and is the Planner-boundary fold. No measured Developer-path loss; future retrieval work |
| `RUNTIME-LINE-EXPECTED-NONZERO-001` (handed over by A3) | Fixed at the kernel projection: `application/history.py::_exit_is_acceptable` reads the gate's recorded runtime disposition, and a grounded expected non-zero exit yields `succeeded = True` (`MEASURED` P6). Stale bookkeeping for the projection; whether the desktop line follows `succeeded` was not read |
| `REVIEWER-OUTPUT-CAP-RUNAWAY-LATENCY-001` (handed over by A3) | Superseded: the Reviewer has its own 4,096-token ceiling and refuses a cut-off answer (`ReviewerAgent.DEFAULT_MAX_OUTPUT_TOKENS`, `REQUIRE_COMPLETE_OUTPUT`), which is the registry's own closing condition; the sprint log records the closed row `REVIEWER-RUNAWAY-UNBOUNDED-OUTPUT-001`. A bounded, visible wait in the desktop was not traced |
| Audit event per outbound lookup query (handed over by A4) | None exists (`TRACED`): each fetched URL gets an audit-only policy consultation that is logged at debug level (`tools/web.py::_audit_network_access`); the run events hold one `egress.authority` record per run. The only durable trace of a fetched page is the `model.request` content blob of the next request, under capture `full`. Part of the required contract of `GAP-A4-04` |

### Open items A6 hands on

- Phase B: everything in "Updates to gaps of earlier slices"; the separate authority gap for total run consumption
  (`GAP-A5-04` update); the split of `GAP-A5-01` by state class with the request-side facts above; the authority
  questions of `GAP-A5-02` and `GAP-A5-05` (no A6 fact answers who authorizes configuration or skill changes);
  whether a resumed enforce run keeps its run id and therefore its evidence store; the stale "pending" entries in
  the route table at the top of `05-INTEGRATION-BYPASS-MAP.md` (generated-test rewrite, CLI / desktop approval,
  final-result aggregation were mapped by A2 and A3).
- Phase D (owner policy), added by A6: whether the doctor or the run is the production admission for model
  qualification (`GAP-A6-01`); the outcome-of-record statement and the meaning of "verified" (`GAP-A6-03`); the two
  declared container rows and the class-D errors with their missing evidence; `TECHNICAL-VERIFICATION-BARE-ROWS-001`.

### Documents reconciled in A6

| ID | Document | Claim | Classification (label) |
|---|---|---|---|
| DOC-A6-01 | `handover/claude-context/model-runtime-and-inference.md` | Runtime port, provider contract, identity, qualification, budgets, fallback, metrics, routing | CURRENT AND CONSISTENT for every fact A6 read in code (`TRACED`), except `DOC-A6-02` and `DOC-A6-03` |
| DOC-A6-02 | The same note: "adapter `kriya-ollama-native/2`" and "`QUALIFICATION_POLICY_VERSION` (now /8 ...)" | Version constants | STALE: the code has `kriya-ollama-native/3` and `kriya-qualification/9` (`MEASURED` P2 for the first; cited symbol for the second) |
| DOC-A6-03 | The same note: "qualified inference identity == executed inference identity" | Qualification coverage | PARTIALLY CURRENT: true of the settings a call sends; it does not say that a run checks the qualification of a fallback only (`GAP-A6-01`) |
| DOC-A6-04 | `core/inference_runtime.py` docstring and `handover/INF_001_RUNTIME_PORT.md` ("the packaged default adapter") | Which adapter is the default | CURRENT, with a consequence stated nowhere: the default adapter cannot be QUALIFIED (it cannot refuse an over-window prompt), so every production configuration must name `ollama_native` (owner ruling, sprint log section 42) |
| DOC-A6-05 | `CLAUDE.md` ("the full suite in parallel (~8 min vs ~30 serial)") | Suite duration | STALE: 3h27m measured on `82436eb` (registry `TEST-CERTIFICATION-WALL-CLOCK-001`) |
| DOC-A6-06 | `handover/claude-context/verification-platform-and-assurance.md`, attempt evidence recorder ("Observational only (I-2)") | What the recorder may do | CURRENT AND CONSISTENT. It does not state what `VERIFIED` proves (`GAP-A6-03`) |
| DOC-A6-07 | Cross-cutting invariant in the project instructions: "Learned/retrieved text stays inside the untrusted-context fencing" | Fencing | STALE-CONTRADICTED BY CODE for text fetched during a run (`GAP-A4-04`) |
| DOC-A6-08 | Registry `GUI-KRIYA-LOG-NOT-PERSISTED-001` ("exists in no file afterwards") | Log persistence | CONTRADICTED BY MEASUREMENT (the file exists in the log directory). The registry is not edited here |
| DOC-A6-09 | `handover/PRODUCT-72H/SPRINT_LOG.md` section 42 | The 65K qualification, the compat disqualification, the doctor's two accepted warnings | CURRENT AND CONSISTENT with the code (preserved measurement; not re-run) |

## Phase B (2026-11-15) - consolidation, and corrections to slice-level severities

The consolidated audit is `06-GAP-AUTHORITY-AUDIT.md`: thirteen root issues (`R-01` to `R-13`), each with its one
truth, current and required owner, child gaps, bypasses, dependencies and disposition. Every registry row is mapped
to one class and root in `REGISTRY-ROOT-MAP.md` (141 of 141). The slice gaps above stay the home of their own
evidence; where this section changes a severity or a reading, this section wins.

### Corrections after the owner's rulings on accepting A6

| Gap | Slice statement | Phase-B statement |
|---|---|---|
| `GAP-A6-02` | P3; reachability not established | **P1 runtime identity, reachable in production** (`MEASURED` B-P1, `evidence/b/`). The defect is wider than the unknown-name branch: `LLMClient._binding` resolves a binding by model name, primary first. A valid configuration in which a role binding and the primary name the same model with different settings executes the role's call with the primary's context window, context policy and runtime adapter (and its `extra_body` when the role sets none), while qualification, the doctor and `kriya model status` assess the role's own binding (`role_binding_for_model`). The only same-alias conflict check covers routing candidates. Root `R-09`. FIX_REQUIRED_BEFORE_PRODUCTION |
| `GAP-A6-01` | P2, owner policy | **P1 runtime authority.** Contract decided in the audit (R-09): every production run verifies its exact active bindings (alternative A). The doctor as an external precondition would need the same check plus a second durable store. FIX_REQUIRED_BEFORE_PRODUCTION |
| `GAP-A6-03` | One gap | **Split.** Part A, runtime final-result truth, is under `R-02`: the RunRecord is written only by the run coordinator; SUCCESS needs the payload's claim and the record's own proofs; a result is reconciled against the record; the history row is written before that reconciliation and not rewritten (audit 3.5). Part B, durable audit and evidence integrity, is under `R-11` and is not closed by part A: binding and completeness are FIX_REQUIRED_BEFORE_PRODUCTION (P2); authenticity is an explicit owner contract choice (diagnostic history, recommended, or tamper-evident evidence, then P1) |
| `GAP-A5-04`, `GAP-A1-04`, `GAP-A1-06` | Three gaps | One root, `R-08` RUN-RESOURCE-BUDGET-AUTHORITY, with six levels (corrected below: the clock is run-wide, so the root is the value, the work and resumes). `GAP-A1-06` is answered (the effective per-unit value is the configuration value). 2,400 versus 3,600 is disproved as a conflict |
| `GAP-A5-01` | One gap, four failures | Seven state classes under one principle (`R-05`). Learned knowledge (`web_knowledge.db`) is operator-taught reference knowledge with one writer, `kriya learn`, always fenced: not project-derived; INTENTIONAL_CURRENT_CONTRACT once declared global. The release cache is a public-fact cache keyed by package coordinate: not a gap. The code index, the skills directory and run history remain gaps |
| `GAP-A3-04` | One gap | Split A (runtime authorization binding) and B (audit binding) under `R-03` |
| `GAP-A3-05` | P2, owner policy | P1; FIX_REQUIRED_BEFORE_PRODUCTION unless the owner states that per-unit approval is intentionally sufficient; the approval subject is defined in Phase C before any measure |
| `GAP-A4-03`, `GAP-A4-04`, `GAP-A5-03`, `GAP-A5-05`, `GAP-A4-01` | P2 or candidates | P1; FIX_REQUIRED_BEFORE_PRODUCTION (roots `R-06`, `R-10`, `R-04`, `R-04`, `R-02`) |

### Facts first established in Phase B (homes: audit section 3)

- Learned knowledge has one writer and is not project-derived; the release cache is a public-fact cache (`TRACED`).
- A resumed enforce run takes a new run id unless the caller passes the old one (`TRACED`).
- Unit checkpoints of an enforce run survive the sandbox reset only when the repository ignores `.kriya/`, which only
  the greenfield bootstrap arranges (`TRACED`, not measured). This closes the question of `BYP-A5-14`.
- The RunRecord's terminal transition and the reconciliation order (`TRACED`).
- Two bindings of one model name (`MEASURED` B-P1).

### Slice gaps by root

| Root | Slice gaps |
|---|---|
| R-01 | `GAP-A2-01`, `GAP-A2-02`, `GAP-A2-03`, `GAP-A4-05`, `GAP-A4-07`, `GAP-A4-08` |
| R-02 | `GAP-A4-01`, `GAP-A3-01`, `GAP-A3-02`, `GAP-A3-03`, `GAP-A3-06`, `GAP-A6-03` (A) |
| R-03 | `GAP-A3-04`, `GAP-A3-05`, `GAP-A5-02` (approval half) |
| R-04 | `GAP-A5-03`, `GAP-A5-05`, `GAP-A5-02` (write half) |
| R-05 | `GAP-A5-01` |
| R-06 | `GAP-A4-03`, `GAP-A4-02` |
| R-07 | `GAP-A1-01`, `GAP-A1-02`, `GAP-A1-03`, `GAP-A1-05`, `GAP-A1-07`, `GAP-A1-08`, `GAP-A1-09`, `GAP-A4-06` |
| R-08 | `GAP-A1-04`, `GAP-A1-06`, `GAP-A5-04` |
| R-09 | `GAP-A6-01`, `GAP-A6-02` |
| R-10 | `GAP-A4-04` |
| R-11 | `GAP-A6-03` (B), `GAP-A1-03`, `GAP-A3-07` |
| R-12, R-13 | Registry rows only |

Every one of the 35 slice gaps appears once (`GAP-A5-02`, `GAP-A6-03` twice by their stated halves).

### Corrections to earlier checkpoints, found by the Phase-B integration audit

A defect in this closure's own earlier work, reported as such.

| Statement | Where it was | What is true | Evidence |
|---|---|---|---|
| "The configured generation budget bounds each unit invocation; under enforce each unit invocation starts a fresh clock; no layer owns a run-level bound" | Introduced in slice A5 (`AUTH-A5-12`, `GAP-A5-04` part (b), the A5 UNKNOWN table), citing slice A1b. Repeated in slice A6 (`AUTH-A6-10`, `FR-A6-10`, the `GAP-A5-04` update), in the first Phase B checkpoint (`f3962f3`: root R-08, the A1 completion row, this section's own table above) and in the first draft of contract C-08 | The generation clock is **one per top-level mutating run**, held on the run lease; every unit invocation, retry, role, embedding call and validator deadline of that run reads it; a nested mutating boundary (a unit of an enforce run) is the same run. Slice A1 (`AUTH-A1-08`) said so. Slice A1b's "fresh budgets per re-invocation" is about attempt counts (`AUTH-A1-07`), which A5 read as time | `TRACED`: `control/run_coordinator.py::claim_run_generation_clock`, `begin_mutating_run`. `MEASURED` B-P2 (`evidence/b/`): two claims and a claim inside a nested boundary return the same start; a second top-level run has a new clock |

Consequences. (1) Under the production profile a top-level run is bounded to one hour of generation time whatever
the number of units and recovery rounds. (2) `GAP-A5-04` part (b) is withdrawn; part (a) (a repository may raise or
remove the value outside production) stands. (3) Root `R-08` is narrower: the value's authority class, no bound on
work, no ledger across resumes; P2. (4) The statement made to the owner in the A5 and A6 summaries, that total run
consumption has no owner because each unit claims a fresh clock, was wrong; the owner's ruling 4 on accepting A6 was
given on that premise and should be re-read against the corrected facts. Every row that carried the error is
corrected in place and marked.

How it was found: the owner's Phase-B method ruling (read content, compare rows of different maps that describe the
same truth). `AUTH-A1-08` and `AUTH-A5-12` disagreed; the code was read, then measured.

### Integration audit (Phase B, second checkpoint)

`06-GAP-AUTHORITY-AUDIT.md` sections 10 to 13: 33 boundaries traced from the content of the maps; nine cross-map
contradictions (`X-01` to `X-09`) resolved or classified; thirteen integration findings (`INT-01` to `INT-13`), five
of them named nowhere before, each attached to a root and closed by a clause in `07-REQUIRED-CONTRACTS.md` section 6.
`evidence/b/crossmap_check.py` checks every row reference of the set (0 dangling) and lists the 53 rows no other
document cites, which the Phase-C stage and integration documents must link.
