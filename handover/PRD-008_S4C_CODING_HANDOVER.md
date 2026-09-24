# PRD-008 S4c - Milestone Resume Closure - Coding Handover

## Status
READY_FOR_PYTEST_VERIFICATION

S4c is committed locally and not pushed. S5 must not start until S4c is verified by pytest. PRD-008A has not been started.

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
  - A mismatch there is charged to the latest completed milestone that wrote the path (`CHANGED/OUTPUT_*`), with `superseded_by` naming the later commit.
  - Before this fix, a user could delete a milestone's contribution from an integration-rewritten `pom.xml` and every milestone was still skipped. A clean rerun still converges.
  - Tests: `test_i_an_edit_to_a_path_the_integration_pass_owns_...` and `..._a_failed_milestone_wrote_last_...`.
- **Result:** every superseding transaction used to explain an earlier milestone must be present, readable, COMMITTED, bound to its RunRecord, and correctly ordered. The path's current bytes must also equal that last writer's post-state. Otherwise the result is UNVERIFIED/CHANGED, never MATCH.
- **S4b assertion changed (stricter, stated explicitly).** The second half of S4b's `test_milestones_modifying_each_others_files_stay_valid_when_nothing_changed` edited an integration-owned `pom.xml` and asserted both milestones MATCH; that was exactly this gap. It now asserts `M1 MATCH, M2 CHANGED/OUTPUT_CHANGED`.

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
5. **Refuse commits settled by `kriya runs recover`** (`COMMIT_SETTLED_BY_RECOVERY`). The process never saw such a commit finish; see the decision below.
6. **Compare the workspace byte-exactly** with the final committed state. A user edit after the crash means refusal.
7. **Re-run the deterministic post-commit steps.** A dependency regression means refusal.
8. **On success,** write the completion proof with `reconstructed_from: {run_id, transaction_ids}`. The later assessment reports that milestone as `MATCH` with `COMPLETION_RECONSTRUCTED`.
9. **On refusal,** the milestone gets `UNVERIFIED/COMPLETION_RECONSTRUCTION_UNVERIFIED` with the `cause`, and it runs. Verified commits are still appended to the ledger as history, so lineage stays correct.

Both crash windows are closed and tested:
- the commit is durable but no ledger entry exists;
- the ledger entry was saved but no proof exists (the S4b ordering).

**Decision for you (conservative choice, easy to relax).** A commit completed by `kriya runs recover --complete-partial` is not reconstructed.
- **Why:** the workflow never returned from that commit, and it keeps S4b's Test C assertion unchanged ("M2 is not complete because part of its commit landed").
- **Test C** now also asserts the machine-readable refusal decision for M2, which strengthens it; no S4b assertion was weakened.

## S4c-1: VERIFIED_NO_CHANGE
**What I found first.** In the real engine, a "nothing to change" milestone rewrites its target file with identical bytes. That produces a COMMITTED cycle, so S4b already proves it byte-exactly and it converges. The test `test_a_real_engine_no_change_milestone_converges` covers this.
- The zero-cycle path only exists when the candidate is empty. My probes could not get a real engine run to succeed that way: a "No change needed" developer answer goes through repair retries and fails.
- VERIFIED_NO_CHANGE therefore covers the zero-cycle path, and is only reachable with deterministic evidence.

