# 04 - Failure / recovery matrix

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS.

Columns: where the failure comes from, the typed evidence it carries, who decides what it means, whether it may be
retried, who repairs, what a person may decide, what may be mutated, and how the unit ends if nothing else changes.

## Slice A1 - how each failure class enters retry accounting

A1 covers the accounting columns only (open finding, family, charge, stop). Which file is repaired and by whom
(the attribution, owner-recovery and verifier columns) is filled by slices A2 and A3. All rows are `TRACED` from
`workflow/open_findings.py`, `workflow/recovery_coordinator.py::classify_attempt_exception`,
`workflow/retry_strategy.py::_record_attempt_failure` and `workflow/retry_policy.py`.

| ID | Failure source (types) | Typed evidence | Replaces the open finding? | Failure family | Charge | Retry eligibility | Terminal outcome if it persists |
|---|---|---|---|---|---|---|---|
| FR-A1-01 | Validator finding about the candidate: compile, tests, targeted test, runtime verification, static rule, goal-spec, regression, and any validator type not listed below | `Failure` with type, raw validator output, file locations | Yes, when authoritative | Its own signature; a new one resets scoped budgets and the exposing targeted attempt is not charged | The mode it ran in | Targeted while implicated files and targeted budget remain, then fallback-targeted once, then full-set | `STOP_EXHAUSTED` at a budget or the ceiling, or a no-progress stop |
| FR-A1-02 | Delivery failure: `anchored_edit`, `attribution_rejected`, `diagnosis_mismatch`, `misdirected_edit`, `no_op_edit`, `operation_contract`, `structural_corruption`, `unaddressed_error_location` | `Failure` with a reason code | No | Inherits the previous family | The mode it ran in | Same routes; a pre-inference capability refusal (`ANCHOR_CONTEXT_NOT_ESCALATED`) forces a strategy change at once | Same |
| FR-A1-03 | Advisory failure (authority not "authoritative") | `Failure.authority` | No; the previous primary failure stays the active one | Inherits | The mode it ran in | Same routes | Same |
| FR-A1-04 | Resource refusal before inference: `context_budget_unsatisfiable`, `output_budget_unsatisfiable` | Reason code and the budget decision | No | Inherits (the run-10 correction) | The mode it ran in | Continues; a fallback with another window may be tried | `STOP_EXHAUSTED` |
| FR-A1-05 | Unclassified exception: `general_error` | Raw text only | No | Inherits | Full-set unless it ran in another mode | Continues | `STOP_EXHAUSTED` |
| FR-A1-06 | Internal Kriya error (`UnboundLocalError`, `TypeError`, `KeyError`, `AssertionError`, `AttributeError`, `NameError`) | Exception class | No | Inherits | Charged, then stopped | None: sets `environment_failure` | `STOP_ENVIRONMENT` on the first occurrence |
| FR-A1-07 | Typed stops listed in `_record_attempt_failure` (time budget, verification infrastructure, containment setup, unattributed regression, fallback incompatible, edit protocol unsatisfiable, provider contract, unqualified retry identity, unresolved requirements, contract registry, static analysis blocked / unknown / unavailable, workspace commit, unsupported file integrity, verification tree mutated, verifier semantic authority required) | Typed failure, usually with a reason code | By the general rule of `AUTH-A1-01` for its type | By the general rule | Charged, then stopped | None | `STOP_ENVIRONMENT` |
| FR-A1-08 | Policy denial that cannot be grounded to an existing owner (`PolicyDeniedError`) | The policy result's reason code | No (`general_error` type) | Inherits | **Not charged** | None | `STOP_ENVIRONMENT` with `UNAUTHORIZED_GENERATION_TARGET` |
| FR-A1-09 | Scope denial grounded to an existing owner, and every attribution result that needs a file outside the unit's write scope | `plan_scope_conflict` with the required files | No (`plan_scope_conflict` is a non-candidate type) | Inherits | **Not charged** | Not a retry: the unit stops and the enforce loop may revise scope or reopen the owner (`BYP-A1-01`) | Unit ends with a scope conflict |
| FR-A1-10 | Candidate-independent deterministic failure (same failure after a materially different candidate, reproduced on the pre-candidate baseline) | Baseline replay output | As its underlying type | As its underlying type | Charged, then stopped | None | `STOP_ENVIRONMENT` with `CANDIDATE_INDEPENDENT_DETERMINISTIC_FAILURE` |
| FR-A1-11 | Consecutive attempts without progress reach the limit; or a declared no-progress stop (verification-only retry with no possible change, failed verifier replay, failed verifier repair) | Progress vector digest; reason code | - | - | Charged | None | No-progress stop |
| FR-A1-12 | API-contract violation detected | Violations, owners, removed signatures | By the type of the failure that carried it | By that failure | Its own counter (3) | Recovery mode until restored or its budget is spent; then hand-back to the ordinary routes under the same ceiling, or stop if not restored | `STOP_EXHAUSTED` when unrestored |
| FR-A1-13 | Generated-verifier ruling (a failing run-authored verifier) | The verifier semantic ruling | Slice A2 | Slice A2 | Charged by the ordinary rule | Replaces attribution: stop, one regeneration, or an owner selected by the strict predicate | Needs-review stop or the ordinary routes |
| FR-A1-14 | Repository content filter refused; membership authority could not list the tree (`RepositoryContentFilterRefused`, `BoundTreeError`) | Typed exception | Not recorded as an attempt failure | - | Not charged | None: the exception leaves the loop | Typed stop of the run |
| FR-A1-15 | Cancellation or interruption (not an `Exception`) | - | Not recorded | - | Not charged | None | Propagates; the run record is closed by the run lifecycle (slice A3) |

