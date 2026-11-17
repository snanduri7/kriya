# 08 - Stage documents (derivative views)

> **Baseline note (final reconciliation, 2026-11-17).** This document describes the AS-IS at the closure baseline `3eded93`, before the
> implementation slices S0 to S8. It is kept unchanged as the evidence the contracts were derived from. The owners and behaviours the slices
> changed are stated per contract and per gap in `13-FINAL-RECONCILIATION.md` (sections 2 and 3); where a row here differs from `13` or from
> the code at the certified revision, the code and `13` are authoritative.

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`. **A stage document defines nothing.**
Every fact has its home in one of the six maps, the gap ledger, the audit (`06`) or the required contracts (`07`);
a cell here names the row that holds it and adds only the few words needed to read the row in this stage's context.
AS-IS and REQUIRED are never blended: fields 1 to 19 are AS-IS; field 20 is the required contract (`PROPOSED`) and
field 21 its proof. `N/A - reason` marks a field that does not apply.

The 21 fields: (1) purpose; (2) entry conditions; (3) inputs; (4) owner; (5) decisions; (6) forbidden decisions;
(7) state; (8) model involvement; (9) human involvement; (10) mutation; (11) outputs; (12) failure classes;
(13) UNKNOWN; (14) retry / recovery; (15) checkpoint / resume; (16) security / authority boundary; (17) evidence /
audit; (18) bypasses; (19) AS-IS invariants; (20) required contract / delta; (21) acceptance proof / negative
controls.

Stage order as a run meets them: goal intake, semantic contract, workspace, knowledge, planning, plan validation,
subtask selection, context, model runtime, generation, candidate, gates, verifier identification, verifier
authority, attribution, retry, recovery, review, apply, replay, final verification, terminal, evidence, checkpoint.

## STAGE-GOAL-INTAKE

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Take the user's request as the run's goal and give it an identity |
| 2 | Entry conditions | A mutating entry (CLI `generate` / `fix` / milestones, desktop launch) under a run lease (`begin_mutating_run`) |
| 3 | Inputs | The goal text; optionally an operator requirements contract |
| 4 | Owner | The operator's input, carried verbatim; identity by `requirements.py::goal_identity` (`AUTH-A4-01`) |
| 5 | Decisions | The goal digest; the requirement set (`AUTH-A4-02`); the execution shape (`AUTH-A4-04`) |
| 6 | Forbidden decisions | Rewriting the goal; adding model or retrieved text to it (AUTH-GOAL-CONTAMINATION-001) |
| 7 | State | Goal hash on the RunRecord; `RequirementSet.goal_digest` |
| 8 | Model involvement | None |
| 9 | Human involvement | Writes the goal |
| 10 | Mutation | The content-addressed requirement contract (`MUT-A4-09` for the sealed run contract) |
| 11 | Outputs | Goal, digest, requirement set |
| 12 | Failure classes | `FR-A4-01` (contract unreadable or bound to another goal), `FR-A4-18` (unknown execution mode) |
| 13 | UNKNOWN | An empty goal derives zero requirements; ambiguity is not representable (`AUTH-A4-03`) |
| 14 | Retry / recovery | None |
| 15 | Checkpoint / resume | A direct resume is judged by the goal fingerprint; a contract of another goal is refused |
| 16 | Security / authority boundary | Only the user's words are goal authority; an enforce unit's `goal` argument also carries Planner text (`BYP-A4-12`) |
| 17 | Evidence / audit | Goal hash; the enforce trace row carries the goal (redacted) |
| 18 | Bypasses | `BYP-A4-12`, `BYP-A4-13` (`kriya fix`: no requirement set; INT-05) |
| 19 | AS-IS invariants | Requirements are traceable to the user's sentences |
| 20 | Required contract / delta | C-01 (semantic state per requirement; ambiguity outcome); C-02 clause for `kriya fix` (INT-05) |
| 21 | Acceptance proof / negative controls | C-01 and C-02 tables in `07` |

## STAGE-SEMANTIC-CONTRACT

| # | Field | Content |
|---|---|---|
| 1 | Purpose | State, before any model call, what each requirement is and how it can be verified |
| 2 | Entry conditions | A requirement set exists |
| 3 | Inputs | The requirement set; operator artifacts (acceptance file, approved examples) |
| 4 | Owner | `contract_compilation.py::compile_verification_contract`, sealed by `seal_verification_contract` (`AUTH-A2-02`) |
| 5 | Decisions | Claim kinds, strength, closers, statement status (`AUTH-A2-02`, `AUTH-A2-04`); whether an unverifiable goal may run (`AUTH-A2-03`) |
| 6 | Forbidden decisions | A closer chosen after generation; any model input |
| 7 | State | `kriya.verification_contract/1`, content-addressed; digest in `ControlState` and the resume identities (`MUT-A2-01`) |
| 8 | Model involvement | None |
| 9 | Human involvement | Operator artifacts only |
| 10 | Mutation | `MUT-A2-01` |
| 11 | Outputs | The sealed contract; authority requests |
| 12 | Failure classes | `FR-A4-04` (goal not admissible under a blocking policy), `FR-A4-05` (baseline authority unavailable; no mutation required) |
| 13 | UNKNOWN | Product semantics of a general statement: no owner (`AUTH-A2-05`); under `record` an unverifiable claim proceeds |
| 14 | Retry / recovery | None |
| 15 | Checkpoint / resume | Digest drift refuses resume |
| 16 | Security / authority boundary | The contract is the only pre-generation authority for verification; it holds no behaviour |
| 17 | Evidence / audit | The sealed file; its digest on verdicts and approvals |
| 18 | Bypasses | `BYP-A2-04`, `BYP-A2-05` |
| 19 | AS-IS invariants | Verifiability is decided before generation |
| 20 | Required contract / delta | C-01 |
| 21 | Acceptance proof / negative controls | C-01 table |

## STAGE-WORKSPACE

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Establish which workspace this is, what belongs to it, where a candidate lives, what may be written and read, and which configuration is effective |
| 2 | Entry conditions | Every run, before any model call; not a step in time but the base of every other stage |
| 3 | Inputs | The filesystem, Git, the configuration layers |
| 4 | Owner | `context/workspace_identity.py` (`AUTH-A5-01`), `repository/bound_tree.py` (`AUTH-A5-02`), `workflow/worktree.py` (`AUTH-A5-03`), `policy/filesystem.py` (`AUTH-A5-04`, `AUTH-A5-05`), `config/authority.py` (`AUTH-A5-10`) |
| 5 | Decisions | Root class and admission (`SM-A5-01`); write and read permission per path; field classification and approval (`SM-A5-03`); tool governance (`AUTH-A5-06`); MCP invocation and capability (`AUTH-A5-07`) |
| 6 | Forbidden decisions | Inferring identity from a name; a write outside the root, to a control path, a credential path or the goal file |
| 7 | State | Derived identity; control state under the workspace; approvals outside it (`AUTH-A5-08`); stores at `paths.memory` / `paths.skills` (`AUTH-A5-09`, `SM-A5-04`) |
| 8 | Model involvement | Candidate generator of tool calls under enforce (`SM-A5-02`) |
| 9 | Human involvement | Chooses the folder; approves protected configuration and MCP tool identities |
| 10 | Mutation | `MUT-A5-01..17` |
| 11 | Outputs | The candidate sandbox; the effective configuration |
| 12 | Failure classes | `FR-A5-01..19`; `FR-A4-12` (the plan sandbox or the skills run shadow cannot be created) |
| 13 | UNKNOWN | Root class unknown is refused; **the owner of a derived store is not represented** (`FR-A5` UNKNOWN table) |
| 14 | Retry / recovery | None for admission; tools are never retried |
| 15 | Checkpoint / resume | A sandbox is its own authority root (`AUTH-A5-01`); audit 3.4 |
| 16 | Security / authority boundary | The write gate, the read gate, SEC-009, TOOL-002, TOOL-003 |
| 17 | Evidence / audit | Ownership metadata on control documents; `PolicyDecisionRecord` |
| 18 | Bypasses | `BYP-A5-01..16`, `BYP-A6-18` |
| 19 | AS-IS invariants | Every workspace-scoped authority of one repository is keyed by one root |
| 20 | Required contract / delta | C-05 (project state), C-04 (MCP, skill writers, path class), C-08 (the budget's class) |
| 21 | Acceptance proof / negative controls | C-04, C-05, C-08 tables |

## STAGE-KNOWLEDGE

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Decide whether the run may proceed although the model may not know a named library, and supply skill text |
| 2 | Entry conditions | Stage 0 of every unit; once before structured planning under enforce |
| 3 | Inputs | The goal (or a unit's composed goal), the skills directory, the release cache |
| 4 | Owner | `tools/knowledge.py::KnowledgeGuard.check_goal` (`AUTH-A4-05`); skills by `SkillEngine` (`AUTH-A4-07`); lookup authorization (`AUTH-A4-06`) |
| 5 | Decisions | Knowledge gap or not (`SM-A4-03`); which skills apply (`AUTH-A4-08`); whether a query leaves the machine |
| 6 | Forbidden decisions | Sending goal or code text in a query (only extracted bare terms) |
| 7 | State | The release cache (`MUT-A4-05`); skill folders, provenance, conflicts (`SM-A4-04`); nothing about the gate's decision |
| 8 | Model involvement | SkillGapAgent extracts rules from fetched or supplied text |
| 9 | Human involvement | Confirms a gap (CLI); approves each query; reviews found references. The desktop cannot confirm (`BYP-A5-11`) |
| 10 | Mutation | `MUT-A4-01..05`, `MUT-A4-11` (scaffold), `MUT-A5-08` (skill writers) |
| 11 | Outputs | Proceed or `knowledge_gap`; the conventions block (`AUTH-A4-09`) |
| 12 | Failure classes | `FR-A4-02`, `FR-A4-03`, `FR-A4-13`, `FR-A4-14`, `FR-A4-15` |
| 13 | UNKNOWN | A folder name is coverage; an unknown release date is not a gap; a skill that fails to load is silently absent (INT-07) |
| 14 | Retry / recovery | The person confirms and reruns |
| 15 | Checkpoint / resume | A direct resume sets the gate confirmed from the checkpoint's existence (`BYP-A4-08`, INT-06) |
| 16 | Security / authority boundary | Egress (`policy/egress.py`); skill trust by location |
| 17 | Evidence / audit | Active-skill manifest; no event per outbound query (ledger A6) |
| 18 | Bypasses | `BYP-A4-02..09`, `BYP-A4-19`, `BYP-A5-02`, `BYP-A6-11` |
| 19 | AS-IS invariants | A query carries only a public technology term |
| 20 | Required contract / delta | C-06, C-05, C-04, C-10 |
| 21 | Acceptance proof / negative controls | C-06 table; INT-06, INT-07 clauses |

## STAGE-PLANNING

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Turn the goal into a plan a unit or a set of units can execute |
| 2 | Entry conditions | Admission passed; knowledge gate passed |
| 3 | Inputs | Goal, requirement ids, repository context, skill constraints, the fenced learned reference |
| 4 | Owner | The Planner agent as proposer; acceptance by `AUTH-A4-10` (enforce) or `AUTH-A4-11` (direct) |
| 5 | Decisions | None by the model; which Planner statements bind the Developer (`AUTH-A4-12`) |
| 6 | Forbidden decisions | A plan naming a path outside path authority (never repaired, `FR-A4-08`) |
| 7 | State | The `plan` stage checkpoint (`MUT-A4-06`); the approved-plan document (`MUT-A4-07`); decision-ledger records (`MUT-A4-08`) |
| 8 | Model involvement | Proposer; its text is evidence |
| 9 | Human involvement | None at this stage |
| 10 | Mutation | `MUT-A4-06..08` |
| 11 | Outputs | A plan (structured under enforce, prose plus a block direct) |
| 12 | Failure classes | `FR-A4-06..11`, `FR-A4-16` |
| 13 | UNKNOWN | Project view or localization unavailable: recorded, planning continues (`FR-A4-16`) |
| 14 | Retry / recovery | Up to two repairs (`SM-A4-01`, `SM-A4-02`) |
| 15 | Checkpoint / resume | Direct: the saved plan is reused and classified again; enforce: a fresh Planner call each invocation (`GAP-A4-06`) |
| 16 | Security / authority boundary | Planner text never becomes goal text, a requirement verdict or file content |
| 17 | Evidence / audit | Planner outcome metrics; decision-ledger records |
| 18 | Bypasses | `BYP-A4-11` (a plan revision during execution), `BYP-A4-15` (shadow mode: observational) |
| 19 | AS-IS invariants | A security rejection is never an opportunity to resubmit |
| 20 | Required contract / delta | C-02 truth (1); C-07 / INT-02 (executed-plan identity); C-01 (decisions checked against clauses) |
| 21 | Acceptance proof / negative controls | C-02 convergence test; INT-02 clause |

## STAGE-PLAN-VALIDATION

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Decide whether a plan may execute |
| 2 | Entry conditions | A Planner answer |
| 3 | Inputs | The plan, known requirement ids, the workspace's files, the runtime-required reading (`AUTH-A4-13`) |
| 4 | Owner | Enforce: the planning loop with `validate_plan` (`AUTH-A4-10`). Direct: `classify_plan_completeness` (`AUTH-A4-11`) |
| 5 | Decisions | Valid or repair or stop; create versus modify (`AUTH-A4-16`) |
| 6 | Forbidden decisions | Accepting an unknown tool or an absent modify target |
| 7 | State | `ControlState.current_plan_hash` (`SM-A4-01`) |
| 8 | Model involvement | Repair answers |
| 9 | Human involvement | None |
| 10 | Mutation | `MUT-A4-10` (the plan revised during execution) |
| 11 | Outputs | The validated plan and its hash |
| 12 | Failure classes | `FR-A4-07..10`, `FR-A4-17` |
| 13 | UNKNOWN | A runtime requirement the patterns miss reads "not required" (`GAP-A4-05`) |
| 14 | Retry / recovery | Repairs; three deterministic revision sites during execution |
| 15 | Checkpoint / resume | The plan hash is compared on resume |
| 16 | Security / authority boundary | `planned_files` become the write boundary of a unit |
| 17 | Evidence / audit | `structured_plan_*` records |
| 18 | Bypasses | `BYP-A4-11`; the direct shape validates no structure |
| 19 | AS-IS invariants | A unit may write only its planned files |
| 20 | Required contract / delta | C-02 truth (1); C-01 (unknown keeps runtime verification) |
| 21 | Acceptance proof / negative controls | C-02 table |

## STAGE-SUBTASK-SELECTION

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Choose the next unit and track unit status |
| 2 | Entry conditions | A valid plan |
| 3 | Inputs | Plan order, unit states |
| 4 | Owner | Two owners: `plan_executor.py::execute_plan` and `enforce_loop.py::EnforceLoop` (`AUTH-A4-14`) |
| 5 | Decisions | Next unit; completion; which unit answers for requirements (`AUTH-A4-15`) |
| 6 | Forbidden decisions | Running a unit after a failed one (direct); committing a plan with a stage not completed (enforce) |
| 7 | State | RunRecord `active_work_unit`, `work_unit_states`; `ControlState.subtask_states` (`MUT-A1-13`) |
| 8 | Model involvement | None |
| 9 | Human involvement | None |
| 10 | Mutation | `MUT-A1-13`, `MUT-A1-15..17` |
| 11 | Outputs | Unit invocations (`BYP-A1-01`, `BYP-A1-02`) |
| 12 | Failure classes | `FR-A1-17..22` |
| 13 | UNKNOWN | A stop that is not a scope conflict has no enforce-level decision (`FR-A1-22`) |
| 14 | Retry / recovery | `SM-A1-04` |
| 15 | Checkpoint / resume | `AUTH-A1-10`, `SM-A1-03` |
| 16 | Security / authority boundary | A TOOL subtask is governed by `AUTH-A5-06` |
| 17 | Evidence / audit | Unit scopes in the evidence store |
| 18 | Bypasses | `BYP-A1-01..03`, `BYP-A1-14` |
| 19 | AS-IS invariants | Partial completion is never SUCCESS (RunRecord proofs) |
| 20 | Required contract / delta | C-02 (one status meaning), C-07 (durable unit state), C-08 (work ceiling) |
| 21 | Acceptance proof / negative controls | C-02, C-07 tables |

## STAGE-CONTEXT

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Assemble what a model request shows |
| 2 | Entry conditions | A unit's analysis |
| 3 | Inputs | Skills, graph context, learned reference, plan, design, retry projections |
| 4 | Owner | `stage_analysis.py` (conventions, graph, fenced reference: `AUTH-A4-09`); `context_budget.py` (fitting: `AUTH-A6-05`) |
| 5 | Decisions | Which optional sections fit |
| 6 | Forbidden decisions | Trimming mandatory text; placing learned reference outside the fence |
| 7 | State | None persisted; `retrieved_chunks` in the trace row (`MUT-A5-13`) |
| 8 | Model involvement | None |
| 9 | Human involvement | None |
| 10 | Mutation | None |
| 11 | Outputs | `convention_prompt`, code context, the fitted request |
| 12 | Failure classes | `FR-A6-08` |
| 13 | UNKNOWN | The origin of a piece of text is not represented (`FR-A6` UNKNOWN table) |
| 14 | Retry / recovery | Sections rebuilt smaller, at most three times |
| 15 | Checkpoint / resume | Rebuilt |
| 16 | Security / authority boundary | The fence (`untrusted_context.py`) |
| 17 | Evidence / audit | `prompt.sections`, `context.request_fit` |
| 18 | Bypasses | `BYP-A4-06` (an unprovenanced rule rendered as trusted), `BYP-A6-05` |
| 19 | AS-IS invariants | A request never exceeds the budget window |
| 20 | Required contract / delta | C-10 (typed trust classes), C-05 (a foreign index never reaches context) |
| 21 | Acceptance proof / negative controls | C-10 table |

## STAGE-MODEL-RUNTIME

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Send one request to the right model with proven settings, within budget and deadline |
| 2 | Entry conditions | A role or Developer choke point made a call |
| 3 | Inputs | Model name and overrides, the configuration's bindings |
| 4 | Owner | `core/llm.py::LLMClient` over the runtime port (`AUTH-A6-01..05`, `AUTH-A6-10`) |
| 5 | Decisions | Binding, adapter, identity, window state, admission, deadline (`SM-A6-01`); fallback compatibility (`AUTH-A6-07`, `SM-A6-04`); qualification where it decides (`AUTH-A6-06`, `SM-A6-02`) |
| 6 | Forbidden decisions | A model selecting a model; a default adapter for an unknown name |
| 7 | State | Fingerprint files, RunRecord fingerprint ids (`MUT-A6-01`, `MUT-A6-02`); qualification records (`MUT-A6-03`) |
| 8 | Model involvement | The call itself |
| 9 | Human involvement | Operator: configuration, qualification |
| 10 | Mutation | `MUT-A6-01..07` |
| 11 | Outputs | `CompletionResult` |
| 12 | Failure classes | `FR-A6-01..15` |
| 13 | UNKNOWN | The binding of a model name; whether the primary is qualified (both fail open) |
| 14 | Retry / recovery | Two in-call retries; role chain; Developer fallback |
| 15 | Checkpoint / resume | Resume fingerprint `model_runtime`; fallback state not persisted |
| 16 | Security / authority boundary | Egress; owned request fields; provider contract |
| 17 | Evidence / audit | `model.request`, `model.result`, `model.transition`, role metrics (`AUTH-A6-08`) |
| 18 | Bypasses | `BYP-A6-01..04`, `BYP-A1-08` |
| 19 | AS-IS invariants | A setting Kriya budgets against is proven to reach the provider or the call fails closed |
| 20 | Required contract / delta | C-09 |
| 21 | Acceptance proof / negative controls | C-09 table |

## STAGE-GENERATION

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Obtain the Developer's answer for the attempt |
| 2 | Entry conditions | The retry policy admitted an attempt and its mode (`AUTH-A1-05`) |
| 3 | Inputs | The request of `AUTH-A6-09`; on a retry the error context (`MUT-A6-13`) |
| 4 | Owner | `agents/agent.py::DeveloperAgent`, entered through `generation_service.py::_run_developer_generation` |
| 5 | Decisions | None about authority: the answer is a proposal |
| 6 | Forbidden decisions | Treating model text as a file, a verdict or a configuration value before the gates |
| 7 | State | Memory |
| 8 | Model involvement | Candidate generator |
| 9 | Human involvement | None |
| 10 | Mutation | None (the candidate stage writes) |
| 11 | Outputs | Parsed file operations |
| 12 | Failure classes | `FR-A1-02` (delivery failures), `FR-A1-04`, `FR-A6-12`, `FR-A6-20`, `FR-A6-21` |
| 13 | UNKNOWN | An unfit or truncated answer is refused, never used |
| 14 | Retry / recovery | STAGE-RETRY |
| 15 | Checkpoint / resume | Not checkpointed inside the loop (`AUTH-A1-09`) |
| 16 | Security / authority boundary | The trust classes of `AUTH-A6-09` |
| 17 | Evidence / audit | `model.request` blob, `developer.parse`, `retry.delta` |
| 18 | Bypasses | `BYP-A6-05..11`, `BYP-A1-07` |
| 19 | AS-IS invariants | Planned Implementation Strategy is approach, not requirement |
| 20 | Required contract / delta | C-10, C-06, C-01 |
| 21 | Acceptance proof / negative controls | C-10 table |

## STAGE-CANDIDATE

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Turn the answer into an exact set of staged bytes |
| 2 | Entry conditions | A parsed answer |
| 3 | Inputs | File operations; the unit's write scope |
| 4 | Owner | `candidate_transaction.py::CandidateTransaction` through the write gate (`AUTH-A3-01`, `AUTH-A5-04`) |
| 5 | Decisions | Accept or refuse the whole attempt's batch |
| 6 | Forbidden decisions | A partial stage; a write the gate denies |
| 7 | State | Sandbox files; candidate digests in memory (`SM-A3-01`) |
| 8 | Model involvement | None |
| 9 | Human involvement | None |
| 10 | Mutation | `MUT-A3-01`, `MUT-A3-02`, `MUT-A5-03..05` |
| 11 | Outputs | The candidate and its digests |
| 12 | Failure classes | `FR-A1-02`, `FR-A1-08`, `FR-A1-09`, `FR-A1-16`, `FR-A5-06`, `FR-A5-07` |
| 13 | UNKNOWN | A batch that cannot be materialized has no binding: refused |
| 14 | Retry / recovery | A refusal is a delivery failure: it inherits the family |
| 15 | Checkpoint / resume | Rebuilt from `final_files` through the same transaction |
| 16 | Security / authority boundary | The write gate |
| 17 | Evidence / audit | `candidate.change` |
| 18 | Bypasses | `BYP-A3-10`, `BYP-A5-01`, `BYP-A5-02` |
| 19 | AS-IS invariants | The first refusal refuses the whole attempt |
| 20 | Required contract / delta | C-04 (path class), C-03 (the batch is the approval subject) |
| 21 | Acceptance proof / negative controls | C-04 table |

## STAGE-GATES

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Decide deterministically whether the candidate may be accepted |
| 2 | Entry conditions | A staged candidate; in held mode, after the source approval (`AUTH-A3-06`) |
| 3 | Inputs | The candidate; the validators |
| 4 | Owner | `gate_orchestrator.py`; terminal regression (`AUTH-A3-07`); enforce terminal gates (`AUTH-A3-08`) |
| 5 | Decisions | Pass, fail, environment stop |
| 6 | Forbidden decisions | A model verdict as a gate result; a pass from a gate that did not execute |
| 7 | State | `gate_outcomes` (memory, trace row, evidence) |
| 8 | Model involvement | Run verifier and spec compliance as advisory judges where configured |
| 9 | Human involvement | Runtime command approval |
| 10 | Mutation | Gate-side candidate writers (`MUT-A3-02`); environment preparation |
| 11 | Outputs | Gate outcomes; a `Failure` |
| 12 | Failure classes | `FR-A1-01`, `FR-A1-07`, `FR-A1-10`, `FR-A5-18`, `FR-A5-19` |
| 13 | UNKNOWN | Whether every environment refusal reaches a typed verdict: not established (R-13) |
| 14 | Retry / recovery | STAGE-RETRY |
| 15 | Checkpoint / resume | `candidate_gates_passed` written only after the held half ran |
| 16 | Security / authority boundary | Containment (TOOL-003 and the gate containment) |
| 17 | Evidence / audit | `gate.result` (purpose on four entries only, `BYP-A6-15`) |
| 18 | Bypasses | `BYP-A3-06`, `BYP-A3-09`, `BYP-A6-08` |
| 19 | AS-IS invariants | Deterministic gate outcomes are the only authority on whether an attempt passed |
| 20 | Required contract / delta | C-13 (the blocker), C-11 (purpose vocabulary) |
| 21 | Acceptance proof / negative controls | C-13: scripted refusal at every launch site |

## STAGE-VERIFIER-ID

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Identify which executed tests the run itself wrote |
| 2 | Entry conditions | A complete structured test report |
| 3 | Inputs | Executed identities, the structural parse, the write journal |
| 4 | Owner | `verifier_semantic.py::derive_executed_verifiers` (`AUTH-A2-06`) |
| 5 | Decisions | Subject or not |
| 6 | Forbidden decisions | Using a file name |
| 7 | State | None |
| 8 | Model involvement | None |
| 9 | Human involvement | None |
| 10 | Mutation | None |
| 11 | Outputs | Verifier subjects |
| 12 | Failure classes | `FR-A2-01..11` (the rows about a run-authored test) |
| 13 | UNKNOWN | An unmapped identity reads "not run-authored" (`GAP-A2-02`) |
| 14 | Retry / recovery | N/A - identification only |
| 15 | Checkpoint / resume | N/A - derived per gate |
| 16 | Security / authority boundary | A subject becomes write-denied after a valid decision |
| 17 | Evidence / audit | The semantic subject fingerprint |
| 18 | Bypasses | `BYP-A2-01..03` |
| 19 | AS-IS invariants | Identification never trusts a name |
| 20 | Required contract / delta | C-01 (unmapped is held) |
| 21 | Acceptance proof / negative controls | The `src/`-layout probe as a test |

## STAGE-VERIFIER-AUTH

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Give or refuse semantic authority to a generated verifier's expectation |
| 2 | Entry conditions | A subject exists and a typed channel to a person |
| 3 | Inputs | The subject; plan criteria and invariants by id and text |
| 4 | Owner | A person through `policy/approval.py::ask_verifier_semantic`; state in `VerifierSemanticLedger` (`AUTH-A2-07`, `AUTH-A2-08`) |
| 5 | Decisions | Valid, invalid, cannot decide |
| 6 | Forbidden decisions | An auto-approving caller's answer; reuse for other bytes |
| 7 | State | The ledger, memory only (`SM-A2-01`, `MUT-A2-02`, `MUT-A2-03`) |
| 8 | Model involvement | One regeneration after an invalid decision |
| 9 | Human involvement | The decision |
| 10 | Mutation | `MUT-A2-02..05` |
| 11 | Outputs | Write-denial of accepted bytes; one regeneration; or needs review |
| 12 | Failure classes | `FR-A2-01..11`, `FR-A1-13` |
| 13 | UNKNOWN | No decision, unattended: needs review |
| 14 | Retry / recovery | One regeneration per subject; replay (`AUTH-A2-13`) |
| 15 | Checkpoint / resume | Lost; the person can be asked again (`AUTH-A1-14`) |
| 16 | Security / authority boundary | Source approval is not semantic approval (`AUTH-A2-12`) |
| 17 | Evidence / audit | The review record |
| 18 | Bypasses | `BYP-A2-06..09`, `BYP-A1-11` |
| 19 | AS-IS invariants | A verifier's expectation has authority only by a bound human decision |
| 20 | Required contract / delta | C-01, C-07 (ledger persisted) |
| 21 | Acceptance proof / negative controls | The seven committed A2 tests; C-07 resume test |

## STAGE-ATTRIBUTION

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Decide which file a failure is about and whose defect it is |
| 2 | Entry conditions | A failed attempt |
| 3 | Inputs | The `Failure`, structural evidence, the plan |
| 4 | Owner | `attribution.py::attribute_failure` (`AUTH-A2-10`); kind by `failure.py::classify_failure_attribution` (`AUTH-A2-11`); production owner (`AUTH-A2-09`) |
| 5 | Decisions | The retry target; the repair owner |
| 6 | Forbidden decisions | A model's guess outranking a unique locator |
| 7 | State | `state.last_attribution` (`MUT-A2-06`) |
| 8 | Model involvement | A typed REDIRECT as a hint; a model tier when nothing structural exists |
| 9 | Human involvement | None |
| 10 | Mutation | `MUT-A2-06` |
| 11 | Outputs | Target files, kind |
| 12 | Failure classes | `FR-A1-09`, `FR-A1-18` |
| 13 | UNKNOWN | An unlisted failure type is `SOURCE_DEFECT` |
| 14 | Retry / recovery | Feeds the retry mode |
| 15 | Checkpoint / resume | N/A - recomputed |
| 16 | Security / authority boundary | A target outside the unit's scope is a scope conflict, never a write |
| 17 | Evidence / audit | `diagnosis` records; labels rewritten after the decision (R-11) |
| 18 | Bypasses | `BYP-A1-07` |
| 19 | AS-IS invariants | Structural evidence first |
| 20 | Required contract / delta | C-11 (labels by the deciding owner; INT-12) |
| 21 | Acceptance proof / negative controls | The label regressions of the R-11 registry rows |

## STAGE-RETRY

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Decide whether and how a unit tries again |
| 2 | Entry conditions | A failed attempt inside a unit invocation |
| 3 | Inputs | The failure, the open finding, the budgets, the progress vector |
| 4 | Owner | `retry_policy.py` (`AUTH-A1-03`, `AUTH-A1-05..07`), `open_findings.py` (`AUTH-A1-01`), `retry_strategy.py` (`AUTH-A1-04`) |
| 5 | Decisions | Finding replaced or kept; new family or not; charge; next mode or stop (`SM-A1-01`, `SM-A1-02`) |
| 6 | Forbidden decisions | Model text as evidence; a second grant path |
| 7 | State | `RetryBudgets`, `open_findings`, memory only |
| 8 | Model involvement | None in the decisions |
| 9 | Human involvement | None |
| 10 | Mutation | `MUT-A1-01..11` |
| 11 | Outputs | The next attempt's mode, or a typed stop |
| 12 | Failure classes | `FR-A1-01..16` |
| 13 | UNKNOWN | An unlisted failure type can replenish the allowance (`GAP-A1-07`) |
| 14 | Retry / recovery | This stage |
| 15 | Checkpoint / resume | Nothing persisted (`SM-A1-03`) |
| 16 | Security / authority boundary | Retry never widens write scope or authority |
| 17 | Evidence / audit | `retry.progress_vector`, `recovery.decision`; no record of grants and charges (`GAP-A1-03`) |
| 18 | Bypasses | `BYP-A1-04..10` |
| 19 | AS-IS invariants | One grant path inside a unit; an invocation's attempts are bounded |
| 20 | Required contract / delta | C-07 |
| 21 | Acceptance proof / negative controls | The run-10 scripted reproducer; the nine committed tests |

## STAGE-RECOVERY

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Repair across units when a unit's failure belongs to another owner |
| 2 | Entry conditions | Enforce; a scope conflict or a verification-only unit's no-change stop |
| 3 | Inputs | The conflict's artifacts, the plan's owners |
| 4 | Owner | `enforce_loop.py` with `recovery_plan.py` and `owner_recovery.py` (`AUTH-A1-11`, `AUTH-A1-14`) |
| 5 | Decisions | Which owners reopen; when the cycle is accepted (`SM-A1-04`) |
| 6 | Forbidden decisions | Reopening an owner with a completed TOOL dependent (`FR-A1-19`) |
| 7 | State | Loop locals (`MUT-A1-19`) |
| 8 | Model involvement | The reopened owner's Developer |
| 9 | Human involvement | One authorization of a verifier repair per run |
| 10 | Mutation | `MUT-A1-15..19` |
| 11 | Outputs | Re-invocations |
| 12 | Failure classes | `FR-A1-17..22` |
| 13 | UNKNOWN | No resolvable owner: the stage fails |
| 14 | Retry / recovery | Three cycles per stage; a run-wide convergence cap |
| 15 | Checkpoint / resume | All lost on a new process |
| 16 | Security / authority boundary | Each re-invocation passes the same gates |
| 17 | Evidence / audit | `plan_recovery_events` on the result |
| 18 | Bypasses | `BYP-A1-01`, `BYP-A1-11..14` |
| 19 | AS-IS invariants | Recovery is count-bounded |
| 20 | Required contract / delta | C-07, C-08, C-02 (repair after a terminal gap) |
| 21 | Acceptance proof / negative controls | C-07 table |

## STAGE-REVIEW

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Ask a person whether a change may be executed and applied |
| 2 | Entry conditions | A candidate; a trigger of `AUTH-A3-03` |
| 3 | Inputs | A diff rebuilt from the sandbox; the pre-approval review text |
| 4 | Owner | `stage_apply.py::approval_gate`; the decision by `policy/approval.py::resolve_approval` (`AUTH-A3-04`) |
| 5 | Decisions | Approved, declined, unavailable; held execution (`AUTH-A3-05`, `AUTH-A3-06`) |
| 6 | Forbidden decisions | Honouring a decision that could not be recorded |
| 7 | State | The launch record (desktop: digest of the presented diffs); an event (CLI) (`SM-A3-02`, `AUTH-A3-13`) |
| 8 | Model involvement | The Reviewer's advisory text (`AUTH-A3-12`) |
| 9 | Human involvement | The decision |
| 10 | Mutation | `MUT-A3-07` |
| 11 | Outputs | Continue or stop |
| 12 | Failure classes | `FR-A3-06`, `FR-A3-20`, `FR-A3-22`, `FR-A4-19` |
| 13 | UNKNOWN | No decision is never approval; an invalid `sensitive_paths` pattern is skipped (INT-09) |
| 14 | Retry / recovery | None |
| 15 | Checkpoint / resume | A decision is never restored; a resumed candidate is asked about again |
| 16 | Security / authority boundary | Execution is held until the review in human-in-the-loop mode |
| 17 | Evidence / audit | `approval.decision` |
| 18 | Bypasses | `BYP-A3-01..06`, `BYP-A3-11`, `BYP-A3-14`, `BYP-A3-16`, `BYP-A6-06` |
| 19 | AS-IS invariants | A stale approval never covers changed bytes during held execution |
| 20 | Required contract / delta | C-03 |
| 21 | Acceptance proof / negative controls | C-03 table |

## STAGE-APPLY

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Put the verified candidate into the real workspace |
| 2 | Entry conditions | Gates passed; approval where required; the binding holds (`AUTH-A3-02`) |
| 3 | Inputs | The verified batch |
| 4 | Owner | `terminal_commit.py::commit_terminal_candidate` (`AUTH-A3-14`) |
| 5 | Decisions | COMMITTED, UNCHANGED, UNCERTAIN (`SM-A3-03`) |
| 6 | Forbidden decisions | Committing a batch that differs from the verified one |
| 7 | State | Commit evidence under the workspace; the RunRecord commit cycle |
| 8 | Model involvement | None |
| 9 | Human involvement | `kriya recover` after an uncertain commit |
| 10 | Mutation | `MUT-A3-03..05` |
| 11 | Outputs | The workspace change |
| 12 | Failure classes | `FR-A3-16`, `FR-A3-21`, `FR-A6-18` |
| 13 | UNKNOWN | An unproven commit is UNCERTAIN and blocks later runs |
| 14 | Retry / recovery | Rollback; recovery command |
| 15 | Checkpoint / resume | The commit state is assessed before any new run |
| 16 | Security / authority boundary | The only writer of the real workspace for a candidate |
| 17 | Evidence / audit | Commit evidence (exact before and after bytes) |
| 18 | Bypasses | `BYP-A3-07` (closed), `BYP-A3-13`, `BYP-A6-18` (MCP), `BYP-A5-07` (skill writers) |
| 19 | AS-IS invariants | Fail closed: UNCERTAIN blocks until settled |
| 20 | Required contract / delta | C-03 (approved digest checked), C-04 |
| 21 | Acceptance proof / negative controls | C-03, C-04 tables |

## STAGE-REPLAY

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Re-verify earlier accepted work before integration, and re-run an accepted verifier after an owner repair |
| 2 | Entry conditions | Milestone plans before the integration unit; a refused verifier candidate with a replay handle |
| 3 | Inputs | Persisted verification commands; the replay handle |
| 4 | Owner | `milestones.py::replay_prior_milestone_report` (`AUTH-A3-15`); `verifier_replay.py` (`AUTH-A2-13`) |
| 5 | Decisions | Pass, fail, not evaluable, not executed |
| 6 | Forbidden decisions | Reading "did not run" as "no regression" |
| 7 | State | The milestone run-state sidecar (`MUT-A3-13`, `MUT-A3-14`) |
| 8 | Model involvement | None |
| 9 | Human involvement | Authorizes a verifier repair |
| 10 | Mutation | `MUT-A3-13`, `MUT-A3-14` |
| 11 | Outputs | The replay report |
| 12 | Failure classes | `FR-A3-17`, `FR-A3-18` |
| 13 | UNKNOWN | `not_evaluable` is recorded and not counted as a failure |
| 14 | Retry / recovery | None |
| 15 | Checkpoint / resume | Handles are memory only |
| 16 | Security / authority boundary | Replays run under the same containment as gates |
| 17 | Evidence / audit | The report on the plan result |
| 18 | Bypasses | Enforce has no replay phase (audit section 10, boundary 25) |
| 19 | AS-IS invariants | A replay that did not run is a typed terminal |
| 20 | Required contract / delta | C-02 (one meaning of "earlier work still verifies"), C-07 (handles persisted) |
| 21 | Acceptance proof / negative controls | C-02 convergence test |

## STAGE-FINAL-VERIFICATION

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Verify the original requirements against the whole change |
| 2 | Entry conditions | Unit acceptance (direct), the integration unit (milestone), the terminal gate (enforce) |
| 3 | Inputs | The obligation ledger; closers; gate results |
| 4 | Owner | `requirements.py` (`AUTH-A3-09`); enforce terminal gates (`AUTH-A3-08`) |
| 5 | Decisions | Requirement outcomes and truth; which requirements block |
| 6 | Forbidden decisions | A model verdict closing a requirement |
| 7 | State | The ledger; the `requirement.truth` event (`MUT-A3-08`) |
| 8 | Model involvement | Spec compliance may report "missing" |
| 9 | Human involvement | Operator acceptance of a requirement |
| 10 | Mutation | `MUT-A3-08`, `MUT-A3-10`, `MUT-A3-11` |
| 11 | Outputs | Requirement truth |
| 12 | Failure classes | `FR-A3-15`, `FR-A3-19` |
| 13 | UNKNOWN | Under the default policies UNKNOWN and UNVERIFIED do not block |
| 14 | Retry / recovery | None after an enforce terminal gate failure (`GAP-A3-02`) |
| 15 | Checkpoint / resume | The ledger snapshot is restored |
| 16 | Security / authority boundary | Requirement verdicts never come from model prose |
| 17 | Evidence / audit | `requirement.verdicts`, `requirement.truth` |
| 18 | Bypasses | `BYP-A3-08` |
| 19 | AS-IS invariants | A violated requirement always blocks |
| 20 | Required contract / delta | C-02, C-01 |
| 21 | Acceptance proof / negative controls | A3 probe P8 inverted |

## STAGE-TERMINAL

| # | Field | Content |
|---|---|---|
| 1 | Purpose | State the run's outcome |
| 2 | Entry conditions | The wrapped mutating function returned or raised |
| 3 | Inputs | The result payload; the RunRecord |
| 4 | Owner | Unit: `RunState.final_workflow_quality_passed` (`AUTH-A3-10`); plan: `AUTH-A3-11`; reconciliation: `control/run_coordinator.py` (audit 3.5) |
| 5 | Decisions | The status word; the RunRecord's terminal state (`SM-A3-04`, `SM-A3-05`) |
| 6 | Forbidden decisions | SUCCESS with an unverified work unit or an unsettled commit |
| 7 | State | The RunRecord; the result |
| 8 | Model involvement | The final review text (advisory under enforce; decides unit status direct and milestone, `GAP-A3-03`) |
| 9 | Human involvement | Reads the result |
| 10 | Mutation | `MUT-A3-12` |
| 11 | Outputs | The reconciled result; exit code |
| 12 | Failure classes | `FR-A3-16`, `FR-A6-18` |
| 13 | UNKNOWN | Requirement truth is not consumed (`GAP-A3-01`) |
| 14 | Retry / recovery | N/A - terminal |
| 15 | Checkpoint / resume | N/A - terminal |
| 16 | Security / authority boundary | A result claiming success against its record is rewritten |
| 17 | Evidence / audit | STAGE-EVIDENCE |
| 18 | Bypasses | `BYP-A3-12`, `BYP-A6-12` |
| 19 | AS-IS invariants | Partial completion is never SUCCESS |
| 20 | Required contract / delta | C-02 |
| 21 | Acceptance proof / negative controls | C-02 table |

## STAGE-EVIDENCE

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Record what happened |
| 2 | Entry conditions | Throughout the run; sealed at run exit |
| 3 | Inputs | Run state at each producer |
| 4 | Owner | Four writers (`AUTH-A6-11`); the desktop projections (`AUTH-A6-12`); build and certification identity (`AUTH-A6-13`) |
| 5 | Decisions | None |
| 6 | Forbidden decisions | Changing a request, a result or a retry decision (invariant I-2) |
| 7 | State | RunRecord, history row, evidence store, run log (`SM-A6-03`) |
| 8 | Model involvement | Model text recorded as content |
| 9 | Human involvement | Reader |
| 10 | Mutation | `MUT-A6-08..11`, `MUT-A3-12`, `MUT-A1-14` |
| 11 | Outputs | The records |
| 12 | Failure classes | `FR-A6-16..19` |
| 13 | UNKNOWN | Whether a record is the one the run wrote is not represented |
| 14 | Retry / recovery | N/A - records |
| 15 | Checkpoint / resume | A resumed run has a new id and a new store (INT-10) |
| 16 | Security / authority boundary | Redaction before storage; private file modes for the state directory |
| 17 | Evidence / audit | This stage |
| 18 | Bypasses | `BYP-A6-12..16` |
| 19 | AS-IS invariants | A record never decides |
| 20 | Required contract / delta | C-11 |
| 21 | Acceptance proof / negative controls | C-11 table |

## STAGE-CHECKPOINT

| # | Field | Content |
|---|---|---|
| 1 | Purpose | Let an interrupted run reuse verified work |
| 2 | Entry conditions | Reaching one of four stages (plan, design, candidate gates passed, developer success); each enforce subtask transition |
| 3 | Inputs | Stage artifacts; fingerprints |
| 4 | Owner | `workflow/checkpoint.py`, `resume_fingerprints.py` (`AUTH-A1-09`); `ControlState` (`AUTH-A1-10`) |
| 5 | Decisions | What a resume may reuse |
| 6 | Forbidden decisions | Reuse across a drifted fingerprint |
| 7 | State | Stage checkpoints; `ControlState` |
| 8 | Model involvement | None |
| 9 | Human involvement | The operator asks for the resume |
| 10 | Mutation | `MUT-A1-12`, `MUT-A1-13`, `MUT-A3-06`, `MUT-A4-06` |
| 11 | Outputs | A resume plan |
| 12 | Failure classes | `resume_refused`; `ControlStateUnreadableError` |
| 13 | UNKNOWN | A missing fingerprint invalidates like a change |
| 14 | Retry / recovery | N/A - this is the recovery of state |
| 15 | Checkpoint / resume | This stage; `SM-A1-03` |
| 16 | Security / authority boundary | A checkpoint's own claim is never trusted |
| 17 | Evidence / audit | Checkpoint files |
| 18 | Bypasses | `BYP-A1-03`, `BYP-A1-12`, `BYP-A4-08`, `BYP-A5-14` |
| 19 | AS-IS invariants | A resumed unit never reuses an artifact whose inputs changed |
| 20 | Required contract / delta | C-07, C-08 (resume ledger), INT-02, INT-10, INT-11 clauses |
| 21 | Acceptance proof / negative controls | C-07 resume tests |
