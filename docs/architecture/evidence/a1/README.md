# Slice A1 evidence

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. Run on 2026-11-15. No model, no network, no container. Nothing in the
repository was modified by a probe (checked with `git status --short` afterwards).

| File | What it is | Result |
|---|---|---|
| `a1_probe.py` | Model-free probe of the pure owners (`build_failure_signature`, `update_open_findings`, `replaces_open_finding`, `observe_failure_family`, `charge_failed_attempt`). Input: the two gate texts preserved from run 10 in `~/kriya-evidence/product-72h-confidence/ws4-same-blocker-prefix-2026-11-14/` | See `a1_probe_output.txt` |
| `a1_probe_output.txt` | Output of the probe. Its first line records that `kriya` was imported from the closure worktree | P1 False, P2 True, P3 True, P4 2 -> 5 not equal, P5 8, P7 False / unchanged, P8 new family with targeted 0, P9 False |
| `check_citations.py`, `check_citations_output.txt` | Checks that every file and symbol cited by the A1 rows exists in the tree | 31 files, 69 symbols, 0 problems |
| `gen_coverage.py`, `coverage_overrides.json` | Generator and A1 inputs of `../../REGISTRY-COVERAGE.md` | 141 rows, 5 reconciled, 3 need characterization |

Commands (the interpreter is the virtual environment of the `Kriya-product-72h` worktree, which is at the same
commit; `PYTHONPATH` makes `kriya` resolve to this worktree):

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<this worktree> KRIYA_STATE_DIR=<scratch> KRIYA_LOG_DIR=<scratch> \
  KRIYA_MODEL_RUNTIME_PROBE=0 <venv>/bin/python a1_probe.py ~/kriya-evidence/product-72h-confidence/ws4-same-blocker-prefix-2026-11-14
PYTHONDONTWRITEBYTECODE=1 <venv>/bin/python -m pytest -q -p no:cacheprovider tests/test_non_candidate_failure_scoped_reset_001.py
python3 check_citations.py <this worktree>
python3 gen_coverage.py <this worktree> coverage_overrides.json
```

Focused existing test run: `tests/test_non_candidate_failure_scoped_reset_001.py` - **9 passed in 0.94s**.

## Slice A1b (2026-11-15, same baseline, same interpreter and environment)

| File | What it is | Result |
|---|---|---|
| `a1b_probe.py`, `a1b_probe_output.txt` | Model-free probe of the pure owners. P10: the staging-rejection type and `no_progress_retry` against the open-finding predicate and the family reducer. P11: the preserved assertion with only its reported value changed. P12: the unit ceiling. P13: requirement ids per generation and the ledger history of an unrecorded id. P14: default `RetryBudgets` and the `ControlState` field list. P15: the no-progress limit | P10 the rejection is kept, survives a resource refusal, is replaced by `no_progress_retry` (scoped budgets 3/used -> 0/available) and by a compile failure. P11 new family. P12 ceiling 10 without a fallback, 11 with one. P13 distinct ids, history 0. P14 no retry or recovery field in `ControlState`. P15 1 and 2 read as 3 |
| `a1b_tests_output.txt` | Seven committed deterministic tests run to turn traced enforce facts into measured ones | 7 passed in 67.32s |

```
<venv>/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_workflow_controller_enforce.py::test_enforce_bounds_recovery_attempts_per_downstream_owner_pair \
  tests/test_workflow_controller_enforce.py::test_enforce_resume_never_skips_sandbox_only_completions_after_a_crash \
  tests/test_workflow_controller_enforce.py::test_enforce_resume_skips_only_the_subtasks_already_completed \
  tests/test_workflow_controller_enforce.py::test_enforce_resume_after_a_committed_sandbox_plan_reuses_it_and_changes_nothing \
  tests/test_enforce_recovery_convergence_001.py::test_new_bytes_every_time_are_stopped_by_the_plan_derived_recovery_cap \
  tests/test_enforce_recovery_convergence_001.py::test_a_repeated_convergence_state_stops_typed \
  tests/test_resume_integrity.py::test_workflow_refuses_uncertain_commit_before_any_model_call
```

Searches recorded for the A1b single-owner and census claims (run from the worktree root):

```
grep -rnE "budgets\.[a-z_]+ *[+-]?= |budgets\.[a-z_]+\.(add|clear|discard|update)\(" kriya --include='*.py'   # 26 writer sites
grep -c "await _invoke_bounded_subtask(" kriya/workflow/enforce_loop.py                                        # 10 call sites
grep -n "legacy_kwargs" kriya/workflow/enforce_loop.py                                                          # resume forwarding
```

A1b limits: P10 and P11 exercise the reducers, not a full retry loop. No unit resume was executed end to end (the
gap ledger states the missing harness). The enforce tests use an engine double that replaces the unit, so they
measure invocation counts and resume decisions, not unit budgets. The owner-recovery self-correction body
(`BYP-A1-13`) was not read. Not run: the full suite, `tests/state_machine`, `tests/architecture`, live models,
qualification.

## Limits

- P3 is a constructed input: the preserved assertion text with only the test method name changed. It shows what the
  signature does with such text; it is not a recorded run.
- The first version of the probe computed P9 without recording the first family and printed `True`. That was a
  mistake in the probe, not a product behaviour; the committed probe records the first family and prints `False`.
- No resume was exercised. Every statement about checkpoint and resume in slice A1 is `TRACED`.
- Not run: the full suite, `tests/state_machine`, `tests/architecture`, live models, qualification.
