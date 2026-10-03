# Spring XML R1 closure (2026-10-03)

Product: feature/planner-context-fit-r1 at c355b70 (8670c98 PLAN-VERIFICATION-SCOPE-001 + c355b70
PLANNER-CONTEXT-FIT-001, on main e889a2d SPRING-XML-PLANNER-EVIDENCE-001).
Operator config: ~/.kriya/operator/provider-contract-v5-production.yaml = v4 + agent_llms.planner.llm
(the qualified llm_chain[0] binding verbatim; `operator_config_v4_to_v5.diff`).
Planner qwen3.6:35b-a3b-q4_K_M-kriya-620d4d5ce36a (ollama_native, fingerprint 26cb2deb..., settings
sha256:482067b2..., QUALIFIED /8); Developer qwen3-coder:30b-kriya-e52213655394 (fingerprint 0769fe62...).

- `planner_matrix.jsonl`: real production planning path (planning_only.py: `kriya generate` stopped at the
  first Developer call; control state restored byte-identical), one run per cell, scored by score.py.
  qwen3.8 not measured: no current-policy (/8) qualification record exists.
- `closure1.summary`: the live run, run id 20261003T094955-b87a3c4e: terminal_status SUCCESS, 0 Planner
  repairs, plan s1 ClinicServiceImpl.java (compile), s2 tools-config.xml (compile), s3 test verification;
  context fit 78/125 relationship lines kept (all 70 focus lines), estimated 29,021 -> 24,125, provider
  9,043 prompt tokens; both files applied by Kriya; held-out PetTypesCacheJudgeTests 2 run, 0 failures.
- Full parallel suite at c355b70: 8355 passed, 0 failed. doctor --production (v5, bench workspace):
  identical to the e889a2d baseline (context.recall_certification FAIL is the bench index, pre-existing).
