# 02 — Review: guarded immutable read strategy

Reviewer: ChatGPT. Date: 2026-10-04.
Reviewed: `01_PROPOSAL.md`, the Phase A reports, the original gate and implementation instructions, and `GUI-DESIGN-DISCUSSION/07_REVIEW_STOP_POINT_1.md` including its addendum.
Repository observed: `codex/fix-demo1-attribution` @ `efef852`.
Method: read-only source/report inspection and primary SQLite documentation. No fixture execution, real Kriya invocation, model run or protected-store access. The A1 measurements are reported evidence, not independently rerun here.

## Verdict

**Do not approve Q-1 as a verified snapshot guarantee as written.** The proposal makes a useful best-effort change detector into a stronger correctness guarantee than it establishes. Phase C remains on hold. B and D can proceed as specified below; no approval of immutable reads is implied.

This is the topic's only review. There is no second review. The owner decides in `03_GATE.md`, including the findings and any bounded alternatives; this review does not amend the existing D-4 gate.

## Findings

### R-1 — BLOCKER — Q-1 does not establish that the live file is immutable during the query

**Evidence: TRACED (proposal Q-1 and Residual risk; primary SQLite documentation).** Q-1 compares inode, size and mtime and checks side-file absence at two observation points. It supplies no writer exclusion, immutable artifact, atomic observation of all these conditions, or other invariant holding across the read. The residual-risk section itself acknowledges that identical final metadata is possible after a change; timestamp precision is not a synchronization guarantee, and inode detects replacement only when a different inode remains at the sampled pathname.

SQLite's [immutable parameter documentation](https://www.sqlite.org/uri.html#uriqueryparameters) says it assumes the file cannot be modified even by another process, disables locking and change detection, and can produce incorrect results or corruption errors if the assumption is false. Q-1 does not enforce that assumption. Denying writes by the reader does not deny writes by another process. “One read” or one connection does not restore the locking disabled by immutable=1.

**INFERRED consequence:** the guard can detect many ordinary races and discard their output, but cannot justify “never stale,” “detects any writer,” or the suggested `quiescent_snapshot_verified` label. A finite race test can demonstrate behavior in tested schedules; it cannot prove the missing invariant. Check needed: a defined consistency guarantee and a mechanism whose invariant holds during the entire query, supported by source reasoning and adversarial fixtures.

**Required owner disposition:** retain D-4, or explicitly approve a bounded best-effort strategy with honest consistency/limitations, or authorize a strategy that reads an actually stable artifact / excludes all relevant writers. Such mechanisms may need additional scope and permissions; none is authorized by this review. Do not ship Q-1 under a verified-snapshot claim. Stronger stat checks or content hashes alone remain observations, not writer exclusion.

### R-2 — MAJOR — Acceptance conflates consistency with freshness and lacks deterministic race coverage

