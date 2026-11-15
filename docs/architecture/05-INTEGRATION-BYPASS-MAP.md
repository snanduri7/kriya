# 05 - Integration / bypass map

Canonical runtime baseline: commit `3eded93c0cfb50f8855d04510e3f9aa2cd5208f7`, tree
`97a3da6b2ef664e2b7e25c4c6fe3fc3f42f253a0`. AS-IS.

A "bypass" here is any route by which work proceeds without passing through the owner a reader would expect. A row
records the route, whether it is bounded, and by what. A route being listed does not make it a defect; the gap
ledger says which ones are.

Routes required by the closure instruction and the slice that maps each:

| Route | Slice | Status |
|---|---|---|
| Ordinary Developer retry | A1 | below |
| Owner recovery | A1 (accounting), A3 (apply) | below (`BYP-A1-13`, `BYP-A2-08`, `BYP-A3-09`; bounded in A5: `BYP-A5-13`) |
| Targeted-test self-correction | A1 (accounting), A3 | below (`BYP-A1-07`, `BYP-A2-06`, `BYP-A2-07`) |
| Generated-test rewrite | A2 | below (`BYP-A2-01..09`) |
| Replay | A1 (accounting), A3 | below (`BYP-A2-06..08`; replay truth `AUTH-A3-15`, `AUTH-A2-13`) |
| MCP / tool writes | A5 | below (`BYP-A5-03`, `BYP-A5-04`) |
| Checkpoint / resume | A1 | below |
| Fallback model | A1 (accounting), A6 | below (`BYP-A1-08`; `BYP-A6-03`, `SM-A6-04`) |
| CLI / Desktop approval | A3 | below (`BYP-A3-01..03`, `BYP-A3-11`; closed for the CLI entries in A5: `BYP-A5-10`) |
| Direct / non-enforce execution | A1 (retry), A4 | below (`BYP-A1-02`, `BYP-A2-09`, the A4 rows; root issue R-02) |
| Recovery coordinator | A1 | below |
| Final-result aggregation | A3 | below (`BYP-A3-12`, `BYP-A3-13`, `BYP-A6-12`; root issue R-02) |

## Slice A1 - routes around or beside retry accounting

