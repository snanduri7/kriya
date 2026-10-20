# S19 P1b decision-to-killer table (TYPE D: fix-forward of S19 Q1's residual paths; owner decisions A-F, 2026-10-20)

Base 041ad84 (refs/kriya/verified = 7eddc5d; P1 43ba06d and P2 are unverified ancestry). Commits: 5b31031 (E), e2d85c8
(A), 4e8973c (D), 23b0d61 (C), f45a674 (B), 4325760 (own bug: non-discriminating C pin), 68b8b6e (own bug: 23b0d61's
`worktree list` literal broke the E pin; C re-done without widening), then this docs/ledger commit. Pins:
tests/test_s19p1b_residuals.py (34 tests) + the stable-identity E pin in tests/test_worktree_canonical_root.py.

Unfixed bytes (the final test file on a `git archive` of 041ad84): 16 failed, 18 passed. Failing = every negative
control below. Passing at the base, by design (MEASURED already true there, kept as pins): the 12 include shapes, the
positive controls, `test_an_installation_root_comes_only_from_the_installation`,
`test_trust_is_never_decided_by_the_installation_path`, the main-only review control. The target-side refusals of the
run entry and worktree creation already held at the base (P1 has no path exemption); B's negative control fails at the
base on its `kriya review` and Open Folder legs.

Mutation campaign 20261020_171951_8d6c2f74 (mutrun v2, `--workers 1`, rev 68b8b6e, VALID, 331 s, tree_diff []),
unmutated L1+L2 first: 215 passed. 20 mutants, 20 killed at L1, 0 survived, 0 equivalent. (An earlier campaign at
4325760 was stopped and is VOID: its L1 already failed the E pin.)

## Decisions

| # | decision | owner (file) | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| A | `kriya review <folder>` asks the one authority through APPLICATION before its own `git status`; no CLI -> REPOSITORY import, no allowlist row, the status spawn stays in the CLI (access-site counts unchanged) | kriya/application/project.py `ProjectService.admit_repository_read`; kriya/cli.py `review` | `test_kriya_review_of_a_folder_refuses_a_repository_filter_before_any_git_runs_it`, `test_kriya_review_inspects_immediately_before_its_own_git_status`, `test_the_cli_reaches_the_authority_only_through_the_application_use_case`, `test_admit_repository_read_is_a_typed_application_refusal_carrying_the_repository_reason` | `test_kriya_review_still_reviews_git_s_changed_files_where_no_repository_filter_is_defined` |
| B | installation trust is a ROLE: exactly `kriya_revision` and `tracked_release_files`; every uninspected git function classified; installation roots only from the installation; no path test | tests (INSTALLATION_STATE, UNINSPECTED_GIT); role notes in kriya/core/release_identity.py, kriya/distribution.py | `test_the_installation_state_git_sites_are_exactly_these`, `test_kriya_s_own_checkout_opened_as_the_target_is_refused_like_any_repository` | `kriya_revision` on the same path returns the revision (same test) |
| C | an operator file definition is judged by its ultimate file; a file of ANY work tree of the repository is repository content (`rev-parse --git-common-dir` in its folder; only "not a git repository" = elsewhere) | kriya/repository/git.py `_same_repository` | `test_inspected_from_a_linked_worktree_a_main_work_tree_file_is_repository_content`, `test_inspected_from_the_main_work_tree_a_linked_worktree_file_is_repository_content`, `test_the_same_repository_question_fails_closed[4]` | `test_an_operator_definition_outside_every_work_tree_still_runs[2]`, the 12 shapes of `test_an_operator_definition_whose_ultimate_file_is_in_the_repository_is_refused` |
| D | Open Folder asks the same authority with the probe's own git and environment; no GUI-specific filter policy; refused = dirty/changed_entries null, reason = the typed refusal (no schema change) | kriya/tools/git_metadata.py `describe_checkout`; kriya/repository/git.py `refuse_repository_content_filters(git=, env=)` | `test_open_folder_reports_a_repository_filter_as_a_typed_refusal_and_runs_nothing`, `test_open_folder_inspects_the_configuration_its_own_probe_reads`, `test_the_open_folder_probe_keeps_no_filter_policy_of_its_own` | `test_open_folder_keeps_an_operator_global_filter_working` (fails at the base: blanked) |
| E | worktree-offender pin by stable identity (file, function or module-level name, constant, holding expression); exact list, nothing widened | tests/test_worktree_canonical_root.py | identities MEASURED equal at 7eddc5d and 041ad84; three comment lines inserted above each offender: new pin passes, old line pin fails | - |
| F | no memoization | - | - | - |

## Mutants (all KILLED at L1)

| id | mutation | killer |
|---|---|---|
| P1bA1 | admit_repository_read inspects nothing | review refusal pin |
| P1bA2 | review inspects AFTER its git status | review refusal pin |
| P1bA3 | review never inspects | review refusal pin |
| P1bA4 | refusal loses its `reason` | `test_admit_repository_read_...` |
| P1bA5 | refusal swallowed | review refusal pin |
| P1bD1 | Open Folder inspects with the process environment | `test_open_folder_inspects_the_configuration_its_own_probe_reads` |
| P1bD2 | Open Folder does not inspect | `test_open_folder_reports_..._runs_nothing` |
| P1bD3 | `_runner` ignores the caller's git/env | `test_open_folder_inspects_the_configuration_its_own_probe_reads` |
| P1bD4 | refusal swallowed, status still probed | `test_open_folder_reports_..._runs_nothing` |
| P1bD5 | refused folder reports dirty=False | same |
| P1bC1 | same-repository question removed | linked -> main pin |
| P1bC2 | any rev-parse failure = elsewhere | fails-closed `[dubious ownership]` |
| P1bC3 | unparseable answer accepted | fails-closed `[empty answer]` |
| P1bC4 | compared against the git dir instead of the common dir | linked -> main pin |
| P1bB1 | kriya_revision inspects (role lost) | `test_the_installation_state_git_sites_are_exactly_these` |
| P1bB2 | path-based exemption for `_SOURCE_ROOT` | `test_trust_is_never_decided_by_the_installation_path` |
| P1bB3 | begin_mutating_run unchecked | own-checkout negative control |
| P1bB4 | release_identity reads the cwd's repository | `test_an_installation_root_comes_only_from_the_installation` |
| P1bE1 | an offender's holding expression changes | E pin |
| P1bE2 | a new offender constant | E pin |

## Changed existing assertions

- tests/test_worktree_canonical_root.py `test_worktree_locations_are_decided_only_by_worktree_py`: `file:line:value`
  -> (file, enclosing function or module-level name, value, holding expression), same four offenders (owner decision E).
- tests/test_kup_workspace.py `test_a_content_filter_driver_the_repository_defines_is_never_executed`: unchanged and
  green (marker absent; the folder is now reported as refused instead of blanked).

## Not covered (owner questions)

- A global/system filter COMMAND that runs a repository script (`clean = ./tools/clean.sh`, git runs it at the work
  tree top): the definition is the operator's, so it is allowed and runs (MEASURED, scratch probe_c_includes). Refusing
  it needs command parsing (`sh tools/x` evades any argv0 rule).
- The Open Folder inspection inherits the authority's 60 s timeout per spawn (the probe's own is 15 s).
