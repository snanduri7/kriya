# Zero-Known-Gap reconciliation of OPEN-ITEMS.md against the VERIFIED rc11 baseline (2026-11-06)

READ-ONLY. No fix implemented. Method: all 133 OPEN / NEEDS_OWNER_DECISION rows of handover/MODULARIZATION/OPEN-ITEMS.md (the 44 registry rows `TRACKED_IN_OPEN_ITEMS` all route here) were split into 5 chunks; five read-only subagents traced each row to the current code and tests (kriya/ and plugins/ identical to candidate b32976245f84), ran cheap reproducers in scratch dirs and targeted tests, and classified it. Per-row tables with file:line / test / commit evidence: `ledger-reconciliation/recon_chunk{1..5}.md`. I independently re-checked two results (NEW-1, OI-156) below; the rest are the agents' findings (MEASURED/TRACED as each row states) and have NOT been re-verified by me.

## Result (133 rows)
| class | n |
|---|---|
| FIXED_VERIFIED | 21 |
| STALE_DUPLICATE_DISPROVED | 7 |
| INTENTIONAL_CONTRACT | 1 |
| FUTURE_FEATURE | 9 |
| NON_BLOCKING_RESIDUAL | 72 |
| NEEDS_OWNER_POLICY_DECISION | 16 |
| OPEN_RELEASE_BLOCKER | 7 (+1 new, NEW-1) |
The ledger is NOT uniformly stale: rows closing in slices that never ran (R1, R2, R5, M2, O1-O3, P1, P2, Q1, Q2, F/G/H) are genuinely pending. The earlier "119 OPEN" is not the real blocker count; the dedup'd real count is below. Registry's 44 rows map to: 22 non-blocking residual, 7 owner decision, 5 fixed, 3 future, 2 blocker (STATE-DIR-RESOLUTION-DIVERGES-001/OI-02, ATOMIC-WRITE-NO-FSYNC-001/OI-03), 5 whose OI row is closed/other.

## Remaining real blockers after dedup (8 candidates; none implemented)
Authority/security first. "Severity" is my suggestion, the owner decides.
| id | what is wrong on the baseline | evidence | suggested severity |
|---|---|---|---|
| NEW-1 (new) | `curl --user=u:PW`, `--proxy-user=u:PW`, `-uu:PW` attached forms are not redacted (`redact_text` redacts only `-u PW`/`--user PW`); agent also reports `commands_carry_secret` is False so runtime-command review shows the raw password | I re-ran redact_text: the three attached forms print the password unchanged (MEASURED by me); review-offer consequence is the agent's | P2 security (secret disclosure on a review/evidence surface); owner kriya/foundation/redaction.py `_USER_FLAGS` |
| OI-172 (new) | planned `git diff` of a modified tracked credential-pattern file (deploy.pem) prints its content although a planned filesystem read of it is denied (SENSITIVE_PATH_DENIED) | MEASURED by the agent on a scratch repo; not re-run by me | P2 authority/security; owner plugins/core_tools GitTool.authorize_model_invocation |
| OI-156 (new) | `_model_identity` puts `llm.base_url` raw into the preflight, which reaches the 0600 launch record and `execution.preflight`; other surfaces use `safe_endpoint` | I TRACED kriya/application/run.py:86 (raw) vs safe_endpoint used in skills/service.py and kup/context_ops.py; the leak itself was MEASURED by the agent | P2/P3 (needs a credential inside the configured URL); fix `safe_endpoint` at that line |
| OI-97 | baseline suite runs without the goal-stated JDK override candidate validators use, yet both environment identities record the override: baseline and candidate are labelled the same environment while ran under JDK 21 vs 17 | MEASURED by the agent; narrow reach (Java, host jdk/mvn mismatch, goal names java side); development_loop.py:282, requirement_closers.py:549 | P2 evidence-binding |
| OI-02 | `KRIYA_STATE_DIR` relative/`~`: state_paths refuses, tools/validate.py, acceptance_jvm.py, model_runtime.py resolve against cwd; model_runtime does not expand `~` | MEASURED; impact is state placement only (no evidence split found) | P2 -> could be P3 |
| OI-56 | a failed deterministic test/build gate is recorded and displayed as `deterministic_result: "PASS"` beside `success: false` (runtime_verification.py:321); no decision reads it | MEASURED; owner said it must close before Q1 | P3 evidence honesty |
| OI-120 | analyzer writes extractor-LLM output into a skill with plain `open()`, bypassing the Skills owner (analyzer.py:1132-1217) | TRACED; reachable from `kriya analyze` and generate with auto_index_missing_dependency_graph | P3, bounded |
| OI-03 | default atomic writer does zero fsyncs; many control-plane writers use it; loaders fail closed on empty files (milestone sidecar loader raises raw JSONDecodeError) | MEASURED; owner approved a central fsync 2026-10-16, never built here | P3 durability |
STOP CONDITION: authority/security blockers survived (NEW-1, OI-172, OI-156; OI-134/OI-154 below). Per your ruling I stopped the backend workstream here: nothing implemented, no product code touched. Any fix changes kriya/ or plugins/ and so forces a new candidate and the full PRD-036 chain: bundle them into ONE correction cycle.

