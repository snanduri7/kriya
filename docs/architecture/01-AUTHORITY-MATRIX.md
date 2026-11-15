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

## Slice A3 - candidate, approval, apply, replay, final result (2026-11-15)

From this slice on every row carries the full field set of the owner's documentation addendum (2026-11-15). A field
whose fact lives in another map cites that row instead of restating it. Paths are under `kriya/`. Three executors
appear: **direct** (one unit, `workflow/plan_executor.py`), **milestone** (several units through the same executor,
each committing to the real workspace) and **enforce** (`workflow/enforce_loop.py`: units commit into one plan
sandbox, one terminal commit to the real workspace). "Unit" means one `run_generation_workflow` invocation.

Evidence: probes P1 to P8 are `evidence/a3/a3_probe.py` and `a3_probe_result_truth.py` with their output files; the
two committed tests are in `evidence/a3/a3_tests_output.txt`.

### AUTH-A3-01 - What bytes are this attempt's candidate?

| Field | AS-IS |
|---|---|
| Truth decided | Which files, with which exact bytes, the attempt staged in its sandbox |
| Owner | `workflow/candidate_transaction.py::CandidateTransaction` (phases `check_operation_contracts`, `capture_originals`, `stage`, `commit`), writing through `policy/filesystem.py::AuthorizedFileWriter.commit_batch` |
| Why this owner | It is the only path by which Developer output, a resumed candidate (`attempt.py::run_attempt`, `reusing_candidate`) and a replayed verifier (`replaying_verifier`) reach the sandbox; all three use the same write path |
| Authoritative inputs | The parsed Developer response (file proposals), the attempt's write scope (`AttemptContext.write_scope_mode`, `allowed_write_relpaths`, `protected_relpath`), the captured originals |
| Evidence allowed | The staged bytes themselves; each file's raw digest is recorded in `RunState.candidate_digests` at commit (`_commit_staged_writes`) |
| Evidence forbidden | Model prose about what it wrote; a path the response names but the stage did not prepare |
| Model role | Candidate generator |
| Human role | None at this point |
| Persisted state | Sandbox files (a git worktree outside the workspace); `candidate_digests`, `all_files_written`, `identical_rewrites`, `all_original_raw` in memory. Nothing durable until a checkpoint (`MUT-A3-06`) |
| State transitions | `SM-A3-01`: PROPOSED to STAGED or REFUSED |
| Downstream consumers | Every gate (`gate_orchestrator.py`), the tree binding (`file_integrity.py::VerificationTreeBinding`), the verified binding (`AUTH-A3-02`), the approval diff (`AUTH-A3-03`) |
| Mutations authorized | `MUT-A3-01` (sandbox only) |
| Mutations forbidden | Any real-workspace byte; any path outside the write scope (`PolicyDeniedError` before the first write of the batch) |
| Failure / UNKNOWN | The first refusal in any phase refuses the whole attempt: nothing staged, `decision = "refused"`, typed reason from `_refusal_reason`. A byte-identical rewrite is not a change: the path is removed from `all_files_written` and recorded in `identical_rewrites` |
| Retry / recovery | A refusal is an attempt failure routed by `retry_strategy.py` (`FR-A1` rows) |
| Checkpoint / resume | A resumed candidate is rebuilt from the checkpoint's `final_files` through this same transaction |
| Audit emitted | `candidate.change` attempt-evidence record (`gate_orchestrator.py::_record_candidate_change`); `validation.invalidated` event |
| Alternate paths | `BYP-A3-10` (gate-side candidate mutations: deterministic pom corrections and the self-correction loops write sandbox files after the transaction) |
| Invariant | Nothing reaches the sandbox until every proposal of the response passed its checks; the batch re-checks revisions and rolls back on an interrupted write |
| Negative control | Writers of `candidate_digests` (search `candidate_digests[`): `_commit_staged_writes` and `gate_orchestrator.py` (the pom correction, line `state.candidate_digests["pom.xml"] = ...`). No third writer |
| AS-IS evidence | `TRACED` (`candidate_transaction.py` lines 1396-1598) |
| Required contract | Same owner. Open point: `GAP-A3-04` (identity) |
| Limitation | Self-correction's own bookkeeping of the files it writes was not traced in A3 (`BYP-A3-10`) |

### AUTH-A3-02 - Is the batch being committed the batch that was verified?

