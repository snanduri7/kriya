# Backlog Closure 6.6: summary

Directive: `handover/BACKLOG_6_6_DIRECTIVES.md`. Three existing defects only; no vLLM adapter, no per-adapter environment evidence, no legacy traces migration.

**Status: FIXED, awaiting the user's pytest run and live gate** (READY_FOR_PYTEST_VERIFICATION). Local commits on `milestone-decomposition`, not pushed.

| Commit | What |
|---|---|
| d4704ab | The directive, recorded |
| e2a3dee | AUTHORITY-INSPECT-TRACEBACK-001 |
| 7d7b0c0 | PROMPT-FIT-ROLE-CHAIN-001 |
| cfe9590 | DEVELOPER-PROMPT-FIT-001 |
| 6d7b6b9 | Docs, tracker, findings |
| 9f957e6 | 8K test also asserts every registered section was located; summary scope notes |
| 69919f1 | **Fix (my cfe9590):** four `test_workflow.py` coordinated-repair fakes had a fixed signature and raised TypeError on the new `optional_sections` argument (3 tests failed; found before your run). Test-only. |
| (next) | Final gate: branch and sibling-retry coverage; PRE-APPROVAL-REVIEW-REFUSAL-001 confirmed advisory |

## AUTHORITY-INSPECT-TRACEBACK-001 (e2a3dee)
The refusal of an approval store inside the workspace is unchanged; only how it is reported changed.
- `TrustPathInsideWorkspaceError` carries `reason_code = TRUST_PATH_INSIDE_WORKSPACE`, the offending path, the workspace and a remediation (point `KRIYA_AUTHORITY_HOME`, `--trust-file`/`KRIYA_TRUST_FILE` or `--out` outside the workspace, or unset it for `~/.kriya/authority`).
- `kriya authority inspect|approve|revoke` resolve the local store through one guarded helper (`_authority_local_path`): exit 1, `Error: [TRUST_PATH_INSIDE_WORKSPACE] …` plus `Remediation: …`, no traceback. `inspect` and `approve` used to traceback; `revoke` already exited cleanly.
- Every "Error loading configuration" line (generate, fix, model, doctor without `--production`) renders the same code and remediation.
- `doctor --production --json` stays one parseable report; its `config.load` check now carries `evidence.reason_code` and the error's own remediation.
- Only the named operator error types are caught. An unexpected exception on the same path still escapes with its traceback (tested).
- Scope of the formatting: `_user_error_text` and the doctor report render any error that carries `reason_code`/`remediation`; today only `TrustPathInsideWorkspaceError` does (checked: `ConfigAuthorityError`, `RemovedConfigFieldError`, `StateDirectoryError`, `LogDirectoryError`, `ApprovalArtifactError` carry neither), so no other message changed.
- JSON modes checked: `doctor --production --json` stays one parseable report. `generate --json` on any config-load refusal writes nothing to stdout (only the stderr error line) - unchanged behaviour that predates this work, out of scope, noted here.

Tests: `tests/test_authority_cli_refusal.py` (8). Pre-fix: 6 fail, the 2 controls pass. Mutations: broad `except Exception`, dropped remediation, `approve` unguarded; all 3 killed.

