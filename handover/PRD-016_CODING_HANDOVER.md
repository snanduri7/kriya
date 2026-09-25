# PRD-016 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 3).

## Scope
Tokenizer-aware dispatch budgeting (`kriya/core/token_budget.py`), applied inside `complete_result` and
`complete_with_tools_result` before every request.

- Counts the full dispatch: every message (content, tool calls, tool results), tool schemas, per-message and request
  framing.
- Decision: keep the full output budget; reduce `max_tokens` to fit (recorded); or raise
  `ContextBudgetUnsatisfiableError` (`CONTEXT_BUDGET_UNSATISFIABLE`) before any inference when the prompt plus the
  minimum output (1024 tokens) plus a reasoning allowance (qualified, else 2048, reasoning models only) cannot fit.
  Inside an attempt it is a typed `context_budget_unsatisfiable` failure (RESOURCE) with its reason code; the retry
  may still succeed on a fallback model with its own window.
- Window: the served `num_ctx` from the exact fingerprint, else `llm.context_window` recorded as `config_declared`.
- Counting, in order: an exact local count provider registered for the runtime's tokenizer digest
  (`register_exact_counter`); the qualified ratios (PRD-014 measures ASCII and non-ASCII bytes per token separately,
  less a 10% margin); the documented default (2.5 ASCII bytes/token, 1.5 non-ASCII bytes/token). Every decision
  records its method and `approximate`; after the call the prediction is compared with the provider's usage and an
  under-prediction is logged.
- `context_budget.estimate_tokens` (`len//4`) still drives context assembly (CTX-001 omission evidence unchanged); the
  default dispatch bound is never below it (tested per content class).

## Decision to review: no exact tokenizer by default
The supported runtime (Ollama 0.34.2) has no tokenize endpoint (`/api/tokenize` 404) and no tokenizer library is a
Kriya dependency. Hand-writing the qwen2 BPE is not reliable (its pre-tokenizer needs `\p{L}`/`\p{N}`, which stdlib
`re` lacks), and adding a dependency is your call, so exact counting is a seam, not a default. The measured-ratio bound
is what "exact where qualified" means here; the live test compares it with the real tokenizer on every content class.
Two ratios, not one: a single ratio either undercounts CJK/emoji or, taken as the minimum, roughly doubles the count
for code (found by the deterministic tests while building this).

## Files
- New: `kriya/core/token_budget.py`
- Changed: `kriya/core/llm.py`, `kriya/core/model_qualification.py` (tokenizer case), `kriya/workflow/retry_strategy.py`,
  `kriya/workflow/failure_reporting.py`
- Tests: `tests/test_prd016_token_budget.py` (new), `tests/test_failure_reporting.py` (pinned vocabulary)

## Evidence (plain runner)
- `test_prd016_token_budget`: all pass, including the model never being called for an unsatisfiable prompt, the
  reduced `max_tokens` actually sent, tool schemas counted, the qualified ratio used for a qualified runtime, and end to
  end: a Developer request that cannot fit fails typed, the Developer's model is never called and the file is untouched.

## Live test
`test_prd016_budget_against_the_real_tokenizer_and_the_served_window` (command in the PRD-014 handover): the default
bound vs the real tokenizer on Java/Python/XML/JSON/Unicode/stack traces (asserted never to undercount), a request
near an 8192-token window accepted, and one that cannot fit refused in under 5 s with nothing sent. The window is
kept at 8192 to bound hardware load.

## Residuals
- The default bound is an approximation, not a guarantee; the live test is where an undercount would show, and
  qualification replaces it.
- An unsatisfiable Developer request retries identically until the repeated-action guard stops the run (no fallback
  model configured); it never reaches the model.
