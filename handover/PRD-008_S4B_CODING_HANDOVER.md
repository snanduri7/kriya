# PRD-008 S4b - Milestone Completion Revalidation - Coding Handover

## Status
READY_FOR_PYTEST_VERIFICATION

S4b is committed locally and not pushed. S5 must not start until S4b's pytest run is verified.

## Source path traced before implementation
- **The sequence driver.**
  - `kriya/cli.py` `generate --from-milestones` calls `load_or_resume_milestone_run_state()`.
  - That result is passed, under `begin_mutating_run`, to `_dispatch_milestones()`.
  - `_dispatch_milestones()` calls either `WorkflowController.execute_milestones()` (`@coordinated_mutation`) or `kriya/workflow/milestones.py::run_milestones()` (`@coordinated_mutation`).
- **Per milestone,** the driver calls `WorkflowEngine.run_generation_workflow()`.
  - The workflow's terminal apply goes through `terminal_commit.commit_terminal_candidate()`.
  - That call records the RunRecord cycle (`begin_run_commit`/`settle_run_commit`) and writes commit evidence at `.kriya/control/commits/<txid>.json`, schema 2, with exact `before`/`after` state and `candidate_hash`.
- **The whole sequence is one run.**
  - Every milestone's commit is a separate cycle on one RunRecord (nested `coordinated_mutation` calls reuse the owning run).
  - The integration pass commits the same way.
- **The gap:** `milestones.py` skipped a milestone on `if milestone.id in run_state.completed_milestone_ids: continue`. Only two things removed an id from that list:
  - a change to a provider's contract shape (MA7-C3);
  - consumer artifact drift, which aborts the run rather than re-running anything.
- **`execute_milestones()` also wrote the ControlState too early.** It computed `milestone_states` (done/pending) from `completed_milestone_ids` and saved them before `run_milestones()` ran.

## Current bug reproduced (pre-fix code)
Scratch script:
1. Complete M1, which writes `a.py`, then M2, which depends on M1.
2. Edit `a.py`.
3. Rerun the sequence.

Result: the engine was called only for integration (`calls == [3]`). M1 and M2 were skipped, and M2 was treated as built on output that no longer existed. After the fix, the same script runs M1, then M2, then integration, with M1 `CHANGED/OUTPUT_CHANGED` and M2 `CHANGED/UPSTREAM_INVALIDATED`. A clean rerun then skips both (`MATCH`).

## Spec deviation (challenged, with reasons)
**The spec's rule:** "every path affected by that commit still has the exact expected post-commit state".

**Why it's wrong for milestones:** milestones routinely modify each other's files; `pom.xml` is the canonical case, and it's why `check_dependency_regression` exists. Applied per milestone, the rule would mark M1 stale on every clean rerun as soon as M2 had touched a file M1 wrote, and that would cascade through the whole sequence.

**What was implemented instead:**
- **Invariant.** The workspace must equal what the verified, recorded commit history of the sequence predicts.
- **Expected state.** A path's expected state is the post-state of its latest verified writer in the ordered commit ledger.
- **Attribution.** A mismatch is charged to that writer, and only when the writer is a completed milestone.
  - A path last written by the integration pass or by an unfinished milestone is not checked, because those run again anyway.
  - The earlier milestone that also wrote that path stays valid.
- **Coverage.** `test_milestones_modifying_each_others_files_stay_valid_when_nothing_changed` covers this; the spec had no such test.

## Schema additions
- **`MilestoneRunState` (sidecar `.kriya/milestones/<group>.json`).** All fields are optional on load:
  - `completion_proofs`: `{milestone_id: MilestoneCompletionProof}`.
  - `commit_ledger`: an ordered list of `MilestoneCommitEntry`.
  - `last_reuse_assessment`: the most recent assessment.
  - `milestone_completion_schema`: `1`. An absent value marks a legacy sidecar.
  - `load_or_resume_milestone_run_state()` now also copies these fields from the sidecar. Without that, every CLI rerun would lose them.
- **`RunRecord`:**
  - New annotatable field `milestone_reuse` (optional, schema 3 unchanged; v3 is local and unreleased).
  - New `run_coordinator.owning_run_commits()`, a read-only public accessor for the owning run's cycles.
- **`recovery.py`:** the path helpers are now public, for reuse: `observe_path_state`, `matches_file_state`, `contained_workspace_path`.

## Completion-proof format (`kriya/workflow/milestone_completion.py`)
```
MilestoneCompletionProof {schema_version: 1, milestone_id, run_id, transaction_ids: [..], completed_at}
MilestoneCommitEntry     {milestone_id | null (integration), run_id, transaction_id, candidate_hash,
                          operations: [{path, operation: CREATE|MODIFY|DELETE,
                                        post_state: {exists, sha256, mode}}],
                          recorded_at, evidence_error}
```
- **Where the ledger entries come from.** They are taken from the owning RunRecord's new COMMITTED cycles during the milestone's whole loop (`owning_run_commit_count()` before, `record_milestone_commits()` after). The operations are copied from each cycle's commit evidence (`target_path`, `kind`, `after`), never from the Planner's or Developer's `result["files"]`.
- **Which cycles are recorded.**
  - Retry iterations inside a milestone.
  - Commits of milestones that were later abandoned (recorded before `milestone_failed` returns).
  - Integration commits.
