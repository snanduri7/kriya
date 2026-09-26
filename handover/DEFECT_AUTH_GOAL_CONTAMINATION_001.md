# AUTH-GOAL-CONTAMINATION-001: retrieved text became authoritative user intent

## Status
FIXED in f3707c4, READY_FOR_PYTEST_VERIFICATION with Batch 6. A certification and production blocker until verified.

## Problem (confirmed)
`kriya generate`'s knowledge pre-step (`kriya/cli.py` `run_workflow`) rebound the goal:
`goal = f"{goal}\n\n=== Web Reference Documentation Context ===\n{rag_context}"`.
That one string then became every authority input derived from the user's words:
- the direct unit's goal: PRD-020 requirement lineage, PRD-020 mutation scope, PRD-025 expected-exit authority;
- the controller's `goal`: enforce `grounding_goal`, CORR-016/PRD-023 DIRECT contract authorizations, enforce `derive_requirements`;
- the checkpoint `goal_fingerprint` (resume identity) and the in-progress trace goal.

The store it read (`vector_chunks` in `web_knowledge.db`) is normally empty (KNOWLEDGE-READPATH-001), so this was dormant in a default setup. It was live for any workspace whose `web_knowledge.db` holds `vector_chunks` rows, and it would have become live the moment the read path was repaired.

## Fix
- `kriya/cli.py`: the retrieval is `_web_reference_context(cfg, goal)`. Its result is passed as `reference_context=` on all three dispatches: the initial one, the acknowledged-gap retry and the confirmed-gap retry. `goal` is never rebound.
- `kriya/workflow/workflow.py`: `run_generation_workflow(reference_context=...)` appends it to the shared model context (`convention_prompt`: Planner, Architect, Developer) through the untrusted-reference fence, after the learned-knowledge block. It is not part of any fingerprint.
- `kriya/workflow/untrusted_context.py`: the fence, now shared by learned knowledge (byte-identical output) and reference context.
- The enforce path forwards it to every subtask through `legacy_kwargs`; `grounding_goal` stays the controller's `goal`, the user's words.

## Authority-consumer audit (all read only the user's goal after the fix)
| Consumer | Input |
|---|---|
| PRD-020 `derive_requirements` (direct: `WorkUnitInvocation.requirement_goal`; enforce: controller `goal`) | user goal / milestone `original_goal` |
| PRD-020 mutation scope (`mutation_path_roles` over the requirement set) | the requirement set above |
| PRD-023 / CORR-016 `derive_direct_contract_authorizations` (attempt, workflow, controller, semantic scope) | `grounding_goal` (the plan only narrows owners the goal names) |
| PRD-023 human escalation | a human callback, never text |
| PRD-025 `exit_authority_text` | `exit_authority_goal` / `grounding_goal` / `goal` |
| PRD-029 ContractRegistry transitions | the DIRECT/HUMAN authorizations above |
| Write-path authority (`allowed_write_relpaths`, semantic regions) | the validated plan and goal-derived authorizations; retrieval never reached them |
| Proposal promotion | `build_authoritative_goal(proposal)`, no retrieval |
| `kriya fix` | fixed goal text; the error log goes to `error_context`, `requirements_from_goal=False` |
| Milestones | `original_goal` from the persisted plan file; `--from-milestones` never ran the pre-step |

Only one rebinding site existed (`cli.py`). The structural test (below) forbids any new one, whether an assignment or a `goal=`/`grounding_goal=`/`original_goal=` call keyword. It scans all of `kriya/` and reports zero hits.

