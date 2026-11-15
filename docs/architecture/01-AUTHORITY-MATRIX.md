# 01 - Authority matrix

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS. All paths are under `kriya/` unless stated.

Each row answers: what is decided, who owns it today, what evidence may and may not establish it, the model's and a
person's role, where it is persisted, the alternate paths found by a negative-control search, and what happens when
the answer is unknown. "Required single owner" is filled only where the AS-IS owner is already the right one; where
it is not, the cell points to the gap and stays open until Phase C.

## Slice A1 - retry, blocker identity, allowance, checkpoint/resume

### AUTH-A1-01 - Is this failure a statement about the candidate?

| Field | AS-IS |
|---|---|
| Decision | Whether a failed attempt's failure opens or replaces the open finding |
| Current owner | `workflow/open_findings.py::replaces_open_finding` |
| Required single owner | Same (already single) |
| Evidence allowed | The validator-set `Failure.type`, its category (`failure_reporting.categorize_failure`), and `Failure.authority` |
| Evidence forbidden | Model text: the finding's subject is copied from the validator's `Failure` and is immutable |
| Model role | None |
| Human role | None |
| Persistence | Memory only: `GenerationState.open_findings` |
| Readers | `open_findings.update_open_findings`; the family inheritance in `workflow/retry_strategy.py::_record_attempt_failure` |
| Alternate paths (negative control) | Search for every assignment to `open_findings` in `kriya/`: `retry_strategy.py` (the update) and `best_of_n.py::reset_state_for_independent_candidate` (reset to empty). No other writer |
| UNKNOWN behaviour | A failure type the category table does not know is treated as a candidate finding and kept (remembered, not forgotten). Because the same predicate also gates the family inheritance (`AUTH-A1-03`), that default is safe for memory and **unsafe for the allowance**: an unlisted non-candidate type replaces the finding and can replenish the scoped budgets. Measured instance: `no_progress_retry`, an orchestrator refusal raised before inference by `workflow/candidate_transaction.py` and `workflow/repair_orchestrator.py`, is in no table (`GAP-A1-07`, probe P10) |
| Label | `TRACED`; `MEASURED` by `tests/test_non_candidate_failure_scoped_reset_001.py` (9 passed on this baseline, 2026-11-15), probe P7 and A1b probe P10 |

### AUTH-A1-02 - Is blocker B still open, resolved, or replaced by B2?

| Field | AS-IS |
|---|---|
| Decision | Continuity and resolution of one blocker across attempts |
| Current owner | **None.** No function decides it as a typed fact |
| What happens instead | `update_open_findings` returns a one-element list built from the newest authoritative candidate failure (`finding_from_failure`), or the unchanged list. A finding is never marked resolved. The list is emptied only by Best-of-N or when the invocation ends |
| Identity available | None. `OpenFinding` fields are `failure_type, reason_code, files, source, evidence, first_attempt, locations`. There is no ID, no digest, no state |
| Evidence that it is not stable | Probe P4: the identical failure recorded at attempts 2 and 5 gives `first_attempt` 2 then 5 and the two findings are not equal. Probe P5: the same assertion one line lower (75 to 76) gives another new finding with different locations. Run 10 (sealed, cited in sprint log 46): `first_attempt` went 2, 5, 8 for one assertion |
| Model role | None |
| Human role | None |
| Persistence | Memory only |
| UNKNOWN behaviour | Not represented. The loop cannot say "unknown whether B is resolved" |
| Required single owner | Open: `GAP-A1-01` |
| Label | `TRACED` + `MEASURED` (probe P4-P6) |

### AUTH-A1-03 - Is this a new failure family (fresh scoped allowance)?

| Field | AS-IS |
|---|---|
| Decision | Whether the targeted counter and the one-shot fallback-targeted flag are replenished |
| Current owner | `workflow/retry_policy.py::observe_failure_family`, on the signature from `workflow/failure_grounding.py::build_failure_signature`, gated by `AUTH-A1-01` (a failure that does not replace the open finding inherits the previous signature) |
| Rule | New family = signature differs from the previous attempt's AND was never seen in this invocation (`RetryBudgets.seen_failure_signatures`) |
| What the signature is | `(failure_type, detail)`. Detail is, in order of preference: a static-rule identity; `(basenames, "javac", messages, details)`; `(basenames, "exception", last cause type and message)`; `(basenames, "digest", sha256 of normalised text)`. Line numbers are removed; files are basenames |
| Evidence allowed | Validator output text and the validator-set failure type |
| Evidence forbidden | Model text |
| Model role | None directly. The model changes the candidate, which changes the validator text |
| Human role | None |
| Persistence | Memory only: `RetryBudgets.last_failure_signature`, `seen_failure_signatures` |
| Alternate paths (negative control) | Search for writers of `targeted_retry_count = 0` and `fallback_targeted_attempted = False`: only `retry_policy.reset_scoped_budgets_for_new_family`. Best-of-N resets `retry_count`, `last_failure_signature`, `seen_failure_signatures` and `fallback_targeted_requested`, and by its own docstring leaves the two scoped fields alone |
| Measured behaviour | P1: a test-compile error and an assertion in the same gate type and file are different families. P2: the same assertion at line 75 and at line 76 is the same family. P3: the same assertion text in a different test method of the same file is the same family. P8: a new family after a targeted attempt leaves targeted at 0, clears the fallback flag and charges nothing. P9: returning to a family seen earlier is not new |
| UNKNOWN behaviour | No unknown state: unparseable output falls to the text digest, so any text change is a new family and identical text is the same family |
| Required single owner | The reducer is single. Whether a text signature may be the authority for an allowance is open: `GAP-A1-01` |
| Label | `TRACED` + `MEASURED` (probe P1-P3, P8, P9; the 9 committed tests) |

