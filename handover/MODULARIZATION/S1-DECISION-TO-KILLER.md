# S1 decision-to-killer table (correction unit S1, TYPE C: TOOLBOX authority for model-authored TOOL actions; OI-88)

One row per authority decision S1 introduces or relies on. Every mutant was run with
`~/kriya-modularization-tools/mutrun.py` v2 (git archive of a fixed revision, `kriya.__file__` under the sandbox, sha256
manifest before/after; deselect `not test_engine_runtime_verification_trace and not test_post_check_passes_on_the_reference`);
L1 = tests/test_s1_toolbox_authority.py + tests/test_tool_subtask_hitl_confirmation.py, L2 = tests/test_tool001_*,
test_subtask_executor, test_tools_command, test_prd012_egress, L3 = test_e12_enforce_characterization, test_lr_r1_m1_t6_fixes,
test_workflow_controller, test_policy_filesystem_authorized_writer, test_plat039_trusted_control_paths. Every campaign VALID.

Campaigns (all MEASURED):
- c1 `20261019_125945_5a2eecff` (5ca49cb): 27 mutants, 25 killed at L1, 2 survived (M17, M18a), 435 s.
- c2 `20261019_130743_26f40fbf` (5ca49cb): M17b killed at L1, 6 s.
- c3 `20261019_130925_1a98f556` (c0df8f2, after the own test-weakness fix): the 10 read/containment mutants re-run; 8 killed at
  L1, M17 / M18a survived again (equivalent, below), 243 s.
- manual two-site mutant M18c (scratch `git archive` of 5ca49cb, then of c0df8f2; not a mutrun campaign because mutrun takes
  one anchor per mutant): `resolve()` without `os.path.abspath` AND a raw lexical prefix in `is_within_scope`. At 5ca49cb all
  12 `..`-form pins failed, but the 3 read pins failed with SENSITIVE_PATH_DENIED (the outside target was named
  `secret.txt`): own test weakness, fixed in c0df8f2 (`data.txt` / `foreign.py`); at c0df8f2 the read pins fail because the
  read COMPLETED. Control (unmutated archive): 12 passed.

