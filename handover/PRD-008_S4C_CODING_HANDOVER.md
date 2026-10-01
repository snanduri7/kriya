# PRD-008 S4c - Milestone Resume Closure - Coding Handover

## Status
USER-VERIFIED (2026-09-24) at `abadb4a`: full non-live suite (a superset of the focused command below) 5074 passed / 0 failed / 8 deselected / 152 warnings (1027 s). The review decision approved every sign-off listed under "Final review closure"; S4c is not reopened without new failing evidence.

S4c is committed locally and not pushed. S5 is unblocked. PRD-008A has not been started.

Commits: `34d11df`, `d1bfaf8`, `0762a0e`, `4ad70f2`, plus one final review-closure commit (see below). No history was rewritten.

## Final review closure (the last commit)
Decisions from the final S4c review, and how each is implemented:
1. **Shared files (approved, kept strict).**
   - A path's current bytes must equal the post-state of its latest verified writer.
   - When that writer superseded a completed milestone's write (the integration pass, or a milestone that did not finish), a mismatch is charged to the latest completed writer with the typed reason `CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE`.
   - The reason carries `divergence` (the underlying OUTPUT_CHANGED/OUTPUT_MISSING/MODE_CHANGED/...), `path`, `transaction_id` and `superseded_by`. It is no longer reported as a plain user edit.
   - A later writer that cannot itself be proven stays `COMMIT_LINEAGE_UNVERIFIED`.
2. **VERIFIED_NO_CHANGE through the fake engine (approved).** Real-engine coverage of the identical-bytes COMMITTED path is kept.
3. **Stricter no-change evidence: acceptance coverage.** See S4c-1 below.
   - A generic passing test is no longer enough.
   - Every structured acceptance criterion must be covered by a deterministic evidence item mapped to it.
   - A zero-test run is `NO_TESTS_EXECUTED`, never positive.
   - Free-text-only milestones get `ACCEPTANCE_COVERAGE_UNAVAILABLE`, so the proof is not issued.
4. **Model runtime vs toolchain.**
   - `model_runtime: NOT_APPLICABLE`: no model-produced evidence is reused.
   - `toolchain` is bound from PRD-008's own toolchain fingerprint, now the extracted `resume_fingerprints.toolchain_fingerprint()`, one source for direct resume and milestones. It is UNAVAILABLE until PRD-011, and no temporary fingerprint was invented.
   - At reuse, either side UNAVAILABLE gives `UNVERIFIED/TOOLCHAIN_IDENTITY_UNAVAILABLE`, and different identities give `CHANGED`.
5. **Workspace evidence scope.** `workspace_evidence_hash()`:
   - Always included: tracked files everywhere, and non-ignored untracked files.
   - Excluded: untracked files under the repository analyzer's generated-output directories. That is the existing set, now the shared constant `kriya/analyzer/analyzer.py::GENERATED_OUTPUT_DIRS`; its dot-prefix heuristic is deliberately not reused, so `.env` and `.github/` stay in.
   - Files a milestone committed are compared byte-exactly on their own, in any directory.
   - STATE-001's `compute_workspace_content_hash` is unchanged.
6. **Recovery-completed milestones.**
   - A commit finished by `kriya runs recover --complete-partial` is now reconstructed as a completion when its recovery provenance proves an exact completion (see S4c-2), with `completion_origin: RECOVERY`.
   - The recovered run's lifecycle and terminal status are untouched (RECOVERED, with NEEDS_REVIEW or FAILURE).
7. **Existing S4c behaviour preserved.** All earlier S4c tests still pass unchanged, except where the typed reason changed (item 1) or the gate-evidence shape gained `status` (item 3).
8. **Current workspace remains the source of truth.** No byte is restored before a rerun (Test K, user guide §3.4.1).
9. **The `[]` defect** is recorded separately in `handover/DEFECT_DEVELOPER_EMPTY_ARRAY_WRITTEN_AS_FILE.md`, with a reproduction, the captured model response and output, and the affected path. The production path is not changed.

**Both execution paths (standing rule: every resume/commit-state invariant proven for direct `generate` and the milestone sequence):**

