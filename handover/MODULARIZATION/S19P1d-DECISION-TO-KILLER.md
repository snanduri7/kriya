# S19 P1d decision-to-killer table (TYPE C, Class A security: third fix-forward of S19 Q1, OI-161; owner decisions 1-6, 2026-10-21)

Base 746fc93 (refs/kriya/verified = 7eddc5d; P1, P2, P1b, P1c, P2b are unverified ancestry). Commits: 96759cf (the
activation step, its call sites and pins), then this docs/ledger commit (also the one test-double change in
tests/test_workflow.py). Pins: tests/test_s19p1d_activation.py (64 tests).

Evidence (scratch p1d, git 2.54.0, job-local HOME reproducing the host's stale `filter.media` / `filter.lfs` lines;
the real ~/.gitconfig was never read): probe_semantics.out.json, probe_checkout_source.out.json, probe_co2.out.json
(git's attribute semantics), meas_AB.jsonl / meas_C.jsonl (pre-implementation costs), meas_post.txt (the guard itself,
fixed vs unfixed bytes), unfixed_run1.txt, mut_run1.txt.

Unfixed bytes (the final test files on a `git archive` of 746fc93, hermetic HOME): tests/test_s19p1d_activation.py
63 failed, 1 passed (`test_the_vector_is_real_when_unguarded`: the negative-control validity check, passes by design).
The changed Q1/P1b files pass at the base too (their changes are test-double signatures and a strengthened bind).

Mutation campaign 20261020_221122_8156c871 (mutrun v2, `--workers 1`, rev 96759cf, hermetic HOME, VALID, 1211 s,
tree_diff []): 25 mutants, 25 killed at L1. S1 of that campaign was an INVALID mutant (own bug: the anchor cut the call's
closing parenthesis, a syntax error); re-run correctly as S1b in 20261020_223155_558bff3a (VALID, killed at L1).

## Operation characterization (owner decision 3)

Path sets are obtained WITHOUT running a filter (`ls-files -s`, `ls-files -o --exclude-standard`, `rev-parse`,
`ls-tree -r`; MEASURED no marker). check-attr form: `git check-attr [--source=<tree>] --stdin -z filter`, ONE spawn per
source, paths NUL-separated, relative to the work-tree top where it runs. Cost per inspection only when a non-empty
operator/command definition exists (none = the P1c spawns, unchanged).

| operation (site) | paths touched | superset used | attribute source git uses (MEASURED) | sources asked |
|---|---|---|---|---|
| status / diff / add -u / commit / blame (git_status_entries, fingerprint, analyzer revision + `--changed`, kriya review, Open Folder, GitTool, skill commit) | index paths (stat-dirty compare), the named tracked file | index + untracked | work tree, else index (probe 6) | default |
| `add -A` / `add <path>` (content hash, evidence hash, skill commit) | index + untracked not ignored | index + untracked | work tree, else (scratch) index | default (+ HEAD below) |
| `read-tree HEAD` + scratch `add -A` (compute_workspace_content_hash, workspace_evidence_hash) | as above; the scratch index is HEAD's | index + untracked + HEAD tree | work tree, else the scratch index = HEAD | default + `--source=HEAD` (revisions=("HEAD",)) |
| scratch-index `add -u` (evidence hash, second inspection with GIT_INDEX_FILE) | scratch index paths + its gitlinks' repositories | scratch index + untracked | work tree, else scratch index | default |
| `diff --name-only <base>` (mutation_scope_evidence, operator scan changed_paths) | index paths vs work tree | index + untracked | work tree, else index | default |
| `worktree add --no-checkout` -> inspect -> `reset --hard` (managed worktree, operator scan base) | HEAD tree (no index exists: probe 2) | HEAD tree | the materialized tree only (default and --cached both unspecified there: probe 2) | default + `--source=HEAD` |
| `checkout -f <target>` (worktree reuse, worktree remove) | index + target tree | index + untracked + target tree | the TARGET tree (a bound index with an unbound target ran nothing: probe_co2) | default + `--source=<target>` |
| `archive <revision>` (export_base) | revision tree | index + untracked + revision tree | the archived tree (probe 3) | default + `--source=<revision>` |
| submodule recursion (any of the above) | the child's own sets; a recursing checkout (`submodule.recurse`, repository config Kriya does not override) materializes the commit the target records | child index + untracked + the commits the caller's trees record at that gitlink | the child's | inspected in the child's context (`caller.elsewhere`) |
| embedded / linked-worktree / includeIf contexts | - | the inspection runs in the actual context (P1c) | - | unchanged |

Not modelled (deliberate): `git add -A` with ignored files (`-f`) - no Kriya site; ignored untracked files - no guarded
operation converts one (MEASURED: blame of one dies "no such path in HEAD" before reading; status/add -A skip it).
A path only HEAD has is converted only when HEAD is materialized (then it is a revision). `--cached` matches no guarded
operation (probe_co2) and is not asked.