| decision | symbol | risk | killer test | mutation | result | evidence |
|---|---|---|---|---|---|---|
| a planned call is model-authored, never the operator's | `subtask_executor._execute_tool_subtask` -> `execute_model_authored` | author flipped: planned step runs with operator authority | test_a_planned_filesystem_write_is_refused_and_writes_nothing[absolute-outside-human-in-the-loop] | M01 executor calls `tool.execute(**args)` | KILLED L1 | c1 |
| the operator path keeps the operator's authority | `kriya/cli.py tools_execute` -> `tool.execute(**args)` | operator CLI refused like a planned call | test_the_operator_cli_writes_where_the_operator_says | M02 CLI calls `execute_model_authored` | KILLED L1 | c1 |
| the roots are the owner workspace and the candidate | `EnforceLoop.run` `tool_roots=(workspace_path, plan_workspace_path)` | no roots / widened roots | write pins (exact codes), test_a_planned_read_inside_the_workspace_still_works | M03 `()`, M04 `("/",)` | KILLED L1 / KILLED L1 | c1 |
| unknown mode counts as human-in-the-loop | `ModelToolInvocation.reviews_every_change` | config without a mode fails open | test_an_unreadable_mode_counts_as_human_in_the_loop | M05 `None` not strict | KILLED L1 | c1 |
| no authority from a missing confirmation flag | `BaseTool.authorize_model_invocation` (default) | HITL stop keyed on `requires_confirmation` only | test_an_undeclared_tool_is_refused_in_human_in_the_loop | M06 `and self.requires_confirmation` | KILLED L1 | c1 |
| HITL is detected at all | `reviews_every_change` | HITL never detected | test_planned_validate_refactor_is_refused_in_human_in_the_loop | M26 `return False` | KILLED L1 | c1 |
| only a BaseTool is dispatched | `execute_model_authored` isinstance | a duck-typed object bypasses schema and authority | test_a_non_tool_object_is_never_dispatched | M07 check removed | KILLED L1 | c1 |
| a broken or denying check refuses | `execute_model_authored` except branch | denial swallowed, call runs | write pins | M08 fall through to run | KILLED L1 | c1 |
| authorize runs before run | `execute_model_authored` | authorize skipped | write pins | M09 `authorized = validated` | KILLED L1 | c1 |
| every planned write refused (DENY_ALL), every spelling | `FilesystemTool.authorize_model_invocation` | write treated as read | write pins; test_every_non_read_filesystem_operation_is_a_refused_write | M14 `"write"` in the read tuple | KILLED L1 | c1 |
| write containment = AuthorizedFileWriter over the roots | same | outside writes reported/handled as in-scope | write pins (exact codes) | M15 writer rooted at `/` | KILLED L1 | c1 |
| read containment = AuthorizedFileReader over the roots | `_authorized_read` | outside reads allowed | read pins | M16 reader rooted at `/` | KILLED L1 (c1, c3) | c1, c3 |
| list is a read | `FilesystemTool.authorize_model_invocation` | list skips the reader | read pins | M24 list path only resolved | KILLED L1 (c1, c3) | c1, c3 |
| symlinks resolved for containment | `is_within_scope` (reused) | a link inside pointing outside admitted | symlink-inside-to-outside pins | M17b normalized-but-unresolved prefix | KILLED L1 | c2 |
| (redundant layer) `_canonical` realpath | `kriya.policy.filesystem._canonical` | - | - | M17 `realpath` -> `abspath` | SURVIVED: EQUIVALENT - `path_relation` realpaths its target again (`kriya/platform/filesystem_semantics.py:90`), so `realpath(abspath(x)) == realpath(x)`; removing both resolutions is M17b (killed) | c1, c3 |
| `..` normalized before authorizing | `ModelToolInvocation.resolve` | `..` escapes | dotdot / normalization / repeated-separator pins | M18a no `abspath` | SURVIVED: EQUIVALENT - the reused authority realpaths the same string and `_run` abspaths it, so the authorized and effective paths are identical; removing BOTH normalizations (M18c, manual two-site) is killed | c1, c3, manual |
| containment by filesystem identity, not prefix | `is_within_scope` (reused) | lexical prefix on the raw string | symlink pins | M18b raw prefix | KILLED L1 (c1, c3) | c1, c3 |
| search reads only admitted files | `SearchTool._run_model_authored` | walk opens a symlinked file outside | test_search_never_reads_through_a_symlink_or_outside | M10 `admit=None` | KILLED L1 (c1, c3) | c1, c3 |
| planned git commit refused (PLAT-039) | `GitTool.authorize_model_invocation` | planned commit mutates `.git` | test_a_planned_git_commit_is_refused_in_every_mode | M11 branch removed | KILLED L1 | c1 |
| git blame file is a read | same | blame through a link | test_ast_and_git_blame_read_only_inside | M23 branch removed | KILLED L1 (c1, c3) | c1, c3 |
| ast file is a read | `ASTTool.authorize_model_invocation` | ast reads outside | test_ast_and_git_blame_read_only_inside | M22 returns args | KILLED L1 (c1, c3) | c1, c3 |
| validate_refactor refused in HITL | `ValidationTool.authorize_model_invocation` | build/tests run unreviewed | test_planned_validate_refactor_is_refused_in_human_in_the_loop | M12 branch removed | KILLED L1 | c1 |
| validate_refactor files are reads | same | compile-check of an outside file | test_validate_refactor_outside_hitl_keeps_its_own_authority_and_reads_only_inside | M25 check removed | KILLED L1 (c1, c3) | c1, c3 |
| shell refused in HITL below B3 | `ShellTool.authorize_model_invocation` | B3 removed -> shell runs | test_the_toolbox_entry_refuses_on_its_own_without_the_executor | M13 branch removed | KILLED L1 | c1 |
| MCP keeps TOOL-002/003 (no S1 refusal) | `MCPTool.authorize_model_invocation` | approved MCP refused in HITL | test_mcp_tools_keep_their_own_tool002_authority_in_every_mode (+ the HITL re-run of the tool001 MCP scenarios) | M19 falls back to the BaseTool default | KILLED L1 | c1 |
| the refusal is typed and recorded | executor `ModelInvocationRefused` branch | reason code lost | test_planned_validate_refactor_is_refused_in_human_in_the_loop | M20 `reason_codes=()` | KILLED L1 | c1 |
| the mode is read from the run | `subtask_executor._autonomy_mode` | constant non-HITL | same | M21 returns `"guardrails"` | KILLED L1 | c1 |

Structural tripwires (planted controls in the same file): `test_the_bypass_finder_sees_a_planted_direct_call` /
`test_no_model_authored_code_path_reaches_a_tool_outside_the_governed_entry` (no `tool.execute()` / `._run()` in
kriya/workflow); `test_the_tripwire_sees_a_planted_undeclared_writing_tool` /
`test_every_shipped_tool_declares_its_model_authority_and_only_the_governed_writer_writes` (closed inventory of the 7 shipped
BaseTool classes, each declaring `authorize_model_invocation`; the only filesystem writer is `FilesystemTool._run`);
`test_the_toolbox_refusal_codes_are_a_closed_table`.

NOT covered (owner questions, S1 report): negative control (g) outside human-in-the-loop for shell, validate_refactor and an
undeclared plugin tool - unchanged by S1 and pinned (`test_an_undeclared_tool_in_a_non_hitl_mode_keeps_its_own_authority`,
`tests/test_prd012_egress.py`, the t6 equivalence scenarios). Residual (INFERRED, not tested): the authorize-then-open window
for a read (a symlink swapped between the reader's decision and `open`), the same window DEV-INV-001 accepts.
