# S19 decision-to-killer table (unit S19, TYPE C: gate-input integrity; OI-141 corrected, OI-142 held)

Base 267f7a4. Preflight b91cb39 (S19-PREFLIGHT.md). Characterization 4668ddb (28 strict-xfail after-pins, MEASURED
failing on b91cb39 under `--runxfail`, every failure on the marker / output assertion; 3 positive controls; 2 held Q1
pins). Correction: 3fee52b seam, then one commit per owner module (c8f499e REPOSITORY, a24233b KERNEL, 1529824
EXECUTION, 873d290 SKILLS, 5cb6e0a RETRIEVAL, c0b3800 STATE, a27a65e QUALIFICATION, b2579b7 ADAPTER-CLI, d3a66db
PLUGIN), each flipping exactly its owner's pins; 67c1f60 enforcement (AST rule + dynamic twin; 59 violations MEASURED on
4668ddb).

L1 = tests/test_s19_git_invocation.py + tests/architecture/test_git_invocation_seam.py; L2 = test_worktree_policy_audit,
test_worktree_hooks, test_kup_workspace. Campaign 20261020_072702_854aea8b (mutrun v2, rev 67c1f60, VALID, 209 s,
tree_diff [], no stragglers): 18/18 KILLED at L1 (pytest `-x`: the first failing test is the recorded killer).

## Decisions

| # | decision | owner (file) | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| D1 | every Kriya git spawn carries `-c core.fsmonitor=false` | kriya/platform/__init__.py `GIT_INVOCATION_OPTIONS` | `test_a_repository_fsmonitor_never_runs_through_kriya_git[17 sites]`, `..._included_...`, `..._include_if_...`, `..._worktree_config_...`, `..._submodule_...` | `test_kriya_git_answers_are_git_s_own_answers`, `test_the_hardened_options_change_no_git_answer` |
| D2 | ... and `-c core.hooksPath=/dev/null` | same | `test_a_repository_index_hook_never_runs[2]`, `test_the_operator_scan_base_worktree_runs_no_checkout_hook` | same |
| D3 | ... and `-c commit.gpgSign=false` (Q2, fail-closed) | same | `test_the_skill_commit_never_runs_a_repository_gpg_program`, `test_the_bootstrap_commit_never_runs_a_repository_gpg_program` | both still create their commit; `test_kriya_commits_still_commit` |
| D4 | options go after the executable, the argv0 must be git, a new list is returned (the audited argv is never mutated) | `git_argv` | `test_the_seam_refuses_anything_but_git[5]`, `test_the_seam_inserts_the_options_after_the_executable_and_returns_a_new_list` | tests/test_worktree_policy_audit.py (audited bootstrap argv unchanged) |
| D5 | applied at the spawn, after `_audit_git_write` / POL-001-P3 recognizers | every call site (56 + GitTool) | `test_every_kriya_git_spawn_goes_through_the_seam` (AST), `test_every_git_process_kriya_spawns_carries_the_options` (dynamic) | `test_the_rule_catches_every_unwrapped_shape` (13 planted controls), `test_the_seam_module_holds_no_git_literal_of_its_own` |
| D6 | GitTool diff `--no-ext-diff --no-textconv`, blame `--no-textconv` (Q3, fail-closed) | plugins/core_tools GitTool._run_in | `test_gittool_diff_runs_no_repository_diff_driver`, `test_gittool_blame_runs_no_repository_textconv` | `test_gittool_reads_are_git_s_own_output_without_drivers`; S18b/S18c output-equality pins |
| H1 | HELD (Q1, digest STOP): content-filter drivers are NOT neutralised | - | `test_held_q1_a_repository_content_filter_never_runs[2]` stays strict xfail | - |
| H1 (S19 Q1, 2026-10-20) | owner decision REFUSAL: the held pins now expect the typed refusal | kriya/repository/git.py | see S19Q1-DECISION-TO-KILLER.md | - |

## Mutants (all KILLED at L1)

| id | mutation | killer |
|---|---|---|
| S19M01 | drop `core.fsmonitor=false` | fsmonitor pin [snapshot.untracked_repository_paths] |
| S19M02 | drop `core.hooksPath=/dev/null` | index-hook pins |
| S19M03 | drop `commit.gpgSign=false` | skill gpg pin |
| S19M04 | accept any argv0 | seam refusal test |
| S19M05 | return the argv unchanged | fsmonitor pins |
| S19M06 | options appended after the subcommand | positive controls (git rejects / answers differ) |
| S19M07 | mutate the audited list in place | seam new-list test |
| S19M08 | worktree.git_status_entries unwrapped | AST rule |
| S19M09 | checkpoint `add -A` unwrapped | AST rule |
| S19M10 | operator_scan._git unwrapped | AST rule |
| S19M11 | skill commit unwrapped | fsmonitor pin [skill] + AST rule |
| S19M12 | bootstrap commit spawned raw | AST rule |
| S19M13 | GitTool diff flags removed | diff-driver pin |
| S19M14 | GitTool blame flag removed | textconv pin |
| S19M15 | GitTool run_async unwrapped | AST rule |
| S19M16 | git_metadata._probe unwrapped | dynamic twin + AST rule (behaviour-equivalent for the measured keys: the probe already disables fsmonitor and takes no optional locks) |
| S19M17 | release_identity `rev-parse` unwrapped | AST rule |
| S19M18 | milestone_completion `write-tree` unwrapped | fsmonitor pin [workspace_evidence_hash] + AST rule |

## Changed existing assertions (each encoded the unhardened argv, not behaviour)

- tests/test_worktree_canonical_root.py `test_a_legacy_worktree_that_cannot_be_removed_fails_closed`: `cmd[1:3] ==
  ["worktree", "remove"]` read the subcommand at a fixed argv position; it now skips `GIT_INVOCATION_OPTIONS` (the
  failing step is still the removal).
- tests/test_worktree_canonical_root.py `test_worktree_locations_are_decided_only_by_worktree_py`: the offender list pins
  LINE NUMBERS; the new import line moves production_doctor 699->700, git_metadata 74->75, operator_scan 70/77->71/78
  (same four offenders, same constants).
