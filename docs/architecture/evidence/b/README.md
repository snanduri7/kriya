# Evidence for Phase B

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called and no network request was made.

| File | What it is | Result |
|---|---|---|
| `b_probe.py` | Model-free probes B-P1 and B-P2 | `b_probe_output.txt` |
| `b_probe_output.txt` | Output on this baseline | **B-P1** (two bindings of one model name with different settings): the configuration is accepted; `routing_alias_conflicts` reports nothing (it covers routing candidates). EXECUTION (`LLMClient._binding`): context window 32768, the default runtime adapter, no `extra_body`, temperature 0.2 (the primary's). ASSESSMENT (`role_binding_for_model("reviewer")`): 65536, `ollama_native`, `{"top_p": 0.5}`, 0.7. The Reviewer's call would use the `ollama` adapter while its binding names `ollama_native`; the settings digests differ. **B-P2** (scope of the generation clock): none outside a run; two claims inside one run return the same start; a nested mutating boundary is the same run and returns the same start; a second top-level run has a new run id and a new clock |
| `check_citations_b.py` | Checks the citations first made in Phase B and runs its negative controls | `check_citations_b_output.txt`: 26 file entries, 73 symbols, 11 controls, 0 problems |
| `crossmap_check.py` | Cross-map consistency over `docs/architecture/*.md`: every row ID defined in its home map, every reference, references to undefined rows, rows defined twice, rows no other document cites; with `--fields`, selected fields of every full-field authority row for the integration reading | `crossmap_check_output.txt`: AUTH 83, SM 22, MUT 80, FR 114, BYP 93, GAP 35 defined; 0 dangling references; 33 double definitions, all intended (the A1/A2 completion and the A1b characterization table); 53 rows cited by no other document (to be linked by the Phase-C stage and integration documents) |
| `registry_root_map.py` | Maps every non-closed registry row to one class and root; fails unless all 141 are mapped exactly once | Writes `../../REGISTRY-ROOT-MAP.md`: 141 rows; ROOT_GAP 71, STALE_BOOKKEEPING 11, DUPLICATE_SUPERSEDED 5, DISPROVED 2, INTENTIONAL_CONTRACT 13, FUTURE_FEATURE 36, PHASE_D_BLOCKER 1, PHASE_D_ITEM_NON_BLOCKING 2 |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout):

```
PYTHONPATH=$PWD:$PWD/tests PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR=<tmp>/state KRIYA_LOG_DIR=<tmp>/logs KRIYA_MODEL_RUNTIME_PROBE=0 <venv python> docs/architecture/evidence/b/b_probe.py
python3 docs/architecture/evidence/b/check_citations_b.py .
python3 docs/architecture/evidence/b/crossmap_check.py . [--fields 300]
python3 docs/architecture/evidence/b/registry_root_map.py .
```

B-P2 creates one scratch Git repository under the probe's temporary directory and opens three mutating runs in it;
the control state it writes is inside that scratch workspace.

Corrections made in Phase B:

- The first citation run failed one control: `routing_alias_conflicts(` is also called by `core/model_routing.py`
  (the routing planner re-checks configurations built in code). The statement that the rule covers routing
  candidates only is unchanged; the allowed list was completed.
- **A wrong statement of slices A5 and A6, and of the first Phase B checkpoint, was found and corrected**: the
  generation clock is one per top-level run, not one per unit invocation (contradiction X-01 of the audit; B-P2;
  gap ledger, "Corrections to earlier checkpoints"). It was found by comparing `AUTH-A1-08` with `AUTH-A5-12`.

Traced, not measured, in Phase B (homes: `06-GAP-AUTHORITY-AUDIT.md` sections 3 and 12): the single writer of
learned knowledge; the release cache schema and writers; run-id selection on a resumed enforce run; survival of
enforce unit checkpoints across the sandbox reset; the RunRecord's terminal transition and the reconciliation
order; the stop-reason prefix protocol; the plan-hash assignments.
