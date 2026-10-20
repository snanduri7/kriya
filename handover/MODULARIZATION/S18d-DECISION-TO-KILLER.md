# S18d decision-to-killer table (unit S18d, TYPE C: enforce TOOL sentence OI-149; planned read view OI-150 a/b/c)

Owner decisions 2026-10-20 (Part 1: fix the stale enforce sentence, golden movement approved only as its mechanical
consequence; Part 2: the read view follows the authoritative observed effect, no label is evidence, reuse the canonical
evidence, no second detector; no forced symmetry with git; control artifacts never exposed as source through the
canonical protected-path ownership, no "hide all dotfiles"). Base 43ba06d (P1 candidate) + ledger 23f5c93.

Commits: 8faf487 characterization (31 strict-xfail after-pins, MEASURED failing on the unfixed bytes); 3bca00a Part 1;
ae0b9f9 Part 2 a/b; 57cb343 Part 2 c; docs/notes/this table after.

L1 = tests/test_s18d_read_view_and_enforce_prompt.py. Campaign 20261020_161613_3c208135 (mutrun v2, rev 57cb343, -n 1,
VALID, 552 s, tree_diff [], no stragglers): 23/24 KILLED at L1, 1 EQUIVALENT (C8).

## Part 1 - golden / digest scope (MEASURED)

The full golden set (kernel/gates/generation/runtime/repair/repository/state/config/kup/policy goldens, E12, E13) plus
the enforce-prompt suites ran after the edit: **no golden and no digest moved**. The enforce kernel goldens script the
plan (planner_calls 0), so the request is not fingerprinted; the E13 planner digest is unchanged because the Planner
system prompt keeps byte-identical text (the shared constant replaces the literal). A first, longer wording (+216 chars)
moved the outcome of two capacity-margin tests (tests/test_prompt_budget_fit_001ab.py, test_prompt_fit_role_chain_001.py:
the first enforce request fitted 0 reference entries instead of 1) - not a golden, but an unapproved observable change -
so the sentence was shortened (+69 chars): the fitted reference counts are identical before and after
(MEASURED: [1, 3] at 12288, [0, 0] at 8192 on both byte sets).

## Part 2 - read-view table (case | observed-effect source | view | evidence)

| case | observed effect source | view | evidence |
|---|---|---|---|
| read after a MODEL unit that applied a change | ControlState.subtask_written_files[unit] non-empty | candidate | table row model-changed-a-file; e2e r1 |
| read after a verification unit (DENY_ALL, applied nothing) | record [] | walk on -> owner if nothing else | rows verification-unit-changed-nothing; e2e r3 (MEASURED candidate before) |
| read after a NO CHANGE implementation unit | record [] (VERIFIED_NO_CHANGE) | walk on | row no-change-implementation-unit; e2e r4 (MEASURED candidate before) |
| unit labelled verification that applied a change | record non-empty | candidate (label irrelevant) | row verification-label-that-changed-a-file |
| no-change unit after a change | walk reaches the change | candidate | row verification-unit-after-a-change; e2e r2 |
| no MODEL ancestor / TOOL-only ancestry | - | owner | row no-model-ancestor; e2e r5; S18c pins |
| MODEL unit with no record / no evidence passed | none | candidate (S18c fail-closed) | rows model-unit-without-evidence-fails-closed, test_without_evidence... |
| git status/diff, any dependencies | - | candidate (S18b) | test_git_keeps_its_per_subject_roots_whatever_the_evidence |
| git log/branch/blame | - | owner (S18b) | same |

