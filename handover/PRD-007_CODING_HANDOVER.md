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

## Reopening addendum (2026-09-24, independent review)
Findings closed (see `handover/PRD-001_011_INDEPENDENT_REVIEW.md` §PRD-007):

**Schema v2 (`kriya/control/run_record.py`).** Commits are explicit cycles
`commits: [{transaction_id, intent, candidate_hash, result}]` because one run can make several real-workspace
commits (a milestone sequence commits once per milestone). `commit_intent/commit_transaction_id/commit_result`
describe the current cycle and are reset by each `begin_commit`; once terminal, `commit_result` is the run-level
summary (`COMMITTED`, `PARTIALLY_COMMITTED`, `ROLLED_BACK`, `NOT_COMMITTED`, `UNCERTAIN`, `NO_COMMIT`). v1
records migrate deterministically (one cycle from `commit_intent`; v1's fabricated `intent + NO_CHANGES` becomes
no cycle and `NO_COMMIT`; the unused `store_revisions`/`candidate_revision` are dropped). An unknown version,
unknown field or malformed value raises `UnsupportedRunRecordError`.
`STORE_CLASSIFICATION` classifies every store as authoritative (RunRecord, commit evidence) or derived; derived
stores already stamp `_run_record: {classification, run_id, revision}` on every write, which is how each one
identifies the RunRecord revision it derives from (req 2 / acceptance 3), instead of the record copying store
revisions.

**Invariants that make a missed hook fail loudly.** COMMIT_ELIGIBLE/COMMITTED are reachable only via
`begin_commit` (requires an intent and a fresh transaction id, refuses while a cycle is unsettled) and
`settle_commit` (COMMITTED -> COMMITTED; ROLLED_BACK/NOT_COMMITTED -> CANDIDATE so a caller may retry;
UNCERTAIN -> terminal). SUCCESS only after a settled COMMITTED cycle, or with no cycle at all; it can never
claim COMMITTED without one. FAILURE is refused while a cycle is unsettled/uncertain - such a run can only end
UNCERTAIN. Every terminal record carries a commit result. The permissive `RUNNING -> anything` row is gone;
`annotate()` changes evidence fields only.

**The record reflects real stages.** `_complete_successful_run` no longer walks RUNNING->...->COMMITTED from the
result payload; it moves an already-settled record to SUCCESS, and a contradiction fails closed. Stage markers
(`mark_run_stage`: PLANNING, CANDIDATE, VERIFYING) are best-effort progress evidence. Commit hooks key on "this
commit targets the run's own workspace" (`owning_run`), not `mutation_depth == 1`: enforce's nested engine in
the candidate worktree is correctly not recorded, and milestone commits (nested) now are.

**One shared commit seam (`kriya/workflow/terminal_commit.py`).** Both terminal commit sites (enforce controller
and the generation workflow's sandbox apply) use `materialize_candidate` + `commit_terminal_candidate`: durable
intent before the first workspace byte (a commit whose intent cannot be persisted is refused:
`RUN_RECORD_INTENT_NOT_PERSISTED`), cycle settled from the commit evidence, a unique transaction id per cycle
(the legacy path used none before). PRD-005 follow-up: the generation workflow's terminal apply (the default
`kriya generate` path, `workflow_controller.enabled: false`) still committed text-decoded content; it now
commits exact bytes and mode like the controller.

**Evidence populated honestly.** `effective_config_fingerprint` = the checkpoint's own
`compute_config_fingerprint(kernel.config.model_dump())` (so PRD-008 can compare like with like);
`approved_plan_hash` at approval and at commit; `obligation_ledger_revision/hash` (`ObligationLedger.fingerprint()`,
append count + content hash); `candidate_hash` (path, bytes, mode, deletion); `verification_evidence_ids`
(`commit:<txid>`); `retry_counters`/`retry_state_reference` (generation workflow). `model_runtime_fingerprint_ids`
stays empty until PRD-013/014 and must be read as UNVERIFIED, never as a match.