| Field | AS-IS |
|---|---|
| Truth decided | Equality of the commit batch with the batch bound when acceptance started |
| Owner | `workflow/verification_binding.py::bind_candidate` (the identity) and `binding_refusal` (the decision), consulted first thing inside `workflow/terminal_commit.py::commit_terminal_candidate` |
| Why this owner | It sits inside the one real-workspace transaction, so both callers (direct/milestone apply and enforce commit) get it and neither can skip it |
| Authoritative inputs | The materialized batch (`terminal_commit.py::materialize_candidate`): per file the workspace-relative path, operation, sha256 of the exact bytes or a deletion marker, mode, expected base revision, expected base existence |
| Evidence allowed | Bytes read from the sandbox at bind time and again at commit time |
| Evidence forbidden | Any stored digest, name or earlier report: the commit recomputes from the bytes it is about to write |
| Model role | None |
| Human role | None |
| Persisted state | **Memory only.** Direct/unit: `RunState.verified_candidate_binding`, set at the start of `development_loop.py::accept_candidate`, cleared by `attempt_lifecycle.py::begin_attempt`. Enforce: `TerminalGateReport.verified_candidate`, set by `TerminalGateService._bind_candidate` before the first terminal gate |
| State transitions | `SM-A3-01`: BOUND; a mismatch moves the commit to REFUSED (`SM-A3-03`) |
| Downstream consumers | The commit guard; the post-review guard of `AUTH-A3-05`; `verifier_replay.py` (reads the binding of a refused candidate) |
| Mutations authorized | None by itself; it is a precondition of `MUT-A3-03` and `MUT-A3-04` |
| Mutations forbidden | A commit with no binding (`VERIFIED_CANDIDATE_EVIDENCE_MISSING`) or a different one (`VERIFIED_CANDIDATE_EVIDENCE_STALE`): nothing is written |
| Failure / UNKNOWN | A batch that cannot be materialized has no binding (`None`), so the commit refuses. Fail closed |
| Retry / recovery | The refusal is a `workspace_commit` failure, a deterministic stop, never a Developer retry (`FR-A3-12`) |
| Checkpoint / resume | Not checkpointed. A resumed run binds again after rebuilding the candidate |
| Audit emitted | On refusal: `workspace_commit.failed` event with the reason code. On success: none; the binding digest is written nowhere (`GAP-A3-04`) |
| Alternate paths | `commit_service.py::commit_verified_candidate` returns `completed=True` without calling the commit when `candidate_root == workspace_path` (`BYP-A3-07`) |
| Invariant | The real workspace receives only bytes equal to those bound before approval and terminal verification |
| Negative control | `MEASURED` P6: same batch passes, a changed byte refuses `..._STALE`, no binding refuses `..._MISSING`. Callers of `commit_terminal_candidate`: `stage_apply.py::apply_terminal_candidate` and `commit_service.py::commit_verified_candidate` only |
| AS-IS evidence | `TRACED` + `MEASURED` (P6) |
| Required contract | Same owner; the digest should be recorded (Phase C, `GAP-A3-04`) |
| Limitation | The binding covers the commit batch only: `all_files_written` on the direct path, the plan's planned files on the enforce path. A sandbox file outside that set is neither bound nor committed |

### AUTH-A3-03 - Must a person approve this source change?

| Field | AS-IS |
|---|---|
| Truth decided | `need_human_approval` for one unit's candidate |
| Owner | `workflow/stage_apply.py::approval_gate` |
| Why this owner | It is the single pre-apply gate of a unit, called once from `accept_candidate` |
| Authoritative inputs | `state.all_files_written` (no files means no approval), `autonomy.mode` through `config/authority.py::is_human_in_the_loop`, `autonomy.sensitive_paths` (regex on each written path), `autonomy.risk_threshold_lines` against the unit's unified-diff line count (default 100), `process_profiles.enabled` and `enforce_approval` with the route's `human_review_required` |
| Evidence allowed | The diff computed here from the sandbox files and the captured originals; the configuration values above |
| Evidence forbidden | Reviewer text, ownership findings and the static-analysis banner: shown to the approver, never an input of the decision (comments at the call sites: "they never decide the approval themselves") |
| Model role | None in the decision. Diagnostic for the approver: the pre-approval review (`AUTH-A3-12`) |
| Human role | None in deciding whether approval is needed; the configuration is the operator's |
| Persisted state | The four triggers are written into the `approval.decision` event (`AUTH-A3-13`) |
| State transitions | `SM-A3-01`: to AWAITING_APPROVAL, or straight on when no trigger fires |
| Downstream consumers | The held-gate step (`AUTH-A3-05`), which requires that approval was actually asked |
| Mutations authorized | None |
| Mutations forbidden | Apply without approval when a trigger fired |
| Failure / UNKNOWN | An autonomy mode outside `guardrails` / `human-in-the-loop` raises a typed error (`MEASURED` P7). An invalid `sensitive_paths` regex is logged and **not enforced** (the loop continues): fail open for that one pattern |
| Retry / recovery | None |
| Checkpoint / resume | Recomputed on resume; a resumed candidate is asked about again (`SM-A3-01`) |
| Audit emitted | `approval.decision` (`AUTH-A3-13`); the audit-only approval-rule check `audit_approval_rules` |
| Alternate paths | `BYP-A3-01` (no plan-level approval under enforce), `BYP-A3-02` (the size trigger is per unit), `BYP-A3-05` (an empty change set), `BYP-A3-06` (outside human-in-the-loop the gates execute before the approval) |
| Invariant | A unit that wrote source in human-in-the-loop mode is never applied without an approval outcome of APPROVED |
| Negative control | Callers of `approval_gate`: `development_loop.py::accept_candidate` only (search `approval_gate(`) |
| AS-IS evidence | `TRACED` (`stage_apply.py` lines 178-464) |
| Required contract | Open: whether the size trigger and the approval subject should be plan-wide under enforce (`GAP-A3-05`, owner policy) |
| Limitation | The invalid-regex fail-open was read, not executed |

