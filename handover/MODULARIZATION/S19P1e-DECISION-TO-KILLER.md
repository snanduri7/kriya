# S19 P1e decision-to-killer table (TYPE C, Class A security: fix-forward of S19 P1d, OI-161 / OI-141; owner-ordered items 1-3, 2026-10-21)

Base 519a243 (the S19e tip; refs/kriya/verified = 7eddc5d; P1 .. P1d, P2, P2b and S19e are unverified ancestry).
Evidence: the independent read-only characterization of P1d (scratch p1d_char: results.json SUMMARY, probes a_ops,
a_div, a_mech, b_sub, b_extra, c_controls; git 2.54.0 Apple Git-157 and Homebrew) and this packet's scratch p1e
(sig_{base,fixed}_{apple,brew}.json, legacy_remove, collateral, unfixed_run.txt, mut/). Every probe ran in a job-local
HOME / GIT_CONFIG_GLOBAL reproducing the host's stale filter.media / filter.lfs lines (never the real ~/.gitconfig).

Commits: 67530b5 (item 2), 57f702a (item 3), 30c8f3c (item 1), 70b2825 / 0fabdbb / f5d940b (pins: own-bug fix of a
vacuous leg, a fail-closed pin, the legacy-removal pin), 6475bf9 (docs/ledger), 070d621 (own bug: the worktree-location
tripwire classifies git's `worktree` scope name), then this docs fix-forward.

## Item 1 - a dormant repository-defined filter is no longer refused

| # | decision | owner | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| 1 | a `local` / `worktree` definition is judged by ACTIVATION: dormant allowed, active refused `GIT_CONTENT_FILTER_REPOSITORY_DEFINED` naming the active driver and its path count | kriya/repository/git.py `_refuse`, `_repository_defined`, `REPOSITORY_SCOPES` | Q1 `test_a_repository_filter_is_refused_before_any_kriya_git_runs_it[17 sites]` (bound; held), `test_every_repository_scope_shape_is_activation_aware[5]`, `test_the_refusal_names_the_active_driver_and_its_count` | `test_a_dormant_repository_filter_lets_every_guarded_site_run[17 sites]` (FAIL on the unfixed bytes: refused), `test_a_dormant_repository_filter_moves_no_identity_digest` (Q1 GOLDEN) |
| 2 | an active repository definition is never admitted by provenance | `_refuse` (repository list kept apart from `trusted`) | `test_an_active_repository_filter_is_refused_even_when_its_program_is_operator_code` (/bin/cat) | - |
| 3 | fail closed: no work tree judges every definition; check-attr failure / inconsistency and an unresolvable revision refuse; a definition reported where git sees no repository refuses | `_refuse`, `_filter_values`, `_Listing.tree` | `test_without_a_work_tree_every_repository_definition_is_refused`, `test_a_check_attr_answer_git_did_not_give_cleanly_refuses_a_repository_definition[exit, short, echo]`, `test_git_s_own_attribute_resolution_failure_refuses_a_repository_definition`, `test_a_materialized_revision_git_cannot_resolve_refuses_a_repository_definition`, `test_a_repository_definition_reported_where_git_finds_no_repository_refuses` (repository-only fixtures: inert global configuration) | `test_an_unborn_repository_with_a_dormant_definition_is_allowed_and_refused_once_bound` |
| 4 | any other scope refuses outright; ORIGIN_IN_REPOSITORY / ORIGIN_UNRECOGNISED and the operator provenance rule unchanged | `_refuse` | Q1 `test_an_unrecognised_scope_origin_or_listing_refuses[blob]` (dormant, still refused) | P1d / P1c pins unchanged and green |
| 5 | divergence cases (characterization A_div): refused by the operation's own inspection OR provably runs nothing | git's own attribute machinery (unchanged) | attr.tree (D2), dotted driver (D3), deprecated `[filter.KX]` (D3), macro only in the target (D4), .gitattributes arriving with the target (C2), `.GitAttributes` on a case-insensitive disk (D5b): refused | core.attributesFile the checkout creates (D1, both spellings), case-different driver names (D3), includeIf onbranch (D6, sha and branch), an include the checkout creates (E2): the operation runs nothing and the NEXT inspection refuses |
| 6 | the uninspected legacy nested worktree removal stays filter-free | worktree.py `_remove_legacy_nested_worktrees` (unchanged argv) | control without `--force` fires | `test_the_legacy_nested_worktree_removal_runs_no_filter_only_its_own_context_activates[kriya-argv]` |

## Item 2 - no submodule recursion at the managed worktree's checkouts

| # | decision | owner | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| 7 | `checkout -f --no-recurse-submodules <target>` at the reuse and remove sites | kriya/workflow/worktree.py `create_git_worktree`, `remove_git_worktree` | B3 `test_b3_a_gitlink_only_the_target_records_never_reattaches_a_kept_child[reuse, remove] x [child_repository, operator_unproven]`, B4 `test_b4_a_child_include_the_checkout_creates_never_loads_in_that_checkout[reuse, remove]`, `test_both_checkout_sites_pass_no_recurse_submodules` | K1 `test_k1_b4_negative_control_...` (passes on both), vector validity `test_b3/b4_the_vector_is_real_when_the_checkout_recurses` (pass on both), K4 `test_a_recursing_configuration_no_longer_breaks_the_worktree_reuse` (FAILS on the unfixed bytes: exit 128) |

Behaviour change: a repository (or operator) `submodule.recurse=true` no longer updates the managed worktree's
submodules on reuse/reset; a fresh worktree never populated them (`_add_worktree` resets with
`--no-recurse-submodules`), and nothing in Kriya reads a submodule of the managed worktree (TRACED: no other
`submodule` reference in kriya/ or plugins/; the sync skips nested repositories). MEASURED: with the submodule
initialized only in the main workspace, the recursing reuse failed (`could not reset submodule index`, exit 128).

## Item 3 - new finding: repository signature program on GitTool `log`

| # | decision | owner | negative control (FAILS on the unfixed bytes) | positive control |
|---|---|---|---|---|
| 8 | `-c log.showSignature=false` in `GIT_INVOCATION_OPTIONS` | kriya/platform/__init__.py | `test_gittool_log_never_runs_a_repository_signature_program[gpg, openpgp, gpg-mergetag, ssh, x509, x509-mergetag]`, the seam tuple pin (deliberately extended), the dynamic twin's per-spawn `REQUIRED_NEUTRALISATIONS` | `test_the_hardened_options_change_no_git_answer` (log --oneline), the log pin's own output check |

Measured variant table (scratch p1e sig_*.json; 84 rows per run; "fired" = the planted program ran):

| variant (repository config: log.showSignature=true + key) | GitTool log, unfixed seam | GitTool log, fixed seam | plain git log | log --no-show-signature / -c log.showSignature=false | rev-list, for-each-ref, cat-file -p, rev-parse, status, blame (seam) | merge --ff-only (merge.verifySignatures, no Kriya site) |
|---|---|---|---|---|---|---|
| openpgp gpg.program, gpgsig / mergetag | fired / fired | - / - | fired / fired | - | - | fired |
| openpgp gpg.openpgp.program, gpgsig / mergetag | fired / fired | - / - | fired / fired | - | - | fired |
| ssh gpg.ssh.program (+ gpg.format=ssh, allowedSignersFile), gpgsig / mergetag | fired / fired | - / - | fired / fired | - | - | fired |
| x509 gpg.x509.program (+ gpg.format=x509), gpgsig / mergetag | fired / fired | - / - | fired / fired | - | - | fired |

Identical on Apple Git-157 and Homebrew git 2.54.0 (base: 20 fired = 8 seam log + 8 plain + 4 merge; fixed: 12 = 8
plain + 4 merge). The ssh variant without an allowed-signers file ran nothing (MEASURED; why is not traced).

## Changed existing assertions (each deliberate)

- tests/test_s19p1d_activation.py: `test_a_dormant_repository_defined_filter_is_still_refused` ->
  `test_a_dormant_repository_defined_filter_is_allowed_and_refused_once_bound` (the owner contract changed; reason
  in its docstring: the characterization MEASURED dormant = runs nothing for every operation class).
- tests/test_s19q1_content_filter_refusal.py: `test_the_refusal_names_key_scope_and_origin_never_the_command`,
  `test_the_refusal_message_goes_through_the_one_redactor`, `test_only_an_explicitly_empty_command_is_not_executable`,
  `test_dotted_and_mixed_case_driver_names_are_refused` now BIND the driver (they asserted a refusal of an unbound
  definition; their subject - message, redaction, value, name - is unchanged; two also assert the active driver).
- tests/test_s19p1c_contexts.py: the untracked-embedded fixture's file is `lib.py` (was `lib.txt`, which `_bind`'s
  `*.py` never bound: MEASURED guard-off hash ran nothing); the .kriya-embedded file `x.py` likewise; the
  GIT_DIR-submodule pin binds `*.txt` in the child. MEASURED after: guard off fires, guarded refuses.