| ID | Route | What it does to retry accounting | Bounded by | Label |
|---|---|---|---|---|
| BYP-A1-01 | **Enforce re-invocation of a unit.** `workflow/enforce_loop.py` calls `_invoke_bounded_subtask` for the first execution of a stage and again for scope revision, owner recovery, dependent reverification, verifier replay and verifier repair. Each call is a new `run_generation_workflow` call | Every call starts a new `GenerationState`: all budgets zero, no open finding, no seen families, attempt number 0. The unit ceiling (`AUTH-A1-07`) applies per call. The blocker that caused the re-invocation is carried as recovery context (required files, a failure identity, a recovery contract block), not as retry state | 3 scope-revision passes per stage; 3 owner-recovery cycles per stage; `len(plan.subtasks) * 3` cycles per run; one owner reopen per verification-only unit; the run's generation clock | `TRACED` |
| BYP-A1-02 | **Three executors, one unit loop.** Direct (`cli.py` -> `run_generation_workflow`), milestone (`workflow/milestones.py`, one call per milestone) and structured enforce (`workflow/enforce_loop.py`, one or more calls per subtask) | The retry loop, the policy and the recorder are the same code in all three. What differs is the number of invocations and therefore how many times budgets start at zero | Per executor | `TRACED` |
| BYP-A1-03 | **Resume.** An operator resumes a direct or milestone unit, or an enforce run | Retry state is not restored (`AUTH-A1-09`, `AUTH-A1-10`). A resumed unit has a full fresh ceiling and the run has a new generation clock. Repeated resume is not counted anywhere. A resumed production enforce run reuses no completion at all unless the plan had already committed (A1b, `AUTH-A1-10`) | Operator action only. No counter of resumes was found (search of the checkpoint and control modules; `UNKNOWN` whether the run record's `resume_decision` annotation is used as one) | `TRACED` |
| BYP-A1-04 | **Best-of-N first attempt.** `workflow/best_of_n.py` | Resets the full-set counter, the family history, the open finding and the progress history for each independent candidate; keeps the attempt number; adds each discarded candidate to the ceiling. Its failures are recorded by the same `handle_attempt_failure` and handed back as already recorded | `autonomy.best_of_n_first_attempt` (default 1, so inert by default); first attempt only; sandbox required | `TRACED` |
| BYP-A1-05 | **Verification-only attempt.** A unit with no write scope and a directly executable verifier runs `gate_orchestrator` `_run_verification_only_attempt` instead of a Developer attempt | Increments the attempt number; a failure goes through the same recorder and charge; `_admit_verification_only_retry` stops before an equivalent retry when nothing can change | The ordinary budgets plus the declared no-progress stop | `TRACED` |
| BYP-A1-06 | **API-contract recovery phase advance.** A verified transition raises `RecoveryPhaseAdvanced` | Not recorded as a failure: no finding, no family, no progress classification. Charged to the recovery counter only, then the loop continues | 3 recovery attempts; the ceiling counts the attempt number | `TRACED` |
| BYP-A1-07 | **In-attempt repair loops.** The targeted-test self-correction tool loop (`workflow/self_correction.py`, entered from `gate_orchestrator` at two sites) makes further model calls inside one attempt | Not charged to `RetryBudgets` and not an attempt. Skipped when a source review guard is active, during a verifier repair, and on a failing run-authored verifier subject | `autonomy.self_correction_loop_enabled` (default off) and `self_correction_loop_max_turns` (default 4) | `TRACED` for the entry conditions and bounds; the loop body was not read in A1 |
| BYP-A1-08 | **Fallback model.** A fallback-targeted attempt and an escalated full-set attempt run on another model | Stays inside the same accounting: one-shot flag, `fallback_attempts_used`, reserved allowance in the ceiling. A fallback proven unable to serve a patch-only attempt is routed around and can end the unit with a typed stop | The policy (`AUTH-A1-05`) | `TRACED` |
| BYP-A1-09 | **Stops that skip the policy.** `conclude_attempt_failure` ends the loop on a plan-scope conflict or a no-progress stop before `decide_for_state` is asked | No accounting effect; the policy would not have been able to continue either way in the scope-conflict case because the unit must hand over | - | `TRACED` |
| BYP-A1-10 | **Exceptions that leave the loop unrecorded.** `RepositoryContentFilterRefused`, `BoundTreeError`, and anything that is not an `Exception` | Not recorded as an attempt failure and not charged | - | `TRACED` |
| BYP-A1-11 | **Verifier semantic ledger.** `workflow/verifier_semantic.py::VerifierSemanticLedger` holds the run's VALID decisions and the per-subject regeneration allowance | A separate allowance beside `RetryBudgets`, memory only, shared by the units of one process run, lost on resume | One regeneration per subject | `TRACED` (owner and persistence only; semantics belong to A2) |

### Slice A1b additions

**BYP-A1-01, characterized.** The call-site census of `_invoke_bounded_subtask` in `workflow/enforce_loop.py` found
ten call sites and ten roles. Budget each role starts with (`TRACED`):

| Role | When | Unit budget | One-attempt by construction? |
|---|---|---|---|
| `planned` | First execution of a stage | Full ceiling | No |
| `dependent_reverification` | Convergence re-entry of a completed stage | Full ceiling | An identical regeneration is the unit's typed one-attempt stop (`reopened_owner`) |
| `plan_scope_recovery` | After a validated scope revision | Full ceiling | No |
| `owner_recovery` | A `MUST_CHANGE` owner group | Full ceiling | No |
| `may_change_owner_recovery` | A `MAY_CHANGE` owner group | Full ceiling | Identical regeneration stops after one attempt |
| `consumer_retry` | The consumer step when no refused candidate can be replayed | Full ceiling | No |
| `verifier_replay` | The consumer step with its carried refused candidate | Full ceiling object, but no Developer call; a failed replay is a declared no-progress stop | Yes |
| `verifier_repair` | After a person authorized it | Full ceiling object; "one Developer answer" | Yes |
| `verification_owner_recovery` | An ancestor of a failed verification-only unit | Full ceiling | Identical regeneration stops after one attempt |
| `verification_rerun` | The verification-only unit after an accepted owner change | Full ceiling object; nothing to generate | Yes in effect |

Arithmetic for one blocker (`MEASURED` invocation count, `TRACED` ceiling; A1b probe P12): a stage whose upstream
requirement is never satisfied runs 1 planned + 3 x (owner recovery + consumer retry) = **7 unit invocations**
(`tests/test_workflow_controller_enforce.py::test_enforce_bounds_recovery_attempts_per_downstream_owner_pair`,
passed on this baseline). The unit ceiling is 10 without a fallback and 11 with one, so up to **77 Developer
attempts** can be spent on one unresolved blocker before the stage stops, plus up to 3 scope-revision passes. The
run-wide cap multiplies by the number of stages. The only bound expressed in time is
`autonomy.generation_time_budget_seconds`, whose default is `null` (`AUTH-A1-08`, `GAP-A1-06`). In practice the
no-progress stop and `RECOVERY_NO_PROGRESS` end most loops much earlier; neither is a bound on a blocker that keeps
producing different bytes or different failure text. `GAP-A1-04`.

| ID | Route | What it does to retry accounting | Bounded by | Label |
|---|---|---|---|---|
| BYP-A1-12 | **The `resume` flag reaches enforce units.** `_invoke_bounded_subtask` forwards the caller's keywords, `resume` and `resume_id` included, to `run_generation_workflow` | A unit of a resumed enforce run looks up the latest stage checkpoint under its workspace authority (`control/checkpoint.py` `_checkpoints_dir` -> `authority_root`) and may reuse a plan, a design or a gate-passed candidate when every fingerprint matches. Retry state is not restored. A1 said this route did not exist; corrected in `AUTH-A1-10` | The fingerprint validation of `AUTH-A1-09` (goal, workspace content, config, authority context, obligation ledger, candidate bytes). Which checkpoint a unit of a fresh plan sandbox finds, and whether a refused checkpoint (`UNCERTAIN_COMMIT_STATE`) can stop a stage, is `NEEDS_CHARACTERIZATION`: it depends on how `authority_root` resolves a plan worktree (slice A5) | `TRACED` for the forwarding (three sites: the controller's `**legacy_kwargs`, the resume block reading `legacy_kwargs.get("resume")`, the forwarding expression); outcome not exercised |
| BYP-A1-13 | **Owner-recovery self-correction.** Before a `MUST_CHANGE` owner without declared verification is re-invoked as a unit, `owner_recovery._attempt_owner_recovery_self_correction` runs the self-correction tool loop on the owner's files with the Developer model | Model calls and file writes outside any unit invocation: no `GenerationState`, no `RetryBudgets`, no attempt number. If it returns a result the governed unit invocation is skipped for that cycle | The recovery cycle that contains it (3 per stage). Its own turn limit and write path were not read in A1b: mapped by slice A5 (mutation authority) and A3 (which gates accept its result) | `TRACED` for the entry condition and placement only |
| BYP-A1-14 | **A unit that stops for a reason that is not a scope conflict.** | No enforce-level retry or recovery; the stage fails and the plan does not commit (`FR-A1-22`, `FR-A1-21`) | - | `TRACED` (call-site census: no re-invocation site is reachable from such a result, except the verification-only no-change stop) |

### Reading of the map for the WS4 question

The unit loop itself has no route that grants a targeted or fallback-targeted allowance other than a new failure
family (`AUTH-A1-03`). The routes that give a blocker more attempts than one unit ceiling are outside the unit:
re-invocation by the enforce loop (`BYP-A1-01`) and resume (`BYP-A1-03`). Both are bounded by something other than
the blocker, and neither knows whether the blocker is the same one.

## Slice A2 - routes around semantic authority

"Semantic authority" here means two different controls: the person's decision about an existing verifier
(`AUTH-A2-07`) and a canonical contract before generation (`AUTH-A2-05`, which does not exist).

