# Post-PRD-036 state-machine transition hardening

Scope: where a subsystem's locally correct state blocks, permits or changes a global workflow transition. Branch `milestone-decomposition`, based on `efd3384` (PRD-036 closed; certified candidate prd-036-rc9 / 7bc686f). This pass is not a release candidate, and nothing has been pushed.

## Result

| Defect | Priority | Pattern | Commit |
|---|---|---|---|
| STATE-BEST-OF-N-HANDOFF-001 (own bug) | P2 (opt-in best-of-N) | B, E, F | `2939a47` |
| STATE-FAILURE-FAMILY-CYCLE-001 | P1 | A, D, E | `e37f444` |
| STATE-RESERVED-FALLBACK-001 (fix chosen by the user) | P1 | D, E | `c8bd61e` |
| STATE-ATTEMPT-METRICS-001 (found in final verification) | P2 | evidence accuracy | `4240d69` |
| STATE-ATTEMPT-METRICS-002 (own bug, incomplete -001) | P2 | evidence accuracy | `f4bb39e` |

Supporting commits:
- `a2e8683` extracts the retry budget bookkeeping into `retry_policy`, with no behavior change, so the tier drives the same functions production does.
- `f3c61ee` and the tier-extension commit add the fast tier.

**Fast tier:** `.venv/bin/pytest -m state_machine` (584 tests at `f4bb39e`, 108.7 s). `tests/state_machine/` alone: about 25 s. The whole-tier runtime has not been measured; that is in the hand-off.

**Verification so far (all run by me):**
- a targeted run per fix;
- three retry-machinery subsystem runs over the 16 files that exercise `handle_attempt_failure`/budgets, plus `tests/state_machine`, the handback and signature suites: 1456/0 (extraction only), 1502/0 (after the family-cycle fix), 1511/1 after the reservation fix (the one failure was an old-contract assertion about the mode at the last slot, updated to the reserved decision; 83/83 on the rerun of that file and the tier directory);
- ruff and pylint at zero.

The full suite has not been run.

## Retry / recovery / fallback: the implemented state machine

**States** (`RetryAction` plus loop terminals):
- attempt modes: `FULL_SET`, `TARGETED`, `MISSING_FILES`, `FALLBACK_TARGETED`, `API_CONTRACT_RECOVERY` (phases `RESTORE_PUBLIC_CONTRACT` → `REPAIR_BEHAVIOR`);
- terminals: `SUCCESS`, `STOP_ENVIRONMENT`, `STOP_EXHAUSTED`, no-progress stop, plan-scope conflict, `FALLBACK_MODEL_INCOMPATIBLE`, `workspace_commit_failed`.

**Budgets:**
- `retry_count` < M, where M = max(4, 1 + len(chain));
- `targeted_retry_count` < 3, per new failure family;
- `fallback_targeted_attempted`: one per new family;
- `api_contract_recovery_count` < 3;
- `fallback_attempts_used` against `FALLBACK_ALLOWANCE` (1);
- the global ceiling M + 3 + FALLBACK_ALLOWANCE (with a chain) + 3 + best-of-N;
- no-progress: a forced transition at 2 consecutive attempts, a stop at max(3, config).

**Models:** targeted, missing-files and recovery attempts run on the primary. `FALLBACK_TARGETED` runs on the first eligible fallback. A full-set attempt runs on the fallback ladder from `retry_count` 1, skipping PRD-017-incompatible entries.

**Decision order** (`decide_retry_action`; the loop and the attempt mode now decide from the same attempt number):
1. environment failure;
2. recovery with budget left (outranks the ceiling, PRV-11);
3. recovery spent and the contract unrestored → STOP;
4. global ceiling;
5. **reserved fallback slot** (new);
6. targeted, unless a fallback-targeted request is pending;
7. missing-files;
8. fallback-targeted;
9. full-set;
10. STOP.

**Per failed attempt** (`_record_attempt_failure`, pinned in order by `test_the_failure_recording_calls_the_modelled_bookkeeping_in_order`):
1. `observe_failure_family`;
2. a reset if the family is new;
3. `record_workspace_progress`;
4. `force_strategy_transition`;
5. attribution;
6. `charge_failed_attempt`;
7. recovery scope override until handback.

