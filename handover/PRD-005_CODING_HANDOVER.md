# PRD-005 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `34c8712` in the target checkout (PRD-004 independently verified).
- Final revision / working-tree diff ID: the local commits containing this handover; obtain with `git log -2`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-005.
- Requirements completed:
  - Documented the pre-change transaction algorithm and its existing guarantees.
  - Canonically preflights duplicate/aliased targets, workspace escapes, directory targets, expected existence, invalid create/delete identity, and every expected content revision before source mutation.
  - Stages and fsyncs replacement contents before mutation, uses atomic replacement, preserves existing file permissions, and fsyncs affected directories where supported.
  - Snapshots every target before mutation and conservatively marks the current target before each filesystem syscall, so a fault raised immediately after a successful replace/unlink still restores create, modify, and delete paths.
  - Converts controlled commit failures into `BatchCommitError` and records `ROLLED_BACK`; rollback/evidence uncertainty records `UNCERTAIN`.
  - Persists versioned, content-free commit evidence under `.kriya/control/commits/` with explicit `IN_PROGRESS`, `COMMITTED`, `ROLLED_BACK`, and `UNCERTAIN` states. Absence resolves as `NOT_STARTED`.
  - Returns a dictionary-compatible `BatchCommitResult` carrying the final typed evidence object.
  - Refuses a new batch and refuses WorkflowController enforce/resume before planning when prior durable commit evidence is in-progress/uncertain or unreadable.
  - Added explicit create/modify/delete existence identity to WorkflowController's terminal batch.
- Requirements deliberately not implemented: automatic recovery of an uncertain SIGKILL state. The task requires fail-closed detection; deciding whether operator recovery should finish or revert an ambiguous commit belongs with PRD-007/008 lifecycle recovery.

## Files changed
- Production: `kriya/workflow/edit_safety.py`, `kriya/workflow/workflow_controller.py`.
- Tests: `tests/test_prd005_commit_transactions.py`, `tests/test_workflow.py`, `tests/test_workflow_controller_enforce.py`.
- Docs/decisions: this handover, tracker row, and `handover/evidence/PRD-005/*`. `.eie/DECISIONS.md` remains absent.

## Pre-change reproduction
- Command: `reproduce.py` was run in an unmodified PRD-004 checkout; it injected a fault immediately after the first real atomic replacement.
- Observed failure/gap: the call raised `OSError`, but `first.txt` remained new, `second.txt` remained old, and no commit evidence existed. The target was appended to the rollback list only after the write helper returned, so an after-side-effect failure escaped rollback tracking.
- Evidence: `handover/evidence/PRD-005/pre-change-reproduction.txt` and `pre-change-algorithm.md`.

## Implementation summary
The batch function now separates preflight, staging/snapshot, durable intent, mutation, rollback, and terminal evidence. All candidate bytes and original snapshots are ready before the first source mutation. Each source operation remains individually atomic. Controlled failure restores every possibly touched target and its mode, while process death leaves an fsynced in-progress record that blocks silent continuation. Evidence contains paths, operation types, expected hashes, states, and result hashes; it never persists source contents.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---|---|
| `pytest -q tests/test_prd005_commit_transactions.py` | 11 | 0 | 0 | 0.15s | Mixed create/modify/delete fault injection, preflight, permissions, evidence, real SIGKILL |
| Focused edit/filesystem/controller regression command | 316 | 0 | 0 | 14.88s | Includes all new PRD-005 tests |
| `pytest -q tests/test_workflow.py` | 864 | 0 | 0 | 428.87s | 121 existing warnings |

The coding agent did not run the full project suite. Independent user verification owns that long-running gate.

## Static/lint/architecture checks
- `python3 -m py_compile` passed for all touched Python files.
- `git diff --check` passed.
- Ruff passed for the focused production module and new PRD-005 test module. Evidence: `handover/evidence/PRD-005/lint.txt`.

