# PRD-004 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `03dbfb4` (Wave 0 verification gate closure).
- Final revision / working-tree diff ID: the local commit containing this handover; obtain with `git log -1`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-004.
- Requirements completed:
  - All blocking terminal checks now evaluate `plan_workspace_path` before the real-workspace batch commit: migration completion, stack contract, preserved-reference integrity, unresolved terminal obligations, and ArtifactRegistry derivation.
  - Migration identity remains resolved once from the immutable source baseline; only completion is evaluated against the candidate.
  - Candidate-derived artifact facts are held in memory and persisted only after a successful workspace commit.
  - Failed, indeterminate, exception-raising, and needs-review terminal outcomes discard the candidate and reset sandbox-only completion state without calling the terminal commit.
  - Post-commit approved-plan and ArtifactRegistry persistence failures are classified separately and cannot change an already verified success.
  - Structured lifecycle events cover `terminal_gates_started`, one `terminal_gate_outcome` per gate, `commit_eligible`, and `workspace_commit_completed`. Event-handler failures are recorded as observability errors and do not grant or revoke correctness authority.
- Requirements deliberately not implemented: none. Atomicity inside the batch commit is the explicitly separate PRD-005 scope.

## Files changed
- Production: `kriya/workflow/workflow_controller.py`.
- Tests: `tests/test_workflow_controller_enforce.py`.
- Docs/decisions: this handover, `handover/TASK_STATUS_TRACKER.csv`, and `handover/evidence/PRD-004/*`. `.eie/DECISIONS.md` is absent; no decision file was created.

## Pre-change reproduction
- Command: the seven PRD-004 tests were copied into an unmodified `03dbfb4` checkout and run with `pytest -q tests/test_workflow_controller_enforce.py -k 'terminal_gate_failure_discards_candidate_before_commit or terminal_events_and_gate_inputs_precede_one_commit or post_commit_registry_persistence_failure_does_not_rewrite_verification'`.
- Observed failure/gap: 7 failed. The pre-change controller called the commit before terminal failures, ran gates on the real workspace, emitted none of the required lifecycle events, and converted post-commit registry persistence failure into `needs_review`.
- Evidence: `handover/evidence/PRD-004/pre-change-reproduction.txt`.

## Implementation summary
The enforce transaction now has one candidate-verification phase followed by one commit eligibility decision. Each terminal gate receives the isolated candidate explicitly, and all gate results are aggregated before the commit function can be reached. On success, one revision-grounded batch applies the candidate. Artifact facts derived during verification are persisted afterward. Persistence and telemetry errors are returned in dedicated fields without rewriting terminal correctness.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---|---|
| New PRD-004 tests only | 7 | 0 | 0 | 1.50s | Five failure cases, ordered success/one commit, post-commit persistence classification |
| `pytest -q tests/test_workflow_controller_enforce.py` | 265 | 0 | 0 | 14.91s | Full controller module |
| Required five-file regression command | 335 | 0 | 0 | 15.25s | Controller, control plane, obligations, migration, artifacts |

The coding agent did not run the full project suite. Independent user verification owns that long-running gate.

## Static/lint/architecture checks
- `python3 -m py_compile` passed for the production and test files.
- `git diff --check` passed.
- Ruff reports the same 10 findings on base and current versions of these legacy files (import ordering and existing F821/F401/F841/E402/F811 findings). No new Ruff finding points into the PRD-004 implementation or added tests. Evidence: `handover/evidence/PRD-004/static-checks.txt`.

## Live test additions
- Required by instruction: NO.
- Test file/case: none.
- Environment prerequisites: none beyond the project test environment.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- PRD-004 establishes pre-commit correctness ordering. Filesystem rollback and crash atomicity within `commit_revision_grounded_batch()` remain PRD-005.
- A failing event subscriber may prevent that subscriber from receiving telemetry; the returned result records the observability error and verification remains based solely on deterministic gates.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent and this task implements the already-specified candidate-before-commit rule.

## Evidence artifacts
- `handover/evidence/PRD-004/pre-change-reproduction.txt`: seven failures against base revision.
- `handover/evidence/PRD-004/required-regressions.txt`: 335 passed.
- `handover/evidence/PRD-004/static-checks.txt`: compile/diff checks and base/current Ruff comparison.

## Verification-agent handoff
From the target checkout and its passing environment, independently run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_workflow_controller_enforce.py \
  tests/test_control_plane_end_to_end.py \
  tests/test_obligations.py \
  tests/test_migration.py \
  tests/test_control_artifacts.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-004/user-full.xml
```

Report complete pass/fail/skip/deselection counts and every skip reason. Write `handover/PRD-004_PYTEST_VERIFICATION.md`. PRD-004 remains `READY_FOR_PYTEST_VERIFICATION` until that independent full non-live run passes. No live-model verification is required.
