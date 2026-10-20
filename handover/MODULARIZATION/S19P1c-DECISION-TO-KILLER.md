# S19 P1c decision-to-killer table (TYPE C, Class A security: second fix-forward of S19 Q1; owner decisions 1-9 + the ROOT INVARIANT correction, 2026-10-20)

Base 135acfe (refs/kriya/verified = 7eddc5d; P1, P2 and P1b are unverified ancestry). Commits: d1a1232 (P1
LOCAL_CODE_DEFECT: lexical containment idiom), c8e8a3f (provenance, Open Folder timeout, repository-selecting
environment), ba31dd5 (root invariant: 8(a), 8(b), 8(d)/F3, dynamic twin), then this docs/ledger commit. Pins:
tests/test_s19p1c_provenance.py, tests/test_s19p1c_contexts.py, the strengthened twin `_assert_guarded` and the new
SITES row in tests/test_s19q1_content_filter_refusal.py.

Pre-fix evidence (scratch p1c, MEASURED at 135acfe): out_base.jsonl (every provenance shape fires through a global
definition; 8(a) mutation_scope_evidence fires; 8(b) `gitdir:**/worktrees/**` smudge fires in create_git_worktree;
8(c) GIT_DIR misclassifies; 8(d) workspace_evidence_hash fires; 8(e) does not reproduce), out_hostcfg.txt (this host's
own `git media clean %f` line + a repository-local alias fires through worktree.git_status_entries). Post-fix:
out_fix1.jsonl / out_fix2.jsonl (all refused, no marker, inspection fires nothing).

Unfixed bytes (the final P1c test files + the changed Q1/P1b/E-pin files on a `git archive` of 135acfe, hermetic HOME):
55 failed, 122 passed. Failing = every negative control below. Passing at the base by design: the positive controls
(operator git-lfs on PATH, absolute operator program, `cat`), the 8(e) pins (not reproducible), the operator-origin
pin, the checkpoint-hash pin (MEASURED filter-free), the unchanged-hash positive controls.

Mutation campaign 20261020_184948_67b08bf6 (mutrun v2, `--workers 1`, rev ba31dd5, hermetic HOME, VALID, 521 s,
tree_diff []): 28 mutants, 28 killed (27 at L1, 1 at L2), 0 survived, 0 equivalent.

## Decisions

| # | decision | owner (file) | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| 1-3 | trusted ORIGIN (system/global file outside the repository, command scope) admits only provably operator EXECUTABLE CODE: conservative direct form, program absolute or resolved as git does (exec path, then PATH; entries absolute and outside), no ancestor/real file in any work tree, not an evaluator (closed set, written and resolved name), no relative/in-repository argument; new code GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN + closed PROVENANCE_CAUSES | kriya/repository/git.py `_provenance_cause` | 28 shapes of `test_an_operator_command_that_does_not_provably_run_operator_code_is_refused` (incl. the git-lfs-named repository files), symlink, symlinked directory, alias of a shell, 4 search-path entries, exec path first, linked-worktree and submodule variants, command scope, `test_the_refusal_table_is_closed` | `test_an_operator_program_outside_the_repository_still_runs` x4 (operator git-lfs clean -- %f, absolute program, options + %f, `cat`); GLOBAL_ONLY_GOLDEN and the filter-free digest pin unchanged |
| 6 | Open Folder inspection uses the probe's own 15 s timeout (041ad84 value), never the authority's 60 s | kriya/tools/git_metadata.py `describe_checkout`; git.py `_runner(timeout=)` | `test_the_open_folder_inspection_uses_the_probe_s_own_15_s_timeout` | Open Folder pins of P1b |
| 7/8(c) | the caller's repository-selecting variables (GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR, GIT_INDEX_FILE, ...) are dropped for the question about another folder and for a submodule's git (git clears them); ownership untouched | git.py `_Caller.elsewhere` | `test_the_caller_s_git_dir_does_not_make_an_operator_file_repository_content`, `test_a_submodule_s_git_is_inspected_without_the_parent_s_repository_variables` | the in-repository include under the same GIT_DIR still refuses (same test) |
| 8(a) | mutation_scope_evidence inspects candidate_root immediately before its work-tree `diff`; typed refusal not absorbed (closer and the four development-loop closers reaching it); git_read_lines classification corrected | kriya/workflow/requirement_closers.py, development_loop.py | `test_mutation_scope_evidence_refuses_before_its_diff_runs_a_filter`, `test_every_git_read_lines_verb_is_classified...`, `test_the_development_loop_lets_the_closers_refusal_through`, SITES `requirement_closers.mutation_scope_evidence` | `test_mutation_scope_evidence_is_unchanged_without_a_repository_filter` |
| 8(b) | `worktree add --no-checkout`, inspect the NEW worktree in its own context, then `reset --hard --no-recurse-submodules` (managed worktree and operator-scan base worktree) | kriya/workflow/worktree.py `_add_worktree`; kriya/static_analysis/operator_scan.py | `test_a_filter_visible_only_in_a_linked_worktree_s_context_is_refused_before_its_checkout`, `test_the_operator_scan_base_worktree_is_inspected_in_its_own_context`, twin `worktree.create_git_worktree` | `test_a_two_step_worktree_is_the_same_worktree` (bytes, modes, index, HEAD, status) |
| 8(d)/F3 | the scratch index is inspected WITH its GIT_INDEX_FILE immediately before `add -u` (its new gitlinks = what add -u recurses into); no pre-scan; typed, never None | kriya/workflow/milestone_completion.py `workspace_evidence_hash` | `test_an_untracked_embedded_repository_s_filter_never_runs_through_the_evidence_hash[vendored, plain-dir/vendored]`, twin `milestone_completion.workspace_evidence_hash` | `test_an_untracked_embedded_repository_without_a_filter_hashes_as_before`, `test_the_checkpoint_content_hash_never_runs_an_embedded_repository_s_filter` |
| 8(e) | NOT reproducible (MEASURED: a timeout/spawn failure of the inspection reports GIT_CONFIG_UNINSPECTABLE, status never probed); pinned | - | (passes at base) `test_an_open_folder_inspection_that_cannot_finish_never_runs_the_status_probe` | - |
| ROOT | structural: every filter-capable spawn preceded by an inspection of its cwd, a fresh inspection WITH a scratch index an `add` changed, no `worktree add` without --no-checkout | tests/test_s19q1_content_filter_refusal.py `_assert_guarded` | twin rows above | every other SITES row |
| P1 defect | lexical containment idiom (PLAT-018) in git.py `_inside` -> path_relation | git.py `_inside` | `test_01_no_lexical_containment_idiom_outside_the_platform_package` (failing since 43ba06d) | - |

