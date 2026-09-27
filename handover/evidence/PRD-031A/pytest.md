# PRD-031A pytest evidence (user-run, 2026-09-27)

| Run | Revision | Result |
|---|---|---|
| Focused (the handover's command) | 3737c32 | all passed |
| Full `.venv/bin/pytest` | 3737c32 | 6711 passed, 2 failed, 56 deselected (27:39) |
| The 2 failures | 3737c32 | Own bugs, both fixed in 1152ad5: <br>• `test_bootstrap_contract::test_every_production_annotation_resolves_at_runtime` (a TYPE_CHECKING-only annotation import) <br>• `test_qual_environment_identity::test_generic_code_never_branches_on_a_provider_or_uses_its_native_api` (the INF-001 tripwire on a waiver provider comparison) |
| Targeted re-run: bootstrap_contract, qual_environment_identity, prd031a_static_analysis, prd031a_semgrep_adapter, prd030_terminal_services, prd007_run_lifecycle, prd008_recovery, prd029_contract_lifecycle | 1152ad5 | 387 passed, 0 failed (30.9s) |
| Real pinned-scanner tier `-m live_static_analysis tests/test_prd031a_semgrep_live.py -v` (Semgrep 1.178.0 on the host, and the OCI image `semgrep/semgrep@sha256:32e45996...786b`) | 1152ad5 | 26 passed, 0 failed (84.6s) |

The delta 3737c32 → 1152ad5 is one import and one comparison, plus one new test. Every other test was green in the full run at 3737c32.

Verdict: VERIFIED_BY_PYTEST. Live-model: NOT_REQUIRED. The real pinned-scanner tier passed.