| FR-A1-16 | Orchestrator refusal before inference that no table lists: `no_progress_retry` (`ANCHOR_CONTEXT_NOT_ESCALATED`, `NO_PROGRESS_RETRY_EXHAUSTED`, `SAMPLING_NOT_PERMITTED`) | Reason code in `diagnostics` | **Yes** (unknown type defaults to candidate finding) | **Its own signature: a new family, scoped budgets replenished** | The mode it ran in | Continues. For `ANCHOR_CONTEXT_NOT_ESCALATED` the forced strategy transition closes the targeted budget again in the same recording; the cleared fallback one-shot stays cleared | `STOP_EXHAUSTED` or a no-progress stop. `GAP-A1-07` (`MEASURED`, probe P10) |

### Slice A1b - enforce-level outcomes of a unit that stopped (`TRACED`, `SM-A1-04`)

| ID | Unit outcome reaching the enforce loop | Decided by | Next | Terminal outcome if it persists |
|---|---|---|---|---|
| FR-A1-17 | Scope conflict whose artifacts resolve to completed owners | `build_recovery_execution_plan` | Owner recovery cycle, then the consumer step | After 3 cycles the stage fails; touched owners end `needs_review`; the plan does not commit |
| FR-A1-18 | Scope conflict whose artifact has no resolvable owner, an ambiguous group order, or a relationship artifact that exists but whose owner has not completed | The same function; the materialized-artifact check | No recovery | The stage fails with the conflict unresolved |
| FR-A1-19 | Recovery would change an owner that has a completed TOOL dependent | `_completed_tool_dependents` | No owner is invoked | Typed `TOOL_STAGE_INVALIDATED_NOT_REPLAYABLE` |
| FR-A1-20 | A `MAY_CHANGE` owner answers in any form other than the unit's typed identical-regeneration stop | The `may_change_unchanged` predicate | The group fails | The recovery is rejected even if another participant fixed the cause (`RECOVERY-MAY-CHANGE-ANSWERED-NO-CHANGE-REFUSED-IN-UNIT-001`: confirmed by trace) |
| FR-A1-21 | Any stage ends without `completed` | The terminal block | The plan sandbox is not committed and its completions are discarded | Every accepted earlier unit's work is discarded with it; a resume starts the plan again (`GAP-A1-09`) |
| FR-A1-22 | Typed stop of a unit that is not a scope conflict (time budget, environment, exhausted budgets) on a unit that owns files | No enforce-level decision exists for it | None | The stage fails. Only a verification-only unit's typed no-change stop has a route to an upstream owner (`SM-A1-04` step 4) |