**Persistence (`kriya/control/persistence.py`).** Strict loader: `load_run_record` returns None only when no
record exists and raises `UnreadableRunRecordError` for corrupt/unknown-schema/foreign-workspace/renamed records
(previously "corrupt means absent", so the uncertainty gate failed open). `scan_run_records` ignores files whose
names are not run ids and reports unreadable records; the enforce gate refuses on them
(`RUN_RECORD_UNREADABLE`) and on any record with an unsettled/uncertain cycle
(`UNCERTAIN_RUN_RECORD_COMMIT_STATE`); resume refuses when its referenced record is unreadable. Compare-and-swap
now takes the record revision and the file's content revision from ONE read and writes revision-grounded on it -
a concurrent writer in between is a `FileRevisionConflict`, not a lost update; an unreadable record is never
overwritten as if absent. `kriya tools execute` records `DIRECT_TOOL_EXECUTION` (success or failure), never a
commit.

**Review fixes before hand-off (same day).** (1) SUCCESS no longer requires the lifecycle to sit at COMMITTED:
a later stage that changes nothing (final milestone integration pass) moves the record to CANDIDATE, and the run
still succeeds when its LAST cycle committed - the derived summary is the rule. (2) The controller's commit
transaction id is now unique per cycle (`<run_id>-<12 hex>`), not `run_id`, which nested executes share and
resume/trace overrides reuse; one commit-evidence file only ever describes one transaction. (3) A stage marker
can no longer set `commit_result`/`terminal_status`. (4) `STORE_CLASSIFICATION` is honest: traces.db,
milestone run state, the proposal store and knowledge staging are `independent` (not stamped, never consulted
for lifecycle truth), not "derived".

**Accepted limitations (disclosed).**
- The single-read compare-and-swap detects a writer that changed the record since that read; the
  revision-grounded write itself re-reads then replaces, so the cross-process guarantee rests on the workspace
  run lock (only the owning run writes its record).
- Run records are never pruned and every enforce run scans all of them. Pruning is unsafe while any record may
  hold an unsettled cycle; it is deferred to PRD-008's settlement/recovery work.
- A record whose commit cycle is unsettled/uncertain (crash between intent and result, or a failed write of the
  COMMITTED result) blocks enforce runs in that workspace until settled. Settling from the commit evidence and an
  operator recovery command are PRD-008's scope; until then the stricter gate blocks more often, by design.
- The generation workflow still routes a failed terminal commit into its generic attempt-failure handler (as
  before). After an UNCERTAIN commit, every further commit is refused by both the RunRecord (terminal) and the
  commit evidence, so nothing can be committed, but remaining retry attempts may still spend model calls.
- In-place generation (`worktree_path == workspace_path`) is unreachable in production (`create_git_worktree`
  failure raises); it is not modelled separately.

**Tests.** `tests/test_prd007_run_lifecycle.py`: v1 migration, unsupported schemas, store classification;
begin/settle/terminal invariants, multi-cycle summaries, annotate limits; scan ignores stray files and reports
corrupt/unknown/foreign/renamed records; unreadable record never overwritten; concurrent writer between check
and write is a conflict; byte/mode-exact seam; nested own-workspace commits recorded and candidate commits not
(the milestone shape; `run_milestones` is proven coordinated); PARTIALLY_COMMITTED after a later conflict;
refused commit when intent cannot persist; entry-point config fingerprint; a REAL forked process killed
(`os._exit`) between intent and first apply leaves COMMIT_ELIGIBLE + unsettled cycle + IN_PROGRESS evidence and
blocks the next enforce run; unreadable record blocks enforce while a stray file does not; the REAL enforce
controller in a REAL git worktree records NEW->RUNNING->PLANNING->CANDIDATE->VERIFYING->COMMIT_ELIGIBLE->
COMMITTED->SUCCESS with plan/ledger/candidate/evidence fields; a REAL `WorkflowEngine.run_generation_workflow`
(model mocked) applies through the seam byte-exactly with NEW->RUNNING->CANDIDATE->COMMIT_ELIGIBLE->COMMITTED->
SUCCESS and the resume config fingerprint. Updated: `test_run_record.py` (v2 API; success without a commit is
`NO_COMMIT`), `test_resume_integrity.py` (`begin_commit`), `test_prd004_commit_failure.py` (seam patch points;
a failed COMMITTED-result write ends UNCERTAIN, never a clean record), `test_workflow_controller_enforce.py`
(patch point moved to `kriya.workflow.terminal_commit`).