A verified RESTORE step charges recovery and records no failure.

### Transition rules, enumerated exhaustively
`tests/state_machine/test_sm_retry_decisions.py` checks all 41,472 decision-relevant combinations:
- counters at 0, bound−1 and bound;
- grounding;
- fallback configured and used;
- recovery count and restoration;
- remaining capacity at 6, allowance+1, allowance, and 0.

Ten rules are checked:

| Rule | What it checks |
|---|---|
| R1 | An environment failure stops. |
| R2 | Recovery with budget left runs first. |
| R3 | Recovery spent on an unrestored contract stops, with no retry and no fallback. |
| R4 | Recovery spent on a restored contract gives the decision the same state has without recovery. |
| R5 | The ceiling stops. |
| R6 | A stop below the ceiling happens only when no family remains. |
| R7 | Each family runs only within its budget and grounding. |
| R8 | Precedence order. |
| R9 | Attempt mode = loop decision, including the reserved flag. |
| R10 | The fallback's reserved slot. |

Mutations: 15/15 on the original policy, plus the reservation set (below).

The recovery transition matrix, as implemented:

| Restored | Recovery budget | Normal budget | Qualified fallback | Kriya | Evidence |
|---|---|---|---|---|---|
| no | exhausted | any | any | STOP (fail closed) | R3; corpus `test_an_unrestorable_contract_…`; handback e2e |
| yes | exhausted | available | any | ordinary family, routed by attribution | R4; C6 corpus + e2e |
| yes | exhausted | exhausted, fallback unused | available | FALLBACK_TARGETED / escalated FULL_SET | R4, R6, R10 |
| yes | exhausted | exhausted | unavailable | STOP | R6 |
| any | available | any | any | API_CONTRACT_RECOVERY, even at the reserved slot | R2; corpus `test_mandatory_recovery_outranks_…` |

### Trajectories
- `tests/state_machine/_retry_model.py` drives the loop over the real `decide_for_state`, `decide_attempt_mode`, `resolve_fallback_model`, `record_workspace_progress` and bookkeeping functions. Only attempt outcomes are scripted.
- `test_sm_retry_trajectories.py` has a named corpus of 17 cases plus 4,500 seeded trajectories (chain lengths 0, 1 and 2). It checks:
  - the ceiling bound (recovery may run past it);
  - models per mode;
  - fail-closed recovery;
  - handback;
  - "no-progress stop never with an unused configured fallback".
- A failing generated sequence is written, with its seed and outcomes, to `$KRIYA_STATE_MACHINE_FAILURES` or the test's temporary directory, for adding to the corpus.

## Defects

**STATE-BEST-OF-N-HANDOFF-001** (own bug, from c9140a6).
- **Defect:** a non-final candidate failure that ended best-of-N was handled twice. It was charged, ledgered, progress-classified and metered twice.
  - A failed worktree reset did the same, after resetting the state for a candidate that never ran.
  - Sticky API recovery survived the independent-candidate reset, so a verified RESTORE transition was recorded as a failure. The C6 shape under best-of-N failed with the fallback unused.
- **Fix:** `BestOfNFailureRecorded` hands the loop the recorded decision. Sampling stops when the loop must stop, the budgets are spent, or repair state is active. The sandbox is created before the reset, and `RecoveryPhaseAdvanced` always reaches the loop.
- **Evidence:** 4 e2e tests (3 fail without the fix); 8/8 mutations caught.

**STATE-FAILURE-FAMILY-CYCLE-001.**
- **Defect:** a primary alternating between two failures (A → B → A, new bytes every time) got a "new family" on every attempt. Each reset the targeted budget uncharged, and each counted as PROGRESS. 11 primary attempts; the run ended exhausted with the fallback never tried.
- **Fix:** only a never-seen family is new, and a return to an earlier family is charged.
- **Evidence:** `test_sm_failure_families.py` (real pipeline; both tests fail without the fix).

