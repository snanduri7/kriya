# PROMPT-BUDGET-FIT-001: Planner and final-Reviewer sections are sized without reserving the rest of the request

## Status
Umbrella OPEN (user decision, 2026-09-27), split into three findings:
- **001A** (OPEN, P2): the Planner graph context is sized without reserving the rest of the Planner request.
- **001B** (OPEN, P2): Reviewer file batches are sized without reserving the system prompt, header, diff and evidence.
- **001C** (P1, FIXED in 8600e2d, **VERIFIED** (user closure 2026-09-27; by pytest: the 001C subset 1856 and full suite 6245 passed; NOT_LIVE_EXERCISED, because the qualified 32K profile does not trigger the refusal, and the user accepted pytest verification): a final-Reviewer refusal after the candidate was applied escaped as a raw exception.

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

## 001A + 001B fix (Backlog 6.5, 2026-09-27): FIXED, awaiting the user's pytest run
**One primitive** (`kriya/workflow/context_budget.py`):

- **`request_capacity(config, binding, role=, output_tokens=)`**: the prompt room of one request, in the dispatch check's own units.
  - Room = served or requested window − that request's preferred output (at most half the window, with the reasoning floor) − framing − the dispatch safety margin.
  - The count uses the dispatch counter: an exact tokenizer, else the qualified ratios, else the defaults, non-ASCII included.
  - `allocation_window` (Developer-shaped) is now this with the Developer's output.
- **`fit_variable_section(capacity, fixed_texts, build)`**: the section gets `capacity − mandatory fixed text`.
  - It is built at that room, re-measured, and rebuilt proportionally smaller at most 3 times.
  - If it still doesn't fit, it is left out. It is never forced in with a floor.
  - The fixed text is never trimmed, so a genuinely oversized request is still refused before inference.

**Applied to:**
- **Direct Planner (001A), `fit_planner_request`.**
  - Fits the graph context first, then the fenced learned reference, trimmed at whole `[Source:]` entries. Repository evidence outranks untrusted text.
  - The Planner's own binding and `planner_max_tokens` are reserved.
  - The rebuild reuses `build_code_context_package` at the room, unchanged; the certification identity is untouched.
  - The retrieval-time graph budget is `min(PRD-016 graph pool, Planner room with what is known so far)`. The pool stays the upper bound because the section is shared with the Architect, so at 32K the Planner prompt is byte-identical (tested).
- **Enforce structured Planner (001A).** The reference is refitted on the first request and on every repair round; before, a fence built once was re-appended to a longer request. A reduction is recorded as a DecisionLedger `context.request_fit`.
- **Reviewer (001B), `review_batches_for_request`**, for pre-approval, final and `kriya review`.
  - Reserves the reviewer binding and its output, the system prompt actually sent (including the rejected-candidate override), the header (goal, candidate diff, gate and ownership evidence) and a batch-label bound. The fixed 0.75 share is gone.
  - With no room at all, one request carries `REVIEW_FILES_OMITTED_NOTE`. Request count is at most the file count.
  - `kriya review`'s structured Java path is taken only for a whole, untruncated file.
- Reductions are `context.request_fit` run events.

**What an 8K run gets** (no qualification record, 2.5 bytes/token; planner_max_tokens 8192 and max_tokens 4096 reserve half the window):
- The Planner system prompt alone (≈4,350 tokens) exceeds its preferred room (3,816). The Planner therefore plans without graph context or reference, recorded, and dispatches with output reduced.
- Before the fix the same request was refused: 9,637 prompt tokens in the harness, with the run ending in an exception.
- At 16K the graph is rebuilt to the room. At 32K nothing changes.

**001C is unchanged.** Its tests force the refusal through a 512-token stand-in, not batch overflow, so they are not made vacuous; re-run green. An oversized header still ends `final_review_refused` (tested end to end).

**Tests:** `tests/test_prompt_budget_fit_001ab.py`, 23 tests:
- primitive units: room math, no room, non-ASCII rebuild, bounded builds, whole-entry trim, byte-identical when it fits, fence never cut, request count bounded, no-room note, reviewer binding, output and reasoning reserve;
- end to end through the real engine and dispatch check on a hashing-embedder index of the PRD-027 Java fixture:
  - 8K baseline (positive control);
  - 8K Planner omits and sends;
  - 16K Planner rebuild within capacity;
  - 32K Planner unchanged;
  - 8K final review of a failing candidate;
  - 8K pre-approval cut to room;
  - 16K final cut to room;
  - 32K whole files;
  - an oversized header ends typed, and the primitive plus `LLMClient` refuse;
- enforce: refit on the first and the repair request; no room leaves it out.

**Pre-fix controls** (the same file run in a worktree at a828a67): 17 fail and 3 pass. The 3 are the baseline and the two 32K "unchanged" tests, which must pass on both trees. Probes at a828a67:
- the 8K Planner request refused (9,637 tokens);
- the 8K 50–80-method failing candidate ends `final_review_refused`.

**Mutations: 15 run, all killed.** They covered:
- ignoring the fixed text;
- accepting an oversized build;
- no entry trim;
- the empty reference not reported omitted;
- no omitted-files note;
- no output reserve;
- no reasoning floor;
- ignoring the reviewer binding;
- the reviewer fixed text dropped (unit, final site, pre-approval site, CLI);
- the Planner fit bypassed;
- the enforce refit bypassed.

**Test changes:**
- `test_prd016_allocation` asserted the removed `review_batch_budget` share; it now asserts that every batch request (system, header, batch) fits.
- `test_review_command`'s multi-batch test used a 1500-token window, which cannot hold the ~2,456-token Reviewer system prompt. It now uses a 4000-token window with room for exactly one of the two files.

## Findings from this slice (recorded, not in 6.5 scope)
- **DEVELOPER-PROMPT-FIT-001 (P2, new).** At 8K with no reference, Developer requests reach ≈5,600 prompt tokens against 3,816 of preferred room. They dispatch only because the dispatch check reduces output. The PRD-016 allocator's "0.10 for system prompt, task and directives" assumption is false at small windows. Same class as 001A/B, for the Developer; not fixed here.
- **Architect at 8K.** 1,817 prompt tokens in the harness without reference text. With a 10-entry learned reference (the Architect carries the whole fenced reference in `convention_prompt`), no request of any role is refused. `test_learned_reference_never_makes_a_developer_request_fail_at_8k` asserts that across all roles. Nothing to fix.
- **Milestone coverage.** Milestone units run the same direct Planner and Reviewer path (`run_generation_workflow` with a WorkUnitInvocation), so 001A/B apply to every unit unchanged. The milestone terminal semantics of a refused final review are 001C's existing milestone test (`tests/test_prompt_budget_fit_001c.py`), re-run green. No separate 8K milestone run was added.
- **PROMPT-FIT-ROLE-CHAIN-001 (P2, new).** Planner and Reviewer requests are sized for the role's first candidate: its `role_llm`, else the primary. A role-chain fallback with a smaller window receives the same request, is refused by the dispatch check if it cannot hold it, and escalation continues. `call_with_escalation` accepts a per-candidate prompt, so per-candidate sizing is the follow-up. Not in 6.5 scope.
- **Learned reference at the Developer (own regression, a314d45/f136d7e).** With learned text present, 8K Developer requests are refused. Fixed in its own commit ("Fix (my a314d45)"); see handover/BACKLOG_6_5_SUMMARY.md.

## Backlog 6.6 follow-ups (2026-09-27)
- **PROMPT-FIT-ROLE-CHAIN-001: FIXED in 7d7b0c0**, awaiting the user's pytest run. Planner and Reviewer requests are fitted per role candidate (`CandidatePrompts`, `review_requests`).
- **DEVELOPER-PROMPT-FIT-001: FIXED in cfe9590**, awaiting the user's pytest run. Every Developer request is fitted into the capacity of the binding it is sent to (`fit_developer_request`).
- **ARCHITECT-PROMPT-FIT-001 (P2, new, recorded only).** The Architect's unfitted reference is refused at 16K with 30 entries.

Details, evidence and the remaining findings: `handover/BACKLOG_6_6_SUMMARY.md`.

> **Status of open items:** the canonical registry `handover/BACKLOG_REGISTRY.csv` (2026-09-27) is authoritative for every open or deferred item named here. Its rows replace the former tracker rows.
