# S19 Q1 decision-to-killer table (TYPE C: OI-141 remainder, repository content-filter drivers; owner decision 2026-10-20: REFUSAL, not blanking)

Base 7eddc5d (= refs/kriya/verified). Characterization f581d18: 44 strict-xfail after-pins + 7 positive controls,
MEASURED on the unfixed bytes (`--runxfail`, a `git archive` of 7eddc5d): 46 failed (the 44 + the 2 held S19 pins),
7 passed; every filter-site pin failed on the MARKER (the planted filter ran), `begin_mutating_run` on the missing
refusal, every dynamic pin on "never inspected". Owner 78485c2 (+ own-bug fix 9ff8638), then one commit per owner module:
02fc1be KERNEL, 4aacee4 STATE, 5f7ddea RETRIEVAL, d976d81 EXECUTION, 826de5a SKILLS, abae6d1 PLUGIN; dcbb761 kills Q1M30.

L1 = tests/test_s19q1_content_filter_refusal.py + tests/test_s19_git_invocation.py; L2 = test_best_of_n,
test_worktree_policy_audit, test_worktree_hooks, tests/architecture/test_git_invocation_seam.py. Campaigns (mutrun v2,
`--workers 1`): 20261020_142719_4800f039 (rev abae6d1, VALID, 397 s, tree_diff [], 33 mutants: 32 killed at L1, Q1M30
survived; Q1M32 was an invalid mutant - a removed line left an empty `try:` - and is re-run below) and
20261020_143519_6e69c80c (rev dcbb761, VALID, 37 s: Q1M08, Q1M30, Q1M32 (now `pass`), Q1M35 all killed at L1).
Total: 35 mutants, 35 killed, 0 equivalent.

## Decisions

| # | decision | owner (file) | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| D1 | refuse an executable `filter.<x>.clean/smudge/process` whose scope is not an operator scope (system/global) or command scope: local, worktree and everything they include | kriya/repository/git.py `_refuse` | `test_each_driver_kind_alone_is_refused[3]`, `test_an_included_or_worktree_scoped_filter_is_refused[5]`, `test_a_repository_override_of_a_trusted_global_filter_is_refused` | `test_a_global_only_filter_still_works_and_a_repository_may_reference_it`, `test_operator_scopes_outside_the_repository_are_trusted[3]` |
| D2 | an operator (global/system) file INSIDE the repository refuses (fail-closed choice, owner question) | same | `test_a_global_file_including_a_repository_file_is_refused` | D1's global-only control |
| D3 | not executable = not refused: empty command, metadata keys; whitespace and valueless refuse | same | `test_only_an_explicitly_empty_command_is_not_executable[whitespace, valueless]` | `test_benign_or_operator_filter_configuration_is_not_refused[4]`, `[empty]` |
| D4 | unknown scope/origin, unreadable or unparseable listing, timeout, failing spawn -> typed refusal | same | `test_an_unrecognised_scope_origin_or_listing_refuses[5]`, `test_an_unreadable_configuration_refuses`, `test_a_timeout_or_a_failing_spawn_refuses` | `test_no_git_or_no_folder_means_nothing_can_run` |
| D5 | submodules / embedded repositories / HEAD-only gitlinks inspected recursively (populated only, bounded nesting) | same `_gitlinks` | `test_a_filter_in_a_submodule_s_own_config_is_refused[3]` | `test_an_unpopulated_gitlink_is_not_inspected_and_nesting_is_bounded` |
| D6 | typed refusal, closed table, message = key/scope/origin through the one redactor, never the command | same `RepositoryContentFilterRefused` | `test_the_refusal_names_key_scope_and_origin_never_the_command`, `test_the_refusal_message_goes_through_the_one_redactor` | `test_the_refusal_table_is_closed` |
| D7 | the inspection runs nothing | same (git config only, through git_argv) | - | `test_the_inspection_itself_runs_no_filter`; S19 dynamic twin |
| D8 | every filter-capable Kriya entry inspects the folder it runs git in, BEFORE that git (15 entries) | the 7 owner modules | `test_a_repository_filter_is_refused_before_any_kriya_git_runs_it[16]`, `test_the_inspection_precedes_every_filter_capable_git_operation[16]`, `test_the_reused_worktree_s_own_config_worktree_is_refused_before_its_checkout`, the 2 S19 pins | `test_a_filter_free_repository_keeps_every_identity_digest` (golden predictor), `test_a_run_still_begins_in_a_folder_that_is_not_a_repository` |
| D9 | cleanup / best-effort sites report the typed refusal and skip their git (remove_git_worktree ERROR, skill commit WARNING) | worktree, skill | site matrix `[worktree.remove_git_worktree]`, `test_the_skill_commit_refuses_without_running_the_filter_or_committing` | - |
| D10 | the refusal reaches the caller as itself (sync, direct development loop, enforce plan worktree; never a fallback) | worktree, development_loop, enforce_loop, analyzer | `test_the_worktree_sync_lets_the_refusal_through`, `test_the_direct_development_loop_lets_the_sandbox_refusal_through`, `test_the_enforce_plan_worktree_lets_the_refusal_through`, the "indexing all files" assertion | - |

## Mutants (all KILLED at L1)

