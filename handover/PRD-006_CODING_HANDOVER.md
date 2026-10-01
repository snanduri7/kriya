# PRD-006 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `617ad4f` in the target checkout (PRD-005 independently verified).
- Production implementation revision: `eff63b9` in the target checkout.
- Final revision / working-tree diff ID: the local handover commit following `eff63b9`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-006.
- Requirements completed:
  - Added a focused coordinator that acquires the existing POSIX workspace lock, canonicalizes workspace identity, captures the base git revision, creates a run ID and scoped `RunContext`, and always expires/releases it.
  - Added a capability validator that rejects missing, expired, malformed, and wrong-workspace contexts.
  - Added a backward-compatible async gateway that automatically starts a coordinated run for legacy direct callers and reuses a valid active context for nested controller/engine/milestone calls.
  - Protected `WorkflowEngine.run_generation_workflow`, `WorkflowController.execute`, `WorkflowController.execute_milestones`, and `run_milestones` at their public mutation boundaries.
  - Changed all existing mutating CLI lock blocks to call `begin_mutating_run`; existing lock diagnostics remain unchanged.
  - Retained read-only command behavior without an exclusive mutation lock.
  - Added direct API, context lifecycle, workspace binding, cleanup, and contention tests while retaining the existing real two-process file-lock and CLI contention coverage.
- Requirements deliberately not implemented: persistent lifecycle state. PRD-007 owns the canonical durable `RunRecord`; PRD-006 supplies its run-begin context and identity boundary.

## Files changed
- Production: `kriya/control/run_coordinator.py`, `kriya/control/run_ownership.py`, `kriya/cli.py`, `kriya/workflow/workflow.py`, `kriya/workflow/workflow_controller.py`, `kriya/workflow/milestones.py`.
- Tests: `tests/test_run_ownership.py`.
- Docs/decisions: this handover, tracker row, and `handover/evidence/PRD-006/*`. `.eie/DECISIONS.md` remains absent.

## Pre-change reproduction
- Command: an unmodified `617ad4f` archive held the real workspace lock and invoked `WorkflowController.execute` directly with an `AsyncMock` triage probe.
- Observed failure/gap: the controller body reached triage while another owner held the workspace lock, proving direct API callers bypassed ownership.
- Evidence: `handover/evidence/PRD-006/pre-change-reproduction.txt`.

## Implementation summary
`begin_mutating_run` is now the single high-level ownership gateway. It obtains the existing nonblocking `flock`, then creates a workspace-bound capability containing the coordinator run ID, canonical path, stable workspace identity, and starting git revision. A private lease expires the capability before lock release. Public async mutation APIs use `coordinated_mutation`: direct legacy calls safely acquire a context, explicit `run_context` calls validate it, and nested workflow calls reuse the active context without nested locking. Cleanup uses context-manager `finally` paths, so normal return, exceptions, and `KeyboardInterrupt` release ownership; process termination remains covered by the kernel file-lock semantics.

## Tests run by coding agent
| Command | Passed | Failed | Skipped | Duration | Notes |
|---|---:|---:|---:|---|---|
| `pytest -q tests/test_run_ownership.py tests/test_workflow_controller_enforce.py tests/test_cli_smoke.py` | 362 | 0 | 0 | 18.31s | Required PRD-006 regression command; includes real two-process contention tests |
| `pytest -q tests/test_proposal_promotion.py` | 52 | 0 | 0 | 7.64s | Preserves proposal refusal and generation-boundary behavior; 8 pre-existing runtime warnings |

The coding agent did not run the full project suite. Independent user verification owns that long-running gate.

## Static/lint/architecture checks
- `python3 -m compileall -q` passed for all touched Python modules and the changed test file.
- `git diff --check` passed.
- Ruff passed for the new focused coordinator module. The repository's broad touched-file Ruff invocation still reports established findings in large legacy modules unrelated to this change, so it was not used as a PRD-006 pass criterion.