| ID | Route | What it bypasses | Bounded by | Label |
|---|---|---|---|---|
| BYP-A2-01 | A run-authored pytest file under a `src/` layout root. pytest reports `src.pkg.test_x.test_y`; the structural parser drops the leading `src/` and declares `pkg.test_x.test_y`; the two never match | The verifier decision: the file is never a subject and no unknown is raised | The unit's retry ceiling; terminal requirement closure still refuses to close a claim on a model-written test | `MEASURED` (P5: real pytest run from the project root, Kriya's JUnit parser, the real derivation). Not measured: the working directory Kriya's own test gate uses |
| BYP-A2-02 | A run-authored test in a language the structural parser has no grammar for (anything but `.py` and `.java`; Kotlin or Groovy tests of a Gradle project produce JUnit reports) | Same | Same | `MEASURED` at the derivation (P7, hand-built report in the documented identity form); no Kotlin run was executed |
| BYP-A2-03 | A test gate failure with no COMPLETE report | Same; nothing executed, so there is no expectation to judge yet. The Developer may rewrite the test and the code under full budgets until a gate executes it; from then on it is a subject | The unit's retry ceiling | `TRACED`. The registry row's "one self-repair only for non-semantic construction errors" is not implemented and was superseded by the owner's correction (`DOC-A2-02`) |
| BYP-A2-04 | Policy `record`: a goal whose every behaviour claim is `AUTHORITY_REQUIRED` runs | A canonical contract before generation | The terminal requirement outcome stays UNVERIFIED; reported success policy belongs to slice A3 | `TRACED` |
| BYP-A2-05 | Policy `block`, a statement closed by a structural closer: "Include a pytest test for it" is `CLOSABLE` by `test_addition`, which proves a test was added, not what it asserts | A canonical contract for the content of that test | The behaviour statement beside it still needs its own authority (P8: the goal as a whole is refused). A goal made only of structural and anchored statements is admitted with a model-written test | `MEASURED` (P8, P10) |
| BYP-A2-06 | Targeted-test self-correction loop in the gate orchestrator | Would rewrite files from a failing test's output | Closed for subjects: `UnitSemantics.autonomous_rewrite_permitted` refuses when the result identifies a failing run-authored verifier or unknown provenance. Open for unmapped tests (`BYP-A2-01`, `BYP-A2-02`) | `TRACED`; pinned by committed tests (not re-run) |
| BYP-A2-07 | Compile-gate self-correction loop | Not guarded by the semantic owner. Its docstring gives the reason: a semantic decision exists only in an attended run, whose executing gates run behind the source review with the micro-loops off (`gate_orchestrator.py`: the loop requires `post_source_review_guard is None`) | The condition at the loop head | `TRACED` for the condition; that every attended run sets the guard is `INFERRED` here and is checked in slice A3 |
| BYP-A2-08 | Owner-recovery self-correction body (`BYP-A1-13`) | Model calls and writes outside any unit, so outside `UnitSemantics` | See `BYP-A1-13` | Carried to slices A3 / A5, unchanged |
| BYP-A2-09 | Direct run (the desktop path) | Not a bypass: a stricter path. No plan, so no regeneration and no production owner; a failing mapped run-authored test always ends the run | - | `MEASURED` (committed tests); `GAP-A2-03` |

## Slice A3 - routes around approval, verification binding and result truth

For each authority of slice A3 the question was: what other path reaches the same mutation or the same result
without passing this owner? "Closed" means the route was looked for and is refused by the code.

