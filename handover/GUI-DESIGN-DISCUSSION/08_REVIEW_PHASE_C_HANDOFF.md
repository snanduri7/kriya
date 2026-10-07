# 08 — Review of the Phase C hand-off (Claude, reviewer)

**Date:** 2026-10-04.
**HEAD:** `b85d333`.
**Kriya-side commits:** `db96b12`, `f4bd13f`.
**Governing gate:** `handover/GUI-D4-READ-STRATEGY/03_GATE.md` (C-1 to C-6).
**Method:** read-only code inspection. I did not run tests, to stay within D-9.

## Verified (TRACED / MEASURED, read-only)

- **Scope of `61a867f..HEAD` in `kriya/` and `tests/`:**
  - `kriya/cli.py` (+136);
  - the new package `kriya/kup/` (`acquire`, `inspect`, `store`, `policy`, `cli_ops`);
  - new tests and goldens;
  - one audit-table line in `tests/test_file_integrity_contract_001.py`, which registers 5 write sites in `kriya/kup/acquire.py`. That is legitimate: it is the write-site audit doing its job.
- **Consistency mechanism** (`kriya/kup/acquire.py:134-160`):
  - a raw (non-WAL-patched) `mode=ro` source connection with `NO_CKPT_ON_CLOSE` and `wal_autocheckpoint=0`;
  - an explicit `BEGIN` plus a read pins one snapshot; the stepped `backup()` runs inside it; then `COMMIT`;
  - the destination is converted to `journal_mode=DELETE`, then `quick_check`;
  - files chmod `0400`, an atomic `rename` publish, a `0700` directory and an `flock` acquisition lock.

  This matches the gate. The claim that the barrier tests break when the pinned transaction is removed is consistent with this code.
- **CLI** (`cli.py`):
  - KUP flags without `--json` are a usage error;
  - text mode bootstraps logging itself and keeps `limit` defaulting to 20, so the output path is unchanged (goldens exist);
  - the `--json` path skips logging;
  - `ConfigAuthorityError` becomes `CONFIG_AUTHORITY_REFUSED` with no retry;
  - typed `INVALID_REQUEST` for mixed modes.
  - **Note:** the `--help` text of `traces` changed (new options, `show_default="20"`). That is acceptable: C-6 protects command behaviour, not help output.

## Verdict

**The Phase C hand-off is ACCEPTED for fixture scope.** This is careful, gate-faithful work.

**Two findings must be fixed before the `KUP:` commits are merged into the main repository.** Both concern the gap between the test environment and how the real app launches Kriya.

## Findings

### F-1 — MAJOR — The zero-write test runs in an environment the real app does not provide (bytecode writes)

- **Evidence: TRACED.**
  - `tests/test_kup_write_boundary.py:94` runs the CLI with `PYTHONDONTWRITEBYTECODE=1`.
  - The real Electron host launches Kriya with **only `PATH`** in its environment (`ui/standalone/src/main/kriya_process.ts:40`, `main.ts` real branch).
  - `sys.dont_write_bytecode = True` is set inside `traces` (`cli.py`), **after** `kriya.cli` and everything it imports at module level have loaded.
- **Consequence (INFERRED):** on a fresh or editable install, the first GUI-launched command can write `__pycache__/*.pyc` into the Kriya **source/install tree**. P-28 forbids automatic bytecode writes. The sandbox test cannot see this, because its environment already suppresses bytecode.
- **Fix:**
  1. The host passes the **constant** `PYTHONDONTWRITEBYTECODE=1` to the real child. This is a fixed value, not inherited from the user's environment, so P-31 is respected.
  2. The write-boundary test builds the child environment with **the same function the host uses**, or an exact copy of its rule, so the two cannot drift.
  3. Add a negative control: without the variable, a fresh tree shows `.pyc` write attempts under the sandbox. This proves the test can detect the problem.

### F-2 — MAJOR — The real app may resolve a different store than the user's terminal `kriya`

- **Evidence: TRACED.** The real child gets an environment containing only `PATH`. No `HOME` and no `KRIYA_*` variables (state directory, config path) are passed. `KRIYA_TRUST_FILE` is correctly not passed.
- **Consequence (INFERRED):** if the owner's setup relies on environment variables, the GUI reads, and acquires snapshots from, a **different history store** than the user's `kriya` uses. The trust strip shows the resolved path, so the difference would be visible, but nothing tests it.
- **Fix, an owner decision recorded in the hand-off:** an explicit **allowlist** of environment variables passed through. Candidates: `HOME` and the specific `KRIYA_*` path variables. **Never** `KRIYA_TRUST_FILE` or any variable that grants authority.

  Add a test: given the same allowlisted environment, `kriya traces --json --capabilities` from the GUI and from a shell report the same resolved `source.state_directory`. Run it on fixtures now, and on the real setup after D-9 is lifted.