## PROMPT-FIT-ROLE-CHAIN-001 (7d7b0c0)
A Planner or Reviewer request is fitted for the model that actually receives it.
- **Primitive.** `context_budget.CandidatePrompts(config, agent, role, build)` builds each role candidate's prompt from that candidate's own `RequestCapacity` (`candidate_request_capacity`: its window, token counter and reasoning floor, and the output that exact call asks for). Built lazily (a fallback only when escalation reaches it) and once per candidate. `BaseAgent.run(prompt=first, candidate_prompt=prompts)` hands it to `call_with_escalation`, which already took a per-candidate prompt.
- **One output rule.** `agent.candidate_output_tokens` is what `_call_with_escalation` sends (the role override for the primary; an explicit candidate's own budget clamped, never raised, by it) and what the sizing reserves. `role_output_override` is shared by `BaseAgent.run` and the sizing.
- **Sites.** Direct Planner (`fit_planner_request` per candidate); enforce structured Planner (reference refitted per candidate, first request and every repair round; persisted diagnostics show the first candidate's request); pre-approval review, final review and `kriya review` (`review_requests`, which replaces `review_batches_for_request`).
- **Reviewer.** Batches keep the first candidate's structure. A candidate whose room holds a batch gets it unchanged, re-measured for that candidate. One that cannot gets that batch's own files refitted into one request: the files that fit, then `REVIEW_FILES_NOT_SHOWN_NOTE` naming the rest (`REVIEW_FILES_OMITTED_NOTE` when none fit). The header (goal, candidate diff, evidence) and system prompt are never trimmed.
- **Deliberate choices, stated:**
  - A *larger* Planner fallback is fitted to its own larger room, so it can carry more reference text than the first model had room for. Graph context was retrieved for the first model and is not re-retrieved.
  - Review batches are never upsized for a larger fallback.
  - `agent_request_capacity` with a `role_llm` now reserves `min(role_llm's own budget, the role override)`, which is what the call sends. Before, it reserved the override, which could differ.
- Refits are `context.request_fit` events (enforce: ledger records) naming the model. Exact inference identity, qualification and PRD-017 escalation are untouched: the same candidates, the same overrides, the same order.
- A fallback that cannot hold the mandatory text is still refused before inference; as the last candidate its `CONTEXT_BUDGET_UNSATISFIABLE` is what surfaces, and 001C's `final_review_refused` still applies.

Tests: `tests/test_prompt_fit_role_chain_001.py` (15), end to end through the real engine, escalation and dispatch check:
- output rule; lazy per-candidate build;
- Planner: primary larger than fallback (primary fails; 12K fallback refitted); primary smaller than fallback; equal windows (byte-identical); mandatory cannot fit a 4K fallback (typed refusal);
- Reviewer: smaller fallback (pre-approval, file cut to room); final review (header whole, batch omitted); equal windows; mandatory header cannot fit (`final_review_refused`); header kept whole; files not carried are named; `kriya review` CLI;
- enforce (first request and repair round); milestone (M2's review goes to the 8K fallback and M2 verifies).

Pre-fix (worktree at e2a3dee): 7 fail; the 5 that pass are the output-rule lock, the equal-window and mandatory-refusal controls. Mutations: 7 primitive and 5 call-site mutations, all killed.

Updated tests: `test_prompt_budget_fit_001ab.py` and `test_prd016_allocation.py` use `review_requests`.

## DEVELOPER-PROMPT-FIT-001 (cfe9590)
Optional Developer context is sized only after the mandatory text.
- `context_budget.fit_developer_request(capacity, system, prompt, sections)`: a request that fits is returned byte-identical. Otherwise every registered optional section found exactly once in the prompt (by offset; missing, repeated or overlapping sections are reported and kept as mandatory text, never guessed at) is refitted with `fit_variable_section` into the room the mandatory text leaves. A section that fits its room is kept unchanged.
- **Order kept** (the last gives way first): sibling contents of the batch, graph context, investigation evidence, untrusted learned reference.
- **Mandatory, never trimmed:** system prompt, task and authoritative goal, design and plan, skill conventions, required blocks (ecosystem, lifecycle, verification contract), retry evidence (the authoritative implementation to repair, the gate error), known-target source, directives. A request whose mandatory text alone does not fit is still refused (`CONTEXT_BUDGET_UNSATISFIABLE`).
- **Capacity:** the binding the request is sent to (`_chain_binding`), with the reasoning floor; a full-file answer grounded to need more output (`OutputExpectation`) reserves that. Capacities are resolved once per output budget.
- **Wiring:** `_run_developer_generation` (the one Developer entry for direct, enforce and milestone runs) builds a `DeveloperRequestFit`; each attempt branch registers its graph context (rebuilt smaller from the same candidates) and fenced reference (whole entries, fence never cut) via `_developer_optional_sections`; DEV-INV evidence registers itself. `DeveloperAgent` fits every per-file request and the single-stage request; the sibling-names retry after a refusal now starts from the fitted prompt.
- **No authority change:** anchored-edit grounding (`apply_anchored_edits`' shown-context check) still receives the full context the attempt built. That check only rejects; an edit still has to match the real file exactly once, so the unshown graph text cannot make a wrong edit succeed. D1 tiers and write scope come from mandatory sections only.
- Reductions are `model.optional_context_reduced` events (reason `request_fit`).
- PRD-016 pools remain upper bounds; no window, margin or output reserve was increased or weakened.

What an 8K repair request does now: at 8K a repair request carried ~4,450 prompt tokens against 3,808 of room. It dispatched only because the dispatch check cut its output. Now the graph context gives way and every Developer request fits its capacity; the mandatory sections are all present.

Branch coverage: the 8K run exercises the first attempt and the targeted retry and asserts every registered section was located exactly once. The final gate added `tests/test_developer_prompt_fit_branches.py` (6), test-only:
- **missing-files, fallback-targeted, coordinated-repair** (parameterized): the real `run_attempt` with a real `DeveloperAgent` at 8K; only `LLMClient.complete` records. Each branch's pool-sized reference does not fit beside a mandatory required block, and every request that reaches inference is within the capacity of the binding actually called (the fallback's for fallback-targeted), with the block whole, the reference fence whole or absent, and a `request_fit` reduction whose sections were all located.
- **sibling-names retry:** the retry after a refusal starts from the fitted request (the graph context the fit removed never returns); a refusal after the fit already dropped the siblings is raised, not retried; without a fit the pre-existing retry is unchanged.
- Mutations: each branch's section registration removed (coordinated inner and outer, fallback-targeted, missing-files), the retry rebuilt from the unfitted prompt, the retry guard removed - 6 run, all killed. No production defect found; no production change.

Tests: `tests/test_developer_prompt_fit_001.py` (11): byte-identical when it fits; order; mandatory never trimmed; unlocated sections; reference whole entries and fence; capacity per binding and expectation; choke-point wiring (fallback binding and sections); 8K boundary with every mandatory repair section present; 32K control unchanged; large reference at 12K shrinks first with its fence whole; mandatory-only overflow refused before inference.

Pre-fix (worktree at 7d7b0c0): 9 fail; the 32K control passes. Mutations: 9 run, all killed (never fit, reversed order, always rebuild, agent skips the fit, capacity ignores the binding, ignores the expectation, choke point drops the binding, drops the sections, fence cut).

Updated test: `test_learned_reference_never_makes_a_developer_request_fail_at_8k` now asserts the fence is whole or absent (at 8K the reference gives way first), not that it is always present.

## Findings recorded (not fixed; in the tracker)
- **ARCHITECT-PROMPT-FIT-001 (P2).** The Architect carries the whole fenced reference unfitted; a 30-entry reference at 16K is refused before inference (typed, fails closed). Same class as 001A/B. Seen while writing the role-chain tests, whose parameters were then reduced to avoid it.
- **DEVELOPER-AUX-LOOP-PROMPT-FIT-001 (P3).** The opt-in DEV-INV investigation loop and self-correction loop (both default off) send unfitted Developer context in their own requests; the dispatch check stays the backstop.
- **MCP-APPROVAL-PATH-TRACEBACK-001 (P3).** `kriya mcp` commands traceback when `KRIYA_MCP_APPROVAL_HOME` is inside the workspace; same shape as the fixed authority defect.
- **PRE-APPROVAL-REVIEW-REFUSAL-001 (P3).** A refused pre-approval review is logged and the approval proceeds with no automated review attached (the human still decides). Seen in the pre-fix control run. **Confirmed advisory** (final gate): no config or production-profile policy requires it, the result reports `review_included_in_approval=false`, and the final review still runs afterwards (a refusal there ends typed `final_review_refused`). P3 stands.

No new P0/P1.

## Verification owed (user)
1. Focused pytest (below) and the full suite.
2. Identity is untouched by construction: `MODEL_PROTOCOL_ADAPTER_VERSION` and every fingerprint input are unchanged, and `index_implementation_digest()` is identical before and after (b4501e5f…, checked at 211369c and at cfe9590), so the stored context certification stays current.
3. Live verdicts: PROMPT-FIT-ROLE-CHAIN-001 and DEVELOPER-PROMPT-FIT-001 are **NOT_LIVE_EXERCISED** - the qualified 32K profile has no smaller role-chain fallback and every Developer request already fits, so neither fit runs. They need the user's decision on pytest verification (as for 001C). AUTHORITY-INSPECT-TRACEBACK-001 can be exercised live (the same `authority inspect` with `KRIYA_AUTHORITY_HOME` inside the workspace that showed the traceback in 6.5).
4. Exit gate (unchanged production profile, not evidence for the two prompt fits): `model status` QUALIFIED for every role, `context certify` PASS, `doctor --production` PRODUCTION_READY=true.

## Final-gate commands (user)
Focused:
```
.venv/bin/pytest tests/test_authority_cli_refusal.py tests/test_prompt_fit_role_chain_001.py tests/test_developer_prompt_fit_001.py tests/test_developer_prompt_fit_branches.py tests/test_prompt_budget_fit_001ab.py tests/test_prompt_budget_fit_001c.py tests/test_prd016_allocation.py tests/test_prd016_adaptive_budget.py tests/test_review_command.py tests/test_review_context.py tests/test_agents.py tests/test_prd017_fallback_transition.py tests/test_dev_inv_001_investigation.py tests/test_sec009_p2_authority_approval.py tests/test_production_doctor.py tests/test_doctor_command.py tests/test_workflow_controller_enforce.py tests/test_workflow.py
```
Full: `.venv/bin/pytest`. Live gate: `$K -c $C model status`, `$K -c $C context certify`, `$K -c $C doctor --production`, `KRIYA_AUTHORITY_HOME=$PWD/.authority-test $K -c $C authority inspect; echo "exit=$?"`.