| Invariant | Direct `generate` | Milestone sequence |
|---|---|---|
| Zero executed tests are `NO_TESTS_EXECUTED`, never positive | Produced in `run_generation_workflow` (`deterministic_gate_evidence`), so both paths emit it. Direct resume does not consume it. | Consumed by `acceptance_coverage` / `no_change_verification` (Tests B, `test_a_real_engine...` through the real engine). |
| Toolchain identity | One source, `resume_fingerprints.toolchain_fingerprint()`. `compute_resume_fingerprints` calls it, so direct-resume behaviour is unchanged (resume_fingerprints module 48/48). | The same function binds and compares no-change proofs (Test C and its toolchain-unavailable companion, Test B `toolchain`). |
| Recovery-completed commits | Direct resume after recovery stays governed by S3/S4's commit-state gate and ResumePlan, unchanged. There is no completion-reuse concept on that path. | Completion reuse through reconstruction (Tests F, G, S4b Test C). |
| A RECOVERED run never becomes SUCCESS | Unchanged: `RunRecord.recover` sets NEEDS_REVIEW or FAILURE. | Reconstruction only reads RunRecords. Test F asserts the recovered record's revision and status are unchanged. |
| Workspace evidence scope / lineage divergence | Not applicable: these are milestone-completion concepts. Direct checkpoints keep STATE-001's `compute_workspace_content_hash`, unchanged. | Tests D, E, I and the S4b shared-file test. |

**Status vocabulary.** No existing gate-level vocabulary tells a zero-test pass apart from a real one:
- `validation_baseline.TestStatus` is per test case (pass, fail, error, skip);
- `workflow_types.VerificationVerdict` is pass, fail or needs_review.

So the review's own names are used (`PASS_WITH_TESTS`, `PASSED`, `NO_TESTS_EXECUTED`, `FAILED`, `UNAVAILABLE`, plus `UNCOVERED` for a criterion), uppercase like `FingerprintStatus`.

**S4b test changes you should know about (both directed by this review):**
- **S4b Test C, stage2:** it now expects M2 to be reconstructed (`MATCH/COMPLETION_RECONSTRUCTED`), and M2 no longer regenerates (`engine.calls == ["INTEGRATION"]`). Before, it expected `UNVERIFIED/COMPLETION_RECONSTRUCTION_UNVERIFIED`. This is item 6. Stage1 (rolled back) is unchanged.
- **The S4b shared-file test:** it now expects `M2 CHANGED/CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE` instead of `OUTPUT_CHANGED`. Same strictness, typed reason (item 1).
- **Harness:** S4b's mid-commit crash script moved into `tests/_milestone_proof_harness.py` (`_crash_mid_commit`, `MID_COMMIT_OUTPUTS`) so S4c Tests F/G reuse it unchanged.

## Foundation: durable work-unit identity
Items 2 and 3 both need to know which milestone a commit or checkpoint belongs to. That identity must not come from list position, timestamps or model output.
- **The identity.** `milestone_work_unit(group, milestone)` is `{kind: "milestone", group_id, milestone_id, definition_digest}`. The integration pass uses `integration_work_unit(group, plan_digest)`.
- **Where it is recorded.**
  - The driver records the unit on the RunRecord (`active_work_unit`, annotatable) before each milestone's workflow calls and before integration, and clears it afterwards.
  - `RunRecord.begin_commit` copies it into every cycle (`cycle["work_unit"]`), so a commit is attributable from the moment its intent is durable, before any workspace byte changes.
  - `workflow.py::_save_stage_checkpoint` stores the owning run's unit in every checkpoint (`work_unit`), via `run_coordinator.owning_run_work_unit()`.
- **If recording fails.** The commit or checkpoint stays unattributed, which only ever leads to a rerun or a fresh start.

## S4c-4: shared-write commit lineage
S4b already built path ownership from verified entries only, so an unproven overwrite was never used to excuse an earlier milestone. Two things were missing:
- **The label.** An earlier milestone's mismatch on a path that a later, unverifiable commit wrote is now `UNVERIFIED/COMMIT_LINEAGE_UNVERIFIED`, naming that path and transaction. Before, it was `CHANGED/OUTPUT_CHANGED`, which read as if the user had edited it.
- **Ordering proof** (`_lineage_order_failures`).
  - Within one run, the ledger's order must follow the cycle order in `RunRecord.commits`.
  - Across runs the workspace lock serializes runs, so `RunRecord.created_at` is the only ordering evidence available. This is the only place a timestamp is used, and only because no transaction ordering exists across runs.
  - An entry out of order is `COMMIT_LINEAGE_UNVERIFIED` and never owns a path.
  - Without this, reordering the ledger would let an earlier writer "own" a file, and a revert of the later milestone's output would pass as MATCH. A test covers exactly that.
