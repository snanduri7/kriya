# PRD-020 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 5: PRD-020 to PRD-024, one pytest stop for the whole batch).

## Source identity
- Base revision: `9b1cca5` (batch 4 verified and pushed).
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Immutable requirement and obligation lineage (instruction: `tasks/PRD-020_Immutable_Requirement_and_Obligation_Lineage.md`).

New module: `kriya/workflow/requirements.py`.

| Requirement | Implementation |
|---|---|
| 1. Immutable requirement records with stable ids | `derive_requirements(goal, clarifications=())` is pure and deterministic: list items, then sentences (never inside backticks or after `e.g.`/`i.e.`/...). Each statement becomes `REQ-n` with the user's text verbatim; a negation cue marks it a constraint (metadata only). A goal with one statement is one requirement. The set's digest binds `REQUIREMENT_DERIVATION_VERSION`, the goal digest and every record. No model is involved, so no model can reword a requirement. |
| 2. Planner/Architect/Developer/verifier mapping | Direct and milestone-integration runs: the Planner and the Architect are both shown the requirements and asked to cite the REQ ids each step or decision serves. The Architect used to see only the Planner's prose, so a requirement the plan dropped never reached the design. Citations are recorded as `requirement.lineage` run events (cited and omitted per stage). Enforce: `Subtask.requirement_ids`, and `validate_plan(known_requirement_ids=)` rejects an invented id (`PLAN_REQUIREMENT_ID_UNKNOWN`). The Developer gets the goal, and a retry gets the failure naming `REQ-n: <original text>`. The verifier (Goal Spec Compliance) returns one verdict per id. |
| 3. Downstream prose cannot mark a requirement satisfied | Only verifier code writes an outcome: `attempt._record_original_requirement_verdicts` after the deterministic gates of that attempt passed, and the enforce terminal gate. The evidence id is the content fingerprint of the goal plus every judged file. Plan and design citations are lineage, never evidence; the Reviewer never writes. A verifier "missing" claim that the existing arbitration suppresses (it contradicts a SATISFIED deterministic obligation, or names a Planner-only identifier) is recorded UNVERIFIED, not VIOLATED. |
| 4. Terminal aggregation blocks per policy | `blocking_requirements`: VIOLATED always blocks. PENDING or UNKNOWN (no verdict) blocks under `autonomy.requirement_unknown_policy: block`; UNVERIFIED blocks under `requirement_unverified_policy: block`. The direct and integration paths enforce this at the pre-apply boundary (typed terminal failure `requirements_unresolved`, reason `REQUIREMENTS_UNRESOLVED`, nothing applied, no Developer retry). The enforce path adds a terminal gate, `original_requirements`, before the commit. |
| 5. Persist lineage and evidence; preserve across retry/resume | Each requirement is an `ORIGINAL_REQUIREMENT` obligation in the run's ObligationLedger (checkpointed, fingerprinted into the RunRecord, restored on resume). Run events: `requirement.derived`, `requirement.lineage`, `requirement.verdicts` (outcomes, evidence id, per-id evidence, findings). The result carries `requirements` (set, digest, outcomes). The ids are derived from the goal again on resume, so a saved plan's wording cannot change them. |

### Which unit verifies the requirements
`WorkUnitInvocation.requirement_goal` (`plan_executor.requirement_goal_of`) names the goal a unit verifies:
- a direct plan's one unit: its goal;
- a milestone plan's integration unit: the plan's `original_goal`;
- every other unit: none (a milestone or a subtask verifies something narrower).

Enforce derives the requirements from the controller's goal and judges them once against the whole verified candidate at the terminal gates.

### The gate stays narrow by code, not by prompt
A "missing" verdict counts only for a requirement that names something concrete: a code span, a quoted literal, a
call, a dotted name, a camelCase/PascalCase/snake_case identifier, or a number (`requirements.names_a_concrete_literal`).
A missing claim about general prose ("make it more robust") is recorded UNVERIFIED and never fails the gate, in the
agent and in verdict parsing alike, whatever the model does with the prompt. This prevents unwinnable retries on
vague requirements.

