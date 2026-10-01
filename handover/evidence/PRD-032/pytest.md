# PRD-032 test evidence (coding agent, Wave 7, 2026-09-27)

The coding agent ran everything below under the user's standing permission for this batch. The venv's `bin` is first on PATH (`scripts/chaos_report.sh` does this itself).

| Run | Revision | Result |
|---|---|---|
| Full `.venv/bin/pytest -q` (venv on PATH) | `b40b4da` | **6774 passed, 0 failed**, 60 deselected (28:23) |
| Chaos, every tier (`scripts/chaos_report.sh … all`: deterministic + real Semgrep 1.178.0 + live qwen3-coder:30b) | `ae29d0a` | **51/51 PASSED**, content digest `9fe5a8fb66fb9d7a72bf971854b0750e96e1e6553c4899899921db0fc325805c` (`chaos/`) |
| Chaos, deterministic tier, run twice | `ae29d0a` / earlier | 47 PASSED, 4 NOT_RUN (their tier was not selected); the same digest `ca058d849df3b4c3…` on every run (`chaos-deterministic-rerun/`) |
| After fix 2 (`2f66763`): the workflow, requirement-lineage, cross-path, skills and knowledge-extraction suites; prompt-fit 001ab / role-chain / PRD-016 allocation / PRD-021 | `2f66763` | 997 + 68 passed, 0 failed |
| Adjacent suites after fix 1 | `91e474d` (pre-commit) | 354 passed |
| Regression tests without their fix | — | fix 1: fail (`quality_gates_exhausted` ≠ `workspace_commit_failed`); fix 2: fail (the absolute path reached the planning prompt) |
| Mutation check, fix 1 | — | 4/4 mutations caught |
| `ruff check .`, `pylint kriya plugins/core_tools tests` | every commit | 0 findings |

## Live observations (not defects; recorded for PRD-035)

- **Real Planner structured-output reliability** (`qwen3-coder:30b`, packaged settings). In a 5-run sample, 2 runs stopped before the Architect. One was `planner_output_unauthorized_path` (absolute planned paths, caused by Kriya's own prompt: **fix 2**). The other was `planner_output_schema_invalid` (an invented `verifier_kind: "file_check"`, still invalid after the repair round). Both are typed, fail-closed stops with nothing changed. L01 records such a stop as its outcome when it happens (its invariant holds either way). L02 reports a stop before the Developer as a failure ("NOT_LIVE_EXERCISED"), never as a pass.
- **L03.** With the always-rejecting compile gate, Kriya now stops early with `candidate_independent_deterministic_failure` (the failure is identical for every candidate), within 3 attempts. Earlier runs ended as `quality_gates_exhausted` after 7. Both are in the closed set of bounded stops.
