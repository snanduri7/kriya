# S18d P2b decision-to-killer table (fix-forward of pilot packet P2 / S18d; TYPE C)

TYPE C: a local code defect in the owner of the per-unit written-files record (the enforce loop's writers of
ControlState.subtask_written_files); secondary: product behaviour of the OI-150 (a) planned-read view. Found by the
blind TEST-B agent at 135acfe (F1), re-derived here on the unfixed bytes. Base 8b99591 (+ ledger 2560e1f, queued patch
002). Owner refinements R1-R10 bind.

## The defect (MEASURED on the unfixed bytes, re-derived)

TEST-B's reproducer (real enforce run, real candidate worktree, production recovery-plan builder; scripted
run_generation_workflow): s0 creates dep0.py and dep1.py; s1 fails with an advisory (attribution_rejected) scope
conflict; the production builder gives the generation-0 participants VERIFY; the enforce loop carries the group forward
without calling the Developer (files []); finalization REPLACED s0's entry with []; t2 (planned filesystem read of
dep0.py, depends_on [s0]) received candidate_writes {s0: [], s1: [a.py]}, read the OWNER baseline, failed 'dep0.py does
not exist', the run stopped failed (MEASURED at 2560e1f, the code of 8b99591). The grounded variant passed. NEW in P2b
(MEASURED): the same erasure happens with GROUNDED evidence - a MUST_CHANGE recovery of a subset (the conflict names
dep1.py only, the owner rewrites dep1.py only) left {s0: [dep1.py]} and t2 again read the baseline and failed. So the
trigger does not need the advisory path.

Root cause (CONFIRMED by the discriminating pins below, which fail on the unfixed bytes and pass after): the producer
REPLACED a unit's record with the latest invocation's REPORTED files. Consumer (reads_candidate_view) unchanged (R1).

## Invariant (one sentence)