### Enforce with milestones
`generate --from-milestones` under enforce goes through `WorkflowController.execute_milestones`, which delegates to
the same `run_milestones` driver (checked in the source). Each milestone is a unit with no requirement set; only
the plan's integration unit verifies the original requirements. No per-milestone `original_requirements` gate
exists.

### Ledger precedence
Requirements are seeded PENDING at JUDGMENT, the lowest authority, so the verifier's verdict becomes the authoritative state. `ObligationLedger.current` keeps the latest record of same-or-higher authority. `unresolved_terminal_obligations()` skips this kind, because the requirement policy, which knows UNVERIFIED from VIOLATED, aggregates it.

## Decisions to review
1. **Policy defaults** (superseded for production by the final-review corrections below). VIOLATED always blocks. UNKNOWN and UNVERIFIED default to `record`: reported in the result and the run events, not blocking. Blocking by default would change every existing run. Production seals `requirement_unknown_policy: block`. `requirement_unverified_policy` is not sealed, because a behavioural requirement ("make it faster") usually cannot be confirmed from source, and sealing it would turn most production runs into NEEDS_REVIEW. Both fields are SECURITY_AUTHORITY under SEC-009, so a repository cannot relax them.
2. **The verifier may mark a behavioural requirement satisfied.** Its per-id verdict is bound to the exact candidate content and runs only after every deterministic gate passed. "missing" is limited to concrete, literally-named requirements, so the gate stays as narrow as before and cannot burn retries on vague prose. A requirement the verifier cannot confirm is UNVERIFIED.
3. **Accepted human clarifications.** The data model supports them (`REQ-C<n>`, source `clarification`), but Kriya has no clarification producer today; the REPL's clarify step is command routing. None is invented here.
4. **Coverage gaps are not plan errors.** A requirement no subtask maps to stays terminally active. Rejecting the plan would add repair loops without making the requirement any safer.
5. **Enforce adds one verifier call** at the terminal gates, only when `spec_compliance_enabled`. With it disabled, requirements stay PENDING, and the policy decides.

### `kriya fix` has no requirement set (review correction)
`fix` runs the engine with Kriya's own placeholder goal ("Fix compilation/test failure") around the error log.
Deriving a "requirement" from that would contradict this PRD's premise, and under production (unknown=block) it
would block every `fix` whose verifier omitted the id. The flag `run_generation_workflow(requirements_from_goal=False)`
travels through the direct wrap's call arguments to the unit call, so plan digests are untouched, and `fix` sets it.
The other synthesized-goal callers were checked: milestone and enforce units carry no requirement goal, and a
proposal's goal is user-authored. Two tests prove it: the engine derives nothing, and `kriya fix` passes the flag
(mutation: flag not passed, caught).

## Residuals
- **Enforce terminal verifier size.** The enforce `original_requirements` gate sends every file the plan leaves in the
  candidate in one verifier call. For a very large candidate, PRD-016 refuses that call before inference. The
  verdict is then unknown, and under the production profile (unknown=block) the run is not successful. The gap
  message carries the verifier's reason (`[verifier: ...]`), so the cause is visible. Batching the verifier over
  file groups is a possible refinement if live evidence shows it matters.

- **Production effect.** Every production (enforce) run now makes one whole-candidate verifier call at its terminal
  gates, and with unknown=block a missing verdict refuses success. A production-profile brownfield run (demo-03) is
  recommended as part of live verification, so the real effect is seen before VERIFIED.

## Resume effect (disclosed)
The ledger now carries requirement records and `Subtask` has a new field. A structured plan approved before this change hashes differently. A direct checkpoint's effective ledger from before this change has no requirement records, so the resumed run seeds them PENDING.

