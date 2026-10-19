# S18b decision-to-killer table (unit S18b, TYPE C: S18 follow-ups; OI-127/OI-138, OI-139, OI-140, OI-143)

Owner decisions 2026-10-20 (1: OI-138 approved, two goldens; 2: OI-139 = current fail-closed contract; 3: OI-140
characterize first, A/B per read; 4: OI-143 only if exactly the defect). Base ec65209.

Commits: f29fd26 characterization (before pins + strict-xfail after pins, MEASURED on the unfixed bytes: 8 xfailed);
720c65b OI-138 correction + the two approved goldens; 33c10e4 OI-140 correction; 1371d77 OI-143 correction (+ the
not-own-repository control); docs/notes commit after.

L1 = tests/test_s18b_planned_tool_contracts.py. Campaign 20261019_224109_59ec7834 (mutrun v2, rev 1371d77, VALID, 83 s,
no stragglers, no tree diff): 17/17 KILLED at L1, 0 survivors.

## OI-140 classification (operation | consumer | A/B | evidence | decision)

Consumers of a planned git read's SubtaskResult (TRACED, exhaustive grep of `tool_output`): the run result
(`legacy_result.subtask_results[].tool_output`, WorkflowResult -> CLI/JSON), attempt evidence `tool.execution`
(content = the output; shown as "Tool execution" in the GUI cockpit), telemetry (presence count). The status
(COMPLETED/FAILED) drives the enforce loop's stop/continue. No model subtask, Reviewer, acceptance oracle, owner recovery
or failure attribution reads the output. The step runs in the plan's sequence, i.e. after earlier MODEL subtasks have
changed the candidate.

| operation | consumer | A/B | evidence | decision |
|---|---|---|---|---|
| status | run result + tool.execution evidence (human reader of the run step) | B | MEASURED: after s1 changed shop/service.py in the candidate, the planned status said "nothing added to commit" (owner) while the run committed the change; diverged control: owner status != candidate status | read the candidate (roots[1]) - corrected 33c10e4 |
| diff | same | B | MEASURED: planned diff returned '' after s1; candidate diff shows the lru_cache hunk | read the candidate - corrected 33c10e4 |
| log | same | A | MEASURED: candidate is detached at the base revision and gains no commits during a run (TRACED: no commit in worktree.py / enforce_loop); an owner commit made after candidate creation shows only in the owner | owner (roots[0]) - INTENTIONAL_CONTRACT, pinned |
| branch | same | A | MEASURED: candidate answers "* (no branch) / + main" (meaningless for the run); owner answers its branches | owner - INTENTIONAL_CONTRACT, pinned |
| blame | same | A | committed-line attribution; candidate adds only "Not Committed Yet" lines (already visible in diff); file_path is authorized/resolved against the owner root by the shared S1 reader | owner - INTENTIONAL_CONTRACT, pinned |
| status/diff, scoped snapshot candidate | same | B, unavailable | MEASURED: snapshot has no commits (diff '', everything untracked whatever changed); the owner root is the ENCLOSING repository (its diff showed an uncommitted change outside the workspace) | refused typed TOOL_WORKSPACE_ROOT_UNAVAILABLE before git runs |

## Decisions and killers

| decision | symbol | risk | killer | mutant |
|---|---|---|---|---|
| example B names a plannable tool (JSON) | agent.py example B tool_name | Planner taught a refused step (3 Planner calls burnt, MEASURED) | test_every_tool_the_planner_examples_teach_is_plannable | S18bM01 KILLED |
| the prose names the same plannable tool | example B note | prose primes shell | same + planted control | S18bM02 KILLED |
| example B is schema-valid | example B shape | verification role with no verifier (old defect) | test_a_plan_following_the_worked_tool_example_is_accepted_first_time | S18bM03 KILLED |
| repair prompts list only plannable names | enforce_loop repair call | registered names shown on repair | test_every_repair_prompt_shows_only_the_plannable_names | S18bM04 KILLED |
| a plugin declaration is never honoured | `_shipped_declaration` | a loaded plugin becomes plannable | test_a_loaded_plugin_tool_is_never_shown_to_the_planner (+ validation/executor pins) | S18bM05 KILLED |
| diff is candidate truth | `_CANDIDATE_VIEW_SUBCOMMANDS` | diff reads the owner | test_a_planned_status_or_diff_reads_the_candidate[diff] | S18bM06 KILLED |
| log is baseline truth | same | log reads the candidate | test_a_planned_log_branch_or_blame_reads_the_owner_repository[log] | S18bM07 KILLED |
| blame is baseline truth | same | blame reads the candidate | ...[blame] | S18bM08 KILLED |
| branch is baseline truth | same | branch reads the candidate | ...[branch] | S18bM09 KILLED |
| B reads roots[1] | `_planned_read_root` | the pre-S18b owner read | test_a_planned_status_or_diff_reads_the_candidate | S18bM10 KILLED |
| one root = owner | `len(roots) > 1` | IndexError / wrong root | test_one_root_means_the_owner_is_the_candidate | S18bM11 KILLED |
| a separate candidate is checked | authorize call | snapshot's empty view reported | test_a_candidate_with_no_base_revision_refuses_status_and_diff_typed | S18bM12 KILLED |
| check only a separate candidate | realpath compare | check skipped for the candidate | same | S18bM13 KILLED |
| base revision required | `HEAD^{commit}` in the probe | commit-less snapshot accepted | same | S18bM14 KILLED |
| the candidate must be its own top level | toplevel compare | enclosing repository reported | test_a_candidate_folder_that_is_not_its_own_repository_is_refused | S18bM15 KILLED |
| a failed probe refuses | returncode check | failed probe accepted | test_a_candidate_with_no_base_revision_refuses_status_and_diff_typed | S18bM16 KILLED |
| kernel-less validation never skipped | enforce_loop catalogue | validation skipped, untyped ValueError at dispatch | test_a_kernel_less_enforce_run_refuses_a_tool_subtask_at_validation | S18bM17 KILLED |

Negative controls on the unfixed bytes (f29fd26, MEASURED): the 8 after-pins were strict xfail (each failed for the
defect: example tool unplannable; example refused; status/diff equal to the owner's; scoped status/diff COMPLETED;
end-to-end diff ''; kernel-less ValueError). The not-own-repository control was added with 1371d77; S18bM15 shows it
fails without the toplevel check.
