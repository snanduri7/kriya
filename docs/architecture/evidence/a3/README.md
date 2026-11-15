# Evidence for slice A3

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called.

| File | What it is | Result |
|---|---|---|
| `a3_probe.py` | Model-free probe P1 to P7, calling the real functions with hand-built inputs | `a3_probe_output.txt` |
| `a3_probe_output.txt` | Output on this baseline | P1: an applied unit with a refused final review is not a pass and its category is `final_review_refused`. P2: the same unit result is a failure for the direct/milestone executor and an acceptance for the enforce loop. P3: in a three-unit milestone plan the first unit stays VERIFIED, the refused one is FAILED, the next is BLOCKED. P4: `commit_eligible` is True with `requirements_verified` False; a PENDING requirement blocks only under `unknown_policy = block`. P5: `approval.decision` carries `outcome`, `triggers` (and `reason_code`). P6: five digests of one candidate, five values; the commit guard passes the same batch and refuses a changed or unbound one. P7: only `True` or an approving `ApprovalOutcome` approves; execution is held only in human-in-the-loop mode with source written; `stale` semantics |
| `a3_probe_result_truth.py` | Probe P8: drives the committed scripted scenario of `tests/test_project_continuity_two_run.py` (control F) through the real direct pipeline and prints result fields the committed test does not assert | `a3_probe_result_truth_output.txt` |
| `a3_probe_result_truth_output.txt` | Output on this baseline | Default policies: `quality_gates_passed = True`, `REQ-1 = unverified`, `requirement_truth = OPEN_REQUIREMENTS`, the substitute `app.py` applied, `main.py` unchanged. `requirement_unverified_policy = block`: `failure_category = verification_authority_required`, nothing generated or applied |
| `run_a3.sh` | Runs `a3_probe.py` and two committed tests, unchanged | `a3_tests_output.txt`: 2 passed |
| `check_citations_a3.py` | Checks that every `path::symbol` cited by the A3 rows exists at the checked-out tree, plus four negative controls (no launch handling in `control/retention.py`; no `launch-decision` in `cli.py`; no `finish_reason` in `stage_review.py`; no `rule_id` in `core/attempt_evidence`) | `check_citations_a3_output.txt`: 57 files, 190 symbols, 4 absence checks, 0 problems |
| `coverage_overrides_a3.json` | The A3 registry coverage entries (28 A3 rows and the row handed over by A1) | merged into `../a1/coverage_overrides.json`: 62 entries |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout; isolated state):

```
sh docs/architecture/evidence/a3/run_a3.sh <venv python> $PWD <tmp>
PYTHONPATH=$PWD:$PWD/tests PYTHONDONTWRITEBYTECODE=1 KRIYA_MODEL_RUNTIME_PROBE=0 \
  <venv python> -m pytest -q -p no:cacheprovider -s --rootdir $PWD docs/architecture/evidence/a3/a3_probe_result_truth.py
python3 docs/architecture/evidence/a3/check_citations_a3.py .
python3 docs/architecture/evidence/a2/merge_overrides.py docs/architecture/evidence/a1/coverage_overrides.json docs/architecture/evidence/a3/coverage_overrides_a3.json
python3 docs/architecture/evidence/a1/gen_coverage.py . docs/architecture/evidence/a1/coverage_overrides.json
```

Limits of this evidence, stated once:

- P1 uses a namespace object carrying the attributes `final_workflow_quality_passed` and `terminal_failure_category`
  read, not a full run state.
- P3 calls the private `plan_executor._execute` with a stub driver and no RunRecord. The executor's decisions are
  measured; the `committed_work_units` report was absent because no RunRecord existed, which is an artifact of the
  probe. The real milestone driver (`milestones.py::_MilestonePlanDriver`) was read, not executed.
- P8 imports the committed test module and `tests/conftest.py` fixtures; it prints and asserts nothing. The tests
  directory must be importable (`PYTHONPATH` above).
- No enforce run was driven. Everything about the enforce terminal (`SM-A3-04`, `FR-A3-15`, `BYP-A3-01`,
  `BYP-A3-02`) is `TRACED`.
- No desktop launch was driven. The review channel and launch record behaviour is `TRACED`; its committed tests were
  not re-run.
- `control/recovery.py` was read at its module contract and outline only.
- The self-correction loops' own writers and the tail of `run_terminal_regression` were not read.