## Tests (plain runner; you run pytest)
- `tests/test_prd020_requirement_lineage.py`: 24 tests (including the `fix` placeholder-goal pair).
  - Derivation: list and prose, determinism and digest binding, code spans and abbreviations, numbered items and continuations, clarifications.
  - Ledger and policy: the verifier outranks the seed, a later violation is a regression and blocks, policy matrix, the generic aggregator skips the kind, snapshot round trip with idempotent re-seeding, lineage is citation only.
  - A missing claim counts only for a concrete requirement (parsing and the agent: general prose never fails the gate).
  - Verdict parsing, config classification and production seal, the verifier naming a missing REQ by id and text, plan validation rejecting an invented id.
  - Direct end to end through `WorkflowEngine`: a paraphrasing plan (cites REQ-1 only) cannot drop REQ-2/REQ-3; the Architect sees them; the retry is told `REQ-3: <text>`; the run passes only once every requirement is verified. Block policy: refused before apply, nothing written, no Developer retry. Record policy: reported. Resume: the saved plan is reused, the same ids and digest are derived.
  - Enforce end to end through `WorkflowController.execute`: a requirement on no subtask is still judged and blocks the commit.
- `tests/test_prd020_milestone_requirements.py`: 2 tests through the real `generate --from-milestones` CLI: only the integration unit carries the plan's original requirements; with no verdicts and the block policy, the plan does not succeed (`REQUIREMENTS_UNRESOLVED`).
- Updated: `tests/test_workflow_controller_enforce.py` (the terminal-gate matrix gains `original_requirements`; the gate order includes it), `tests/test_failure_reporting.py` (new failure type).
- Mutation checks, each caught:
  - no pre-apply block;
  - requirements seeded at DETERMINISTIC (the verifier could never settle them);
  - the Architect not shown the requirements;
  - the integration unit not given the original goal (2 tests);
  - the enforce gate not counted;
  - plan validation ignoring invented ids;
  - the verifier's missing verdict not failing compliance.

## Live test
`tests/test_live_prd020_024_batch5.py::test_prd020_lineage_survives_model_paraphrase` (see the batch-5 live command in the PRD-024 handover).

## Final review corrections (before pytest/live verification)
1. **Production blocks VIOLATED, NO_VERDICT and CANNOT_CONFIRM_FROM_CODE.** The production profile now seals `requirement_unverified_policy: block` as well as unknown. Decision 1 above no longer holds for production; the non-production default stays `record`.
2. **CANNOT_CONFIRM_FROM_CODE (UNVERIFIED) is never SATISFIED.** It can be closed only by another authoritative verifier's positive evidence for that exact REQ on that exact candidate. Closure is a separate DETERMINISTIC record (`requirement.REQ-n.closure`), never written on the verdict's own id: `ObligationLedger.current` keeps the newest same-or-higher-authority record, so a DETERMINISTIC record there would hide a later VIOLATED verdict about different code. `requirement_outcomes` reports CLOSED_BY_EVIDENCE (distinct from SATISFIED) only when the closure's `evidence_id` equals the current verdict's. VIOLATED and NO_VERDICT are never closed. REQ ids, text, digest and derivation are unchanged.
3. **Two producers, both bound by the user's own words, never a model citation:**
   - **Named tests** (`workflow.close_requirements_with_named_tests`): tests the REQ's own text names (path, file name or stem, e.g. `test_legacy`, `PricingTest`). The candidate must not have written or edited them; they run on the exact candidate the verifier judged, before anything changes it, and must execute (non-zero tests) and pass. Direct/milestone pre-apply boundary (a `requirement.closure` run event) and enforce's terminal gate alike.
   - **Migration gate** (`attempt._close_requirements_by_migration_gate`): a REQ naming both the migration's source and its target, once every current migration obligation is SATISFIED for that candidate. A REQ naming only one side (e.g. how the target library is used) is a different claim and stays UNVERIFIED. It runs on the direct attempt path and at the enforce terminal gate. Production runs are enforce runs, and their subtasks carry no requirement set, so there it closes against the terminal verdict's evidence id, and only when the terminal migration check passed on that same final candidate.
