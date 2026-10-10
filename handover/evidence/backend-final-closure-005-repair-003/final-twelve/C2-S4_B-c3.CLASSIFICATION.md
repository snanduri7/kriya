# S4_B JsonPath tip - SAFE_FAILURE (model behaviour)
Classification under cohort-002/CLASSIFICATION_RULES.md; executable 3844540 (checkout 6c917fa); run 2026-10-10; evidence in this directory (result.json, generate.log, verify.json, oracle-*-ws-c3.json, leak_check.json, git_status_after_generate.txt, candidate-export*/, oracle-*-candidate*.json). Labels: MEASURED = read from these files; TRACED = read from the producing code/sealed records; INFERRED = consistent, not proven.

- Kriya: FAILED after 11 attempts (anchored edits ANCHOR_NOT_IN_FILE on CompiledPath.java, two failure-family resets, a NO_CHANGE answer), nothing applied; tree at base + .kriya/. MEASURED.
- External oracle: untouched ACCEPT 1 / REGRESS 0. Exported last candidate (attempt 9, CompiledPath +111/-4): ACCEPT 1 and REGRESS 1 with tests=0 - the candidate does not compile. Not a false negative. MEASURED.
- Leak: LEAKED verdict, one hidden-only unit 'package com.jayway.jsonpath;' present in 60 tracked base files at e7eaf18: checker false positive under R2-F7, 0 leaks (identical to S4_B-final). Store VERIFIED. Authority stores: run id only.
- Classification: SAFE_FAILURE (model). Model calls 16, wall 1463.1 s.