## Live test additions
- Required by instruction: NO.
- Test file/case: no model test; ownership and contention are deterministic local OS behavior.
- Environment prerequisites: POSIX `fcntl.flock` on macOS/Linux.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- The ownership primitive is intentionally single-host and POSIX-only, as documented by CONC-001.
- Direct proposal promotion performs its read-only approval validation before the decorated engine mutation boundary. This preserves the existing guarantee that a refused direct proposal creates no workspace files; its first source-mutating operation still cannot bypass the coordinator.
- The context contains starting source revision and identity but is not durable. PRD-007 adds the persistent lifecycle record and terminal-state integration.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent. The gateway and capability contract are recorded in the focused production module, tests, and this handover.

## Evidence artifacts
- `handover/evidence/PRD-006/pre-change-reproduction.txt`
- `handover/evidence/PRD-006/required-regressions.txt`
- `handover/evidence/PRD-006/proposal-regression.txt`
- `handover/evidence/PRD-006/lint.txt`

## Verification-agent handoff
From the target checkout, independently run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_run_ownership.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_cli_smoke.py \
  tests/test_proposal_promotion.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-006/user-full.xml
```

Report complete pass/fail/skip/deselection counts and every skip reason. Write `handover/PRD-006_PYTEST_VERIFICATION.md`. PRD-006 remains `READY_FOR_PYTEST_VERIFICATION` until the independent full non-live suite passes. No live-model verification is required.

## Reopening addendum (2026-09-24, independent review)
Blocking finding closed: every real enforce-mode structured subtask raised `InvalidRunContextError` - the
controller runs the `@coordinated_mutation` engine against its `.kriya/worktree` candidate while the active
RunContext is bound to the real workspace. Earlier tests were green only because fixtures mapped the worktree to
the workspace or replaced the engine with an undecorated fake.

- `authorize_candidate_workspace()` (run_coordinator): the owner of an isolated candidate registers it on the
  active lease right after creating it (the enforce controller does so after `create_git_worktree`).
  `require_mutating_run` accepts the run's workspace or an explicitly authorized candidate of the same lease;
  never inferred from path containment (a scoped snapshot sandbox may live outside the workspace); expires with
  the run. A nested begin on a candidate joins the run - no second lock, no second RunRecord.
- Lease cleanup hardened: a failure to persist the terminal record in `begin_mutating_run` (except or finally)
  or in the decorator's failure path is logged and can no longer mask the run's own error or skip
  `lease.active = False` / ContextVar reset - so a same-process caller (REPL) can never reuse a capability
  whose lock was released.
- `kriya tools execute` now goes through the same gateway for possibly-mutating calls:
  `BaseTool.mutates_workspace(arguments)` fails closed (True, incl. every MCP tool); filesystem read/list,
  git status/diff/log/branch/blame, search and ast opt out. A mutating call while another run owns the CWD
  workspace prints `[Workspace Locked]` and exits 1; its RunRecord goes RUNNING -> SUCCESS on success.

Req 3 note: the `coordinated_mutation` compatibility adapter (acquire a context when none is supplied) remains
the production default; commit primitives (`commit_revision_grounded_batch`, `AuthorizedFileWriter`) still do
not call `require_mutating_run` themselves - every real-workspace call site is reached only through a
coordinated entry point. Enforcing at the primitive would break ~dozens of direct unit callers and sandbox-only
writes; recorded as an accepted residual rather than silently claimed.

Tests (`tests/test_prd006_candidate_workspace.py`): REAL git repo + REAL create/remove_git_worktree + engine
stand-in carrying the REAL decorator (and a proof the production method carries it): subtask runs in the
candidate under the same run_id, one RunRecord, SUCCESS/COMMITTED, workspace changed only by the terminal
commit; unauthorized other workspace refused; authorized out-of-tree candidate accepted and expiring with the
run; lock + capability released when the terminal record write fails; mutating tool call refused while the
workspace is owned (file not written); read-only tool call takes no lock. `test_cli_smoke`'s shell test now runs
in tmp_path so its ownership record stays out of the checkout.