| ID | Route | What it reaches without which owner | Bounded by | Label |
|---|---|---|---|---|
| BYP-A3-01 | Enforce terminal commit | The real workspace receives the whole plan candidate with no approval of that candidate. Approval exists only inside each unit, on that unit's diff against the plan sandbox as the unit found it | By construction the final sandbox is the chain of unit commits, each of which passed its own gate, so in human-in-the-loop mode every committed change was shown in some unit's diff. Nothing compares the terminal batch with what was approved. Writers into the plan sandbox other than a unit's commit were not enumerated (slice A5) | `TRACED` (no approval call in the enforce tail; search in `03-MUTATION-AUTHORITY.md`) |
| BYP-A3-02 | The diff-size trigger is evaluated per unit | In `guardrails` mode a change of any total size is applied without approval when each unit's diff stays at or under `autonomy.risk_threshold_lines` (default 100). The same goal run as one direct unit would ask | The sensitive-path trigger still fires per file; human-in-the-loop mode asks for every unit | `TRACED` (`approval_gate` computes `total_diff_lines` from this unit's `all_files_written`). `GAP-A3-05` |
| BYP-A3-03 | `--yes` outside a desktop launch | The callback answers True without asking (`cli.py::_approval_decision`, `_fix_approval_decision`, the proposal command's `on_approval`) | The operator's own standing authority; refused inside a launch | `TRACED`; intentional contract |
| BYP-A3-04 | A caller that supplies no approval callback | Closed: `approval_required`, nothing applied (`FR-A3-07`). The comment records the earlier fail-open and its fix | - | `TRACED` |
| BYP-A3-05 | A unit that wrote no source | No approval is asked (there is no change to approve). Runtime commands of such a unit have their own approval subject (`policy/approval.py::RuntimeCommandSubject`) | - | `TRACED`; intentional contract |
| BYP-A3-06 | Approval required by a trigger other than human-in-the-loop mode (sensitive path, size, process profile) | The executing gates build, test and run the model-authored candidate **before** the person is asked | The approval still precedes the apply | `TRACED` + `MEASURED` (P7: `guardrails` is not held). Registry `HITL-OTHER-APPROVAL-TRIGGERS-DEFERRAL-001`, open owner decision |
| BYP-A3-07 | `commit_service.py::commit_verified_candidate` when `candidate_root == workspace_path` | Returns `completed=True` without the transaction, so without the binding check, the guards and the commit evidence | The enforce loop takes its sandbox from `create_git_worktree`, whose contract (stated at the direct call site) is a sandbox path or an error. The enforce call site has no equality assertion of its own | `TRACED` for the branch; unreachability `INFERRED` (the worktree function was not read) |
| BYP-A3-08 | The run-list reader takes the last `requirement.truth` event as the run's final truth | The event is written at the unit's closure step, before terminal regression and apply, once per attempt that reaches it. In a run whose later attempts fail earlier, the last event describes a superseded candidate | Both come from the same ledger; the result field is computed at the end. Affects the run list of failed runs | `TRACED` (`application/history.py::_truth_of_events`, `development_loop.py` emission site). Not executed |
| BYP-A3-09 | An attempt of an attended human-in-the-loop run before the run has written any source | Its executing gates are not held and run without the post-review guard, so the compile self-correction loop is not switched off for it. If that loop writes files, model-authored bytes are compiled and tested before any source review | The approval gate still precedes the apply | Hold condition `TRACED` + `MEASURED` (P7: wrote nothing means not held). That the loop can then write and execute was **not traced**: `INFERRED`. Residue of `BYP-A2-07` |
| BYP-A3-10 | Gate-side candidate mutations (`MUT-A3-02`) | Bytes enter the candidate outside the candidate transaction | They happen before the binding (pom corrections are applied before the review in held mode; self-correction is off after a review), so they are inside what is bound, shown and verified | `TRACED` for the ordering; the loops' writers not traced |
| BYP-A3-11 | A desktop launch reaching a decision that is not a change, a runtime command or a verifier review | Cannot be asked: knowledge-gap acknowledgement and policy actions answer UNAVAILABLE and stop the run; skill gaps, skill conflicts and both live-lookup decisions are declined and the run continues (`control/launch.py::decline_decision`) | Recorded in the launch record (`approval_required`, `declined_decisions`) | `TRACED` (`policy/approval.py::KINDS` has four kinds; `cli.py` call sites). Registry `DESKTOP-LOOKUP-QUERY-APPROVAL-001` |
| BYP-A3-12 | One unit result, two readers | A unit applied with its final review refused is a failure for `plan_executor.py::_passed` and an acceptance for `enforce_loop.py::enforce_unit_gates_accepted` | Reported either way (`final_review_refusal`, `advisory_reviews_not_performed`) | `MEASURED` (P2). `GAP-A3-03` |
| BYP-A3-13 | Milestone plans commit each unit to the real workspace before the plan is verified | "Applied" precedes the replay phase and the integration unit's requirement check; a later failure leaves the commits | Declared strategy (`execution_plan.py::CommitStrategy.INCREMENTAL_WORK_UNIT`); the result names the retained commits. Enforce is all-or-nothing instead | `TRACED`; intentional contract, to be stated as one (`GAP-A3-01` discussion) |
| BYP-A3-14 | The final review is the reused pre-approval review | In held mode that review was written before anything was built or tested. After a terminal-regression failure on the last attempt it is returned as the run's review (`FR-A3-11`) | Advisory text only | `TRACED`; golden pin. `GAP-A3-06` |
| BYP-A3-15 | The approved diff is read from the sandbox after the binding was taken | The digest the person approved (`policy/approval.py::candidate_digest`, over diff text) is never compared with the binding or with the committed bytes. Equality of "shown" and "committed" rests on the sandbox not changing between the bind and the diff read; a change that is reverted before the commit would be shown and not committed, or the reverse | The commit refuses any batch that differs from the binding; in held mode the guard re-binds right after the approval. The window needs two writes to the sandbox | `TRACED`. Registry `GUI-REVIEW-APPLY-BINDING-001`. `GAP-A3-04` |
| BYP-A3-16 | The approval diff is text | Files are read with replacement of undecodable bytes; a mode change and a binary change are not visible in the diff, yet they are in the binding (mode and exact bytes) and are committed | The file-integrity contract refuses targets the edit engine cannot mutate byte-exactly | `TRACED` (`approval_gate` reads with `errors="replace"`); not executed |
| BYP-A3-17 | A managed (long-running) service chosen by the model, outside human-in-the-loop mode | Runs with no reviewable approval subject | Human-in-the-loop mode fails closed (`MANAGED_SERVICE_APPROVAL_UNSUPPORTED`) | `TRACED` for the constant; the non-HITL path was not re-read. Registry `MANAGED-SERVICE-APPROVAL-001` |

Carried from earlier slices and still open after A3: `BYP-A1-13` / `BYP-A2-08` (owner-recovery self-correction body;
A3 read no further into it; slice A5 owns the writes). `BYP-A2-07` is settled for attempts that wrote source
(`AUTH-A3-05`) and narrowed to `BYP-A3-09`.

## Slice A4 - routes around goal, knowledge, skill and plan authority

For each authority of slice A4 the question was: what other path reaches the same decision, the same model request
or the same mutation without passing this owner? "Closed" means the route was looked for and is refused by the code.

| ID | Route | What it reaches without which owner | Bounded by | Label |
|---|---|---|---|---|
| BYP-A4-01 | The direct shape executes without `validate_plan` | A plan whose structure enforce would refuse is `complete` (cycle, two owners of a file, no invariants, no semantic contracts) | On the direct shape the structure is not executed: the prose plan goes to the Architect, one unit, one retry loop. The divergence is in what "the plan was valid" means | `MEASURED` P3 |
| BYP-A4-02 | A unit's skill selection reads the composed unit goal | A skill the Planner never saw reaches a Developer; a version in Planner text can remove a skill the Planner planned with | The user's goal is inside the unit goal, so goal-selected skills normally stay; repository-fact skills are the same for both | `MEASURED` P4 |
| BYP-A4-03 | An empty folder named after a library | The knowledge gate treats the library as covered: no structural gap, and a post-cutoff version is MEDIUM instead of HIGH. A folder inside a workspace skills directory is project-controlled | The temporal gap still reports (MEDIUM). `knowledge.offline_mode` removes the temporal check altogether | `MEASURED` P5. Registry `KNOWLEDGEGUARD-SKILL-IDENTITY-001` |
| BYP-A4-04 | Live lookup on a repeated gate failure | Fetched page text (up to 2,000 characters per term) is appended to `state.error_context`, which heads the next Developer request and the Planner's "Fix the following compile/test error" block, **outside the untrusted-reference fence** | Off unless the operator enabled web lookup and configured a search endpoint; each query needs a human approval unless auto-approve is on and every term is public. Project Setup writes `web_lookup_enabled: false` | `MEASURED` P7 (the function's output); the prompt positions are `TRACED` (`stage_planning.py` plan head) and the Developer request was not read |
| BYP-A4-05 | A non-public name as a lookup term | An internal library or product name from the goal or a skill name leaves the machine as a search query | Requires the per-query human approval, which shows the exact terms; unattended mode refuses non-public terms | `MEASURED` P6 |
| BYP-A4-06 | A workspace skill's rules with no provenance record | Rendered under the trusted `Rules:` header, outside the untrusted fence, with the Planner told not to contradict them. A repository can therefore ship instructions | The skill's `verified` flag cannot be self-attested, so the person is asked about the unverified skill once (unless no callback is wired); the label grants no write, tool or approval authority (W3-1 census in the registry row) | `MEASURED` P8. Registry `SKILL-RULE-PROVENANCE-PROJECT-SUPPLIED-001` |
| BYP-A4-07 | Skill writes on the direct and milestone shapes | Skeletons, extracted rules and acknowledgements are written to the canonical skills directory and Git-committed during analysis, before a candidate exists and whatever the run's outcome. When the skills root is inside the workspace repository this moves the repository's HEAD outside the candidate transaction | Enforce uses the run shadow. `new_skill_refusal` protects the shared install directory | `TRACED` (single caller of `activate_run_shadow`). Handed to slice A5 (workspace mutation) |
| BYP-A4-08 | A resumed direct unit | Skips the knowledge gate (`knowledge_risk_confirmed = True`) | A checkpoint exists only after stage 0 passed in the checkpointed run; the goal is drift-checked. A skill removed or a cutoff changed since is not re-evaluated | `TRACED` |
| BYP-A4-09 | The controller's knowledge check reads `config.paths.skills`; a unit's reads the engine's effective root | Two skill-folder listings for one decision | They differ only while a run shadow is active, which starts after the controller's check | `TRACED` |
| BYP-A4-10 | The Planner's decomposition chooses the subject of the size trigger | Under enforce with `autonomy.mode: guardrails`, a change split into units of at most `risk_threshold_lines` lines each asks nobody; no plan-level or run-level measure exists | `human-in-the-loop` (the packaged default and the Project Setup profile, `MEASURED` P10) approves every unit that wrote source; the sensitive-path trigger is per file and cannot be split away | `TRACED` (one reader of `risk_threshold_lines`; no approval in plan acceptance). Extends `BYP-A3-02`; `GAP-A3-05` |
| BYP-A4-11 | A plan revision during execution | The executed plan differs from the one validated at the start | Revalidated by `validate_plan` at each of the three sites; bounded by 3 scope revisions | `TRACED` at call sites; bodies not read |
| BYP-A4-12 | An enforce unit's `goal` argument | Consumers of `goal` (knowledge gate, skill selection, the unit's own Planner-stage prompt head) read Planner text together with the user's goal | The user's text is labelled authoritative inside it; requirement derivation does not read it (`AUTH-A4-15`) | `TRACED`; `MEASURED` P4, P5 for two consumers |
| BYP-A4-13 | `kriya fix` | Runs with no requirement set (`requirements_from_goal=False`): nothing to close, so nothing can be open | Its goal is Kriya's own wording around an error log; the gates still run | `TRACED` (`cli.py` line 4770, `workflow.py`) |
| BYP-A4-14 | A folder with no `kriya.yaml` | Runs the direct shape; the same folder after Project Setup runs enforce. Result truths differ by shape (`GAP-A4-01`) | Both are human-in-the-loop by default | `MEASURED` P10 |
| BYP-A4-15 | Shadow mode | A structured plan is built and executed by `SubtaskExecutor` next to the real run | Observational: exceptions are swallowed, files and outcome are the direct run's (`workflow_controller.py::execute`); `shadow_mode.py` not read | `TRACED` at the call site |
| BYP-A4-16 | An integration decision recorded by the Planner | Binds every participating unit's Developer "exactly", over an applicable skill | `validate_plan` checks that a decision selects one of its alternatives and that decisions do not conflict (`INTEGRATION_DECISION_*`); no person sees it | `TRACED` |
| BYP-A4-17 | Design-derived lookup terms (stage 2B) | A library name the Architect wrote into its design becomes an outbound search term: a model chooses what is searched | The term passes the sanitizer; the per-query authorization of `AUTH-A4-06` applies; web lookup must be enabled | `TRACED` |
| BYP-A4-18 | Stage 2A with no approval callback | A dependency the Architect introduced, which the knowledge gate flags, is accepted with a log warning | Callers that wire a callback (the CLI's `generate`; a desktop launch's channel) ask. Which entry points wire none was not established (`kriya fix` is the candidate) | `TRACED` |
| BYP-A4-19 | The knowledge guard's own scaffold | `generate_skill_template` creates a folder named after the coordinate, with no rule and unverified. On the next run the gate finds "a skill" for that library: the structural gap is gone and a post-cutoff version drops from HIGH to MEDIUM. Auto-accrual does this without a person | Online, a post-cutoff version is still reported (MEDIUM); the analysis stage still lists the unverified skill for the skill-gap prompt | `MEASURED` P11 (offline) |