Canonical evidence reused: ControlState.subtask_written_files ("the real, applied file paths that subtask actually
wrote", from the candidate transaction's state.all_files_written, which excludes byte-identical rewrites), already the
authority for owner recovery and abandoned-plan quarantine, and in scope at the enforce TOOL call site. No second
detector. Planned deletes never apply inside the candidate before the terminal commit (commit_service), so the record
misses no in-run mutation.

## Part 2c - control artifacts (owner of the rule: kriya/policy/filesystem.py, PLAT-039)

MEASURED before: owner listing showed .git and .kriya; owner .git/config readable; worktree candidate's .git gitfile
listed, readable and a search hit (`gitdir: <owner path>`); snapshot listing showed .git and the sentinel, the sentinel
read/ast/search-hit, .git listable and searchable. Rule: TRUSTED_CONTROL_NAMES = .git, .kriya + SCOPED_SNAPSHOT_SENTINEL
(worktree.py imports the name); planned filesystem/search/ast refuse a named control path (TRUSTED_CONTROL_PATH_DENIED,
trusted_control_path_read_denial) and drop control entries from listings and search hits, judged under the innermost
root holding the target (the candidate worktree lives under the owner's .kriya: an any-root judgement refused every
candidate read - my own intermediate bug, caught by 48 S18c pins, mutant C4). Writer, attempt planned-target refusal and
terminal-commit backstop now also cover the sentinel (same code). No golden and no reason-code table moved.

## Decisions and killers

| decision | symbol | risk | killer | mutant |
|---|---|---|---|---|
| P1: no stale no-router claim | enforce sentence | stale sentence back | test_the_enforce_request_drops_the_stale_no_router_claim | Q1 KILLED |
| P1: names no unplannable tool | same | shell named | test_the_enforce_request_names_no_tool_a_planned_step_cannot_run | Q2 KILLED |
| P1: the rule is stated | same | sentence dropped | ...state_one_planned_tool_contract | Q3 KILLED |
| P1: plannable, not registered | PLANNED_TOOL_CATALOGUE_RULE | rule widened | same | Q4 KILLED |
| P1: no list -> MODEL | same | Planner told to use tools | same | Q5 KILLED |
| P1: one shared wording | agent.py | Planner prompt loses the rule | same (+E13 digest) | Q6 KILLED |
| P1: stated once | same | duplicated | same | Q7 KILLED |
| A: a record [] is no change | _changed_the_candidate | always change | defect rows | A1 KILLED |
| A: no record fails closed | same | no record = no change | model-unit-without-evidence row | A2 KILLED |
| A: a non-empty record is a change | same | never change | model-changed-a-file row | A3 KILLED |
| A: label is not evidence | reads_candidate_view | role decides | no-change-implementation-unit row | A4 KILLED |
| A: walk through no-change units | pending.extend | MODEL stops the walk | verification-unit-after-a-change row | A5 KILLED |
| A: enforce passes the record | enforce_loop | evidence not passed | e2e baseline test | A6 KILLED |
| A: execute forwards it | execute | dropped | defect rows | A7 KILLED |
| A: invocation uses it | _execute_tool_subtask | dropped | defect rows | A8 KILLED |
| C: named control path refused | _authorized_read | read allowed | test_naming_a_control_path_is_refused_typed | C1 KILLED |
| C: listing filtered | FilesystemTool._run_model_authored | entries shown | test_a_listing_leaves_out_the_control_entries | C2 KILLED |
| C: search filtered | SearchTool admit | control hit | test_a_search_never_hits_a_control_file | C3 KILLED |
| C: innermost root | _is_control_path | any root (candidate under owner .kriya) | S18d/S18c candidate reads | C4 KILLED |
| C: innermost, not outermost | same | min root | same | C5 KILLED |
| C: sentinel in the set | TRUSTED_CONTROL_NAMES | sentinel exposed | listing [snapshot] | C6 KILLED |
| C: folded names include it | _FOLDED_CONTROL_NAMES | depth/case spelling | writer test | C7 KILLED |
| C: no-holding fallback | _is_control_path | fail open | - | C8 EQUIVALENT: reachable only for a listing entry that is a symlink resolving outside every root (the reader refuses every other outside target first); for such a target is_trusted_control_path is False under every root (its relative path starts with '..', and no root's control directory is its ancestor) except when a stat fails (UNKNOWN), which no deterministic test can produce; kept as defence in depth (as S18cA10) |
| C: the read denial's code | trusted_control_path_read_denial | another code | named-control-path test | C9 KILLED |

Negative controls on the unfixed bytes (MEASURED at 8faf487 with --runxfail): 31 after-pins failed - Part 1: 2
(stale sentence present; rule constant absent); Part 2a: 12 table rows + the e2e (r3, r4 saw the candidate:
`(True, True) == (False, False)`); Part 2c: 3 listings, 9 named control paths COMPLETED, 3 search hits, the writer test.

Changed assertions: tests/test_workflow_controller_enforce.py (the stale sentence -> the corrected one, still MODEL-only);
tests/test_s18c_read_root_identity.py (the snapshot listing no longer shows .git/.kriya-scoped-snapshot - OI-150 c owner
decision; the cycle walk calls the renamed reads_candidate_view, semantics unchanged without evidence).
