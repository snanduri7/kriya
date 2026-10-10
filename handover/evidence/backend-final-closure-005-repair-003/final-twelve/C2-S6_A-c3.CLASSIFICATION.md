# S6_A toolz false premise - GENUINE
Classification under cohort-002/CLASSIFICATION_RULES.md; executable 3844540 (checkout 6c917fa); run 2026-10-10; evidence in this directory (result.json, generate.log, verify.json, oracle-*-ws-c3.json, leak_check.json, git_status_after_generate.txt, candidate-export*/, oracle-*-candidate*.json). Labels: MEASURED = read from these files; TRACED = read from the producing code/sealed records; INFERRED = consistent, not proven.

- Kriya: SUCCESS NO_MUTATION_REQUIRED, tree unchanged (base + .kriya/), 0 model calls. MEASURED.
- External oracle: ACCEPT_EXIT=0 with NOOP_CHANGES=[] (the rule's unchanged-tree requirement), EXAMPLES_EXIT=0, 51 passed; REGRESS_EXIT=0 (192 passed). Leak UNAVAILABLE (no model call). Store VERIFIED. MEASURED.
- Classification: GENUINE_SUCCESS. Wall 41.0 s.