**STATE-RESERVED-FALLBACK-001.**
- **Defect:** a primary exposing a genuinely new failure every attempt spent the whole ceiling, including the fallback's counted allowance, on itself.
- **Fix** (your Option 1):
  - the fallback's allowance is reserved from explicit state (allowance − `fallback_attempts_used` against the remaining capacity);
  - it is evaluated after the recovery rules and adds no attempt;
  - it runs fallback-targeted when grounded, else the escalated full-set;
  - it records a typed `retry.reserved_fallback` event.
- **Evidence:**
  - `test_sm_reserved_fallback.py`, 5 real-pipeline tests (the first two fail without the fix): the last slot goes to the fallback, recorded by the typed event; no attempt is added; with no fallback the ordinary ceiling holds; no slot stays reserved after an earlier fallback-targeted or escalated full-set attempt;
  - R10 at the boundaries (remaining = allowance+1 → the primary may run; = allowance → the fallback runs; allowance used → no reservation);
  - 4 corpus cases, including recovery precedence.
- **Mutations:** 11 of 13 caught. The other 2 were a redundant capacity check (always ≥ 1 after the ceiling check) and an unreachable `retry_count`-0 escalation guard (every recorded failure charges `retry_count` first). Both were removed from the code; the reserved full-set is decided only from `retry_count` 1. Two further boundary mutations were caught.

## Live-discovered defects: deterministic regressions (criterion 7)
| Defect (PRD-036) | Deterministic reproducer (seconds, scripted model) |
|---|---|
| C6 WORKFLOW-RECOVERY-HANDBACK-001 | `test_workflow_recovery_handback.py` (real pipeline); corpus `test_c6_…` |
| C8 FAILURE-SIGNATURE-RUN-NOISE-001 | `test_failure_signature_run_noise.py::test_a_repeating_failure_…`; corpus `test_c8_c10_…` |
| C10 FAILURE-SIGNATURE-SHIFTING-VALUES-001 | `test_failure_signature_run_noise.py::test_a_repeating_static_rule_violation_…` |
| PLAN-OBLIGATION-SUPERSEDED-001, CANARY-FRESH-WORKSPACE-001, WORKTREE-LEGACY-NESTED-001 | their PRD-036 tests (unchanged) |

## §7 invariants → the test that asserts each

| Invariant | Test |
|---|---|
| Uncertain authoritative state → no mutation/commit; recovery failure fails closed | R3; `test_an_unrestorable_contract_stops_fail_closed_and_never_calls_the_fallback`; generated fail-closed check |
| A rejected candidate never reaches the workspace | `test_a_restored_contract_hands_…` (`DROPS_ADD not in seen_on_disk`); `test_a_candidate_changed_after_verification_is_refused_with_nothing_written` |
| Subsystem exhaustion does not suppress a valid global transition after safe restoration | R4; C6 e2e; `test_sm_reserved_fallback.py` |
| The fallback is considered exactly when policy says | R6–R8, R10; corpus; generated no-progress/fallback check |
| The fallback does not reset unrelated exhausted budgets | `test_a_new_family_resets_only_the_scoped_budgets`; `test_a_failed_attempt_is_charged_to_its_own_family_only` |
| Retry N+1 needs progress or a permitted resample | PRD-026 suite (`test_prd026_retry_progress.py`, e.g. `test_identical_evidence_is_refused_at_temperature_zero`) |
| SUCCESS requires all mandatory terminal evidence | `test_gates_that_never_ran_are_not_commit_eligible`; `test_a_validator_that_raises_is_an_indeterminate_failure_never_a_pass` |
| Committed digest = verified digest; stale/missing evidence → refused | `test_a_missing_binding_is_refused_even_for_an_empty_batch`; `test_a_stale_binding_is_not_rescued_by_valid_static_analysis_evidence`; `test_enforce_refuses_a_candidate_changed_after_its_gates_bound` |
| The static-analysis requirement cannot disappear between verification and commit | `test_prd031a_static_analysis.py::test_an_effective_settings_change_is_refused`, `::test_stale_candidate_bytes_are_refused`, `::test_required_but_disabled_is_rejected_at_config_load` |
| Stale resume evidence invalidates; unaffected evidence survives | `test_invalidation_keeps_the_longest_valid_prefix`; `test_every_surviving_artifact_has_only_matching_dependencies`; `test_model_runtime_invalidates_only_model_protocol` |
| An interrupted transition is never completed | `test_crash_after_every_replace_settles_committed_but_never_success`; `test_partial_commit_without_a_run_record_is_never_rolled_forward`; `test_crashed_run_without_a_commit_is_recovered_as_failed` |
| A spawned process is managed or cleaned before an error returns | `test_a_failed_attach_kills_and_reaps_the_new_process_on_every_spawn_path`; `test_a_failed_async_attach_is_reaped_before_the_refusal_reaches_the_caller`; `test_a_hung_command_times_out_and_its_whole_tree_is_reaped` |
| Worktree/recovery state has one owner | `test_worktree_reset_failure_hands_the_recorded_failure_to_the_loop_unreset` and the best-of-N e2e (new); WORKTREE-CANONICAL-ROOT-001 tests (existing) |
| Budget refusal → retry/fallback (area 11) | `test_sm_budget_transitions.py::test_a_primary_budget_refusal_reaches_the_fallback_by_the_full_set_route` (new; it passed, so the behavior was already correct) |

