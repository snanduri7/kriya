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
| UNKNOWN behaviour | A failure type the category table does not know is treated as a candidate finding and kept (remembered, not forgotten) |
| Label | `TRACED`; `MEASURED` by `tests/test_non_candidate_failure_scoped_reset_001.py` (9 passed on this baseline, 2026-11-15) and probe P7 |

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
| Limit | `max(3, autonomy.max_consecutive_no_progress_attempts)`; the config default is 2, so a configured 1 or 2 is read as 3 |
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
| Unit behaviour | Only `completed` is skipped. Any other subtask is invoked again from the start. The enforce loop passes no `resume` flag to a unit (negative control: the only callers passing `resume=` to `run_generation_workflow` are `cli.py` and `workflow/milestones.py`) |
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
| Bounds found | Scope-revision loop: 3 passes per stage. Owner-recovery loop: 3 cycles per stage. Convergence cap: `len(plan.subtasks) * 3` owner-recovery cycles per run. One owner reopen per verification-only unit. Per obligation: prior attempts counted from the obligation ledger's history |
| Relation to unit budgets | Independent. Each re-invocation gets a full unit ceiling (`AUTH-A1-07`) |
| Persistence | Memory only, except the obligation ledger, whose persistence is not traced in A1 (`UNKNOWN`, slice A3) |
| Label | `TRACED` (bounds located by search; the loops were not read line by line in A1) |

### AUTH-A1-12 - "The same failure" has four definitions

| Definition | Owner | Form | Used for |
|---|---|---|---|
| Failure family | `failure_grounding.build_failure_signature` | Normalised text, line numbers removed | Scoped allowance, repeat detection, live-lookup trigger, deterministic-failure diagnostic |
| Failure identity | `workflow/verifier_replay.py::failure_identity` | sha256 of type + exact gate output | Enforce-level comparison of a dependent's failure before and after an owner repair or a replay |
| Progress-vector failure dimension | `retry_progress.build_progress_vector` | Hash of the family signature | No-progress classification |
| Open finding | `open_findings.finding_from_failure` | A copy of the failure, no identity | The model-facing "still unresolved" block and carried source lines |

None of the four is declared the authority for blocker continuity. They can disagree: a one-line shift keeps the
family and changes the exact identity and the finding. `GAP-A1-01`. Label: `TRACED` + `MEASURED` (probe P2, P5).
