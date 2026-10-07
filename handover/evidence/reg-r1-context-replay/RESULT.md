# REG-R1 context-stability deterministic replay of the preserved qwen3.8 candidate (no model, no generation)

Certified code 5c55630 (worktree HEAD 97db017; `kriya` imported from the worktree, stability policy 2 - `identity.txt`),
live runtime (matched config, contained OCI python:3.12-slim), frozen Graphify base 67f99bd, staged engine.py
e47eacba60c19860270cb8c46fe95f2443a897eb5ffe0fddd921ed1cf0426034. Five full-suite runs, 354 s.

Same-context replays: the baseline's own full-suite gate call, twice (selections `[None, None]`), once for context
859cc7f8...; both decisions below were judged from that one measurement (the candidate's from the cache).

| Full-suite baseline observation | complete | cases | failed / passed / skipped | timing test | message digest |
|---|---|---|---|---|---|
| original | yes | 5370 | 217 / 4896 / 257 | FAIL AssertionError | ce0b515f... |
| replay 1 | yes | 5370 | 217 / 4896 / 257 | FAIL AssertionError | 9d7fff92... |
| replay 2 | yes | 5370 | 217 / 4896 / 257 | FAIL AssertionError | 4d3b8630... |

`test_ts_normalizer_scales_linearly_on_large_files`: outcome STABLE, exception type STABLE, message VOLATILE, body
VOLATILE. `test_built_at_commit_comes_from_the_target_repo`: outcome/type/message STABLE, body VOLATILE.

| Decision | authority | blocking | level 2 | whole output (diagnostic) |
|---|---|---|---|---|
| untouched base vs a second untouched-base run | pytest_per_test | **no** | 217 PRE_EXISTING | CHANGED_FAILURE |
| candidate e47eacba vs baseline | pytest_per_test | **no** | 217 PRE_EXISTING | CHANGED_FAILURE |

**PRESERVED QWEN3.8 CANDIDATE SURVIVES REGRESSION GATE: YES.**

Deterministic continuation (TRACED from the live run's own order, 20261006T231821-fb31caad): in attempt 2 of
subtask s1 the full-regression gate was the last check (compile, run verification, spec compliance, static analysis
and the pre-approval review had already run). With the gate non-blocking the attempt passes its Quality Gates and
s1's candidate is committed to the enforce plan worktree; the next step is subtask s2 of 3, whose Developer request is
a model call - stopped there (no model call made, nothing synthesized). The candidate is externally 3/5 (A and E not
fixed): REQ-1 could not pass the terminal acceptance gates later, so no false success follows from this gate passing.
