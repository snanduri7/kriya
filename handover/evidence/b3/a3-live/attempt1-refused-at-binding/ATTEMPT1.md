# A3 live attempt 1: refused at binding, before any model call (no run created)

`kriya generate` exited 1 after 0.7 s: `[Acceptance file refused] ACCEPTANCE_APPROVAL_INVALID: REQ-1: the goal differs`.
run_id NONE (no run, no model call, no M1 store); workspace HEAD = base, no change.

Root cause (CONFIRMED, harness): the approval template's `goal_sha256` was computed from the frozen goal FILE bytes,
which end with a newline; `run-b3.sh` passes the goal as `"$(cat goal.txt)"`, and command substitution strips the
trailing newline. Same requirement texts, different goal digest:
- file bytes (`kriya generate -f goal.txt`): goal_digest 06f0f1e9..., matches the approved approval;
- newline stripped (`$(cat)`): goal_digest 86384ef1..., does not match.
Kriya's B3 binding behaved as specified (fail closed on a goal mismatch). Not a stop condition; nothing fixed.
Proposed: pass the frozen goal with `-f <goal file>` (its exact bytes - the bytes the owner-approved approval binds).
