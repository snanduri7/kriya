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

## Slice A4 - goal, knowledge, Skills, plan, executor convergence (2026-11-15)

AS-IS at the baseline. Rows carry the full field set of the documentation addendum. Probe references (`P1` to `P10`)
are `evidence/a4/a4_probe.py` with its output in `evidence/a4/a4_probe_output.txt`; "tests" are the two committed test
files re-run in `evidence/a4/a4_tests_output.txt` (37 passed).

Three execution shapes exist and the rows below say which one each fact holds for:

| Shape | Entered when | Plan object | Executor |
|---|---|---|---|
| direct | `workflow_controller.enabled` is false (the packaged default, `MEASURED` P10), or `kriya fix` | `ExecutionPlan(DIRECT)`, one unit (`plan_adapters.py::direct_execution_plan`) | `plan_executor.py::execute_plan` |
| milestone | a milestone run state | `ExecutionPlan(MILESTONE)` (`plan_adapters.py::milestone_execution_plan`) | `plan_executor.py::execute_plan` |
| enforce | `workflow_controller.enabled` and `mode: enforce` (what Project Setup writes, `MEASURED` P10) | `plan_schema.py::EngineeringPlan` | `enforce_loop.py::EnforceLoop`, which calls the unit primitive itself |

All three run each unit through the same primitive, `workflow.py::WorkflowEngine.run_generation_workflow`.

### AUTH-A4-01 - What is the user's goal, and what is its identity?

| Field | AS-IS |
|---|---|
| Truth decided | The goal text of the run and the digest other evidence binds to |
| Owner | The operator's input, carried verbatim. Identity: `requirements.py::goal_identity` (SHA-256 of the goal plus a NUL), and for a direct unit the plain SHA-256 of the goal (`plan_adapters.py::direct_execution_plan`, equal to the run record's goal hash) |
| Why this owner | Nothing rewrites the goal: the direct unit's goal is "the user's goal, verbatim" (`direct_execution_plan`), and the enforce loop passes it to every unit as `grounding_goal` |
| Authoritative inputs | The goal string from the CLI or the desktop launch; a goal file (`--file`) is protected from generated writes (`workflow.py::_resolve_protected_relpath`) |
| Evidence allowed | The goal string only |
| Evidence forbidden | Planner, Architect, Reviewer or retrieved text. An enforce unit's own `goal` argument is a composed text (below) and is never the requirement source (`AUTH-A4-15`) |
| Model role | None for the goal. Proposer for everything rendered under "Planned Implementation Strategy" |
| Human role | Author |
| Persisted state | Goal hash on the run record; `RequirementSet.goal_digest`; the enforce trace row carries the goal |
| State transitions | None; fixed at run start |
| Downstream consumers | Requirement derivation (`AUTH-A4-02`), the KnowledgeGuard (`AUTH-A4-05`), skill selection (`AUTH-A4-08`), both Planners (`AUTH-A4-10`, `AUTH-A4-11`), the runtime-behaviour reading (`AUTH-A4-13`), `derive_stack_contract`, goal facts (`goal_facts.py`) |
| Mutations authorized | None |
| Mutations forbidden | Any rewrite of the goal by a model |
| Failure / UNKNOWN | An empty or meaningless goal derives zero requirements (`derive_requirements`: `_meaningful`); what admission then does is `AUTH-A2` territory |
| Retry / recovery | None |
| Checkpoint / resume | A direct resume is judged by the goal fingerprint (`plan_adapters.py::work_unit_record` deliberately stores no definition digest for a direct unit). An explicit requirement contract bound to another goal is refused (`REQUIREMENT_CONTRACT_GOAL_MISMATCH`) |
| Audit emitted | `requirement.derived` event with the ids |
| Alternate paths | `BYP-A4-12`: an enforce unit's `goal` argument contains Planner text next to the user's goal; consumers that read `goal` instead of `grounding_goal` read both (`AUTH-A4-05`, `AUTH-A4-08`) |
| Invariant | The text under the "Authoritative Goal" header of a unit goal is the user's goal, byte for byte (`planning_request.py::build_subtask_goal_text`) |
| Negative control | `build_subtask_goal_text` has one production caller, `enforce_loop.py::_invoke_bounded_subtask` (search `build_subtask_goal_text(`) |
| AS-IS evidence | `TRACED`; `MEASURED` P1 (digest stable, equals `goal_identity`), P2 |
| Required contract | Same. Open: one named "goal of record" field read by every consumer, instead of `goal` versus `grounding_goal` (`GAP-A4-01`) |
| Limitation | The CLI and desktop intake code (`cli.py::generate`, the launch service) was not read in A4; slice A5 owns the launch |

### AUTH-A4-02 - What are the run's requirements?

| Field | AS-IS |
|---|---|
| Truth decided | The closed set of requirements the run must close, with stable ids |
| Owner | `requirement_contract.py::requirement_set_for`: the operator's explicit contract when one is bound, else `requirements.py::derive_requirements(goal)` |
| Why this owner | One function, called by the direct engine and by the enforce loop (three call sites in `enforce_loop.py`, one in `workflow.py`) |
| Authoritative inputs | The goal; or a `kriya.requirements/1` file outside the workspace |
| Evidence allowed | Derived: sentences, list items and code blocks of the goal, split deterministically; `kind` is `constraint` when a cue matches. Contract: exactly the `id`/`text`/`kind` entries |
| Evidence forbidden | A contract file inside the workspace ("a repository can never ship its own requirements"); model text; a merge of contract and derived requirements (the contract is a closed set) |
| Model role | None. The Planner is shown the set and may only cite ids (`requirements_prompt_block`: "Never drop, merge or reword") |
| Human role | Authors the goal or the contract |
| Persisted state | Contract bytes content-addressed under `<state>/requirement-contracts/<sha256>.json`; `RequirementSet.digest` joins the verification contract, verdicts, approvals and the enforce `ControlState.requirement_contract_digest` |
| State transitions | None; "fixed in memory at run start" |
| Downstream consumers | The verification contract compiler (`workflow.py::compile_run_contract`, slice A2), the Planner prompts, `validate_plan(known_requirement_ids=...)`, the terminal requirement verifier (`AUTH-A3-09`) |
| Mutations authorized | The content-addressed contract copy (`MUT-A4-01`) |
| Mutations forbidden | Adding, removing or rewording a requirement after run start |
| Failure / UNKNOWN | `REQUIREMENT_CONTRACT_INVALID` before any model call for an unreadable file, another format, an empty set, a bad id, a duplicate id, an empty text or an unknown kind |
| Retry / recovery | None |
| Checkpoint / resume | Enforce: a prior state recorded under another contract digest is not reused (`REQUIREMENT_CONTRACT_CHANGED`) |
| Audit emitted | `requirement.derived`; requirement lineage per stage (`requirements.py::requirement_lineage`) |
| Alternate paths | `kriya fix` passes `requirements_from_goal=False`: no requirement set at all (`BYP-A4-13`). A milestone unit and an enforce unit have none of their own (`AUTH-A4-15`) |
| Invariant | A plan may cite only known ids (`PLAN_REQUIREMENT_ID_UNKNOWN`); mapping a requirement never satisfies it and leaving one out never removes it (`plan_schema.py::Subtask.requirement_ids` comment) |
| Negative control | Callers of `derive_requirements(`: `requirement_contract.py` only. No caller passes `clarifications` (search `clarifications=`: none) |
| AS-IS evidence | `TRACED`; `MEASURED` P1 |
| Required contract | Same for identity. Whether a derived sentence is a requirement at all is `GAP-A2-01` and `GAP-A4-07` |
| Limitation | Derivation is syntactic: every separable statement becomes a requirement or a constraint; nothing classifies a statement as context except the operator's dispositions (slice A2) |

### AUTH-A4-03 - Is the goal ambiguous, and who resolves it?

| Field | AS-IS |
|---|---|
| Truth decided | Whether the goal needs clarification before work starts |
| Owner | **None.** No type, state or stage represents an ambiguity |
| Why this owner | - |
| Authoritative inputs | - |
| Evidence allowed | - |
| Evidence forbidden | - |
| Model role | The Planner and the Developer resolve an ambiguity implicitly by choosing; the integration-decision seam (`AUTH-A4-12`) records a choice only between alternatives a skill or the Planner names |
| Human role | None at this point. A person can pre-empt with an explicit requirement contract, dispositions or acceptance evidence (slice A2) |
| Persisted state | None. `derive_requirements` accepts `clarifications` and would mint `REQ-C<n>` ids, but "no producer supplies yet" (its docstring) |
| State transitions | None |
| Downstream consumers | - |
| Mutations authorized | - |
| Mutations forbidden | - |
| Failure / UNKNOWN | An underspecified goal is one ordinary requirement: "Make it better." derives `REQ-1` (`MEASURED` P1). Under the `block` policies admission can refuse a requirement that has no deterministic closer (slice A2); under the default `record` policies the run proceeds |
| Retry / recovery | None |
| Checkpoint / resume | None |
| Audit emitted | None |
| Alternate paths | The verifier semantic review (`AUTH-A2-07..09`) can catch a wrong reading, after generation |
| Invariant | None exists |
| Negative control | Search `clarifications=` and `REQ-C` producers in `kriya/`: none |
| AS-IS evidence | `TRACED`, `MEASURED` P1 |
| Required contract | Open, with `GAP-A2-01`: a canonical semantic contract before the Developer and the verifier would be the place an ambiguity is recorded and resolved. `GAP-A4-07` |
| Limitation | `architectural_choice.py` was not read; it concerns a choice between implementation alternatives, not goal ambiguity (by name and by its use in the integration-decision seam) |

### AUTH-A4-04 - Which execution shape runs this goal?

| Field | AS-IS |
|---|---|
| Truth decided | direct, shadow-plus-direct, or enforce; and the route (kind, risk, weight) |
| Owner | `cli.py::_dispatch_generation` reads `workflow_controller.enabled` and `.mode`; `workflow_controller.py::WorkflowController.execute` refuses a mode outside `legacy`, `shadow`, `enforce`. The route is `engineering_triage.classify` |
| Why this owner | Configuration is operator authority (SEC-009, slice A5); the header of the Project Setup file says the workflow mode needs approval |
| Authoritative inputs | The resolved `AppConfig` |
| Evidence allowed | Configuration |
| Evidence forbidden | The goal text, model output |
| Model role | None ("never asked of a model", `plan_schema.py::PlannerStructuredOutput` docstring about `kind`) |
| Human role | Operator configuration; Project Setup (`application/project_init.py`) writes `enabled: true, mode: enforce` and `autonomy.mode: human-in-the-loop` |
| Persisted state | `kriya.yaml`; `ControlState.engineering_route` |
| State transitions | None |
| Downstream consumers | Every other row of this slice |
| Mutations authorized | None |
| Mutations forbidden | Enforce never degrades to whole-goal direct execution when planning fails (`_StructuredPlanUnavailable` handler: "authoritative mode will not degrade to legacy execution") |
| Failure / UNKNOWN | An unknown mode raises `WorkflowControllerConfigurationError`; the schema accepts only `shadow` or `enforce` |
| Retry / recovery | None |
| Checkpoint / resume | A resume runs under whatever the configuration says now |
| Audit emitted | The log line "Run route: kind= risk= weight=" |
| Alternate paths | A folder with no `kriya.yaml` runs on the packaged defaults, the direct shape (`project_init.py` module docstring; `MEASURED` P10). So the same desktop product runs two shapes depending on whether Setup was used (`GAP-A4-01`) |
| Invariant | In shadow mode the structured path never affects files or the outcome (`workflow_controller.py::execute`, the `except Exception` around `_run_structured_shadow`) |
| Negative control | Sources of `migration_mode`: `cli.py:_dispatch_generation` only (search `migration_mode` in `kriya/`) |
| AS-IS evidence | `TRACED`; `MEASURED` P10 |
| Required contract | Open: the result truths must be the same whichever shape runs (`GAP-A4-01`) |
| Limitation | `engineering_triage.classify` and the config authority check were not read in A4 (slice A5). `execute_milestones` was not read |

### AUTH-A4-05 - May the run proceed although the model may not know a named library?

