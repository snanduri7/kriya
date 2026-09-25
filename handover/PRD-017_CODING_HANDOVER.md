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
| 4. Refuse an incompatible fallback explicitly | An incompatible fallback is skipped, never sent a request, for the next configured fallback that can serve (configured order kept; see "Fallback selection" below), recorded as a `model.fallback_selection` run event. Only when no remaining fallback can serve: the typed `fallback_incompatible` failure, reason code `FALLBACK_MODEL_INCOMPATIBLE`, with every rejection in `diagnostics.rejected`. It is terminal (stops the retry loop, `failure_category: fallback_model_incompatible`, a dedicated CLI message) and recorded as a `model.fallback_incompatible` run event. |
| 5. Record what changed between attempts | `model.transition` run event on every change of request profile: `from`, `to` (full profiles with digests) and `changes` (field by field). The retry no-progress check now keys on the request-profile digest instead of the model alias, so a hop that changes the window, protocol or runtime is new progress opportunity by construction. |

### Fallback selection (architecture review correction)
`Primary -> incompatible F1 -> compatible F2` escalates to F2, and F1 receives no request:
- **At escalation** (`attempt._select_developer_fallback`, both the full-set branch and the one-shot targeted branch), before the prompt is built for the chosen model's window: starting at `resolve_fallback_model`'s entry, a fallback with an attempt-independent incompatibility (failed Developer case, production without QUALIFIED, no prompt room) is skipped for the next configured one. Its reasons are kept in `GenerationState.incompatible_fallbacks` for the run; `resolve_fallback_model(retry_count, chain, skip=)` (still the only formula) and failure triage honour the same skip, so triage rides the model generation actually escalated to.
- **At the call** (`_substitute_for_required_patch`): the anchored-patch requirement depends on the prompt just built, so a whole-file-only fallback facing a required patch is replaced by the next configured patch-capable fallback. This is attempt-specific and does not enter the run-wide skip set. The prompt was sized for the requested fallback's window; PRD-016's dispatch check still refuses it before inference if it does not fit the substitute.
- The attempt records the model its call actually went to (`state.last_model_override`, `model_hops`).
- Compatible fallbacks are never reordered by preference or metrics.

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
1. **Unset fallback `max_tokens` is the shared default, not the primary's.** Kriya has no chain-wide output setting: `llm` is the primary binding, so `llm.max_tokens` is that model's own value. An unset `FallbackModelConfig.max_tokens` (now `Optional[int] = None`, was `4096`) resolves to `DEFAULT_OUTPUT_TOKENS`, read from the packaged `default_config.yaml` `llm.max_tokens` (16384; no second literal), through one rule, `model_runtime.binding_output_tokens`, used by the Developer hop, `LLMClient._binding`, the allocator, role escalation and routing placement. An explicit value wins; a role's ceiling (`planner_max_tokens` etc.) still clamps it; PRD-016 still bounds each call by the window and `max_output_tokens`. Resume effect: the serialized `llm_chain` changes (4096 becomes null), so a checkpoint saved before this batch with such an entry no longer matches its resume fingerprint and is refused (fail closed); start the run again.
2. **Incompatible fallbacks are skipped in configured order** (architecture review, replacing the first version, which ended the attempt). See "Fallback selection".
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
- `test_prd017_fallback_transition`: 23 passed (14 original, plus the review corrections: the shared output default and its yaml pin, an explicit value wins, a role chain follows the rule; F1 incompatible then F2 selected; a compatible fallback not reordered; all incompatible gives the typed failure with every reason; a required patch moves the call to the next patch-capable fallback with no request to the first; end to end through `WorkflowEngine`, F1 is sent nothing and the skip is in `traces.db`; triage follows the skip).
- Review-correction mutations, each caught: no escalation skip (2 tests), no call-time substitution, triage ignoring the skip, unset `max_tokens` inheriting the primary (3 tests).
- Original version: They use the real DeveloperAgent and LLMClient with opposite primary and fallback profiles, and assert on the requests actually sent (model, `num_ctx`, `max_tokens`, no `response_format`, no `tools`, no streaming).
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
- **Late patch-capability switch into a smaller window (non-blocking, recorded by review).** When `_substitute_for_required_patch` moves a call to a fallback whose window is smaller than the prompt already built for the requested one, PRD-016's dispatch check refuses before inference (`CONTEXT_BUDGET_UNSATISFIABLE`). The request is never truncated or forced. A future context/fallback refinement could rebuild or reduce the prompt for the replacement's own budget, if evidence shows it matters.
- The primary model still silently turns a required patch into a whole-file request when its profile prefers whole files, and the answer is then rejected by D1. Changing that for the primary would affect every unverified model (the conservative profile is whole-file only). It needs its own decision.
- JSON mode is not gated at the `LLMClient` boundary for all roles. The Planner, Architect and role agents run on their own bindings, not on the Developer's fallback, and PRD-019 routing requires JSON mode for the roles that parse JSON.