- **When the ledger is saved.** It is persisted right after the loop succeeds, before later steps (such as the artifact registry) can fail and return.

## Commit-evidence binding (in order; any failure means UNVERIFIED, and the milestone reruns)
1. `transaction_id` is valid.
2. The RunRecord `run_id` loads strictly. Missing or pruned → `RUN_RECORD_MISSING`; an exception → `RUN_RECORD_UNREADABLE`.
3. The record has a cycle with this txid (else `TRANSACTION_MISMATCH`), and its result is `COMMITTED` (else `COMMIT_NOT_COMMITTED`). The record's own lifecycle doesn't matter; a `RECOVERED` record is accepted.
4. The evidence file exists (else `COMMIT_EVIDENCE_MISSING`) and is readable (else `COMMIT_EVIDENCE_UNREADABLE`).
5. The evidence is schema 2 or later (else `COMMIT_EVIDENCE_LEGACY`), with state `COMMITTED`.
6. `candidate_hash` is non-empty and identical in the evidence, the cycle and the ledger entry (else `PROOF_EVIDENCE_MISMATCH`).
7. The ledger entry's (path, kind, post_state) set equals the evidence's (else `PROOF_EVIDENCE_MISMATCH`).
8. The proof's txids exist in the ledger for this milestone and run (else `TRANSACTION_MISMATCH`).
9. For each path the milestone still owns, the workspace is compared byte-exactly (see below).

**Workspace comparison.** It uses `lstat` and never follows a symlink; the path must be contained in the workspace. It hashes raw bytes with sha256 and compares the mode. A mismatch gives one of `OUTPUT_MISSING`, `OUTPUT_CHANGED`, `MODE_CHANGED` or `DELETED_PATH_RECREATED`, and the status becomes `CHANGED`. Text is never normalised.

## Invalidation algorithm (`assess_completed_milestone_reuse` + `revalidate_completed_milestones`)
1. Verify every ledger entry. Only verified entries take part in path ownership, and the latest verified writer of each path owns it.
2. Assess each completed milestone: `MATCH`, `CHANGED`, or `UNVERIFIED` with reasons. The vocabulary is PRD-008's `FingerprintStatus`; `NOT_APPLICABLE` means the milestone isn't completed, so no decision is made.
3. The rerun set is every non-MATCH completed milestone plus every milestone that isn't completed. Expand it to a fixpoint:
   - a VALID milestone with an ancestor in the rerun set (via `depends_on`) → `CHANGED/UPSTREAM_INVALIDATED`;
   - a VALID milestone that shares a ledger path with a rerun milestone it is not an ancestor of → `CHANGED/SHARED_PATH_WITH_RERUN`. That rerun could rewrite the path after this milestone was skipped.
4. For each milestone that will rerun:
   - drop it from `completed_milestone_ids` and add it to `stale_milestone_ids`;
   - drop its proof and `verification_commands`, and its paths from `established_file_context`, so pre-edit content is never fed back as "established";
   - delete its checkpoints (`_invalidate_milestone_checkpoints`).
5. Save the sidecar, annotate the RunRecord with `milestone_reuse`, log each decision, and return it in the result (`milestone_reuse`, on success and on `milestone_failed`).
6. **Where it runs:**
   - At the start of `run_milestones()`.
   - In `execute_milestones()` before `milestone_states` is derived, so the ControlState never marks as done a milestone that is about to rerun.
   - It is idempotent.

## Backward compatibility
- **A legacy sidecar** (no `milestone_completion_schema`) loads unchanged. Each completed entry is `UNVERIFIED/LEGACY_STATE_UNVERIFIED` and reruns, along with its dependents; nothing is invented. Once those milestones re-complete, the sidecar carries real proofs.
- **A current sidecar with a missing or unreadable proof** gives `COMPLETION_PROOF_MISSING`.
- **A milestone that committed nothing** gives `UNVERIFIED/NO_COMMITTED_OUTPUT`. This follows the spec's hard invariant (a skip needs a committed transaction). Cost: a no-op root milestone makes its dependents rerun. Relaxing that is a user decision.
- **Retention.** `prune_run_state` now protects every run id a sidecar ledger references. An unreadable sidecar contributes no references, because its proofs are unusable anyway.

## Tests
- **New file:** `tests/test_prd008_s4b_milestone_completion.py`, 26 cases:
  - **A** – `execute_milestones` refusal with the same reason and status as direct `execute()`, before triage or any record.
  - **B** – real `WorkflowEngine`, resume inside a milestone. When the fingerprints match, the plan is reused (RunRecord `resume_decision` includes `plan`, one fewer LLM call). When the workspace changed, nothing is reused.
  - **C** – a real subprocess `os._exit` during M2's commit (partial and not-applied variants). A rerun is refused; `recover_workspace` (with `--complete-partial` where needed) leaves the record RECOVERED; the rerun then skips M1 on its original transaction and runs M2.
  - **D–H** – skip when clean; OUTPUT_CHANGED with UPSTREAM; OUTPUT_MISSING; DELETED_PATH_RECREATED; git reset.
  - **I** – missing evidence (UNVERIFIED); corrupt evidence (refused by the S1 gate first, since an unreadable file blocks commits); missing RunRecord; ledger tampered against the evidence.
  - **J** – legacy sidecar.
  - **K** – selectivity, plus the shared-path rule.
  - **L** – CRLF vs LF, non-UTF-8 bytes that decode to the same text, mode change.
  - **Wiring** – retention protection; interim ControlState; established context dropped; shared files and integration commits stay valid.