### N-1 — Note — The D-9 interpretation question

The agent asked whether its sandboxed CLI subprocess test, run against a fixture store with all home and root paths redirected, counts as a "real Kriya CLI invocation".

**Reviewer view:** it is within D-9's intent. D-9 protects the live matrix, real stores, `~/.kriya` and models, and this test touches none of them. Keep it enabled. The owner can overrule.

### N-2 — Note — Before the merge into the main repository (Repository strategy, items 2 to 4)

1. Run the full suite **after the matrix finishes**:
   `ulimit -n 256; .newvenv/bin/pytest -q -n 2 --dist loadgroup`.
2. The write-site audit fails only because of the owner's untracked `kriya/workflow/orig-attempt.py`. Move that file out of `kriya/` (or delete it) before the run. It is not part of any commit.
3. After merging into the main repository, re-run there:
   - the KUP tests;
   - the byte-identical goldens;
   - the sandbox test, with the F-1 fix;
   - the full suite.
4. The first real acquisition happens only after D-9 is lifted, through this same procedure (03_GATE C-6). Record its duration and size.

## Next instruction to the agent

1. Fix F-1, with the negative control, in a `KUP:` commit (test side) and a `GUI-C:` commit (host side).
2. Implement F-2's allowlist **only after the owner names the variables**. Until then, add the resolved-path equality test with the current `PATH`-only environment, and document the limitation in the hand-off.
3. Stop again, and report the new commit ids.

## Addendum: ChatGPT's review of the same hand-off (2026-10-04). Its findings are verified and adopted.

### F-3 — BLOCKER — The UI reads the wrong field names for run events (credit: ChatGPT; I missed this)

- **Evidence: TRACED.**
  - Kriya serializes events with `RunEvent.to_dict()` (`kriya/workflow/run_events.py:28-44`, used by `workflow.py:1297`). The keys are `kind`, `attempt`, `source`, `authority`, `message`, `failure_type`, `operation`, `details` and `created_at` (epoch float).
  - The UI reads `e.event` and `e.at`:
    - `ui/shared/src/panels/Inspector.tsx:67-68` (the context filter);
    - `TrustStrip.tsx:25` (`model.role_metrics`);
    - `Timeline.tsx:74`.
  - The generated fixtures use the same invented shape, so every test passes.
- **Consequence:** on real history, the Context panel, the model strip and the timeline event names would be **empty or "(unnamed event)"**. That is silent data loss behind green tests.
- **Fix:**
  1. Align the KUP event schema and all three panels with the baseline shape (`kind`, `created_at`).
  2. Generate at least one fixture set **from Kriya's own serializer**: a small Python script builds real `RunEvent` objects (`context.known_target_package`, `developer.prompt_composition`, `model.role_metrics`) and writes `to_dict()` output. Add a test that fails if the UI drops any of those events.
  3. No protected store is needed.

### F-4 — MAJOR — Snapshot integrity is weaker than the specification

- **Evidence: TRACED.** `kriya/kup/store.py:126-141`: every query checks size, mtime and mode. The full SHA-256 check runs only at acquisition and with `--snapshots --verify`.
- **Fix:**
  - **(recommended)** the host requests digest verification when it **pins** a snapshot for a browsing session (`snapshot.list --verify` for that id), and per-query checks stay metadata-only;
  - **or** the owner explicitly accepts the weaker check.
- Either way, the hand-off must state the guarantee in exactly those terms: "digest verified at pin; metadata checked per query".

### D-9 ruling needed (replaces my N-1)

- ChatGPT reads D-9 literally: *no real Kriya CLI invocation, including fixture subprocesses*. On that reading, the sandboxed CLI test was a deviation, though a well-isolated one.
- Both reviewers recommend the same remedy: **the owner explicitly permits isolated fixture CLI tests from now on**. That means:
  - temporary state, home and root paths;
  - no `~/.kriya` and no `~/kriya-cagc-v2`;
  - no models.
- This **does not** lift protection for real stores or models.

## Revised next instruction to the agent (supersedes the list above)

1. **F-3 first:**
   - change the schema and panels to `kind`/`created_at`;
   - add the fixtures generated from Kriya's serializer and the regression test.
2. **F-1:**
   - the host passes `PYTHONDONTWRITEBYTECODE=1`;
   - the boundary test uses the host's environment rule;
   - add the negative control.
3. **F-4:** digest verification at pin time, and the guarantee wording.
4. **F-2:** only after the owner names the environment allowlist. Until then, add the resolved-path test with the current environment.
5. **Commit discipline:**
   - Kriya-side fixes as an extra `KUP:` commit;
   - UI fixes as `GUI-C:`;
   - the merge candidates become `db96b12`, `f4bd13f`, then the corrective `KUP:` commit(s), in that order.
6. Stop and report.