Human authority in the retry loop: none. A person acts only at the approval gate, the verifier semantic review
and the resume request, which belong to slices A2 and A3 and to `AUTH-A1-09` / `AUTH-A1-10`.

## Slice A2 - failures that involve a run-authored test, and admission

| ID | Situation | Decided by | Outcome | Label |
|---|---|---|---|---|
| FR-A2-01 | Mapped run-authored test **fails**, run unattended | `AUTH-A2-08` | Unit stops `verifier_semantic_authority_required` (`VERIFIER_SEMANTIC_UNATTENDED`) before any retry; nothing applied | `MEASURED` (committed test) |
| FR-A2-02 | Same, attended, person says valid, strict owner selected, enforce unit | `AUTH-A2-09` | Production owner repaired; verifier write-denied. If the owner is another unit the locator-tier attribution names out-of-scope files and the existing plan-scope escalation takes over (`FR-A1` rows, `SM-A1-04`) | `TRACED`; the cross-unit continuation was not executed |
| FR-A2-03 | Same, attended, valid, no owner selected (always so in a direct run) | `AUTH-A2-09` | Unit stops (`VERIFIER_SEMANTIC_NO_PRODUCTION_OWNER_AUTHORITY`) | `MEASURED` for direct (committed test) |
| FR-A2-04 | Same, attended, person says invalid | `AUTH-A2-08` | Enforce allow-list unit: one regeneration of that path. Direct run: stop (`VERIFIER_SEMANTIC_REGENERATION_UNAVAILABLE`) | `MEASURED` for direct (committed test); enforce `TRACED` |
| FR-A2-05 | A verifier may no longer be written and the next attempt would be FULL_SET | `UnitSemantics.full_regeneration_refusal` | Unit stops (`VERIFIER_SEMANTIC_FULL_REGENERATION_REFUSED`) | `TRACED` |
| FR-A2-06 | Mapped run-authored test **passes**, run unattended | `AUTH-A2-08` at the pass seams | Candidate not accepted, unit stops for review; nothing applied | `MEASURED` (committed test) |
| FR-A2-07 | Executed case whose run-authored source is ambiguous or unreadable | `AUTH-A2-06` | Unit stops (`..._SUBJECT_AMBIGUOUS` / `..._SUBJECT_UNREADABLE`), nobody asked | `TRACED` |
| FR-A2-08 | Run-authored test executed and failed, identity **does not map** (src layout, language without a grammar) | nobody: `AUTH-A2-06` returns no subject and no unknown | Historical retry: `AUTH-A2-10` picks targets, the Developer may rewrite the test or the code beside it, full unit budgets | `MEASURED` at the derivation (P5, P7) and for the fall-through (committed test `..._keeps_the_historical_retry`); `GAP-A2-02` |
| FR-A2-09 | Test gate failed without a COMPLETE report (collection error, test-source compile failure, timeout) | nobody (by the owner's correction of 2026-11-14) | Historical retry, full unit budgets | `TRACED`; `BYP-A2-03` |
| FR-A2-10 | Goal has a mandatory claim with no bound authority, policy `block` | `AUTH-A2-03` | Refused before any model call: `VERIFICATION_AUTHORITY_REQUIRED` with typed authority requests; or `GOAL_INSUFFICIENT_FOR_VERIFICATION` when a statement is ambiguous | `TRACED`; refusal `MEASURED` (P8, P9) |
| FR-A2-11 | Same goal, policy `record` | `AUTH-A2-03` | Sealed and run; model-written semantics follow (`GAP-A2-01`). Run 10 is the preserved instance: 5 of 5 entries `AUTHORITY_REQUIRED` and the run proceeded | `TRACED`; preserved (registry row, sealed run 10) |

## Slice A3 - failures between a staged candidate and the run's result

"Stop" means a deterministic stop of the unit: `retry_strategy.py` lists the failure type among those a Developer
retry cannot change. "Attempt failure" means the failure enters the retry accounting of slice A1 (`FR-A1` rows).
Nothing in this table is applied to the unit workspace unless the row says so.

| ID | Situation | Decided by | Outcome | Recovery available | Label |
|---|---|---|---|---|---|
| FR-A3-01 | The candidate transaction refuses a file (operation contract, edit protocol, write scope, integrity) | `AUTH-A3-01` | Nothing staged; attempt failure with the refusal's typed reason | Retry per `FR-A1` | `TRACED` |
| FR-A3-02 | A declared required verification has no passing authoritative evidence | `development_loop.py::accept_candidate` (`_raise_if_required_verification_unresolved`) | `verification_infrastructure_failure`: "refusing pre-apply success" | Per that type's routing (A1) | `TRACED` |
| FR-A3-03 | An original requirement blocks at the unit boundary | `AUTH-A3-09` | Stop `requirements_unresolved`; under `unverified_policy = block` a goal with an unbound behaviour claim is refused earlier, at admission (`FR-A2-10`; `MEASURED` P8: `verification_authority_required`, nothing generated) | None | `TRACED`; admission `MEASURED` (P8) |
| FR-A3-04 | Static analysis does not permit; the exception-masking rule finds a mask; the artifact registry cannot be derived | `stage_apply.py::run_static_analysis_gate`, `run_semantic_safety_gate`, `run_artifact_registry_gate` | Stop; the static-analysis message carries no scanner text | None | `TRACED` (call sites; gate bodies not read) |
| FR-A3-05 | The person declines the change | `AUTH-A3-04` | Unit result `human_rejected`, files `[]`, sandbox removed, checkpoint deleted, full trace row | A new run | `TRACED` |
| FR-A3-06 | No decision could be obtained (expired, host gone, channel failed, unrecognized answer, decision not recordable, a launch that cannot ask) | `AUTH-A3-04` | Unit result `approval_required`; "nothing was approved and nobody declined"; the reason code is in the `approval.decision` event | A new run | `TRACED`; classification `MEASURED` (P7) |
| FR-A3-07 | Approval required and no callback was supplied | `AUTH-A3-03` | Same result as `FR-A3-06`; the event says `unavailable` with no reason code | A new run | `TRACED` |
| FR-A3-08 | The candidate changed after the source approval | `AUTH-A3-05` | `verification_infrastructure_failure`: nothing executed, nothing applied | The retry loop produces a new candidate, reviewed again | `TRACED`; predicate `MEASURED` (P7) |
| FR-A3-09 | A held gate or a requirement closer fails after the approval | The gate | Attempt failure through the ordinary loop; the pre-approval review is dropped so the final review describes what failed | Retry; the next candidate is reviewed again | `TRACED` |
| FR-A3-10 | Terminal regression blocks | `AUTH-A3-07` | Attributable: retryable `regression_test` failure. Nothing attributable: stop `regression_unattributed` | Retry, or none | `TRACED` |
| FR-A3-11 | The last attempt fails at terminal regression after an approval | - | The run ends on its budgets; the result's `review` is the pre-approval review and `review_included_in_approval` is True; no rejected-candidate diagnostic review is produced | - | `TRACED` (`begin_attempt` resets the review only at the next iteration; `final_review` reuses it); pinned by `tests/golden/kernel/legacy_gates_exhausted.json` (the field reads `true` there). `GAP-A3-06` |
| FR-A3-12 | The commit finds the batch is not the verified one, or has no binding | `AUTH-A3-02` | Workspace UNCHANGED; stop `workspace_commit_failed` (`VERIFIED_CANDIDATE_EVIDENCE_STALE` / `..._MISSING`) | None in the run | `MEASURED` (P6) + `TRACED` |
| FR-A3-13 | The commit is refused for another reason (semantic safety, static-analysis guard, contract transition, intent not persisted), conflicts on a base revision, fails and rolls back, or ends UNCERTAIN | `AUTH-A3-14` | UNCHANGED: stop `workspace_commit_failed` (contract codes: `contract_registry_blocked`). UNCERTAIN: stop; enforce keeps the sandbox and reports `needs_review` with `retained_candidate_path`; later mutating runs are refused | `kriya runs recover` for UNCERTAIN | `TRACED` |
| FR-A3-14 | The candidate is applied and the final review cannot be performed (budget refusal, deadline, provider error) | `AUTH-A3-10` | Direct and milestone: unit non-success `final_review_refused`; bytes stay; checkpoint deleted; the milestone plan stops, the unit is FAILED, later units BLOCKED (`MEASURED` P1, P3). Enforce: the unit is accepted with `ADVISORY_REVIEW_NOT_PERFORMED` (`MEASURED` P2) | Milestone: the failure callback may answer retry. None otherwise | `MEASURED` (P1-P3) + `TRACED`. `GAP-A3-03` |
| FR-A3-15 | An enforce terminal gate reports a gap (for example an original requirement blocks, with a precise verifier detail) | `AUTH-A3-08` | Run `failed` (`needs_review` for an artifact error); the gap text is in the result (`global_*_gap`); every unit's accepted work in the plan sandbox is discarded and the sandbox removed | **None.** No unit is reopened and no repair attempt is made | `TRACED` (`enforce_loop.py` 4573-4661: after `gate_service.run` the only branches are commit or discard). `GAP-A3-02` |
| FR-A3-16 | An exception escapes the terminal gates or the commit (enforce) | The error handler in the enforce tail | Durable state settled as the ordinary path would have; an unproven commit is UNCERTAIN; then the error propagates | `kriya runs recover` | `TRACED` |
| FR-A3-17 | A milestone unit passes its gates but dropped an earlier dependency, or its capability transition is not in the registry | `milestones.py::_MilestonePlanDriver.check_passed_unit` | The unit is turned into a failure (`dependency_regression` / `contract_registry_incomplete`) although its bytes are already in the workspace | The failure callback | `TRACED` |
| FR-A3-18 | Milestone replay did not run, or found a regression | `AUTH-A3-15` | Plan result `milestone_replay_not_executed` / `milestone_replay_failed`; the integration unit is BLOCKED | None | `TRACED` |
| FR-A3-19 | The integration unit fails (for example an original requirement blocks at plan level) | `AUTH-A3-09` in the integration unit | Plan result `integration_failed`; the milestones' commits stay in the workspace and are reported as retained | A new run | `TRACED` |
| FR-A3-20 | The process dies while a held candidate waits for its review | - | No candidate checkpoint exists (it is written after the held gates); the launch record's pending review is closed EXPIRED by `finish` when that runs | A resume starts from the last earlier stage checkpoint and regenerates the candidate | `TRACED` (registry `HITL-RESUME-DEFERRED-GATES-001`) |
| FR-A3-21 | Post-commit persistence fails (artifact registry, learning, plan document, control state) | The writer | The run's verified result stands; the failure is listed in `post_commit_persistence_errors` | Operator | `TRACED` |
| FR-A3-22 | The pre-approval review cannot be produced | `AUTH-A3-12` | Approval proceeds; the approver is told "NOT ATTACHED" with the reason code; `review.pre_approval_unattached` event | - | `TRACED` |

UNKNOWN behaviour in this slice, in one place:

| Unknown | What the code does | Fail closed? |
|---|---|---|
| No approval decision | `FR-A3-06`, `FR-A3-07` | Yes |
| No binding / changed binding | `FR-A3-12` | Yes |
| Commit outcome unproven | UNCERTAIN (`FR-A3-13`, `FR-A3-16`) | Yes |
| Requirement PENDING / UNKNOWN / UNVERIFIED | Blocks only under the `block` policies; otherwise the run succeeds with the truth reported beside it | **No** under the defaults (`GAP-A3-01`) |
| Replay with no verdict | Recorded, not a failure (`AUTH-A3-15`) | No, bounded by the integration suite |
| Invalid `sensitive_paths` regex | Logged, that pattern not enforced (`AUTH-A3-03`) | **No** for that pattern |
| Final review not performed | Non-success on direct and milestone, accepted on enforce (`FR-A3-14`) | Divergent |

## Slice A4 - failures before the first unit runs, and in the inputs every unit shares

"Before any model call" is stated only where the code order was read. Nothing in this table is applied to a
workspace.

| ID | Failure | Detected by | Outcome | Retry / recovery | Fails safely? | Label |
|---|---|---|---|---|---|---|
| FR-A4-01 | Requirement contract unreadable, malformed, inside the workspace, or bound to another goal | `requirement_contract.py` | `REQUIREMENT_CONTRACT_INVALID` / `REQUIREMENT_CONTRACT_GOAL_MISMATCH`, before any model call | None | Yes | `TRACED` |
| FR-A4-02 | Knowledge gap, unconfirmed | `AUTH-A4-05` | `status: knowledge_gap` before any model call (direct: stage 0; enforce: before planning) | The person confirms and reruns | Yes for the gate; the coverage input is weak (`GAP-A4-03`) | `TRACED`, `MEASURED` P5 |
| FR-A4-03 | Knowledge gap first named by the Planner's text in an enforce unit | Unit stage 0 with coordinate-scoped acknowledgement | The unit returns `knowledge_gap`; it becomes the run's result only while no file is established, otherwise the unit is an ordinary non-passing unit | None | Yes (stops), but the trigger is model text | `TRACED`, `MEASURED` P5 for the predicate |
| FR-A4-04 | Goal not admissible under a blocking requirement policy; verification authority required | `verification_contract.refusal()` (slice A2) | Enforce: `failure`, `GOAL_ADMISSION` or `VERIFICATION_AUTHORITY`, authority requests sealed. Direct: the same refusal inside the unit | The operator supplies authority | Yes | `TRACED` (call sites) |
| FR-A4-05 | Baseline authority cannot run; or the baseline already satisfies every mandatory claim | `contract_baseline.py` (enforce only) | `VERIFICATION_AUTHORITY_UNAVAILABLE` typed stop; or `NoMutationRequired`: a **success without a model call** | None | Yes | `TRACED` |
| FR-A4-06 | Planner answer empty, truncated or not JSON (direct) | `classify_plan_completeness` | `planner_output_incomplete`; no repair | None | Yes | `TRACED` |
| FR-A4-07 | Planner structured block fails the schema, names an unknown or non-plannable tool, or modifies an absent path (direct) | The same, plus `_classify_against_project` | Up to 2 repairs, then `planner_output_schema_invalid` | Bounded repair | Yes | `TRACED` |
| FR-A4-08 | Planner names a path outside path authority (direct) | Schema validator inside `PlannedFile` | `planner_output_unauthorized_path`, never repaired | None | Yes | `TRACED` |
| FR-A4-09 | Structured plan invalid (enforce), any of the 37 `plan_validation` reason codes or the loop's own checks | `AUTH-A4-10` | Up to 2 repairs, then `needs_review`, `PLANNING_ERROR`, `STRUCTURED_PLAN_REPAIR_EXHAUSTED`; never whole-goal execution | Bounded repair with preservation of validated facts | Yes | `TRACED`, `MEASURED` P3 |
| FR-A4-10 | A repair fixes one thing and silently regresses another | The two regression checks on the obligation ledger | The candidate is still invalid (`SEMANTIC_CONTRACT_REGRESSION_REJECTED`, `PRESERVED_REFERENCE_REGRESSION_REJECTED`); the slot is spent | None extra | Yes | `TRACED` |
| FR-A4-11 | Planner call stopped by the run deadline | `InferenceDeadlineError` | Typed deadline stop (`deadline_stop_result`) on both shapes | None | Yes; the CLI text after it is inaccurate (registry `CLI-RESUME-HINT-DESIGN-STAGE-STOP-TEXT-001`) | `TRACED` |
| FR-A4-12 | Plan sandbox or skills run shadow cannot be created | `create_git_worktree`, `RunShadowRefused` | Typed stop or `STRUCTURED_PLAN_UNAVAILABLE`; learning discarded | None | Yes | `TRACED` |
| FR-A4-13 | A skill folder cannot be loaded | `SkillEngine.discover_and_load` | Logged; the skill is absent from the run; the run continues | None | **No signal in the result**: a rule the operator relies on can be missing silently | `TRACED` |
| FR-A4-14 | Applicable skill unverified, or a goal library has no skill | `stage_analysis.py` section 1.2 | Live lookup if enabled and approved, else the person is asked once; declining proceeds "best-effort" and the names are recorded as unresolved skill gaps | None | Proceeds by design, with a record | `TRACED` |
| FR-A4-15 | Live lookup: unsafe term, search or fetch error, nothing usable, callback error | `AUTH-A4-06` | Nothing is sent or nothing is used; the run continues | None | Yes | `TRACED`, `MEASURED` P6 |
| FR-A4-16 | Current Project View, grounded owner candidates or Code Intelligence localization unavailable | `stage_analysis.py`, `stage_planning.py`, the enforce planning loop | Recorded or logged; planning continues with less evidence | None | Degrades; plan validation still reads the disk | `TRACED` |
| FR-A4-17 | The request's runtime requirement is not recognized | `AUTH-A4-13` | A Planner runtime verifier is removed, or the plan is refused as unjustified | Plan repair in the refused case | **No** for a false negative: success without execution (`GAP-A4-05`) | `TRACED`; the fixed case `MEASURED` by tests |
| FR-A4-18 | Unknown execution mode | `WorkflowController.execute`, the config schema | Raises / schema refusal | None | Yes | `TRACED` |
| FR-A4-19 | The Architect's design names a library the goal did not (direct and milestone units) | Stage 2A of `stage_architecture.py` | Approval callback asked; declined: `human_rejected`; no decision: `approval_required`; **no callback: proceeds with a warning** | None | Yes with a callback; no without one (`BYP-A4-18`) | `TRACED` |

### UNKNOWN handling in this slice

| UNKNOWN | What the code does | Fails safely? |
|---|---|---|
| Release date of a named version unknown (or offline) | Not a temporal gap | No, by design of offline mode |
| Ecosystem of the workspace unknown | Assumed Java for the registry lookup | Benign: a miss is "unknown release date" |
| Whether a sentence is a requirement or context | Every separable statement is a requirement or a constraint | Over-inclusive (safe); the blocking policies then refuse what cannot be closed |
| Whether the goal is ambiguous | Not represented (`AUTH-A4-03`) | No |
| Whether a skill applies to a unit | Decided on text that includes Planner prose (`AUTH-A4-08`) | Not safe or unsafe: unbound |
| Whether the request requires execution | A miss reads "no" (`AUTH-A4-13`) | No |
| A plan criterion id that does not resolve at render time | Silently omitted from the unit goal | Relies on `validate_plan` having rejected it |
