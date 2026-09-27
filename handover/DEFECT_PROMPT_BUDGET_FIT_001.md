# PROMPT-BUDGET-FIT-001: Planner and final-Reviewer sections are sized without reserving the rest of the request

## Status
Umbrella OPEN (user decision, 2026-09-27), split into three findings:
- **001A** (OPEN, P2): the Planner graph context is sized without reserving the rest of the Planner request.
- **001B** (OPEN, P2): Reviewer file batches are sized without reserving the system prompt, header, diff and evidence.
- **001C** (P1, FIXED in 8600e2d, **VERIFIED_BY_PYTEST**: the 001C subset 1856 and full suite 6245 passed; NOT_LIVE_EXERCISED, because the qualified 32K profile does not trigger the refusal, and the user accepted pytest verification): a final-Reviewer refusal after the candidate was applied escaped as a raw exception.

001A and 001B go to a dedicated prompt-fit follow-up; PRD-016 is not redesigned in Batch 6. Their fix budgets each variable section from `effective prompt capacity - mandatory fixed prompt cost - safety reserve`, and never by raising context limits.

Original finding: Found on 2026-09-27 while triaging the first Batch 6 live run. Not fixed: the live failures were a fixture defect (Case A of the user's triage: the live fixture ran at 8K with no qualification record; fixed in 3e6f273), and under Case A production budgeting is not changed. This record keeps the gap visible.

## What happens
Two prompt sections are sized as a share of the Developer-shaped allocation window (`context_budget.allocation_window`), which assumes about 10% of the window for the system prompt and task text. Neither reserves for the rest of its own request:

| Request | Section and budget | Not reserved |
|---|---|---|
| Planner (direct; `workflow.py` step 1.5, `_reserve_graph_context_budget`) | graph context: 0.60 of the window minus the conventions text | the Planner system prompt (10,873 chars), `repo_context`, the error prefix, the owner, REQ, tool and grounding blocks, the fenced reference (appended after the budget is computed) |
| Final Reviewer (`workflow.py` REVIEW; `review_batch_budget`) | file batch: 0.75 of the window | the goal header with the candidate diff and gate/ownership evidence, the Reviewer system prompt (6,139 chars) |

The pre-approval Reviewer uses the same batch budget. The enforce structured Planner's fenced reference (85a5bfd) is not reserved either.

Where the rest is small next to the window, this is harmless: at the qualified 32K window with a 2.11 bytes/token floor the requests fit with room to spare. Where it is not (a model served at 8K, or an unqualified runtime counted at the 2.5 default byte bound), PRD-016 refuses the request before inference, which is the correct backstop but loses the call:
- a Planner refusal ends the run with an exception (`<trace_id>.exception`);
- a final-Reviewer refusal happens after the candidate is applied, so an applied, successful run is reported as an exception rather than its success result.

## Evidence (offline, no model call)
A harness that runs `run_generation_workflow` with canned responses, the deterministic hashing embedder and an 8K window reproduced both refusals:
- PRD-027 Java fixture: the Planner request needs about 7066 prompt tokens against 8192 − 1024 (live run: 7089). At 510fd98 the same run sent no graph section at all: retrieval returned 0 hits, the defects PRD-027 fixed (8aa6d26). So PRD-027 made this reachable; it did not create it.
- PRD-028 ledger fixture (160 methods): batch 2477 + diff 291 + system prompt ~1535 allocator tokens + evidence exceed the 4300-token allocation window (live run: 7212 prompt tokens).

## Fix direction (for when it is scheduled)
Size each section against the room its own request has left: `allocation_window(config, agent binding)` minus the agent's system prompt and every other section of that request, capped by the existing share. When the remainder cannot hold the section, rebuild it smaller (`build_code_context` at the remainder, `build_review_batches` at the remainder) or leave it out and record the omission; never force it in with a floor. Separately, a refusal of the advisory final review must not turn an applied success into an exception. Needs a deterministic regression test at an 8K window with no qualification record that fails before the fix, and a mutation check of the reservation.

## 001C fix (8600e2d)
- The final review catches `ContextBudgetUnsatisfiableError` and records `review.refused` (AUTHORITATIVE).
- The run ends as `failure_category: final_review_refused`, and is never SUCCESS: `final_workflow_quality_passed()` is false while `state.final_review_refusal` is set. There is no retry and no rollback.
- The result keeps what already happened:
  - the gate results;
  - `final_review_refusal`, with `reason_code`, `detail`, `candidate_applied`, `committed_work_units` (read from the RunRecord) and `rolled_back: false`.
- An applied candidate leaves no resume checkpoint.
- Enforce: the subtask fails with the refusal's reason code, and nothing reaches the live workspace.
- Milestone: the plan fails, and it reports the units it leaves committed.
- The `generate` and `fix` CLI output no longer claims the files were not applied.
- Tests are in `tests/test_prompt_budget_fit_001c.py` (6 tests). Four of the five scenario tests fail before the fix; the control passes. 9 mutations were run and all were killed.
