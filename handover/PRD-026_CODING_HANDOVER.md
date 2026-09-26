# PRD-026 Coding Agent Handover: Universal Retry Progress Invariant

## Status
READY_FOR_PYTEST_VERIFICATION. This is part of the Batch 6 stop.

## Source identity
- Base revision: the PRD-025 commit (83a80fd).
- Final revision: the PRD-026 commit (see `git log --grep PRD-026`).
- Directives: handover/BATCH6_DIRECTIVES.md, the PRD-026 section.

## Scope implemented
- All five requirements, with the user's corrections:
  - SAMPLING_RESAMPLE is a typed allowance, not progress;
  - seen-vector cycle detection;
  - API recovery brought under the same rule with its hard maximum kept.
- **Audit result** (requirement 4: prove, don't reimplement):
  - Every retry family was already count-bounded:
    - per-family budgets;
    - the `max_total_attempts` ceiling;
    - API recovery capped at 3 (`retry_policy.decide_retry_action`).
  - Proof tests cover those bounds (`test_api_contract_recovery_keeps_its_hard_maximum`, plus the existing `tests/test_retry_policy.py`).
- **Real gaps found and closed:**
  1. `record_workspace_progress` compared each attempt only with the one before it. Any workspace change reset the counter, so an A→B→A→B cycle between two byte states never counted as no progress. `test_same_cycle_without_vectors_is_the_legacy_gap` documents the old behaviour; `test_alternating_cycle_is_not_progress_and_terminates` covers the fix.
  2. The retry-evidence gate (`_prepare_retry_context`) also compared only with the last fingerprint, so a deterministic-verdict A→B→A evidence cycle slipped through. It also let a probabilistic failure resend identical evidence even at temperature 0, where the output cannot vary.
  3. API-contract recovery was excluded from the evidence gate, and it had no cycle detection within its three attempts.
  4. A no-progress stop surfaced as `failure_category: quality_gates_exhausted`, with no terminal reason anywhere in the result.

## Files changed
- **Production:**
  - `kriya/workflow/retry_progress.py` (new):
    - `ProgressVector`, `build_progress_vector`, `classify_progress`, `sampling_resample_permitted`;
    - the `RETRY_ACTION_MATERIAL_DELTA` audit table;
    - the reason codes.
  - `kriya/workflow/retry_strategy.py`:
    - `record_workspace_progress(vector=)` applies the seen-set rule and emits `retry.progress_vector` / `retry.no_progress_terminal`;
    - `_attempt_progress_vector` builds the vector;
    - the forced transition now emits `retry.strategy_transition`.
  - `kriya/workflow/attempt.py`:
    - the evidence gate uses the whole-run seen set;
    - SAMPLING_RESAMPLE and SAMPLING_NOT_PERMITTED;
    - `_effective_retry_temperature`.
  - `kriya/workflow/state.py`: the `progress_vector_digests`, `last_progress_vector`, `sampling_resamples`, `retry_evidence_seen` and `no_progress_reason` fields, and `retry_progress_summary()`.
  - `kriya/workflow/workflow.py`: `failure_category: "no_progress"` and the `retry_progress` result field.
  - `kriya/cli.py` + `kriya/cli_output.py`: the `[NO PROGRESS]` stop message (generate and fix).
- **Tests:** `tests/test_prd026_retry_progress.py` (37 tests).
- **Live:** `tests/test_live_prd025_029_batch6.py::test_live_prd026_ineffective_repair_terminates`.
- **Docs:** `docs/design.md`, retry-progress paragraph.

## Implementation summary
- **Vector.** The vector dimensions are:
  - failure signature, workspace hash, implicated and missing files;
  - the evidence fingerprint shown this attempt;
  - context revisions (path, revision, tier, member);
  - the attempt mode, and the API-recovery phase as the protocol;
  - the Developer request profile (`model@profile-digest`);
  - the plan `content_hash()`;
  - the repair-contract revision (id, status, active group, participants);
  - the diagnostics reason code.
  - Nothing time-based and no raw model text is included.
- **Classification.** A seen digest is REPEATED_VECTOR and counts. Otherwise the pre-PRD-026 pairwise rules apply unchanged: a new workspace is PROGRESS; a new action on the same bytes is a strategy transition, which is a new vector and is allowed once.
- **Seen vectors are never erased**, including by best-of-N candidate resets; a discarded candidate's state is still not progress.
- **Sampling.**
  - On identical evidence with a probabilistic last failure, `SAMPLING_RESAMPLE` is recorded only when the mode is sampling-eligible (targeted, missing_files, fallback_targeted, full_set) and the effective temperature is above 0, or None (a provider default counts as sampling). It increments `sampling_resamples`.
  - Otherwise the retry is refused before any Developer call, as `no_progress_retry` with `SAMPLING_NOT_PERMITTED`.
  - A resample never touches the progress counter or the seen vectors; only its resulting vector is classified.
  - `test_workflow_fallback_chain`'s bounded stochastic retries still pass (default temperature 0.2).
- **Terminal.** `no_progress_reason = RETRY_NO_PROGRESS_EXHAUSTED`, then `failure_category: no_progress` and `retry_progress` in the result, and the CLI message.

## Tests run by coding agent (targeted)
| Command | Passed | Failed |
|---|---:|---:|
| `.venv/bin/pytest -q tests/test_prd026_retry_progress.py tests/test_val001_g1r3_retry_context.py tests/test_retry_policy.py` | 75 | 0 |
| `.venv/bin/pytest -q tests/test_workflow.py -k "fallback_chain or two_full_set_attempts or progress_gate or no_progress"` | 4 | 0 |
| `.venv/bin/pytest -q tests/test_d1_operation_mode_authority.py -k "never_rewarded or whole_retry or full_file"` | 12 | 0 |

**Mutation checks.** All 14 were killed:
- the seen check;
- a pairwise-only seen check;
- the temperature rule;
- the family rule;
- the last-only evidence comparison;
- the retry_temperature precedence;
- the resample counter;
- the terminal reason;
- the diagnostics dimension (initially SURVIVED; test added);
- the workspace dimension;
- the vector wiring;
- the `no_progress` category;
- the counter reset on a new vector (initially SURVIVED; test added);
- the `retry.strategy_transition` event kind (asserted through the persisted `traces.db` run events).

## Static/lint/architecture checks
ruff: All checks passed. pylint: exit 0.

## Live test additions
- Required: YES.
- `test_live_prd026_ineffective_repair_terminates` runs a real Developer at temperature 0 against a compile gate that rejects every candidate.
- Invariant: the run stops with `no_progress` or `quality_gates_exhausted` within 12 Developer calls, and `retry_progress` is recorded.
- Command: the Batch 6 live command with `-k prd026`.

## Known limitations / residual risks
- A behaviour change to note: a configuration with temperature 0 (or `retry_temperature: 0`) no longer spends a Developer call re-sending identical evidence after a probabilistic failure; it transitions strategy instead. The packaged default is 0.7, so it is unaffected.
- The effective temperature does not model `apply_fix_analysis=False` retries, which send no temperature override. It uses `retry_temperature` whenever that is configured.
- The full suite may contain end-to-end tests whose runs now end with `failure_category: no_progress` rather than `quality_gates_exhausted`. That is the intended change; any such assertion should be reported from the user's full run.

## Verification-agent handoff
Run the Batch 6 focused command, then the full suite, then the PRD-026 live case.
