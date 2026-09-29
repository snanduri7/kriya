# Post-PRD-036 state-machine transition hardening

Scope: where a subsystem's locally correct state blocks, permits or changes a global workflow transition. Branch `milestone-decomposition`, based on `efd3384` (PRD-036 closed; certified candidate prd-036-rc9 / 7bc686f). This pass is not a release candidate.

## Result

| Defect | Priority | Pattern | Fix |
|---|---|---|---|
| STATE-BEST-OF-N-HANDOFF-001 (own bug) | P2 (opt-in best-of-N) | B, E, F | `2939a47` |
| STATE-FAILURE-FAMILY-CYCLE-001 | P1 | A, D, E | this batch |

Also in this batch, without a behavior change: `a2e8683` extracts the retry budget bookkeeping into `retry_policy`, so the tier drives the same functions production does.

**Fast tier:** `.venv/bin/pytest -m state_machine` (447 tests). `tests/state_machine/` alone: 50 tests, about 12 s. The whole-tier runtime has not been measured yet; that measurement is in the hand-off below.

## State machine inspected: retry / recovery / fallback

**States** (as implemented; `RetryAction` plus loop terminals):
- attempt modes: `FULL_SET`, `TARGETED`, `MISSING_FILES`, `FALLBACK_TARGETED`, `API_CONTRACT_RECOVERY` (phases `RESTORE_PUBLIC_CONTRACT` → `REPAIR_BEHAVIOR`);
- terminals: `SUCCESS`, `STOP_ENVIRONMENT`, `STOP_EXHAUSTED`, no-progress stop, plan-scope conflict, `FALLBACK_MODEL_INCOMPATIBLE`, `workspace_commit_failed`.

**Budgets:**
- `retry_count` < M, where M = max(4, 1 + len(chain));
- `targeted_retry_count` < 3, scoped to one failure family;
- `fallback_targeted_attempted`: one per new family;
- `api_contract_recovery_count` < 3;
- the global ceiling M + 3 + (1 if chain) + 3 + best-of-N;
- no-progress: a forced transition at 2 consecutive attempts without progress, a stop at max(3, config).

**Models:** targeted, missing-files and recovery attempts run on the primary. `FALLBACK_TARGETED` runs on the first eligible fallback. A full-set attempt runs on `chain[min(retry_count-1, len-1)]` once `retry_count` ≥ 1, skipping PRD-017-incompatible entries.

**Decision order** (`decide_retry_action`, one function called at two sites):
1. environment failure;
2. recovery with its own budget left (outranks the ceiling, PRV-11);
3. recovery spent and the contract unrestored → STOP (fail closed);
4. global ceiling;
5. targeted, unless a fallback-targeted request is pending;
6. missing-files;
7. fallback-targeted;
8. full-set;
9. STOP.

**Per failed attempt** (`_record_attempt_failure`, pinned by `test_the_failure_recording_calls_the_modelled_bookkeeping_in_order`):
1. `observe_failure_family`;
2. a scoped reset if the family is new;
3. `record_workspace_progress`;
4. `force_strategy_transition`;
5. attribution;
6. `charge_failed_attempt`;
7. recovery scope override until handback.

A verified RESTORE step charges recovery and records no failure.

### Transition rules, encoded exhaustively
`tests/state_machine/test_sm_retry_decisions.py` enumerates the whole decision domain: all 10,368 combinations of counters at 0, bound−1 and bound; grounding; fallback; recovery count and restoration; the ceiling. It checks nine rules:

| Rule | What it checks |
|---|---|
| R1 | An environment failure stops. |
| R2 | Recovery with budget left runs first. |
| R3 | Recovery spent on an unrestored contract stops, with no retry and no fallback. |
| R4 | Recovery spent on a restored contract gives exactly the decision the same state has with no recovery. Subsystem exhaustion never changes the global transition. |
| R5 | The ceiling stops. |
| R6 | A stop below the ceiling happens only when no family remains. |
| R7 | Each family runs only within its budget and grounding. |
| R8 | Precedence order. |
| R9 | The attempt mode equals the loop decision. |

15/15 mutations of `retry_policy` are caught.

The recovery transition matrix from the task, as actually implemented (R3/R4 plus the corpus):

| Restored | Recovery budget | Normal budget | Qualified fallback | Kriya | Evidence |
|---|---|---|---|---|---|
| no | exhausted | any | any | STOP (fail closed) | R3; `test_an_unrestorable_contract_stops_fail_closed_without_the_fallback`; handback e2e |
| yes | exhausted | available | any | ordinary family (routed by attribution) | R4; C6 corpus + e2e |
| yes | exhausted | exhausted, fallback unused | available | FALLBACK_TARGETED / escalated FULL_SET | R4 + R6 |
| yes | exhausted | exhausted | unavailable | STOP | R6 |
| any | available | any | any | API_CONTRACT_RECOVERY | R2 |

### Trajectories
- `tests/state_machine/_retry_model.py` drives the loop skeleton over the real `decide_for_state`, `decide_attempt_mode`, `resolve_fallback_model`, `record_workspace_progress` and bookkeeping functions. Only attempt outcomes are scripted.
- `test_sm_retry_trajectories.py` holds a named corpus and 4,500 seeded trajectories (1,500 each for chain lengths 0, 1 and 2). They are checked for:
  - the ceiling bound, with recovery allowed past it;
  - models per mode;
  - fail-closed recovery;
  - handback;
  - "no-progress stop never with an unused configured fallback".
