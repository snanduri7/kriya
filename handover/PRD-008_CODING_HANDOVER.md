# PRD-008 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: `ebe4994` (PRD-007 verified).
- Production implementation revision: `31d19da` in the target checkout.
- Final revision / working-tree diff ID: the handover commit following `31d19da`.
- Kriya version: 0.1.0.

## Scope implemented
- Instruction file: `KRIYA_ALL_IMPLEMENTATION_INSTRUCTIONS_v1.0.md`, PRD-008.
- Requirements completed:
  - Added an explicit fingerprint-to-stage invalidation matrix for source/workspace, config, goal, plan, obligation ledger, skills, model runtime, containment, toolchain, verification policy, and commit state.
  - Added machine-readable resume decisions with fingerprint, action, invalidated stages, and reason.
  - Preserved safe no-change and legacy-checkpoint behavior while invalidating only dependent stages for changed optional fingerprints.
  - Made uncertain RunRecord commit intent/result refuse normal resume before any model call.
  - Persisted COMMIT_ELIGIBLE before actual workspace mutation and COMMITTED afterward in both legacy WorkflowEngine and enforced WorkflowController terminal paths.
  - Added controller refusal when a prior RunRecord has uncertain mutation authority.
  - Kept PRD-005 uncertain commit-evidence refusal intact.
- Requirements deliberately deferred: computing exact model/toolchain/containment fingerprints belongs to their later owning PRDs; PRD-008 defines and enforces their resume contract when present.

## Files changed
- Production: `kriya/workflow/checkpoint.py`, `kriya/workflow/workflow.py`, `kriya/workflow/workflow_controller.py`, `kriya/control/run_coordinator.py`, `kriya/control/persistence.py`.
- Tests: `tests/test_resume_integrity.py`.
- Docs/decisions: this handover, tracker row, `handover/evidence/PRD-008/*`. `.eie/DECISIONS.md` is absent.

## Pre-change reproduction
- Observed gap: resume had no explicit invalidation matrix or machine-readable decisions and did not consult RunRecord commit intent/result.
- Evidence: `handover/evidence/PRD-008/pre-change-reproduction.txt`.

## Implementation summary
`validate_resume_against_reality` remains the shared comparison boundary and now returns deterministic structured decisions. Ordinary mismatches invalidate named dependent stages and start fresh. An uncertain commit is different: it returns REFUSED and blocks all model work until explicit recovery. The commit lifecycle is written before and after the real mutation call, so process death leaves an actionable durable state.

## Tests run by coding agent
| Command | Passed | Failed | Skipped/Deselected | Duration | Notes |
|---|---:|---:|---:|---|---|
| Required resume/checkpoint/RunRecord/controller/CLI/ownership regressions | 435 | 0 | 0 | 27.53s | Includes parameterized fingerprint matrix and uncertain-commit integration |
| `tests/test_workflow.py -k 'resume or checkpoint'` | 13 | 0 | 851 deselected | 5.54s | Existing workflow resume behavior |

The coding agent did not run the full project suite; independent user verification owns that gate.

## Static/lint/architecture checks
- Compileall and `git diff --check` passed.
- Ruff passed for the new focused PRD-008 test module.

## Live test additions
- Required by instruction: NO.
- Test file/case: deterministic local resume and commit-state tests.
- Environment prerequisites: POSIX filesystem/process behavior.
- Exact command for Live-Model Verification Agent: not applicable.
- Expected invariant/evidence: not applicable.

## Known limitations / residual risks
- Fingerprints introduced by later model-runtime and containment PRDs remain optional until those owners populate them.
- Refused uncertain commits require an explicit recovery workflow; PRD-008 intentionally does not guess whether to complete or roll back an ambiguous commit.

## Decisions recorded
- `.eie/DECISIONS.md` entries: none; the file is absent.

## Evidence artifacts
- `handover/evidence/PRD-008/pre-change-reproduction.txt`
- `handover/evidence/PRD-008/required-regressions.txt`
- `handover/evidence/PRD-008/workflow-resume-regressions.txt`
- `handover/evidence/PRD-008/lint.txt`

## Verification-agent handoff
Run:

```bash
.newvenv/bin/python -m pytest -ra \
  tests/test_resume_integrity.py \
  tests/test_checkpoint_control_plane_hashes.py \
  tests/test_state001_checkpoint_workspace_identity.py \
  tests/test_run_record.py \
  tests/test_workflow_controller_enforce.py \
  tests/test_cli_smoke.py \
  tests/test_run_ownership.py

.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-008/user-full.xml
```

Record complete counts and skip reasons in `handover/PRD-008_PYTEST_VERIFICATION.md`. No live-model verification is required.