| Field | AS-IS |
|---|---|
| Truth decided | Whether a "knowledge gap" stops the run before any model call |
| Owner | `tools/knowledge.py::KnowledgeGuard.check_goal`, called at three sites: `stage_knowledge_guard.py::check_knowledge_guard` (stage 0 of every unit, over the unit's goal), `workflow_controller.py::WorkflowController.execute` (once, before structured planning, over the user's goal) and `stage_architecture.py::design_work_unit` (stage 2A, over the **Architect's design text**, when `knowledge.check_enabled`) |
| Why this owner | One detector. The enforce run asks once for the whole goal and then lets each unit ask only about coordinates the run did not acknowledge; stage 2A asks again about libraries the design introduced |
| Authoritative inputs | `extract_library_versions(text)`; the configured cutoff date; the release date from a registry adapter (or the cache; none when `offline_mode`); the names of the folders in the skills directory |
| Evidence allowed | A gap is: a named library with no skill folder (MEDIUM), or a version released after the cutoff (HIGH without a skill folder, MEDIUM with one) |
| Evidence forbidden | Nothing is excluded: **skill coverage is a substring test of an alias against a folder NAME** (`check_goal`, the `os.listdir` loop). The folder's `skill.yaml`, its `verified` flag and its rules are not read |
| Model role | None in the decision. On a unit of an enforce run the scanned text includes the Planner's description (`BYP-A4-12`) |
| Human role | Confirms the risk: `knowledge_risk_confirmed` (CLI `--yes` or the permissive knowledge policy, `cli.py` lines 3393, 3485, 3535; not read further) |
| Persisted state | The release-date cache (`MUT-A4-05`); nothing about the confirmation |
| State transitions | `SM-A4-03` |
| Downstream consumers | The analysis stage renders every gap as a "KNOWLEDGE GUARD SAFETY CONSTRAINTS" block into the convention prompt (`stage_analysis.py`). Stage 2A: a library the design names and the goal did not is put to the approval callback ("Do you want to proceed with these dependencies?"); a decline ends the unit `human_rejected`, no decision ends it `approval_required`; **with no approval callback the unit proceeds with a warning** (`BYP-A4-18`). An enforce unit has an empty predetermined design, so stage 2A finds nothing there |
| Mutations authorized | None |
| Mutations forbidden | Any model call before the gate when a gap is unconfirmed |
| Failure / UNKNOWN | An unknown release date is not a temporal gap (`if rel_date:`); offline, every temporal check is skipped. An unknown ecosystem is assumed Java. Fail open for the temporal half, by design of the offline mode |
| Retry / recovery | None. Result `status: knowledge_gap` |
| Checkpoint / resume | Direct: a resumed unit sets `knowledge_risk_confirmed = True` (`stage_knowledge_guard.py`, "a resumed checkpoint proves this gate was already cleared"). Enforce: the gate is coordinate-scoped (`resolved_knowledge_coordinates`), so the resume flag does not open it |
| Audit emitted | A `knowledge_gap` traces row with the run's events (`persist_early_exit_trace`) |
| Alternate paths | `BYP-A4-03` (an empty folder with the right name covers a library, `MEASURED` P5), `BYP-A4-19` (the guard's own scaffold is such a folder, `MEASURED` P11), `BYP-A4-08` (direct resume), `BYP-A4-09` (the controller reads the configured skills path, the unit reads the engine's effective root), `BYP-A4-18` (stage 2A without a callback) |
| Invariant | Under enforce a coordinate the run-level check did not see remains a gap at the unit (`workflow.py::unresolved_knowledge_report`; `MEASURED` P5: a coordinate named only by the Planner's text stops the unit) |
| Negative control | Callers of `.check_goal(` in `kriya/`: the three sites above (`evidence/a4/check_citations_a4.py`). The first draft of this row said two; the search found the third and the row was corrected before the checkpoint. `tools/knowledge.py` never imports the skill engine (search `SkillEngine`: none) |
| AS-IS evidence | `TRACED`; `MEASURED` P5, P11 |
| Required contract | Skill coverage from the loaded skill's identity and Kriya-owned verification, not a folder name (`GAP-A4-03`, registry `KNOWLEDGEGUARD-SKILL-IDENTITY-001`) |
| Limitation | The registry adapters and the CLI confirmation flow were not read. Whether a desktop launch can confirm a gap is owed by slice A5. `knowledge.check_enabled` is read by stage 2A only; stage 0 and the controller check do not read it (search `check_enabled` in `kriya/`) |

### AUTH-A4-06 - May a search query leave the machine, and with what text?

| Field | AS-IS |
|---|---|
| Truth decided | Whether live lookup runs, and the exact query |
| Owner | Enablement: `policy/egress.py::web_lookup_allowed` (`autonomy.web_lookup_enabled` and a configured `search.base_url`). Per-query authorization: `workflow.py::WorkflowEngine._approve_web_lookup`. Query content: `outbound_lookup.py::OutboundLookupRequest.from_extracted_terms` inside `live_lookup.py::_resolve_via_web_lookup` |
| Why this owner | One enablement predicate for every consumer (its docstring); one request type "the only value accepted by the outbound search broker" |
| Authoritative inputs | Terms computed by code: names of unverified applicable skills, goal libraries with no skill (`stage_analysis.py`), library names extracted from the **Architect's design text** that no skill covers (`stage_architecture.py`, stage 2B: the term is bounded by the extractor, its source is model output, `BYP-A4-17`), terms extracted from a repeated gate failure (`retry_strategy.py`: `extract_error_search_terms`, excluding the project's own coordinate) |
| Evidence allowed | A term of at most 6 words matching `[A-Za-z0-9 ._:+@-]`, not ending in a source suffix; the query is the term plus " example" |
| Evidence forbidden | Paths, code, diagnostics, free text (`UnsafeLookupTerm`, `MEASURED` P6). Unattended (`web_lookup_auto_approve`): any term outside the public catalogue or `search.public_terms` |
| Model role | None in the term. A model (SkillGapAgent) later reads the fetched pages |
| Human role | Approves each query (`web_lookup_query_callback`, shown the terms and the endpoint) unless auto-approve is on and every term is public; then reviews the found references (`web_lookup_callback`) before they are used for skills |
| Persisted state | None for the decision |
| State transitions | None |
| Downstream consumers | Skill extraction (`AUTH-A4-07`, `MUT-A4-03`); the retry error context (`BYP-A4-04`) |
| Mutations authorized | Skill-store writes after the human reviewed the references (analysis stage); none on the retry path |
| Mutations forbidden | A query with no authorization: "no callback and no opt-in means the query never fires" |
| Failure / UNKNOWN | An unsafe term blocks the whole request (returns nothing); a search or fetch error skips that term; a callback error is a refusal. Fail closed |
| Retry / recovery | One query per term, no variants |
| Checkpoint / resume | None |
| Audit emitted | Log lines only (a blocked request is a warning). No run event was found for a query that left |
| Alternate paths | `BYP-A4-05`: with a per-query human approval a non-public name (for example an internal library named in the goal) is sent (`MEASURED` P6: accepted by the sanitizer, not in the public catalogue). The human-supplied URL path in `stage_analysis.py` is refused under `egress_policy: local_only` |
| Invariant | The query never contains goal, design, code or error text, only the bounded term |
| Negative control | Callers of `_resolve_via_web_lookup`: `stage_analysis.py`, `stage_architecture.py`, and `_augment_error_with_live_lookup` (called by `retry_strategy.py` only) |
| AS-IS evidence | `TRACED`; `MEASURED` P6, P7 |
| Required contract | Fetched text must enter a model request only inside the untrusted fence (`GAP-A4-04`). An audit event per outbound query (slice A6, evidence) |
| Limitation | `tools/search.py`, `tools/web.py`, `extract_error_search_terms` and the PRD-012 egress owner were not read (slice A6 owns egress identity). Registry `DESKTOP-LOOKUP-QUERY-APPROVAL-001` was reconciled in A3 |

### AUTH-A4-07 - What does a Skill say, and is it verified? (Skill fact truth)

| Field | AS-IS |
|---|---|
| Truth decided | A skill's rules, instructions, examples, tags, supported versions, and whether Kriya treats it as verified |
| Owner | `skills/skill.py::SkillEngine` ("the only store", `skill_conventions.py` docstring): `discover_and_load` reads the folders; `_skill_verification_is_authoritative` decides whose `verified` claim counts |
| Why this owner | One loader; precedence is install library, then `cwd/skills`, then the configured `paths.skills` (later wins) |
| Authoritative inputs | `skill.yaml`, `rules.txt`, `instructions.md`, `examples/*` of each folder; the folder's location relative to the workspace and to the Kriya install |
| Evidence allowed | `verified: true` in `skill.yaml` of a skill in the install library or outside the workspace. For a rule: the per-rule provenance file (`rule_provenance.json`) |
| Evidence forbidden | A workspace skill's own `verified` or `verification_gap_acknowledged` (`MEASURED` P8: claim true, honoured false) |
| Model role | Candidate generator for rules: SkillGapAgent extracts rules and examples from a reference; they are written as unverified (`skill_extraction.py::_write_skill_extraction`, `record_rule_provenance`) |
| Human role | Supplies references; `kriya skills create / approve / promote / unverify` (CLI only) |
| Persisted state | The skill folder; `rule_provenance.json`; the conflict registry; in memory for the run: held verifications and run-local gap acknowledgements |
| State transitions | `SM-A4-04` |
| Downstream consumers | Selection (`AUTH-A4-08`), rendering (`AUTH-A4-09`), the skill-gap prompt, the desktop read-only catalogue (`skills/service.py`, KUP `skills.list` / `skills.describe`) |
| Mutations authorized | `MUT-A4-02..04` |
| Mutations forbidden | A new skill in Kriya's shared install directory from another project's run (`new_skill_refusal`); a workspace skill attesting itself |
| Failure / UNKNOWN | A folder that fails to load is logged and skipped: the skill is silently absent from the run (`discover_and_load`, the `except Exception`). An unreadable provenance file leaves every rule trusted (W3 census in the registry row; not re-read) |
| Retry / recovery | None |
| Checkpoint / resume | The canonical `skills_dirs` are part of the resume fingerprint (comment on the run shadow); a workspace skill verified in a run is verified for that run only |
| Audit emitted | The run records the skills it used (`stage_analysis.py::record_active_skills`) |
| Alternate paths | `BYP-A4-06`: a rule with no provenance record is rendered under the trusted `Rules:` header, also for a workspace skill (`MEASURED` P8). `BYP-A4-07`: on the direct and milestone shapes skill writes go to the canonical directory and are Git-committed during analysis; only the enforce loop uses the run shadow |
| Invariant | Kriya's `verified` is never taken from bytes the project controls |
| Negative control | `activate_run_shadow(` has one caller, `enforce_loop.py` (search in `kriya/`). "The only store" does not mean the only writer: `tools/knowledge.py::KnowledgeGuard.generate_skill_template` writes a skill folder with plain `open()` calls, from `stage_apply.py` (auto-accrual after a run) and from the CLI's scaffold prompt (`MUT-A4-11`) |
| AS-IS evidence | `TRACED`; `MEASURED` P8, P11 |
| Required contract | Per-rule trust for workspace skills needs a Kriya-owned attestation (registry `SKILL-WORKSPACE-PERSISTENT-VERIFICATION-ATTESTATION-001`, a future feature by owner decision); until then the label for a workspace skill's rules is the gap (`GAP-A4-06`) |
| Limitation | `skills/run_shadow.py`, `mark_verified`, `apply_held_verifications` and the CLI skill commands were not read line by line |

### AUTH-A4-08 - Which Skills apply to this request? (Skill relevance)

| Field | AS-IS |
|---|---|
| Truth decided | The set of skills rendered into a model request |
| Owner | `skill_conventions.py::skill_applies_to_goal`, called per skill by `stage_analysis.py::analyze_work_unit` (every unit) and by `skill_extraction.py::planning_skill_constraints` (the enforce Planner) |
| Why this owner | One predicate so that "there is no second selection path that could drift" (module docstring, SHARED-DECISION-AUTHORITY-001) |
| Authoritative inputs | The text passed as `goal`; the repository analysis (`fact_match`: a tag names a dependency or framework found in the repo); the per-repo auto skill name; the skill's `supported_versions` against a version in the text |
| Evidence allowed | Token-run equality (`mentions_term`), never substring |
| Evidence forbidden | Embedding or lexical retrieval (owner ruling 2026-11-14, registry `SKILL-RELEVANCE-CONTEXT-SCOPE-001`); none is implemented |
| Model role | None in the predicate. **But the text is not the same for both callers:** the Planner's call passes the user's goal; a unit's call passes the composed unit goal, which contains the Planner's description, acceptance text, planned files and verification text |
| Human role | None. "Skills are selected automatically (no manual selection API)" (registry `GUI-SKILLS-MUTATION-001`) |
| Persisted state | None; the names are recorded on the trace |
| State transitions | None |
| Downstream consumers | `AUTH-A4-09`; the skill-gap prompt (unverified applicable skills) |
| Mutations authorized | None |
| Mutations forbidden | None |
| Failure / UNKNOWN | A version outside `supported_versions` removes the skill (logged). No "unknown" state |
| Retry / recovery | None |
| Checkpoint / resume | Recomputed |
| Audit emitted | Log line per loaded skill; active skill names on the trace |
| Alternate paths | `BYP-A4-02`: a unit can be given a skill the Planner never saw (`MEASURED` P4: Planner `[alpha-skill]`, unit s2 `[alpha-skill, beta-skill]` because the Planner's own description mentions `betalib`), and a version in Planner text can remove one |
| Invariant | Claimed: the Planner sees the same applicable skills as the Developers. Holds only when the Planner's text adds no skill name, tag or library version |
| Negative control | Callers of `skill_applies_to_goal(`: the two above |
| AS-IS evidence | `TRACED`; `MEASURED` P4 |
| Required contract | Selection keyed on the goal of record (and the repository facts), identical for the Planner and every unit; rule-level and unit-level applicability declared by the skill (`GAP-A4-02`) |
| Limitation | The measured case uses the selection predicate with real collaborators and synthetic skills, not a full enforce run |

### AUTH-A4-09 - How does Skill text enter a model request? (rendering and injection)

| Field | AS-IS |
|---|---|
| Truth decided | The bytes of the skill block and where it sits |
| Owner | `skill_conventions.py::render_skill_conventions`; placement by `stage_analysis.py` (into `convention_prompt`) and by the enforce loop (appended to the authoritative Planner request under `PLANNING_SKILL_CONSTRAINTS_HEADER`) |
| Why this owner | One renderer for both |
| Authoritative inputs | The whole skill: every rule (split trusted / unverified), the instructions, every example file |
| Evidence allowed | - |
| Evidence forbidden | Trimming: "Lossless and never trimmed (a request that cannot hold it is a typed dispatch refusal, not a silent drop)" (`enforce_loop.py` comment); `context_budget` treats it as mandatory text |
| Model role | Reader. The Planner and Architect prompts add "Your plan must not contradict any Rule listed there" when the block is present outside the untrusted fence (`stage_planning.py`, `stage_architecture.py`) |
| Human role | None |
| Persisted state | None |
| State transitions | None |
| Downstream consumers | Planner, Architect, Developer requests of every unit |
| Mutations authorized | None |
| Mutations forbidden | None |
| Failure / UNKNOWN | A request that cannot hold the block is refused at dispatch (slice A6 owns the budget rows) |
| Retry / recovery | None |
| Checkpoint / resume | Rebuilt |
| Audit emitted | None beyond the names |
| Alternate paths | The block is **outside** the untrusted-reference fence although its content can be a workspace file (`BYP-A4-06`) or rules a model extracted from a fetched page (`GAP-A4-04`, labelled "Unverified Rules") |
| Invariant | Skill text is advisory about how to implement; a plan's integration decision binds over it (`planning_request.py::INTEGRATION_CONTRACT_AUTHORITY`) |
| Negative control | Callers of `render_skill_conventions(`: `stage_analysis.py`, `skill_extraction.py` |
| AS-IS evidence | `TRACED`. Size: registry `SKILL-RELEVANCE-CONTEXT-SCOPE-001` (`MEASURED` in run 10, preserved: about 31,000 characters to the Planner and to each of six units) |
| Required contract | Per-rule applicability, so a unit receives the rules that apply to it (`GAP-A4-02`). No truncation |
| Limitation | Skills user experience (creating, building from a document, approving in the desktop) is not implemented: `skills/service.py` is read-only; registry `GUI-SKILLS-BUILD-001`, `GUI-SKILLS-MUTATION-001` |

### AUTH-A4-10 - Is the structured plan valid? (enforce)

| Field | AS-IS |
|---|---|
| Truth decided | Whether an `EngineeringPlan` may execute |
| Owner | The planning loop of `enforce_loop.py` (the `while True` after "STRUCTURED PLANNING"), which composes: parse (`agents/contracts.py::parse_planner_structured_output`), the deterministic normalizers (`plan_validation.py::canonicalize_planned_file_actions`, then `plan_normalization.py::coalesce_same_file_owners`, `redirect_immutable_test_edits`, `derive_verification_contracts`, `scope_verification_to_requirements`), `plan_validation.py::validate_plan`, `plan_targets` check, the unbounded-model-subtask check, `planning_request.py::find_missing_grounded_production_artifacts`, `evaluate_plan_edit_feasibility`, and the two repair-regression checks |
| Why this owner | The loop is the only place all the checks meet; `validate_plan` is the largest (37 reason codes in the module) but not the whole decision |
| Authoritative inputs | The Planner's JSON; the workspace (file existence, manifests); the route; the stack contract (`derive_stack_contract(goal)`); the requirement ids; the sealed verification contract's frozen tests, frozen files and documentation paths; delegating shims; the tool catalogue snapshot (`subtask_executor.py::plannable_tool_names`); the structural edges of the bounded candidate set; `goal_requires_runtime_behavior(goal)` |
| Evidence allowed | Deterministic checks only |
| Evidence forbidden | Retrieved reference text: "no authority decision ever reads it" (AUTH-GOAL-CONTAMINATION-001 comment). A tool name outside the plannable snapshot |
| Model role | Proposer. The Planner authors subtasks, files, criteria, invariants, capabilities, dependencies, integration relationships. Bounded repair: at most `planner_repair.py::STRUCTURED_PLAN_REPAIR_MAX_ATTEMPTS` = 2 further answers |
| Human role | **None.** The plan is called "approved" when it validates (`build_approved_plan_document`, lifecycle `approved`); no person sees it before execution |
| Persisted state | The approved-plan document (`MUT-A4-07`); decision-ledger records `structured_plan_normalized`, `structured_plan_targets`, `structured_plan_edit_feasibility`; the planning attempt diagnostic; `ControlState.current_plan_hash` = hash of the **raw** Planner plan |
| State transitions | `SM-A4-01` |
| Downstream consumers | The unit loop: order, `planned_files` as the write boundary (`allowed_write_relpaths`), verification methods, the semantic context of each unit |
| Mutations authorized | None in the workspace. After validation: creation of the plan sandbox |
| Mutations forbidden | Any unit before a valid plan; whole-goal execution on exhaustion |
| Failure / UNKNOWN | Parse failure and every validation error are repair input. Exhaustion: `STRUCTURED_PLAN_REPAIR_EXHAUSTED`, result `needs_review`, `failure_type: PLANNING_ERROR` (`workflow_controller.py`, the `_UnsafeStructuredPlan` handler). A registry that cannot be listed admits no tool |
| Retry / recovery | The 2 repairs; a strict regression never becomes the baseline of the next prompt and is not refunded |
| Checkpoint / resume | Every enforce invocation asks the Planner again ("plan_text above is a FRESH Planner call every time"); prior subtask states are reused only if the new raw plan's hash equals the stored one (`GAP-A4-06`) |
| Audit emitted | `record_plan_created`; ledger records; Planner outcome in the role metrics |
| Alternate paths | During execution the plan is revised deterministically and revalidated at three further `validate_plan` call sites (planned prerequisite, runtime plan gap, scope-owner revision; `plan_revision.py`, bounded by `_MAX_PLAN_SCOPE_REVISION_ATTEMPTS` = 3). The direct shape never runs this decision (`AUTH-A4-11`) |
| Invariant | A MODEL subtask that is not a verification unit owns at least one planned file; one owner per mutable file; the dependency graph is acyclic; every cited id exists |
| Negative control | Callers of `validate_plan(`: `enforce_loop.py` (four sites) and `shadow_mode.py` (search in `kriya/`). No approval call exists between validation and the first unit (search `approval` in `enforce_loop.py` lines 1129 to 1838: resume digests and the approved-plan document only) |
| AS-IS evidence | `TRACED`; `MEASURED` P3 (six reason codes on a plan the direct shape accepts) |
| Required contract | Open: whether a plan needs a human decision under any trigger (`GAP-A3-05`); otherwise the same |
| Limitation | `validate_plan`'s body (lines 565 to 1418) was read through its signature, docstring and reason-code inventory, not line by line. `plan_revision.py` was not read |

### AUTH-A4-11 - Is the plan acceptable? (direct and milestone units)

| Field | AS-IS |
|---|---|
| Truth decided | Whether the Planner's answer may go to the Architect |
| Owner | `file_resolution.py::classify_plan_completeness`, wrapped by `stage_planning.py::_classify_against_project` |
| Why this owner | The direct unit executes off the prose plan; the structured block is used as evidence that the answer is complete and authorized |
| Authoritative inputs | The plan text; the tool catalogue snapshot; the Current Project View's paths |
| Evidence allowed | The structured JSON block parses and passes the schema (`PlannerStructuredOutput`, path authority inside `PlannedFile`); tool names are registered and plannable; a MODIFY or DELETE names a path the project holds (`PLANNED_FILE_ACTION_MISMATCH`) |
| Evidence forbidden | - |
| Model role | Proposer of the prose plan. The Architect (a model) then decides the file list (`stage_architecture.py`, not read) |
| Human role | None at this stage |
| Persisted state | The `plan` stage checkpoint (`MUT-A4-06`) |
| State transitions | `SM-A4-02` |
| Downstream consumers | The Architect |
| Mutations authorized | None |
| Mutations forbidden | Reaching the Architect with an empty, truncated, schema-invalid or path-unauthorized answer |
| Failure / UNKNOWN | `planner_output_incomplete`, `planner_output_schema_invalid` (after at most 2 repairs), `planner_output_unauthorized_path` (never repaired: "repairing it would convert a security rejection into an opportunity to resubmit") |
| Retry / recovery | Repair only for `schema_invalid` |
| Checkpoint / resume | A resumed unit reuses the saved plan and skips the Planner; the saved plan is classified again |
| Audit emitted | `structured_plan_validation`, `structured_plan_repair_requested`, `structured_plan_repair_result` events; the early-exit trace |
| Alternate paths | The plan's **structure** is not validated: dependency cycles, two owners of one file, missing invariants and missing semantic contracts are `complete` here (`MEASURED` P3). On this shape that structure is not executed, so the divergence is one of truth, not of safety: see `GAP-A4-01`. An enforce unit also passes this check, with the one-line predetermined plan (`build_subtask_plan_text`) |
| Invariant | Tool membership uses the same validator as enforce (`planner_validation.py::validate_tool_capability_membership`) and the same snapshot that was shown to the Planner |
| Negative control | Callers of `classify_plan_completeness(`: `stage_planning.py` and the legacy wrapper in `file_resolution.py` |
| AS-IS evidence | `TRACED`; `MEASURED` P3 |
| Required contract | State which plan facts are authoritative on each shape (`GAP-A4-01`) |
| Limitation | The Architect stage and how the write scope of a direct unit is inferred were not read (slice A5 owns the write gate) |

### AUTH-A4-12 - Which Planner statements bind the Developer? (criteria, invariants, decisions)

| Field | AS-IS |
|---|---|
| Truth decided | What a unit's Developer is told and with what force |
| Owner | `planning_request.py`: `build_subtask_goal_text` (the unit goal), `build_subtask_semantic_context` (JSON block), `build_subtask_constraint_context` (the overall request as context), `build_subtask_plan_text` ("Implement: <description>"); assembled by `enforce_loop.py::_invoke_bounded_subtask` |
| Why this owner | One caller, one renderer per part |
| Authoritative inputs | The validated plan and the user's goal |
| Evidence allowed | Under "Authoritative Goal": the user's goal. Under "Planned Implementation Strategy": the subtask description, the acceptance criteria mapped to it, its planned files, its verification text. In the semantic context: the unit's relevant global invariants (statements), upstream and downstream capability contracts, verification targets, runtime ownership, and every integration relationship that names it |
| Evidence forbidden | A plan is "never a source of file content" (`build_subtask_plan_text` docstring) |
| Model role | Planner: proposer whose text is labelled as "the Planner's own implementation choice, not a new user requirement" and "may be adapted if it conflicts with the authoritative goal". **Exception:** integration contracts are labelled AUTHORITATIVE for the stage and a recorded decision must be implemented "exactly" (`INTEGRATION_CONTRACT_AUTHORITY`) |
| Human role | None |
| Persisted state | The plan document |
| State transitions | None |
| Downstream consumers | The unit's analysis, the Developer request, the spec-compliance check (`strict_spec_compliance=True`), the verifier semantic review, which lists criteria and invariants (`AUTH-A2-07`) |
| Mutations authorized | The unit may write only its planned files (`allowed_write_relpaths`); a verification unit is `DENY_ALL` |
| Mutations forbidden | Files of other units |
| Failure / UNKNOWN | A criterion id that does not resolve is silently not rendered (`if criterion_id in criteria_by_id`); `validate_plan` is what should have rejected it |
| Retry / recovery | Recovery contracts travel in their own field (`recovery_contract_block`), not in this context |
| Checkpoint / resume | Rebuilt from the plan |
| Audit emitted | None specific |
| Alternate paths | The direct shape has none of this: the Developer gets the prose plan and the Architect's design. Milestone units get MilestonePlanner text as their goal (`plan_adapters.py::milestone_execution_plan`) |
| Invariant | Planner-authored criteria and invariants are evidence and context; they close no requirement (`AUTH-A3-09`) |
| Negative control | `integration_contracts_for` is "the single reader of the relationship seam for Developer context" (its docstring); not searched further |
| AS-IS evidence | `TRACED` |
| Required contract | With `GAP-A2-01`: a canonical semantic contract would replace Planner-authored criteria as the thing a verifier is judged against. An integration decision is model-authored authority over other units' code: record who may make it (`GAP-A4-08`) |
| Limitation | The Developer request assembly itself (STAGE-GENERATION: `attempt.py`, `retry_prompts.py`, `prompt_composition.py`) is still not traced; handed to slice A6 with the context-budget rows |

### AUTH-A4-13 - Does the request require the application to be executed?

| Field | AS-IS |
|---|---|
| Truth decided | `runtime_verification_required` for the run |
| Owner | `acceptance.py::goal_requires_runtime_behavior` (regular expressions over the goal's sentences; an explicit negation wins for the whole goal) |
| Why this owner | "Kriya's own deterministic reading of the user's request" (`plan_normalization.py::scope_verification_to_requirements` docstring); one function for normalization, validation and each unit |
| Authoritative inputs | The user's goal |
| Evidence allowed | Sentences that state output, exit, startup, response or an explicit execution requirement about an executable subject |
| Evidence forbidden | "runnable" alone; documentation framing; a relative clause of a repair sentence |
| Model role | None in the reading. The Planner may add an `application_runtime` verifier; when the reading is false that verifier is **removed** (`PLAN_VERIFICATION_SCOPE_NORMALIZED`) or, where removal is not obviously safe, the plan is refused (`PLAN_VERIFICATION_SCOPE_UNJUSTIFIED`) |
| Human role | None |
| Persisted state | The normalization record in the decision ledger |
| State transitions | Part of `SM-A4-01` |
| Downstream consumers | `validate_plan(runtime_verification_required=...)`, the unit's `runtime_verification_required`, the run-verification judge's advisory-only rule |
| Mutations authorized | Removal of a plan verifier or of a whole verification-only unit |
| Mutations forbidden | Touching compile or test verifiers |
| Failure / UNKNOWN | No "unknown": a sentence the patterns miss reads as "not required". Then a correct Planner verifier is removed and the run can succeed without the application ever being executed |
| Retry / recovery | None |
| Checkpoint / resume | Recomputed |
| Audit emitted | `structured_plan_normalized` |
| Alternate paths | The owner's greenfield goal is now read as requiring runtime behaviour and the application is executed (`MEASURED`: tests `test_the_goal_reading_that_drives_the_scoping`, `test_the_owner_goal_keeps_s4_and_the_application_is_executed`). A build-only wording still succeeds without execution, as designed (`test_a_goal_without_a_runtime_requirement_still_succeeds_without_executing`) |
| Invariant | A runtime verifier survives only when the user's own text requires execution |
| Negative control | Not searched beyond the three enforce call sites and the unit parameter |
| AS-IS evidence | `TRACED`; `MEASURED` by the committed tests (7 in `tests/test_greenfield_runtime_verification_dropped.py`) |
| Required contract | A false negative must not silently delete evidence: either a typed "not run" in the result or the semantic contract of `GAP-A2-01` (`GAP-A4-05`) |
| Limitation | The pattern set was not reviewed for coverage; only the measured goals are known |

### AUTH-A4-14 - Who executes the plan: order, unit status, completion? (mandatory row)

| Field | AS-IS |
|---|---|
| Truth decided | Which unit runs next, what state each unit is in, when the plan is complete |
| Owner | **Two owners.** direct and milestone: `plan_executor.py::execute_plan` with `_Lifecycle` (`WorkUnitStatus`) and a `PlanDriver`. enforce: `enforce_loop.py::EnforceLoop`, with its own `order`, `approved_stage_states` (strings), `ControlState.subtask_states`, recovery sub-machine (`SM-A1-04`) and terminal (`SM-A3-04`) |
| Why this owner | History: PRD-008A unified direct and milestone; "The structured subtask loop is not yet executed by execute_plan" (comment at the unit call) |
| Authoritative inputs | direct / milestone: the `ExecutionPlan`, durable completion evidence handed in as `reusable_unit_ids`. enforce: the `EngineeringPlan`, the control state |
| Evidence allowed | A unit is VERIFIED / completed only from its own result (`quality_gates_passed`) or validated durable completion |
| Evidence forbidden | A plan result that claims success while a unit is not VERIFIED (`PLAN_TERMINAL_MISMATCH`, direct / milestone) |
| Model role | None in ordering. The Planner authored the dependency edges the enforce order is computed from |
| Human role | None |
| Persisted state | direct / milestone: the run record's `active_work_unit` and commit cycles; stage checkpoints. enforce: `ControlState`, the approved-plan document; a STRUCTURED unit has **no durable unit record** (`plan_adapters.py::work_unit_record` returns None) |
| State transitions | `SM-A3-05` (direct / milestone unit), `SM-A1-04` and `SM-A3-04` (enforce) |
| Downstream consumers | The run result (`AUTH-A3-10`, `AUTH-A3-11`) |
| Mutations authorized | direct / milestone: each unit commits into the real workspace as it passes (incremental; a later failure leaves earlier commits, reported as `committed_work_units`). enforce: units commit into the plan sandbox; one terminal commit to the workspace |
| Mutations forbidden | Starting a plan inside another plan's unit (`NestedExecutionPlanError`); an enforce unit carries a `WorkUnitInvocation` so the engine does not wrap it in a direct plan |
| Failure / UNKNOWN | direct / milestone: a failed unit blocks the rest; an exception marks the unit FAILED before propagating. enforce: `FR-A1` and `FR-A3` rows |
| Retry / recovery | direct / milestone: `driver.retry_failed_unit` (false for direct). enforce: owner recovery, dependent reverification, plan revision |
| Checkpoint / resume | direct / milestone: `select_work_unit_checkpoint`. enforce: `AUTH-A1-10` |
| Audit emitted | direct / milestone: `work_unit_states` in the result. enforce: the `.enforce` trace row, the plan document |
| Alternate paths | The two executors share the unit primitive, `TerminalGateService` and the commit service, and differ in the facts listed in `GAP-A4-01` |
| Invariant | Each: partial completion is never success |
| Negative control | Callers of `execute_plan(`: `plan_executor.py::execute_direct_goal` and the milestone runner (search; `milestones.py` not read). `WorkUnitInvocation(` with `PlanSourceKind.STRUCTURED`: `enforce_loop.py` only |
| AS-IS evidence | `TRACED`; `MEASURED` P2, P3 |
| Required contract | Phase C: one statement of each result truth that both executors satisfy; whether the executors themselves merge is a design choice, not the requirement (`GAP-A4-01`) |
| Limitation | `milestones.py` and the milestone driver were not read in A4 |

### AUTH-A4-15 - Which unit answers for the user's requirements? (requirement ownership)

| Field | AS-IS |
|---|---|
| Truth decided | Where the original requirements are compiled into a contract and verified |
| Owner | direct: the one unit (`WorkUnitInvocation.requirement_goal` = the goal; `workflow.py` compiles the contract inside the unit). milestone: the integration unit only (`plan_executor.py::requirement_goal_of`). enforce: the loop compiles and seals the contract before planning and verifies at the terminal gate; **no unit carries a requirement goal** (`MEASURED` P2: `requirement_goal None`, `authoritative_goal None`) |
| Why this owner | "a milestone or a subtask verifies something narrower than the whole request" (`WorkUnitInvocation` comment) |
| Authoritative inputs | `AUTH-A4-02` |
| Evidence allowed | Slice A2 and `AUTH-A3-09` |
| Evidence forbidden | A subtask's `requirement_ids` as proof ("lineage only") |
| Model role | Planner cites ids; cites are lineage |
| Human role | As in `AUTH-A3-09` |
| Persisted state | The sealed contract; `ControlState.verification_contract_digest` |
| State transitions | - |
| Downstream consumers | The terminal gate (`AUTH-A3-08`, `AUTH-A3-09`) |
| Mutations authorized | None |
| Mutations forbidden | None |
| Failure / UNKNOWN | enforce: admission refusal before any model call when the policy blocks; baseline authorities unavailable is a typed stop; "no mutation required" ends the run as a success without a model call (`NoMutationRequired`) |
| Retry / recovery | enforce: none after a terminal requirement failure (`GAP-A3-02`). direct: the unit's own retry loop can still act, because the verification happens inside the unit |
| Checkpoint / resume | enforce: contract digest drift refuses resume |
| Audit emitted | `requirement.derived`, the sealed contract report |
| Alternate paths | Because an enforce unit has no requirement set, unit-level closers that need one run only at the terminal; A3 found the enforce terminal calling the `file_creation` and `runtime_stdout` closers without per-unit evidence. The Current Project View "before" context reaches the direct Reviewer and spec check, not the enforce terminal verifier (registry `REQUIREMENT-VERIFIER-ENFORCE-PATH-NO-PROJECT-BEFORE-001`; `terminal_gate_service.py::_verify_original_requirements` judges with per-file baselines; row not re-read in A4) |
| Invariant | Exactly one place per shape verifies the original requirements |
| Negative control | `requirement_goal` assignment sites: `plan_executor.py::WorkUnitInvocation.for_unit` only (search `requirement_goal` in `enforce_loop.py`, `terminal_gate_service.py`, `milestones.py`: none) |
| AS-IS evidence | `TRACED`; `MEASURED` P2 |
| Required contract | The point of verification differs by shape and decides whether a repair is possible; Phase C must state it once (`GAP-A4-01`, `GAP-A3-02`) |
| Limitation | The terminal closers were read in A3, not again here |

### AUTH-A4-16 - Brownfield or greenfield: what changes?

| Field | AS-IS |
|---|---|
| Truth decided | Which rules apply because the workspace already holds code |
| Owner | No single owner. The facts are computed where used: `plan_validation.py::canonicalize_planned_file_actions` (create or modify from file existence), the `extension_points` rule of `validate_plan` (`EXTENSION_POINT_REQUIRED`, exempt for an empty workspace and for a resume of the run's own progress), `REFACTOR_BASELINE_MISSING` for a refactor route, `grounded_owner_candidates` for TASK and ENHANCEMENT routes, the Current Project View (`stage_analysis.py`), `admit_workspace_for_mutation` (a non-empty workspace with no Git repository is refused), the contract baseline run (`contract_baseline.py`, enforce only) |
| Why this owner | Each rule reads the repository directly |
| Authoritative inputs | The bound tree and file system; the route kind from triage |
| Evidence allowed | Repository state |
| Evidence forbidden | A Planner claim that a file exists or not: the action is canonicalized from disk, and on the direct shape a MODIFY of an absent path is `PLANNED_FILE_ACTION_MISMATCH` |
| Model role | None |
| Human role | None |
| Persisted state | None of its own |
| State transitions | - |
| Downstream consumers | Plan validation, the Planner prompt (owner candidates, grounding, project view) |
| Mutations authorized | None |
| Mutations forbidden | Converting a mismatched MODIFY into a CREATE ("never converted into a create here or downstream") |
| Failure / UNKNOWN | The Current Project View can be unavailable: recorded (`project_view.unavailable`), the Planner continues on the aggregate analysis, "never read as no files". Owner candidates and localization unavailable: logged, planning continues |
| Retry / recovery | - |
| Checkpoint / resume | The raw plan hash is used for resume precisely because canonicalization flips create to modify after a unit has written (`enforce_loop.py` comment on `raw_plan`) |
| Audit emitted | `project_view.built` / `project_view.unavailable` |
| Alternate paths | A bug fix typed as a refactor route demands a refactor baseline (registry `PLAN-FORM-NORMALISATION-001`, 7 of 7 runs; not re-measured) |
| Invariant | File existence is read from disk, never from the model |
| Negative control | Not run |
| AS-IS evidence | `TRACED` at the call sites; the rule bodies inside `validate_plan` were not read |
| Required contract | Same |
| Limitation | Triage (`triage.py`) decides the route kind and was not read; brownfield baseline policy is slice A2 / A3 material (`baseline_policy.py`) |

### A4 answers to the slice questions (each cites its row)

1. **Goal intake.** `AUTH-A4-01`. The goal is never rewritten; an enforce unit's `goal` argument is a composition and
   two consumers read it instead of the goal of record (`BYP-A4-12`).
2. **Requirement normalization.** `AUTH-A4-02`. One function, syntactic derivation or a closed operator contract.
3. **Semantic ambiguity.** No owner: `AUTH-A4-03`, `GAP-A4-07`.
4. **Knowledge guard.** `AUTH-A4-05`, `SM-A4-03`, `GAP-A4-03`.
5. **Outward lookup.** `AUTH-A4-06`, `GAP-A4-04`.
6. **Skills.** Fact authority `AUTH-A4-07`; relevance selection `AUTH-A4-08`; rendering and injection `AUTH-A4-09`;
   user experience: read-only in the desktop, CLI-only mutation, no build operation (`AUTH-A4-09` limitation). No
   embedding retrieval exists and none is proposed.
7. **Planner authority and plan validation.** `AUTH-A4-10` (enforce), `AUTH-A4-11` (direct), `SM-A4-01`, `SM-A4-02`.
8. **Criteria and global invariants.** `AUTH-A4-12`.
9. **Direct and enforce execution, convergence.** `AUTH-A4-04`, `AUTH-A4-14`, `GAP-A4-01` (mandatory row).
10. **Brownfield / greenfield.** `AUTH-A4-16`.
11. **Requirement ownership.** `AUTH-A4-15`.
12. **Aggregate approval authority (owner question on `GAP-A3-05`).** None in this slice's scope: plan acceptance
    involves no person (`AUTH-A4-10`), and the size trigger has one reader, per unit (`BYP-A4-10`).

## Slice A5 - workspace, root, tools, MCP, mutation, private state, configuration (2026-11-15)

AS-IS at the baseline. Rows carry the full field set of the documentation addendum. Probe references (`P1` to `P8`)
are `evidence/a5/a5_probe.py` with its output in `evidence/a5/a5_probe_output.txt`; "control" means a search in
`evidence/a5/check_citations_a5.py` (output beside it). A committed test is named where one pins the fact.

Three roots are distinct in the code and the rows keep them apart:

| Root | What it is | Owner |
|---|---|---|
| operating root | The directory the run was invoked in and operates on (`workspace_path`) | the caller (CLI working directory; the desktop passes the opened folder as the child's working directory, `ui/standalone/src/main/execution_host.ts`) |
| authority root | The repository root that workspace belongs to: every workspace-scoped authority is keyed by it | `context/workspace_identity.py::authority_root` |
| candidate root | The sandbox a run edits (`<workspace>/.kriya/worktree` or the scoped snapshot) | `workflow/worktree.py::create_git_worktree` |

A fourth location is not a root of the project at all and is the subject of the mandatory row: the **installation
directory** (`config/environment.py::kriya_install_dir`), against which the packaged defaults `paths.skills` and
`paths.memory` resolve (`AUTH-A5-09`).

### AUTH-A5-01 - Which workspace is this, and what is its identity? (root binding)

| Field | AS-IS |
|---|---|
| Truth decided | The canonical spelling of the workspace, the repository root it belongs to, and the identity every workspace-scoped record is keyed by |
| Owner | `context/workspace_identity.py`: `canonical_workspace`, `find_repository_root`, `authority_root`, `workspace_identity` |
| Why this owner | D-001 (owner decision 2026-10-23, in the docstring): one repository, one authority; `<repo>` and `<repo>/sub` share the run lock, RunRecords, launches, approvals and recovery |
| Authoritative inputs | The filesystem only: the real path re-spelled as stored (`platform/filesystem_semantics.py::canonical_spelling`) and the nearest `.git` entry at or above it. The search never ascends through a Kriya-owned directory (`_KRIYA_OWNED_DIRECTORIES`: `.kriya`, `gate-views`, `gate-caches`) |
| Evidence allowed | A `.git` entry (directory, or the file of a linked work tree or submodule) |
| Evidence forbidden | A Git process (none is run here), a name, the configuration, any model text |
| Model role | None |
| Human role | Chooses the folder (CLI working directory; the desktop's open-folder or `workspace.create`, `application/project.py::ProjectService.create`: one `os.mkdir`, nothing scaffolded) |
| Persisted state | The identity is derived, never stored on its own: `sha256(authority_root)`. It is embedded in ownership metadata of control documents (`ownership_metadata`), in the file names of the configuration approval (`authority_approval.py::default_local_approval_path`), the MCP approval store (`mcp/invocation_approval.py::default_local_approval_path`) and the launch records (`control/launch.py`), and in attempt-evidence manifests |
| State transitions | None (pure functions) |
| Downstream consumers | Run lock and RunRecords, checkpoints (`AUTH-A1-09`), launches, approvals, recovery, history attribution (`application/history.py`), configuration discovery (`AUTH-A5-10`), root classification (`AUTH-A5-02`) |
| Mutations authorized | None |
| Mutations forbidden | - |
| Failure / UNKNOWN | A path in no repository is its own root (not an error). A document recorded under another identity is refused (`validate_ownership`: `WORKSPACE_OWNERSHIP_MISSING` / `_MISMATCH`). The pre-PLAT-017 identity is still accepted on read and never written (`legacy_workspace_identity`, `workspace_identities`) |
| Retry / recovery | None |
| Checkpoint / resume | A linked work tree and a scoped snapshot hold their own `.git` entry, so `authority_root(<candidate root>)` is the candidate root itself: a unit of an enforce run, which operates inside the plan sandbox, keys its stage checkpoints by the sandbox, not by the owner workspace. This answers the open question of `BYP-A1-12` for the identity (`TRACED`); whether such a checkpoint survives the sandbox reset of the next run (`git clean -fd` keeps ignored paths only) was not established and goes to Phase B |
| Audit emitted | The identity in every record named above |
| Alternate paths | `kriya/mcp/mcp.py` binds an MCP server's workspace to `os.path.realpath(os.getcwd())`, not to `authority_root` or to the run's `workspace_path` (`BYP-A5-03`). Two projects have different identities (`MEASURED` P7); a renamed or moved folder is a new identity and its approvals and history do not follow it (by construction, not measured) |
| Invariant | Every workspace-scoped authority of one repository is keyed by one root; a Kriya-owned directory never takes the identity of the repository around it |
| Negative control | Control `os.getcwd()` lists every module that reads the process directory; `kriya/mcp/mcp.py` and `kriya/mcp/capability.py` are among them |
| AS-IS evidence | `TRACED`; `MEASURED` P7 |
| Required contract | None differing, except that the MCP workspace binding should be this owner's (Phase C, with `GAP-A5-03`) |
| Limitation | `platform/filesystem_semantics.py` was read at `canonical_spelling` only. `PRODUCT-CANONICAL-SPELLING-LISTING-001` (cost of the per-component listing) is confirmed as a design property, not measured again |

### AUTH-A5-02 - Which files belong to the workspace? (membership; the NO_GIT rule)

| Field | AS-IS |
|---|---|
| Truth decided | Whether a membership authority exists for the operating root, and the set of member paths |
| Owner | `repository/bound_tree.py`: `_root_class` / `workspace_root_class` (the class), `bound_tree_paths` / `bound_tree_declaration` (the set), `admit_workspace_for_mutation` (the admission of a folder with no repository) |
| Why this owner | W3-3 (D-001): the repository's own listing is the only membership; "the filesystem is never a membership authority" (docstring) |
| Authoritative inputs | `authority_root` (`AUTH-A5-01`); Git's own answer for the work tree (`work_tree_root`) and its listing (`visible_repository_paths`: tracked plus untracked-not-ignored) |
| Evidence allowed | Git agreeing with D-001: `identity_key(git top level) == identity_key(authority_root)` |
| Evidence forbidden | A directory name, a basename list, an `os.walk`. A Git refusal is never read as "no repository" |
| Model role | None |
| Human role | For a populated folder with no repository: runs `git init` and commits a baseline (the refusal text says so) |
| Persisted state | None. A candidate sandbox records its own membership once, at creation (`AUTH-A5-03`) |
| State transitions | `SM-A5-01` |
| Downstream consumers | Sandbox creation, the brownfield baseline, checkpoint tree identity, gate views, the static-analysis scope, closers that count files |
| Mutations authorized | None by this owner. `NO_GIT_REPOSITORY` and empty admits the greenfield bootstrap (`MUT-A5-01`) |
| Mutations forbidden | Committing a user's existing files for them (a populated non-Git folder is refused before the contract is compiled) |
| Failure / UNKNOWN | Typed `BoundTreeError`: `BOUND_TREE_UNAVAILABLE` (Git cannot answer, answers differently from D-001, an unresolvable identity, an unlistable folder), `BOUND_TREE_NON_GIT_WORKSPACE_NOT_EMPTY`, `BOUND_TREE_OPERATING_SCOPE_EXCLUDED`, `BOUND_TREE_IDENTITY_MISMATCH`. UNKNOWN fails closed (`FR-A5-01`) |
| Retry / recovery | None; a mid-run membership failure ends the run as `verification_authority_unavailable` (registry `CLI-ADMISSION-REFUSAL-TEXT-MIDRUN-AUTHORITY-UNAVAILABLE-001`: the CLI sentence for that category still says no model was called, `kriya/cli.py::_echo_admission_refusal`, `TRACED`) |
| Checkpoint / resume | Not persisted; recomputed |
| Audit emitted | The refusal's reason code in the result |
| Alternate paths | The registry's own census (`DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001`) lists sites that still decide a membership question by directory name and are not result truth. Re-confirmed for two of them only: `code_intel/service.py::discover_source_files` falls back to an `os.walk` that prunes dot-directories when `git ls-files` fails, and `analyzer.py::index_repository` walks with its own ignore rules (`BYP-A5-08`) |
| Invariant | Membership is the repository's or nothing; a refusal is never an absence |
| Negative control | Not run in A5 (the W3-3 census is cited, not repeated) |
| AS-IS evidence | `TRACED` |
| Required contract | None differing for result truth. The sibling sites are a P3 correctness pass (registry) |
| Limitation | `repository/git.py` and `snapshot.py` were not read; `GIT-ICASE-PATHSPECS-UNQUALIFIED-001` stays UNKNOWN on this host by the registry's own account (`platform/__init__.py` records it beside `GIT_STRIPPED_ENVIRONMENT`) and needs a case-sensitive filesystem |

### AUTH-A5-03 - Where does a candidate live, and what is in it? (candidate isolation)

| Field | AS-IS |
|---|---|
| Truth decided | The directory a run edits and verifies in, and the candidate's own member set |
| Owner | `workflow/worktree.py::create_git_worktree` (creation), `candidate_member_declaration` / `candidate_member_paths` (members) |
| Why this owner | The one sandbox builder; the direct executor and the enforce loop both call it |
| Authoritative inputs | The workspace; Git's top level for it; the repository HEAD; the workspace's uncommitted changes |
| Evidence allowed | A linked work tree at `<workspace>/.kriya/worktree` when the workspace is the repository's top level; a scoped snapshot sandbox (`_create_scoped_snapshot_sandbox`, built from the bound-tree membership) when the workspace is a subfolder of a larger repository |
| Evidence forbidden | The enclosing repository's other content (the nested case exists because checking the owner repository out contaminated a subfolder project, comment in the function) |
| Model role | None |
| Human role | None |
| Persisted state | The sandbox directory; Git's work-tree registration; in a scoped snapshot, a synthetic `.git` holding `kriya-candidate-membership.json` and the append-only `kriya-candidate-writes` |
| State transitions | absent -> created; registered and present -> reset (`checkout -f` of the resolved HEAD, `git clean -fd`), then the workspace's uncommitted changes are copied in (`_sync_uncommitted_changes_into_worktree`) and the materialized tracked bytes are proven equal to the workspace's (`_verify_materialized_tracked_bytes`) |
| Downstream consumers | Every write of a unit (`AUTH-A5-04`), every gate, the commit service (slice A3) |
| Mutations authorized | `MUT-A5-01`, `MUT-A5-02` |
| Mutations forbidden | Running a repository's content filters or hooks (`refuse_repository_content_filters`, `_HOOKS_DISABLED`); nesting a sandbox in a sandbox (`NestedWorktreeError`) |
| Failure / UNKNOWN | The function returns a sandbox path or raises (typed `RepositoryContentFilterRefused` / `BoundTreeError`, else `ValueError` / `RuntimeError`). It has no branch that returns the workspace itself. This closes `BYP-A3-07` as far as this function is concerned: the commit service's `candidate_root == workspace_path` branch is not reachable from its return value (`TRACED`, every `return` read) |
| Retry / recovery | A reused sandbox is reset to HEAD on every creation |
| Checkpoint / resume | See `AUTH-A5-01` |
| Audit emitted | `_audit_git_write` for the bootstrap commits |
| Alternate paths | Comments in the function still speak of "falling back to the unisolated workspace"; no such return exists in it (`DOC-A5-04`). Writers into the sandbox other than a unit's batch are enumerated in `03-MUTATION-AUTHORITY.md` (`MUT-A5-03..05`): the pom correction and the self-correction loop, both through the write gate; no tool and no MCP server writes there (`BYP-A5-03` writes the owner workspace instead) |
| Invariant | A run never edits the owner workspace before its commit transaction (`PRODUCTION_FIXED_RUNTIME_GUARANTEES`: `candidate_isolation_fail_closed`) |
| Negative control | Control `AuthorizedFileWriter(`: four writing sites, all rooted at a candidate root or DENY_ALL |
| AS-IS evidence | `TRACED` |
| Required contract | None differing |
| Limitation | The scoped-snapshot builder and the sync function were read at their signatures and docstrings only. `KERNEL-STAGING-WORKSPACE-BASE-ESCAPES-001`: a Developer edit whose base file exists in the workspace but not in the sandbox is refused by the guarded commit with an untyped `BatchCommitError`; pinned by `tests/test_candidate_transaction.py::test_a_whole_file_replacement_based_on_the_workspace_file_is_refused_by_the_guarded_commit` (committed, not re-run in A5). Fail closed, untyped: `FR-A5-05` |

### AUTH-A5-04 - May model-directed content be written to this path? (the write gate)

| Field | AS-IS |
|---|---|
| Truth decided | Whether one target path may receive a workflow-originated content write |
| Owner | `policy/filesystem.py::AuthorizedFileWriter.authorize` (real enforcement in every mode: `commit_batch` authorizes every item before the first write) |
| Why this owner | MA4.16: the one content-write choke point once a root is known |
| Authoritative inputs | The writer's roots (canonical, symlinks resolved), the target's canonical path, the control-object identities of the root, the unit's `allowed_write_relpaths` and `write_scope_mode`, the run's protected goal-file path |
| Evidence allowed | Filesystem identity (`path_relation`, `path_identity`) |
| Evidence forbidden | A lexical prefix; the path as the model spelled it |
| Model role | Candidate generator: it names the path and supplies the bytes |
| Human role | None at this gate (approval is slice A3) |
| Persisted state | None |
| State transitions | None; decisions in order: outside every root -> `PATH_OUTSIDE_AUTHORIZED_WRITABLE_ROOTS`; a trusted control path (`.git`, `.kriya` and their aliases) -> `TRUSTED_CONTROL_PATH_DENIED`; `DENY_ALL` scope -> `WRITE_SCOPE_DENY_ALL`; outside the unit's allowlist -> `FILE_OUTSIDE_VALIDATED_SUBTASK_SCOPE`; the goal file or an alias of it -> `GOAL_SOURCE_FILE_PROTECTED`; a narrow credential pattern (`_ENFORCEMENT_SENSITIVE_PATH_PATTERNS`) -> `SENSITIVE_PATH_DENIED`; else allow |
| Downstream consumers | `candidate_transaction.py::_stage_candidate_writes` (the batch), `gate_orchestrator.py` (the pom correction), `self_correction.py`, `plugins/core_tools` `FilesystemTool` (planned calls, DENY_ALL) |
| Mutations authorized | `MUT-A5-03..05` |
| Mutations forbidden | The six refusals above |
| Failure / UNKNOWN | `REQUIRE_APPROVAL` is treated as a denial (`_raise_if_denied`); an identity that cannot be established counts as an alias (fail closed) |
| Retry / recovery | A denial raises `PolicyDeniedError` out of the batch; the attempt fails |
| Checkpoint / resume | None |
| Audit emitted | The `PolicyResult` in the exception; no separate record of an allow |
| Alternate paths | **The gate knows the path classes above and nothing about what a path means to Kriya's next run.** `MEASURED` P5: it allows `kriya.yaml`, `kriya.yml`, `skills/<name>/rules.txt`, `skills/<new>/skill.yaml`, `.github/workflows/ci.yml`, `.gitignore`, `.gitattributes`; it denies `.kriya/control/*`, `.git/hooks/*`, `.env` and a path outside the root. So a candidate can carry the project's own Kriya configuration and workspace skills (`BYP-A5-01`, `GAP-A5-02`). Writers that do not pass this gate at all: every skill writer (`BYP-A5-02`), an MCP server with a write grant (`BYP-A5-03`), the stores of `AUTH-A5-09` |
| Invariant | No workflow-originated content write lands outside the candidate root, on a control path or on a credential path |
| Negative control | Control `AuthorizedFileWriter(` (the first run found two sites the draft had not listed: the facade `policy/authority.py::AuthorityPort.file_writer`, which has no caller, control `.file_writer(`; and the cockpit's `security_demonstration.py`, which only calls `authorize` over a disposable folder). Absent `kriya.yaml` in `policy/filesystem.py` |
| AS-IS evidence | `TRACED`; `MEASURED` P5 |
| Required contract | `GAP-A5-02` |
| Limitation | P5 measures the gate's verdict. That a full run then carries such a file through approval and the commit into the owner workspace is `INFERRED` from the generic apply path (no path-specific exclusion was found by search in `kriya/workflow`, `kriya/policy`, `kriya/repository`); it was not exercised end to end |

### AUTH-A5-05 - May this path be read at a model's request? (the read gate)

| Field | AS-IS |
|---|---|
| Truth decided | Whether one path may be read for a model-requested lookup |
| Owner | `policy/filesystem.py::AuthorizedFileReader.authorize` / `raise_if_denied` |
| Why this owner | DEV-INV-001: the read-side sibling of the write gate |
| Authoritative inputs | The readable roots (owner workspace first, then the candidate), the canonical target, the same narrow credential patterns, the control-path rule, the hard-link census of the roots (`ReadableRootLinkCensus`) |
| Evidence allowed / forbidden | As `AUTH-A5-04` |
| Model role | Requester (Developer investigation verbs; a planned `filesystem`, `search`, `ast` or `git` read) |
| Human role | None |
| Persisted state | None |
| State transitions | None |
| Downstream consumers | `plugins/core_tools` (`_authorize_read_target`), `workflow/investigation.py` |
| Mutations authorized / forbidden | None |
| Failure / UNKNOWN | Typed denial; a hard link whose other names lie outside the roots is refused (`HARD_LINK_OUTSIDE_AUTHORIZED_READABLE_ROOTS`) |
| Retry / recovery | None |
| Checkpoint / resume | None |
| Audit emitted | The denial |
| Alternate paths | Readers that take a store path instead of a workspace path are outside this gate: `investigation.py` `find_symbol` / `find_callers` answer from `dependency_graph.db` (`AUTH-A5-09`). Kriya's own context construction reads workspace files directly; that is not a model-requested read |
| Invariant | A model-requested read never leaves the roots and never returns a credential or control file |
| Negative control | Not run in A5 (`tests/test_wave8_git_content_authority.py` and the DEV-INV tests are cited by the subsystem note, not re-run) |
| AS-IS evidence | `TRACED` at the class and its callers |
| Required contract | None differing |
| Limitation | The class body below `authorize` was not read line by line |

### AUTH-A5-06 - May a planned step run this tool? (planned-tool governance)

| Field | AS-IS |
|---|---|
| Truth decided | Whether a tool may be the subject of a model-authored (planned) call, and under which governance |
| Owner | `tools/tool.py::resolve_planned_tool_governance`, rechecked in `authorize_model_authored`; the one entry is `execute_model_authored` |
| Why this owner | S1 / S18: one resolution, projected to the Planner catalogue (`subtask_executor.py::plannable_tool_names`), enforced at plan validation and rechecked by the executor |
| Authoritative inputs | The tool's class, the file that defines its `planned_governance` (closed table `_SHIPPED_PLANNED_DECLARATIONS`), the declared `ToolGovernance`, the run's autonomy mode |
| Evidence allowed | A declaration made by a shipped class in its shipped file |
| Evidence forbidden | A plugin's own declaration; a tool's description; the subtask's description or the Planner's rationale; the tool's name parsed as a string |
| Model role | Candidate generator: `tool_name` and `tool_arguments` are model output |
| Human role | Operator only: `kriya tools execute` is a separate authority and keeps `shell` and `validate_refactor` |
| Persisted state | The TOOL subtask lifecycle in ControlState (`tool_started` -> effect -> `completed`, durable writes); the RunRecord's `tool_execution` for an operator run |
| State transitions | `SM-A5-02` |
| Downstream consumers | The enforce loop's TOOL branch (the only supplier of roots: control `tool_roots=`) |
| Mutations authorized | A `READ_ONLY` tool: none. `FilesystemTool`: every planned write refused (`DENY_ALL`). `GOVERNED_SIDE_EFFECT` (`MCPTool` only): whatever the server does within its capability grant (`AUTH-A5-07`) |
| Mutations forbidden | `shell`, `validate_refactor` (`TOOL_OPERATOR_ONLY`); a third-party plugin tool (`TOOL_MODEL_AUTHORITY_UNDECLARED`); a tool that asks for confirmation under human-in-the-loop (`TOOL_CONFIRMATION_UNSUPPORTED`; `MCPTool` does not ask: control `requires_confirmation` is declared by `BaseTool` and `ShellTool` only) |
| Failure / UNKNOWN | An unreadable declaration is `TOOL_GOVERNANCE_INVALID`; no configuration means the strictest mode. A failure before `_run_model_authored` is `PROVEN_PRE_EFFECT`; any failure after it is `MAY_HAVE_EFFECTED`, terminal, never retried (`TOOL-RUNPHASE-PRE-EFFECT-DENIAL-TERMINAL-001`: intentional contract) |
| Retry / recovery | None for a TOOL subtask; an interrupted one is `needs_review` (`UNRESOLVED_TOOL_EXECUTION`) |
| Checkpoint / resume | A durably completed TOOL subtask is never run again; its authority is revalidated without effect (`revalidate_model_authored`) |
| Audit emitted | The subtask result with reason codes; the policy decision record for MCP |
| Alternate paths | The direct and milestone shapes have no TOOL subtasks at all. Under enforce the governance of an MCP call rests entirely on `AUTH-A5-07` |
| Invariant | A planned call runs only a governed shipped tool, at most once per subtask, with the arguments the validated plan declared |
| Negative control | Control `planned_governance` (declared in `kriya/mcp/mcp.py`, `plugins/core_tools/__init__.py`, `plugins/core_tools/validation_tool.py`); control `tool_roots=` |
| AS-IS evidence | `TRACED` (the subsystem note `handover/claude-context/kernel-mcp-and-tool-authority.md` is CURRENT for these facts, `DOC-A5-01`) |
| Required contract | None differing, except the statement of `BYP-A5-03` |
| Limitation | `tests/test_s18_planned_tool_governance.py` and the TOOL-001 tests were not re-run |

### AUTH-A5-07 - May this MCP tool be invoked, and what can its server touch?

| Field | AS-IS |
|---|---|
| Truth decided | (a) whether one `tools/call` may be dispatched; (b) the filesystem and network authority of the server process |
| Owner | (a) `policy/execution.py::ExecutionPolicy._check_mcp_invocation`, reached only through `mcp/mcp.py::MCPTool._decide_invocation_authority`; (b) `mcp/containment_adapter.py::map_capability_profile_to_containment` over the profile bound in `MCPManager.start_all` |
| Why this owner | TOOL-002 / TOOL-003: invocation authority and capability authority are separate decisions, each with one owner |
| Authoritative inputs | (a) the structured identity (server, tool, schema digest), the capability-profile digest, the workspace identity, the durable approval read fresh from `<MCP approval home>/<workspace identity>.json`; (b) `mcp.<server>.capabilities` (SEC-009 `SECURITY_AUTHORITY`, so operator-approved, `AUTH-A5-10`), the protected authority paths (`AUTH-A5-08`) |
| Evidence allowed | An operator's `kriya mcp approve` record whose binding digest matches all five fields |
| Evidence forbidden | The server's description, annotations, results; the call's arguments; a configuration approval (SEC-009 approval never authorizes an invocation); a flattened display name |
| Model role | Candidate generator of the call (tool name and arguments) under enforce |
| Human role | Operator: approves the configuration (SEC-009) and each tool identity (TOOL-002), both outside the workspace |
| Persisted state | The approval store; the container label carrying the profile digest |
| State transitions | Server: not started -> contained and started, or refused before any process (`ExternalExecutionRefused`, `MCPContainmentUnsupportedError` for `explicit_destinations`, `MCPProtectedAuthorityGrantError`). Call: decide -> dispatch |
| Downstream consumers | `MCPTool._run`; the resume revalidation |
| Mutations authorized | Inside the container: the workspace mount when `workspace_write` is granted (read-write), each `additional_write_paths` entry |
| Mutations forbidden | A grant that is, contains or lies inside a protected authority path refuses the whole mapping (no masking: owner-accepted limit, `MCP-PROTECTED-AUTHORITY-GRANT-001`) |
| Failure / UNKNOWN | DENY, REQUIRE_APPROVAL and a raising policy engine all mean no `tools/call`; an unreadable approval store grants nothing |
| Retry / recovery | None |
| Checkpoint / resume | The decision is re-made on resume without dispatch |
| Audit emitted | `PolicyDecisionRecord` with the tool identity, the profile identity and the containment state (required / active / backend) |
| Alternate paths | The workspace an MCP server is given is `os.path.realpath(os.getcwd())` in `MCPManager.start_all` and in the approval resolver: the **owner** workspace, mounted for the lifetime of the kernel, whatever root the run edits. `MCPTool.authorize_model_invocation` returns its arguments unchanged and never reads the invocation's roots (absent `invocation.roots` in `mcp.py`). A planned MCP call with a `workspace_write` grant therefore changes the owner workspace in place, outside the candidate (`BYP-A5-03`, `GAP-A5-03`) |
| Invariant | No `tools/call` without a current, exactly bound operator approval; no server outside an isolating backend |
| Negative control | Absent `invocation.roots` in `kriya/mcp/mcp.py`; control `os.getcwd()` |
| AS-IS evidence | `TRACED` |
| Required contract | `GAP-A5-03` |
| Limitation | `mcp/capability.py`, `lifecycle.py` and the OCI backend were not read; no MCP server was started (the real-OCI tier is cited, not run) |

### AUTH-A5-08 - Which paths are Kriya's own authority state, and who may touch them?

| Field | AS-IS |
|---|---|
| Truth decided | The set of protected authority paths for a workspace, and the access mode of Kriya's private stores |
| Owner | `policy/protected_authority_paths.py::protected_authority_paths` (the set); `platform/private_store.py` (modes: directories 0700, files 0600, another owner's artifact or a symlink refused, `PrivateStoreUnsafeError`); `policy/filesystem.py::is_trusted_control_path` (the write and read gates' view) |
| Why this owner | One canonical set, consumed by the MCP grant check and by containment; "a path added to the canonical set is protected with no MCP change" (subsystem note) |
| Authoritative inputs | The workspace root; the authority homes' own resolvers (configuration approval home, MCP approval home and the others in `AUTHORITY_HOMES`); an explicit trust file |
| Evidence allowed / forbidden | Filesystem identity; never string logic |
| Model role | None |
| Human role | The operator's homes are the operator's: `KRIYA_AUTHORITY_HOME`, `KRIYA_MCP_APPROVAL_HOME`, `KRIYA_TRUST_FILE` |
| Persisted state | `<workspace>/.kriya/control`, `<workspace>/.kriya/checkpoints`; `~/.kriya/authority/<workspace identity>.json`; `~/.kriya/authority/launches/<workspace identity>/<id>.json`; `~/.kriya/mcp_approvals/<workspace identity>.json`; the state directory (`AUTH-A5-09`) |
| State transitions | None |
| Downstream consumers | The write and read gates, `containment_adapter.refuse_grants_exposing_protected_authority`, the OCI masks |
| Mutations authorized | Control state through `control_store.write_control_file`; approvals through the `kriya authority` and `kriya mcp` commands and the desktop configuration operations |
| Mutations forbidden | Any model-directed write (`TRUSTED_CONTROL_PATH_DENIED`, `MEASURED` P5); an approval store inside the workspace (`validate_trust_path_outside_workspace`, `validate_store_path_outside_workspace`) |
| Failure / UNKNOWN | An authority home that resolves inside the workspace: `ProtectedAuthorityPathsUnresolvable`; the MCP mapping then refuses (`MCP_PROTECTED_AUTHORITY_UNRESOLVABLE`) |
| Retry / recovery | None |
| Checkpoint / resume | - |
| Audit emitted | - |
| Alternate paths | The authority home does not follow `KRIYA_STATE_DIR` (`authority_approval.py::_authority_home_dir`: `KRIYA_AUTHORITY_HOME`, else `~/.kriya/authority`), so a probe or test that isolates only the state directory still writes launch records into the real home (registry `PROCESS-DIAGNOSTIC-PROBE-REAL-AUTHORITY-HOME-001`: confirmed by the code, a process rule, not a product defect; the A5 probe sets its own authority home for this reason). Launch records are never pruned (absent `launch` in `control/retention.py`; `GUI-LAUNCH-RETENTION-001` confirmed current). The skills and memory locations are **not** in the protected set and not private stores (`AUTH-A5-09`) |
| Invariant | A repository can neither ship nor reach an approval of itself |
| Negative control | Absent `launch` in `kriya/control/retention.py` |
| AS-IS evidence | `TRACED`; the per-project store paths `MEASURED` P7 |
| Required contract | None differing |
| Limitation | `private_store.py`, `control_store.py` and the OCI mask code were not read (the subsystem note `config-resolution-and-authority.md`, "Private stores", is cited as written); `CERT-OWNER-DEATH-MASK-SCRATCH-001` and the Docker cleanup rows are environment residue of that mask code, not traced |

### AUTH-A5-09 - Where does Kriya's state live, and to which project does each store belong? (mandatory row)

The owner's proof standard for `DESKTOP-PROJECT-RETRIEVAL-STATE-ISOLATION-001` asks for nine facts per store. The
table gives them; the row below it carries the authority fields.

| Store | Storage path (default) | Namespace key | Write key | Read / lookup key | Fallback, default or global lookup | Legacy / migration path | Reopen / resume | Desktop vs CLI |
|---|---|---|---|---|---|---|---|---|
| Run control: RunRecords, ControlState, plan document, checkpoints | `<authority root>/.kriya/control`, `.kriya/checkpoints` | The folder itself, plus `workspace_id` ownership metadata checked on read | workspace path | workspace path | None | Legacy nested control store (not read) | Per project by construction | Same |
| Configuration approval | `<authority home>/<workspace identity>.json` | workspace identity in the file name and in the record | same | same; else an explicit trust file | The trust file is operator-supplied and applies to whatever workspace it is used with (bound by the set digest) | - | Follows the path identity | Same store |
| MCP invocation approval | `<MCP approval home>/<workspace identity>.json` | workspace identity + tool identity + profile digest | same | same, read fresh per call | None | - | Follows the path identity | CLI only (`kriya mcp`) |
| Launch records | `<authority home>/launches/<workspace identity>/<id>.json` | workspace identity | same | same | None | - | Never pruned | Desktop launches only |
| Run history (`traces.db`), attempt evidence, model runtime records | State directory: `KRIYA_STATE_DIR` > `paths.state` > `~/.kriya/state` | **None in the `runs` table.** A run belongs to a workspace only through its evidence manifest's `workspace_identity` | run id | The desktop history filters by the manifest's identity; a run with no manifest is `unattributed` and shown for no workspace (`application/history.py`) | One state directory for every project (`MEASURED` P7: same for A and B); a window started with another `KRIYA_STATE_DIR` reads another history (`HISTORY-STATE-DIR-PER-PROJECT-001`, confirmed) | `kriya traces --migrate-legacy` copies a pre-move database once | A window pins a history snapshot (`HISTORY-SNAPSHOT-STALE-ON-REOPEN-001`, not re-traced) | The desktop passes `KRIYA_STATE_DIR` through only when the operator set it (`child_env.ts`) |
| Code index: `dependency_graph.db` (legacy graph tables and the structural `ci_*` tables), `vector_index.db` | `paths.memory`; packaged default `./memory` resolved against the **installation directory** | **None.** Rows are keyed by repository-relative path; the manifest holds the embedding fingerprint, the structural identity and a `repository_revision`, and no root, identity or path of the indexed repository (`MEASURED` P2: columns and manifest keys) | relative path | relative path, symbol name, embedding similarity | **The default is global**: every project without its own `paths.memory` resolves the same directory (`MEASURED` P1: two bare folders and a Project Setup folder) | None | The store outlives every run; whichever project indexed last owns it | Same resolution. Writers differ: see the row below |
| Release-date cache `knowledge_cache.db` | `paths.memory` | None (ecosystem, package, version) | package coordinate | package coordinate | Global by default | Columns added in place | - | Same |
| Learned web knowledge `web_knowledge.db` | `paths.memory` | None | embedding of fetched text | embedding of the goal | Global by default (`MEASURED` P7: same path for A and B) | - | - | Same; written only when web lookup is enabled |
| Skills: folders, rules, provenance, conflict registry, staged facts | `paths.skills`; packaged default `./skills` resolved against the installation directory; plus `<cwd>/skills` when `skills.load_cwd` | The folder name. `auto-<basename of the workspace>` for staged run facts (`stage_apply.py`, `repo_slug`): two projects with the same folder name share it | skill name | folder name (knowledge gate), tags and text (selection, slice A4) | **Global by default** (`MEASURED` P1). The engine also loads the installation library whenever `skills.load_global` | - | Under enforce a run shadow isolates the run's writes until commit (slice A4) | Same |

| Field | AS-IS |
|---|---|
| Truth decided | Which location each store uses, and which project its content belongs to |
| Owner | Location: `config/resolution.py::resolve_config_state` for `paths.*` (anchored to the directory of the file that set the value; the packaged default to `KRIYA_INSTALL_DIR`), `core/state_paths.py` for the state directory. **Project attribution of the retrieval, knowledge and skill stores: no owner.** No module assigns, records or checks one |
| Why this owner | Historical: the packaged defaults predate the desktop; `./skills` in the installation is the shipped skill library, and `./memory` was resolved the same way |
| Authoritative inputs | The effective configuration; for the state directory the environment variable |
| Evidence allowed | For control state and approvals: the workspace identity. For the stores under `paths.memory` and `paths.skills`: nothing identifies the project |
| Evidence forbidden | - |
| Model role | Consumer: retrieval hits, goal facts, skill text and learned references enter Planner and Developer requests (slices A4, A6) |
| Human role | Sets `paths.memory` / `paths.skills` in the project's `kriya.yaml` to make them project-local (a relative value inside the project is `REPOSITORY_SAFE`). Project Setup does not write them (`MEASURED` P1: the file has no `paths` section; absent `"paths"` in `application/project_init.py`) |
| Persisted state | The stores of the table |
| State transitions | `SM-A5-04` |
| Downstream consumers | Readers of the code index: the analysis stage (`stage_analysis.py`, when `vector_index.db` exists), `graph_retrieval.py` (`retrieve_graph_context`, the localization helper), `goal_facts.py::developer_goal_facts`, `plan_targets.py`, `candidate_transaction.py`, `enforce_loop.py`, `edit_feasibility.py`, `generation_service.py`, `investigation.py` (`find_symbol`, `find_callers`), `triage.py` (blast radius), `process_boundary.py` and `gate_orchestrator.py` (class locations), `production_doctor.py` (control `paths.memory` lists them all) |
| Mutations authorized | `MUT-A5-09..13` |
| Mutations forbidden | A **new skill** in the installation's skills directory from another project's run (`SkillEngine.new_skill_refusal`, enforced by `create_skill` and asked by the analyzer's conventions skill only). Nothing forbids any other write there |
| Failure / UNKNOWN | An absent or empty index reads as "no index" and the run proceeds without retrieval. A populated index of **another** project reads as this project's index (`MEASURED` P2, below). There is no "this index is not mine" state |
| Retry / recovery | None |
| Checkpoint / resume | The memory and skills paths are part of the configuration fingerprint a checkpoint binds (comment in `control/checkpoint.py`); the stores' content is not |
| Audit emitted | `retrieval.*` run events; the trace row's `retrieved_chunks` (`control/trace.py`) |
| Alternate paths | Who can populate the shared code index: `kriya analyze` (`cli.py`) in any folder whose configuration does not set `paths.memory`, and `autonomy.auto_index_missing_dependency_graph` (`workflow.py::_ensure_repository_indexed`), which is off by default and `REPOSITORY_SAFE`, so a project's own `kriya.yaml` may turn it on (control `index_repository(cfg`: these two callers only). The desktop has no indexing operation (absent `index_repository` under `kriya/kup`, `kriya/application`, `kriya/client`). So a desktop-only installation with the packaged defaults never populates the index, and a machine where `kriya analyze` was run once in any unconfigured folder has a populated global one |
| Invariant | REQUIRED and not held: state or retrieval belonging to project A is never returned, written or reused for project B through any explicit, fallback, default, global, legacy or missing-key path |
| Negative control | Absent `workspace` in `code_intel/store.py` and `memory/learned_knowledge.py`; absent `workspace_id` in `memory/vector.py`; the directory-level absences above |
| AS-IS evidence | `MEASURED` P1, P2, P7, P8; `TRACED` for the reader and writer lists |
| Required contract | `GAP-A5-01` |
| Limitation | The registry's own measurement (ten desktop runs with no `retrieval.*` event; an install-tree database with no `files` rows) was not repeated. Which reader created that empty database was not traced; at least one reader opens `DependencyGraph(db_path)` on a path it did not first check (the class-location index in `gate_orchestrator.py`), and SQLite creates a missing file on open, so a read can create the file in the installation tree (`INFERRED` for the measured file) |

What project B reads once project A has been indexed into the location both resolve (`MEASURED` P2, a deterministic
embedder, no model):

| Reader, called for project B | Result |
|---|---|
| `DependencyGraph.has_indexed_files()` (the auto-index "already indexed" test) | True: B is never indexed, A's index stands in for it |
| `DependencyGraph.find_symbol_locations("AlphaSecretService")`, `get_class_symbol_locations()` (investigation verbs; the duplicate-type gate) | A's file `src/alpha_secret.py` |
| `open_code_intelligence(B, ...)` | A service: a "baseline exists" for B. Its `find_symbol` without `current_view()` returns A's symbol; with `current_view()` it returns nothing of A and B's own symbols (the overlay shadows every stored path that B does not hold) |
| `retrieve_graph_context(goal, B, ...)` | `matched` True, `matched_files` = A's file; A's private marker text is in `retrieved_chunks` (which `stage_analysis.py` appends to the run's trace row); it is **not** in `graph_rag_context` (the file does not exist in B, so the assembled block has no content for it) |
| `developer_goal_facts(goal, B, ...)` | Empty (it uses `current_view()`) |
| Indexing B afterwards (`RepositoryAnalyzer(B).index_repository`) | A's rows are gone: `indexed_paths` = B's file only. The full walk decides what exists, so each `kriya analyze` replaces the other project's index |

### AUTH-A5-10 - Which configuration is effective, and who may set each field? (configuration authority)

| Field | AS-IS |
|---|---|
| Truth decided | The effective value of every configuration field, the source of each, and whether that source may set it |
| Owner | `config/resolution.py::resolve_config_state` (layering, provenance, canonical paths, preset expansion, closed vocabularies), `config/authority.py::compute_violations` (classification), `config/authority_approval.py::resolve_with_approval` (approval coverage), `config/config.py::load_config` (the raise) |
| Why this owner | SEC-009: one path for what counts as a violation, shared by `load_config`, `kriya authority inspect / approve` and the desktop's configuration operations (`application/configuration.py`, `config/service.py`) |
| Authoritative inputs | The packaged default; one user layer (the `kriya.yaml` at the authority root of the working directory, else the installation directory's, else `--config`); the approval artifact or trust file |
| Evidence allowed | For a non-`REPOSITORY_SAFE` field from an untrusted source: an approval whose set digest covers exactly the current security-relevant field set |
| Evidence forbidden | The field's presence in a `runtime_profile` preset set by the repository (derived fields inherit the untrusted source); an approval of a malformed `autonomy.mode`, egress policy or containment opt-out (refused before classification) |
| Model role | None by design. See alternate paths |
| Human role | Operator: `kriya authority approve` or the desktop approval. A Project Setup folder needs it before its first run: `autonomy.mode`, `llm.base_url`, `embedding.base_url`, `llm_chain`, `workflow_controller.enabled`, `workflow_controller.mode` (`MEASURED` P1) |
| Persisted state | The approval artifact (outside the workspace, salted value digests) |
| State transitions | `SM-A5-03` |
| Downstream consumers | Everything; `serve.py` reloads the configuration per request so a `SECURITY_AUTHORITY` value is never read once and kept |
| Mutations authorized | The approval store (`MUT-A5-14`) |
| Mutations forbidden | A trust file or approval inside the workspace |
| Failure / UNKNOWN | An unclassified field is `SECURITY_AUTHORITY` (fails closed). A `kriya.yaml` between the working directory and the root is refused (`ShadowedConfigurationError`). A corrupt approval grants nothing |
| Retry / recovery | None |
| Checkpoint / resume | The configuration fingerprint is a checkpoint binding (slice A1) |
| Audit emitted | `kriya authority inspect` shows each pending field, value redacted |
| Alternate paths | (1) **`REPOSITORY_SAFE` means "a repository may set it without approval", and the repository's bytes can be written by a run** (`BYP-A5-01`): the write gate allows `kriya.yaml` (`MEASURED` P5). The repository-safe set includes `autonomy.run_verification_enabled`, `spec_compliance_enabled`, `knowledge.check_enabled`, `knowledge.offline_mode`, `auto_index_missing_dependency_graph`, `brownfield_full_regression_baseline_policy` and the run budget (`AUTH-A5-12`). (2) The reason text for a dynamically classified field (`agent_llms.<role>`) says "unknown/future field" because `compute_violations` labels with `is_known_field`, which reads the static tables only (`SEC009-AGENT-ROLE-REASON-TEXT-001`: confirmed current, message only, `TRACED`). (3) The installation directory's `kriya.yaml` is an untrusted source (`AUTO_DISCOVERED_INSTALL_DIR`), while the installation directory's `skills/` and `memory/` are the trusted default locations (`AUTH-A5-09`) |
| Invariant | Repository-controlled configuration cannot grant itself control-plane authority |
| Negative control | Not a search: P6 measures the classification of the budget, the size trigger and the mode |
| AS-IS evidence | `TRACED`; `MEASURED` P1, P6 |
| Required contract | `GAP-A5-02` (what a run may write), `GAP-A5-04` (budget) |
| Limitation | `authority_approval.py` was read at its outline; the desktop configuration operations were not read |

### AUTH-A5-11 - What is the production profile, and what does it seal?

| Field | AS-IS |
|---|---|
| Truth decided | The values a `runtime_profile: production` run is held to |
| Owner | `config/resolution.py::runtime_profile_preset_fields` and the contradiction check in `resolve_config_state`; `production_doctor.py` verifies the fixed guarantees against the real environment |
| Why this owner | The preset is expanded before classification so a profile cannot launder a field |
| Authoritative inputs | The preset table; the user layer's explicit values |
| Evidence allowed | An explicit value equal to the preset, or for the budget a positive integer at most 3600 (`_PRODUCTION_STRICTER_ACCEPTED`) |
| Evidence forbidden | Any other explicit value for a sealed leaf: `ValueError`, not approvable |
| Model role | None |
| Human role | Operator selects the profile; from a repository file it is a `PLATFORM_POLICY` violation needing approval, as are twelve of the derived fields (`MEASURED` P6) |
| Persisted state | None of its own |
| State transitions | None |
| Downstream consumers | Every reader of the sealed fields |
| Mutations authorized / forbidden | - |
| Failure / UNKNOWN | Contradiction refuses at load |
| Retry / recovery / checkpoint | - |
| Audit emitted | `kriya doctor --production` |
| Alternate paths | The seal sets: enforce workflow; `execution_policy` enforce; `egress_policy: local_only`; containment `oci` with both mandates; `brownfield_full_regression_baseline_policy: required`; `shell_network: denied`; `knowledge.offline_mode: true`; **`requirement_unknown_policy: block` and `requirement_unverified_policy: block`**; budget 3600. This answers what A4 handed over: the production profile blocks on unverified and unknown requirements and keeps KnowledgeGuard off public registries. It does **not** set `autonomy.mode` (so the mode stays whatever the configuration says; the packaged default is human-in-the-loop), `web_lookup_enabled`, `risk_threshold_lines` or `paths.*`. The Project Setup profile is a different profile (`local-models-governed`): enforce, human-in-the-loop, budget 900, no requirement policy, so `record` / `record` (slice A4 P10) |
| Invariant | A production run cannot be weakened by an explicit override of a sealed leaf |
| Negative control | P6: production with 7200 or with `null` is refused; with 2400 accepted |
| AS-IS evidence | `TRACED`; `MEASURED` P6 |
| Required contract | Phase C decides whether the desktop's Setup profile and the production profile should differ on requirement policy (`GAP-A3-01` depends on it) |
| Limitation | `production_doctor.py` was not read |

### AUTH-A5-12 - What is the run's wall-clock budget, and who owns it?

| Field | AS-IS |
|---|---|
| Truth decided | The number of seconds a run may take before it stops |
| Owner | The value: whichever configuration layer sets `autonomy.generation_time_budget_seconds` (`REPOSITORY_SAFE`). The clock: each unit invocation's `GenerationState` (slice A1, `AUTH-A1-08`) |
| Why this owner | The field was classified repository-safe as a tuning value ("picking a model or tuning sampling never grants new capability") |
| Authoritative inputs | Packaged default `null` (no deadline); Project Setup writes 900; the production preset 3600 (or a smaller explicit value); the Scenario-04 template 2400 |
| Evidence allowed | Any value from any layer; `null` included, outside the production profile |
| Evidence forbidden | Under production: a value above 3600, zero, negative, a boolean, or `null` |
| Model role | None directly (but see `BYP-A5-01`: the field is in a file a run may write) |
| Human role | Operator or repository author |
| Persisted state | None |
| State transitions | None |
| Downstream consumers | `workflow.py` (the deadline of a unit), `core/llm.py` (inference deadline), `memory/embedding.py` (`run_deadline`), `tools/validate.py`, `workflow/attempt.py`, `control/run_coordinator.py` (control `generation_time_budget_seconds`) |
| Mutations authorized / forbidden | - |
| Failure / UNKNOWN | No budget means no deadline |
| Retry / recovery | Under enforce each unit invocation starts a fresh budget (A1b); the number is therefore a per-invocation bound, not a run bound |
| Checkpoint / resume | A resumed run starts a fresh clock (slice A1) |
| Audit emitted | The CLI's "GENERATION BUDGET EXHAUSTED" text |
| Alternate paths | A repository `kriya.yaml` may set 999999 or `null` with no violation (`MEASURED` P6). **2400 versus 3600:** 2400 is one scenario's calibrated configuration value (`demo-v2/scenarios/04-spring-ignite-protocol/config/kriya.template.yaml`), 3600 is the production seal's ceiling (`PRODUCTION_GENERATION_TIME_BUDGET_SECONDS`), and 2400 satisfies the seal (`MEASURED` P6). No lower layer diverges from an accepted profile silently: the effective number is the configuration's, read directly by every consumer. What is missing is an owner of the **run-level** bound (the number is applied per unit invocation) and any binding of the number into qualification identity, which slice A6 checks |
| Invariant | Under the production profile every unit invocation has a finite deadline of at most one hour |
| Negative control | P6 |
| AS-IS evidence | `TRACED`; `MEASURED` P6 |
| Required contract | `GAP-A5-04` |
| Limitation | The consumers were located by search, not read |

### How the A5 trace list maps to rows

1. **Root binding.** `AUTH-A5-01`. 2. **Git membership, NO_GIT rules.** `AUTH-A5-02`, `SM-A5-01`. 3. **Candidate
member paths.** `AUTH-A5-03`. 4. **Filesystem reads and writes, the write gate, mutation scopes.** `AUTH-A5-04`,
`AUTH-A5-05`, `MUT-A5-01..17`. 5. **Tool and MCP writes, containment.** `AUTH-A5-06`, `AUTH-A5-07`, `SM-A5-02`,
`GAP-A5-03`. 6. **Protected authority paths, private state.** `AUTH-A5-08`. 7. **Desktop project and retrieval
isolation (mandatory row).** `AUTH-A5-09`, `SM-A5-04`, `GAP-A5-01`. 8. **Configuration authority, production profile,
the 2400 / 3600 budget.** `AUTH-A5-10..12`, `SM-A5-03`, `GAP-A5-04`. 9. **MCP parent-read limitation.** An intentional
secure contract today (`AUTH-A5-07`, mutations forbidden): a grant containing a protected path is refused, nothing is
masked. 10. **Aggregate approval authority (owner question on `GAP-A3-05`).** None in tools, MCP, configuration or the
application layer: the launch approval binds one candidate digest per review (`control/launch_approval.py::build_pending`),
the MCP approval binds one tool identity, the configuration approval binds a field set, and the process-profile
approval (`process_profiles.enforce_approval`, off by default) forces the same per-unit gate. Controls: no
`risk_threshold_lines` under `kriya/kup`, `kriya/application`, `kriya/mcp`, in `enforce_loop.py` or in
`launch_approval.py`. 11. **Class-D setup errors.** Not owned here: the three contained-environment rows are
characterized in `GAP-LEDGER.md` (slice A5 registry table) as environment-coupled and go to A6 with the remaining
class-D evidence.

## Slice A6 - runtime, model, qualification, fallback, Developer request, evidence, extensibility (2026-11-15)

AS-IS at the baseline. Rows carry the full field set of the documentation addendum. Probe references (`P1` to `P7`)
are `evidence/a6/a6_probe.py` with its output in `evidence/a6/a6_probe_output.txt`; "control" means a search in
`evidence/a6/check_citations_a6.py` (output beside it: 76 files, 337 symbols, 47 controls, 0 problems). "Section 42"
is `handover/PRODUCT-72H/SPRINT_LOG.md` section 42 (preserved historical measurement of 2026-11-14, not re-run:
live models are frozen).

The layering every row below relies on (`TRACED`, module docstring of `core/inference_runtime.py`):

```
workflow / agents  ->  LLMClient (kriya/core/llm.py: the inference service)  ->  InferenceRuntimePort  ->  runtime adapter  ->  provider
```

Everything that is a decision (which model, which settings, the budget, the deadline, egress, retries, fallback,
evidence) sits in `LLMClient` or above it. An adapter owns the wire format, the per-request context-window option, the
identity probe, model discovery and the classification of transport errors.

Trust classes of the text in one model request, used by `AUTH-A6-09` and by Phase B:

| Class | Meaning | Carried how |
|---|---|---|
| T-KRIYA | Text Kriya authored (system prompt, section headers, protocol, reminders, scaffolds) | Literal strings in `agents/agent.py`, `retry_prompts.py` |
| T-USER | The user's own goal words | `Goal:` / the Authoritative Goal section of the task description |
| T-MODEL | Text another model produced in this run (plan, design, Planner criteria) | Task description (`Plan:`), `=== Architecture Design ===` |
| T-REPO | Bytes of the project (code context, retry projections, sibling files) | `=== Existing Code Base Context ===` |
| T-VALIDATOR | Output of a deterministic gate (compiler, test runner, runtime) | `=== Previous Error to Fix ===` |
| T-SKILL | Skill text (rules, instructions, examples), split trusted / unverified | The conventions block inside the code-base context |
| T-REFERENCE | Learned or retrieved documentation | Inside the one fence, `untrusted_context.py::fence_untrusted_reference` |
| T-EXTERNAL | Text fetched from the web during this run | **No class of its own in the code** (`GAP-A4-04`, completed in this slice) |

No request carries these classes as structure: a request is two messages, `system` and `user`
(`LLMClient.complete_result`), and the classes exist only as headings inside the user message.

### AUTH-A6-01 - Which model serves this call, and with which settings? (model binding)

| Field | AS-IS |
|---|---|
| Truth decided | The model name, endpoint, sampling settings, output budget, declared window and runtime adapter of one call |
| Owner | `core/llm.py::LLMClient._binding` (settings of a model), with the caller choosing the model: `agents/agent.py::call_with_escalation` for Planner, Architect, Reviewer, run verifier, spec compliance and skill gap (the role's `agent_llms.<role>.llm`, then its chain); `workflow/generation_service.py::_run_developer_generation` for the Developer (`AUTH-A6-07`) |
| Why this owner | MODEL-EVIDENCE-HARDENING-001: a call to a model executes with that model's own binding, never the primary's, so the qualified inference identity equals the executed one |
| Authoritative inputs | The effective configuration: `llm`, `llm_chain`, `agent_llms.*` (all `SECURITY_AUTHORITY` fields for endpoint, model, `extra_body`, `inference_runtime`, `context_policy`: `AUTH-A5-10`); the routing plan when `model_policy.routing` is on (`core/model_routing.py::plan_routes`, applied to a copy of the configuration before the client exists, `cli.py::_workflow_config`) |
| Evidence allowed | The configuration and, for routing, a current QUALIFIED record plus the operator's metrics table |
| Evidence forbidden | Any model text; the model's own name as proof of capability; `extra_body.model` (refused, `PROVIDER_REQUEST_FIELD_CONFLICT`: `provider_contract.py::refuse_extra_body_request_fields`, checked in the configuration validators, in `LLMClient._refuse_owned_request_fields` and in `ChatRequest.__post_init__`) |
| Model role | None (a model never selects a model) |
| Human role | Operator: writes and approves the configuration |
| Persisted state | None of its own; each call's result carries `model`, `runtime_fingerprint`, `inference_settings_digest` (`core/completion.py::CompletionResult`) |
| State transitions | None |
| Downstream consumers | `AUTH-A6-02` to `AUTH-A6-05`; role metrics (`AUTH-A6-08`); the evidence records `model.request` / `model.result` |
| Mutations authorized | None |
| Mutations forbidden | - |
| Failure / UNKNOWN | **A model name no binding names resolves to the primary's binding** (its settings, window and adapter), the request still naming the unknown model (`MEASURED` P7; the last `return` of `_binding`). The reasoning flag is looked up by exact match while the binding is matched case-folded (`TRACED`, `complete_result`). `GAP-A6-02` |
| Retry / recovery | Non-Developer roles: the role chain in order, on a failed call or a schema failure (`call_with_escalation`). Developer: `AUTH-A6-07` |
| Checkpoint / resume | The resume fingerprint `model_runtime` binds every role model's exact runtime and settings digest; routes are replayed from the checkpoint or refused (`ROUTE_RESUME_MISMATCH`) (cited from the subsystem note, not re-read) |
| Audit emitted | `model.request.requested_model`, `model.result.reported_model` (absent when the response named none), `model.route` events when routing is on |
| Alternate paths | Override arguments of `complete_result` (`model_override`, `base_url_override`, `extra_body_override`, `temperature_override`): callers are the role and Developer choke points and the knowledge channels (lesson extraction uses the last Developer model, `stage_apply.py`) |
| Invariant | Executed inference identity equals the identity the binding declares; a request field Kriya owns is never replaced through `extra_body` |
| Negative control | Control `runtime_profile` and the three refusal sites of `PROVIDER_REQUEST_FIELD_CONFLICT` (cited symbols) |
| AS-IS evidence | `TRACED`; `MEASURED` P7 |
| Required contract | An unknown model name is a typed refusal (`GAP-A6-02`) |
| Limitation | `core/model_routing.py` was read at its entry points only; routing is off by default |

### AUTH-A6-02 - Which runtime adapter carries the call? (adapter identity)

| Field | AS-IS |
|---|---|
| Truth decided | The adapter (wire protocol) of a binding |
| Owner | `core/inference_runtime.py::runtime_for_binding` over the registry (`register_runtime_adapter`) |
| Why this owner | INF-001: one port, so no workflow or agent module names a provider, a transport or an SDK |
| Authoritative inputs | The binding's `inference_runtime` (`None` = the default adapter) |
| Evidence allowed | The registry content: `ollama` (default; OpenAI-compatible `/v1`, no per-request window, no schema-constrained output) and `ollama_native` (`/api/chat`, per-request window, `truncate: false`, schema-constrained output) (`MEASURED` P2) |
| Evidence forbidden | Name similarity: `vllm`, `openai` and `Ollama` (other case) are refused with `UnknownRuntimeAdapterError`, never served by the default (`MEASURED` P2) |
| Model role | None |
| Human role | Operator (`inference_runtime` is `SECURITY_AUTHORITY`) |
| Persisted state | The adapter's protocol version is part of the runtime fingerprint (`kriya-openai-compat/3`, `kriya-ollama-native/3`) |
| State transitions | None |
| Downstream consumers | `LLMClient._runtime`, the request plan (`request_plan`), qualification, doctor |
| Mutations authorized | None |
| Mutations forbidden | Registering an adapter outside the provider module (control `register_runtime_adapter(`: the port and `core/model_runtime.py` only) |
| Failure / UNKNOWN | Unregistered name: refused before any provider contact |
| Retry / recovery | None |
| Checkpoint / resume | Through the runtime fingerprint (`AUTH-A6-03`) |
| Audit emitted | `model.request.adapter`; `provider_contract` entry of each `CompletionResult` |
| Alternate paths | None found: `LLMClient` is the only caller of an adapter's `complete` / `complete_with_tools` outside qualification and doctor, which build a `ChatRequest` themselves (the same refusal applies at `ChatRequest.__post_init__`) |
| Invariant | A binding is served by the adapter it names or not at all |
| Negative control | Control `register_runtime_adapter(`; `tests/test_inf001_runtime_port.py` (layering, cited, not run) |
| AS-IS evidence | `TRACED`; `MEASURED` P2 |
| Required contract | None differing. The production runtime is the native adapter by owner ruling (section 42); nothing in the code makes the production profile require it (the profile requires what only the native adapter can pass: `AUTH-A6-06`) |
| Limitation | The native adapter's request and response code (`OllamaNativeRuntimeAdapter`) was read at its declaration only |

### AUTH-A6-03 - What exact runtime is this? (runtime identity)

| Field | AS-IS |
|---|---|
| Truth decided | The identity of the served model and runtime: `ModelRuntimeFingerprint` and whether it is `exact` |
| Owner | `core/model_runtime.py::probe_model_runtime` through `resolve_model_runtime` (cached per process and runtime input) |
| Why this owner | PRD-013: identity is what the endpoint reports, never what the configuration hopes |
| Authoritative inputs | The endpoint's own reports (version, model digest, weights blob, quantization, template and parser, tokenizer digest, normalized parameters, capabilities) plus Kriya-side inputs (capability profile, the window sent, the adapter protocol version) |
| Evidence allowed | Probe responses of a local endpoint |
| Evidence forbidden | The model name; timestamps; anything for a non-local endpoint (never probed under any egress policy) |
| Model role | None |
| Human role | None |
| Persisted state | `<state>/model_runtimes/<digest>.json` (`record_fingerprint`); `RunRecord.model_runtime_fingerprint_ids` (`control/run_coordinator.py::record_model_runtime_use`, exact fingerprints only) |
| State transitions | Missing components are `unavailable`; `exact` only with artifact digest and provider version |
| Downstream consumers | Qualification (`AUTH-A6-06`), role metrics, routing, the resume fingerprint, the release-candidate identity (`AUTH-A6-13`) |
| Mutations authorized | The fingerprint file |
| Mutations forbidden | - |
| Failure / UNKNOWN | A probe failure never fails the call: a non-exact fingerprint with `probe_errors` (`LLMClient._runtime_fingerprint`). Non-exact is never QUALIFIED |
| Retry / recovery | None (cached, including failures, for the process) |
| Checkpoint / resume | The resume fingerprint binds each role model's exact runtime (cited) |
| Audit emitted | `model.runtime` run event at run start (`workflow.py::_primary_model_runtime_details`: "Evidence only; never blocks a run"); the digest on every `CompletionResult` |
| Alternate paths | `KRIYA_MODEL_RUNTIME_PROBE=0` disables probing (the test suite's setting): every runtime is then non-exact. It is an environment variable, not an authority field |
| Invariant | Evidence binds to the exact runtime digest; the same weights under another provider or version are a different runtime |
| Negative control | - (identity producers are in one module by a committed tripwire, cited) |
| AS-IS evidence | `TRACED` |
| Required contract | None differing |
| Limitation | The probe itself was not run (live runtime frozen) |

### AUTH-A6-04 - Which context window is requested, served and budgeted? (capacity versus admission)

| Field | AS-IS |
|---|---|
| Truth decided | Three separate numbers: the REQUESTED window (runtime identity), the SERVED window (observation) and the BUDGET window (what Kriya admits) |
| Owner | `core/provider_contract.py::context_window_state` (dispatch check) and `budget_window` (the one sizing rule), fed by `model_runtime.py::requested_context_window` and `admission_ceiling` |
| Why this owner | MODEL-RUNTIME-CAPACITY-VS-ADMISSION-AUTHORITY-001 (2026-11-14): "served 65,536, budgeted 32,768" must be expressible without changing the runtime's identity |
| Authoritative inputs | Requested: an explicit provider option in `extra_body`, else the binding's `context_window`. Served: `observe_served_context_state` (`/api/ps`), else the served model's own configuration. Ceiling: the binding's `context_policy.max_context_tokens` |
| Evidence allowed | An observation with its provenance (`Provenance`) |
| Evidence forbidden | Seeding the served window from the request; a larger served window enlarging the budget (`MEASURED` P3: requested 65536, served 131072 gives budget 65536) |
| Model role | None |
| Human role | Operator sets window and ceiling; `kriya model pin` writes a derived model carrying the window |
| Persisted state | `provider_contract` entry of each result (`budget.context_state`) |
| State transitions | Served below requested: refused always (`SERVED_CONTEXT_BELOW_REQUESTED`). Served different from requested under the production profile: refused (`RUNTIME_CONTEXT_IDENTITY_MISMATCH`). Otherwise budget = min(requested, ceiling) (`MEASURED` P3: all four cases) |
| Downstream consumers | Dispatch (`LLMClient._dispatch_budget`), allocation (`context_budget.py::request_capacity`), routing (`model_routing._served_window`), the transition profile (`ModelRequestProfile.context_window`) |
| Mutations authorized | None |
| Mutations forbidden | The ceiling entering any identity: it is absent from the inference-settings identity (control), cleared in `qualification_config`, and not in the runtime fingerprint |
| Failure / UNKNOWN | Served window unobserved: outside production recorded as unverified; under production on an exact runtime that declares observation, a result whose window was not observed after the call is refused (`SERVED_CONTEXT_UNOBSERVABLE`) |
| Retry / recovery | One bounded observation retry (`SERVED_CONTEXT_OBSERVATION_ATTEMPTS`); nothing cached |
| Checkpoint / resume | The ceiling is in the `config` resume fingerprint (cited) |
| Audit emitted | `provider_contract` entry; `provider_contract.admission_miss` when admission passed and the provider refused |
| Alternate paths | The after-call identity check compares the observed window with the REQUESTED window (`llm.py::identity_window`), not with the budget (the defect fixed in `2abb5d6`, section 42) |
| Invariant | Requested is identity, the ceiling is controller policy; no sizing site uses a window above the budget window |
| Negative control | Absent `max_context_tokens` in `core/inference_settings.py`; `tests/test_model_runtime_capacity_vs_admission_001.py` (cited) |
| AS-IS evidence | `TRACED`; `MEASURED` P3; section 42 for the live values |
| Required contract | None differing. Reconciliation item "native Ollama 65K qualification": consistent with the code (`AUTH-A6-06`) |
| Limitation | The window of a real launch was not observed in this slice |

### AUTH-A6-05 - Does the request fit, and what may be left out? (dispatch budget and request fitting)

| Field | AS-IS |
|---|---|
| Truth decided | Whether one request may be sent, with which output budget and (adaptive policy only) which larger qualified window; before that, which optional sections a prompt keeps |
| Owner | Admission: `core/token_budget.py::plan_dispatch`, called inside `complete_result` / `complete_with_tools_result` before every request. Fitting: `workflow/context_budget.py::fit_developer_request` (Developer), `fit_planner_request` (Planner, Architect), `review_requests` (Reviewer) |
| Why this owner | PRD-016 / PROMPT-BUDGET-FIT: one counter for dispatch; a request whose fixed text is known sizes its variable sections against its own capacity |
| Authoritative inputs | The budget window (`AUTH-A6-04`), the binding's output budget, the qualified reasoning reserve, the counter (exact registered > qualified ratios > default bytes per token) |
| Evidence allowed | A grounded `OutputExpectation` (the size of a file being rewritten) may enlarge the output budget up to `max_output_tokens` |
| Evidence forbidden | A model's own estimate of its output; trimming mandatory text (goal, protocol, requirements, the error, skill conventions: never trimmed, so an oversized request is refused, not silently cut) |
| Model role | None |
| Human role | Operator: `llm.context_policy` (`SECURITY_AUTHORITY`) |
| Persisted state | `model.budget_expansion` and `context.request_fit` / `model.optional_context_reduced` run events |
| State transitions | Fits -> dispatch. Does not fit -> `ContextBudgetUnsatisfiableError` (`CONTEXT_BUDGET_UNSATISFIABLE`) or `OutputBudgetUnsatisfiableError` before inference; the Developer falls back once to an anchored patch on the output refusal |
| Downstream consumers | The attempt (a refusal is a non-candidate failure, `FR-A1` rows), the retry sub-machine |
| Mutations authorized | None |
| Mutations forbidden | - |
| Failure / UNKNOWN | Optional sections are dropped in a fixed order (planned source, siblings, graph, investigation, learned reference), each rebuilt smaller at most three times, else left out and recorded |
| Retry / recovery | The refusal does not open a new failure family (NON-CANDIDATE-FAILURE-SCOPED-RESET-001, `AUTH-A1-03`) |
| Checkpoint / resume | None |
| Audit emitted | The events above; `prompt.sections` evidence records with a digest per segment |
| Alternate paths | `context_budget.estimate_tokens` (`len // 4`) still drives context assembly, a second, coarser counter than the dispatch counter (stated in the subsystem note; by design) |
| Invariant | A request never reaches the provider above the budget window; mandatory text is never trimmed |
| Negative control | - |
| AS-IS evidence | `TRACED` at the entry points; the fitting internals are cited from the subsystem note |
| Required contract | None differing |
| Limitation | `context_budget.py` (about 1,200 lines) was read at its public functions only |

### AUTH-A6-06 - Is this runtime qualified for the role, and where does that decide anything? (qualification)

| Field | AS-IS |
|---|---|
| Truth decided | QUALIFIED / NOT_QUALIFIED / STALE / MISSING / RUNTIME_NOT_EXACT for (exact runtime, inference settings, policy digest), per required capability of a role |
| Owner | `core/model_qualification.py::assess` over records written by `kriya model qualify`; `required_capabilities(cfg, role, model)` |
| Why this owner | PRD-014: capability is measured on the exact identity, never inferred from a model name |
| Authoritative inputs | The record of the qualification identity (`inference_settings.py::qualification_identity`: runtime digest + settings digest), the policy version (`kriya-qualification/9` at the baseline) and policy digest, the execution environment for capacity evidence |
| Evidence allowed | Deterministic case results run by Kriya against the runtime |
| Evidence forbidden | Model prose; a record of another runtime digest, other settings or another policy (STALE); a record inside the workspace (`_refuse_inside_workspace`) |
| Model role | Subject of the test |
| Human role | Operator runs `kriya model qualify` |
| Persisted state | `~/.kriya/qualifications` (`KRIYA_QUALIFICATION_HOME`), outside every workspace |
| State transitions | A record is current only while runtime, settings, adapter version and policy digest all match |
| Downstream consumers | **Decides** in exactly these places (control `assess(`, `fallback_incompatibilities(`, `run_production_doctor(`): (1) Developer fallback under the production profile: a fallback that is not QUALIFIED is skipped (`model_transition.py::fallback_incompatibilities`); (2) the Developer retry identity under production (`generation_service.py::_require_qualified_retry_identity`); (3) routing eligibility when routing is on; (4) `doctor --production` (`model.qualification` FAIL unless every model of every role is QUALIFIED); (5) the release-candidate identity (`AUTH-A6-13`). **Records only** at run start (`workflow.py::_primary_model_runtime_details`) and in `kriya model status` |
| Mutations authorized | The record store, by `kriya model qualify` |
| Mutations forbidden | Qualification never executes model output on the host (cited) |
| Failure / UNKNOWN | MISSING, STALE and non-exact are recorded, never refused, outside production. **Under production the primary Developer model and every non-Developer role are not checked by the run at all**: `_enter_developer_model` evaluates the rule only when `is_fallback` (`TRACED`), and no other call site assesses a role's model during a run. `GAP-A6-01` |
| Retry / recovery | A skipped fallback is replaced by the next configured one that can serve; none left: `FALLBACK_MODEL_INCOMPATIBLE` |
| Checkpoint / resume | The offered context tiers and settings digests are bound into the resume fingerprint (cited) |
| Audit emitted | `model.runtime` (with `developer_qualification`), `model.fallback_selection`, `model.retry_identity_not_qualified` |
| Alternate paths | The production doctor is a separate command; no run invokes it (control `run_production_doctor(`; absent in `workflow.py`). The desktop reads no qualification (absent `model_qualification` under `kriya/application` and `kriya/kup`) |
| Invariant | A capability claim holds only for the exact identity that was measured |
| Negative control | The three controls named above |
| AS-IS evidence | `TRACED`. Reconciliation: section 42 records both 65K pins NOT_QUALIFIED on the OpenAI-compatible path at `over_context_refusal` (an 81,920-token prompt answered from 32,770 tokens: the runtime truncates silently) and QUALIFIED on the native adapter. The code agrees: every role requires `over_context_refusal`, and only the native adapter sends `truncate: false` (`MEASURED` P2 capabilities; cited note). "Compat truncation disqualification" is therefore a property of the default adapter, not a defect: with the default adapter no role can be QUALIFIED |
| Required contract | `GAP-A6-01` |
| Limitation | No qualification was run (frozen). The case implementations were not read |

### AUTH-A6-07 - May the Developer move to another model? (fallback and transition)

| Field | AS-IS |
|---|---|
| Truth decided | Which model the next Developer call goes to, and whether a configured fallback can serve the attempt |
| Owner | Escalation order: `attribution.resolve_fallback_model` (cited; the one formula). Compatibility: `workflow/model_transition.py::fallback_incompatibilities`, applied by `fallback_decision.py::_select_developer_fallback` (before the prompt is sized) and `_substitute_for_required_patch` (at the call), entered from `generation_service.py::_enter_developer_model` |
| Why this owner | PRD-017: everything a request depends on is resolved again for the model actually called (`ModelRequestProfile`) |
| Authoritative inputs | The configured chain order; the profile of the candidate (exact runtime, Developer qualification, capability profile, edit protocol, windows, own output budget); the files the attempt may only patch |
| Evidence allowed | A FAILED Developer qualification case; production without QUALIFIED; a whole-file-only profile against a patch-only attempt; no prompt room |
| Evidence forbidden | Model size or name as a capability claim; metrics; reordering by preference |
| Model role | None |
| Human role | Operator configures the chain |
| Persisted state | `GenerationState.incompatible_fallbacks` (run-wide skips), `last_developer_request_profile`; neither is in a checkpoint (`AUTH-A1-09`) |
| State transitions | `SM-A6-04` |
| Downstream consumers | The retry sub-machine (`SM-A1-01`); the no-progress fingerprint keys on the profile digest |
| Mutations authorized | None |
| Mutations forbidden | A fallback inheriting the primary's sampling settings or output override (`binding_output_tokens`) |
| Failure / UNKNOWN | No remaining fallback can serve: terminal `fallback_incompatible` before any request |
| Retry / recovery | Bounded by the scoped budgets of `AUTH-A1-06` |
| Checkpoint / resume | Not persisted: a resumed unit starts again on the primary (consequence of `GAP-A1-02`) |
| Audit emitted | `model.transition` (field-by-field changes), `model.fallback_selection`; evidence `fallback.decision` |
| Alternate paths | Non-Developer roles escalate through `call_with_escalation` with no compatibility or qualification check (their chain is tried in order) |
| Invariant | A request is sized, shaped and attributed for the model that receives it |
| Negative control | Control `fallback_incompatibilities(` |
| AS-IS evidence | `TRACED` |
| Required contract | None differing, apart from `GAP-A6-01` (the primary) |
| Limitation | `attribution.resolve_fallback_model` and triage were not re-read (slice A1 covered the retry modes) |

### AUTH-A6-08 - Which role made the call, and must roles be independent? (role attribution and independence)

| Field | AS-IS |
|---|---|
| Truth decided | (a) the role a call is counted under; (b) whether verifier roles must run on a runtime other than the Developer's |
| Owner | (a) `core/role_metrics.py::model_role` (a context variable set by `call_with_escalation`, the Developer choke point, the gate orchestrator's self-correction entry and localization); (b) `model_policy.independent_roles`, enforced by `cli.py::_workflow_config` (`ROLE_INDEPENDENCE_REQUIRED`) and reported by the doctor |
| Why this owner | PRD-018: one shared client, so attribution is by scope, never guessed |
| Authoritative inputs | (a) the active scope; (b) the exact runtime digests of the roles (`role_runtimes`) |
| Evidence allowed | Exact runtime identity |
| Evidence forbidden | A second model's opinion as verification evidence (`second_opinion_is_verification: False` in both records) |
| Model role | None |
| Human role | Operator sets `independent_roles` (`SECURITY_AUTHORITY`); default is the empty list |
| Persisted state | `model.role_independence` (run start) and `model.role_metrics` run events; `kriya model metrics` aggregates the history database |
| State transitions | None |
| Downstream consumers | Routing (reads the candidate's own identity rows), the operator |
| Mutations authorized | The routing table, only by `kriya model metrics --write-table` (runs never write it) |
| Mutations forbidden | Metrics deciding anything during a run |
| Failure / UNKNOWN | A call outside any scope is `unattributed`. Producers found: every call of the knowledge channels (absent `model_role` under `kriya/knowledge`), among them the lesson extraction after a retried success, which calls the last Developer model, a fallback when the fallback fixed it (`stage_apply.py`, `knowledge/channels/live_failure.py`) |
| Retry / recovery | None |
| Checkpoint / resume | None |
| Audit emitted | The two events |
| Alternate paths | With the default policy (no required roles) the doctor reports shared runtimes as WARN and the run proceeds: the accepted warning `models.role_independence` of section 42 |
| Invariant | Metrics are observations; independence is enforced only when the operator requires it |
| Negative control | Control `model_role(` (seven modules); absent under `kriya/knowledge` |
| AS-IS evidence | `TRACED`. That the `unattributed` bucket measured in PRD-035 attempt 2 / C6 is the lesson-extraction call is `INFERRED` (the artifact was not opened) |
| Required contract | None differing. Reconciliation "role independence warning": intentional current contract |
| Limitation | - |

### AUTH-A6-09 - What is in a Developer request, and which parts are trusted? (STAGE-GENERATION, trust boundary)

| Field | AS-IS |
|---|---|
| Truth decided | The exact text of the two messages a Developer call sends |
| Owner | `agents/agent.py::DeveloperAgent._fill_missing_content` (one request per file) and `run_generation` (file-list request, single-stage fallback), fed by `workflow/attempt.py` (first attempt and retries) and `workflow/retry_prompts.py` |
| Why this owner | Stable blocks first for prefix reuse; the per-file instruction last |
| Authoritative inputs | See the layout below |
| Evidence allowed | T-KRIYA, T-USER, T-REPO, T-VALIDATOR; T-MODEL as approach (the system prompt states that the Planned Implementation Strategy is "not itself part of the requirement") |
| Evidence forbidden | By the cross-cutting rule: learned or retrieved text outside the fence. **Not enforced for text fetched during the run** |
| Model role | Candidate generator |
| Human role | None at this point |
| Persisted state | Evidence `model.request` (the exact messages as a content blob under capture `full`), `prompt.sections` |
| State transitions | None |
| Downstream consumers | The provider; the candidate (`AUTH-A3-01`) |
| Mutations authorized | None (the answer becomes a candidate through the write gate, `AUTH-A5-04`) |
| Mutations forbidden | - |
| Failure / UNKNOWN | An unfit request: `AUTH-A6-05`. A truncated answer is refused (`OUTPUT_TRUNCATED`) |
| Retry / recovery | The retry builders re-compose the task description with the previous error |
| Checkpoint / resume | The request is rebuilt; `state.error_context` is not persisted |
| Audit emitted | The two evidence kinds; `retry.delta` |
| Alternate paths | The Reviewer, the lesson extraction and the targeted-test selection read the same error context (rows of the table below) |
| Invariant | Model and retrieved text is evidence, never authority; untrusted text is fenced |
| Negative control | Absent `fence_untrusted_reference` in `live_lookup.py`, `retry_strategy.py`, `retry_package.py`, `retry_prompts.py`, `repair_orchestrator.py`, `stage_review.py`; absent `unresolved_skill_gap` and `check_goal` in `agents/agent.py` and `generation_service.py` |
| AS-IS evidence | `TRACED`; `MEASURED` P1 |
| Required contract | `GAP-A4-04` (completed below), `GAP-A4-03` |
| Limitation | The per-file request has more than one instruction variant (protocol modes); the layout is the same in each. `attempt.py` was read at the retry call sites only |

Layout of one per-file Developer request (`TRACED`, `_fill_missing_content`):

| Message | Part, in order | Class | Source |
|---|---|---|---|
| system | Mode and response protocol for one file | T-KRIYA | `file_sys_prompt` |
| user | `=== Existing Code Base Context ===` | mixed | `existing_code_context` = skill conventions (T-SKILL: "Rules" trusted, "Unverified Rules" labelled) + graph context of the project's files (T-REPO, unfenced by design: repository evidence) + the fenced learned reference (T-REFERENCE) (`stage_analysis.py`), then retry projections of the current files |
| user | `=== Architecture Design ===` | T-MODEL | Architect |
| user | `=== Task ===` | mixed | Goal (T-USER), plan (T-MODEL), contract blocks (T-KRIYA), then on a retry `=== Previous Error to Fix ===` (T-VALIDATOR **plus T-EXTERNAL when the live lookup ran**) |
| user | Sibling files (optional section) | T-REPO | - |
| user | Directive, verification reminder, skill reminder, authority reminder, source-location block, fix-analysis instruction and scaffolds | T-KRIYA, the scaffolds derived from the error text | `prompt_tail` |

Where text fetched by the live lookup goes after `retry_strategy.py` appended it to `state.error_context`
(`MEASURED` P1 unless marked; this completes the trace the owner required for `GAP-A4-04`):

| Step | What happens to the page text | Trust label at that point |
|---|---|---|
| 1. Fetch | Up to 2,000 characters per term, appended under `=== Reference material found for '<term>' (from <url>) - ... verify before relying on it ===` | A plain banner naming the URL. No fence, and markers inside the text are kept verbatim: a page can carry the fence END marker or its own `=== Task ===` heading |
| 2. Developer task, no retry package | The whole error context is the `=== Previous Error to Fix ===` body | Same banner, inside the section that carries validator output |
| 3. Developer task, retry package | `repair_orchestrator.py` slices the text from the banner on into `advisory_context`; `RetryPackage.render_error` prints it under "Advisory reference evidence (verify against local validators):" | Labelled advisory; still unfenced; not in `prior_error_text()` |
| 4. Kriya-authored instructions | Without a retry package the error context is `prior_error_context`; the incompatible-types and buffer-capacity scaffolds are built from it by pattern match. A page line shaped like a compiler message produces scaffold text | The scaffold is T-KRIYA text: the page's content now speaks as Kriya |
| 5. Gate selection | `gate_orchestrator.py` passes `state.error_context` to `extract_target_test`; a test file name in the page text selects that test as the targeted run (raw error: suite; augmented: `tests/test_b.py`) | None: read as validator evidence. A targeted run that executes no test falls back to the suite (`TRACED`), so the effect is bounded to which test runs first |
| 6. Reviewer, rejected candidate | `stage_review.py` passes `state.error_context` as `terminal_reason`; `rejected_candidate_system_prompt` places it in the **system** message inside the block headed "AUTHORITATIVE RUN DISPOSITION (deterministic control-plane fact, not your own assessment)", and again in the user message as "Last quality gate error" | Presented as a control-plane fact. The Reviewer's answer is advisory text shown to the person (`AUTH-A3-12`) |
| 7. Lesson extraction | After a retried success `stage_apply.py` gives `state.error_context` to `LiveFailureChannel.extract`; the extracted facts are staged as learned knowledge (`TRACED`) | None. Staged facts are unverified until promoted (slice A4); how a staged fact is later rendered was not re-read |
| 8. Provider payload | `[{"role": "system"}, {"role": "user"}]`; no field carries provenance | Headings only |

Answers to the owner's four questions: (1) at dispatch the external text is not tagged as untrusted: it has a banner
but not the fence, and the fence is the only mechanism the code defines for "reference, never instruction"; (2) it can
become instruction-like trusted content in two ways: Kriya-authored scaffold text derived from it (step 4) and the
Reviewer's system prompt (step 6); (3) the transformations do strip what provenance there was: the slice in step 3
keeps the banner, the uses in steps 4 to 7 read the string as if it were validator output; (4) it influences one
deterministic decision, the targeted-test selection (step 5), and no write decision: the write gate, the candidate
and the gates never read it. Reach is unchanged from A4: web lookup is off by default and in the Project Setup
profile, the production profile seals offline mode, and each query needs an approval.

### AUTH-A6-10 - How long may a call and a run take? (deadline)

| Field | AS-IS |
|---|---|
| Truth decided | The deadline of one model call |
| Owner | `core/llm.py::LLMClient._deadline` over the run's generation clock (`control/run_coordinator.py::claim_run_generation_clock`) |
| Why this owner | PROVIDER-CONTRACT-001A: one clock, so a stream cannot outlive the budget |
| Authoritative inputs | `autonomy.generation_time_budget_seconds` (`AUTH-A5-12`), narrowed by `inference_deadline()` |
| Evidence allowed | The monotonic clock |
| Evidence forbidden | - |
| Model role | None |
| Human role | None |
| Persisted state | `provider_contract.deadline` on each result |
| State transitions | Exhausted before contact: `INFERENCE_DEADLINE_EXHAUSTED`; mid-request: `INFERENCE_DEADLINE_EXCEEDED`; neither is resent or escalated |
| Downstream consumers | The attempt (`time_budget_exhausted` stop, `AUTH-A1-08`); the final review (typed refusal) |
| Mutations authorized | None |
| Mutations forbidden | - |
| Failure / UNKNOWN | No budget configured: `deadline_bound=false` |
| Retry / recovery | None |
| Checkpoint / resume | The clock is per unit invocation under enforce (`AUTH-A1-08`, `GAP-A5-04`) |
| Audit emitted | The deadline record |
| Alternate paths | The budget is not part of any model identity: absent from the inference-settings identity, the qualification identity guard, the qualification record module and the release-candidate module (four controls). So the 2,400 s of the scenario template and the 3,600 s production ceiling are configuration values only; no qualification or certification is tied to either |
| Invariant | A call never outlives the run's clock |
| Negative control | Absent `generation_time_budget` in the four identity modules |
| AS-IS evidence | `TRACED` |
| Required contract | The run-level owner of `GAP-A5-04` (Phase C, with the A1 allowance contract) |
| Limitation | - |

### AUTH-A6-11 - What is recorded about a run, and which record is the truth? (evidence persistence, audit truth)

| Field | AS-IS |
|---|---|
| Truth decided | Nothing: these are records. The row states which record holds which fact, who writes it and what a reader may conclude |
| Owner | Four writers, one per record: see the table below |
| Why this owner | Each record has its own purpose (control, history, attempt evidence, log); only the RunRecord takes part in decisions |
| Authoritative inputs | The run's own state at the moment of writing |
| Evidence allowed | A reader may conclude: from the RunRecord, the lifecycle and commit result; from a VERIFIED evidence store, that the file is internally consistent and complete to its seal |
| Evidence forbidden | Reading a history row or an evidence store as authenticated: neither carries a key or a binding outside itself (`MEASURED` P4, P5) |
| Model role | None (model text is recorded as content, with provenance `MODEL_CLAIMED` where a record carries a model's claim) |
| Human role | None |
| Persisted state | The four records |
| State transitions | Evidence store: `SM-A6-03` |
| Downstream consumers | Recovery and resume (RunRecord); the desktop history and inspector (history row, evidence store, run log); metrics (history rows, RunRecords, adjudications); the release tooling |
| Mutations authorized | Each writer appends or replaces its own record |
| Mutations forbidden | The recorder changing request bytes, the workspace, a result or a retry decision (invariant I-2, `tests/test_lr_r1_m1_equivalence.py`, cited) |
| Failure / UNKNOWN | History row: a write failure is logged and the run's result is unchanged (`run_lifecycle.py::persist_run_trace`: "Never raises"). Evidence store: cannot open = `RECORDER_UNAVAILABLE`, the run continues; a failed record is a counted gap and the seal says `complete: false`. Run log: cannot open = reported and skipped. RunRecord: the terminal transition at run exit is attempted in a `finally`; a failure to persist it is logged (`"terminal record not persisted"`) and the next run's commit-state assessment treats the record as needing recovery |
| Retry / recovery | Only the RunRecord has recovery (`kriya recover`, slice A3) |
| Checkpoint / resume | The evidence store of a resumed run is the same run id's store when the id is kept (open question for Phase B, handed on by A3) |
| Audit emitted | - |
| Alternate paths | `evidence.capture: off` or an absent `evidence` configuration disables the store; the `evidence` section is excluded from the configuration fingerprint |
| Invariant | A record never decides; the control record is the only durable truth of a run's outcome |
| Negative control | Absent `hmac` and `private_key` under `kriya/core/attempt_evidence`; absent `workspace` in `control/trace.py`; listing `.log_run(` |
| AS-IS evidence | `TRACED`; `MEASURED` P4, P5 |
| Required contract | `GAP-A6-03` |
| Limitation | `control/persistence.py` (RunRecord storage) was not re-read; slice A3 owns it |

| Record | Location | Writer | Written when | Integrity | Failure of the write |
|---|---|---|---|---|---|
| RunRecord | `<workspace>/.kriya/` control state, keyed by workspace identity | `control/run_coordinator.py` (`begin_mutating_run`, `_fail_active_run`), `control/run_record.py` transitions | Run start, each stage marker, commit begin and settle, terminal | Revisioned compare-and-set with ownership metadata (`AUTH-A5-01`) | Raises inside the run; at exit logged, then recovery |
| History row | `<state>/traces.db`, table `runs`, one database for all projects | `control/trace.py::TraceLogger.log_run` (callers: listing `.log_run(`) | At each terminal and early exit; `INSERT OR REPLACE` on the run id | None: no workspace, digest or writer column (`MEASURED` P5); every column redacted before storage | Logged; the result is unchanged |
| Attempt evidence | `<state>/attempt-evidence/<run id>/` | `core/attempt_evidence/writer.py` through `scope.py` | Every model call, parse, candidate change, gate, decision; sealed at run close after the RunRecord's terminal transition | Hash chain and seal: an edit of one record is `CHAIN_BROKEN`; a consistent rewrite of all records and the seal is `VERIFIED` (`MEASURED` P4) | Gap, degraded seal, or unavailable; the run continues |
| Run log | `<log dir>/runs/<run id>/kriya.log` (default `~/.kriya/logs`, a location separate from the state directory) | `core/logging_setup.py::run_log` | For the whole run | None; redacting formatter; created with the process's default file mode (`MEASURED` on one real file: `0644`, in a directory under a `0700` parent) | Reported and skipped |

### AUTH-A6-12 - What does the desktop show as the outcome? (final evidence assembly, desktop visibility)

| Field | AS-IS |
|---|---|
| Truth decided | Nothing: a display projection. The row states what each line of the desktop result is derived from |
| Owner | `application/history.py::HistoryService` (list, detail, `application_outcome`, `_truth_of_events`), `application/evidence.py::EvidenceService` (verified pages of the evidence store), `application/run_log.py::read_run_log`, `application/run.py` (`launch.status`: launch record, RunRecord, evidence store and history row) |
| Why this owner | The application layer reads durable records only; "this projection reads it and never decides it" (`_exit_is_acceptable`) |
| Authoritative inputs | The history row (status, gate outcomes, run events), the last `requirement.truth` event, the evidence store, the RunRecord for a launch's status |
| Evidence allowed | A recorded gate outcome with its own `runtime_disposition`; the closed truth vocabulary |
| Evidence forbidden | Inferring a phase from message text; turning a missing or malformed truth into a verified one (`_truth_of_events` returns `None`) |
| Model role | None |
| Human role | Reader |
| Persisted state | None of its own (history snapshots are copies) |
| State transitions | None |
| Downstream consumers | The cockpit through KUP (`kup/evidence_ops.py`, `kup/run_log_ops.py`) |
| Mutations authorized | None, except `mark_in_progress`, which overwrites a transient knowledge-gap row with `in_progress` |
| Mutations forbidden | - |
| Failure / UNKNOWN | No gate outcomes: `UNKNOWN`. No runtime execution: `NOT_APPLICABLE`. An evidence page is served only from a chain that verified in that read |
| Retry / recovery | None |
| Checkpoint / resume | None |
| Audit emitted | None |
| Alternate paths | "Application ran and succeeded" is recomputed by the projection from the newest runtime gate (success, acceptable exit, not timed out). It reads the owner's recorded disposition: a non-zero exit is acceptable only with `EXPECTED_NONZERO_EXIT_GROUNDED` and `final == PASS` (`MEASURED` P6). The unit and run status themselves are the stored `status` column (`AUTH-A3-10`, `AUTH-A3-11`) |
| Invariant | The desktop shows recorded facts with their provenance; a missing fact is shown as missing |
| Negative control | Absent `model_qualification` under `kriya/application` and `kriya/kup` (the desktop shows no qualification state) |
| AS-IS evidence | `TRACED`; `MEASURED` P6 |
| Required contract | None differing for the projection. The statement of what "verified" means on screen follows `GAP-A6-03` |
| Limitation | The UI code (`ui/`) was not read beyond locating the run-log consumer; what the cockpit words each state as is slice A3's `GAP-A3-01` concern |

### AUTH-A6-13 - Which Kriya build ran, and what identity does a release certify? (source identity, certification)

| Field | AS-IS |
|---|---|
| Truth decided | (a) the Kriya build of a run; (b) the identity a live certification and a release streak are keyed by |
| Owner | (a) `foundation/build_info.py::version_report` plus `control/source_identity.py::run_source_identity`, recorded by `attempt_evidence/scope.py::_run_manifest`; (b) `core/model_certification.py` (`CertificationKey`, `record_trial`) and `core/release_candidate.py::release_candidate_identity` |
| Why this owner | KRIYA-VERSION-001, RUN-SOURCE-IDENTITY-001, PRD-035 / PRD-036: evidence says precisely which revision ran |
| Authoritative inputs | (a) the embedded build info of a package, or for a source checkout its exact HEAD and measured dirty flag; (b) runtime digest, settings digest, environment digest and case-set version; for a release candidate the release identity, the operator production configuration, every role's model identity and the case set |
| Evidence allowed | Deterministic case evidence; every bound model identity exact and QUALIFIED |
| Evidence forbidden | A model name; prose; a tree hash that was not read (`tree` is recorded as unknown for a checkout, never inferred) |
| Model role | Subject |
| Human role | Operator runs the matrix and the release tooling (`scripts/`, not read) |
| Persisted state | The evidence manifest (`kriya_build`, workspace identity, base revision and tree); the certification store (`KRIYA_CERTIFICATION_HOME`), outside every workspace; the sealed streak log |
| State transitions | Certification: CURRENT / FAILED / STALE / INVALID / MISSING. Streak: reset by anything but a full matrix on the exact candidate |
| Downstream consumers | `kriya model certification`, the release gate |
| Mutations authorized | The certification and streak stores |
| Mutations forbidden | - |
| Failure / UNKNOWN | Source identity unavailable: the build report plus `source_identity_unavailable: <reason code>` |
| Retry / recovery | None |
| Checkpoint / resume | None |
| Audit emitted | The manifest; the trial log |
| Alternate paths | No run consults a certification or a streak: they gate a release, not a launch (no reader under `kriya/workflow`; `TRACED` by the module's callers) |
| Invariant | A certification holds for the exact identity only; any drift is STALE |
| Negative control | Absent `generation_time_budget` in `core/release_candidate.py` (the module names no budget; the operator configuration file as a whole is part of the candidate identity) |
| AS-IS evidence | `TRACED` at the module level |
| Required contract | None differing |
| Limitation | The certification matrix, the release scripts and the hosted certification were not read or run |

### How the A6 trace list maps to rows

| Owner's trace item | Row |
|---|---|
| Provider selection, model binding | `AUTH-A6-01` |
| Inference adapter identity, native Ollama | `AUTH-A6-02` |
| Runtime identity | `AUTH-A6-03` |
| Requested versus served context, admission ceiling | `AUTH-A6-04`, `AUTH-A6-05` |
| Qualification identity, role qualification | `AUTH-A6-06` |
| Fallback | `AUTH-A6-07`, `SM-A6-04` |
| Role independence | `AUTH-A6-08` |
| Developer request, trust boundary (`GAP-A4-04`, `GAP-A4-03`, `BYP-A4-04`) | `AUTH-A6-09` |
| Runtime verification | Owned by slice A3 (`AUTH-A3-07`, `AUTH-A3-08`); A6 adds the evidence side: `AUTH-A6-11`, `AUTH-A6-12` |
| Evidence persistence, audit truth | `AUTH-A6-11`, `SM-A6-03`, `GAP-A6-03` |
| Package and source identity | `AUTH-A6-13` |
| Final evidence assembly, desktop visibility | `AUTH-A6-12` |
| vLLM and Skills/Tools seams | `05-INTEGRATION-BYPASS-MAP.md`, "Extensibility seams" |
| Reconciliation: 65K native qualification, compat truncation, role-independence warning, precision boundary, class-D errors | `AUTH-A6-04`, `AUTH-A6-06`, `AUTH-A6-08`, gap ledger "Reconciliation items" |