- A failing generated sequence is written to `$KRIYA_STATE_MACHINE_FAILURES`, or the test's temporary directory, with its seed and outcome list, so it can be added to the corpus.

## Defects

**STATE-BEST-OF-N-HANDOFF-001** (own bug, from c9140a6).
- **Defect:** a non-final candidate failure that ended best-of-N was handled twice. It was charged, ledgered, progress-classified and metered twice.
  - A failed worktree reset did the same, after the state had been reset for a candidate that never ran.
  - Sticky API recovery survived the independent-candidate reset, so a verified RESTORE transition was recorded as a failure. The C6 shape under best-of-N ended with "recovery budget exhausted before the contract was restored" and the fallback unused.
- **Fix:** best-of-N hands the loop `BestOfNFailureRecorded` with its stop decision. It stops sampling when the loop must stop, the budgets are spent, or authoritative repair state is active. The sandbox is created before the state is reset, and `RecoveryPhaseAdvanced` always reaches the loop.
- **Evidence:** 4 end-to-end tests through the real pipeline; the first 3 fail without the fix. 8/8 mutations caught.

**STATE-FAILURE-FAMILY-CYCLE-001.**
- **How it was found:** by the trajectory model, then reproduced through the real `run_generation_workflow`.
- **Defect:** a primary alternating between two failures, with new bytes each time, got a "new family" on every attempt. Each one reset the targeted budget and was never charged, and every attempt counted as PROGRESS. The run used 11 primary attempts and ended exhausted, with the fallback never tried and 3 of 4 full-set retries unused.
- **Fix:** only a never-seen family is new, and a return to an earlier family is charged.
- **Evidence:** `test_sm_failure_families.py`; both tests fail without the fix.

## Live-discovered defects: deterministic regressions
| Defect (PRD-036) | Deterministic reproducer |
|---|---|
| C6 WORKFLOW-RECOVERY-HANDBACK-001 | `test_workflow_recovery_handback.py` (real pipeline), corpus `test_c6_…` |
| C8 FAILURE-SIGNATURE-RUN-NOISE-001 | `test_failure_signature_run_noise.py::test_a_repeating_failure_…`, corpus `test_c8_c10_…` |
| C10 FAILURE-SIGNATURE-SHIFTING-VALUES-001 | `test_failure_signature_run_noise.py::test_a_repeating_static_rule_violation_…` |
| PLAN-OBLIGATION-SUPERSEDED-001, CANARY-FRESH-WORKSPACE-001, WORKTREE-LEGACY-NESTED-001 | their PRD-036 tests (unchanged) |

Each runs in seconds with a scripted model.

## Examined and found correct
- **A second public-API violation after a handback.** It is recorded as `api_contract_recovery_incomplete` and routing continues. Probed end to end: it reached the fallback and succeeded.
- **No-progress vs. the fallback (the open PRD-026 question).** After 2 counted no-progress attempts the forced transition requests the fallback: fallback-targeted when grounded, else the escalated full-set route. A model change is itself a strategy change, so the counter restarts. Over 4,500 trajectories, no no-progress stop occurred with a configured, compatible fallback unused. The corpus pins the stuck-primary and ungrounded-stuck cases. PRD-026 itself was not changed.
- **Unrestored recovery.** It stops fail-closed before any ordinary retry or fallback, and this holds in every generated trajectory.
- **A context/output-budget refusal on the primary.** It is typed and implicates no file, so it takes the escalated full-set route to the fallback's own window.
- **Time budget exhaustion and internal errors.** Both are an environment stop, a legitimate global terminal.
- **Verification → terminal gates → commit, static analysis → commit, and resume/invalidation.** These are owned by existing deterministic suites, now part of the tier:
  - `test_candidate_verification_binding.py`: the committed digest equals the verified digest, and stale or missing evidence is refused with nothing written.
  - `test_prd030_terminal_services.py`: fixed gate order, a raising gate fails, and a report is required for commit.
  - `test_prd008_commit_state_gate.py` and `test_prd008_recovery.py`: an uncertain workspace is refused, and an interrupted or unsettled cycle is never treated as complete.
  - `test_prd008_resume_fingerprints.py`, `test_prd008a_resume_convergence.py` and `test_resume_integrity.py`: stage-precise invalidation, unaffected evidence survives, and incoherent reuse fails closed.
  - `test_prd032_terminal_commit_stop.py`, `test_prd032_chaos_commit.py` and `test_prd032_chaos_static_analysis.py`: chaos at the commit and static-analysis boundaries.
  - No gap was found that needed a new test.

## Policy questions for the user (not changed)
1. **The global ceiling vs. an unused fallback.** A primary that keeps exposing genuinely new, never-seen failure families (each one real progress) can still spend the global ceiling before the fallback's own slot. The ceiling formula reserves "+1 if chain" for it. This happens in about 1% of generated trajectories. Option: when one attempt is left and no fallback has run, route it to the fallback. That would not add a budget.
2. **PRD-017 `FALLBACK_MODEL_INCOMPATIBLE`.** When escalation needs a fallback and none can serve, the run ends even if primary full-set budget remains. This is documented as intended and pinned in the corpus.

## Hand-off
- Tier runtime: `.venv/bin/pytest -m state_machine --durations=10`.
- Full gate at the batch boundary: `.venv/bin/pytest`.