## Owner policy decisions (16; two gate release)
- OI-134 / OI-154 (RELEASE-GATING per the agent): digest-bound/byte-exact private stores (attempt-evidence content blobs at capture `full`, verification contracts, checkpoints, RunRecord plan, milestone state, scanner evidence) retain a credential raw; MEASURED file mode 0644 via `durable_atomic_write_bytes(mode=None)` under umask 022; REQUIRED register row OBS-003 neither met nor amended; no S20c/S20m commit on this branch. Options: retention + 0600/0700 + amend OBS-003 (agents' recommendation) / redact at write (breaks resume binding) / encrypt / default capture digest_only. Also OI-154 Q2: is the review-diff sidecar outside OBS-003.
- OI-166: no gate runs the UI/Electron/KUP-consumer tests (real-Kriya UI tests skip without .venv). Is ui/ inside the release boundary? (this session ran the UI gates by hand: all green after one timeout normalization).
- OI-170: hard links to files outside every root bypass the path-based containment check. Add containment-by-identity or accept as operator-created.
- OI-174: bootstrap `git init` leaves a `.git` in a foreign-owned folder when the commit is refused (pinned by test). Roll back?
- OI-175: should the origin-in-repository content-filter rule be activation-aware.
- OI-125: does the PRD-036 canary replace the Scenario-04 rehearsal as pre-tag live gate? OI-126: which readiness claims does the release assert (second provider, two-context isolation, decision-owner table, soak) and must therefore prove.
- OI-114 (preserve upstream accepted work when a later unit stops), OI-76 (repair attempt all-or-nothing vs partial staging; current contract test-pinned), OI-81 (what counts as skill coverage; MEASURED an empty folder clears the gap offline), OI-93 (runtime-intent detector width; realistic phrasings end as honest "not fully verified"), OI-104 (retention/purge of review-diff sidecars and launch records), OI-105 / OI-106 (HITL attempt.closed placement; pre-review checkpoint; stale snapshot after held gates is discarded on resume = fail-closed), OI-61 (CLI import time +8..15% median vs the +3% criterion; machine load ~100, rough).

## Notes and caveats (honest limits)
- The agent downgraded OI-107, 111, 112, 115 from the owner's "Class A"; please confirm those downgrades (the rows are in recon_chunk4.md).
- Real but fail-closed, left non-blocking by the agents: OI-78 (masking prohibition lost after a no_progress_retry; guards still refuse), OI-80 (wasted triage model call after time-budget stop), OI-90 (UI says "Runtime verification failed" for a goal that expects non-zero exit), OI-77 (open-findings set in memory).
- Test hazards (non-blocking): OI-49/OI-133/OI-151/OI-159 fail only with a very long TMPDIR; OI-136 polluter reproduced (62 pass alone; 26 fail after tests/test_lr_r1_m1_lv2_q4.py in one process) - can matter under xdist in the final canonical gate.
- Not verified: OI-36 (marker/npm suites not run), OI-05 (24 paths not re-measured), OI-142 host-gate write into origin .kriya/venv (UNKNOWN), OI-89 vitest part (agent had no node_modules; I ran the UI suites separately).
- SIDE EFFECT (disclosed by the chunk-5 agent): one probe wrote a launch record into the REAL ~/.kriya/authority/launches/ (authority home ignores KRIYA_STATE_DIR); the agent deleted that file and the two empty directories it created (the directory did not exist before). The chunk-2 agent saw a conftest "authority changed during the session" teardown error whose attribution it left UNKNOWN: this is a probable cause, not confirmed. No repo file was changed by any agent.
- Process note: five subagents were spawned for this reconciliation under your explicit instruction for a complete row-by-row sweep.