Closed routes found in A4: a workspace skill attesting its own verification or gap acknowledgement (`MEASURED` P8);
a requirement contract shipped by the repository; a repaired plan validated against a different tool catalogue than
the one shown (same snapshot); whole-goal execution after structured planning fails; a path, code or diagnostic
string as a lookup term (`MEASURED` P6).

Carried into later slices: `BYP-A4-07` (A5), the Developer request positions of `BYP-A4-04` (A6, with STAGE-GENERATION).

## Slice A5 - routes around root, write, tool, state and configuration authority

The question per authority of slice A5: what other path reaches the same mutation, the same model input or the same
effective setting without passing this owner? "Closed" means the route was looked for and is refused by the code.
The table at the top of this document lists "MCP / tool writes" as owed by A5: `BYP-A5-03` and `BYP-A5-04`.

| ID | Route | What it reaches without which owner | Bounded by | Label |
|---|---|---|---|---|
| BYP-A5-01 | A unit's candidate contains `kriya.yaml` (or `kriya.yml`) | Model-authored bytes become the project's Kriya configuration for every later run. For `REPOSITORY_SAFE` fields no approval is consulted: verification can be switched off (`run_verification_enabled`, `spec_compliance_enabled`), the knowledge check too (`knowledge.check_enabled`), the run budget removed, auto-index turned on. The configuration authority (`AUTH-A5-10`) is not passed, because it judges sources, and the source is "the repository" | Every non-safe field still needs the operator's approval, and any change to the approved set invalidates the approval. The unit's source approval shows the diff in human-in-the-loop mode; in guardrails mode it asks only on the size and sensitive-path triggers, and `kriya.yaml` matches no sensitive pattern. The current run is not affected (its configuration is already loaded) | Gate verdict `MEASURED` P5; the end-to-end carry `INFERRED` (`AUTH-A5-04`, limitation) |
| BYP-A5-02 | A unit's candidate contains files under `<workspace>/skills/` | Model-authored text becomes a workspace skill (`skills.load_cwd` is on by default): rules and instructions rendered into later requests, and a folder name that satisfies the knowledge gate (`GAP-A4-03`) | A workspace skill's `verified` claim is not honoured (slice A4 P8); its rules are still rendered under the trusted header when they have no provenance record (`BYP-A4-06`). The same approval bound as `BYP-A5-01` | As `BYP-A5-01` |
| BYP-A5-03 | A planned MCP call whose server holds a `workspace_write` grant | The owner workspace is changed in place during the run: no candidate, no diff, no source approval, no gate, no rollback, no record of the changed paths. The write gate and the candidate transaction are not passed | Two operator decisions, both outside the workspace: SEC-009 approval of the server's configuration and capabilities, TOOL-002 approval of the exact tool identity. Containment limits the server to its grants. A later unit whose file the server changed meets a revision conflict; a file no unit touches is simply different afterwards | `TRACED`: `MCPManager.start_all` binds `os.getcwd()`; `containment_adapter.py` mounts it read-write; `MCPTool.authorize_model_invocation` ignores the invocation; absent `invocation.roots` |
| BYP-A5-04 | A planned read tool, a planned filesystem write, a planned shell command, a plugin tool | - | **Closed.** Reads pass the read gate over the invocation's roots; every planned filesystem write is refused (`DENY_ALL`); `shell` and `validate_refactor` are operator-only; a plugin's declaration is not honoured | `TRACED` (`AUTH-A5-06`) |
| BYP-A5-05 | The packaged-default memory location | Another project's code index is read as this project's: symbol lookups, the "already indexed" test, retrieval matches and the trace row (`SM-A5-04`). No project attribution owner exists to pass | The structural readers that go through `current_view()` shadow every stored path the project does not hold, so goal facts, plan-target validation and the localization helper see only the project's own files (`MEASURED` P2). The assembled Planner block omits a matched file that does not exist in the project. Populating the location needs `kriya analyze` or the opt-in auto-index; the desktop alone never does | `MEASURED` P1, P2 |
| BYP-A5-06 | The packaged-default skills location | A run in one project writes a scaffold, appended rules, provenance or staged facts into the installation's skills directory, where every other project loads them (`skills.load_global`) and where the knowledge gate of every other project reads the folder name. A scaffold made by project A's run removes project B's knowledge gap (`MEASURED` P8) | `new_skill_refusal` for a new skill created through the engine; a logged warning otherwise. Skills in the installation directory are the ones whose `verified` claim Kriya believes (slice A4, `AUTH-A4-07`): the location that is writable by any project's run is also the trusted one | `MEASURED` P1, P8; `TRACED` for the writers (`MUT-A5-08`) |
| BYP-A5-07 | Skill writes and Git commits into a skills root inside the workspace repository, on the direct and milestone shapes (`BYP-A4-07`, handed over) | The repository's HEAD moves during analysis, outside the candidate transaction and outside the write gate | Enforce (the Project Setup shape) uses the run shadow. On the direct shape nothing bounds it; the workspace-sync step then copies the committed state into the sandbox | `TRACED`: writers enumerated in `MUT-A5-08`, none constructs the write gate (control) |
| BYP-A5-08 | Membership decided by a directory name | The code-intelligence file discovery when `git ls-files` fails, and the analyzer's walk, each use their own rules instead of the bound tree | Neither decides a result; both feed retrieval only. The full census is the registry's (`DIRECTORY-NAME-PRUNING-P3-SIBLING-SITES-001`) | `TRACED` for these two sites; the other sites of the census were not re-read |
| BYP-A5-09 | A repository raises or removes its own run budget | `generation_time_budget_seconds: 999999` or `null` in the project's `kriya.yaml` takes effect with no approval | The production profile refuses a value above 3600 and `null`. Other bounds stay (per-call transport timeouts, the containment resource bounds, which a repository may only lower) | `MEASURED` P6 |
| BYP-A5-10 | Entry points with no approval callback (`BYP-A4-18`, handed over) | - | **Closed for the CLI.** Every CLI call into the engine passes one: `generate` (four call sites), the proposal execution, `kriya fix` (control `approval_callback=`, and the call sites read). A desktop launch runs the same `generate` with a review channel; if the channel is unavailable no review can be answered and the run stops at the first one. The other holders of `approval_callback=` (`milestones.py`, `development_loop.py`, `proposal_promotion.py`, `workflow.py`) are internal and were located by the control, not read | `TRACED` for the CLI call sites; the internal forwarding `INFERRED` |
| BYP-A5-11 | A desktop launch confirming a knowledge gap (handed over by A4) | - | **Closed.** `_launched_generate` calls `_generate_impl` with `yes=False` and the application's fixed `KNOWLEDGE_POLICY = "strict"`: an unacknowledged gap stops the run. The desktop can therefore not confirm a knowledge risk; it can only be made to disappear by a skill folder (`BYP-A5-06`, `GAP-A4-03`) | `TRACED` |
| BYP-A5-12 | `commit_verified_candidate` with a candidate root equal to the workspace (`BYP-A3-07`, handed over) | - | **Closed at the producer.** `create_git_worktree` returns the managed sandbox path or the scoped snapshot path, or raises; no branch returns the workspace | `TRACED` (every `return` of the function) |
| BYP-A5-13 | Owner-recovery self-correction (`BYP-A1-13`, `BYP-A2-08`) and the compile self-correction of an unheld attempt (`BYP-A3-09`) | Model writes outside a unit's batch | Off unless an operator enabled `self_correction_loop_enabled` (value-gated, `SECURITY_AUTHORITY`); when on, every write passes the write gate at the candidate root (`MUT-A5-05`). Project Setup writes the restricting value | `TRACED` |
| BYP-A5-14 | The `resume` flag in an enforce unit (`BYP-A1-12`) | A unit looks for checkpoints under the plan sandbox's own authority, because a sandbox is its own authority root (`AUTH-A5-01`) | The fingerprint validation of `AUTH-A1-09`. Whether anything survives the sandbox reset is not established (Phase B) | `TRACED` for the identity |
| BYP-A5-15 | The installation directory's own `kriya.yaml` | A second user layer, read when the workspace has none | Treated as an untrusted source like a repository's (`AUTO_DISCOVERED_INSTALL_DIR`) | `TRACED` |
| BYP-A5-16 | A diagnostic or test that isolates the state directory only | Launch records and approvals are written to the real authority home | A process rule (registry `PROCESS-DIAGNOSTIC-PROBE-REAL-AUTHORITY-HOME-001`); `tests/conftest.py` isolates the homes for the suite | `TRACED` |

