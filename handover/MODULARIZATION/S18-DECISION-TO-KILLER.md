# S18 decision-to-killer table (correction unit S18, TYPE C: planned-command authority, Option B; OI-127, OI-88, OI-129, OI-135)

STATUS: decisions and intended killers were written with the tests (commit group a, 7b9b79a), BEFORE the production code
existed; the results below were filled in after the campaigns (ordering deviation from "table with the flips": a killer cannot
be measured before the code it kills exists).

Preflight and verdict: handover/MODULARIZATION/S18-PREFLIGHT.md (OPTION_B_RECOMMENDED, accepted by the orchestrator 2026-10-19).
L1 = tests/test_s18_planned_tool_governance.py + tests/test_s18_planned_command_characterization.py (+ grep of the symbol);
L2 = test_s1_toolbox_authority, test_tool001_autonomous_tool_execution, test_tool_subtask_hitl_confirmation, test_prd012_egress,
test_planner_robust_001_repair, test_work_unit_stages, test_s16a_autonomy_mode_vocabulary.

| decision | symbol | risk | intended killer | mutant |
|---|---|---|---|---|
| an empty declaration is undeclared | `resolve_planned_tool_governance` None branch | a tool declaring None becomes plannable | test_an_unreadable_garbage_or_empty_declaration_is_refused_even_when_admitted[declares-none] | S18M01 |
| OPERATOR_ONLY is never plannable | `PLANNABLE_TOOL_GOVERNANCE` | planned shell / validate_refactor run again | test_planned_shell_and_validate_refactor_are_refused_in_both_modes; P1 pins | S18M02 |
| the executor rechecks | `execute_model_authored` recheck | a stale or injected plan runs an unplannable tool | test_the_model_authored_entry_rechecks_on_its_own; test_the_executor_refuses_an_unplannable_tool_before_it_runs | S18M03 |
| the catalogue projects the resolution | `subtask_executor.plannable_tool_names` | every registered tool shown / accepted | test_the_catalogue_projects_only_plannable_tools | S18M04 |
| validation enforces it | `validate_tool_capability_membership` plannable set | an unplannable tool validates | test_plan_validation_refuses_a_registered_but_unplannable_tool | S18M05 |
| a listing failure admits nothing (enforce) | `enforce_loop` catalogue snapshot | listing failure skips the check (old `None`) | test_a_registry_listing_failure_in_a_real_enforce_run_admits_no_tool | S18M06 |
| a self-declared plugin is not honoured | `_shipped_declaration` table | R72/R73: plugin grants itself plannability | test_nothing_but_a_shipped_declaration_is_plannable[self-declared-plugin] | S18M07 |
| only the class's own declaration counts | `cls.__dict__` | a subclass inherits plannability | ...[subclass-inheriting-the-declaration] | S18M08 |
| the declaring file must be the shipped file | `co_filename` check | a same-named redeclaring class passes | ...[subclass-named-like-a-shipped-tool] | S18M09 |
| a declaration must be a ToolGovernance value | type check | "read_only" (str == enum) passes | ...[garbage-value] | S18M10 |
| the refusal codes are a closed table | `MODEL_TOOL_REFUSAL_REASON_CODES` | a code dropped | test_the_toolbox_refusal_codes_are_a_closed_table + every refusal pin | S18M11 |
| the new plan code is distinct | `PLANNED_TOOL_NOT_PLANNABLE` | mislabelled UNREGISTERED | test_the_membership_check_reports_the_new_code_only_for_registered_tools | S18M12 |
| a listing failure admits nothing (helper) | helper except branch | a fabricated catalogue | test_a_registry_that_cannot_be_listed_admits_no_tool | S18M13 |
| the Planner sees only plannable tools | `stage_planning` prompt | registered names shown | test_the_planner_is_shown_only_plannable_tools | S18M14 |
| the enforce repair prompt shows only plannable tools | enforce repair prompt | registered names shown | R80 pin transcript assertion | S18M15 |
| the shell tool's own wall | `ShellTool.authorize_model_invocation` | a direct authorize call passes | test_the_tools_own_wall_refuses_a_direct_authorize_too | S18M16 |
| a planned git read runs in the supplied root | `GitTool._run_model_authored` | process cwd used (OI-129 item 3) | test_a_planned_git_read_runs_in_the_supplied_owner_root_not_the_process_folder | S18M17 |
| shell is declared OPERATOR_ONLY | `ShellTool.planned_governance` | shell plannable | P1 shell pins | S18M18 |
| validate_refactor is declared OPERATOR_ONLY | `ValidationTool.planned_governance` | validate_refactor plannable | P1 validate pin | S18M19 |
| the reader parses | `is_human_in_the_loop` | unknown mode read as not-HITL | test_the_reader_itself + every OI-135 site test | S18M20 |
| the config reader parses | `autonomy_mode_of` | unvalidated mode returned | test_the_reader_itself | S18M21 |
| stage_apply reads through the reader | `approval_gate` | typo = apply without review | test_the_pre_apply_approval_gate_stops_typed_and_applies_nothing | S18M22 |
| spec_compliance reads through the reader | contract escalation | typo = escalation unavailable, untyped | test_the_contract_escalation_stops_typed | S18M23 |
| runtime command approval | `_runtime_commands_need_approval` | typo = no approval needed | test_runtime_command_approval_stops_typed | S18M24 |
| managed-service block | `_managed_service_blocked_without_approval_subject` | typo = service starts | test_the_managed_service_block_stops_typed | S18M25 |
| source-review deferral | `policy.approval.defer_execution_until_source_review` | typo = executes before review | test_the_source_review_deferral_stops_typed | S18M26 |
| the executor's mode reading | `subtask_executor._config_autonomy_mode` | non-string mode read as "no config" | test_the_executor_stops_typed_on_an_unvalidated_mode[non_string] | S18M27 |
| invocation mode reading | `ModelToolInvocation.reviews_every_change` | typo = not HITL | test_the_invocation_mode_reading_is_fail_closed | S18M28 |
| desktop interaction display | `application.run._interaction` | typo shown as a configured mode | test_the_desktop_interaction_shows_unknown_never_a_mode | S18M29 |
| the executor stops typed before B3 | `_execute_tool_subtask` early mode check | AutonomyModeError escapes the executor | test_the_executor_stops_typed_on_an_unvalidated_mode | S18M30 |