### AUTH-A1-04 - Did the attempt make progress?

| Field | AS-IS |
|---|---|
| Decision | The classification of a failed attempt (`PROGRESS`, `NO_PROGRESS`, `REPEATED_ACTION`, `REGRESSION`, `REPEATED_VECTOR`) and the consecutive no-progress count |
| Current owner | `workflow/retry_strategy.py::record_workspace_progress` with `workflow/retry_progress.py::classify_progress` |
| Evidence allowed | The progress vector (`retry_progress.ProgressVector`: failure signature, workspace hash, implicated and missing files, evidence fingerprint, context revisions, action, protocol, request profile, plan revision, repair-contract revision, diagnostics) and the effective workspace hash |
| Evidence forbidden | Timestamps, attempt numbers, raw model text, prompt wording (excluded from the vector by design) |
| What it controls | Only `consecutive_no_progress_attempts`, `no_progress_terminated`, and through `force_strategy_transition` the closing of the targeted budget. It grants no allowance |
| Limit | `max(3, autonomy.max_consecutive_no_progress_attempts)`; the config default is 2, so a configured 1 or 2 is read as 3 (`MEASURED`, A1b probe P15). `application/readiness.py` tells the operator the run "stops after {configured value} consecutive attempts", so with the default it states 2 while the loop stops at 3 (`TRACED`). `GAP-A1-05` |
| Model role | None |
| Human role | None |
| Persistence | Memory only. Each classification is also a run event (`retry.progress_vector`, `retry.no_progress_terminal`) |
| Alternate paths (negative control) | Search for writers of `no_progress_terminated`, `consecutive_no_progress_attempts`, `progress_vector_digests`, `last_failed_workspace_hash` outside `retry_strategy.py`: only the Best-of-N reset. Inside `retry_strategy.py` four more functions set `no_progress_terminated` for declared stops: `_apply_declared_no_progress_stop`, `_admit_verification_only_retry`, `_stop_after_a_failed_verifier_replay`, `_stop_after_a_failed_verifier_repair` |
| UNKNOWN behaviour | None represented |
| Required single owner | Same module (already single for the counter) |
| Label | `TRACED`. The statement "PROGRESS is workspace movement, not blocker resolution" is `MEASURED` in the preserved run-10 reproducer (`~/kriya-evidence/product-72h-confidence/ws4-same-blocker-prefix-2026-11-14/`, pre-fix and post-fix output, baseline `3722d9d` lineage) and was not re-run in A1 |

### AUTH-A1-05 - Continue or stop, and in which mode?

| Field | AS-IS |
|---|---|
| Decision | The next retry action: API-contract recovery, targeted, missing-files, fallback-targeted, full-set, stop-environment, stop-exhausted |
| Current owner | `workflow/retry_policy.py::decide_retry_action` (pure) |
| Call sites | Loop head: `attempt_lifecycle.plan_attempt` -> `decide_for_state`. After a failure: `recovery_coordinator.conclude_attempt_failure` -> `decide_for_state`. Inside the admitted attempt: `attempt.run_attempt` -> `decide_attempt_mode`. All three read the state through one helper (`_state_inputs`) |
| Stops decided before the policy | `conclude_attempt_failure` stops on `plan_scope_conflict` or `no_progress_terminated` without asking the policy |
| Override | `recovery_coordinator._route_around_patch_incompatible_fallbacks` may turn `STOP_EXHAUSTED` into a typed `STOP_ENVIRONMENT` (`FALLBACK_MODEL_INCOMPATIBLE`); it never adds an attempt |
| Evidence allowed | The run state's counters and flags only |
| Model role | None |
| Human role | None inside the loop |
| Persistence | None. The decision is recorded as evidence (`recovery.decision`) when attempt-evidence capture is on |
| UNKNOWN behaviour | None represented: every input is a counter or a boolean |
| Required single owner | Same (already single) |
| Label | `TRACED` |