Carried into later slices: the Developer request positions of retrieval and skill text (A6, with STAGE-GENERATION and
`GAP-A4-04`); whether the run budget enters qualification identity (A6).

## Slice A6 - routes around model, qualification, trust-boundary and evidence authority

The question per authority of slice A6: what other path reaches the same model call, the same prompt content or the
same recorded outcome without passing this owner? The table at the top of this document lists "Fallback model" as
owed by A6: `BYP-A6-03` and `SM-A6-04`.

| ID | Route | What it reaches without which owner | Bounded by | Label |
|---|---|---|---|---|
| BYP-A6-01 | A production-profile run on an unqualified primary model | Every Developer call on the primary, and every Planner, Architect, Reviewer, run-verifier, spec-compliance and skill-gap call, without the qualification authority (`AUTH-A6-06`) | The operator running `doctor --production` beforehand; the release-candidate tooling; the provider-contract identity checks, which still apply | `TRACED` (`GAP-A6-01`) |
| BYP-A6-02 | A model name with no binding | A call with the primary's settings and adapter under another model name (`AUTH-A6-01`) | Model names come from the configuration's own chain and routes, so the route needs a caller that passes a name outside them; none was found | `MEASURED` P7 (the resolution); reachability `INFERRED` absent (`GAP-A6-02`) |
| BYP-A6-03 | Fallback model | A Developer call on another model. It passes the compatibility authority (`AUTH-A6-07`); the retry accounting side is `BYP-A1` rows | Closed for the Developer. Non-Developer role chains escalate with no compatibility or qualification check | `TRACED` |
| BYP-A6-04 | `KRIYA_MODEL_RUNTIME_PROBE=0` in the environment | Every runtime becomes non-exact: no qualification can be current, and outside production nothing is refused for it | Under production a non-exact runtime fails the identity checks that need an observation; the variable is not a configuration field, so SEC-009 does not see it | `TRACED` |
| BYP-A6-05 | Text fetched during the run, into the Developer task (completes `BYP-A4-04`) | The `=== Previous Error to Fix ===` body of the next Developer request, outside the fence; with a retry package under "Advisory reference evidence" | As `BYP-A4-04` (lookup off by default, approval per query, offline sealed in production) | `MEASURED` P1 (b) |
| BYP-A6-06 | The same text, into the Reviewer's system prompt | The block headed "AUTHORITATIVE RUN DISPOSITION" of the rejected-candidate system prompt, as `terminal_reason`; and the user message ("Last quality gate error") | As above; only on a run that ended with a rejected candidate. The Reviewer decides nothing (`AUTH-A3-12`); its text is shown to the person | `MEASURED` P1 (c) |
| BYP-A6-07 | The same text, into Kriya-authored instructions | The incompatible-types and buffer-capacity scaffolds built by pattern match from `prior_error_context` | Only on the path without a retry package (with one, `prior_error_text()` excludes the advisory part, `MEASURED`) | `MEASURED` P1 (d) |
| BYP-A6-08 | The same text, into a deterministic selection | `extract_target_test(state.error_context, ...)`: a test file name in the page text selects the targeted run | A targeted run that executes no test falls back to the suite; the suite gates still decide the candidate | `MEASURED` P1 (e) |
| BYP-A6-09 | The same text, into durable knowledge | The lesson-extraction prompt after a retried success (`LiveFailureChannel.extract`), whose facts are staged | Staged facts are unverified until promoted (slice A4); how a staged fact is later rendered was not re-read; staging follows `GAP-A5-05` | `TRACED` |
| BYP-A6-10 | The Planner's "Fix the following compile/test error" block (`BYP-A4-04` named it) | The Planner reads `state.error_context` at planning time. In the stage order of one unit invocation planning precedes the first attempt, so that value is the caller's own error argument (`kriya fix`), not the augmented text | Refinement of `BYP-A4-04`: not reached by fetched text in the sequence read. Re-planning routes (`plan_revision.py`) were not read | `TRACED` for the read; the ordering argument is `INFERRED` from the stage order |
| BYP-A6-11 | A knowledge gate closed by an empty folder, then generation | The Developer request, with no text about the library and no check that the "covering" skill has any | Nothing at request time (controls: no reader of the knowledge gate or of the unresolved gaps in `agents/agent.py` or `generation_service.py`). The unit analysis prompts about missing skills but does not stop | `TRACED` (`GAP-A4-03`) |
| BYP-A6-12 | The outcome shown by the desktop history | The stored `status` of a history row that any later write of the same run id replaces and that carries no binding to a workspace or to the RunRecord | The state directory is private to the user (private file modes); `launch.status` reads the RunRecord | `MEASURED` P5 (`GAP-A6-03`) |
| BYP-A6-13 | A rewritten evidence store | A reader's `VERIFIED` | As above: needs write access to the state directory | `MEASURED` P4 (`GAP-A6-03`) |
| BYP-A6-14 | A run with evidence capture off, or with an unwritable state directory | A committed change with no attempt evidence, or no history row | By design (observational recorder). Capture defaults to `full` (design D4, cited) | `TRACED` |
| BYP-A6-15 | Gates run outside a declared purpose | A `gate.result` record with no `purpose`: the terminal verification coordinator, the development loop's baseline and regression runs, the requirement closers, regression attribution and the self-correction loop declare none | Diagnostic only: no verdict, order or count depends on it | `TRACED` (controls) |
| BYP-A6-16 | Unattributed model calls | Calls of the knowledge channels counted under `unattributed`, on whichever model the caller passes (the last Developer model for lesson extraction) | Observations only; routing reads identity rows of a role, so an unattributed row matches no candidate | `TRACED` |
| BYP-A6-17 | An aggregate size or approval authority in the runtime, control, metrics or evidence layers (the last place `GAP-A3-05` could have been closed) | None exists: no reader of `risk_threshold_lines` under `kriya/core`, `kriya/control` or `kriya/metrics`; the evidence store records `candidate.change` per unit and decides nothing | - | `TRACED` (three controls). `GAP-A3-05` stands |
| BYP-A6-18 | An MCP call's effect, as the records see it (the transaction question of `GAP-A5-03`) | See the table below | - | `TRACED` |