The suites above are in the tier through `tests/conftest.py::STATE_MACHINE_TIER_FILES`. `tests/state_machine/test_sm_tier_guard.py` keeps live markers out.

## §5 fault injection: where each is forced
- **`tests/state_machine/_scripted_run.py`** (real pipeline, scripted model): API violation, restoration success, compile and static-rule failure, test failure, targeted and full-set exhaustion, fallback available/unavailable/success/failure, budget refusal, commit refusal (via the PRD-032 tests).
- **`_retry_model.py`:** restoration failure, no-progress, fallback incompatibility, environment stop.
- **The PRD-032 chaos harness** (`tests/_chaos_harness.py`): timeout (hung command), stale verification evidence, commit refusal and concurrent edit, static-analysis rejection, resume mismatch (PRD-008 suites).
- **Ownership conflict:** the PRD-022 suite, not added to the tier.

## Examined and found correct
- **A second API violation after a handback.** It is recorded as `api_contract_recovery_incomplete` and routing continues. Probed end to end: it reached the fallback and succeeded.
- **No-progress vs. the fallback (the open PRD-026 question).** The forced transition after 2 counted no-progress attempts requests the fallback (fallback-targeted when grounded, else escalated full-set). A model change restarts the counter. No generated trajectory ended on no-progress with a configured, compatible fallback unused. The corpus pins the stuck-primary and ungrounded cases. PRD-026 is unchanged.
- **Time budget exhaustion and internal errors** are environment stops, a legitimate global terminal.
- **`RetryBudgets` is never serialized**, so the new set field cannot reach JSON.

## Policy question left as documented
PRD-017 `FALLBACK_MODEL_INCOMPATIBLE`: when escalation needs a fallback and none configured can serve, the run ends even with primary full-set budget left. This is documented as intended, and now pinned end to end: `tests/test_prd017_fallback_transition.py::test_a_required_fallback_that_none_can_serve_ends_typed_with_primary_capacity_unused` (`e509fbb`). That test proves:
- the fallback-targeted transition was admitted;
- the only fallback is sent nothing;
- there is no routing back to unused primary full-set capacity;
- no extra attempt is made (4 primary attempts, then the stop);
- the typed `fallback_incompatible` failure appears, with `failure_category: fallback_model_incompatible` and a `model.fallback_selection` record;
- the workspace is untouched.

## Final verification (2026-09-29)

**Final hardening revision: `f4bb39e`.** It is NOT the PRD-036 certified candidate, which stays prd-036-rc9 / 7bc686f on Ollama 0.34.2. Full post-hardening release certification (a 3×11/11 streak and hosted CI) is deferred on purpose to `KRIYA_PRODUCTION_CERTIFICATION`. PRD-036 evidence is untouched.