A unit's entry in ControlState.subtask_written_files is the union of the files that every invocation of that unit in
this run COMMITTED to the candidate (first run, owner recovery, verification reopen, consumer retry, scope recovery) -
never replaced by a later invocation, never grown by an uncommitted one, present (possibly empty) once the unit ran -
and only a candidate boundary (a new run's ControlState, the discard reset) starts it over.

"Committed" is the candidate transaction's own result authority (no second detector, R2):
`quality_gates_passed is True` (set only after the sub-run's terminal commit committed; any commit that did not commit
stops the sub-run) or `final_review_refusal.candidate_applied is True` (applied, then its final review was refused;
TRACED: outcome.final_review_refusal_payload, stage_apply). One canonical operation:
`kriya/workflow/enforce_loop.py::record_committed_effects` (+ `committed_files`), next to every writer (all writers live
in the one enforce function; no new import edge, no ControlState surface change).

## Writer audit (R3) - every writer of ControlState.subtask_written_files

| file:line (base 8b99591) | what | before | class | decision |
|---|---|---|---|---|
| kriya/control/state.py:104 | field default (ControlState.new per run, workflow_controller.py:1243) | - | INITIALIZE | unchanged |
| kriya/control/state.py:218 | from_dict (deserialization of a persisted state) | copy | INITIALIZE (restore) | unchanged |
| kriya/workflow/enforce_loop.py:2841-2844 | owner-recovery finalization (VERIFY and MUST_CHANGE groups) | REPLACE with the recovery's reported files | WRONG REPLACEMENT | -> record_committed_effects (union, committed only) |
| kriya/workflow/enforce_loop.py:3002-3005 | verification-reopen fold (owner_accepted only) | UNION | ADD COMMITTED EFFECTS | -> record_committed_effects (same result: owner_accepted implies gates passed) |
| kriya/workflow/enforce_loop.py:3097-3100 | per-unit result (first run, or the last of consumer_retry / plan_scope_recovery / verification_rerun) | REPLACE with the reported files (failed units too) | WRONG REPLACEMENT (unobservable replace, observable uncommitted files) | -> record_committed_effects |
| kriya/workflow/enforce_loop.py:3317 | candidate discarded (not all completed, sandboxed) | {} | RESET AT AUTHORITATIVE BOUNDARY | unchanged |

consumer_retry sibling (R5), deterministic argument instead of a reproduction: the 3097 writer runs once per unit id
per run (the loop is `for position, subtask_id in enumerate(order)` over the plan's unique ids; a later
`order = ...` rebinding does not restart the iterator) and it is the FIRST writer of that id: the owner-recovery writer
writes only owners, and an owner is never the failing consumer (owner_recovery.py: resolve_effective_artifact_owner
excludes failed_subtask, resolve_scope_conflict_owners line 96 too); the verification-reopen writer writes only a
COMPLETED upstream owner (the consumer is in progress). So replace-vs-union at 3097 has no runtime witness, and the
replacement TEST-B inferred there cannot lose a recorded effect. The same root correction still applies there because
3097 also recorded UNCOMMITTED files of a failed unit (MEASURED: {s1: [a.py]} for a unit whose gates failed and that
committed nothing). Pins: test_writer_unit_result_records_what_the_consumer_retry_committed (behaviour, before and
after), the structural pin (replacement), test_writer_unit_result_of_a_failed_unit_records_no_uncommitted_file.

Declared consequence (not byte-identical, R2 supersedes): the record of a unit whose invocation committed nothing is
now [] instead of its reported files. Consumers: the read view (a failed unit stops the run, so no later read),
owner resolution and the abandoned-plan quarantine (both read only COMPLETED units). Owner resolution additionally now
still sees an owner whose recovery was VERIFY-only or a subset (before, its first-run files dropped out of
_successful_modifiers_in_execution_order).

## Controls on the unfixed bytes (MEASURED)

Characterization commit e8b7379 (17 strict-xfail after-pins): with `--runxfail` on the unfixed enforce_loop all 17
failed (TEST-B e2e: candidate_writes {s0: [], s1: [a.py]}, t2 'dep0.py does not exist'; subset e2e: s0 [dep1.py];
VERIFY writer s0 []; MUST_CHANGE writer [dep1.py]; failed recovery [dep1.py, dep2.py]; re-invocation [dep2.py];
failed unit s1 [a.py]; the canonical-op tests (absent); the structural pin: enforce_loop 2841, 3002, 3097). The same
17 fail again with the final pins (T6 harness, production recovery builder) on the unfixed enforce_loop. The grounded
all-files e2e, the verification-reopen union, the consumer-retry record, case D, case F, B21/B26/B30 and the write-side
hard-link pin pass before and after (production already correct there).

## Decisions and killers (campaign 20261020_195337_1dce45c9, mutrun v2, rev e2bbe8a, --workers 1, hermetic HOME, VALID, 364 s, tree_diff [], no stragglers: 18/18 KILLED at L1)

| decision | symbol | mutant | killer |
|---|---|---|---|
| owner-recovery writer unions | enforce_loop finalization | R01 replaces | test_a_verify_only_recovery_keeps_the_owners_change_and_the_read_sees_the_candidate |
| owner-recovery writer: committed only | same | R02 counts uncommitted | test_writer_owner_recovery_that_failed_adds_nothing_it_did_not_commit |
| verification-reopen writer unions | enforce_loop reopen fold | R03 replaces | test_writer_verification_reopen_adds_its_files_to_the_first_run_files |
| unit writer through the one op | enforce_loop per-unit result | R04 replaces (with committed files) | test_every_writer_of_the_record_goes_through_the_one_cumulative_update (structural; no runtime witness exists - see the consumer_retry argument) |
| unit writer: committed only | same | R05 counts uncommitted | test_writer_unit_result_of_a_failed_unit_records_no_uncommitted_file |
| the op unions | record_committed_effects | R06 replaces | TEST-B e2e |
| the entry exists when empty | same | R07 omits an empty entry | test_writer_verification_reopen... (sv: []) / test_a_unit_that_committed_nothing_gets_an_empty_entry |
| VERIFY path keeps the record | VERIFY carry-forward | R08 clears the owner record | TEST-B e2e |
| committed = the commit verdict | committed_files | R09 ignores it | failed-recovery writer test |
| applied-then-refused review counts | same | R10 ignores candidate_applied | test_only_committed_files_count[review-refused-applied] |
| verdict is the bool True | same | R11 truthiness | test_only_committed_files_count[not-a-bool] |
| consumer_retry result recorded | consumer_retry path | R12 retry loses its files | TEST-B e2e (s1 [a.py]) |
| discard boundary resets | enforce_loop reset | R13 reset removed | e12 test_a_failed_workspace_commit_fails_the_run_and_keeps_the_candidate_only_when_uncertain |
| empty entry = no change | subtask_executor._changed_the_candidate | R14 empty read as change | S18d read-view table |
| the op never mutates in place | record_committed_effects | R15 in-place write | test_the_record_is_the_union_and_never_loses_or_invents_a_file |
| B21 identity alias of the sentinel | is_trusted_control_path | B21 (TEST-B text) | test_a_hard_link_to_the_sentinel_is_protected_like_the_sentinel |
| B26 / C8 stat failure fails closed | _is_control_path fallback | B26 (TEST-B text) | test_a_path_whose_stat_fails_is_judged_protected_and_left_out |
| B30 subfolder symlink into .git | FilesystemTool listing | B30 (TEST-B text) | test_a_subfolder_symlink_into_git_is_protected[snapshot] |

C8 (R7): OLD CLAIM 'a stat failure cannot be deterministically produced' / NEW EVIDENCE 'an inaccessible (mode 000)
directory outside the roots deterministically reaches the path' (TEST-B; re-measured here: path_relation UNKNOWN for a
symlink into it, the entry hidden, a readable outside symlink listed) / CORRECTION: deterministic killer added, C8
KILLED. Pilot evidence: DEV equivalent-mutant judgement errors = 1.

