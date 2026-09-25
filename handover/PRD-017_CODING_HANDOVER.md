# PRD-017 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION (batch 4: PRD-017 to PRD-019, one pytest stop for the whole batch).

## Source identity
- Base revision: `26f4dc6` (batch 3 verified and pushed), plus `0c0c74b` (final-newline fix).
- Branch: `milestone-decomposition`, local and unpushed.

## Scope
Capability-aware fallback model transition (instruction: `tasks/PRD-017_Capability-Aware_Fallback_Model_Transition.md`).

| Requirement | Implementation |
|---|---|
| 1. Resolve the fallback's runtime, profile and qualification on transition | `kriya/workflow/model_transition.py::resolve_request_profile(config, binding)`: exact runtime digest, qualification status for the Developer role (with the failed cases), capability profile and its source, edit protocol, native tools, JSON mode, streaming, reasoning, served and allocation windows, the binding's own output budget, context policy. Resolved at every Developer call (`attempt._enter_developer_model`, the single choke point `_run_developer_generation`). |
| 2. Recompute context, tools, edit protocol, output/reasoning budget and structured-output strategy | Context: the allocator already re-ran against the fallback's window (PRD-016). Output: **new** - the fallback's own `max_tokens` is used (it was ignored; see Pre-change). Tools: `LLMClient.complete_with_tools_result` already refuses a model without native tools, and investigation resolves capabilities per model. Edit protocol: already per model in the Developer. Structured output: **new** - the Developer file-list request used JSON mode for every model; it now follows the model's profile. |
| 3. Rebuild from structured state, not the prior rendered prompt | Already true, verified: every retry branch rebuilds the task and context from `GenerationState` with the active model's allocation window; `state.error_context` is raw failure evidence, not budgeted against a window; the retry package is rebuilt each attempt. No change needed. |
| 4. Refuse an incompatible fallback explicitly | Typed `fallback_incompatible` failure, reason code `FALLBACK_MODEL_INCOMPATIBLE`, raised before any request. It is terminal (stops the retry loop, `failure_category: fallback_model_incompatible`, a dedicated CLI message) and recorded as a `model.fallback_incompatible` run event. |
| 5. Record what changed between attempts | `model.transition` run event on every change of request profile: `from`, `to` (full profiles with digests) and `changes` (field by field). The retry no-progress check now keys on the request-profile digest instead of the model alias, so a hop that changes the window, protocol or runtime is new progress opportunity by construction. |

### When a fallback is refused (evidence only)
- A qualification record for its exact runtime has a FAILED Developer case.
- `runtime_profile: production` and its runtime is not QUALIFIED.
- The attempt may only patch an existing file (the completeness gate D1 found no complete, exact current source, so a whole-file answer would be rejected after the call) and the fallback's profile returns whole files only, or it failed `anchored_edit_protocol`.
- Its window leaves no room for a prompt beside its output budget.

MISSING, STALE and non-exact runtimes are recorded in the transition event, never refused: that is every local setup that has not run `kriya model qualify`, and the mocked suite.

## Pre-change reproduction (found while implementing)
- **Fallback output budget ignored.** `FallbackModelConfig.max_tokens` (default 4096) was never used on a Developer hop. `attempt.py` passed no `max_tokens_override`, so `LLMClient` sent the primary's 16384, and `allocation_window` reserved the primary's output too. Role-agent escalation (`call_with_escalation`) already passed each candidate's own value, so only the Developer path was wrong.
- **JSON mode on every model.** `DeveloperAgent._resolve_step1_file_list` sent `json_mode=True` regardless of the model. A chain entry with no explicit capabilities resolves to the conservative profile (`json_mode=False`), so a hop sent JSON mode to a model not marked for it.
- **Silent whole-file degradation.** For a model whose profile prefers whole files, the Developer quietly turns a required anchored patch into a whole-file request (`agent.py`, "prefers full-file repair"). When D1 required a patch, that answer is rejected after the call. On a fallback hop this is now refused before the call. The primary keeps today's behavior (see Residuals).