## Mutants (all KILLED)

| id | mutation | killer |
|---|---|---|
| R1 | search-path entries before the hit unchecked | search path `[""]` |
| R2 | relative program token admitted | `[./tools/clean.sh]` |
| R3 | absolute program: ancestry check skipped | symlinked-directory pin |
| R4 | ancestors not walked | symlinked-directory pin |
| R5 | evaluator set never matches | `[sh]` |
| R6 | shell metacharacters admitted | the `{op}/op-filter` piped-to-sh shape |
| R7 | relative PATH entry admitted | search path `[.]` |
| R8 | exec path not searched first | `test_git_s_exec_path_is_searched_first` |
| R9 | version suffix not dropped | `[python3.12 -]` |
| R10 | unresolved program admitted | `[not-installed-anywhere -- %f]` |
| R11 | argument path check disabled | `[sh tools/clean.sh]` |
| R12 | command scope not judged | command-scope pin |
| R13 | resolved name's evaluator unchecked | alias-of-a-shell pin |
| R14 | `.`/`..` arguments admitted | `[{op}/op-filter .]` |
| R15 | same-repository question with the caller's GIT_DIR | GIT_DIR pin |
| R16 | submodule inspected with the caller's variables | submodule GIT_DIR pin |
| R17 | caller's timeout ignored | 15 s pin |
| T1 | Open Folder passes no timeout | 15 s pin |
| L1 | lexical idiom reintroduced | PLAT-018 tripwire (L2) |
| A1 | mutation_scope_evidence inspection removed | 8(a) pin |
| A2 | refusal absorbed as unavailable | 8(a) pin |
| A3 | development loop absorbs it | development-loop pin |
| B1 | new worktree not inspected | UNINSPECTED_GIT classification pin (structural; the 8(b) pin also covers it) |
| B2 | worktree add checks out again | 8(b) pin |
| B3 | operator-scan base worktree not inspected | operator-scan pin |
| D1 | scratch-index inspection removed | F3 pin |
| D2 | scratch-index inspection without the scratch index | F3 pin |
| D3 | evidence-hash refusal absorbed as None | F3 pin |

## Changed existing assertions (each deliberate)

- `test_the_refusal_table_is_closed`: + GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN and the PROVENANCE_CAUSES keys.
- `test_operator_scopes_outside_the_repository_are_trusted`: plants an operator `git-lfs` on PATH (this host has none;
  an unresolved program now refuses, owner decision 2).
- `test_the_same_repository_question_fails_closed`: the operator file names an absolute program in the same folder;
  `asked` compared as a set (the program's folder is asked the same question).
- S19 Q1 / P1b autouse fixtures also isolate HOME (the Open Folder probe reads HOME/.gitconfig; MEASURED: the host's
  `filter.media.clean` reached the D pins).
- `UNINSPECTED_GIT`: git_read_lines reason corrected (8(a)); operator_scan._git reason updated (8(b)).
- E pin (tests/test_worktree_canonical_root.py): the operator scan's offender expression gains `--no-checkout` (same
  file, function, constant; nothing widened).
- `test_the_inspection_precedes_every_filter_capable_git_operation`: body moved into `_assert_guarded` and strengthened.

## Not covered / owner questions

- HOST CONFIG: this host's ~/.gitconfig has `filter.media.* = git media ... %f` (git = evaluator; MEASURED alias route)
  and `filter.lfs.*` without git-lfs installed (unresolved; MEASURED git then runs nothing, exit 127). Every repository
  is refused on this host (run entry, worktree, Open Folder) until the owner cleans the stale lines, installs git-lfs,
  or changes the rule. Tests not isolating HOME/GIT_CONFIG_GLOBAL fail here (MEASURED 18 in two files; hermetic: green).
- Residual (INFERRED): an operator program outside the closed evaluator set that itself executes repository files
  given only bare-word arguments is admitted; only the target repository's work trees count as repository code.
- Dubious ownership (decision 7): the target repository's own dubious ownership makes git refuse every operation
  (MEASURED rc 128, no filter runs); the inspection admits (its rev-parse fails and the layout is treated as absent).
  INFERRED non-blocker, no unit opened.