**Still on hold:**
- the merge into the main repository;
- any real-store use;
- the full suite at `-n 2`.

All wait for the matrix to finish and for the owner to release them.

## Addendum 2: corrective hand-off review (HEAD `8d6fe45`, 2026-10-04)

**Verified (TRACED, read-only; tests not re-run):**
- **F-3 RESOLVED.**
  - The panels now read `kind`, `created_at` and `message` (`Inspector.tsx:67-68`, `Timeline.tsx:73-74`).
  - There are no remaining reads of `e.event` or `e.at`.
  - `8065005` pins the adapter path to the real `RunEvent.to_dict()` output, written through `TraceLogger.log_run`.
- **F-1 RESOLVED.**
  - `ui/standalone/src/main/child_env.ts` sets fixed values for `PATH` and `PYTHONDONTWRITEBYTECODE=1`.
  - The sandbox test runs under the same rule, and the stray `-B` is removed.
  - Negative control: 90 `__pycache__` write attempts without the variable, which proves the check can detect the problem.
- **F-4 RESOLVED.** `snapshot.verify` checks one exact snapshot id before any pinned read. The wording "digest verified at pin; metadata checked per query" is used consistently.
- **F-2 RESOLVED, per the owner's policy.** Allowlist: `PATH` and `PYTHONDONTWRITEBYTECODE` (both fixed), the operator's `HOME`, and `KRIYA_STATE_DIR` only when it is set and absolute. No trust or authority variables, no `PYTHONPATH`. The parity test passes for three configurations.

### F-5 — MAJOR (UI side only) — The real child has no fixed working directory

- **Evidence: TRACED.**
  - The real-branch call `runKriya({ executable: configured, argv })` in `main.ts` passes no `cwd`.
  - So the child inherits Electron's working directory: `/` when the app is launched from Finder, the shell's directory when it is launched from a terminal. The agent flagged this itself.
- **Why it matters:**
  - Kriya finds `kriya.yaml`, and applies SEC-009, from its working directory.
  - A project whose `kriya.yaml` sets `paths.state` would therefore resolve a **different history store** depending on how the app was started.
  - P-5 (`01_PROPOSAL`, carried into v2) required the working directory to be **explicitly the selected workspace**.
- **Fix:**
  1. Every real KUP call runs with `cwd` set to the selected workspace's absolute path.
  2. With no workspace selected, history operations are refused with a typed error until one is chosen. `capabilities` may run from a fixed neutral directory (`HOME`).
  3. Extend the parity test with a workspace whose `kriya.yaml` sets `paths.state`. The GUI and a shell run in that directory must resolve the same store.

### Minor note

The child-environment rule exists twice: in TypeScript, and in `tests/_kup_fixtures.py`. The key set is pinned by tests, so this is acceptable for M1. Later, generate both copies from one JSON definition in `ui/kup/`.

## Status

- **Kriya-side merge candidates, complete, in order:**
  1. `db96b12`
  2. `f4bd13f`
  3. `f4688ea`
  4. `8065005`
  5. `f876f49`

  F-5 is UI-only and does not change them. They are ready, but still held until:
  - the matrix finishes;
  - the full suite passes at `-n 2` (after moving the untracked `kriya/workflow/orig-attempt.py` out of `kriya/`);
  - the owner coordinates with the main-repository session.
- **UI:** fix F-5, then M1's remaining step is acceptance against the real CLI (Phase F2) after D-9 is lifted.

## Addendum 3: F-5 fix refined after ChatGPT's review (2026-10-04). This supersedes the F-5 fix above.

ChatGPT's version is better than mine, and I adopt it. Setting the working directory to the **recovery workspace** would make the history store change whenever the user picks a different workspace to assess. That merges the two contexts that D-6 / R1-4 deliberately keep separate.

**Fix (UI side only):**
1. **A separate, visible "Configuration directory" setting.**
   - It must be an absolute path to an existing directory, validated, with no control characters.
   - It is shown in the trust strip next to the resolved history-store path.
   - It is used as the child's `cwd` for **every** real `kriya` call. Electron's own launch directory is never inherited.
2. **It is independent of the recovery workspace.** `runs status --workspace PATH` keeps its explicit workspace argument. Changing the workspace must not change the configuration directory or the history store, and the reverse.
3. **Default value:** the operator's `HOME`. The value is persisted, and the user changes it explicitly. When it is unset or invalid, `capabilities` and history operations return a typed error until it is fixed.
4. **Test:** launch the host logic from different working directories, including `/` and an unrelated temporary directory. Configuration and store resolution must be identical in all of them. Add a case where the configuration directory contains a `kriya.yaml` that sets `paths.state`.
5. **No new configuration environment variable.** The mirrored environment rule stays as it is, covered by the existing parity checks.

**The stop point stays in place.** The main-repository merge, real-store integration and the full suite all still wait for the owner to release the matrix protection.