| id | mutation | killer |
|---|---|---|
| Q1M01 | only scope `local` refuses | `test_an_included_or_worktree_scoped_filter_is_refused[config.worktree]` |
| Q1M02 | every valued key skipped | held S19 pin [content hash] |
| Q1M03 | whitespace treated as empty | `test_only_an_explicitly_empty_command_is_not_executable[   -True]` |
| Q1M04 | origin-inside-repository check disabled | `test_a_global_file_including_a_repository_file_is_refused` |
| Q1M05 | command scope trusted for any origin | unrecognised `[command file:]` |
| Q1M06 | unrecognised origin trusted | unrecognised `[global standard input:]` |
| Q1M07 | no submodule recursion | submodule `[submodule]` |
| Q1M08 | the HEAD listing branch disabled (no HEAD ls-tree accepted) | `test_kriya_git_answers_are_git_s_own_answers` (every repository refuses) |
| Q1M09 | any non-zero config exit = "no filters" | `test_an_unreadable_configuration_refuses` |
| Q1M10 | timeout returns | `test_a_timeout_or_a_failing_spawn_refuses` |
| Q1M11 | message not redacted | `test_the_refusal_message_goes_through_the_one_redactor` |
| Q1M12 | unpopulated gitlinks recursed into | `test_an_unpopulated_gitlink_is_not_inspected_and_nesting_is_bounded` (missing folder) |
| Q1M13 | nesting bound removed | same |
| Q1M14 | listing shape not validated | unrecognised `[not NUL-terminated]` |
| Q1M15 | git_status_entries unchecked | held S19 pin [git_status_entries] |
| Q1M16 | create_git_worktree unchecked | site matrix `[create_git_worktree]` |
| Q1M17 | reuse path's worktree unchecked | `test_the_reused_worktree_s_own_config_worktree_is_refused_before_its_checkout` |
| Q1M18 | remove_git_worktree unchecked | site matrix `[remove_git_worktree]` |
| Q1M19 | remove_git_worktree raises instead of reporting | same |
| Q1M20 | sync folds the refusal into WORKTREE_SYNC_FAILED | `test_the_worktree_sync_lets_the_refusal_through` |
| Q1M21 | fingerprint check inside its try (-> None) | site matrix `[compute_workspace_fingerprint]` |
| Q1M22 | content hash unchecked | held S19 pin [content hash] |
| Q1M23 | evidence hash unchecked | site matrix `[workspace_evidence_hash]` |
| Q1M24 | export_base unchecked | site matrix `[export_base]` |
| Q1M25 | development loop wraps the refusal in RuntimeError | `test_the_direct_development_loop_lets_the_sandbox_refusal_through` |
| Q1M26 | enforce folds it into STRUCTURED_PLAN_UNAVAILABLE | `test_the_enforce_plan_worktree_lets_the_refusal_through` |
| Q1M28 | begin_mutating_run unchecked | site matrix `[begin_mutating_run]` |
| Q1M29 | analyzer revision unchecked | site matrix `[_repository_revision]` |
| Q1M30 | incremental index check inside the fallback try | site matrix `[index_repository(changed)]` (after dcbb761; SURVIVED before: a later inspection masked it) |
| Q1M31 | operator scan unchecked | site matrix `[changed_paths]` |
| Q1M32 | skill commit unchecked (`pass`) | `test_the_skill_commit_refuses_without_running_the_filter_or_committing` |
| Q1M33 | GitTool commit not in the filter set | site matrix `[GitTool commit]` |
| Q1M34 | GitTool blame not in the filter set | site matrix `[GitTool blame]` |
| Q1M35 | HEAD-only gitlinks ignored | submodule `[gitlink only HEAD still has]` |

## Changed existing assertions

- tests/test_s19_git_invocation.py `test_held_q1_a_repository_content_filter_never_runs[2]`: strict xfail removed; the
  body now expects `RepositoryContentFilterRefused` (REPOSITORY_DEFINED) and still asserts no marker (owner decision Q1:
  refusal; the call cannot both refuse and return).
- tests/test_file_integrity_contract_001.py `test_issue12_status_parser_refuses_unparseable_records`: its double answers
  EVERY `subprocess.run`, so the inspection's own spawns got the status bytes and refused (GIT_CONFIG_UNINSPECTABLE)
  before the parser under test ran; the test now holds the inspection out (setup only, assertion unchanged).
- tests/test_worktree_canonical_root.py `test_worktree_locations_are_decided_only_by_worktree_py`: operator_scan line
  numbers 71/78 -> 74/81 (3 added lines; same two offenders, same constants - the S19 precedent).
- Own characterization pins (f581d18) changed before they flipped: remove_git_worktree reports instead of raising (D9);
  the unknown-scope unit expectation in 78485c2 (`blob` scope) became REPOSITORY_DEFINED with the own-bug fix 9ff8638.

## Not covered (reported)

- `kriya review <folder>` (ADAPTER-CLI `git status`): ADAPTER-CLI may import REPOSITORY only through the composition
  modules; the check needs one new import-direction row `("kriya.cli", "kriya.repository.git")` (MEASURED: 2 failures
  in test_import_direction without it). STOP: diff in the S19 Q1 report.
- Kriya's own source checkout (`release_identity.kriya_revision`): the operator's installation (whoever writes its
  `.git/config` can write Kriya's code); not inspected (owner question).
- `git_metadata._probe` (GUI Open Folder) keeps its pre-S19 blanking of filter commands for its read-only status probe
  (no digest, no run); unchanged.
