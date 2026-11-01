# Malformed / absent canonical manifest: what the milestone path does (2026-11-01, owner-requested characterization)

Scratch tests (stored as `.py.txt`, never committed as tests: a passing assertion would bless the behaviour).
- `registry_layer_fake_engine.json`: gates faked (`tests/test_milestones.py::_committing`), final code. MEASURED: a milestone that commits a
  malformed `pyproject.toml` COMPLETES with an EMPTY registry and its dependent proceeds; a later milestone that turns a RECORDED valid
  manifest malformed also completes (drift is reported only if a consumer validates that provider: `ARTIFACT_DRIFT` for M1). The only later
  signal is a typed `ToolchainResolutionError` escaping `run_milestones` from the plan-wide replay phase (after all units are durably complete).
- `real_engine_partial.json`: the real engine. Case 1 (no manifest) = success, empty registry (VALID: artifact not applicable). Cases 2a/2b ended
  `milestone_failed` (M1 / M2); the failure REASON was not captured in that run, so it is NOT asserted here (consistent with the same
  Python toolchain resolver, UNKNOWN). Maven/npm manifests were not measured.
- Distinction the fix must make (not implemented here): "artifact not applicable" (marker file absent) vs "artifact expected but derivation
  indeterminate" (marker present, underivable) - without making the derivers fail on every unrecognized file.
Registry row: ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001.
