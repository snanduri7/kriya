# PRD-009 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

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