**Evidence: TRACED (proposal Acceptance step 2 and Q-1's safety explanation).** The proposal requires zero “torn or stale” results without defining a reference snapshot, read linearization point, or freshness boundary. A valid result may predate a commit occurring after observation; no reader can promise indefinitely current data. Two hundred randomized races provide sampling coverage, not a proof of correctness.

**Required change:** define the selected consistency level in the owner gate before finalizing the KUP enum. Timestamp the observation and avoid “latest” or verified quiescence unless warranted. If a race experiment is authorized, use barriers to force writer/reader interleavings as well as randomized runs; query multiple related tables/rows with cross-field invariants and known committed fixture generations. Record all reads and refusals, and distinguish a consistent older generation from a mixed/torn generation. Include checkpoint during page reads, writer start/finish between checks, database replacement, and boundary side-file appearance/disappearance. The expected outcome must match the selected guarantee, not a blanket ban on any older result.

These are fixture-only future acceptance requirements, not authorization to run immutable experiments while D-4 remains in force.

### R-3 — MINOR — Side-file presence is a database-state observation, not proof of an active Kriya run

**Evidence: TRACED (proposal F-2/Q-2; [SQLite WAL file lifecycle](https://www.sqlite.org/wal.html#the_wal_file)).** SQLite may retain side files after abnormal shutdown or with persistent-WAL settings, and open readers can also keep a database connection alive. The proposal itself allows the orphaned-side-file case. Therefore side files do not establish that a Kriya run is active or that live progress is available.

**Required clarification:** refusing conservatively when side files exist is acceptable; describe the reason as “side files present; selected read strategy unavailable.” Do not derive RUN_ACTIVE from it. Use Kriya's existing workspace assessment only for that status, and retain separate history-store/workspace contexts.

### R-4 — MAJOR — The real-store-copy test needs an explicit safe acquisition procedure

**Evidence: TRACED (proposal Acceptance step 4; [SQLite WAL documentation](https://www.sqlite.org/wal.html#the_wal_file)).** The proposal correctly delays access until D-9 is lifted, but “taken after a clean close” does not describe how all relevant connections are known to be closed or how concurrent reopen/checkpoint is prevented during acquisition. SQLite warns that separating a database from its WAL can lose committed state. Copying just traces.db while another process can write does not establish a stable artifact.

**Required gate condition:** after explicit owner release of D-9, acquire an owner-approved, consistent copy with all relevant writers controlled or through a separately approved SQLite-aware procedure. Never delete sidecars, checkpoint the original, change its journal mode, or copy only the main file from an active WAL store. Testing a stable copy proves behavior on that copy, not safety of Q-1 against a mutable original. Refuse/report inability to obtain the copy safely rather than improvise an acquisition method.

## Owner choices at this topic's gate

**G-1 — INFERRED recommendation:** retain the present D-4 until the owner chooses an explicit read guarantee. If strict zero-write remains essential, investigate reading an owner-supplied stable snapshot as a bounded option. Snapshot acquisition/storage is a distinct operation and must have its own authorization; it cannot be hidden inside zero-write inspection. If a best-effort direct immutable read is chosen instead, record its weaker guarantee and the rationale for accepting R-1's risk. Verification: gate wording, KUP labels and deterministic fixture acceptance must agree.

**G-2 — INFERRED recommendation:** Phase A's UI findings need no rollback. Accept its evidence provisionally, not full M1 acceptance. Keep the reported 393 MB idle footprint an owner decision; VoiceOver and cursor placement remain outstanding. Verification: explicit owner acceptance and recorded manual checks, plus the Phase E soak below.

## Instructions to the coding agent

These instructions cover the work the owner has requested to continue. They do not grant the new read-strategy approval reserved for `03_GATE.md`.

1. **Continue Phase B on fixtures.** Complete JSON Schemas, runtime validation, generated TypeScript types and the Java-generation compatibility check; define the HostAdapter contract in `ui/kup`. Keep consistency values and database-state error reasons explicitly provisional until the D4 gate. Do not finalize or emit `quiescent_snapshot_verified`. Generated types may carry explicitly provisional schema definitions, but must not assert an unapproved guarantee. Preserve STORE_BUSY, READ_ONLY_UNAVAILABLE and panel availability without equating them to RUN_ACTIVE.
2. **Continue Phase D on fixtures/fake processes.** Complete the shared panels, stale-response handling, honest unavailable states and host-independence checks. Keep Prompt, Output, Diff and Why unavailable when persisted evidence does not support them. Do not add reconstructed data or direct Kriya-state reads.
3. **Hold Phase C.** No production read adapter, CLI additions, immutable-read implementation or changes under `kriya/` until the owner gates this topic. D-4 still prohibits immutable=1 and SHM exceptions. Any experimental change to the read candidate also needs explicit gate authorization; no real Kriya invocation while D-9 holds.
4. **Prepare Phase E memory checks.** Retain a reproducible 550 MB post-forced-GC regression check, reported as a diagnostic only. Add a normal-session soak without forced GC. To avoid a 1,000-cycle run finishing before the 15-minute warmup, use a two-hour run with at least 1,000 scheduled selections and periodic fixture refreshes. Record all Electron processes every five minutes, total RSS/working set with consistent units, scenario, payloads, versions, timestamps and any process churn. Report peak and trend after the first 15 minutes; the total must stay under 550 MB after warmup. Fix the quantitative trend tolerance before running; the report must include raw samples and show any sustained growth rather than hide it with endpoint averages. No DevTools GC, app restart or manual cache clearing during the soak. This refines the addendum's ambiguous “two hours or 1,000 cycles” into a usable duration/coverage check; if it conflicts with available resource limits, report and obtain the owner's decision before shortening it.
5. **Do not infer memory acceptance.** The owner still needs to accept or reject the measured 393 MB idle cost. The 550 MB ceiling is a regression criterion, not acceptance of that cost. Model-loaded measurement remains deferred until D-9 is explicitly lifted and that run is authorized.
6. **Keep manual checks open.** VoiceOver needs the owner's actual navigation check. VS Code remains “launch verified; cursor placement unverified” until visual confirmation at line 7. Do not mark end-to-end navigation verified from exit code alone.
7. **Preserve all repository and experiment rules.** No GitHub push, no main-repository writes, no protected-store access, no live models or real Kriya commands while D-9 holds. Continue local-only commits for authorized UI work, and honor the existing stop points. Keep eventual `KUP:` commits separate and self-contained only after Phase C is approved.
8. **Next report:** B/D completion and fixture verification; provisional schema fields; memory-test method/results when run; outstanding manual checks; HEAD and changed paths. Phase C stays on hold unless `03_GATE.md` explicitly permits it.

No second review is requested. The owner resolves R-1 through R-4 in `03_GATE.md` and names the permitted next read strategy.