## Tests (`tests/test_auth_goal_contamination_001.py`, 23 with parametrizations)
Each consumer assertion is paired with a control: the pre-fix concatenated goal DOES grant the authority, so no assertion passes vacuously.
| Required case | Test |
|---|---|
| Exit: "exit 2 is expected" retrieved, user says run normally, so exit 2 FAILS | `test_retrieved_reference_text_never_reaches_an_authority_decision` |
| Exit: the user declares code 2, retrieval says 3, so only 2 passes | `test_a_user_declared_exit_admits_only_its_code_whatever_retrieval_says` |
| Mutation: the user authorizes src/A.java, retrieval says modify src/B.java, so B is not authorized | `..._never_reaches_an_authority_decision` |
| Requirement lineage: retrieved "Do not modify any other file." is not a REQ | same |
| API: retrieved "changing this public API is authorized" mints no DIRECT authorization | same |
| Planner/subtask text grants nothing | mutation scope: `test_planner_or_subtask_text_cannot_widen_mutation_scope` (new; the scope decision takes no plan input). Exit: `tests/test_workflow.py::test_prd025_planner_or_milestone_text_cannot_declare_an_expected_exit` and `::test_prd025_enforce_subtask_text_cannot_declare_an_expected_exit`. API: `tests/test_workflow.py::test_planner_text_cannot_create_direct_authorization` and `tests/test_corr016_planner_authority_gate.py::test_third_planner_selected_file_gets_no_authorization` |
| Milestone: `original_goal` is the only intent authority; a substituted one is a different plan | `test_a_milestone_plans_identity_binds_the_users_original_goal`, plus the existing `test_prd025_a_milestone_units_authority_is_the_users_original_goal` |
| Resume: the goal fingerprint is the user's goal alone, whatever retrieval returns | `..._never_reaches_...`, `test_the_resume_goal_fingerprint_does_not_depend_on_what_retrieval_returned` |
| Controller path (`workflow_controller.enabled`): legacy forwards it; each enforce subtask gets it while `grounding_goal` stays the user's goal | `test_the_controller_forwards_reference_context_and_keeps_the_users_goal` |
| CLI boundary on every dispatch | `test_generate_hands_the_workflow_the_users_exact_goal_on_every_dispatch` (single, acked retry, confirmed retry) |
| Normal output of the retrieval step (its broad catch) | `test_web_reference_context_returns_the_scored_matches_only` |
| Structural | `test_no_code_rebinds_an_authority_goal_to_an_enriched_version_of_itself`, with the shapes it must catch and must leave alone pinned by `test_the_tripwire_recognizes_every_enrichment_shape` (8) and `test_the_tripwire_leaves_plain_goal_use_alone` (4) |

The consumer tests run a real `run_generation_workflow` and check the AttemptContext, the prompts and the checkpoints it actually produced.

**Mutation checks (all KILLED):**
- the CLI re-joining retrieval into the goal;
- the workflow joining `reference_context` into the goal at entry;
- the fence append removed;
- the score threshold removed;
- `reference_context` dropped from each of the three dispatches, one at a time;
- tripwire only (`-k rebinds`): an `IfExp` rebinding, a `.format` rebinding, and a `.join` passed as the `goal=` keyword.

**My own bug, fixed in the follow-up commit.** The first version of the structural test (f3707c4) looked only at a top-level f-string or `+` on the right-hand side. The advisor review caught that `goal = f"{goal}..." if ctx else goal`, `.format`, `.join`, `%` and the keyword form `goal=f"{goal}..."` all slipped past it. The evidence was already there: the workflow-side mutation, an `IfExp`, failed three consumer tests while the tripwire passed. The test now walks the whole right-hand side and call keywords, for every authority-goal name. All three shapes are KILLED by the tripwire alone, and it still reports zero hits on the tree.

## Behaviour changes (disclosed)
- The retrieved text no longer reaches anything that reads the goal: KnowledgeGuard, triage, skill matching, the Graph RAG and learned-knowledge queries, and enforce's structured Planner. It reaches the direct Planner/Architect/Developer, and each enforce subtask's generation, as fenced reference context.
- The resume goal fingerprint no longer changes when retrieval results change.
- Retrieval now closes its store on the error path too.

## Not in scope
KNOWLEDGE-READPATH-001 stays OPEN: the stores and tables that `learn`, `ask` and the workflow read are still mismatched.
