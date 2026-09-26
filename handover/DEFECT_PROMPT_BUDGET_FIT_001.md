# PROMPT-BUDGET-FIT-001: Planner and final-Reviewer sections are sized without reserving the rest of the request

## Status
OPEN, P2. Found on 2026-09-27 while triaging the first Batch 6 live run. Not fixed: the live failures were a fixture defect (Case A of the user's triage: the live fixture ran at 8K with no qualification record; fixed in 3e6f273), and under Case A production budgeting is not changed. This record keeps the gap visible.

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