## Live test additions
- Required by instruction: NO.
- Test file/case: no model test; the subprocess crash test is deterministic and local.
- Environment prerequisites: POSIX signals for the SIGKILL proof; the test is skipped nowhere on the verified macOS environment.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- A true SIGKILL can leave a mixed set of complete old/new files; no local multi-file filesystem transaction can prevent that. Kriya now durably detects the uncertainty before mutation and refuses silent continuation afterward.
- Directory fsync is best-effort on platforms that do not support opening/fsyncing directories.
- Commit evidence is an interim versioned record designed for PRD-007 to reference or migrate; it does not replace the future canonical RunRecord.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent. The durable commit-state contract is recorded in production types, schema version, tests, and this handover.

## Evidence artifacts
- `handover/evidence/PRD-005/pre-change-algorithm.md`
- `handover/evidence/PRD-005/pre-change-reproduction.txt`
- `handover/evidence/PRD-005/focused-regressions.txt`
- `handover/evidence/PRD-005/workflow-regressions.txt`
- `handover/evidence/PRD-005/lint.txt`

## Verification-agent handoff
From the target checkout, independently run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_prd005_commit_transactions.py \
  tests/test_edit_revisions.py \
  tests/test_policy_filesystem_authorized_writer.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_workflow.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-005/user-full.xml
```

Report complete pass/fail/skip/deselection counts and every skip reason. Write `handover/PRD-005_PYTEST_VERIFICATION.md`. PRD-005 remains `READY_FOR_PYTEST_VERIFICATION` until the independent full non-live suite passes. No live-model verification is required.

## Reopening addendum (2026-09-24, independent review)
Findings closed in `kriya/workflow/edit_safety.py::commit_revision_grounded_batch` (order of operations now
documented in its docstring):
- **Temp-file leak / untraceable leftovers**: IN_PROGRESS evidence is persisted BEFORE staging and staging runs
  inside the same try/finally as the apply loop, so a staging or evidence fault leaves no `.kriya-stage-*` file;
  operations now record `stage_prefix` (derived from the transaction id) so a post-SIGKILL leftover is
  attributable, and `candidate_revision` so recovery can classify each target as applied / not applied by hash.
- **Rollback failure misreported**: a failed rollback step, or a failure to persist ROLLED_BACK evidence after a
  clean rollback, now raises `UncertainCommitError` (previously a plain `BatchCommitError` while the evidence
  said UNCERTAIN / stayed IN_PROGRESS). Intent-persistence failure before any write is a plain
  `BatchCommitError` with no evidence file (= not started).
- **Committed bytes != verified bytes**: `StagedFileWrite` gained `content_bytes` and `mode`; staging writes
  bytes. The enforce controller now passes the candidate's exact bytes (CRLF / non-UTF-8 preserved) and mode
  (new executable files such as `mvnw` keep +x). `content` stays the decoded text used for revision identity,
  consistent with `read_file_revision()`.
- **Symlink targets** are refused before mutation (a link would have been replaced by a regular file and could
  not be restored); **base_path** containment is now checked alongside target containment.
- **Evidence growth**: a zero-write batch still performs the uncertainty check but writes no evidence; terminal
  (committed / rolled-back) evidence is pruned to the newest 50 after each commit; in-progress, uncertain and
  unreadable evidence is never pruned.

Tests added (`tests/test_prd005_commit_transactions.py`): intent-persistence failure, staging failure mid-batch,
fault before a replace takes effect, rollback failure -> UNCERTAIN and later commits refused, rolled-back
evidence write failure -> IN_PROGRESS + UncertainCommitError, revision conflict at every position, CRLF /
non-UTF-8 / new-file-mode fidelity, symlink refusal, base escape, empty batch, evidence traceability fields,
pruning. The previously `pragma: no cover` rollback-failure path is now covered.
Remaining for PRD-008: an operator recovery command that uses `candidate_revision`/`stage_prefix` to resolve
an uncertain commit (the review's "no recovery path" finding).
