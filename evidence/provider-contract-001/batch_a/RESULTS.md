# PROVIDER-CONTRACT-001 Batch A - verification record (2026-09-30)

Base: 853aa43 + Batch A working tree.

## Full pytest (once) - full_pytest.txt
7503 passed, 4 failed, 72 deselected (28m52s). Failures classified before fixing:

| Test | Class | Cause | Fix |
|---|---|---|---|
| test_prd012_network_inventory::test_every_network_client_is_inventoried_with_its_authority | test pin (expected) | AsyncOpenAI construction moved to one site (LLMClient._client_for, 3 -> 1); new direct httpx transports (llm.py, model_qualification.py) | inventory updated with each client's governing authority |
| test_prd020_mutation_scope::...committed_path_history[m1.py / m2.py] | test fake (same class as earlier fake-usage migration) | transport fake reported 10 prompt tokens for an 11.6 KB prompt on an exact runtime -> PROVIDER_PROMPT_TRUNCATED | fake reports plausible_prompt_tokens |
| test_worktree_canonical_root::test_worktree_locations_are_decided_only_by_worktree_py | test pin (line number) | production_doctor.py lines shifted by the new model.provider_contract row | pin 594 -> 596 |

Rerun of those three files: 61 passed.

## Mutations (mutate_pc.py.txt, mutate_doctor.py.txt) - all KILLED
SDK retries 0->2; trust_env False->True; reasoning:false default; top-level sampling wire; exact-identity mismatch; served-below-requested; unknown field; conflicts; not-effective refusal; deadline cap; passed deadline; aclose; truncation check disabled; budget = served; doctor strict enforce; doctor exact window; doctor unverified gate; pin server-only filter; pin local-only.

Additional negative controls: budget_window (pre-fix sized to 65536: FAILED; post-fix PASS);
whitespace-dense consumption (raw bytes: FAILED; normalized: PASS).

## Static
ruff check kriya tests plugins scripts: clean. `ruff check .` findings only in the untracked
handover/claude_kriya_2_design_review/reproducers/*.py (not written by this work; left untouched).
pylint kriya plugins/core_tools tests: clean.
