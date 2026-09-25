# PRD-023 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 5: PRD-020 to PRD-024, one pytest stop for the whole batch).

## Source identity
- Base revision: PRD-022.
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Derived contract change classification and escalation (instruction: `tasks/PRD-023_Derived_Contract_Change_Classification_and_Escalation.md`).

New module: `kriya/workflow/contract_classification.py`.

The existing detector, `file_resolution.find_brownfield_public_api_changes`, is unchanged and is still the only source of contract-change evidence. It reports every established public signature a candidate removed or reshaped while other files call it, after dropping what a DIRECT authorization covers (CORR-016). What changed is that every change is now classified before anything else is considered.

### 1. Statuses and their evidence
| Status | Evidence required | Blocking | Escalation |
|---|---|---|---|
| AUTHORIZED_DIRECT | a DIRECT authorization of this subtask covers the owner, symbol and category (found as the difference between the detector with and without authorizations) | no | n/a |
| AUTHORIZED_HUMAN | a human-approved, revision-bound authorization covers it | no | n/a |
| UNAUTHORIZED | any of: the symbol is gone while callers still name it (even an edited caller); a caller was left on the old shape; the goal authorizes no contract change in this run | yes | never |
| POTENTIALLY_DERIVED | every caller co-updated **and** a supported relationship links the owner to a contract a DIRECT authorization of this run changes: the owner references it, or a co-updated caller maps one to the other (references both) | yes | may be offered |
| INDETERMINATE | every caller co-updated and the run authorizes a contract change elsewhere, but no supported relationship links this owner to it | yes | may be offered |

`run_authorizations` are every DIRECT authorization of the run, not only this subtask's, because the upstream change usually belongs to an earlier subtask. The real PRV-08 case classifies as POTENTIALLY_DERIVED: `CustomerSummary` gains `region` because `CustomerRecord` did, mapped by `SummaryService`. Under the default policy it is still rejected, so the CORR-016 characterization is unchanged.

### 2. Clear unauthorized changes stay blocking
UNAUTHORIZED is never offered to anyone. A mixed candidate proves it: the approval prompt lists only the derived change, and the removed `Old.legacy()` still blocks after the approval.

### 3. Escalation only for evidence-backed cases, only by policy
`autonomy.contract_change_escalation` sets the policy:
- `deny` (the default): never ask; every classified change stays a violation.
- `human`: POTENTIALLY_DERIVED and INDETERMINATE changes are offered to the approval callback, with the evidence (relationship, updated callers). This needs a human-in-the-loop run with a callback. Otherwise the change stays blocked with `CONTRACT_ESCALATION_UNAVAILABLE`, which is the autonomous fail-closed case.
- A refusal leaves it blocked (`CONTRACT_ESCALATION_DECLINED`).

The policy is SECURITY_AUTHORITY under SEC-009. Escalation happens at the per-attempt pre-write gate (`attempt._classify_and_escalate_contract_changes`), before API-contract recovery would start.

### 4. What an approval creates
`contract_authority.human_contract_authorization` is the only other minting site. It stays in `contract_authority.py`, so the CORR-016 structural rule "constructed only in its own module" still holds, and `derive_direct_contract_authorizations` still has exactly one call in `attempt.py`.

The record is a `ContractEvolutionAuthorization`:
- provenance `HUMAN`, authority `HUMAN_APPROVED`;
- exactly one owner, symbol and change category;
- `legal_scope` {owner, subtask};
- the plan revision (in the id and the record);
- the full classification as evidence.

It authorizes that change in later detector calls of the same subtask: the pre-write gate and the terminal re-check. Model prose never creates one.

### 5. Persisted
- Each classification is a `CONTRACT_CHANGE_CLASSIFICATION` obligation (DETERMINISTIC authority for authorized/unauthorized, GROUNDED for derived/indeterminate; never terminal).
- The failure diagnostics carry `contract_classifications` and the escalation reason code.
- An approval is a `contract.human_authorization` run event.

## Decisions to review
1. **Default `deny`.** No run changes behaviour until an operator opts in.
2. **Two supported relationships only** (direct reference, and a co-updated mapper). Anything else is INDETERMINATE rather than guessed.
3. **Approval is all-or-nothing per prompt** (every offered change of that attempt). A partial approval UI does not exist.

## Tests (plain runner; you run pytest)
`tests/test_prd023_contract_classification.py`, 10 passed:
- one case per status:
  - a removed signature with an active, even edited, caller;
  - direct authorization;
  - the ambiguous downstream migration (PRV-08 shape);
  - an unsupported relationship;
  - callers left on the old shape, and no authorized change at all;
- the default policy never asks;
- autonomous mode and a missing callback fail closed;
- a declined escalation stays blocked;
- an approval creates a revision-bound record for only the offered changes, while the clear violation still blocks;
- end to end through `run_attempt`: `deny` rejects with the classification in diagnostics; `human` plus approval lets the candidate through, and the terminal re-check with that authorization finds nothing.

Mutation checks, each caught:
- the removal rule dropped;
- UNAUTHORIZED made escalatable;
- escalation without human-in-the-loop;
- the mapper relationship dropped;
- the stale-caller rule dropped;
- the policy ignored.

Regression, plain runner, all green:
- `test_corr016_planner_authority_gate` 9/0;
- `test_control_contracts` 40/0;
- `test_corr018_general_case_closure` 33/0;
- `test_semantic_region_authority` 37/0;
- `test_control_contract_persistence` 3/0;
- `test_repair_contract` 13/0;
- config/authority green.

## Live test
NOT APPLICABLE, with evidence:
- Nothing any model sees changed. The classification runs on the detector's deterministic output.
- The escalation prompt goes to a human approver, not an LLM.
- The Developer, Reviewer and Planner prompts are untouched.

## Residuals
- The terminal re-check honours human authorizations, but a terminal-only violation, i.e. one first seen at terminal regression, is not escalated there. The pre-write gate sees every candidate first.