### AUTH-A3-04 - What did the person decide?

| Field | AS-IS |
|---|---|
| Truth decided | APPROVED, DECLINED or UNAVAILABLE, with a reason code from the closed table `policy/approval.py::APPROVAL_REASON_CODES` |
| Owner | `policy/approval.py::resolve_approval` with `classify_approval_answer` |
| Why this owner | One classification for every approval site; `ApprovalOutcome` has no truth value, so no site can read an answer through Python truthiness |
| Authoritative inputs | The callback's resolved answer: an `ApprovalOutcome`, or a real `bool` |
| Evidence allowed | Terminal: an explicit `y`/`yes` or `n`/`no` (`cli.py::_terminal_answer`). Desktop launch: a `decision` frame over the launch's private review channel that carries the review identity and the one-shot capability (`control/review_channel.py`, `control/launch_approval.py::_wait`) |
| Evidence forbidden | Anything else: `None`, a number, a string, a mock, an unresolved awaitable are UNAVAILABLE (`MEASURED` P7). The persisted decision file is audit output and is never read as authority. End of file, a bare Enter, Ctrl-C, an expired review or a vanished host are UNAVAILABLE, never a decline |
| Model role | None |
| Human role | Authority. `--yes` outside a launch is the operator's standing approval (`BYP-A3-03`); inside a launch `--yes` cannot take effect |
| Persisted state | Desktop: the launch record's `pending_approval` (status, decision, `candidate_digest` = digest of the presented diffs, per-file diff digests), written **before** the workflow continues; the review-diff sidecar; the decision file (`MUT-A3-07`). Terminal: only the `approval.decision` event |
| State transitions | `SM-A3-02` |
| Downstream consumers | `approval_gate` (continue, or `_abort_without_applying`) |
| Mutations authorized | APPROVED: the unit may go on to its remaining gates and its apply. It authorizes no other bytes |
| Mutations forbidden | DECLINED and UNAVAILABLE: nothing applied; the sandbox is removed |
| Failure / UNKNOWN | UNAVAILABLE ends the unit `approval_required`; DECLINED ends it `human_rejected`. A decision that cannot be made durable is `APPROVAL_DECISION_NOT_RECORDED`: not honoured |
| Retry / recovery | Neither is retried. The checkpoint is deleted (`_abort_without_applying`) |
| Checkpoint / resume | A decision is never restored from a checkpoint; a resumed run asks again |
| Audit emitted | `AUTH-A3-13` |
| Alternate paths | `BYP-A3-03`, `BYP-A3-04` (no callback), `BYP-A3-11` (a desktop launch cannot be asked anything but a change, a runtime command or a verifier review) |
| Invariant | Only an explicit, attributable approval approves |
| Negative control | `MEASURED` P7 (six answers). The channel's refusal of forged, replayed and cross-review decisions is pinned by committed tests (not re-run) |
| AS-IS evidence | `TRACED` + `MEASURED` (P7) |
| Required contract | Same |
| Limitation | Same-user threat model: the channel defends against a workload that can rewrite files, not against ptrace or account compromise (its own stated claim) |

### AUTH-A3-05 - Is the candidate still the one the person approved (held execution)?

| Field | AS-IS |
|---|---|
| Truth decided | Whether a source approval still covers the bytes about to be built, tested or run |
| Owner | `policy/approval.py::stale`, fed by the guard `development_loop.py::accept_candidate` installs (`_candidate_is_the_reviewed_one`), raised as a typed failure by `attempt.py::assert_candidate_unchanged_since_source_review` |
| Why this owner | One predicate for every execution boundary after a source review |
| Authoritative inputs | The binding of `AUTH-A3-02` taken before the review, and a binding recomputed from the sandbox at each check |
| Evidence allowed | Recomputed bytes only |
| Evidence forbidden | The diff digest shown to the person; any cached state |
| Model role | None |
| Human role | None |
| Persisted state | None (`AttemptContext.post_source_review_guard`, memory) |
| State transitions | `SM-A3-01`: HELD_GATES to FAILED (stale) |
| Downstream consumers | `gate_orchestrator.py` (before compile, before the runtime-command review), `runtime_verification.py` (before Maven preparation and before each command), `accept_candidate` (before the held gates, after them, after the requirement closers) |
| Mutations authorized | None |
| Mutations forbidden | Execution of, and any apply of, a candidate that differs from the reviewed one. While the guard is set the three self-correction loops are not entered (`gate_orchestrator.py` and `runtime_verification.py`: `ctx.post_source_review_guard is None` in each loop condition) |
| Failure / UNKNOWN | Stale: `verification_infrastructure_failure` "CANDIDATE_CHANGED_AFTER_SOURCE_REVIEW". A binding that cannot be computed is stale (the guard returns False) |
| Retry / recovery | The retry loop is the only way to a new candidate, which is reviewed again |
| Checkpoint / resume | Not persisted; `begin_attempt` clears it |
| Audit emitted | The gate outcome of the failure |
| Alternate paths | `BYP-A3-09` (an attempt of an attended run that wrote no source is not held) |
| Invariant | In an attended human-in-the-loop run, no gate executes model-authored bytes other than the ones a person approved |
| Negative control | Readers of `post_source_review_guard`: the three loop conditions and `assert_candidate_unchanged_since_source_review`. Writers: `accept_candidate` (set, cleared in `finally`) and `begin_attempt`. `MEASURED` P7 for `stale` |
| AS-IS evidence | `TRACED`; `MEASURED` (P7) for the predicate. This settles `BYP-A2-07` for every attempt that wrote source |
| Required contract | Same |
| Limitation | `BYP-A3-09` |