## Decisions to review
1. **Unset fallback `max_tokens` now inherits the primary's.** It also changes the serialized `llm_chain` of every existing configuration (4096 becomes null), so a checkpoint saved before this batch with an `llm_chain` entry that left `max_tokens` unset no longer matches its resume fingerprint, and resuming it is refused (fail closed); start the run again. `FallbackModelConfig.max_tokens` is `Optional[int] = None` (was `4096`). A chain entry without `max_tokens` gets `llm.max_tokens` on every call, for the Developer and for role escalation. Role chains without an explicit value move from 4096 to the primary's value; a role's own ceiling (`planner_max_tokens` etc.) still clamps it. An explicit value is always used. The alternative (keep 4096 and apply it to the Developer) would have cut every existing demo config's fallback output to 4096.
2. **Incompatibility ends the attempt; the chain is not reordered.** `resolve_fallback_model` stays the only escalation formula. Skipping to "the next compatible entry" would be a second, silent selection policy.
3. **STALE is recorded, not refused.** A stale record means the runtime drifted since qualification; it is not evidence of a failing case. The production profile still requires QUALIFIED.
4. **Self-correction stays on the primary.** The compile-failure self-correction loop has always called the primary model, and `LLMClient` gates tools by the model actually called. A fallback attempt's self-correction therefore uses the primary's own capabilities, which is consistent but not a fallback. Not changed.

## Files
- New: `kriya/workflow/model_transition.py`, `tests/test_prd017_fallback_transition.py`.
- Changed:
  - `kriya/config/config.py` (`FallbackModelConfig.max_tokens` optional);
  - `kriya/core/model_runtime.py` (`binding_output_tokens`);
  - `kriya/core/llm.py` (a call to a binding uses that binding's `max_tokens`);
  - `kriya/workflow/context_budget.py` (allocation reserves the binding's output);
  - `kriya/agents/agent.py` (escalation and the file-list JSON mode);
  - `kriya/workflow/attempt.py` (`_enter_developer_model`, `_patch_required_files`, profile-keyed progress fingerprint);
  - `kriya/workflow/state.py` (`last_developer_request_profile`);
  - `kriya/workflow/retry_strategy.py` (terminal type);
  - `kriya/workflow/failure_reporting.py` and `tests/test_failure_reporting.py` (the pinned vocabulary);
  - `kriya/workflow/workflow.py` (`failure_category`);
  - `kriya/cli.py` (the stop message).

## Tests (plain runner; you run pytest)
- `test_prd017_fallback_transition`: 14 passed. They use the real DeveloperAgent and LLMClient with opposite primary and fallback profiles, and assert on the requests actually sent (model, `num_ctx`, `max_tokens`, no `response_format`, no `tools`, no streaming).
- Mutation checks, each caught:
  - removing the refusal crashes the refusal test (`pytest.raises` fails);
  - the file list always JSON fails 1;
  - the primary's `max_tokens` on the hop fails 2;
  - the allocator reserving the primary's output fails 1;
  - the refusal not terminal fails 1.
- Regression, plain runner, same failure names as before this batch (the plain runner's known caplog and environment failures, which pass under pytest):
  - `test_workflow` 862/6;
  - `test_workflow_controller_enforce` 219/53;
  - `test_agents` 216/2;
  - `test_dev_inv_001_investigation` 85/2;
  - `test_prd008_resume_fingerprints` 97/1.
- Everything else passed: milestones, proposal promotion, agent contracts, PRD-016 allocation, adaptive budget and token budget, failure reporting, traces, model capabilities (except its one caplog test), G1-R3 retry context, generation time budget, retry policy, self-correction.

## Live test
See the PRD-019 handover for the batch-4 live command. PRD-017's case: primary `qwen3-coder:30b`, fallback `qwen3.5:9B` declared with an 8K window, no JSON mode and whole-file edits. The case sends one bounded Developer request on the fallback and checks:
- the transition event against the real exact fingerprints;
- the fallback request's `num_ctx`, `max_tokens` and JSON mode;
- a real answer.

## Residuals
- The primary model still silently turns a required patch into a whole-file request when its profile prefers whole files, and the answer is then rejected by D1. Changing that for the primary would affect every unverified model (the conservative profile is whole-file only). It needs its own decision.
- JSON mode is not gated at the `LLMClient` boundary for all roles. The Planner, Architect and role agents run on their own bindings, not on the Developer's fallback, and PRD-019 routing requires JSON mode for the roles that parse JSON.