What exists for a planned MCP call with a write grant, against what a candidate transaction has (`TRACED`; no MCP
server was run):

| Property of a transaction | Candidate (slices A3, A5) | Planned MCP call |
|---|---|---|
| Exact mutation scope | The staged batch, path by path, inside the unit's scope | The whole workspace mount (and each additional write path) for the lifetime of the server; the call's arguments are forbidden evidence for authority (`AUTH-A5-07`) |
| Approval binding | The unit's candidate digest (source approval), or the launch review's digest | The tool identity (server, tool, schema digest), the capability-profile digest and the workspace identity. Not the arguments, not the resulting bytes |
| Isolation before commit | The sandbox | None: the mount is the owner workspace |
| Rollback / failure | The guarded commit and its recovery | None. The RunRecord holds `tool_execution` started / failed / completed; a started call that did not finish blocks the next run as `UNRESOLVED_TOOL_EXECUTION` (`SM-A5-02`) |
| Audit | Candidate digests, `candidate.change`, gate results, the commit record | `PolicyDecisionRecord` (identity, profile, containment state) and the evidence record `tool.execution`: tool name, status, error, reason codes and the bounded tool output. No list of files, no digest of the workspace before or after (absent `record_candidate_write` and `candidate_digest` under `kriya/mcp`; absent `record_candidate_write` in `subtask_executor.py`) |
| Final-result integration | Files modified, the diff, requirement evidence | The TOOL subtask's status only; the changed files are in no `files_modified`, no diff and no review |

