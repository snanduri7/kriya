# PRD-016 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 3: PRD-013 to PRD-016, one pytest stop for the whole batch).

## Scope
Tokenizer-aware allocation and dispatch budgeting, with the bounded adaptive budget policy
(`kriya/core/token_budget.py`, `kriya/workflow/context_budget.py`, `kriya/core/llm.py`).

### 1. Allocation agrees with dispatch (CTX-001 reconciliation, the blocker found in review)
Before this, every prompt builder took a fraction of the raw `context_window` in `len//4` units: graph 0.75, siblings
0.15, retry evidence 1.5 chars/token, with task, design and system text unbudgeted on top. A fully allocated
Developer prompt counted as about 48K tokens against a 32K window. The dispatch check refused it, and before that
check existed the server silently truncated it.

- `context_budget.prompt_allocation_window(window, output, bytes_per_token)` is the room a two-message prompt may use:
  - the served window,
  - minus the output budget (at most half the window),
  - minus framing and a 256-token safety margin,
  - converted to allocator units with the dispatch counter's ratio.
- `allocation_window(cfg, binding)` mirrors exactly what LLMClient checks for that call: the served `num_ctx`, the
  client's `max_tokens` (12288 for reasoning models), and the qualified ratio when the runtime is qualified.
- Shares of that window:
  - graph pool 0.60 (skills, learned knowledge, retrieved code, known-target and member source; the design and plan
    are now reserved first);
  - retry evidence 0.15;
  - siblings 0.15;
  - investigation evidence capped at 0.10 (it was unbounded; whole items are kept and the rest are named);
  - review batches 0.75.
- Every builder routes through it: attempt graph/known-target/member-hint/retry/sibling on primary and fallback
  models, the first-attempt convention budget, both Reviewer stages, and `kriya review`.
- Section floors are capped at 15% of the window. In an 8K window with a 4K output, the old absolute floors alone
  made a prompt larger than the window.
- Defect found while measuring (in the frozen CTX-001 code): `build_code_context` stopped degrading at "signatures"
  and rendered everything. Twelve large classes produced 210K characters against a 4.7K-token budget. Files that do
  not fit even as signatures are now left out, lowest-ranked first (related before matched). They are recorded as
  `budget_exhausted` and named in the prompt. Output within budget is unchanged.
- Measured with the real DeveloperAgent and LLMClient, a fully allocated prompt:

  | Window | max_tokens | Prompt tokens | Outcome |
  |---|---|---|---|
  | 32768 | 16384 | 13.9K | whole output kept |
  | 65536 | 16384 | 36.0K | whole output kept |
  | 16384 | 4096 | 9.6K | whole output kept |
  | 16384 | 16384 | fits | output trimmed (it asks for the whole window) |
  | 8192 | 4096 | fits | output trimmed |

  Every legacy composition is refused.
- **Cost, stated plainly:** the retrieved context is smaller than before. At 32K/16384 with the default ratio the
  whole prompt is about 40K characters, against about 166K characters of allocation before (which the server could
  not hold).
  - Qualifying the runtime raises the ratio by about 19%.
  - A qualified larger tier (below) serves requests whose mandatory content needs more.
  - Lowering `max_tokens` also returns room.

### 2. Adaptive budget (the user's CTX-001/PRD-016 decision)
- `plan_dispatch` treats the served window and `max_tokens` as PREFERRED values.
  - Adaptive (default): it selects the SMALLEST offered larger tier that holds the prompt, the needed output, the
    reasoning allowance and the 256-token margin.
  - Strict: it never exceeds the preferred values.
- Tiers come from `model_qualification.offered_context_tiers`:
  - A current record for the exact runtime at that `num_ctx` (`kriya model qualify --context-window N`) that passes
    every case the model's roles need, plus the new near-window `context_capacity` case.
  - Or, while no qualification data exists for that size, `llm.context_policy.declared_safe_context_tiers`. A
    NOT_QUALIFIED or STALE record overrides the declaration.
  - Never above `max_context_tokens` or the model's trained length.
  - Only on an exact Ollama runtime. Elsewhere adaptive degrades to strict and records why (`tier_note`).
  - Never because the host has spare RAM.
- The selected tier's `num_ctx` is sent for that request only; the configured `extra_body` is never changed. The call
  is attributed to the tier's own runtime fingerprint.
- Output grows above `max_tokens` only for a grounded `OutputExpectation`:
  - The attempt measures each existing target for a full-file rewrite (current tokens × 1.1 + 256, qualified ratio
    when known). An anchored patch gets none.
  - Never because a model produced more than expected.
  - Never above `max_output_tokens`.
  - The empty-content retry (an ungrounded enlargement) is now capped by the selected window and the output ceiling,
    and recorded.
- Refusals before inference:
  - `CONTEXT_BUDGET_UNSATISFIABLE`;
  - the new `OUTPUT_BUDGET_UNSATISFIABLE` (a subclass, so every existing handler catches it; typed failure
    `output_budget_unsatisfiable`, RESOURCE).
- Fallback order when nothing fits:
  1. Optional context is already sized to the preferred window, so it never forces a tier.
  2. For a full-file rewrite, the attempt asks once for an anchored patch of that file (D1 `repair_with_patch`), for
     models whose profile accepts patches. Recorded as `model.output_budget_protocol_fallback`.
  3. Otherwise the typed failure.
- A budget refusal inside the Developer's file-list path is no longer swallowed into the single-stage batch fallback
  (a larger request).