**Runtime.** Ollama was upgraded on this host from 0.34.2 to **0.34.4** after PRD-036.
- The model weights are unchanged: artifact digests `sha256:06c1097e…` (qwen3-coder:30b) and `sha256:07d35212…` (qwen3.6:35b-a3b-q4_K_M). Only `provider_version` differs.
- So the runtime digests are new: `9439a612…` and `39cda4f4…`.
- Every role model was requalified on 0.34.4 under the unchanged operator config (digest `a5be2e83…`): 18 PASS, 0 FAIL, 1 UNAVAILABLE (`endpoint_restart_semantics`) each.
- After requalification, the doctor reports `PRODUCTION_READY=true`.
- Evidence: `evidence/STATE-MACHINE-HARDENING/qualification/`.

**Deterministic gates on `f4bb39e`.**

| Gate | Result |
|---|---|
| Full pytest | 7288 passed, 0 failed, 0 errors, 0 skips (33:00) |
| Fast tier `-m state_machine` | 584/584, 0 skips, 108.7 s |
| ruff / pylint | 0 / 0 |
| Open P0/P1 | none |

Ten slowest tier tests: `test_prd032_chaos_static_analysis.py::test_a_nosemgrep_suppression_in_the_candidate_never_hides_a_finding` 5.0 s; the reserved-fallback e2e tests 2.9–3.2 s each (5); `test_sm_attempt_metrics.py::test_new_family_resets_do_not_hide_targeted_attempts` 3.3 s; `test_sm_failure_families.py::test_with_no_fallback_…` 2.4 s; two `test_prd031a_static_analysis.py` tests at 2.2 s. There is no accidental slowness: the slow ones are real scripted-pipeline runs.

**Production canary and live matrix (production profile, enforce path, operator config, Ollama 0.34.4).**

| Attempt | Revision / candidate | Canary | Live 11-case matrix |
|---|---|---|---|
| 1 | `e509fbb` / `33998645…` | PASS (both runs) | **9/11 FAILED**: C4 exact_requirement, C11 static_analysis_enabled (`matrix/trial-20260929T025849Z`), kept as failed evidence |
| 2 | `4240d69` / `0ef71721…` | PASS | **11/11 CERTIFIED** (`matrix/trial-20260929T040507Z`) |
| final | `f4bb39e` / `051daa7d…` | PASS (both runs, every check) | **11/11 CERTIFIED** (`matrix/trial-20260929T052623Z`, 9:42, run detached at exactly `f4bb39e`; first-pass cases now report retries 0/0) |

The canary checks are all true in every run:
- `PRODUCTION_READY` before and after;
- the enforce controller exercised;
- only the authorized file written, HEAD unchanged;
- independent compile and test exit 0;
- a committed transaction with its verification and static-analysis evidence bound (the commit is refused unless the verified digest matches);
- a bounded, non-nested worktree set reused on run 2;
- no leaked processes, containers or networks;
- the workspace lock released;
- a SUCCESS run record consistent with the trace.

**Attempt 1 classification (provisional).** Model/runtime behavior, not a Kriya defect:
- both cases fail on first-generation output, which the hardening doesn't touch;
- neither case configures a fallback, so STATE-RESERVED-FALLBACK-001 is inactive;
- no failure-family cycle occurred;
- every candidate was rejected with a typed failure, nothing was committed, and there was no false success.

Both cases passed all 9 PRD-036 trials on 0.34.2. Ollama 0.34.4 is a candidate cause, **not proven**: 0.34.4 has now produced both a 9/11 and an 11/11 run, so runtime repeatability stays a production-certification concern. The recommended next diagnostic is 0.34.2 vs 0.34.4 on C4/C11.

**Defects found during final verification (Fix-Now):**
- **STATE-ATTEMPT-METRICS-001** (P2, `4240d69`, found while classifying attempt 1). The reported retry counts came from the scoped budget counters, so C11 reported 4 targeted retries where 3 ran, and 0 where nine ran. This was evidence-only; decisions were unaffected.
- **STATE-ATTEMPT-METRICS-002** (P2, own bug in that fix, `f4bb39e`, found in the attempt-2 report). Counting started attempts made a first-pass success read `Retries 1/0`. The counts are now failed attempts per mode, and a first-pass success is 0/0; the final canary's production runs report 0/0.

