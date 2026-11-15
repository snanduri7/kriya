# 03 - Mutation authority map

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS. All paths are under `kriya/workflow/` unless stated.

Columns: who asks for the mutation, which decision authorizes it, who performs it, whether it is transactional,
how it is undone, and what audit record it leaves.

## Slice A1 - retry state, checkpoints, control state

Workspace, source, test, plan, model-binding, approval, evidence and final-result mutations are mapped by slices
A3 to A6. Every A1 mutation below changes in-memory run state or Kriya's own control files, never the workspace.

| ID | Mutation | Caller | Authority | Mutation owner | Transaction | Rollback | Audit | Label |
|---|---|---|---|---|---|---|---|---|
| MUT-A1-01 | Open-finding set replaced | `retry_strategy._record_attempt_failure` | `open_findings.replaces_open_finding` (`AUTH-A1-01`) | `open_findings.update_open_findings` | None (one assignment) | Emptied by `best_of_n.reset_state_for_independent_candidate` | The failure itself is a ledger entry and an `attempt.failed` event. No event says a finding was opened, kept or replaced | `TRACED` |
| MUT-A1-02 | Scoped budgets reset (targeted counter to 0, fallback-targeted flag cleared) | `_record_attempt_failure` | `retry_policy.observe_failure_family` (`AUTH-A1-03`) | `retry_policy.reset_scoped_budgets_for_new_family` | None | None | A log line only ("Quality Gates surfaced a new failure family"). No run event and no evidence record | `TRACED` |
| MUT-A1-03 | Failed attempt charged | `_record_attempt_failure` | `retry_policy.charge_failed_attempt` (`AUTH-A1-06`) | Same | None | None | No per-attempt record of the counter values. The counters appear in the next failure's log banner; the end-of-run metrics (`state.GenerationState.generation_metrics`) hold attempts started and failed by mode | `TRACED` |
| MUT-A1-04 | Fallback one-shot consumed; fallback use counted | `attempt.run_attempt`, at the start of a fallback-targeted or escalated full-set attempt | The admitted mode (`AUTH-A1-05`) | `attempt.run_attempt` | None | None | The fallback selection is recorded (`fallback.decision` evidence, run events) | `TRACED` |
| MUT-A1-05 | Strategy transition forced (targeted budget closed, fallback-targeted requested) | `_record_attempt_failure` | The no-progress count (`AUTH-A1-04`) | `retry_policy.force_strategy_transition` | None | None | Run event `retry.strategy_transition` | `TRACED` |
| MUT-A1-06 | Progress counters and vector history updated | `_record_attempt_failure` | `retry_progress.classify_progress` | `retry_strategy.record_workspace_progress` | None | Zeroed by the Best-of-N reset | Run events `retry.progress_vector`, `retry.no_progress_terminal` | `TRACED` |
| MUT-A1-07 | Attempt number incremented | `attempt.run_attempt`; `gate_orchestrator` (verification-only attempt) | The loop's admission (`AUTH-A1-05`) | Those two functions | None | Never reset, including by Best-of-N | Attempt-evidence scope and every event carry the number | `TRACED` |
| MUT-A1-08 | State reset for an independent Best-of-N candidate | `best_of_n.run_attempt_with_best_of_n` | Configuration `autonomy.best_of_n_first_attempt` > 1, first attempt, sandbox present (`attempt_lifecycle.plan_attempt`) | `best_of_n.reset_state_for_independent_candidate` | None | None | `best_of_n_candidates_tried` counts discarded candidates | `TRACED` |
| MUT-A1-09 | API-contract recovery counter incremented on a phase advance | `attempt_lifecycle.run_iteration` | A verified recovery phase transition | `attempt_lifecycle.record_phase_advanced` | None | None | Run event `api_contract_recovery.phase_advanced` | `TRACED` |
| MUT-A1-10 | Fallback-targeted requested after the primary rejected an authoritative target | Candidate staging | Preserved files, targeted mode, fallback available, flag unused | `candidate_transaction._request_fallback_for_rejected_authoritative_target` | None | Cleared by a new family or when the attempt starts | None found | `TRACED` |
| MUT-A1-11 | Stop flags set (`environment_failure`, `plan_scope_conflict`, `no_progress_terminated`) | Failure recording and several gates | The typed failure or the attribution result | `environment_failure`: `retry_strategy`, `recovery_coordinator`, `stage_apply`, `workflow`. `plan_scope_conflict`: `retry_strategy`, `attempt`, `candidate_transaction`. `no_progress_terminated`: `retry_strategy` only | None | Best-of-N clears `no_progress_terminated` only | `recovery.decision` evidence; typed stop reason when present | `TRACED` (writers found by search; each site not read) |
| MUT-A1-12 | Stage checkpoint written | Stage code (`stage_planning`, `stage_architecture`, `development_loop`) | Reaching the stage; skipped entirely during a verifier repair | `workflow.py` `_save_stage_checkpoint` -> `kriya/control/checkpoint.py::save_checkpoint` | Single file write; a failure is swallowed and the checkpoint is then simply not resumable | Overwritten by the next stage of the same run id | The checkpoint file | `TRACED` |
| MUT-A1-13 | Enforce control state written | `enforce_loop` at each subtask transition | The loop's own subtask state transition | `kriya/control/persistence.py::save_control_state` (optionally durable, with a reload-and-compare check) | Single file | Overwritten | The control-state file | `TRACED` (owner located; write path not read in A1, belongs to A5) |
| MUT-A1-14 | Retry counters copied to the run record | `stage_apply`, at terminal apply (success path only) | Reaching terminal apply | `annotate_run` | Part of the run-record annotation | None | Run record field `retry_counters` and `retry_state_reference`. No reader in `kriya/` | `TRACED` |

