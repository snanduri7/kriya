# S2_A toolz packaging - SAFE_FAILURE (model: planner non-convergence)
Classification under cohort-002/CLASSIFICATION_RULES.md; executable 3844540 (checkout 6c917fa); run 2026-10-10; evidence in this directory (result.json, generate.log, verify.json, oracle-*-ws-c3.json, leak_check.json, git_status_after_generate.txt, candidate-export*/, oracle-*-candidate*.json). Labels: MEASURED = read from these files; TRACED = read from the producing code/sealed records; INFERRED = consistent, not proven.

- Kriya: needs_review, failure_type PLANNING_ERROR, reason_codes STRUCTURED_PLAN_SCHEMA_INVALID / STRUCTURED_PLAN_REPAIR_EXHAUSTED / PLAN_REPAIR_NON_CONVERGENCE after 2 bounded repairs (attempt 0: PLAN_VERIFICATION_SCOPE_UNJUSTIFIED, SEMANTIC_DEPENDENCY_EDGE_MISSING; 1: PLAN_VALIDATION_FAILED; 2: STRUCTURED_PLAN_SCHEMA_INVALID); no Developer unit ran; tree at base + .kriya/. MEASURED.
- External oracle: untouched ACCEPT 1 (tlz import fails = the mutation) / REGRESS 0. No candidate. Leak CLEAN (planner only: sensitivity INCONCLUSIVE, stated). Store VERIFIED. Authority stores: run id only.
- The 557035d false negative (partial no-change coverage + raw suite-preservation closure) did not recur: the run never reached those paths. Not evidence that the fixes work live; evidence only that the planner failed first.
- Classification: SAFE_FAILURE (model, planner). Model calls 3, wall 150.4 s.