## Results (mutrun v2, MEASURED)

- c1 `20261019_193341_406320cf` at 89f1a24: 31 mutants, VALID (tree diff none, no stragglers), 175 s. 29 KILLED at L1;
  S18M08 and S18M09 SURVIVED - own test weakness: the subclass doubles were defined inside functions, so their qualname
  (`_shipped_subclass.<locals>.SearchTool`) failed the table lookup and the own-class / file checks were never reached.
  Fixed in its own commit c432cab (module-level `SearchTool` / `ASTTool` doubles + a guard test that their names ARE in the
  shipped table).
- c2 `20261019_193723_6a189503` at c432cab: S18M08, S18M09 re-run, VALID, both KILLED at L1 (8 s).
- Total: 31/31 KILLED at L1, 0 equivalent, 0 survivors. Every killer is the intended one in the table above, except where the
  campaign recorded an additional first failing test (all in L1).

| mutant | result | first killing test(s) (campaign log) |
|---|---|---|
| S18M01 | KILLED L1 | ...[declares-none] |
| S18M02 | KILLED L1 | test_the_catalogue_projects_only_plannable_tools, test_every_shipped_tool_declares_and_the_resolution_projects_it |
| S18M03 | KILLED L1 | test_an_undeclared_plugin_tool_is_refused_by_the_executor_in_every_mode |
| S18M04 | KILLED L1 | test_the_catalogue_projects_only_plannable_tools; R80 pin |
| S18M05 | KILLED L1 | test_plan_validation_refuses_a_registered_but_unplannable_tool[shell]; R80 pin |
| S18M06 | KILLED L1 | test_a_registry_listing_failure_in_a_real_enforce_run_admits_no_tool; R80 pin |
| S18M07 | KILLED L1 | test_nothing_but_a_shipped_declaration_is_plannable[self-declared-plugin] |
| S18M08 | KILLED L1 (c2) | ...[subclass-inheriting-the-declaration] |
| S18M09 | KILLED L1 (c2) | ...[subclass-named-like-a-shipped-tool] |
| S18M10 | KILLED L1 | ...[garbage-value] |
| S18M11 | KILLED L1 | the P1 shell pins (ModelInvocationRefused rejects the dropped code) |
| S18M12 | KILLED L1 | test_plan_validation_refuses_a_registered_but_unplannable_tool[validate_refactor]; R80 pin |
| S18M13 | KILLED L1 | test_a_listing_failure_fails_validation_closed, test_a_registry_that_cannot_be_listed_admits_no_tool |
| S18M14 | KILLED L1 | test_the_planner_is_shown_only_plannable_tools |
| S18M15 | KILLED L1 | R80 pin (repair-prompt transcript) |
| S18M16 | KILLED L1 | test_the_tools_own_wall_refuses_a_direct_authorize_too |
| S18M17 | KILLED L1 | test_a_planned_git_read_runs_in_the_supplied_owner_root_not_the_process_folder |
| S18M18 | KILLED L1 | test_the_catalogue_projects_only_plannable_tools, test_every_shipped_tool_declares_... |
| S18M19 | KILLED L1 | test_every_shipped_tool_declares_..., test_the_catalogue_projects_only_plannable_tools |
| S18M20 | KILLED L1 | test_the_invocation_mode_reading_is_fail_closed |
| S18M21 | KILLED L1 | test_the_desktop_interaction_shows_unknown_never_a_mode, test_the_reader_itself |
| S18M22 | KILLED L1 | test_the_pre_apply_approval_gate_stops_typed_and_applies_nothing |
| S18M23 | KILLED L1 | test_the_contract_escalation_stops_typed |
| S18M24 | KILLED L1 | test_runtime_command_approval_stops_typed |
| S18M25 | KILLED L1 | test_the_managed_service_block_stops_typed |
| S18M26 | KILLED L1 | test_the_source_review_deferral_stops_typed |
| S18M27 | KILLED L1 | test_the_executor_stops_typed_on_an_unvalidated_mode[non_string] |
| S18M28 | KILLED L1 | test_the_invocation_mode_reading_is_fail_closed |
| S18M29 | KILLED L1 | test_the_desktop_interaction_shows_unknown_never_a_mode |
| S18M30 | KILLED L1 | test_the_executor_stops_typed_on_an_unvalidated_mode |
| S18M31 (revalidate_instances removed) | KILLED L1 | test_an_unvalidated_section_handed_to_appconfig_is_revalidated |

Structural tripwires with planted controls: test_the_tripwire_sees_a_planted_validation_without_the_plannable_names /
test_every_production_validation_passes_the_plannable_names; test_no_production_module_compares_the_mode_with_a_literal
(planted tree) / S16a's scan now requires zero literal sites; tests/architecture tool_registry table (a registry listing
outside subtask_executor fails).

Known residuals (honest): plugins are in-process code - the shipped-declaration rule stops a plugin from declaring itself
plannable through the tool contract, not hostile in-process code (it could patch the table). The Planner system prompt's
worked example B still names validate_refactor (changing it moves the kernel golden and the E13 prompt digest - owner
decision); the catalogue, validation and executor refuse it regardless.