- Evidence: every expansion (context or output) goes to `LLMClient.budget_expansions`. It is logged and drained into a
  `model.budget_expansion` run event (after each Developer generation and before every trace write), so it lands in
  `traces.db`. Fields:
  - preferred and selected window and output;
  - prompt tokens and expected output;
  - reason;
  - qualification source;
  - ceilings;
  - tier runtime fingerprint;
  - elapsed time.
- `llm.context_policy` (`mode`, `declared_safe_context_tiers`, `max_context_tokens`, `max_output_tokens`) exists on
  every binding. It is SECURITY_AUTHORITY: a repository cannot grant itself a larger window.
- The offered tiers (size, source, tier digest) are bound into the `model_runtime` resume fingerprint.

## Decisions to review
1. **Output reserve.** Ungrounded calls reserve the configured `max_tokens`, capped at half the window. This follows
   the spec's "start with normal configured output budget". Grounded calls reserve their expectation.
2. **Optional context never triggers expansion.** Only mandatory content or grounded output can move a request to a
   larger tier, so optional context is allocated against the preferred window. The alternative (allocate optional
   context against the largest tier) would expand, and likely reload, on most calls.
3. **Tier qualification needs `context_capacity`.** The 18 protocol cases never fill the window, so a record at 64K
   would only prove the model loads at 64K. Adding the case bumped `QUALIFICATION_POLICY_VERSION` to
   `kriya-qualification/2`, so existing records are stale.
4. **Reload cost.** Ollama sizes a model's context at load, so a request with a different `num_ctx` can reload the
   model. This was not measured live (that needs inference; `ollama ps` shows it). Calls alternating between windows
   can repeat the reload. Recorded as elapsed time on each expansion event and documented in user guide §2.0e.
5. **No exact tokenizer by default.** Unchanged: Ollama 0.34.2 has no tokenize endpoint and nothing is downloaded. The
   deterministic "exact Ollama count" fixtures were dropped. The comparison with the real tokenizer is made live
   (`prd016-estimator-vs-tokenizer.json`); pin those counts after the live run.

## Files
- Changed:
  - `kriya/core/token_budget.py`, `kriya/core/llm.py`, `kriya/core/model_qualification.py`,
    `kriya/core/model_runtime.py`;
  - `kriya/config/config.py`, `kriya/config/authority.py`;
  - `kriya/workflow/context_budget.py`, `attempt.py`, `workflow.py`, `investigation.py`, `review_context.py`,
    `state.py`, `retry_strategy.py`, `failure_reporting.py`, `resume_fingerprints.py`;
  - `kriya/agents/agent.py`, `kriya/cli.py`.
- Tests:
  - New: `tests/test_prd016_allocation.py` (16), `tests/test_prd016_adaptive_budget.py` (31).
  - Extended: `tests/test_prd016_token_budget.py` (+11 selector tests).
  - Updated: `tests/test_workflow.py` (shares/floors; the hybrid-score fixture window retuned to the new semantics,
    assertions unchanged), `tests/test_val001_g1r3_retry_context.py` (`prompt_window=`),
    `tests/test_failure_reporting.py` (pinned vocabulary).

## Evidence (plain runner; you run pytest)
- `test_prd016_allocation` 16/0, `test_prd016_adaptive_budget` 31/0, `test_prd016_token_budget` 36/0.
- `test_prd014` 43/0, `test_prd013` 28/0, `test_prd015` 24/0, `test_llm_extra` 30/0, `test_production_doctor` 57/0.
- `test_config` 31/0, `test_sec009_config_authority` 52/0, `test_sec009_p2` 35/0, `test_failure_reporting` 35/0.
- `test_context_budget` 30/0, `test_review_context` 69/0, `test_val001_g1r3` 23/0, `test_val001_g1_remediation` 53/0.
- `test_d1` 23/0, `test_milestones` 68/0, `test_prd008a` 29/0, `test_tool001` 38/0.
- Same failures as HEAD:
  - `test_agents` 216/2 (caplog);
  - `test_dev_inv_001` 85/2;
  - `test_prd008_resume_fingerprints` 97/1;
  - `test_workflow_controller_enforce` 219/53 (identical names).
- `test_workflow`: see the batch READY message.
- Mutation checks:
  - disabling the omission bound fails 7 allocation tests;
  - disabling the trace drain or the `num_ctx` switch fails 4 adaptive tests;
  - disabling the patch fallback or the single-stage refusal guard fails 2.

## Live test
In `tests/test_live_prd013_016_model_runtime.py` (command in the PRD-014 handover):
- the qualification campaign now includes `context_capacity` at `num_ctx` 8192, asserted to PASS;
- new `test_prd016_adaptive_policy_serves_a_declared_larger_tier`: preferred 8192, declared 16384. A ~10K-token
  prompt must be sent with `num_ctx` 16384, the server must report more than 8192 prompt tokens, and the expansion
  event is written to `prd016-adaptive-tier.json`.

Windows stay at 8192/16384 to bound hardware load; no 64K run is part of this batch. Qualifying a 64K tier is
`kriya model qualify --context-window 65536`, on the operator's machine.

## Residuals
- The default bound is an approximation, not a guarantee (qualification replaces it). The allocator converts with the
  ASCII ratio, so non-ASCII-heavy context relies on the dispatch check as the backstop.
- The patch fallback reruns the Developer's batch for that attempt. Files generated before the refused one are
  generated again (bounded: once).
- Tool-calling turns (investigation, self-correction) take no output expectation; they never grow output.