- **Final comparison against the last writer.**
  - Every path's current bytes are compared with the post-state of its latest verified writer, even when that writer is the integration pass or a milestone that did not finish.
  - A mismatch there is charged to the latest completed milestone that wrote the path (`CHANGED/CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE`, with `divergence` giving the underlying OUTPUT_* code), with `superseded_by` naming the later commit.
  - Before this fix, a user could delete a milestone's contribution from an integration-rewritten `pom.xml` and every milestone was still skipped. A clean rerun still converges.
  - Tests: `test_i_an_edit_to_a_path_the_integration_pass_owns_...` and `..._a_failed_milestone_wrote_last_...`.
- **Result:** every superseding transaction used to explain an earlier milestone must be present, readable, COMMITTED, bound to its RunRecord, and correctly ordered. The path's current bytes must also equal that last writer's post-state. Otherwise the result is UNVERIFIED/CHANGED, never MATCH.
- **S4b assertion changed (stricter, stated explicitly).** The second half of S4b's `test_milestones_modifying_each_others_files_stay_valid_when_nothing_changed` edited an integration-owned `pom.xml` and asserted both milestones MATCH; that was exactly this gap. It now asserts `M1 MATCH, M2 CHANGED/CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE`.

## S4c-3: milestone-aware checkpoint selection
`select_unit_checkpoint()` runs before every workflow call of a milestone, including the integration pass:
- **Selection.** With `--resume` and no id, the unit's own newest checkpoint is chosen by `work_unit` match (group, id, definition digest) and passed as `resume_id`, with `resume=False`.
  - The driver never passes a bare `resume=True` again. That is what made the workflow pick the newest checkpoint in the workspace.
  - If no compatible checkpoint exists, the call runs fresh with `NO_COMPATIBLE_MILESTONE_CHECKPOINT`.
- **An explicit `--resume-id`** is offered only to the unit that saved it. Every other unit gets `CHECKPOINT_IDENTITY_MISMATCH` and starts fresh.
- **Selection never declares a checkpoint safe.** The workflow still runs `validate_resume_against_reality()` and `build_resume_plan()` on it; Test G proves a stale checkpoint is still rejected.
- **Pre-S4c checkpoints** have no `work_unit` and are never offered to a milestone (disclosed; this only means a fresh start).
- **Invalidation.** `_invalidate_milestone_checkpoints` matches on `work_unit.milestone_id` when present, and on position only for legacy checkpoints.
- **Where decisions are recorded:** `milestone_reuse.events` (see "Decision records" below).

## S4c-2: reconstructing a completion after a crash between commit and state save
**The rule: a commit is not a completion.** After the workflow returns, the driver still runs deterministic steps that can fail: the dependency-regression check, dependency refresh, established context, and artifact derivation.
- Those steps are now one helper, `_complete_milestone()`, used by both the normal path and reconstruction.
- The judge (a model call) is not part of reconstruction. A reconstructed milestone therefore has no `verification_commands`, and the integration replay does not re-run its verification (disclosed).

`find_completion_to_reconstruct()` then `_reconstruct_completions()`, run inside revalidation before any reuse is assessed, for each not-yet-completed milestone whose upstream milestones are all complete:
1. **Collect the commits.** Take the COMMITTED cycles whose `work_unit` names this exact milestone, from the newest run that has any. Nothing else counts: no model-reported files, no plan file lists, no sidecar assertions, no timestamps.
2. **Skip milestones the driver saw fail.** If the ledger marks the loop failed (`loop_outcome = failed`), there is nothing to reconstruct.
3. **Check order.** These commits must be the newest in the sequence's history; otherwise `COMMIT_LINEAGE_UNVERIFIED`.
4. **Verify every commit** against its RunRecord cycle and evidence, as in S4b.
5. **A commit settled by `kriya runs recover`** counts only when its recovery provenance proves an exact completion (`_recovery_provenance_problem`). Otherwise the result is `RECOVERY_PROVENANCE_UNVERIFIED`. The provenance must show:
   - the tool was `kriya runs recover`;
   - the prior state was IN_PROGRESS or UNCERTAIN, meaning durable intent existed before the crash;
   - the outcome was COMMITTED or ROLLED_FORWARD;
   - every operation was APPLIED, or NOT_APPLIED and then rolled forward, never FOREIGN or AMBIGUOUS.

   Step 4 has already proven that the candidate hash, the COMMITTED state and every post-state match. The completion is then marked `completion_origin: RECOVERY`.