### AUTH-A3-06 - Is execution held until the source review?

| Field | AS-IS |
|---|---|
| Truth decided | Whether this attempt's executing gates (POM validation, compile, tests, runtime verification) and the requirement closers that run tests wait for the approval |
| Owner | `policy/approval.py::defer_execution_until_source_review` |
| Inputs | The caller's flag (always True from `development_loop.py`), `autonomy.mode`, whether the run has written source |
| Rule | Held only in human-in-the-loop mode and only when source was written (`MEASURED` P7: `guardrails` is not held; no source is not held). Held even with no approver: the run then ends `approval_required` having executed nothing |
| Model / human role | None / none (owner decision of 2026-10-15 recorded in the docstring) |
| Persisted state | None (`AttemptContext.deferred_executing_gates`, a partial bound to this attempt; cleared by `run_attempt` and `begin_attempt`) |
| Consumers | `attempt.py::run_attempt` (hold or run), `accept_candidate` (runs the held half after approval; refuses `EXECUTION_DEFERRED_WITHOUT_SOURCE_REVIEW` when the gates were held but no review was asked) |
| Failure / UNKNOWN | An unreadable mode raises; nothing is treated as not held by default |
| Checkpoint / resume | The `candidate_gates_passed` checkpoint is written only after the held half ran, so a resume can never skip an unrun gate. A crash during the review leaves no candidate checkpoint (`FR-A3-20`) |
| Audit emitted | The attempt's `attempt.closed` evidence record is written when `run_attempt` returns, before the held gates run (`GAP-A3-07`) |
| Alternate paths | `BYP-A3-06` |
| Invariant | See `AUTH-A3-05` |
| AS-IS evidence | `TRACED` + `MEASURED` (P7) |
| Required contract | Open owner decision: whether the other approval triggers should hold execution too (`HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`) |

### AUTH-A3-07 - Did the candidate pass terminal regression (direct, milestone and enforce unit)?

| Field | AS-IS |
|---|---|
| Truth decided | Whether the full suite on the still-isolated candidate blocks acceptance |
| Owner | `development_loop.py::run_terminal_regression`; attribution of a failing suite to the candidate by `workflow/suite_attribution.py::attribute_suite_result` when a baseline was captured |
| Inputs | The full-suite result from `tools/validate.py::PolymorphicValidator.run_tests` on the sandbox; the captured brownfield baselines; the stability cache |
| Evidence allowed | Validator output; the PRE/POST delta; an isolated pristine replay for ambiguous tests |
| Evidence forbidden | Model text |
| Model / human role | None / none |
| Persisted state | `RunState.terminal_regression_succeeded`, `terminal_full_suite_result` (memory); kept for reuse after apply (`MUT-A3-10`) |
| Consumers | The generated-verifier pass seams (`AUTH-A2-08`), the `developer_success` checkpoint, the apply, `final_workflow_quality_passed` (`AUTH-A3-10`) |
| Failure / UNKNOWN | Blocking with an attributable test: a retryable `regression_test` failure. Blocking with nothing attributable: the deterministic stop `REGRESSION_UNATTRIBUTED`. An environment result stops the run (`_stop_on_environment_gate_result`) |
| Checkpoint / resume | Always re-run on resume (the comment in `run_attempt`: "terminal regression always runs") |
| Audit emitted | `validation_baseline.*` events (advisory authority), the gate outcome |
| Alternate paths | Under enforce the plan-level commit has no full-suite gate of its own: each unit ran its own; the terminal gates are the eight of `AUTH-A3-08` |
| Invariant | Nothing is applied by a unit whose terminal regression blocked |
| AS-IS evidence | `TRACED` (lines 1220-1479 read; the tail of the function was not read line by line) |
| Required contract | Same |

### AUTH-A3-08 - May the enforce candidate be committed?