### AUTH-A1-06 - Who charges a failed attempt and who else writes budgets?

| Field | AS-IS |
|---|---|
| Decision | Which budget a failed attempt consumes |
| Current owner | `workflow/retry_policy.py::charge_failed_attempt`, called once, at the end of `_record_attempt_failure` |
| Rule | Plan-scope conflict or terminal policy denial: nothing. API-contract recovery: its own counter. Targeted or missing-files: the targeted counter unless the attempt exposed a new family. Fallback-targeted: nothing (bounded by its flag). Everything else: the full-set counter. A successful attempt charges nothing |
| Other writers of `RetryBudgets` fields (negative control: search for every assignment to a `budgets.` field in `kriya/`) | `workflow/attempt.py::run_attempt` (sets `fallback_targeted_attempted`, clears `fallback_targeted_requested`, increments `fallback_attempts_used` at two sites, sets `scoped_full_set_failure_signature`); `workflow/attempt_lifecycle.py::record_phase_advanced` (increments `api_contract_recovery_count`); `workflow/candidate_transaction.py::_request_fallback_for_rejected_authoritative_target` (sets `fallback_targeted_requested`); `workflow/repair_orchestrator.py` (sets `last_retry_evidence_fingerprint`); `workflow/retry_strategy.py` (sets `last_failure_signature`); `workflow/retry_policy.py` (family, reset, strategy transition, charge); `workflow/best_of_n.py::reset_state_for_independent_candidate` (resets several). The `budgets` object itself is never replaced |
| Assessment | One decision reducer; seven writer modules for the state it reads. Each field has an identifiable writer, but nothing structural stops a new writer. Recorded as a fact, not as a gap: no wrong decision was traced to it |
| Writer classification (A1b; every assignment found by the regular-expression search recorded in `evidence/a1/README.md`, 26 sites) | **Initialization:** `RetryBudgets()` defaults only, one per `GenerationState` (probe P14). **Charge:** `retry_policy.charge_failed_attempt` (3 sites); `attempt_lifecycle.record_phase_advanced` (API-contract counter). **Allowance grant (reset):** `retry_policy.reset_scoped_budgets_for_new_family` (3 fields) and nothing else. **Allowance closure:** `retry_policy.force_strategy_transition`. **Consumption of a one-shot:** `attempt.run_attempt` (`fallback_targeted_attempted`, `fallback_attempts_used` x2). **Request flags:** `candidate_transaction._request_fallback_for_rejected_authoritative_target`, `force_strategy_transition`, cleared in `run_attempt`. **Bookkeeping of identity:** `retry_strategy` (`last_failure_signature`), `retry_policy.observe_failure_family` (`seen_failure_signatures`), `attempt.run_attempt` (`scoped_full_set_failure_signature`), `repair_orchestrator` (`last_retry_evidence_fingerprint`). **Reset for an independent candidate:** `best_of_n.reset_state_for_independent_candidate` (7 fields) and its ceiling extension (`best_of_n_candidates_tried`). **Copy:** `stage_apply` to the run record (never read back). **Recovery / resume restore:** none exists. **Incidental:** none found |
| Model role | None |
| Human role | None |
| Persistence | Memory only. On the success path `workflow/stage_apply.py` copies `retry_count` and `targeted_retry_count` into the run record (`retry_counters`); no code in `kriya/` reads that field back |
| Label | `TRACED` |

### AUTH-A1-07 - Global attempt ceiling

| Field | AS-IS |
|---|---|
| Decision | The maximum number of attempts of one unit invocation |
| Current owner | `workflow/retry_policy.py::max_total_attempts_for` |
| Formula | `max_retries + targeted_max_retries + (1 if a fallback is available) + 3 (API-contract recovery) + best_of_n_candidates_tried` |
| Inputs | `max_retries = max(4, 1 + len(chain))` with a fallback chain, else 4; `TARGETED_MAX_RETRIES = 3`. Both are set in `workflow/development_loop.py` and are not configuration fields |
| Scope | One `GenerationState`, that is one unit invocation. It is not a run-wide bound (`BYP-A1-01`) |
| Persistence | None |
| Label | `TRACED` |

### AUTH-A1-08 - Run-wide generation time budget

| Field | AS-IS |
|---|---|
| Decision | When the run's generation deadline is reached |
| Current owner | `control/run_coordinator.py::claim_run_generation_clock` (the clock) and `autonomy.generation_time_budget_seconds` (the limit) |
| Scope | One process run: every unit, retry and role of the run reads the same start, held on the run lease in memory. A human wait is credited |
| Persistence | None. A resumed run is a new process with a new lease and a new clock |
| Configured value | Default `null`. The packaged comment names 3600 for the production profile; the scenario-04 demo template sets 2400; the other demo templates set 900 |
| UNKNOWN | Which value an actual desktop or production launch runs with was not established: `NEEDS_CHARACTERIZATION` (`GAP-A1-06`) |
| Label | `TRACED` |