So no independently authoritative transaction exists for MCP workspace mutation: what is governed is the tool, not
the change. Under the owner's ruling on `GAP-A5-03` this meets the condition for FIX_REQUIRED_BEFORE_PRODUCTION
(gap ledger, "Updates to gaps of earlier slices").

### Extensibility seams (`TRACED` seam analysis; no empirical claim)

**A second inference runtime (vLLM).** The documented extension point (`core/inference_runtime.py` module docstring,
`handover/INF_001_RUNTIME_PORT.md`) is: subclass `OpenAICompatibleTransport`, declare
`per_request_context_window=False`, implement `probe`, register under its own name.

| Contract a new runtime must meet | Where it is enforced | Fit of the documented vLLM design |
|---|---|---|
| Selected by name, never by default | `runtime_for_binding`; `vllm` is refused today (`MEASURED` P2) | Fits: one registration in the provider module |
| No provider name above the port | A committed tripwire; in the modules read, provider names above the port occur in comments and doctor remediation text only (search of `kriya/core`, `production_doctor.py`) | Fits. The doctor's remediation sentences name the default runtime's endpoints and would mislead for another runtime |
| Exact identity (artifact digest and server version) | `ModelRuntimeFingerprint.exact` | Open: the design says "from the server's version and model endpoints"; whether such a server reports an artifact digest is not established here. Without one the runtime is never QUALIFIED and, under production, cannot pass |
| Over-window prompts refused, not truncated (`over_context_refusal`, required of every role) | Qualification policy `/9` | Open: it is the case the default OpenAI-compatible adapter fails (section 42). A subclass of the same transport inherits the question; it depends on the server, and was not measured |
| Requested window equals served window under production | `context_window_state` | Needs the probe or an observation to report the served window; with a server-fixed window and no per-request option the declared window must equal it, else `RUNTIME_CONTEXT_IDENTITY_MISMATCH` |
| Served-window observation after the call under production | `_check_after_call`, only for a runtime that declares observation | Open: the refusal applies to a runtime that declares observation. What production does with an exact runtime that declares none was not traced |
| Settings proven effective | `ProviderRequestPlan` from the adapter's `provider_capabilities` | The adapter must declare, per semantic setting, supported / server-only / unsupported; an undeclared setting is never sent |
| Adaptive context tiers | Only on a runtime that takes a per-request window | Not available: the adaptive policy behaves like strict and says so |
| Capacity evidence per execution environment | `execution_environment.py`, observable only for a local endpoint | A remote server has no observable environment: capacity evidence would not count |
| Egress | `local_only` refuses a non-local endpoint; a non-local endpoint is never probed under any policy | A server on another host needs `egress_policy: unrestricted` and can then never be exact |

Conclusion at `TRACED` confidence: the port contract can take a second runtime without a change above the port. The
production profile can take it only if that server gives an exact identity, refuses over-window prompts and runs on
this machine. None of the three is established for vLLM. The existing contracts therefore fit a local second runtime
for non-production use; production use is an identity and qualification question, not a seam question.

**Richer Skills and Tools user experience.**

| Capability a richer UX would add | Existing contract it must use | Fit |
|---|---|---|
| Browse skills and tools, see approval state | `application/catalog.py` (`SkillCatalogService`, `ToolCatalogService`), KUP `skills.list`, `skills.describe`, `tools.list`, `tools.describe`, `tools.approval_state` | Exists, read-only |
| Create or edit a skill from the desktop | One skill-write entry with a location rule and a transaction | **Does not exist**: eleven writers outside the write gate (`GAP-A5-05`); the default skills directory is installation-wide (`GAP-A5-01`); no KUP operation mutates a skill (registry `GUI-SKILLS-MUTATION-001`, `GUI-SKILLS-BUILD-001`) |
| Mark a skill verified, promote a rule | Skill fact authority (`AUTH-A4-07`: verification is Kriya's, by a committed run) | A person's "verify" button would be a second fact authority. Not a UX question: an authority decision |
| Choose which skills apply | Skill relevance authority | **Does not exist below the goal** (`GAP-A4-02`) |
| Close a knowledge gap from the desktop | The knowledge gate | The gate accepts a folder name (`GAP-A4-03`); a UX on top would make the false closure one click |
| Approve an MCP tool from the desktop | TOOL-002 approval store, outside the workspace, operator-only (`kriya mcp approve`) | No KUP operation writes it; the desktop's `configuration.approve` shows the pattern (a review fingerprint, refused when stale). A tool approval would bind the same five fields as today |
| Run a tool from the desktop | Planned-tool governance, or operator execution | A write-capable MCP tool has no transaction (`GAP-A5-03`) |
| Tool health | A bounded health read | Not present (registry `GUI-TOOLS-HEALTH-001`) |

Conclusion at `TRACED` confidence: the read side fits today. Every mutating Skills or Tools feature lands on a gap
that is already recorded (`GAP-A4-02`, `GAP-A4-03`, `GAP-A5-01`, `GAP-A5-03`, `GAP-A5-05`). Building the UX first
would give those gaps a button; the contracts have to exist first.