4. **Disclosed consequences under production:**
   - A verifier "missing" claim suppressed because it names a Planner-only identifier, or a "missing" claim about a requirement naming nothing concrete, is recorded UNVERIFIED and now blocks production unless one of the producers closes it. This is the reviewer's rule (CANNOT_CONFIRM blocks without other authoritative evidence), not a regression, but it can end a production run as REQUIREMENTS_UNRESOLVED where it previously passed.
   - A `requirements_unresolved` stop is not retried (already in the non-retryable set): the Developer cannot supply a verdict.
   - There is no runtime (run-verification) producer yet: the run verifier judges the goal as a whole, with no per-REQ binding. A behavioural requirement with neither named tests nor a migration binding blocks production.
5. **Milestone final verification.** Units still commit incrementally. When the integration unit's check fails, the plan is not successful, and:
   - the plan result carries `committed_work_units` and `committed_changes_retained`;
   - `RunRecord.committed_work_units()` derives the same from the record's own commit cycles (`commits[].work_unit` plus the settled result). There is no schema change and no new lifecycle enum;
   - the CLI prints, from the result, "Committed and still applied (not rolled back): M1, M2." and whether the failed unit was applied. A milestone can fail after its own commit (the dependency-drop guard, the artifact registry), and is then named as applied.

   Tested through the real CLI and milestone driver, and on the files on disk.

Tests added (plain runner):
- `test_prd020_requirement_lineage.py`: 41 passed, 17 of them new (including the enforce terminal migration closure). They cover:
  - the production blocking matrix: NO_VERDICT, CANNOT_CONFIRM and VIOLATED;
  - CANNOT_CONFIRM with no other evidence blocks, end to end;
  - closure bound to the exact candidate;
  - VIOLATED and NO_VERDICT never closed;
  - the named-test conditions: unmodified, executed, passing;
  - the direct engine and the enforce terminal gate, each with a passing and a failing named test;
  - the migration binding.
- `test_prd020_milestone_requirements.py`: 5 passed, 3 of them new: the committed units via the CLI, the result and the RunRecord; a milestone failing after its own commit named as applied; only settled COMMITTED cycles counting.
- Mutations, each caught: closure not bound to the candidate, closure closing VIOLATED/UNKNOWN, a modified test accepted, execution not confirmed, production seal dropped, direct closure skipped, enforce closure skipped, migration single-term binding, committed units not reported, unsettled cycles counted.

### demo-03 under the production profile (derived offline; no model)
`derive_requirements(goal.md)` gives:
- REQ-1: the bug description; names literals.
- REQ-2: persist via `driverRepository`; names literals.
- REQ-3: do not change the signature, annotations or other methods; names literals.
- REQ-4: "Do not modify any other file in the repository." Names no literal, so a verifier "missing" claim cannot count, and "unverifiable" is possible.

Resolved by the closure-and-push review: REQ-4 is decided by deterministic mutation-scope evidence (below).

## Closure review: mutation-scope evidence
"Do not modify any other file" must not stay CANNOT_CONFIRM when Kriya can prove it from its own write record.
- **Recognition** (`is_mutation_scope_requirement`): a closed pattern matched against the whole requirement ("do not / never / must not" + "modify / change / edit / touch / alter" + "any other file(s)", optionally "in the repository/project/..."). REQ-3's "any other method in this class", "keep the change small" and a conditional ("... unless ...") do not match. No model, no fuzzy matching.
- **Authorized paths** (`mutation_path_roles`, corrected by the final mutation-scope review): only paths the goal's own words establish as change *targets* - never every path mentioned. A named path is exactly a path tracked at the run's base (no basename/stem match), or an untracked file-shaped path under a creation verb. Its role at each mention is read from the nearest cue word before it in its own clause (code spans skipped, `;` ends a clause), from closed vocabularies, no model:
  - mutation verb (modify/change/edit/fix/update/patch/rewrite/refactor/implement/correct/adjust/alter/amend/add/remove/delete/rename/replace/create/write) → target (`Modify src/A.java and src/B.java` → both);
  - reference word (compare/see/refer/reference/read/consult/mirror/follow/inspect/use/look/like/according/based/similar/against/per/example/template) → reference, never writable (`Compare it with src/B.java`);
  - a negated mutation verb (`do not modify src/B.java`) → forbidden;
  - a relational word (with/next/beside/alongside/near/into/onto) under a mutation verb (`Replace A with B`, `Create N next to A`), no cue at all, or different roles at different mentions → ambiguous.
  
  Any ambiguous path leaves the requirement unresolved (production blocks); no target at all = "other" has no referent (unresolved). Planner/Architect/Developer file lists are never inputs. demo-03: REQ-1's "Fix ... (in `.../DefaultDriverService.java`)" → exactly that file, nothing ambiguous. Evidence also records `reference_paths`; attempts record forbidden/ambiguous paths.