**Proof requirements** (`no_change_verification()`). All of these must hold, otherwise the completion stays `NO_COMMITTED_OUTPUT` and reruns:
- `quality_gates_passed`, with zero committed cycles for the milestone;
- at least one behavioural gate (`test`, `targeted_test`, `regression_test` or `run_verification`) in the workflow's new `deterministic_gate_evidence`. That field lists the latest outcome per gate type and excludes:
  - gates from earlier attempts, which ran against a different candidate; only the final attempt counts, and the attempt number is part of each evidence id;
  - skipped or unconfirmed gates, using the existing rule (e.g. an "unknown" stack's pass-through);
  - test runs that executed zero tests, using the existing `output_confirms_nonzero_test_execution`. The real engine reported `regression_test` as passed on a repository with no tests ("collected 0 items / no tests ran"), and that pass is now excluded.

  Compile alone never counts. An empty diff, "NO CHANGE", or a Planner, Developer or Reviewer verdict are never evidence.
- a current verification-policy fingerprint (PRD-008's `verification_policy` owner split, not a second freshness model);
- a git content hash for the workspace.

**What the proof is bound to:** gate evidence and evidence ids (`run:<id>:gate:<type>`), the verification-policy fingerprint, the upstream proof identity (the completions of its `depends_on`), `workspace_content_hash`, `ledger_position`, `zero_mutations`, and `obligations: NOT_APPLICABLE` (the milestone driver passes no obligation ledger).

**Reuse.** The following must all still hold, otherwise the result is `CHANGED/VERIFIED_NO_CHANGE_INVALIDATED` (or `COMMIT_LINEAGE_UNVERIFIED` when a later commit can't be verified):
- the definition digest;
- the verification policy;
- the upstream identity;
- the workspace content hash, which must equal the hash recorded after the last verified ledger entry since the proof (or the proof's own hash if nothing has been committed since). Every entry since then must verify.

It uses `compute_workspace_content_hash` (content only), not the `workspace` fingerprint, so a user committing Kriya's output to git does not invalidate the proof.

**Your sign-off is needed on four points:**
1. Green gates show the workspace builds and tests pass. They do not show the milestone's free-text acceptance criteria are met; `AcceptanceCriterion` has no deterministic check (see its docstring).
2. The toolchain and model_runtime fingerprints are excluded, because they are UNAVAILABLE until PRD-011/013. This matches S4b, where committed milestones are not re-verified under a new toolchain.
3. Any workspace change not explained by verified commits invalidates the proof and cascades downstream. This includes untracked, non-ignored build output such as `__pycache__` in a repo without a `.gitignore`.
4. It needs a git workspace; otherwise the proof is UNVERIFIED and the milestone reruns.

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
  - `COMMIT_SETTLED_BY_RECOVERY`
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
- **`docs/user_guide.md`:** §3.4.1 rules.

## Tests
**New file:** `tests/test_prd008_s4c_milestone_resume.py`, 26 cases. The shared harness moved to `tests/_milestone_proof_harness.py`, imported by bare name like `_plugin_test_support`. `tests/` has no `__init__.py`, so a `from tests....` import would fail collection under pytest.

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

**Updated:** `tests/test_prd008_s4b_milestone_completion.py`:
- Test C now also asserts M2's reconstruction-refusal decision in the rolled-forward case.
- The shared-file test is now stricter (see S4c-4).

Two unit tests also cover `deterministic_gate_evidence` itself: final attempt only, and zero-test runs excluded.

### Coding-agent checks (plain-function runner, not pytest)
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
  tests/test_bootstrap_contract.py tests/test_cli_smoke.py tests/test_workflow.py -ra
.venv/bin/pytest   # full non-live suite
```

## Residual risks
- **VERIFIED_NO_CHANGE is narrow by construction.** It needs git, a behavioural gate that really ran, and a workspace explained exactly by verified commits. Anything else reruns, which is safe.
- **A reconstructed milestone has no `verification_commands`,** so the integration replay does not re-run its verification.
- **Recovery-settled commits are never reconstructed;** that milestone reruns on top of its own full output.
- **Cross-run lineage ordering relies on `RunRecord.created_at`,** under the serializing workspace lock.
- **Checkpoints saved before S4c** are never offered to a milestone.
- **An observation outside S4c scope, not fixed:** in the real engine, a Developer answer of `[]` in REPAIR_WITH_FULL_FILE mode was written verbatim as the file's content (seen during the zero-write probe). This belongs in a separate finding.
- **PRD-008A should replace** the milestone-specific reuse and selection code with one shared WorkUnit invariant.
