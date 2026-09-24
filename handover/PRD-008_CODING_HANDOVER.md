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

## Reopening addendum (2026-09-24, independent review) - S1: commit-state gate, exact evidence, reference-safe retention

Status: IMPLEMENTING (S1 of S1-S5 committed locally; S2-S5 pending). Findings closed here: review §PRD-008
"uncertain record blocks forever" groundwork (the gate half; `kriya runs recover` is S4) and the retention
hole found during planning.

**One gate for every mutating entry point.** `kriya/control/commit_state.py::assess_workspace_commit_state`
(RunRecords with an unsettled/UNCERTAIN cycle, unreadable records, IN_PROGRESS/UNCERTAIN/unreadable commit
evidence) is called by `begin_mutating_run` AFTER the workspace lock is taken and BEFORE the new run's record is
created, so the new record can never contaminate the assessment. Refusal = `UncertainWorkspaceStateError`
(lock released, no record written). `coordinated_mutation` turns it into the owner's structured refusal when the
owner defines `workspace_refusal_result` (WorkflowEngine -> dict, WorkflowController -> WorkflowResult with
`control_state=None, route=None`), else re-raises (milestones, future APIs). Every CLI `begin_mutating_run` site
prints `[Recovery Required]` and exits 1 (AST test enforces this for future sites). Previously only enforce mode
checked; `generate`/`fix`/milestones/proposal execution/tool writes could run model work on a half-committed
workspace. The controller's own enforce re-check now uses the same assessment (covers a controller entered inside
an already-owned run). Reason codes unchanged; the payload now reports every unsafe store at once (was
first-tier only) and carries `recovery_command`.

**Commit evidence schema 2** (`edit_safety.py`, v1 still loads): each operation records `kind`
(CREATE/MODIFY/DELETE) and exact `before`/`after` state `{exists, sha256 (bytes), mode}` - no magic null
hashes, CRLF/non-UTF-8 visible - plus top-level `candidate_hash` (`candidate_digest`, moved from
terminal_commit.py) equal to the RunRecord cycle's `candidate_hash`, linking the two for S4's
`--complete-partial` proof. Durability order (tested): RunRecord intent -> IN_PROGRESS evidence (fsync file+dir)
-> staged temp files (fsync) -> first replace. Deliberately NOT the order proposed in review item 10 (stage
before IN_PROGRESS): staged temp files live beside source files, so staging first would leave unattributed bytes
in the workspace after a crash.

**Reference-safe retention** (`kriya/control/retention.py`). The batch no longer prunes evidence by count
(pre-fix reproduction: 50 later commits deleted the COMMITTED evidence of an unsettled cycle, which recovery would
have read as "never started"). Mark-and-sweep: protected = non-terminal, commit-state-unknown,
checkpoint-referenced (`checkpoint.list_checkpoint_run_references`), caller-named, newest N terminal; a record and
the evidence its cycles reference are pruned as a unit; IN_PROGRESS/UNCERTAIN/unreadable evidence never; nothing
at all while any record is unreadable. Runs best-effort at the end of every run, under the lock.

**Tests changed:** `test_prd005_commit_transactions.py::test_terminal_evidence_is_pruned_but_uncertain_never_is`
replaced by `test_batch_never_prunes_evidence_on_its_own` (behavior deliberately moved); 
`test_prd007_run_lifecycle.py` unreadable test now expects both codes; `test_resume_integrity.py` uncertain-resume
test now expects the earlier coordinator refusal (validator REFUSED stays covered at unit level). New:
`tests/test_prd008_commit_state_gate.py`.

**S1 verification commands (user runs):**
```bash
.venv/bin/pytest tests/test_prd008_commit_state_gate.py -ra
.venv/bin/pytest tests/test_prd005_commit_transactions.py tests/test_prd007_run_lifecycle.py \
  tests/test_resume_integrity.py tests/test_run_ownership.py tests/test_run_record.py \
  tests/test_prd004_commit_failure.py tests/test_workflow_controller_enforce.py tests/test_cli_smoke.py -ra
```
Lint (coding agent, run): `ruff check` on every touched/new file passes; `compileall`, `git diff --check` clean.

**S1 user verification (2026-09-24):** both S1 commands above all green after test-ordering fix e74f79d
(`test_prune_never_removes_uncertain_or_unreadable_evidence...` planted bad evidence before its own commit; the
batch correctly refused). S1 = 46d2a33 + e74f79d.