| Field | AS-IS |
|---|---|
| Truth decided | `TerminalGateReport.commit_eligible` |
| Owner | `workflow/terminal_gate_service.py::TerminalGateService.run` |
| Rule | The gates ran and none of eight gaps is set: migration, stack contract, preserved references, static analysis, semantic safety, terminal obligations, original requirements, artifact registry |
| Inputs | The plan sandbox, the plan, the obligation ledger, the requirement set, the autonomy configuration, the sealed verification contract, the operator's acceptance artifacts |
| Evidence allowed | Deterministic checks on the candidate; for original requirements the verifier's verdicts and the deterministic closers, then the requirement policy (`AUTH-A3-09`) |
| Evidence forbidden | The plan's own acceptance text (docstring of `_requirement_gap`); a raised exception is never reported as an unmet requirement |
| Model role | Diagnostic: the spec-compliance verifier's verdicts are recorded, and a model "satisfied" never closes a requirement (`requirements.py::requirement_outcomes`) |
| Human role | None at this gate (acceptance artifacts and dispositions were bound before generation) |
| Persisted state | Memory; each gate's result goes to attempt evidence (`_record_terminal_report`) and to `terminal_gate_outcome` events |
| Consumers | `commit_service.py::commit_verified_candidate` (raises if not eligible), the enforce aggregate |
| Mutations authorized | `MUT-A3-04` |
| Mutations forbidden | A commit of an ineligible candidate |
| Failure / UNKNOWN | A gate validator that raises is a failed gate for static analysis and terminal obligations; an unexpected exception elsewhere propagates to the controller's error handler. `requirements_verified` is reported and **does not enter `commit_eligible`** (`MEASURED` P4) |
| Retry / recovery | **None.** A gap ends the run; no unit is reopened (`FR-A3-15`, `GAP-A3-02`) |
| Checkpoint / resume | Not checkpointed; sandbox completions are discarded when the gates fail (`MUT-A3-11`) |
| Audit emitted | `gate.result` per terminal gate with `commit_eligible`; `requirements_verified` on the requirements gate |
| Alternate paths | `terminal_obligations` and `preserved_references` exist only under enforce (documented in `generation-pipeline.md`) |
| Invariant | The real workspace is untouched until every terminal gate passed |
| Negative control | Constructors of a `TerminalGateReport(ran=True, ...)`: the service only |
| AS-IS evidence | `TRACED` + `MEASURED` (P4) |
| Required contract | Open: `GAP-A3-01`, `GAP-A3-02` |

### AUTH-A3-09 - Requirement outcome, requirement truth, and what blocks

| Field | AS-IS |
|---|---|
| Truth decided | (a) each original requirement's outcome; (b) the run's typed truth `ALL_VERIFIED` / `RESOLVED_WITH_OPERATOR_ACCEPTANCE` / `OPEN_REQUIREMENTS` / `CONTRADICTED`; (c) which requirements forbid success |
| Owner | (a) `workflow/requirements.py::requirement_outcomes`; (b) `requirement_truth`, derived only from (a); (c) `blocking_requirements` |
| Why this owner | One derivation from the obligation ledger; every reporter calls it |
| Inputs | Ledger records: the verifier's verdict, deterministic closures bound to the candidate's evidence id, counter-evidence, operator dispositions, non-claim records; the two policies `autonomy.requirement_unknown_policy` and `requirement_unverified_policy` (default `record`; the production profile sets `block`, `config/resolution.py`) |
| Evidence allowed | Deterministic closure for the exact candidate; operator acceptance (counted as accepted, never as verified) |
| Evidence forbidden | A model "satisfied" without deterministic closure (downgraded to UNVERIFIED); a model "violated" without counter-evidence (downgraded to UNVERIFIED) |
| Model role | Diagnostic. One exception that blocks: a model "missing" on an UNVERIFIED requirement blocks under either policy (`_model_reported_missing`) |
| Human role | Operator acceptance and dispositions, bound before generation |
| Persisted state | The ledger (memory; snapshotted in checkpoints as `EFFECTIVE_LEDGER_KEY`); the result field `requirement_truth`; the `requirement.truth` run event; the launch record copy |
| Consumers | Unit: `development_loop.py::_close_and_check_requirements`. Enforce: `TerminalGateService._requirement_gap`. Reporters: `outcome.py::completed_result`, the enforce aggregate, `control/launch.py::_requirement_truth_field`, `application/history.py` (the last `requirement.truth` event) |
| Mutations authorized | None. A blocking requirement stops the unit or the terminal gate |
| Mutations forbidden | None under `record`: **an open requirement does not stop the apply** |
| Failure / UNKNOWN | VIOLATED always blocks. PENDING and UNKNOWN block only under `unknown_policy = block`; UNVERIFIED only under `unverified_policy = block` or a model "missing" (`MEASURED` P4). Under the defaults UNKNOWN **fails open** |
| Retry / recovery | A unit-level block is the deterministic stop `requirements_unresolved` (`retry_strategy.py`: "The Developer cannot supply the verifier's missing verdict"). A terminal block ends the run (`FR-A3-15`) |
| Checkpoint / resume | The ledger snapshot is restored on resume |
| Audit emitted | `requirement.closure` and `requirement.truth` events (authoritative); `closure_attempts` in the enforce result |
| Alternate paths | `BYP-A3-08` (the last `requirement.truth` event can describe a candidate that was not the applied one) |
| Invariant | Truth has one derivation; it never changes the lifecycle status (comment at the enforce aggregate) |
| Negative control | Callers of `requirement_truth(`: `development_loop.py`, `outcome.py`, `enforce_loop.py` (through `deps`), `shadow_mode.py` copies the result field into an event. No reader recomputes outcomes |
| AS-IS evidence | `TRACED` + `MEASURED` (P4, P8, the two committed tests) |
| Required contract | Open: `GAP-A3-01` |

