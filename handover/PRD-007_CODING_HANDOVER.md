# PRD-007 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `db63ab0` (PRD-006 verified).
- Production implementation revision: `d267c3a` in the target checkout.
- Final revision / working-tree diff ID: the handover commit following `d267c3a`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-007.
- Requirements completed:
  - Added a versioned, content-free `RunRecord` containing all required identity, source, config/model, goal/plan/ledger, candidate, verification, retry, commit, lifecycle, terminal, revision, and timestamp fields.
  - Added explicit validated lifecycle transitions and immutable terminal states.
  - Added atomic RunRecord persistence with optimistic revision checks that reject stale writers.
  - Integrated RunRecord creation with `begin_mutating_run` and durable SUCCESS, FAILURE, and UNCERTAIN completion with commit results.
  - Persisted the proven success sequence through candidate, verification, commit eligibility, committed, and success states.
  - Added RunRecord derivation metadata to control documents, registries, approved plans, decision-ledger entries, and checkpoints without copying their payloads into RunRecord.
  - Preserved backward compatibility for stores/checkpoints without derivation metadata.
  - Added real process-crash evidence proving the last durable lifecycle state survives and the kernel ownership lock releases.
- Requirements deliberately deferred: resume-time interpretation and invalidation of interrupted records belongs to PRD-008.

## Files changed
- Production: `kriya/control/run_record.py`, `kriya/control/run_coordinator.py`, `kriya/control/persistence.py`, `kriya/control/decisions.py`, `kriya/workflow/checkpoint.py`, `kriya/workflow/workflow_controller.py`.
- Tests: `tests/test_run_record.py`.
- Docs/decisions: this handover, tracker row, and `handover/evidence/PRD-007/*`. `.eie/DECISIONS.md` is absent.

## Pre-change reproduction
- Command: production search for `RunRecord` and `load_run_record` at base `db63ab0`.
- Observed failure/gap: no canonical lifecycle schema, persistence path, revision guard, or coordinator-owned durable lifecycle existed.
- Evidence: `handover/evidence/PRD-007/pre-change-reproduction.txt`.

## Implementation summary
The coordinator creates revision 1 before yielding mutation authority. Each transition writes a complete atomic document under `.kriya/control/runs/<run_id>.json` and requires the exact prior revision. Nested workflow calls share one record. The outer mutation boundary records terminal success or failure; ambiguous commit exceptions record UNCERTAIN. Other control stores remain specialized and carry only a run ID/revision derivation reference.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---|---|
| Required control/state/checkpoint/controller/CLI regression set | 511 | 0 | 0 | 27.91s | Includes stale writer, lifecycle, real crash/reload, ownership, registries, controller and CLI |

The coding agent did not run the full project suite; independent user verification owns that gate.

## Static/lint/architecture checks
- Compileall passed for touched Python modules.
- `git diff --check` passed.
- Ruff passed for the new RunRecord, coordinator changes, and focused test module.

## Live test additions
- Required by instruction: NO.
- Test file/case: deterministic local persistence and real subprocess crash tests only.
- Environment prerequisites: POSIX process/file-lock behavior.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- PRD-008 must decide whether an interrupted nonterminal record is safely resumable or invalid.
- Later tasks own population of fingerprints not yet available at run start, including exact model-runtime qualification and final plan/ledger hashes.
- Commit evidence remains its own authoritative specialized store; RunRecord records lifecycle disposition and references rather than copying evidence payloads.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent.

## Evidence artifacts
- `handover/evidence/PRD-007/pre-change-reproduction.txt`
- `handover/evidence/PRD-007/required-regressions.txt`
- `handover/evidence/PRD-007/lint.txt`

## Verification-agent handoff
Run from the target checkout:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_run_record.py \
  tests/test_run_ownership.py \
  tests/test_control_persistence.py \
  tests/test_control_state.py \
  tests/test_control_plane_end_to_end.py \
  tests/test_control_decisions.py \
  tests/test_control_contracts.py \
  tests/test_control_artifacts.py \
  tests/test_checkpoint_control_plane_hashes.py \
  tests/test_state001_checkpoint_workspace_identity.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_cli_smoke.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-007/user-full.xml
```

Report complete counts and skip reasons in `handover/PRD-007_PYTEST_VERIFICATION.md`. No live-model verification is required.
