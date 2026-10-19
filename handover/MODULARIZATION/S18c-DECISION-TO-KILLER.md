# S18c decision-to-killer table (unit S18c, TYPE C: planned read-root identity consistency; OI-148 group A, OI-147 group B)

Owner decisions 2026-10-20 (two commit groups; one shared three-state fixture; A: the explicit root is authoritative,
typed refusal TOOL_CANDIDATE_VIEW_UNAVAILABLE, no pathspec confinement, monorepo limitation documented, no-roots pin;
B: measure first, candidate for current-candidate reasoning, baseline kept where explicit, no plan-schema flag, no prompt
change). Base eb58aa6.

Commits: 3efdbce group A characterization (21 strict-xfail after-pins, MEASURED failing on eb58aa6); 3de789e group A
correction; 3549439 group B characterization (15 strict-xfail after-pins, MEASURED failing on 3de789e); 725fcf3 group B
correction; docs/notes/this table after.

L1 = tests/test_s18c_read_root_identity.py + tests/test_s18b_planned_tool_contracts.py + tests/test_s1_toolbox_authority.py.
Campaign 20261020_003517_7941dda9 (mutrun v2, rev 725fcf3, VALID, 307 s, tree_diff [], no stragglers): 21/22 KILLED at
L1, 1 EQUIVALENT (S18cA10).

## Shared fixture (tests/test_s18c_read_root_identity.py::build_three_state)

enclosing repository (branch enclosing-branch, uncommitted outside.txt edit) > projects/owner (its OWN repository,
branch owner-branch, HEAD moved after the candidate, ignored/untracked/symlinked files) + its real candidate
(create_git_worktree, changed like a MODEL step: app.py rewritten, a file created, a file deleted) > projects/plain (a plain
folder, NOT its own repository) + its real scoped snapshot (changed) > projects/fresh (own repository, no commit) + a
symlink alias of the owner. Guards: test_the_three_states_really_differ, test_the_read_states_really_differ.

## Group B read table (tool/operation | root before | consumer | intended identity | evidence | decision)

Consumers of a planned read's SubtaskResult (TRACED): run result subtask_results[].tool_output (WorkflowResult -> CLI/JSON),
attempt evidence tool.execution (GUI "Tool execution"), telemetry presence count; status COMPLETED/FAILED drives the enforce
loop's stop/continue. No later step, Reviewer, oracle or recovery reads the output (resume_support explicitly does not).

| tool/operation | root before S18c | consumer | intended identity | evidence | decision |
|---|---|---|---|---|---|
| filesystem read/list, search, ast; step depends (transitively) on a MODEL step | owner (roots[0]) | human reader of the run step; status stops/continues the run | the candidate as the MODEL step left it | MEASURED: owner content after m1; candidate_only.py FAILED 'does not exist'; real enforce run: s3 (depends_on s1) showed the uncached service while the run committed the cached one | candidate (read_root) - corrected 725fcf3 |
| same; no MODEL step among the dependencies (incl. TOOL-only ancestry) | owner | same | the owner baseline (pre-run, immutable) | MEASURED: the example-B shape runs AFTER the MODEL steps (order m1, m2, t9; real run s4 last) - position cannot be the signal; owner says example B is a baseline read | owner - INTENTIONAL_CONTRACT, pinned |
| any read, one root / in place | owner | same | the one folder the run changes | no second root exists | owner - pinned |
| git status/diff | candidate (S18b) | same | candidate (S18b, accepted) | S18b | unchanged, re-pinned with and without a MODEL dependency |
| git log/branch/blame | owner (S18b) | same | owner (S18b, accepted) | S18b; blame's file resolved in the owner whatever the dependencies | unchanged, pinned |
| filesystem write | refused (owner-resolved) | - | - | DENY_ALL in every case | unchanged |

## Snapshot differences (characterized and pinned, candidate view)

| difference | worktree candidate | scoped snapshot | pin |
|---|---|---|---|
| git-ignored files | absent (ignored.env -> FAILED does not exist) | files the ENCLOSING repo ignores are COPIED (x.log) | test_files_absent_..., test_files_present_in_both_views_read_the_same |
| untracked before candidate creation | synced (present) | copied | test_files_present_in_both_views_read_the_same |
| untracked created after candidate creation | absent | n/a | test_files_absent_...[owner_untracked_after.py] |
| file the run deleted | absent | - | test_files_absent_...[doomed.py] |
| relative in-root symlink | resolves inside the candidate (candidate bytes) | - | test_a_relative_symlink_in_the_candidate_resolves_inside_the_candidate |
| absolute in-root symlink | git stores the target text: points at the OWNER bytes (what the gates see too) | same (points at the plain folder) | test_an_absolute_in_root_symlink_resolves_to_the_owner_bytes_in_both_views |
| symlink out of the roots | PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS in both views | - | test_authorization_holds_on_whichever_root_is_read |
| credential-shaped file | SENSITIVE_PATH_DENIED in both views | - | same |
| layout | .git is a gitfile; no .kriya | Kriya-made .git dir and .kriya-scoped-snapshot listed (owner has neither) | test_the_scoped_snapshot_is_read_after_a_model_step_with_its_kriya_markers |