### Slice A1b additions (`TRACED`, `workflow/enforce_loop.py` read line by line)

| ID | Mutation | Caller | Authority | Mutation owner | Transaction | Rollback | Audit | Label |
|---|---|---|---|---|---|---|---|---|
| MUT-A1-15 | Dependents of a reopened owner set to `pending`; completed ones lose their result and are queued for re-entry | The owner-recovery loop and the verification-owner episode, before the owner runs | The recovery execution plan (owner resolved for each implicated artifact) or the nearest-owner resolver | `_invalidate_dependents` (the one invalidation of the loop), then `_persist_control_state` | In-memory change followed by one plan-document save and one control-state save | `_restore_invalidation`, only when no byte of the owner changed | `plan_recovery_events` on the run result (`invalidated_subtasks`); log lines | `TRACED`; `MEASURED` by `tests/test_enforce_recovery_convergence_001.py` (two of its tests run in A1b) |
| MUT-A1-16 | Stage state of an owner set to `in_progress`, then `completed` or `needs_review` | The owner-recovery loop | The plan-level outcome of the cycle (the consumer step's gates), never the owner's own gates alone | The loop body; `record_committed_effects` for the written-file set | Plan-document save and control-state save per transition | None | `plan_recovery_events`; `OWNER_RECOVERY_COMPLETED` log line | `TRACED` |
| MUT-A1-17 | Sandbox completions discarded when the plan does not commit (`completed` becomes `pending`, written files emptied) | The terminal block of `EnforceLoop.run`, on any outcome that is not a completed commit | `gate_report.commit_eligible` and the commit result | `_discard_sandbox_completions` | The plan's stage states and the control state change together | None. `_adopt_sandbox_completions` is the opposite transition after a durable commit | The control-state file; the run result | `TRACED`; `MEASURED` by the sandbox resume tests cited in `AUTH-A1-10` |
| MUT-A1-18 | Cross-owner requirement recorded and later marked satisfied in the run's obligation ledger | The owner-recovery loop | The scope conflict's evidence; satisfaction only when the whole recovery plan is accepted | `owner_recovery._get_or_create_cross_owner_obligation`; `obligation_ledger.record` | None | None | In memory only; reaches a unit's stage checkpoint as the effective-ledger snapshot | `TRACED` |
| MUT-A1-19 | Enforce recovery counters and episode state (`AUTH-A1-14`) | The loop body | The loop's own transitions | The loop body (locals) | None | None | None, except what `plan_recovery_events` reports | `TRACED` |

### Observations

- **No retry-state mutation is transactional or persisted.** That is consistent with the design statement in
  `retry_policy.py` ("pure retry-state reduction"), and it is the direct cause of `GAP-A1-02`.
- **The two mutations that grant or consume allowance leave the weakest audit trail.** A reset (`MUT-A1-02`) is a
  log line; a charge (`MUT-A1-03`) has no per-attempt record. Search of `kriya/core/attempt_evidence`,
  `kriya/metrics`, `kriya/kup`, `run_events.py`, `run_trace.py` and `decision_evidence.py` for
  `targeted_retry_count`, `fallback_targeted_attempted` and `retry_count` returned nothing. What a run does record
  is each attempt's mode and, at the end, attempts started and failed by mode and the progress summary
  (`GenerationState.generation_metrics`, `retry_progress_summary`). The counter values and the resets between
  attempts are not recorded, so run 10's budget trajectory had to be reconstructed by a model-free reproducer
  (sprint log 46). `GAP-A1-03`.