## Measurements (owner decision 4; MEASURED, idle host unless noted)

Pre-implementation (meas_AB.jsonl, meas_C.jsonl): P1c guard and the raw git costs (min of 3, ms).

| repository | paths (union) | P1c filter-free | P1c stale (refuses) | ls-files -o | check-attr default | check-attr --source=HEAD |
|---|---|---|---|---|---|---|
| A this repository (Kriya-main-demo, linked worktree) | 6,782 | 63 | 20 | 40 | 19 | 249 |
| B largest real non-Kriya repository found (~/kriya-m1-live/.../MR-J07/ws; search of $HOME depth 16 for .git index files) | 3,523 | 33 | 20 | 38 | 14 | 42 |
| B2 Kriya-By-ClaudeCode main work tree | 4,169 | 44 | 20 | 43 | 12 | 109 |
| C synthetic 10k (.gitattributes in every directory) | 10,200 | 42 | 21 | 18 | 22 | 23 |
| C synthetic 50k | 51,000 | 95 | 21 | 60 | 81 | 78 |
| C synthetic 200k | 204,000 | 439 | 23 | 224 | 294 | 992 |

The largest real repository on this machine is A itself (6.8k paths); B is smaller. Post-fix guard (meas_post.txt,
min of 5, ms; "stale" = the dormant host lines, every result "allowed"):

| repository | unfixed filter-free | fixed filter-free | fixed stale | fixed stale + revisions=HEAD |
|---|---|---|---|---|
| A | 73 | 82 | 205 | 542 |
| B | 36 | 43 | 149 | 224 |
| B2 | 50 | 59 | 178 | 320 |
| C 10k | 45 | 51 | 148 | 202 |
| C 50k | 93 | 113 | 342 | 507 |
| C 200k | 273 | 335 | 1,117 | 1,651 |

Scaling: linear in path count for every spawn except `--source=<tree>`, which grows faster (C: 23 -> 78 -> 992 ms;
A's deep tree 249 ms). Bounded: a fixed number of spawns per repository context (no per-path spawn, no batching
needed: one stdin stream). Filter-free: same spawns as P1c; +9 ms (A) / +62 ms (200k) MEASURED; cause INFERRED (not profiled):
building the listing records in Python. No caching (owner decision stands; not measured necessary).

## Decisions

| # | decision | owner (file) | negative control (FAILS on the unfixed bytes) | positive control (fails on the unfixed bytes unless noted) |
|---|---|---|---|---|
| 1 | ACTIVATION before provenance; dormant operator/command definitions are not judged | kriya/repository/git.py `_refuse`, `_active_drivers` | `test_a_repository_that_activates_the_stale_media_filter_is_refused_naming_it[7 sources]`, `test_the_uninstalled_lfs_filter_is_refused_when_active_and_named[2]`, `test_a_repository_alias_behind_an_active_operator_filter_never_runs` | `test_stale_unused_global_media_and_lfs_lines_never_refuse_an_unrelated_repository[17 sites]`, `test_the_stale_lines_move_no_identity_digest`, `test_open_folder_describes_...`, `test_an_active_operator_program_outside_the_repository_still_runs` |
| 2 | git's attribute authority, literal value match | `_filter_values` | `test_a_driver_literally_named_set_is_active_for_a_bare_filter_attribute` | `test_a_negated_empty_or_unrelated_filter_attribute_activates_nothing[6]` |
| 3 | path superset + materialized trees (`revisions=`) per operation (table above) | git.py `_Listing`; the 7 call sites | `test_a_filter_active_only_in_a_new_worktree_s_context_is_refused_before_its_reset`, `test_the_operator_scan_base_worktree_reads_its_head_tree`, `test_export_base_reads_the_attributes_of_the_tree_it_archives`, `test_a_path_only_the_archived_tree_has_activates_the_driver`, `test_the_worktree_checkout_reads_its_target_s_tree[reuse, remove]`, `test_the_content_hash_reads_head_s_attributes_for_its_scratch_index`, `test_an_untracked_file_the_hash_would_add_activates_the_driver`, `test_a_submodule_commit_a_materialized_tree_records_is_judged_in_the_submodule` | `test_an_unborn_repository_s_content_hash_is_unavailable_not_refused` |
| 4 | actual context: work-tree top, caller env, child context | `_Listing(run, top)`, recursion via `caller.elsewhere` | `test_activation_is_decided_for_the_whole_work_tree_even_from_a_subdirectory`, `test_an_untracked_embedded_repository_activating_media_is_refused_in_its_own_context`, `test_a_submodule_activating_media_is_refused_through_the_parent`, `test_mutation_scope_evidence_is_guarded_by_activation` | same tests' dormant legs |
| 5 | fail closed: check-attr exit/inconsistency, unresolvable revision; no work tree = judge all; "not a git repository" = nothing activates | `_filter_values`, `_Listing.tree`, `_layout` | `test_a_check_attr_answer_git_did_not_give_cleanly_refuses[exit, short, echo, unterminated]`, `test_git_s_own_attribute_resolution_failure_refuses` (unfaked: GIT_ATTR_SOURCE naming no tree, check-attr exit 128), `test_a_materialized_revision_git_cannot_resolve_refuses`, `test_without_a_work_tree_to_ask_every_trusted_definition_is_judged` | `test_a_run_begins_in_a_plain_folder_under_the_stale_lines` |
| 6 | repository-origin definitions still refuse, active or not (owner safe default) | `_refuse` (unchanged order) | `test_a_dormant_repository_defined_filter_is_still_refused` (fails at the base only on the new `driver` attribute; the refusal itself held there) | - |
| 7 | the refusal names the ACTIVE driver + operator remedy; never the command (one redactor) | `_unproven`, template | every negative control above (`driver`, "ACTIVE", "Operator remedy", no command text) | - |
| 8 | cost shape: nothing without a definition; one check-attr per source | `_refuse` | `test_activation_is_one_check_attr_spawn_per_source[2]` | `test_a_filter_free_configuration_asks_no_activation_question` |

