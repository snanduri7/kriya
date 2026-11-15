# Evidence for slice A2

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called.

| File | What it is | Result |
|---|---|---|
| `a2_probe.py` | Model-free probe. P1 to P7: real pytest runs in temporary directories, their JUnit XML parsed by `kriya.tools.test_execution.parse_junit_xml`, then the real `verifier_semantic.derive_executed_verifiers` (P7 uses a hand-built report in the documented identity form). P8 to P10: the real `derive_requirements` and `compile_verification_contract` on three goals | `a2_probe_output.txt` |
| `a2_probe_output.txt` | Output of the probe on this baseline | P1 to P4 and P6 are intercepted; **P5 (src layout) and P7 (no grammar) are not, and no unknown is raised**; P8 and P9 are `VERIFICATION_AUTHORITY_REQUIRED` with every behaviour entry GENERAL and without a closer; P10 is `ADMITTED` through `runtime_stdout` |
| `run_a2_tests.sh` | Runs seven committed tests of `tests/test_verifier_semantic_loop_001.py`, unchanged | `a2_tests_output.txt`: 7 passed |
| `check_citations_a2.py` | Checks that every `path::symbol` cited by the A2 rows exists at the checked-out tree | `check_citations_a2_output.txt`: 31 files, 62 symbols, 0 problems |
| `coverage_overrides_a2.json` | The A2 registry coverage entries (13 A2 rows and the row handed over by A1) | merged into `../a1/coverage_overrides.json` |
| `merge_overrides.py` | Merges a slice's entries into the one overrides file the generator reads | 34 entries after the merge |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout; isolated state):

```
PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR=<tmp> KRIYA_LOG_DIR=<tmp> KRIYA_MODEL_RUNTIME_PROBE=0 \
  <venv python> docs/architecture/evidence/a2/a2_probe.py
sh docs/architecture/evidence/a2/run_a2_tests.sh <venv python> <tmp>
python3 docs/architecture/evidence/a2/check_citations_a2.py .
python3 docs/architecture/evidence/a2/merge_overrides.py docs/architecture/evidence/a1/coverage_overrides.json docs/architecture/evidence/a2/coverage_overrides_a2.json
python3 docs/architecture/evidence/a1/gen_coverage.py . docs/architecture/evidence/a1/coverage_overrides.json
```

Limits of this evidence, stated once:

- P5 ran plain pytest from the project root. The working directory and options Kriya's own test gate uses for a
  `src/` layout were not exercised, so the identity Kriya would report there is `TRACED` to pytest's node-id rule,
  not measured through the gate.
- P7 did not run a Kotlin or Groovy test; it measures only what the derivation does with a run-authored path the
  structural parser has no grammar for.
- No retry loop was executed with an unmapped test. The fall-through to the historical path is shown by the
  committed test `test_a_failure_with_no_run_authored_executed_verifier_keeps_the_historical_retry`.
- Sealed run 10 was not re-read; its facts are cited from the registry row.