6. **Compare the workspace byte-exactly** with the final committed state. A user edit after the crash means refusal.
7. **Re-run the deterministic post-commit steps.** A dependency regression means refusal.
8. **On success,** write the completion proof with `reconstructed_from: {run_id, transaction_ids, completion_origin}` and `completion_origin` RECONSTRUCTED or RECOVERY. The later assessment reports that milestone as `MATCH` with `COMPLETION_RECONSTRUCTED`.
9. **On refusal,** the milestone gets `UNVERIFIED/COMPLETION_RECONSTRUCTION_UNVERIFIED` with the `cause`, and it runs. Verified commits are still appended to the ledger as history, so lineage stays correct.

Both crash windows are closed and tested:
- the commit is durable but no ledger entry exists;
- the ledger entry was saved but no proof exists (the S4b ordering).

**Recovery-completed commits (final review: closed).**
- **Why a recovered commit is safe to treat as a completion.** The one commit seam starts a commit (COMMIT_ELIGIBLE, durable intent) only for a verified candidate:
  - `workflow.py` commits only after candidate gates and terminal regression passed;
  - `workflow_controller.py` commits only when every subtask completed.

  So the interrupted candidate had passed its gates, and recovery finished exactly that candidate (same `candidate_hash`, byte-exact post-states). The model judge is skipped, as for any reconstruction.
- **The recovered run keeps its own status.** Reconstruction only reads RunRecords, so the run stays RECOVERED with NEEDS_REVIEW or FAILURE; Test F asserts its revision is unchanged. The run lifecycle, the commit result and the milestone completion proof stay separate concepts.

## S4c-1: VERIFIED_NO_CHANGE
**What I found first.** In the real engine, a "nothing to change" milestone rewrites its target file with identical bytes. That produces a COMMITTED cycle, so S4b already proves it byte-exactly and it converges. The test `test_a_real_engine_no_change_milestone_converges` covers this.
- The zero-cycle path only exists when the candidate is empty. My probes could not get a real engine run to succeed that way: a "No change needed" developer answer goes through repair retries and fails.
- VERIFIED_NO_CHANGE therefore covers the zero-cycle path, and is only reachable with deterministic evidence.

**Proof requirements** (`no_change_verification()`, final review). All of these must hold. Otherwise no proof is issued: the completion is `NO_COMMITTED_OUTPUT` with a `cause` taken from the proof's new `no_change_refusal`, and the milestone reruns.
- **The milestone's quality gates passed** (else `QUALITY_GATES_NOT_PASSED`), with zero committed cycles.
- **Structured acceptance criteria exist** (`MilestoneV2.acceptance`). Without them the result is `ACCEPTANCE_COVERAGE_UNAVAILABLE`: a free-text goal has no deterministic check, and no model opinion fills that gap.
- **Every criterion is covered** (`acceptance_coverage()`), else `ACCEPTANCE_COVERAGE_INCOMPLETE` with per-criterion statuses.
  - **The coverage map.** It is the workflow result's `acceptance_coverage`, one item per (criterion, evidence) pair: `{criterion_id, kind, selector, attempt, status, tests_executed}`. Kriya checks each item and never infers one.
  - **Kind:** compile, test, targeted_test, regression_test or run_verification. A gate of that family must itself have executed and passed in the final attempt, per `deterministic_gate_evidence`.
  - **Attempt:** the item's attempt must be that final attempt.
  - **Test items:** a test item counts only as `PASS_WITH_TESTS` with `tests_executed > 0`.
  - **Unknown criteria:** an item naming a criterion the milestone does not have covers nothing.
  - **Per-criterion status vocabulary:** `PASS_WITH_TESTS`, `PASSED`, `NO_TESTS_EXECUTED`, `FAILED`, `UNAVAILABLE`, `UNCOVERED`.
  - **No production producer of this map exists.** The criteria are free text, so VERIFIED_NO_CHANGE is unreachable in production today (see sign-offs).