### AUTH-A1-09 - What may a resumed unit reuse?

| Field | AS-IS |
|---|---|
| Decision | Which checkpointed artifacts a resumed `run_generation_workflow` call reuses |
| Current owner | `workflow/checkpoint.py::validate_resume_against_reality` and `workflow/resume_fingerprints.py::build_resume_plan` |
| Checkpoint writer | `workflow/workflow.py` (`_save_stage_checkpoint`) -> `control/checkpoint.py::save_checkpoint`. Written at four stages only: `plan`, `design`, `candidate_gates_passed`, `developer_success` |
| What a checkpoint holds | Stage, resume fingerprints, model routes, workspace / config / goal fingerprints, milestone and work-unit identity, validation baselines, pytest stability cache, and the stage's own artifacts (plan, design, architect files, final files, gate outcomes, effective obligation ledger) |
| What it does not hold (negative control) | Search of `control/checkpoint.py`, `workflow/checkpoint.py`, `workflow/subtask_checkpoint.py`, `workflow/resume_fingerprints.py`, `workflow/resume_support.py`, `workflow/work_unit_runner.py` and `control/*.py` for `retry_count`, `budgets`, `open_findings`, `attempt_number`, `seen_failure`, `no_progress`, `last_failure`: the only hit is the run record's `retry_counters` field (`AUTH-A1-06`), which is not a checkpoint and is never read back |
| No checkpoint inside the retry loop | A failed attempt writes none. A process that dies in attempt N resumes from `design` (or `plan`) with a new sandbox and a new `GenerationState`; the failing candidate and its finding are gone together |
| Evidence allowed | Fingerprints computed from the current workspace, config, goal, authority context, obligation ledger and candidate files |
| UNKNOWN behaviour | A missing or unavailable fingerprint is `UNVERIFIED` and invalidates like a change. A refusal returns the typed result `resume_refused`. An unreadable checkpoint means a fresh run with a warning |
| Model role | None |
| Human role | The operator asks for the resume (`resume` / `resume_id`); no auto-detection |
| Required single owner | Same for artifact reuse. Retry-state continuity has no owner: `GAP-A1-02` |
| Label | `TRACED`. Not exercised in A1 |

### AUTH-A1-10 - Which enforce subtasks are complete on resume?

| Field | AS-IS |
|---|---|
| Decision | Which subtasks of a structured plan are skipped by a resumed enforce run |
| Current owner | The resume block of `workflow/enforce_loop.py` (structured enforce), on `control/state.py::ControlState` |
| Persisted state | `subtask_states`, `subtask_written_files`, `current_plan_hash`, contract and approval digests, base commit, tree hash, workspace content hash, `subtask_completion_scope`, `tool_attempts` |
| Checks before trusting it | Requirement-contract digest, acceptance-approval digest, verification-contract digest, plan hash, completion scope, then `validate_resume_against_reality` on the workspace identity |
| Where completions live | `ControlState.subtask_completion_scope` is `candidate` while the plan runs in its sandbox and becomes `workspace` only after the terminal commit. A run that fails or is interrupted discards its completions (`_discard_sandbox_completions`: every `completed` becomes `pending`), and a resume refuses a state whose scope is not `workspace` (`COMPLETIONS_NOT_IN_WORKSPACE`). Production enforce runs always use a separate plan sandbox, so **a resumed production enforce run reuses no subtask unless the earlier run already committed the whole plan** (`MEASURED`: `tests/test_workflow_controller_enforce.py::test_enforce_resume_never_skips_sandbox_only_completions_after_a_crash` and `::test_enforce_resume_after_a_committed_sandbox_plan_reuses_it_and_changes_nothing`, passed on this baseline). Skipping of individual completed subtasks happens only when the plan ran in place (`::test_enforce_resume_skips_only_the_subtasks_already_completed`) |
| Unit behaviour | Only `completed` is skipped. Any other subtask is invoked again. **Correction of the A1 checkpoint:** A1 stated that the enforce loop passes no `resume` flag to a unit. That was wrong. `_invoke_bounded_subtask` forwards every caller keyword except `trace_id_override` (`**legacy_kwargs`), and `resume` / `resume_id` are among them (the same dictionary the resume block reads). The A1 negative control searched for the literal `resume=` and could not see a forwarded dictionary. A unit of a resumed enforce run therefore looks up the latest stage checkpoint of its workspace authority and reuses whatever its fingerprints still validate. What that reuses in a fresh plan sandbox is not characterized (`BYP-A1-12`) |
| Not persisted (negative control: the `ControlState` field list) | The owner-recovery cycle counter (`convergence_recovery_cycles`), the set of verification units that already reopened an owner, the captured pre-repair owners, the run's verifier semantic ledger (`verifier_semantic.VerifierSemanticLedger`, "memory only" by its own docstring), the deterministic-failure diagnostic store |
| UNKNOWN behaviour | Any digest or identity drift refuses subtask resume and runs the plan from subtask 1; an unreadable control state is the typed `ControlStateUnreadableError` |
| Human role | The operator asks for the resume |
| Required single owner | Same for completion. Recovery-state continuity: `GAP-A1-02` |
| Label | `TRACED`. Not exercised in A1 |