### AUTH-A3-10 - Did the unit succeed?

| Field | AS-IS |
|---|---|
| Truth decided | A unit result's `quality_gates_passed` and `failure_category` |
| Owner | `workflow/state.py::RunState.final_workflow_quality_passed`; the category by `workflow/outcome.py::terminal_failure_category`; the result by `outcome.py::completed_result` |
| Rule | All of: candidate gates succeeded, terminal regression succeeded, the attempt succeeded, quality gates succeeded (set only by `apply_terminal_candidate` after a committed apply), no open API-contract recovery, **and no final-review refusal** |
| Inputs | Terminal run state only ("never historical-attempt truth") |
| Evidence allowed | The flags the gates and the apply set |
| Evidence forbidden | The Reviewer's verdict text: it is returned as `review` and decides nothing. Only the fact that the review request could not be performed enters the rule |
| Model role | None in the rule. The Reviewer is diagnostic (`AUTH-A3-12`) |
| Human role | None here |
| Persisted state | The result dict; the trace row (`status` success / failure); the launch record's `ended` block (a copy) |
| Consumers | `plan_executor.py::_passed` (direct and milestone), `enforce_loop.py::enforce_unit_gates_accepted` (enforce), the CLI exit code |
| Failure / UNKNOWN | A unit whose candidate **was applied** and whose final review could not be performed reports `quality_gates_passed = False`, `failure_category = final_review_refused`, `final_review_refusal.candidate_applied = True`, nothing rolled back (`MEASURED` P1) |
| Retry / recovery | The checkpoint is deleted when the unit passed, or when it was applied and only the review was refused |
| Audit emitted | The trace row; `attempt.passed` with `applied: True`; `review.refused` |
| Alternate paths | `BYP-A3-12`: the same unit result is a failure for the direct and milestone executor and an acceptance for the enforce loop (`MEASURED` P2) |
| Invariant | `quality_gates_passed = True` implies the candidate was committed to the unit's `workspace_path`. It does **not** imply requirements verified (`AUTH-A3-09`) |
| Negative control | Writers of `quality_gates_succeeded = True`: `stage_apply.py::apply_terminal_candidate` (the verification-only path was not searched in A3) |
| AS-IS evidence | `TRACED` + `MEASURED` (P1, P2, P8) |
| Required contract | Open: `GAP-A3-01`, `GAP-A3-03` |

### AUTH-A3-11 - Did the run succeed (plan level)?

| Field | AS-IS |
|---|---|
| Truth decided | The run's terminal status |
| Owner | Enforce: the tail of `enforce_loop.py` (`aggregated["status"]`). Direct and milestone: `plan_executor.py::_terminal` over the driver's result (`milestones.py::_MilestonePlanDriver.plan_result` / `unit_failed`) |
| Rule, enforce | `success` when every subtask completed, the terminal gates are commit-eligible and the commit completed; `needs_review` when a subtask is NEEDS_REVIEW, the artifact derivation failed or the commit failed; otherwise `failed` |
| Rule, direct / milestone | Success only when every unit is VERIFIED; a result that claims success otherwise is turned into `needs_review` with `PLAN_TERMINAL_MISMATCH` |
| Inputs | Unit results (`AUTH-A3-10`), the terminal gate report (`AUTH-A3-08`), the commit result (`AUTH-A3-14`) |
| Evidence forbidden | Recomputing unit truth: the aggregate copies it. Requirement truth is attached, never folded into the status |
| Model / human role | None / none |
| Persisted state | The result; the `.enforce` trace row; the approved plan's `lifecycle_state`; `ControlState.subtask_states`; the RunRecord's `work_unit_states`; the launch record |
| Failure / UNKNOWN | An exception in the gates or the commit ends the run as an error after the durable state is settled pessimistically; an unproven commit is `UNCERTAIN` and `needs_review`, never `failed` |
| Retry / recovery | Milestone: `milestone_failure_callback` may answer `retry` for a failed unit. Enforce: owner recovery inside the stage loop (`SM-A1-04`), none after the terminal gates |
| Checkpoint / resume | `SM-A1-03`, `GAP-A1-09` |
| Alternate paths | `BYP-A3-13` (milestone units are committed to the real workspace before the plan is verified) |
| Invariant | Partial completion is never success |
| AS-IS evidence | `TRACED` + `MEASURED` (P3) |
| Required contract | Open: `GAP-A3-01`, `GAP-A3-02`, `GAP-A3-03` |

### AUTH-A3-12 - What authority does the Reviewer have?

