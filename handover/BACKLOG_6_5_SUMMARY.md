# Backlog Closure 6.5: summary

- **Directive:** handover/BACKLOG_6_5_DIRECTIVES.md (user, 2026-09-27).
- **Scope:** six items in this order: KNOWLEDGE-READPATH-001 → FALLBACK-CONTEXT-WINDOW-001 → PROMPT-BUDGET-FIT-001A+B → PRD027-SCORE-NORMALIZATION-001 → INF-001 (scoped framework). Plus any P0/P1 found on the way.
- **Status:** READY_FOR_PYTEST_VERIFICATION. All commits are local on top of a7916f1. No push.

## Commits
| Commit | Slice |
|---|---|
| a314d45 | Fix (my f3707c4): the Developer never received `reference_context` |
| f136d7e | KNOWLEDGE-READPATH-001: one learned-knowledge store and one reader |
| 12c1b0a | FALLBACK-CONTEXT-WINDOW-001: the budgeted context window is the one requested |
| a828a67 | Fix (my 12c1b0a): `test_llm_extra` still expected a request body without the context window |
| 8892e3c | PROMPT-BUDGET-FIT-001A/B: one fixed-overhead-aware section budget for Planner and Reviewer requests |
| 63e171c | Fix (my a314d45): learned reference overflowed Developer requests at small windows |
| f324f91 | Fix (my 3c1822d): the certification identity did not cover how context is ranked and rendered |
| 3c9f7db | PRD027-SCORE-NORMALIZATION-001: direct query evidence ranks above graph expansion |
| abbdb4b | INF-001 (1/3): golden parity test - the wire and the runtime digest at 3c9f7db |
| bda7e85 | INF-001 (2/3): the inference runtime port, the default adapter behind it, config selection |
| 79750a8 | INF-001 (3/3): docs, handover, tracker |
| 9062a5c | Summary and verification hand-off |
| (next) | Fix (my 12c1b0a): the packaged default config's num_ctx outranked a user's context_window |

## Per item
- **KNOWLEDGE-READPATH-001 (P1).** FIXED, awaiting pytest. See handover/DEFECT_KNOWLEDGE_READPATH_001.md, "Fix".
- **FALLBACK-CONTEXT-WINDOW-001 (P2).** FIXED, awaiting pytest. See handover/DEFECT_FALLBACK_CONTEXT_WINDOW_001.md.
  - demo-03 request bodies and runtime digests are unchanged.
  - A binding without an explicit option gets a new runtime digest and needs `kriya model qualify`.
- **PROMPT-BUDGET-FIT-001A/B (P2).** FIXED, awaiting pytest. See handover/DEFECT_PROMPT_BUDGET_FIT_001.md, "001A + 001B fix".
- **PRD027-SCORE-NORMALIZATION-001 (P2).** FIXED, awaiting pytest and `context certify`. See handover/DEFECT_PRD027_SCORE_NORMALIZATION_001.md, "Fix".
  - CI certification: 0.5435, CERTIFIED.
  - The stored certification is invalidated by design, so `context certify` must be re-run.
- **INF-001 (P2).** COMPLETE for its scoped framework deliverable, awaiting pytest and the live identity check. See handover/INF_001_RUNTIME_PORT.md.
  - The port and the default adapter are in place, with byte-identical wire and runtime digest (pinned), config selection, a test-only fake and the contract suite.
  - Port overhead is about 0.5 µs per call.
  - vLLM is an extension point only.

## Findings recorded on the way
- **Developer reference fit (own regression from a314d45/f136d7e). FIXED in its own commit.** With learned knowledge present, 8K REPAIR-mode Developer requests were refused, because the reference was reserved but never trimmed. `developer_reference` now trims it to the graph pool.
  - The regression test fails at 8892e3c with 4 Developer refusals, and passes with the fix, matching the terminal state of the same run without learned knowledge.
  - 2 mutations run, both killed.
- **DEVELOPER-PROMPT-FIT-001 (P2, OPEN).** The Developer's fixed text at 8K exceeds its preferred room. Not in 6.5 scope.

## Verification hand-off (the user's runs)
### 1. Focused pytest (every changed subsystem)
```bash
.venv/bin/pytest tests/test_knowledge_readpath_001.py tests/test_auth_goal_contamination_001.py tests/test_rag_queries.py \
  tests/test_learn_command.py tests/test_learn_sources.py tests/test_learn_text_naming_collision.py \
  tests/test_fallback_context_window_001.py tests/test_llm_extra.py tests/test_production_fallback_identity.py \
  tests/test_prompt_budget_fit_001ab.py tests/test_prompt_budget_fit_001c.py tests/test_prd016_allocation.py \
  tests/test_review_command.py tests/test_prd027_score_normalization_001.py tests/test_prd027_context_certification.py \
  tests/test_prd027_precision_expansion_seeds.py tests/test_prd027_retrieval_defects.py \
  tests/test_inf001_runtime_parity.py tests/test_inf001_runtime_port.py tests/test_prd013_model_runtime.py \
  tests/test_prd014_model_qualification.py tests/test_prd015_completion_result.py tests/test_prd016_adaptive_budget.py \
  tests/test_prd016_token_budget.py tests/test_prd017_fallback_transition.py tests/test_prd019_model_routing.py \
  tests/test_production_doctor.py tests/test_qual_environment_identity.py tests/test_model_qual_identity_001.py \
  tests/test_model_evidence_hardening_final.py tests/test_sec009_config_authority.py tests/test_workflow_controller.py \
  tests/test_agents.py tests/test_self_correction.py
```

### 2. Full suite
```bash
.venv/bin/pytest
```

### 3. Live and gate runs
Run these from demo-03 `workspace/repo`, with `K=<kriya repo>/.venv/bin/kriya` and `C=../../config/generate-production.yaml`:
1. **Identity parity (INF-001, FALLBACK-CONTEXT-WINDOW-001).** Run `$K -c $C model fingerprint` and `$K -c $C model status`. The primary must still read ea90552d… QUALIFIED, and the qwen3.6 fallback fc063b9e… QUALIFIED.
   - If either reads STALE or MISSING, stop and report it. Don't re-qualify to hide it: the digests are pinned unchanged by test.
2. **Learned knowledge end to end (KNOWLEDGE-READPATH-001).**
   - `$K -c $C learn -t "<a fact about the repo>"`.
   - Then `$K -c $C ask "<question on that fact>"`. The answer's prompt shows the fact fenced, with its source.
   - Optionally, a `generate` run on the same fact.
3. **Live-model tier.** `.venv/bin/pytest -m live_model`, with `KRIYA_BATCH6_EVIDENCE_DIR` set to `handover/evidence/BACKLOG_6_5/user-live`.
4. **Retrieval and index end to end.**
   - Re-index if the index is older than these commits: `$K -c $C analyze .`.
   - Then `$K -c $C context certify`. It must be CERTIFIED, with precision ≥ 0.5 and every recall class passing.
   - The stored record is invalidated by design: the certification identity now covers the evidence ranking and every context-assembly step.
5. **Doctor.** `$K -c $C doctor --production` must report PRODUCTION_READY=true, with `model.qualification` PASS and `context.recall_certification` PASS.

## Open backlog after 6.5 (P2+, not expanded into 6.5)
- **DEVELOPER-PROMPT-FIT-001 (P2, new).** The Developer's fixed prompt text at 8K exceeds its preferred room.
- **INF-001 follow-ups.** A real vLLM adapter (a documented extension point, needs approval). Environment observation per adapter.
- **Batch 7 (PRD-030/031)** is not started and needs approval.