### AUTH-A1-11 - Enforce-level recovery bounds

| Field | AS-IS |
|---|---|
| Decision | How many times the enforce loop may re-invoke units for scope revision and owner recovery |
| Current owner | `workflow/enforce_loop.py`, with the constant `_MAX_PLAN_SCOPE_REVISION_ATTEMPTS = 3` from `workflow/workflow_controller.py` |
| Bounds found | Scope-revision loop: 3 passes per stage. Owner-recovery loop: 3 cycles per stage. Convergence cap: `len(plan.subtasks) * 3` owner-recovery cycles per run, shared with convergence re-entries. A repeated convergence state (plan hash, stages to re-enter, bytes of every planned file) stops typed (`ENFORCE_CONVERGENCE_NOT_REACHED`). One owner-reopen episode per verification-only unit, each eligible owner at most once inside it. One verifier-repair request per run. A recovery candidate byte-identical to the previous one for the same recovery plan, owner and files is not progress (`RECOVERY_NO_PROGRESS`) |
| A bound that cannot fire | The per-requirement cap (`prior_attempts >= 3` on the obligation ledger's history of the requirement id). The id contains the generation, and the generation of the (consumer, owner, files) key is advanced on every evaluation of the group, so each id is recorded at most once before the check and the count read is always 0. The committed test `test_enforce_bounds_recovery_attempts_per_downstream_owner_pair` says so in its docstring ("never itself hits its own per-id cap") and pins that the surrounding 3-cycle loop is the real bound (`TRACED` + `MEASURED`: passed on this baseline; probe P13). `GAP-A1-08` |
| Relation to unit budgets | Independent. Each re-invocation gets a full unit ceiling (`AUTH-A1-07`, `BYP-A1-01`) |
| Persistence | Memory only. The run's obligation ledger is also in memory (`workflow/obligations.py` states it is not checkpoint-persisted); only a unit's stage checkpoint carries a snapshot of the effective ledger for candidate reuse |
| Label | `TRACED`: the scope-revision loop, the owner-recovery loop, the consumer step, the finalization and the verification-owner episode were read line by line in A1b (`workflow/enforce_loop.py`, the body of `EnforceLoop.run` from the recovery-state declarations to the end of the verification-owner episode). `MEASURED` where a committed test is cited |

### AUTH-A1-12 - "The same failure" has four definitions

| Definition | Owner | Form | Used for |
|---|---|---|---|
| Failure family | `failure_grounding.build_failure_signature` | Normalised text, line numbers removed | Scoped allowance, repeat detection, live-lookup trigger, deterministic-failure diagnostic |
| Failure identity | `workflow/verifier_replay.py::failure_identity` | sha256 of type + exact gate output | Enforce-level comparison of a dependent's failure before and after an owner repair or a replay |
| Progress-vector failure dimension | `retry_progress.build_progress_vector` | Hash of the family signature | No-progress classification |
| Open finding | `open_findings.finding_from_failure` | A copy of the failure, no identity | The model-facing "still unresolved" block and carried source lines |

None of the four is declared the authority for blocker continuity. They can disagree: a one-line shift keeps the
family and changes the exact identity and the finding. `GAP-A1-01`. Label: `TRACED` + `MEASURED` (probe P2, P5).

## Slice A1b - additions (2026-11-15)

### AUTH-A1-13 - Does the allowance have one real authority?

| Field | AS-IS |
|---|---|
| Question | Who may give a blocker more attempts |
| Inside one unit invocation | **One grant path.** The only writer that replenishes `targeted_retry_count` or `fallback_targeted_attempted` is `retry_policy.reset_scoped_budgets_for_new_family`, called from one site (`retry_strategy._record_attempt_failure`) on one decision (`retry_policy.observe_failure_family`). The writer classification in `AUTH-A1-06` found no other grant. Best-of-N extends the ceiling by the candidates it discarded and does not touch the two scoped fields |
| What that one path decides on | Not on the blocker. On a text signature (`AUTH-A1-03`) gated by a predicate whose unknown default is "candidate finding" (`AUTH-A1-01`). So the grant has one owner and no authoritative input: `GAP-A1-01`, `GAP-A1-07` |
| Across unit invocations | **No authority.** A new `GenerationState` is a full new allowance, and it is created by whoever calls `run_generation_workflow`: the enforce loop (nine re-invocation roles besides the planned execution, `BYP-A1-01`), the milestone driver, the CLI, and an operator resume. None of them consults the attempts already spent on the same blocker, and no object carries them (`AUTH-A1-14`) |
| Answer | Single owner for the in-unit grant; no owner for the cross-invocation grant. The allowance does **not** have one real authority |
| Label | `TRACED` (negative-control search of every `budgets.` writer, 26 sites); `MEASURED` for the enforce re-invocation count (the committed test cited in `AUTH-A1-11`) |

### AUTH-A1-14 - Inventory of enforce-level recovery state

Every object below is a local of `EnforceLoop.run`: created per process run, never written to `ControlState`, the
approved-plan document or a checkpoint (`MEASURED` for `ControlState`: probe P14 lists its 24 fields and none names
retry or recovery state; `TRACED` for the rest).

| State | Decides | Lost on a new process |
|---|---|---|
| `convergence_recovery_cycles`, `convergence_signatures`, `convergence_reentry` | The run-wide recovery cap, the repeated-state stop, which completed stages must run again | Yes |
| `recovery_generation_by_key`, `recovery_plan_generation_by_subtask` | The requirement id and recovery-plan id of the next cycle; promotion of a `VERIFY` participant to `MUST_CHANGE` on recurrence | Yes |
| `recovery_candidate_fingerprints` | `RECOVERY_NO_PROGRESS` (an identical owner regeneration) | Yes |
| `verification_owner_reopened` | One owner-reopen episode per verification-only unit. The code comment states the bound is per process | Yes |
| `pre_repair_owners`, `verifier_repair_ineligible`, `verifier_repair_asked`, `replay_handles` | Whether a refused verifier candidate may be replayed and whether a person may be asked to authorize a verifier repair (at most once per run) | Yes: a person can be asked again |
| `verifier_semantic_ledger` | VALID decisions on generated verifiers and the one regeneration per subject (slice A2) | Yes |
| `advisory_review_records`, `dependent_verification_facts`, `owners_disproved_in_episode` | Context shown to a reopened owner; the owner's current result | Yes |
| `obligation_ledger` (run level) | Cross-owner requirements, `MUST_PRESERVE` of earlier corrections | Yes |
| `plan_recovery_events` | Nothing; it is the record of the episodes on the run result | Reported on the result only |

Persisted by the loop at each transition: stage states (`pending`, `in_progress`, `completed`, `needs_review`), the
written files of completed stages, the plan hash and digests. In production these are not reusable after a failure
(`AUTH-A1-10`), so losing the recovery state above changes nothing there: the whole plan runs again. It matters for
an in-place plan and for any future design that makes sandbox completions resumable.

## Slice A2 - semantic contract, generated verifier, attribution (2026-11-15)

Evidence: `evidence/a2/a2_probe.py` and its output (probes P1 to P10, model-free; the pytest identities are produced
by real pytest runs and parsed by Kriya's own JUnit parser), `evidence/a2/a2_tests_output.txt` (7 committed tests,
unchanged, passed on this baseline), and the code read recorded per row. Two questions are kept apart in every row:
**who decides about a verifier that already exists** (WS3, `AUTH-A2-06` to `AUTH-A2-09`) and **what canonical
product semantics exist before the Developer writes code or a test** (`AUTH-A2-01` to `AUTH-A2-05`).

| ID | Truth | Owner | Authoritative inputs | Explicitly not authority | Label |
|---|---|---|---|---|---|
| AUTH-A2-01 | What the user's goal requires, as statements | `workflow/requirements.py::derive_requirements` through `workflow/requirement_contract.py::requirement_set_for`: deterministic, the user's sentences verbatim as `REQ-n`. An operator contract (`--requirements`, `kriya.requirements/1`) replaces the derived set, never merges | The goal text; the operator contract file | Any model | `TRACED`; derivation `MEASURED` (P8 to P10) |
| AUTH-A2-02 | How each statement can be verified: claim kinds, strength, which deterministic closer and which authority | `workflow/contract_compilation.py::compile_verification_contract`, sealed by `seal_verification_contract` before the first model call (`kriya.verification_contract/1`, content-addressed; digest joins the resume identities and `ControlState`) | The requirement set; operator artifacts (acceptance file, approval, external authority bundle, dispositions); the goal's own example lines; repository facts | Any model. "LLM output authorizes nothing" (module docstring) | `TRACED`; entries `MEASURED` (P8 to P10) |
| AUTH-A2-03 | May a goal whose mandatory claim has no bound authority run at all? | `VerificationContract.refusal` decides the answer; **whether the answer is enforced** is `workflow/workflow.py::_requirement_policy_blocks` (`autonomy.requirement_unverified_policy == "block"`), read at `run_generation_workflow` and by the enforce loop (`enforce_loop.py`, `deps.load_run_contract`). Under `record` the contract is sealed with admission `VERIFICATION_AUTHORITY_REQUIRED` and the run proceeds | The contract; the configured policy (production seals `block`) | - | `TRACED` (both call sites); refusal object `MEASURED` (P8, P9). The production launch value is a configuration-authority fact for slice A5 |
| AUTH-A2-04 | Is a statement ambiguous? | `compile_verification_contract` sets `STATUS_AMBIGUOUS` in exactly two cases, both about mutation scope: path roles that cannot be determined (`requirements.py::mutation_path_roles`) and "other" with no referent. `requirements.py::behavior_strength` classifies a behaviour statement EXACT or GENERAL | The user's words, closed vocabularies | - | `TRACED`. **No owner represents semantic ambiguity inside a behaviour statement** (two readings of "a 4-byte time field"): GENERAL means "finite cases cannot prove it", not "it has more than one meaning". Negative control: search of `kriya/workflow` for `ambigu` found only the mutation-scope sites, locator ambiguity and regression replay |
| AUTH-A2-05 | The concrete product semantics of a GENERAL behaviour statement (values, ranges, widths, rounding, edge cases) | **No owner.** The sealed entry holds the sentence, its claim kinds, strength and closers (`MEASURED`, P8/P9: field list of a sealed entry); nothing structured about what the product must do. Semantics exist before generation only when the operator supplied them: an acceptance file (B2), an approved suite (B3), an external authority bundle, example lines in the goal, or one of the two anchored grammars (`runtime_stdout`, `file_creation`; P10) | Operator artifacts; the user's literal examples | - | `MEASURED` + `TRACED`. Where none exists and the run is admitted, the first concrete semantics are written by models: the Planner's acceptance criteria (Planner JSON, validated only structurally: ids resolve, `plan_validation.py`), the Developer's production code and the Developer's test. `GAP-A2-01` |
| AUTH-A2-06 | Which run-authored sources are generated-verifier subjects | `workflow/verifier_semantic.py::derive_executed_verifiers`: a COMPLETE structured test report, an executed identity equal to the `lookup_key` of a callable the structural parser finds in a file, and that file in the unit's write journal. One subject per file. No file name is consulted | The gate's own report dict; `code_intel.service.CandidateOverlay`; `state.all_files_written` | File names, directories, the test-source compile phase (owner correction 2026-11-14) | `TRACED`; `MEASURED` (P1 to P4, P6 map; **P5 and P7 do not map and raise no unknown**: `GAP-A2-02`) |
| AUTH-A2-07 | Does a verifier's expectation have semantic authority? | A person, asked through `policy/approval.py::ask_verifier_semantic` (typed callback only; an auto-approving caller is never asked). State: `verifier_semantic.py::VerifierSemanticLedger` (memory, one per enforce run; a direct run gets its own). An `expectation_valid` decision is keyed by `VerifierSemanticSubject.semantic_authority_fingerprint` (run, plan hash, unit, verifier path and bytes, goal digest, contract digest, the unit's plan criteria and invariants by id and text). The review **event** is `fingerprint`, which adds the review phase and the failure identity | The person's decision bound to the subject as it is after the wait (`verifier_semantic_matches`) | A passing or failing run of the verifier; any model text; the Reviewer | `TRACED`; pinned by `tests/test_verifier_semantic_authority_001.py` (not re-run). The person is shown the goal text as the "canonical contract" (`contract_text=self.goal`): the decision is the first semantic authority and it arrives after generation, per verifier file |
| AUTH-A2-08 | What a decision permits | `VerifierSemanticLedger.after_decision`: valid = these bytes are write-denied and the unit proceeds; invalid = ONE regeneration of exactly that path per subject (`policy/approval.py::VerifierSemanticAllowance`, keyed run + unit + path), only where the unit has a validated plan and an allow-list scope (`UnitSemantics.regenerable`); cannot-decide, no decision, stale decision, unattended = typed stop `verifier_semantic_authority_required` | - | A valid decision never says which code is wrong and authorizes no change | `TRACED`; `MEASURED` for unattended and direct (7 tests) |
| AUTH-A2-09 | Which production owner may be repaired after an accepted verifier fails | `verifier_semantic.py::production_owner_authority`: executed failure + a locator that `failure_grounding.py::resolve_unique_locator_artifacts` resolves to a unique non-verifier workspace member + exactly one owner in the validated plan. The ruling replaces model attribution (`retry_strategy.py::_semantic_attribution`, tier `locator`) | The gate's locators; plan ownership | Model diagnosis, REDIRECT, triage, a shared basename | `TRACED`; `MEASURED` (test `test_a_ruled_failure_is_never_handed_to_failure_attribution`). A direct run has no plan, so no owner is ever selected there |
| AUTH-A2-10 | Which file a failure is about, when no generated-verifier subject is involved | `workflow/attribution.py::attribute_failure`, in this order: authoritative structural files; the model's typed REDIRECT of the previous answer (`self_diagnosis`, confidence "high", ranked **above** the locator on a confirmed repeat); preserved locator; test-fixture precondition; locator; grounded architectural owner; subtask scope; judge evidence; model triage (`_tier_triage`, a model call when more than one file is known); else `full_set` | Per tier | - | `TRACED`. `DETERMINISTIC_ATTRIBUTION_TIERS` (5 tiers) is the one set that gates the plan-scope escalation and the `NO_AUTHORIZED_REPAIR_TARGET` stop (closed row `ATTRIBUTION-MODEL-CLAIM-HARD-STOP-001`). A model tier still chooses the **repair targets inside the unit's own scope** |
| AUTH-A2-11 | Whose defect a failure is (the kind label) | `workflow/failure.py::classify_failure_attribution`, by failure type alone; any type it does not list is `SOURCE_DEFECT` (so `internal_framework_error` is). `retry_strategy.py` then flips `TEST_DEFECT` to `SOURCE_DEFECT` whenever the attributed files include a non-test file, whatever the tier | Failure type | - | `TRACED`. Negative control (search of `kriya/` for `attribution_kind`): readers are `Failure.to_gate_outcome` and `core/attempt_evidence/scope.py` only. The label is diagnostic; the routing uses the kind **before** the flip |
| AUTH-A2-12 | Source approval versus semantic approval | Separate review kinds with separate vocabularies: `policy/approval.py::DECISIONS_BY_KIND` (`change_approval` approve/reject; `verifier_semantic_review` valid/invalid/cannot-decide; a word of another kind is refused) | - | Approving the source of a test is not accepting its expectation, and the reverse | `TRACED`. Candidate and approval binding belong to slice A3 |
| AUTH-A2-13 | Replay and repair of a verifier after an owner recovery | Replay validity: `workflow/verifier_replay.py::ReplayHandle.refusal` (run, plan hash, unit, goal digest, contract digest, path set, bytes). Repair of a replayed verifier: one person's authorization per run (`VerifierRepairSubject`), one Developer answer, and the staged V1 must be rejected by a deterministic gate against the owner's pre-repair bytes (`verifier_repair.py::verify_against_pre_repair_owner`) | Existing canonical identities; the person | A replay that fails is evidence against the owner's repair, never a licence to rewrite the verifier | `TRACED` (module contracts and symbols; the enforce call sites were read in A1b, `AUTH-A1-14`). Both states are memory only |