- tests/test_s19_git_invocation.py: the seam tuple gains `-c log.showSignature=false`.
- tests/test_worktree_canonical_root.py `test_worktree_locations_are_decided_only_by_worktree_py`: the stable-identity
  offender list gains `("kriya/repository/git.py", "<module>:REPOSITORY_SCOPES", "worktree", "{'local', 'worktree'}")`
  - git's config.worktree SCOPE name, never a worktree location (an offender-list entry, not an import-direction
  allowlist row; tests/architecture is untouched). Own bug: 30c8f3c was committed without running this file; MEASURED
  failing at 6475bf9, fixed forward in 070d621.

## Pre-existing failure seen (not P1e)

`tests/test_prd030_terminal_services.py::test_one_terminal_gate_and_one_commit_implementation` fails identically at
the base 519a243 (git archive, hermetic HOME): a source-text tripwire finds `commit_terminal_candidate` in a docstring
(final_review_refusal text); no file it reads is touched by P1e. Not OI-47. Reported for the ledger, not fixed here.

## Mutants

Campaign 20261021_004721_f0b89400 (mutrun v2, `--workers 1`, rev 0fabdbb, hermetic HOME, VALID, 489 s, tree_diff []):
19 mutants, 19 killed at L1, no survivor.

| id | mutation | killer |
|---|---|---|
| D1 | dormant repository definition treated as active | P1d `..._is_allowed_and_refused_once_bound` |
| D2 | active repository definition allowed | P1d `..._is_allowed_and_refused_once_bound` |
| D3 | no work tree (activation unknown) allowed | `test_without_a_work_tree_every_repository_definition_is_refused` |
| D4 | check-attr failure read as dormant for repository-only definitions | `test_a_check_attr_answer_..._refuses_a_repository_definition[exit]` |
| D5 | activation ignored for the repository origin only (judged only when a trusted one exists) | `test_every_repository_scope_shape_is_activation_aware[include.path]` |
| D6 | active repository definition judged by provenance | P1d pin (operator-shaped marker program admitted) |
| D7 | the "not a git repository" shortcut also covers repository definitions | `test_a_repository_definition_reported_where_git_finds_no_repository_refuses` |
| D8 | every untrusted scope made activation-aware | Q1 `test_an_unrecognised_scope_origin_or_listing_refuses[blob]` |
| D9 | `worktree` scope not activation-aware | `test_every_repository_scope_shape_is_activation_aware[config.worktree]` |
| D10 | active driver not named | P1d pin (`driver == "kx"`) |
| D11 | activation detail always "cannot be determined" | P1d pin ("ACTIVE here") |
| D12 | wrong active path count | P1d pin ("for 2 path(s)") |
| N1 / N2 | reuse / remove checkout without `--no-recurse-submodules` | B3 `[reuse-child_repository]` / `[remove-child_repository]` |
| N3 / N4 | reuse / remove checkout with `--recurse-submodules` | B3 `[reuse-...]` / `[remove-...]` |
| S1 | seam without `log.showSignature=false` | seam tuple pin |
| S2 | `log.showSignature=true` | seam tuple pin |
| S3 | the seam drops it for `log` argv only | the dynamic twin (`REQUIRED_NEUTRALISATIONS` on the GitTool log spawn) |
