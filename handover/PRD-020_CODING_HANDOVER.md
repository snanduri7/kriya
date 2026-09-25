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
1. **Policy defaults.** VIOLATED always blocks. UNKNOWN and UNVERIFIED default to `record`: reported in the result and the run events, not blocking. Blocking by default would change every existing run. Production seals `requirement_unknown_policy: block`. `requirement_unverified_policy` is not sealed, because a behavioural requirement ("make it faster") usually cannot be confirmed from source, and sealing it would turn most production runs into NEEDS_REVIEW. Both fields are SECURITY_AUTHORITY under SEC-009, so a repository cannot relax them.
2. **The verifier may mark a behavioural requirement satisfied.** Its per-id verdict is bound to the exact candidate content and runs only after every deterministic gate passed. "missing" is limited to concrete, literally-named requirements, so the gate stays as narrow as before and cannot burn retries on vague prose. A requirement the verifier cannot confirm is UNVERIFIED.
3. **Accepted human clarifications.** The data model supports them (`REQ-C<n>`, source `clarification`), but Kriya has no clarification producer today; the REPL's clarify step is command routing. None is invented here.
4. **Coverage gaps are not plan errors.** A requirement no subtask maps to stays terminally active. Rejecting the plan would add repair loops without making the requirement any safer.
5. **Enforce adds one verifier call** at the terminal gates, only when `spec_compliance_enabled`. With it disabled, requirements stay PENDING, and the policy decides.

## Residuals
- **Enforce terminal verifier size.** The enforce `original_requirements` gate sends every file the plan leaves in the
  candidate in one verifier call. For a very large candidate, PRD-016 refuses that call before inference. The
  verdict is then unknown, and under the production profile (unknown=block) the run is not successful. The gap
  message carries the verifier's reason (`[verifier: ...]`), so the cause is visible. Batching the verifier over
  file groups is a possible refinement if live evidence shows it matters.

## Resume effect (disclosed)
The ledger now carries requirement records and `Subtask` has a new field. A structured plan approved before this change hashes differently. A direct checkpoint's effective ledger from before this change has no requirement records, so the resumed run seeds them PENDING.

## Tests (plain runner; you run pytest)
- `tests/test_prd020_requirement_lineage.py`: 22 tests.
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