- **Updated tests:**
  - `tests/test_milestones.py`:
    - The resume-skip test now asserts the new rule: a completion with no proof reruns (it was renamed).
    - The contract-invalidation test patches revalidation out, to keep testing the MA7-C3 mechanism on its own.
  - `tests/test_workflow_controller.py`: `_milestone_run_state()` now returns a real empty `MilestoneRunState` instead of a `MagicMock`, because revalidation reads the real fields.

### Tests run by the coding agent
**Standing-rule conflict, disclosed.** The S4b spec says the coding agent must run pytest. This repo's CLAUDE.md and the user's standing memory rule say the coding agent never runs pytest; the user runs every pytest command. I kept the standing rule. The user can override it explicitly for S4b.

What was run: every test function in the affected modules, called as plain Python functions (scratch runner: it awaits coroutines and supplies the `tmp_path`/`git_workspace` fixtures; tests needing other fixtures were skipped).

| Module | Result |
|---|---|
| test_prd008_s4b_milestone_completion | 26 passed, 0 failed |
| test_milestones | 68 / 0 |
| test_workflow_controller | 31 / 0 (1 needs monkeypatch, skipped) |
| test_control_contracts | 40 / 0 |
| test_prd008_commit_state_gate | 26 / 0 |
| test_prd007_run_lifecycle | 28 / 0 |
| test_prd008_recovery | 20 / 0 (3 skipped for fixtures) |
| test_run_record | 9 / 0 |
| test_run_ownership | 21 / 0 (12 skipped for fixtures) |
| test_prd008_resume_fingerprints | 47 / 0 (15 skipped for fixtures) |
| test_resume_integrity | 18 / 0 (2 skipped) |
| test_state001_checkpoint_workspace_identity | 12 / 0 (23 skipped) |
| test_bootstrap_contract | 18 / 0 |
| test_cli_smoke | 4 / 0 |

`test_workflow_controller_enforce` failed 52 functions under this runner both before and after S4b (the same set, diffed). Those tests depend on conftest autouse fixtures that only pytest applies, so this is not a regression signal. They must be verified under pytest.

ruff: no new findings. The 7 pre-existing findings in touched files are unchanged, and the new files are clean.

### Verification commands (user runs)
```bash
.venv/bin/pytest tests/test_prd008_s4b_milestone_completion.py tests/test_milestones.py \
  tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py tests/test_control_contracts.py \
  tests/test_dispatch_generation.py tests/test_subtask_checkpoint.py tests/test_checkpoint_control_plane_hashes.py \
  tests/test_control_plane_end_to_end.py tests/test_run_ownership.py tests/test_run_record.py \
  tests/test_prd007_run_lifecycle.py tests/test_prd008_recovery.py tests/test_prd008_commit_state_gate.py \
  tests/test_prd008_resume_fingerprints.py tests/test_resume_integrity.py \
  tests/test_state001_checkpoint_workspace_identity.py tests/test_prd005_commit_transactions.py \
  tests/test_bootstrap_contract.py tests/test_cli_smoke.py -ra
.venv/bin/pytest   # full non-live suite
```
Full non-live pytest result: pending (user).

## Remaining risks
- **A crash between a commit and the sidecar save** leaves that commit out of the ledger. If it overwrote a path a completed milestone owns, that milestone is judged CHANGED on the next run. This is conservative: extra reruns, never a false skip.
- **The shared-path rule is static.** It uses the paths a rerun milestone wrote last time. If a rerun writes a new path that an unrelated, skipped milestone owns, the next run charges that path to the rerun, not to the milestone skipped earlier. This is the same hazard any sequential plan has when two unrelated milestones edit one file; it is disclosed rather than solved (solving it means re-ordering the loop, which is out of scope).
- **Rerunning is not the same as rolling back.** A stale milestone reruns on top of the current (edited) workspace. S4b decides reuse; it doesn't restore content.
- **An unreadable sidecar** gives no retention protection. Its proofs couldn't be used anyway.
- **Resume selection inside a milestone.** With `--resume` and no id, each milestone's call resumes the workspace's latest checkpoint. A checkpoint from another milestone is rejected by the goal/workspace fingerprints (a fresh run, never a wrong reuse), but a milestone may miss its own older checkpoint. This is unchanged by S4b; PRD-008A is the right place to fix it.
- **PRD-008A.** This gap existed because milestone execution has its own "completed work reuse" concept. Once direct and milestone execution share `ExecutionPlan → WorkUnit[]`, completion reuse should become a single shared invariant.
