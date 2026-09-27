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

## Final verification (per the "PRD-031A Final Verification" directive, 2026-09-27, at 85bf3d5)

| Gate | Result |
|---|---|
| Focused command from the handover | 1828 passed, 1 failed (see the environment note below). Passes with the venv on PATH, and is a subset of the full run below, which is fully green. |
| Full `.venv/bin/pytest`, venv `bin` on PATH (as in the operator's terminal) | **6714 passed, 0 failed**, 56 deselected (27:41) |
| `-m live_static_analysis tests/test_prd031a_semgrep_live.py -v` | **26 passed**: host and OCI, with the pinned digest and `--pull never` asserted on the prepared argv |
| Leftover `kriya-oci` containers after the runs | **0** |
| `doctor --production`, static analysis disabled (unchanged `generate-production.yaml`) | PRODUCTION_READY=true; all 7 static_analysis rows NOT_APPLICABLE, required=false, never a PASS verdict (`doctor-production-static-analysis-disabled.json`) |
| `doctor --production`, enabled and required (user-run with a separate trust file) | PRODUCTION_READY=true; configuration, provider, capability, coverage and egress PASS; prerequisites and waivers NOT_APPLICABLE (`doctor-production-static-analysis-enabled.md`) |

**Environment note (not a defect).** A first full run from the agent's tool shell gave 6705 passed and 9 failed. That shell has no `pip` or `python` on PATH: `FileNotFoundError: 'pip'` in `test_validate_policy_audit.py`, and the Django command test in `test_workflow.py`. Those tests invoke bare `pip`/`python` by design. With the venv's `bin` on PATH, as in the operator's terminal and in the user's earlier full run, the rerun above has 0 failures.

**Acceptance mapping.**
- Disabled analysis is DISABLED/NOT_CONFIGURED, never PASS: core tests and the disabled doctor run.
- UNKNOWN, UNAVAILABLE and incomplete required coverage block: core tests, and the live oversized and missing-image cases.
- ACCEPTED_RISK exits 0 but stays distinct in text and JSON: `test_cli_prints_the_accepted_risk_banner...` and `test_valid_waiver_is_accepted_risk_never_pass`.
- Both commit paths require fresh evidence, and missing or stale evidence is refused before any workspace write: core boundary and guard tests, and the live stale-evidence cases, host and contained.
- PRD-004/005 commit safety stays green: within the full run.
- No new P0/P1: the registry adds STATIC-ANALYSIS-INPLACE-BASELINE-001, P2 OPEN, non-blocking, target post-PRD-032.

**Pinned runtime and rule-pack evidence** is kept in this folder:
- Semgrep 1.178.0 (pipx);
- image `semgrep/semgrep@sha256:32e459968daabe7ab86968184a29109b9564aa00392401156f9788452b42786b`;
- demo rule pack `java-security.yml`, pack digest `e92e2501…0277`;
- fixture rule-pack digest `6abc9d9e…fee591` (tests/fixtures/static_analysis/semgrep/1.178.0/manifest.json).

**Final verdict: VERIFIED.**
