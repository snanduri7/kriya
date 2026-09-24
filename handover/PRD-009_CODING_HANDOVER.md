# PRD-009 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (reopened; see "Reopen - independent review closure" below; batch PRD-009 + PRD-010)

## Source identity
- Base revision: `f70c1a5` in the target checkout (PRD-008 verified; tree-equivalent coding checkout revision `2508ad1`).
- Production implementation revision: `d7163de` in the target checkout.
- Final revision / working-tree diff ID: the handover commit following `d7163de`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-009.
- Requirements completed:
  - Added `runtime_profile: production` without changing the default or the `legacy`, `validated`, and `hardened` mappings.
  - Sealed WorkflowController and ExecutionPolicy in enabled/enforce mode.
  - Required a finite 3600-second generation budget, OCI target-code containment, and MCP containment before any configured MCP server can execute.
  - Selected `brownfield_full_regression_baseline_policy: required` until PRD-024 gives `auto` safe semantics.
  - Rejected contradictory explicit values with field-specific required/actual diagnostics while accepting identical values and unrelated settings.
  - Preserved runtime-profile authority classification for the scalar and all derived security controls.
  - Kept semantic-region enforcement disabled until PRD-028 establishes language support.
- Requirements deliberately not implemented: PRD-010 owns `kriya doctor --production` reporting for unavailable runtimes and unsupported semantic precision; PRD-009 exposes machine-readable fixed guarantees for that consumer.

## Files changed
- Production: `kriya/config/config.py`, `kriya/config/default_config.yaml`.
- Tests: `tests/test_config.py`, `tests/test_sec009_config_authority.py`.
- Docs/decisions: this handover, tracker row, `handover/evidence/PRD-009/*`. `.eie/DECISIONS.md` is absent.

## Pre-change reproduction
- Command: inspect the base revision's `_VALID_RUNTIME_PROFILES` and `runtime_profile_preset_fields()`.
- Observed failure/gap: only `legacy`, `validated`, and `hardened` existed; no production preset could seal execution policy, containment, runtime budget, or baseline policy.
- Evidence: `handover/evidence/PRD-009/pre-change-reproduction.txt`.

## Implementation summary
The profile expands before authority resolution, so derived production fields retain the profile's provenance and cannot launder authority. Dict-level resolution rejects contradictory explicit values before overwriting anything. A model-level validator also checks the fully effective object, closing direct `AppConfig` construction paths that bypass preset expansion. Candidate isolation, persistent workflow checkpoints/traces, and no host fallback remain existing fixed runtime mechanisms rather than new no-op switches.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---:|---|
| Config, authority, execution-policy wiring, and core containment suites | 111 | 0 | 0 | 8.00s | Includes safe/prohibited production matrix and derived authority checks |
| Controller, enforced-controller, deterministic MCP containment, combined closure, and dispatch suites | 329 | 0 | 3 | 49.08s | Three OCI integration cases skipped because Docker daemon is unavailable |

The coding agent did not run the full project suite; independent user verification owns that gate.

## Static/lint/architecture checks
- Compileall passed for all touched Python files.
- `git diff --check` passed.
- Ruff passed for all touched Python files.

## Live test additions
- Required by instruction: NO.
- Test file/case: deterministic config/authority/controller/containment tests only.
- Environment prerequisites: Python project dependencies; Docker is optional for the focused suite and unavailable cases skip explicitly.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- Production readiness still depends on PRD-010's deployment checks for the OCI runtime, persistence destinations, toolchain, and unsupported semantic precision.
- The production profile deliberately does not enable experimental semantic-region enforcement before PRD-028.
- OCI integration cases require a reachable Docker daemon; three were skipped in coding-agent verification.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent.

## Evidence artifacts
- `handover/evidence/PRD-009/pre-change-reproduction.txt`
- `handover/evidence/PRD-009/config-policy-containment.txt`
- `handover/evidence/PRD-009/controller-mcp-regressions.txt`
- `handover/evidence/PRD-009/lint.txt`

