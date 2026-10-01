# PRD-021 Coding Agent Handover

## Status
VERIFIED at `ff3e6c9` (batch 5). Focused batch-5 pytest 1962 passed at `023a9cd` (the earlier focused run's 1 failure, an enforce test fixture whose bare MagicMock kernel enabled the PRD-020 verifier, fixed in `7c9da46` together with an unbound `requirement_closure_attempts` on early-stop enforce runs); full `.venv/bin/pytest` 5829 passed, 1 failed at `bcac161` (same fixture cause in `tests/test_workflow_controller.py`, fixed in `ff3e6c9`, module re-run 32/0 under pytest); live `tests/test_live_prd020_024_batch5.py` 5/5 passed, evidence in `handover/evidence/BATCH5/user-live/`; demo-03 production-profile run PASS (REQ-4 closed_by_evidence via MUTATION_SCOPE, authorized = actual = DefaultDriverService.java only, all 4 REQs resolved, all terminal gates passed, 53/53 tests, verifier 1 call 3.19s 1685/86 tokens, baseline source captured), evidence in `handover/evidence/BATCH5/demo03-production/`. Doctor `--production` was PRODUCTION_READY=false only because fallback qwen3.6:35b-a3b-q4_K_M is NOT_QUALIFIED (hidden reasoning exhausts case budgets); the run never needed it.

## Source identity
- Base revision: PRD-020 (`0cf643f`, `b51758a`).
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Plan-time grounded existing-responsibility ownership (instruction: `tasks/PRD-021_Plan-Time_Grounded_Existing-Responsibility_Ownership.md`).

New module: `kriya/workflow/ownership_findings.py`.

### 1. Current behaviour, proved first and preserved
`prefer_existing_artifact_owners` is pinned by tests:
- the exact-basename tier (two or more tokens);
- the token-containment tier;
- the explicit "create a new ..." exemption.

**Pre-fix case (reproduced).** With `src/order_validator.py` in the repository and the goal "Reject orders whose quantity exceeds the stock level during checkout validation", these planned files all pass as new owners: `src/order_rules.py`, `src/order_validation.py`, `src/stock_checker.py`. Nothing deterministic connects them to `order_validator.py`.

The module runs only on files that are still new after these rules, so the deterministic rules keep precedence by construction.

### 2. Grounded evidence, no second index
`grounded_owner_candidates(workspace, goal)` works over the bounded, goal-ranked candidate set that authoritative planning already uses (`_authoritative_planner_extension_candidates`). It combines two sources:
- that set's graph edges (`build_planning_structural_evidence`);
- an in-memory `DependencyGraph` over exactly those files, for member symbols.

This is the same parser and the same ephemeral approach as planning's structural evidence. The persisted index is not read, and no new index is created.

A file is a candidate only when the goal names its responsibility:
- the role word of its name (`OrderValidator` → *valid*, `stock_checker` → *check*), or
- a member verb beyond its own name (`validate()`);

plus at least one more shared term.

Negative tests show what does not qualify:
- a shared domain noun alone (*order* in `order_repository.py`, `order.py`);
- two goal nouns in a name (`order_stock_repository.py` for "Report order stock levels").

`find_ownership_findings` produces one typed `OwnershipFinding` per planned new file per overlapping candidate. It needs a shared name term, or the file's work text naming the candidate's responsibility. Each finding carries:
- planned path and candidate owner;
- subtask;
- symbols;
- relationships (graph: which planned or touched files reference the owner);
- match basis (`name:`, `role:`, `members:`, `graph:referenced_by_planned_file`);
- confidence (`high` with two or more strong bases, otherwise `medium`);
- classification `GROUNDED`, a suspicion and never deterministic equivalence.

### 3–4. Status: model prose can only acknowledge
`settle_findings` rules:
- A finding starts UNRESOLVED.
- A Planner or Architect `ownership_justification` moves it only to ACKNOWLEDGED. The justification is structured: `Subtask.ownership_justification` on enforce, or a key of the Architect's JSON file-list block on the direct path. It is never regexed out of prose.
- SATISFIED requires one of:
  - goal intent (`_explicitly_requests_new_artifact`, the rule the deterministic tier already trusts);
  - deterministic repository evidence (the plan deletes the candidate owner, i.e. a migration);
  - a human approval by finding id.

  No autonomous human-approval producer exists. Approving a whole diff in HITL mode is deliberately not treated as approving a finding.

### 5. Injected, never denying
- **Before planning:** the candidates go into the Planner request (enforce) and into the Planner and Architect prompts (direct, on TASK/ENHANCEMENT routes, where the deterministic owner rules apply). They are marked "a suspicion, not a rule", with where to put a justification.
- **After planning:** findings go to the Developer. On enforce they appear in the creating subtask's context; on direct they are in the generation prompt. They carry the advice to reuse or delegate to the existing owner.
- A finding is never a `validate_plan` reason code, never a repair trigger and never a gate. Tests show the direct run passes, and an enforce run with an ACKNOWLEDGED finding succeeds.

### 6. Persisted
- Ledger: each finding is a `GROUNDED_OWNERSHIP` obligation (GROUNDED authority, `terminal_required=False`; UNRESOLVED→PENDING, ACKNOWLEDGED→INDETERMINATE, SATISFIED→SATISFIED), with the full finding as evidence and a stable id `ownership.<planned>::<owner>`.
- Direct runs: `ownership.finding` run events and the result's `ownership_findings`.
- `state.ownership_findings` carries them to review (PRD-022).

## Decisions to review
1. **The Planner sees the candidates before planning, and there is no advisory re-plan round.** On enforce the Developer can only write planned files, so a finding shown only after planning cannot change ownership. The pre-plan block surfaces it with no extra model call, and anything the plan still creates is recorded and shown downstream.
2. **The direct path computes candidates only on TASK/ENHANCEMENT routes**, the routes `prefer_existing_artifact_owners` already runs on, so both kinds of owner evidence share one scope.
3. **Stemming is deliberately simple** (suffix stripping; validator/validate/validation → *valid*) and runs only over name, member and goal words. It is not a semantic model, and there is no new embedding stack.
4. **Plan revisions after approval** (owner-recovery prerequisite revisions) do not recompute findings. The findings describe the plan as approved.

## Tests (plain runner; you run pytest)
`tests/test_prd021_grounded_ownership.py`, 11 passed:
- current resolution pinned, and the pre-fix acceptance reproduced;
- candidate precision (responsibility required; domain nouns alone and two-noun names excluded; graph `referenced_by`);
- findings for the three duplicates and none for an unrelated new file, with basis and relationships;
- a justification only acknowledges;
- goal intent, owner removal and human approval satisfy;
- grounded, non-terminal ledger records with a stable id;
- the structured justification is parsed, prose is not;
- direct end to end through `WorkflowEngine`: the Planner and Architect see the owner, the finding reaches the Developer, the run passes;
- enforce end to end through `WorkflowController.execute`: the Planner request shows the owner, the subtask context carries an ACKNOWLEDGED finding with its justification, and the run succeeds.

Mutation checks, each caught:
- the second-term requirement dropped;
- the responsibility requirement dropped;
- a justification satisfying;
- findings not in the enforce subtask context;
- findings not in the direct generation prompt;
- candidates not in the Planner prompt.

## Live test
`tests/test_live_prd020_024_batch5.py::test_prd021_planner_sees_the_grounded_owner`: a real local Planner/Architect run on the order-validator fixture. It records whether the model extended the owner or created a new file, and the finding or justification persisted either way.

## Residuals
- Candidates are drawn from the bounded (100-file) goal-ranked set. An owner ranked outside it is not seen, which is the same bound planning's structural evidence already has.
- Member extraction depends on the graph parser's languages (Python, Java, Ruby, XML). Other languages rely on name evidence only.