### A2 answers to the slice questions (each cites its row)

1. **Goal semantic contract authority.** Statements: `AUTH-A2-01`. Verifiability: `AUTH-A2-02`. Product meaning of a
   general statement: none (`AUTH-A2-05`, `GAP-A2-01`).
2. **Ambiguity.** Represented only for mutation scope (`AUTH-A2-04`).
3. **Planner criteria and invariants.** Model text, structurally validated. They are bound into the semantic subject
   by id and exact text and shown to the reviewer as plan-declared (`AUTH-A2-07`); they are never a closer. Their
   rendering to the Developer and their validation rules belong to slice A4.
4. **Verifier specification authority.** None before generation. After generation: a person, per verifier file
   (`AUTH-A2-07`).
5. **Semantic-authority fingerprint versus review-event fingerprint.** `AUTH-A2-07`.
6. **Verifier PASS / FAIL evidence.** Neither is truth about the contract until a person decides; unattended, both
   end needs-review (`AUTH-A2-08`, `FR-A2-01`, `FR-A2-06`).
7. **Test-source compile phase.** Establishes no subject; the policy owner has no phase rule (`AUTH-A2-06`,
   `BYP-A2-03`).
8. **Production versus verifier defect ownership; locator identity; owner selection.** `AUTH-A2-09`.
9. **Python limitation.** Python and Java are the only languages with a structural grammar
   (`code_intel/parsing.py`, `_LANGUAGE_BY_EXTENSION`); within Python a `src/` layout breaks the identity mapping
   (`GAP-A2-02`).
10. **Unattended, direct and non-enforce limits.** `AUTH-A2-08`, `GAP-A2-03`.
11. **Replay / O0, full-set regeneration refusal, side routes.** `AUTH-A2-13`, `FR-A2-05`, `BYP-A2-06` to `BYP-A2-08`.
