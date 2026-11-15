# Evidence for Phase B

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. Nothing here changes runtime code,
tests, goldens or the registry. No model was called and no network request was made.

| File | What it is | Result |
|---|---|---|
| `b_probe.py` | Model-free probe B-P1: two bindings that name one model with different settings | `b_probe_output.txt` |
| `b_probe_output.txt` | Output on this baseline | The configuration is accepted; `routing_alias_conflicts` reports nothing (it covers routing candidates). EXECUTION (`LLMClient._binding`): context window 32768, runtime adapter the default, no `extra_body`, temperature 0.2 (the primary's). ASSESSMENT (`role_binding_for_model("reviewer")`): 65536, `ollama_native`, `{"top_p": 0.5}`, 0.7. The Reviewer's call would use the `ollama` adapter while its binding names `ollama_native`; the two settings digests differ |
| `check_citations_b.py` | Checks the citations first made in Phase B and runs its negative controls | `check_citations_b_output.txt`: 20 files, 58 symbols, 11 controls, 0 problems |
| `registry_root_map.py` | Maps every non-closed registry row to one class and root; fails unless all 141 are mapped exactly once | Writes `../../REGISTRY-ROOT-MAP.md`: 141 rows; ROOT_GAP 71, STALE_BOOKKEEPING 11, DUPLICATE_SUPERSEDED 5, DISPROVED 2, INTENTIONAL_CONTRACT 13, FUTURE_FEATURE 36, PHASE_D_BLOCKER 1, PHASE_D_ITEM_NON_BLOCKING 2 |

How it was run (from the closure worktree root; interpreter of the `Kriya-product-72h` checkout):

```
PYTHONPATH=$PWD:$PWD/tests PYTHONDONTWRITEBYTECODE=1 KRIYA_STATE_DIR=<tmp>/state KRIYA_LOG_DIR=<tmp>/logs KRIYA_MODEL_RUNTIME_PROBE=0 <venv python> docs/architecture/evidence/b/b_probe.py
python3 docs/architecture/evidence/b/check_citations_b.py .
python3 docs/architecture/evidence/b/registry_root_map.py .
```

A correction made before the checkpoint: the first citation run failed one control. `routing_alias_conflicts(` is
also called by `core/model_routing.py` (the routing planner re-checks configurations built in code). The statement
in the audit, that the rule covers routing candidates only, is unchanged; the allowed list was completed.

Traced, not measured, in Phase B (homes: `06-GAP-AUTHORITY-AUDIT.md` section 3): the single writer of learned
knowledge; the release cache schema and writers; run-id selection on a resumed enforce run; survival of enforce unit
checkpoints across the sandbox reset; the RunRecord's terminal transition and the reconciliation order.