## R8 characterization (MEASURED before any pin)

- A (hard link): to the sentinel - refused/hidden/not searchable/writer-refused (identity rule): correct, pinned. To a
  file BENEATH .git or .kriya (.git/config, .kriya/control/state.json): READABLE and a search hit in the owner view -
  NOT fixed: OWNER QUESTION Q1 (strict-xfail pin). Reachability: git checkouts and the scoped-snapshot copy never create
  hard links (TRACED: worktree.py uses shutil.copy2/copytree), planned writes are refused and Developer writes are
  atomic replaces, so only an operator can create one. Write side MEASURED safe: the writer admits the ordinary name but
  the atomic write (temporary file + os.replace) replaces the entry and leaves the control file's bytes unchanged
  (pinned). A generic fix needs an identity index of the control trees for st_nlink > 1 files (cost/design decision;
  refusing every st_nlink > 1 file would refuse pnpm-style hard-linked trees).
- B (stat failure): fail closed already, outside and inside the roots; pinned (C8 killer).
- C (subfolder symlink into .git): protected in the snapshot, owner and worktree views (relative and absolute targets);
  pinned.

## R9 (OI-149 residual wording)

kriya/workflow/planner_repair.py always-present line: "real, currently registered Kriya tool" -> "plannable Kriya
tool" (-17 chars). Before/after (MEASURED): fitted reference counts [1, 3] at 12288 and [0, 0] at 8192 both;
repair prompts 3058/3085/3517 -> 3041/3068/3500 chars, twice identical. Golden walk: digests of all 445 files under
tests/golden and tests/fixtures before; after the edit the golden set (S18d's list + prompt-fit + repair suites, 1525
tests) failed exactly one: kernel legacy_planner_schema_invalid (two planner repair prompts 5382 -> 5365 chars + their
digests). Kernel goldens regenerated twice: identical, 41 files, only that one differs; restoring only the sentence (2
occurrences) in the captured prompts reproduces the committed lines exactly. The historical fixture
plan_global_invariant_projection_001.json (a recorded live prompt) is evidence, not regenerated. Changed assertion: the
extraction marker _TARGETED_CORRECTION_END_MARKER in tests/test_workflow_controller_enforce.py (a verbatim copy of the
line).