## Mutants (all KILLED at L1)

| id | mutation | killer |
|---|---|---|
| D1 | dormant treated as active | s19 seam test (hermetic stale HOME refuses everything) / dormant site matrix |
| D2 | active treated as dormant | s19 seam test / every negative control |
| D3 | materialized tree source dropped | new-worktree context pin |
| D4 | default reading replaced by --cached (wrong source) | work-tree .gitattributes pin (attr.tree pin also) |
| D5 | check-attr failure read as allow | check-attr `[exit]` |
| D6 | negated / empty / set attribute activates | negated `[unset]` |
| D7 | attributes asked in the inspected folder, not the top (wrong context) | subdirectory pin |
| D8 | new worktree inspected in the main context (wrong worktree context) | P1c linked-worktree pin |
| D9 | check-attr echo unchecked | check-attr `[echo]` |
| D10 | check-attr record count unchecked | check-attr `[short]` |
| P1 | untracked paths dropped | untracked-file pin |
| P2 | materialized tree's paths dropped | new-worktree pin (and the archived-only-path pin) |
| P3 | submodule commits of materialized trees lost | submodule-commit pin |
| P4 | unborn HEAD refuses | unborn pin |
| W1 | a git dir treated as "no repository" | git-dir pin |
| W2 | "not a git repository" never recognised | plain-folder pin |
| R1 | active driver not named | `driver ==` assertions |
| R2 | repository-origin definitions judged by activation | held Q1 pin / dormant repository pin |
| S1b | export_base without its revision | export pin |
| S2 / S3 | worktree reuse / remove without the target | target pins |
| S4 / S5 | content / evidence hash without HEAD | scratch-index pin |
| S6 / S7 | operator-scan / managed worktree reset without HEAD | operator-scan / new-worktree pins |

## Changed existing assertions / doubles (each deliberate)

- No existing pin asserted the refusal of a DORMANT definition (every P1c provenance pin binds its driver); none changed.
- tests/test_s19q1_content_filter_refusal.py: `_fake_run` and `_entry_checks_pass` doubles accept the new keywords
  (`data=` for check-attr's stdin, `revisions=`); `test_operator_scopes_outside_the_repository_are_trusted` now binds
  `lfs` so the definition is ACTIVE and the provenance rule (not dormancy) is what admits it.
- tests/test_s19p1b_residuals.py `test_the_same_repository_question_fails_closed`: the double forwards `data=`.
- tests/test_workflow.py `test_greenfield_bootstrap_failure_fails_closed_before_candidate_writes`: its double answers
  EVERY subprocess.run (text stderr); it passed at the base only because the inspection consumed both answers and the
  worktree's own rev-parse hit StopIteration. The inspection is held out (setup only, the Q1 precedent); assertions
  unchanged.

## Not covered / owner notes

- The repository-origin-but-unactivated decision: kept REFUSED (reproducer `test_a_dormant_repository_defined_filter_is_still_refused`):
  repository configuration is repository-controlled and a later path set (or a .gitattributes the model writes) can
  activate it; activation-awareness gives no usability gain there (`git lfs install --local` is already refused with
  a remedy since Q1).
- Superset effect (by design): an untracked file bound to an unproven active driver refuses status-only operations too
  (status converts no untracked file); a recursing-checkout commit missing in the submodule refuses (fail closed).
- P1c question residue unchanged: `git <sub>`, interpreter + script, uninstalled programs stay refused WHEN ACTIVE.
- Docker-lane tests (3 in tests/test_prd036_release_gate.py) fail identically at the base here: the Docker daemon is
  not running on this host (environment); NOT_RUN for the TEST agent's Docker lane.