- **`deterministic_gate_evidence` items now carry `status`:**
  - `PASS_WITH_TESTS`, `PASSED`, `FAILED`, `NO_TESTS_EXECUTED` (exited 0 after running nothing) or `UNAVAILABLE` (skipped or unconfirmed);
  - `passed` is True only for the first two;
  - only the final attempt counts.
- **A current verification-policy fingerprint** is required, else `VERIFICATION_POLICY_UNAVAILABLE`.
- **A workspace evidence identity** is required, else `WORKSPACE_IDENTITY_UNAVAILABLE`.

**What the proof is bound to:**
- `acceptance_coverage`;
- evidence ids `run:<id>:attempt:<n>:criterion:<cid>:<kind>:<selector>`;
- the passing gate evidence;
- the verification-policy fingerprint;
- the upstream proof identity;
- `workspace_content_hash`, which is now the `workspace_evidence_hash` value;
- `ledger_position`;
- `zero_mutations`;
- `toolchain` (PRD-008's fingerprint, UNAVAILABLE today);
- `model_runtime: NOT_APPLICABLE`;
- `obligations: NOT_APPLICABLE`.

**Reuse** (`_assess_no_change`).
1. **Checked first, CHANGED/`VERIFIED_NO_CHANGE_INVALIDATED` on any failure:**
   - the definition digest;
   - the verification policy;
   - the upstream identity;
   - the workspace evidence hash, which must equal the hash after the last verified ledger entry since the proof;
   - every committed path, byte-exact against its latest verified writer.
2. **Then the toolchain:** `UNVERIFIED/TOOLCHAIN_IDENTITY_UNAVAILABLE` while either side is unavailable, CHANGED if it differs.
3. **A later commit that cannot be verified** gives `COMMIT_LINEAGE_UNVERIFIED`.

**Workspace evidence scope** (`workspace_evidence_hash`). It works like STATE-001's scratch-index technique, in a separate function, so checkpoints are unaffected.
- **It stages:**
  - every non-ignored path, except untracked files under `GENERATED_OUTPUT_DIRS`;
  - then `git add -u`, so tracked files anywhere, even under `build/`, stay in scope.
- **Tests:**
  - `__pycache__`, `pkg/__pycache__` and `target/` output in a repo without `.gitignore` keep the proof valid (Test D);
  - a new untracked `src/new_module.py` or `config.yaml`, an edit to a tracked `build/settings.gradle`, and an edit to a milestone-committed `bin/tool.sh` each invalidate it (Test E).

**Your sign-off is needed on:**
1. **VERIFIED_NO_CHANGE is unreachable in production until two things exist:** a deterministic criterion-to-evidence producer (obligation/spec work), and PRD-011's toolchain identity.
   - Today a zero-write milestone reruns; with test evidence the proof is issued but reused as UNVERIFIED. This is the conservative result the review asked for.
   - The real engine's "no change" path is the identical-bytes COMMITTED one, which S4b proves.
2. **Your Test C ("VERIFIED_NO_CHANGE issued, clean rerun skips") cannot hold with the production default toolchain.** Item 4 makes toolchain-dependent evidence UNVERIFIED while the identity is unavailable, and no toolchain-independent evidence producer exists.
   - The test therefore injects a toolchain identity at the one PRD-008 source (`resume_fingerprints.toolchain_fingerprint`), the seam PRD-011 will fill. It shows the skip there.
   - A companion test asserts the production default gives `UNVERIFIED/TOOLCHAIN_IDENTITY_UNAVAILABLE`.
3. **The generated-output exclusion reuses the analyzer's directory set from `is_ignored` (now `GENERATED_OUTPUT_DIRS`).**
   - That set includes `bin` and `obj`, so an untracked, non-committed script under `bin/` is outside the no-change evidence. Tracked and milestone-committed files there are always checked.
   - That set omits `.pytest_cache` and `.egg-info`, which `analyze()`'s separate `ignore_dirs` in the same file does exclude. An untracked `.pytest_cache` in a repo without `.gitignore` therefore still invalidates the proof. That is conservative: it only causes a rerun.
   - Unifying the analyzer's two sets is a separate change. It does not block anything, since the no-change path is unreachable in production today.
4. **A git workspace is still required;** otherwise the proof is not issued (`WORKSPACE_IDENTITY_UNAVAILABLE`).

## Unchanged by design: a stale milestone reruns on the current workspace
No bytes are restored before a rerun; the user's edits are the source of truth, and Kriya is not an implicit rollback system. This is documented in `docs/user_guide.md` §3.4.1, together with the reuse, reconstruction and `--resume` rules. Test K asserts that the user's bytes are still on disk when the rerun starts.

## Decision records
Everything is recorded in one place: `milestone_reuse` on the run result (success and `milestone_failed`), `RunRecord.milestone_reuse`, and the sidecar's `last_reuse_assessment`.
- **`decisions`:** a status and typed reasons per completed milestone, plus refused reconstructions.
- **`events`:** reconstructions, and checkpoint selections (`MILESTONE_CHECKPOINT_SELECTED`, `NO_COMPATIBLE_MILESTONE_CHECKPOINT`, `CHECKPOINT_IDENTITY_MISMATCH`).
- **Reason codes added:**
  - `VERIFIED_NO_CHANGE_INVALIDATED`
  - `COMPLETION_RECONSTRUCTED`
  - `COMPLETION_RECONSTRUCTION_UNVERIFIED`
  - `RECOVERY_PROVENANCE_UNVERIFIED` (replaces `COMMIT_SETTLED_BY_RECOVERY`, removed)
  - `CURRENT_BYTES_DIVERGE_FROM_COMMITTED_LINEAGE`
  - `TOOLCHAIN_IDENTITY_UNAVAILABLE`
  - issuance refusals: `QUALITY_GATES_NOT_PASSED`, `ACCEPTANCE_COVERAGE_UNAVAILABLE`, `ACCEPTANCE_COVERAGE_INCOMPLETE`, `VERIFICATION_POLICY_UNAVAILABLE`, `WORKSPACE_IDENTITY_UNAVAILABLE`
  - `CHECKPOINT_IDENTITY_MISMATCH`
  - `NO_COMPATIBLE_MILESTONE_CHECKPOINT`
  - `MILESTONE_CHECKPOINT_SELECTED`
  - `COMMIT_LINEAGE_UNVERIFIED`
- The spec's `SHARED_WRITE_INVALIDATION` is S4b's existing `SHARED_PATH_WITH_RERUN`, kept under that name.

## Files
- **`kriya/control/run_record.py`:** `active_work_unit` field (annotatable); `begin_commit` copies it into the cycle.
- **`kriya/control/run_coordinator.py`:** `owning_run_work_unit()`.
- **`kriya/workflow/workflow.py`:** `_gate_outcome_proven` (extracted, same rule) and `deterministic_gate_evidence()`; result key `deterministic_gate_evidence`; checkpoint `work_unit`.
- **`kriya/workflow/milestone_completion.py`:**
  - work units;
  - entry fields `workspace_content_hash_after` and `loop_outcome`;
  - proof fields `kind`, `verification` and `reconstructed_from`;
  - `no_change_verification`;
  - lineage ordering and labels;
  - `_assess_no_change`;
  - `find_completion_to_reconstruct`;
  - `select_unit_checkpoint`;
  - `events`.
- **`kriya/workflow/milestones.py`:**
  - `_complete_milestone` helper;
  - `_reconstruct_completions`;
  - `_publish_reuse_assessment`;
  - `_set_work_unit`;
  - per-call checkpoint selection;
  - ledger `loop_outcome`;
  - capability marking for reconstructed milestones;
  - identity-based checkpoint invalidation.
- **`kriya/workflow/workflow_controller.py`:** passes config to revalidation.
- **Final review closure:**
  - `kriya/workflow/milestone_completion.py`: `acceptance_coverage`, `workspace_evidence_hash`, `current_toolchain_identity`, `_recovery_provenance_problem`, proof fields `completion_origin` and `no_change_refusal`, and the new reason codes;
  - `kriya/workflow/milestones.py`: origin and refusal wiring;
  - `kriya/workflow/resume_fingerprints.py`: `toolchain_fingerprint()` extracted;
  - `kriya/workflow/workflow.py`: gate evidence `status`;
  - `kriya/analyzer/analyzer.py`: the `GENERATED_OUTPUT_DIRS` constant, behaviour unchanged;
  - `docs/user_guide.md` §3.4.1.
- **`docs/user_guide.md`:** §3.4.1 rules.

## Tests
**New file:** `tests/test_prd008_s4c_milestone_resume.py`, 45 cases after the final review (26 before). The shared harness moved to `tests/_milestone_proof_harness.py`, imported by bare name like `_plugin_test_support`. `tests/` has no `__init__.py`, so a `from tests....` import would fail collection under pytest.

| Spec test | What it covers |
|---|---|
| A | A stable no-op milestone becomes VERIFIED_NO_CHANGE and is skipped (fake engine with gate evidence). Also a real-engine identical-bytes no-op that converges. |
| B | Verification-policy change, an unrelated workspace edit, and an upstream completion change each invalidate the no-change proof and cascade downstream. |
| C | Model-only "no change" (no gates, or compile only) never becomes reusable. |
| D | A real subprocess `os._exit` after M2's commit (both windows); M2 is reconstructed and not rerun, and the rebuilt proof persists. |
| E | Deleted evidence or edited output: reconstruction is refused with a typed cause and M2 reruns. Corrupt evidence is refused by the gate first. |
| F | `--resume` selects each milestone's own newest checkpoint and never another milestone's. |
| G | Real engine: the checkpoint is selected by identity, then the PRD-008 validator reuses it when fingerprints match and invalidates it when the workspace changed. A newer decoy checkpoint of another unit is ignored. |
| H | Only other units' or legacy checkpoints exist: fresh start with `NO_COMPATIBLE_MILESTONE_CHECKPOINT`. An explicit `--resume-id` is offered only to its own unit. |
| I | A fully proven overwrite leaves both milestones valid. An edit to a path last written by the integration pass or by a failed milestone is charged to the completed milestone that wrote it. |
| J | An unproven overwrite gives `COMMIT_LINEAGE_UNVERIFIED` naming the transaction; corrupt evidence is refused by the gate; a reordered ledger cannot rewrite who wrote last. |
| K | A rerun sees the user's bytes; they are not restored. |

**Final-review tests (the review's A-G):**
| Review test | Test function |
|---|---|
| A | `test_a_an_unrelated_or_unproven_passing_test_never_issues_verified_no_change`: an unrelated criterion's passing test, no coverage map, coverage from a gate that never ran, coverage from an earlier attempt, a failed covering test → not issued, M1 reruns with the typed cause. |
| B | `test_b_zero_executed_tests_are_never_positive_evidence`: real `deterministic_gate_evidence` on "collected 0 items" gives `NO_TESTS_EXECUTED`; coverage on it, even one claiming PASS_WITH_TESTS, → not issued. |
| C | `test_c_a_covered_no_op_milestone_is_verified_no_change_and_skipped`, with the toolchain seam injected; plus `test_no_change_reuse_is_unverified_while_the_toolchain_identity_is_unavailable` for the production default. Test B gains a `toolchain` change case. |
| D | `test_d_generated_output_never_invalidates_verified_no_change`. |
| E | `test_e_relevant_untracked_or_tracked_changes_invalidate_verified_no_change`: new source, new config, a tracked file under `build/`, a committed file under `bin/`. |
| F | `test_f_a_recovery_completed_milestone_is_reusable_and_its_run_stays_recovered`: a real subprocess crash mid-commit, then `recover_workspace(complete_partial=True)` → M2 is `MATCH/COMPLETION_RECONSTRUCTED` with origin RECOVERY and is not regenerated. The recovered RunRecord's lifecycle, terminal status, commit result and revision are unchanged. |
| G | `test_g_recovery_completion_with_unproven_evidence_is_refused_and_reruns`: deleted evidence (`COMMIT_EVIDENCE_MISSING`); a FOREIGN operation, a non-interrupted prior state, or a non-Kriya tool in the recovery record (`RECOVERY_PROVENANCE_UNVERIFIED`) → refused, M2 reruns. |

**Updated:** `tests/test_prd008_s4b_milestone_completion.py`:
- Test C's rolled-forward case now expects M2 reconstructed with origin RECOVERY (final review; see above). At 4ad70f2 it asserted the refusal.
- The shared-file test is now stricter (see S4c-4).

Two unit tests also cover `deterministic_gate_evidence` itself: only the final attempt counts, and a zero-test run is `NO_TESTS_EXECUTED` with `passed: None`.

### Coding-agent checks after the final review (plain-function runner, not pytest)
- **S4c:** 45/45 pass; **S4b:** 28/28.
- **Regression modules, all 0 failed:** milestones 68, workflow_controller 32, control_contracts 40, prd007 28, run_record 9, prd008_recovery 23, commit_state_gate 26, resume_fingerprints 48, resume_integrity 18, state001 12, bootstrap_contract 18, cli_smoke 4, subtask_checkpoint 8.
- **Mutation checks,** each one reverted afterwards. Every mutation is caught by its intended test:
  - scoped hash → plain hash: Test D fails;
  - `git add -u` step removed: Test E tracked-under-`build/` fails;
  - committed-path byte check off: Test E `bin/` fails;
  - recovery provenance check off: all three Test G provenance cases fail;
  - coverage ignoring criterion ids: Test A unrelated fails;
  - zero-test rule relaxed: Test B fails;
  - toolchain check off: the toolchain-unavailable test fails;
  - lineage reason code reverted: both Test I lineage cases fail.
- **ruff:** no new findings.

### Coding-agent checks at 4ad70f2 (plain-function runner, not pytest)
- **S4c tests:** 26 of 26 pass. The S4b and S4c files also pass when run from `/tmp` with only `tests/` on `sys.path`, which is how pytest imports them.
- **Mutation checks:** each one disables a feature and confirms its test then fails. All six fail as they should:
  - lineage ordering off → the reorder test fails;
  - selection off → Tests F and H fail;
  - reconstruction off → Test D fails;
  - lineage label off → Test J fails;
  - model-trusting no-change → Test C fails;
  - the S4c-4 last-writer comparison reverted → both new Test I cases and the S4b shared-file test fail.
- **Regression modules, all 0 failed:** s4b 28, milestones 68, workflow_controller 31, control_contracts 40, prd007 28, run_record 9, prd008_recovery 20, commit_state_gate 26, resume_fingerprints 47, resume_integrity 18, state001 12, bootstrap_contract 18, cli_smoke 4, subtask_checkpoint 8, checkpoint_control_plane_hashes 3, dispatch_generation 1.
- **Modules that need pytest-only fixtures:**
  - `test_workflow_controller_enforce`: the same 52 functions fail before and after (autouse fixtures).
  - `test_workflow`: one test, `test_resolve_jdk_home_..._on_linux`, fails in isolation both before and after S4c. It's a runner artifact (tmp-path resolution), not a regression.
- **ruff:** no new findings.

### Verification commands (user runs)
```bash
.venv/bin/pytest tests/test_prd008_s4c_milestone_resume.py tests/test_prd008_s4b_milestone_completion.py \
  tests/test_milestones.py tests/test_workflow_controller.py tests/test_workflow_controller_enforce.py \
  tests/test_control_contracts.py tests/test_dispatch_generation.py tests/test_subtask_checkpoint.py \
  tests/test_checkpoint_control_plane_hashes.py tests/test_control_plane_end_to_end.py tests/test_run_ownership.py \
  tests/test_run_record.py tests/test_prd007_run_lifecycle.py tests/test_prd008_recovery.py \
  tests/test_prd008_commit_state_gate.py tests/test_prd008_resume_fingerprints.py tests/test_resume_integrity.py \
  tests/test_state001_checkpoint_workspace_identity.py tests/test_prd005_commit_transactions.py \
  tests/test_bootstrap_contract.py tests/test_cli_smoke.py tests/test_workflow.py tests/test_analyzer.py -ra
.venv/bin/pytest   # full non-live suite
```

## Residual risks
- **VERIFIED_NO_CHANGE is unreachable in production today.** It needs a deterministic acceptance-coverage producer, a toolchain identity (PRD-011) and git. Anything else reruns, which is safe.
- **A reconstructed milestone has no `verification_commands`,** so the integration replay does not re-run its verification.
- **Recovery-completed commits are reconstructed only on proven provenance.** The model judge is skipped, as for every reconstruction; the candidate had already passed its gates before its commit started.
- **Cross-run lineage ordering relies on `RunRecord.created_at`,** under the serializing workspace lock.
- **Checkpoints saved before S4c** are never offered to a milestone.
- **Separate defect, not fixed here:** a Developer answer of `[]` in REPAIR_WITH_FULL_FILE mode is written verbatim as the file's content and committed as SUCCESS. See `handover/DEFECT_DEVELOPER_EMPTY_ARRAY_WRITTEN_AS_FILE.md`.
- **PRD-008A should replace** the milestone-specific reuse and selection code with one shared WorkUnit invariant.