Both are reporting-only. **`f4bb39e` differs from the 11/11 revision `4240d69` only by STATE-ATTEMPT-METRICS-002**: `state.py` and `retry_strategy.py` count failed attempts for the metrics dict, with no routing, gate or commit change. Full pytest, the tier and the canary are green on `f4bb39e` itself.

### Invariant check (§8 of the verification task)
| # | Invariant | Result | Evidence |
|---|---|---|---|
| 1 | Unrestored/uncertain authority cannot reach fallback/commit | PASS | R3; unrestorable-contract e2e + corpus; generated fail-closed check |
| 2 | Safe handback does not suppress remaining legal retry/fallback | PASS | R4; C6 e2e + corpus |
| 3 | A seen failure family cannot reset its budget | PASS | STATE-FAILURE-FAMILY-CYCLE-001 e2e; `test_only_a_never_seen_failure_family_is_new` |
| 4 | Best-of-N records one failure exactly once | PASS | `test_sm_best_of_n.py` (4 e2e) |
| 5 | Candidate reset clears candidate-local recovery state | PASS | best-of-N hands authoritative repair state to the loop, never sampling under it; reset clears seen families (`test_reset_clears_candidate_specific_fields`) |
| 6 | Primary cannot consume the fallback's reserved allowance | PASS | `test_sm_reserved_fallback.py`; R10 |
| 7 | Reserved fallback adds no attempt | PASS | `test_the_reserved_fallback_attempt_adds_no_attempt_to_the_ceiling` |
| 8 | Mandatory recovery outranks the reserved fallback | PASS | R2 before R10; `test_mandatory_recovery_outranks_the_reserved_fallback_slot` |
| 9 | No-progress cannot stop with an unused compatible fallback | PASS | 4,500 generated trajectories; stuck-primary corpus cases |
| 10 | Fallback required but none compatible → PRD-017 as documented | PASS | new end-to-end PRD-017 test above |
| 11 | Attempt mode and loop decision derive from the same state | PASS | R9 over 41,472 states (including the reserved flag); `decide_attempt_mode` decides at the loop's attempt number |
| 12 | C6/C8/C10 deterministically reproducible in seconds | PASS | table above |
| 13 | SUCCESS requires mandatory evidence and verified == committed | PASS | binding/terminal-gate suites in the tier; canary commit evidence bound |
| 14 | Process/worktree lifecycle intact | PASS | lifecycle suites in the tier; canary leak and worktree checks |

**Completion status.** At the user's instruction (a verification model bound to identity), ONE complete matrix ran on the exact final revision `f4bb39e`, candidate `051daa7d…` (CURRENT at `f4bb39e`): **11/11**. The earlier evidence is preserved unchanged:
- 9/11 on `e509fbb`;
- 11/11 on `4240d69`.

Every criterion is green on `f4bb39e`:
- PRD-017 pinned;
- tier 584/584;
- full pytest 7288/0;
- ruff and pylint at zero;
- canary PASS;
- live matrix 11/11;
- no open P0/P1;
- clean tracked tree.

**`STATE_MACHINE_HARDENING_VERIFIED=true`** (final hardening revision `f4bb39e`, Ollama 0.34.4). On Ollama 0.34.4 the same Kriya behaviour has now produced one 9/11 and two 11/11 matrices, so runtime repeatability stays a KRIYA_PRODUCTION_CERTIFICATION concern.

## Next: memory/resource-leak audit (designed, not run)
The harness and plan are at the session scratchpad `leak_audit/`. They were designed read-only and none of it has been executed. Retention hypotheses to measure:
- the unbounded `_ANALYZE_CACHE` (`kriya/analyzer/analyzer.py`);
- `TraceLogger` SQLite connections closed only by GC;
- unclosed `AsyncOpenAI` clients;
- jdtls released only on the normal return path;
- the path-keyed qualification `_RECORD_CACHE`;
- `ManagedProcess` cleanup only on `terminate()`.