| Field | AS-IS |
|---|---|
| Truth decided | None about the candidate. Its text is advice for the approver (`stage_review.py::pre_approval_review`) and the run's review text (`final_review`) |
| Owner | `workflow/stage_review.py` |
| Inputs | The goal, the gate evidence, the project BEFORE view, the candidate files or the candidate diff |
| Model role | Diagnostic |
| Human role | The approver reads the pre-approval report |
| Persisted state | `RunState.pre_approval_review` (memory), the result's `review`, the launch review's `reason` text |
| What its failure does | Pre-approval review fails: recorded (`review.pre_approval_unattached`, advisory), the approver is told, approval proceeds. Final review refused or errored: `final_review_refusal`, which makes the unit a non-success on the direct and milestone path and is advisory under enforce (`AUTH-A3-10`) |
| Reuse rule | When a pre-approval review exists, `final_review` returns it and makes no second call |
| Alternate paths | `BYP-A3-14` (the reused review can predate the build and describe a candidate that then failed terminal regression); a review cut off at the output cap is attached as if complete (`GAP-A3-06`) |
| Invariant | Reviewer text never changes a gate verdict |
| Negative control | `stage_review.py` contains no read of a finish reason or truncation flag (search `finish_reason`, `OUTPUT_TRUNCATED`, `truncat` in the file: comments only) |
| AS-IS evidence | `TRACED` |
| Required contract | Open: `GAP-A3-03`, `GAP-A3-06` |

### AUTH-A3-13 - What record proves an approval?

| Field | AS-IS |
|---|---|
| Truth decided | The durable account of a human approval decision |
| Owners | Three records, three writers. (1) `workflow.py::_record_approval_decision`: the `approval.decision` run event with `outcome`, `triggers` and, when unavailable, `reason_code`. (2) `control/launch.py` + `launch_approval.py::build_pending`: the launch record's `pending_approval` with `review_id`, `candidate_digest` (digest of the presented diffs), per-file `diff_sha256`, `reason_sha256`, status and decision; plus the review-diff sidecar holding the exact diff text. (3) `launch_approval.py::_write_audit_decision`: the decision file |
| What none of them carries | The verification binding digest (`AUTH-A3-02`) or the commit's `candidate_hash`. `MEASURED` P5: the event's detail keys are exactly `outcome`, `triggers` (and `reason_code`). `MEASURED` P6: five functions each digest "the candidate" and give five different values for one file |
| Consumers | `metrics/derive.py` (outcome counts) and `metrics/evidence.py` (allowed fields). No gate reads any of the three (search `approval.decision` in `kriya/`) |
| Model / human role | None / the decision recorded is the person's |
| Failure / UNKNOWN | The launch record write must succeed before the workflow continues; the decision file's failure is logged and changes nothing |
| Checkpoint / resume | Not read on resume |
| Invariant | A record is output only; authority is the live decision of `AUTH-A3-04` |
| AS-IS evidence | `TRACED` + `MEASURED` (P5, P6) |
| Required contract | Open: `GAP-A3-04` |
| Limitation | A terminal (non-desktop) run keeps no digest of what was approved at all |

### AUTH-A3-14 - Who may write the real workspace, and what state is a commit in?

| Field | AS-IS |
|---|---|
| Truth decided | Whether the real workspace holds the candidate: COMMITTED, UNCHANGED (not committed or rolled back) or UNCERTAIN |
| Owner | `workflow/terminal_commit.py::commit_terminal_candidate` over `repository/commit.py::commit_revision_grounded_batch`. After an interruption: the commit evidence journal with the owning RunRecord's commit cycle (`commit_service.py::interrupted_commit_outcome`), and `control/recovery.py` for an explicit recovery |
| Order inside the commit | Binding (`AUTH-A3-02`), empty batch, semantic-safety refusal, static-analysis guard, contract transition, durable RunRecord intent, registry staging, the batch (preflight, snapshot, IN_PROGRESS evidence, staging, apply with a per-file base re-check, COMMITTED evidence), re-read digest check, registry promotion, settle |
| Inputs | The materialized batch; each path's expected base revision (direct: the bytes the run first read; enforce: the workspace revision when the plan started) |
| Evidence allowed | Bytes on disk and the fsynced journal |
| Evidence forbidden | In-memory flags after an exception: "the local flags say nothing then: the journal decides" |
| Model / human role | None / none (explicit recovery is an operator command) |
| Persisted state | `<workspace>/.kriya` commit evidence (schema 2: exact before and after byte state per operation), the RunRecord commit cycle with `candidate_hash`, staged temp files while unsettled |
| State transitions | `SM-A3-03` |
| Mutations authorized | `MUT-A3-03`, `MUT-A3-04`, `MUT-A3-09` |
| Mutations forbidden | A commit while an earlier commit is uncertain (`UncertainCommitError`); a write whose base changed (`FileRevisionConflict`) |
| Failure / UNKNOWN | Controlled failure before the apply: nothing changed. During the apply: every applied path is restored, ROLLED_BACK. A failed rollback or evidence write: UNCERTAIN. Fail closed: UNCERTAIN blocks later mutating runs until recovery settles it |
| Retry / recovery | Never a Developer retry (`workspace_commit` is a deterministic stop). `kriya runs recover` settles only what the evidence and the bytes prove; a partial commit only by `--complete-partial` with matching candidate hashes; no crash rollback (base bytes are not durable) |
| Audit emitted | Commit evidence, RunRecord cycle, `workspace_commit.failed` / `workspace_commit_completed` |
| Alternate paths | `BYP-A3-07`; `stage_apply.py::_abort_without_applying` restores original bytes in an in-place run (`MUT-A3-05`, unreachable at the baseline) |
| Invariant | One transaction, two callers, all-or-nothing per batch |
| Negative control | Callers of `commit_terminal_candidate` (two, see `AUTH-A3-02`); `handover/claude-context/generation-pipeline.md` names a committed test pinning this (`tests/test_gate_f_closer_sequence.py`, not re-run) |
| AS-IS evidence | `TRACED` (`terminal_commit.py` 245-413, `repository/commit.py` 557-746, `recovery.py` docstring; the recovery functions were not read) |
| Required contract | Same |