## Decisions and killers

| decision | symbol | risk | killer | mutant |
|---|---|---|---|---|
| A: every rooted read is identity-checked | authorize_model_invocation check | only a separate candidate checked (S18b) | test_a_nested_plain_folder_never_answers_for_the_enclosing_repository | S18cA01 KILLED |
| A: base revision only for a separate candidate | base_revision=separate | commit-less own repo refused | test_a_repository_without_a_commit_still_answers_status_with_one_root | S18cA02 KILLED |
| A: separate candidate needs a base revision | same | snapshot status/diff accepted | test_a_candidate_with_no_base_revision_refuses_status_and_diff_typed (S18b) | S18cA03 KILLED |
| A: realpath identity | toplevel compare | a symlinked root refused (macOS /var) | test_a_root_spelled_through_a_symlink_is_the_same_repository | S18cA04 KILLED |
| A: blame checked too | blame branch | blame answers for the enclosing repository | ...never_answers...[blame-*] | S18cA05 KILLED |
| A: no roots = process folder (S18b o12) | _planned_read_root | another folder read | test_a_planned_git_read_without_roots_runs_in_the_process_folder | S18cA06 KILLED |
| A: no roots also checked (fail-closed choice) | check unconditional | nested process folder answers for the enclosing repo | test_without_roots_a_nested_process_folder_is_refused | S18cA07 KILLED |
| A: one cause, one code | refusal code | old TOOL_WORKSPACE_ROOT_UNAVAILABLE emitted | S18b tests (changed assertions) + _refused_as_not_its_own_repository | S18cA08 KILLED |
| A: failed probe refuses | returncode | failed probe accepted | test_a_candidate_with_no_base_revision... | S18cA09 KILLED |
| A: line count | len(lines) | - | - | S18cA10 EQUIVALENT: with returncode 0, `git rev-parse --show-toplevel [HEAD^{commit}]` prints exactly one line per argument, so the count is implied by the return-code check (same as S18b o6); kept as defence in depth |
| A: code in the closed table | MODEL_TOOL_REFUSAL_REASON_CODES | refusal construction fails (untyped) | S18b/S18c refusal pins + the tripwire | S18cA11 KILLED |
| B: executor supplies the flag | follows_model_step(...) | always owner | test_a_read_after_a_model_step_reads_the_candidate | S18cB01 KILLED |
| B: baseline reads stay baseline | same | always candidate | test_a_read_with_no_model_step_among_its_dependencies_reads_the_owner_baseline | S18cB02 KILLED |
| B: transitive | pending.extend | direct only | test_a_read_after_a_model_step_through_another_step_reads_the_candidate | S18cB03 KILLED |
| B: only MODEL ancestry | execution_method == MODEL | TOOL ancestry counted | test_a_read_after_only_tool_steps_reads_the_owner_baseline | S18cB04 KILLED |
| B: read_root honours the flag | read_root | flag ignored | test_a_read_after_a_model_step_reads_the_candidate | S18cB05 KILLED |
| B: one root = that root | len(roots) > 1 | IndexError | test_with_one_root_a_read_after_a_model_step_reads_that_root | S18cB06 KILLED |
| B: blame file in the owner | in_owner=True | blame of a candidate path in the owner repo | test_git_keeps_its_per_subject_roots_whatever_the_dependencies[blame] | S18cB07 KILLED |
| B: search uses read_root | SearchTool authorize | owner searched | ...reads_the_candidate[search] | S18cB08 KILLED |
| B: ast uses read_root | ASTTool authorize | owner parsed | ...reads_the_candidate[ast] | S18cB09 KILLED |
| B: filesystem uses read_root | FilesystemTool authorize | owner read/listed | ...reads_the_candidate[read/list] | S18cB10 KILLED |
| B: resolve honours in_owner | ModelToolInvocation.resolve | blame resolved in the candidate | ...per_subject_roots...[blame] | S18cB11 KILLED |

Negative controls on the unfixed bytes (MEASURED): group A 21 after-pins failed on eb58aa6 (COMPLETED with the enclosing
repository's answer, blame FAILED untyped "no such path 'projects/plain/app.py' in HEAD", or the S18b no-root code); group B
15 after-pins failed on 3de789e (owner content; candidate_only.py FAILED; real enforce run s3 = uncached service).
Original reproducer re-measured after 725fcf3 (scratchpad measure.py, before/after diff): only the predicted rows changed.
