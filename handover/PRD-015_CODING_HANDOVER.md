# PRD-015 Coding Agent Handover

## Status
VERIFIED (pytest 5582 passed at 881da7e; the 3 fixture failures were fixed in 19572e9 and re-run green; live-model tier and the demo-03 brownfield generate passed at 19572e9).

## Scope
Normalized completion result at the LLM boundary (`kriya/core/completion.py`, `LLMClient.complete_result` /
`complete_with_tools_result`).

- `CompletionResult`: runtime fingerprint, protocol (json mode, streaming, tools, reasoning model, JSON-mode fallback
  and floor retries used), content, reasoning presence/size/source (never its text), normalized tool calls (native,
  plus Hermes JSON and Qwen XML `<tool_call>` text the server's parser did not convert - validated the same way;
  a malformed block is reported, never executed), finish reason, token usage (estimated flag), elapsed time, parser
  status, backend status/error, output budget, provider ids (id/model/system_fingerprint only), budget decision.
- Statuses: OK, EMPTY_CONTENT, MALFORMED_STRUCTURED_OUTPUT, OUTPUT_TRUNCATED (outranks everything),
  BACKEND_ERROR, TIMEOUT, CANCELLED (recorded, then re-raised - never swallowed).
- Compatibility: `complete()`/`complete_with_tools()` keep their return values and raised exceptions exactly (every
  existing LLM test passes unchanged); `llm.last_completion` exposes the normalized result.
- Reasoning: a leading `<think>` block (or one cut off before closing) is always hidden; blocks elsewhere and a lone
  `</think>` prefix only for configured reasoning models (the long-standing behaviour) - generated source may contain
  the tag text.
- Migrated call sites (bounded): the Developer's per-file full-content/repair path and its batch file-list path reject
  an OUTPUT_TRUNCATED answer (`protocol_reason_code = OUTPUT_TRUNCATED`, the existing operation-contract rejection,
  retry). A truncated Python file cut at a line boundary compiles; before this it could be committed.
- Telemetry: `last_call_metrics.completion` / `protocol_status`, Developer `generation_timings` and the
  `generation.completed` event's `model_use`; secret-free (tested).
- The empty-array defect routed here was already fixed and verified at `fd42e01`
  (`DEFECT_DEVELOPER_EMPTY_ARRAY_WRITTEN_AS_FILE.md`); nothing further.

## Files
- New: `kriya/core/completion.py`
- Changed: `kriya/core/llm.py`, `kriya/agents/agent.py`, `kriya/workflow/attempt.py`
- Tests: `tests/test_prd015_completion_result.py` (new)

## Evidence (plain runner)
- `test_prd015_completion_result`: 24/24, including end to end on both execution paths (direct `generate` and a
  milestone sequence, real workflow/gates/commit seam): a truncated-but-compilable Developer answer leaves `calc.py`
  byte-identical, a typed OUTPUT_TRUNCATED failure, no COMMITTED cycle; a negative control (same answer,
  `finish_reason: stop`) is committed.
- Mutation: disabling the truncation check fails 4 tests (both unit paths and both end-to-end paths).
- Regression: `test_llm_extra` 30/30, `test_llm_egress_policy_integration`, `test_agent_contracts` 37/37,
  `test_performance_telemetry`, `test_agents` (same 2 caplog-only runner failures as HEAD), `test_workflow` (same 6
  as HEAD), enforce (same 53 as HEAD).

## Residuals
- Other callers still use the string API (Planner/Architect/Reviewer free text, JSON-mode roles); they keep their own
  lenient parsing. New capability-sensitive code must use the normalized API.