### AUTH-A3-15 - Did earlier milestones still verify before integration (milestone replay)?

| Field | AS-IS |
|---|---|
| Truth decided | Per completed milestone: its persisted verification commands re-executed against the current workspace, PASS, FAIL, not evaluable or not executed |
| Owner | `workflow/milestones.py::replay_prior_milestone_report`, run as the plan phase `REPLAY_PRIOR_VERIFICATIONS` before the integration unit |
| Inputs | `MilestoneRunState.verification_commands`, captured after each milestone by a Run-Verifier judgment (model-inferred commands, Python targets grounded deterministically) |
| Evidence allowed | The executed commands' output and the `[VERIFICATION]` marker |
| Model role | Proposer of the commands at capture time; none at replay |
| Human role | In human-in-the-loop mode the exact command set is approved through the run's approval channel before it executes (docstring: "never re-resolved after the approval") |
| Failure / UNKNOWN | A replay that did not run is the typed terminal `milestone_replay_not_executed`, never "no regression". A regression is `milestone_replay_failed`. No verdict (`not_evaluable`) is recorded and not counted as a failure: **fail open for that case**, bounded by the integration unit's own suite |
| Retry / recovery | None |
| Persisted state | The milestone run-state sidecar |
| Alternate paths | Enforce has no such phase |
| AS-IS evidence | `TRACED` (docstring and the first 40 lines; `_authorize_replay` not read) |
| Required contract | Same; listed for completeness of "replay". Verifier replay after an owner repair is `AUTH-A2-13` |

### A3 answers to the slice questions (each cites its row)

1. **Candidate identity and transaction.** `AUTH-A3-01`, `AUTH-A3-02`; five unlinked digests, `GAP-A3-04`.
2. **Review and source approval.** Need: `AUTH-A3-03`. Decision: `AUTH-A3-04`. Reviewer: `AUTH-A3-12`.
3. **Approval record and candidate digest binding.** `AUTH-A3-13`. The live binding is sound for
   verified-to-committed (`AUTH-A3-02`); approved-to-verified holds by ordering and, in human-in-the-loop mode, by
   the guard (`AUTH-A3-05`), not by comparing the approved digest with anything (`BYP-A3-15`).
4. **Deferred gates.** `AUTH-A3-06`, `AUTH-A3-05`, `FR-A3-20`.
5. **Terminal regression.** `AUTH-A3-07`.
6. **Apply authority, rollback.** `AUTH-A3-14`.
7. **Replay.** Milestone replay: `AUTH-A3-15`. Verifier replay: `AUTH-A2-13`. Commit recovery: `AUTH-A3-14`.
8. **Requirement closure.** `AUTH-A3-09`.
9. **Final-result aggregation, success and failure truth.** `AUTH-A3-10`, `AUTH-A3-11`.
10. **Milestone review and refusal.** `AUTH-A3-10`, `BYP-A3-12`, `GAP-A3-03`.
11. **Repair after a terminal requirement failure.** None exists: `AUTH-A3-08`, `GAP-A3-02`.
12. **Audit record.** `AUTH-A3-13`, `GAP-A3-04`, `GAP-A3-07`.

**What Kriya's words mean today (AS-IS).**

| Word in a result | What the code established | What it does not establish |
|---|---|---|
| `quality_gates_passed: True` (unit) | Candidate gates, terminal regression, a committed apply into the unit's `workspace_path`, a performed final review | That any original requirement is verified (`AUTH-A3-09`); that a person approved (only when a trigger fired) |
| `status: success` (enforce) | Every unit accepted, eight terminal gates without a gap, the real-workspace commit completed | Requirements verified under `record`; a plan-level approval |
| `status: success` (milestone) | Every unit VERIFIED, replay phase without failure, integration unit passed | The same |
| applied / committed | The bytes equal the verified binding and are in the workspace | Verified against the user's requirements |
| `requirement_truth: ALL_VERIFIED` | Every mandatory requirement closed by deterministic evidence for this candidate | - |
| `review_included_in_approval: True` | A pre-approval review text exists | That it describes the final candidate's built or tested state (`BYP-A3-14`) |
