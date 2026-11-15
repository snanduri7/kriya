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

## Limits

- P3 is a constructed input: the preserved assertion text with only the test method name changed. It shows what the
  signature does with such text; it is not a recorded run.
- The first version of the probe computed P9 without recording the first family and printed `True`. That was a
  mistake in the probe, not a product behaviour; the committed probe records the first family and prints `False`.
- No resume was exercised. Every statement about checkpoint and resume in slice A1 is `TRACED`.
- Not run: the full suite, `tests/state_machine`, `tests/architecture`, live models, qualification.