- **Actual paths** (`workflow.mutation_scope_evidence`): the candidate's own paths that git reports changed against the base (`git diff --relative <base>` + untracked, `.kriya/` and ignored files excluded) plus the run's committed path history - every settled COMMITTED cycle of the RunRecord, read from that transaction's commit evidence (byte state before != after). Anything else git reports is `foreign_paths`. The candidate must be at the run's base revision (milestones never git-commit, so it is), otherwise evidence is unavailable.
- **Decision** (`close_mutation_scope_requirements`), bound to the verdict's `evidence_id`:
  - any actual path outside the set: a DETERMINISTIC VIOLATED record under `requirement.REQ-n.closure`; `requirement_outcomes` reports VIOLATED whatever the verifier said (new: counter-evidence);
  - otherwise foreign changes, unavailable evidence, or no verdict to bind to: not closed;
  - otherwise, with an UNVERIFIED or SATISFIED verdict: CLOSED_BY_EVIDENCE (a SATISFIED verdict is reported as closed by the deterministic proof, so the outcome names what proved it). VIOLATED/UNKNOWN verdicts are never closed.
  - Evidence recorded: requirement id, `kind: MUTATION_SCOPE`, authorized paths, actual paths, out-of-scope paths, foreign paths, committed history, run id, base and candidate revision, verdict evidence id. Results expose it as `requirements.evidence`.
- **Where:** the direct/milestone pre-apply boundary (in the `requirement.closure` event, candidate paths = `all_files_written`) and the enforce terminal `original_requirements` gate (candidate paths = every planned path).
- **Disclosed:** an untracked build artifact that `.gitignore` does not cover is a foreign change, so the requirement stays unresolved rather than closing (fail closed). A goal that names a file only as context (not as the change target) still authorizes it: the referent is the user's literal words.

Tests: `tests/test_prd020_mutation_scope.py`, 34 passed (plain runner; 10 added by the target-only correction: one and two explicit targets, reference not allowed, modifying a referenced path VIOLATED, ambiguous roles unresolved incl. relational words, negated/created paths, the demo-03 goal allows only DefaultDriverService.java; the enforce case with the Planner's extra file stays VIOLATED):
- recognition and the exact-path referent;
- exact one-file scope closes (UNVERIFIED and SATISFIED verdicts);
- a second changed file is VIOLATED and blocks under every policy;
- a Planner-selected file with no goal-named file cannot close;
- stale evidence (closure and counter-evidence) never carries to a later candidate;
- foreign changes / unavailable evidence / no verdict stay unresolved;
- the direct engine on a real git repo (close; violate with nothing applied);
- the enforce terminal gate (close; Planner's second file violated; no referent unresolved);
- the real CLI milestone driver: M2's earlier commit to another file makes the integration check VIOLATED although the integration candidate itself only touched the authorized file; both milestones in scope closes.

Mutations, each caught: every named path authorized (the old rule), ambiguity ignored, reference cue as target, relational word ignored, no-cue path as target, negation ignored, actual paths used as authorized, evidence-id binding dropped, foreign check dropped, VIOLATED override dropped, committed history dropped, enforce call site dropped, direct call site dropped.