## Verification-agent handoff
Run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_config.py \
  tests/test_sec009_config_authority.py \
  tests/test_workflow_execution_policy_config_wiring.py \
  tests/test_process_controller_containment.py \
  tests/test_containment.py \
  tests/test_workflow_controller.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_tool003_p2_deterministic.py \
  tests/test_tool002_tool003_combined_closure.py \
  tests/test_dispatch_generation.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-009/user-full.xml
```

Record complete counts and skip reasons in `handover/PRD-009_PYTEST_VERIFICATION.md`. No live-model verification is required.


## Reopen - independent review closure (2026-09-25)

Source: `handover/PRD-001_011_INDEPENDENT_REVIEW.md` (PRD-009: minor issues). Implemented at `3fb4749`, on top of PRD-008A
(`590fa16`). The review's PRD-009 findings and how each one closed:

| Finding | Resolution |
|---|---|
| The exact-equality seal rejects a stricter `generation_time_budget_seconds` | Accepted as 3600 or a smaller positive integer (`bool` excluded) at all three enforcement points: the dict-level contradiction check, the preset expansion (the stricter value is kept with its own provenance and never loosened back to 3600), and the direct-`AppConfig` validator. `production_sealed_value_satisfied()` / `production_sealed_requirement()` are the one rule. None, 0, True and 7200 are still rejected. |
| `autonomy.egress_policy` not sealed | Sealed at `local_only`. The default is already `local_only`, so default users see no change. Added to the contradiction matrix and to the SEC-009 derived-field classification test; that test is now stricter. |
| Isolation, persistence and no-host-fallback are declared constants, not verified | `kriya doctor --production` (PRD-010, `d22cb8e`) now verifies each guarantee against the real deployment. `runtime.fixed_guarantees` is derived from the checks that verify it (see the PRD-010 handover). The constant stays as the shared vocabulary; the docstring and YAML say it is verified, not asserted. |
| The config docstring and YAML claim a doctor precision-boundary report that did not exist | The claim is now true: `semantic.precision_boundary` (PRD-010) reports `SEMANTIC_REGION_SUPPORTED_SCOPE`, which is owned by `semantic_region_authority.py`. |

**Operator note - re-approve production configs.** Sealing `autonomy.egress_policy` adds it to the production profile's
security-field set: at `590fa16` a `runtime_profile: production` config had 9 violations, at HEAD it has 10. A SEC-009
approval (`kriya authority approve`, or a `--trust-file`) is digest-bound to the exact set, so every existing production
approval stops matching and `load_config` denies until you re-approve or regenerate the trust file. This is the
correct fail-closed behaviour. Re-approve before the live `doctor --production` step, or the doctor reports only
`config.load` FAIL.

Assertion changes: none weakened. The contradiction matrix gained 4 rejection rows (egress, 7200, 0, True). The expansion
test asserts `egress_policy`. The SEC-009 derived-field list gained `autonomy.egress_policy`. The new tests are
`test_runtime_profile_production_keeps_a_stricter_explicit_deadline` and
`test_direct_production_app_config_accepts_only_an_equal_or_stricter_deadline`. The stricter-deadline test's provenance line only holds
when the field is itself a violation; the value assertion (1800 kept) is the real proof.

Non-pytest checks, plain-Python runner (not pytest): the touched production-profile tests pass (18 plus 3 SEC-009);
ruff F821/F401 are clean; `git diff --check` is clean.

Pytest (user) - the batch commands are in the PRD-010 handover:

```bash
.venv/bin/pytest -ra \
  tests/test_production_doctor.py tests/test_doctor_command.py \
  tests/test_config.py tests/test_config_command.py tests/test_config_extra.py \
  tests/test_autonomy_config_registry_hosts.py tests/test_sec009_config_authority.py \
  tests/test_execution_policy_config.py tests/test_workflow_execution_policy_config_wiring.py \
  tests/test_containment.py tests/test_containment_oci.py tests/test_containment_oci_registry_scoped_unit.py \
  tests/test_process_controller_containment.py tests/test_ver006_distrust_containment.py \
  tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py \
  tests/test_tool003_p2_deterministic.py tests/test_tool002_tool003_combined_closure.py \
  tests/test_dispatch_generation.py tests/test_plugins.py tests/test_plugins_command.py \
  tests/test_semantic_region_authority.py tests/test_bootstrap_contract.py
.venv/bin/pytest
```
