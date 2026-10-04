# Phase C hand-off (stop point): KUP snapshot acquisition and inspection on fixtures

Date: 2026-10-04. Authority: `handover/GUI-D4-READ-STRATEGY/03_GATE.md` (C-1..C-6), then
`06_IMPLEMENTATION_INSTRUCTIONS_M1.md` §C and the Repository strategy. Implementing agent: Claude.

Standing lines: **No GitHub push. No live model run. No real `kriya` command against any real store. No writes to
the main repository. Matrix protection (D-9) respected** - every test ran against fixture stores in temporary state
directories; the Electron shell talked only to the fixture stand-in. Nothing under `kriya/` outside the two `KUP:`
commits. Work stopped at this hand-off; nothing past it was started.

## 1. Commits to hand to the owner (local, on `codex/fix-demo1-attribution`)

| commit | kind | content |
|---|---|---|
| `db96b12` | **KUP:** (Kriya, self-contained) | `kriya/kup/` adapter: policy, store, acquire, inspect, cli_ops; `tests/test_kup_acquisition.py` (16); fixture writer `tests/_kup_fixtures.py`; write-site audit rows (`tests/test_file_integrity_contract_001.py`, `handover/FILE_INTEGRITY_CONTRACT_001.md`) |
| `f4bd13f` | **KUP:** (Kriya, self-contained, depends on `db96b12`) | `kriya/cli.py` grammar in `traces`/`runs status`; `tests/test_kup_cli.py` (27); `tests/test_kup_write_boundary.py` (sandbox); baseline goldens `tests/golden/kup/`; raw sandbox evidence `handover/evidence/KUP/` |
| `504c3ac` | GUI-C (ui/, stays here) | KUP contract finalized to the gate; generated TS/Java/validators; fixtures |
| `a65acdd` | GUI-C (ui/, stays here) | shared panels: acquire vs refresh, pinned session, labels; Electron host grammar; stand-in; test host |
| (this commit) | GUI-C docs | this hand-off, the strategy document's status line, the Electron screenshot after an explicit acquire |

The `KUP:` commits are the merge candidates for the main repository (after D-9 is lifted and the owner has
coordinated with the other session, per the Repository strategy); the GUI-C commits stay in this checkout. **After the
corrective hand-off (§6) the merge candidates are, in order: `db96b12`, `f4bd13f`, `f4688ea`, `8065005`, `f876f49`.**

## 2. What was built, against the six conditions

**C-1 Acquisition explicit, authority traced.** `kriya traces --json --snapshot [--workspace P]` is the only
acquisition; `kriya/kup/acquire.py` is the only writing module (five audited write sites). The UI has two distinct
buttons, **Acquire new snapshot** and **Refresh displayed snapshot**; a refresh or a query never acquires (test:
`Refresh never acquires`). The snapshot directory's authority is TRACED in `kriya/kup/__init__.py`: it is
`<state dir>/kup-snapshots`, where the state dir comes only from `state_paths.resolve_state_directory` (env > SEC-009-
classified `paths.state` > default), is confined to `<workspace>/.kriya/` inside a workspace, and already receives
`traces.db`; nothing resolves a path from configuration or CWD on its own, reads a trust file, or retries with
broader permission (a `ConfigAuthorityError` is the typed `CONFIG_AUTHORITY_REFUSED`; test). With `--workspace`,
acquisition is refused typed (`ACQUISITION_REFUSED_RUN_ACTIVE`) when the existing read-only recovery assessment
reports RUN_ACTIVE; that check is policy, not proof that every writer to a shared store is absent (documented).

**C-2 Pinned browsing, safe lifecycle.** Acquisition returns `snapshot_id`; `history.list/detail/prompt` REQUIRE
`--snapshot-id` (no snapshot id = typed `INVALID_REQUEST`; no implicit newest). Cursors embed the snapshot id and a
cursor from another snapshot is refused (test). The UI session pins the acquired or user-chosen id; a switch bumps
the request generations, clears list/detail/prompt and the selection (test). Retention keeps the newest 3 at the end
of a successful acquisition; explicit prune keeps `--keep N` (0 clears everything, e.g. duplicated prompts). A pruned or
unknown id is `SNAPSHOT_UNAVAILABLE`, no snapshot at all is `SNAPSHOT_MISSING`, a tampered file is
`SNAPSHOT_CORRUPT` - never a substitute. One acquirer at a time via the platform lock port (flock, OS-released on
death); a concurrent acquisition or prune gets `ACQUISITION_IN_PROGRESS` immediately; under the lock any staging
directory is PROVEN abandoned and removed; the active acquisition's staging is never touched (tests: concurrent,
SIGKILL interruption + cleanup).

**C-3 Honest consistency.** `consistency.kind = snapshot_copy`; the manifest and every history envelope carry
`acquisition_started_at`, `acquisition_completed_at`, `source_metadata_at_acquisition`, `source_metadata_now`
(stat only) and `source_metadata_changed`. The UI shows "snapshot acquired at …" and "source metadata change
detected" / "no metadata change detected (not a freshness guarantee)"; the strip never says current/latest (test
asserts the words are absent), and the model/qualification line is labelled historical. Metadata equality never
skips a requested acquisition (acquire always copies; test `test_acquire_list_detail_prompt_are_pinned…` acquires
twice on an unchanged store and gets two ids).

**C-4 Bounds.** Documented in `kriya/kup/policy.py` (and §4/§8 of the strategy doc): 512 MiB per-snapshot bound on
the DESTINATION's actual growth, checked after every backup step, plus an up-front refusal from `page_count x
page_size` read inside the backup's own read transaction; 45 s deadline checked between steps (inside the host's
60 s); 2 s busy timeout; free-space refusal (expected size + 64 MiB margin); retention newest 3 (1.5 GiB retained
ceiling), one staging at a time (2 GiB directory ceiling); oversized or interrupted acquisitions leave nothing
published; nothing active is ever deleted to force an acquisition through. The 2 GiB-vs-1 GiB inconsistency of the
specification is resolved as above.

**C-5 Acceptance evidence (fixtures, deterministic barriers).** `tests/_kup_fixtures.py::GenerationWriter`: each
generation commits a `runs` row, a `milestone_plans` row and a generation record with a digest in ONE transaction;
a consistent image satisfies `count(runs) == count(plans) == gen` and the digest; committed generations are logged
after COMMIT. The acquisition's `step_hook` forces interleavings between backup steps. MEASURED: writer commit +
PASSIVE checkpoint mid-copy, an uncommitted transaction committed mid-copy, a TRUNCATE checkpoint mid-copy, and
a file replacement mid-copy each produce exactly the generation committed before the read transaction opened;
side files appear (clean WAL) and are removed by the next writer's close; a subsequent writer commits and
checkpoints normally and the main file is untouched. Mutant M1 (read transaction removed) breaks the barrier test,
so the consistency rests on the pinned read transaction, not on luck. Sandbox (macOS `sandbox-exec`, subprocess,
unified-log attribution): acquisition with writes allowed ONLY to `-wal`/`-shm` and the snapshot directory and
inspection under complete denial both show zero attributed write attempts; raw evidence in
`handover/evidence/KUP/write_boundary_evidence_2026-10-04.json`.

**C-6 Protections.** Text-mode `kriya traces` is byte-identical to goldens captured from the unmodified baseline
(default, `-n 3`, `--all`, empty state). No source checkpoint, no source journal change, no `immutable=1`. The
KUP path never bootstraps logging (test), sets `sys.dont_write_bytecode`, creates nothing except through
`--snapshot`. D-9 kept: fixtures and the stand-in only.

## 3. Tests and checks (MEASURED, this checkout, `.newvenv`)

| suite | count | result |
|---|---|---|
| `tests/test_kup_acquisition.py` | 16 | pass |
| `tests/test_kup_cli.py` | 27 | pass |
| `tests/test_kup_write_boundary.py` | 1 (6 sandboxed CLI runs) | pass |
| adjacent: traces command, state location, platform guard, authority CLI refusal, PRD-033 CLI, strict doubles, CLI smoke | 159 + 118 | pass |
| `test_every_filesystem_write_site_is_audited` | 1 | pass (with the owner's pre-existing untracked `kriya/workflow/orig-attempt.py` set aside; that file, not this work, trips it) |
| mutation campaign on the adapter | 9 mutants | all killed (read transaction removed, WAL destination, cursor unbound, prompt leak, retention off-by-one, integrity skipped, logging bootstrap, orphan cleanup removed, directory created before the missing-store check) |
| pylint (repo rules) / ruff on the new code | - | 0 findings |
| `ui/`: `npm run check` | 99 tests + generated-output drift + Java round trip (265 files) | pass |
| Electron end-to-end on the stand-in | 5-cycle measurement after an explicit acquire | populate max 179 ms, 74 controls, 0 unnamed |

**Not run:** the full Kriya suite at `-n 2` (the owner's D-9 rule keeps the full suite out of the session; command:
`ulimit -n 256; .newvenv/bin/pytest -q -n 2 --dist loadgroup`). Everything above is evidence for this checkout only;
the merged code must be re-verified in the main repository (Repository strategy item 4).

## 4. Interpretation to confirm

The write-boundary test and the byte-identical goldens invoke the CLI entry point (`from kriya.cli import main`) -
in-process through Click's `CliRunner` for most tests, and as a `sandbox-exec` subprocess for the write-boundary
test - always against a fixture store in a temporary state directory, with HOME, log, authority, qualification,
MCP-approval and certification roots all pointed at the temporary directory, no model and no network. I read D-9's
"no real Kriya CLI invocation" as "no invocation against real stores/models"; if the owner reads it as "no CLI
process at all", the sandbox test is the one to exclude until D-9 is lifted (the rest is in-process, like the
existing `tests/test_traces_command.py`).

## 5. Remaining limitations (honest list)

1. Real store never touched: the first real acquisition (duration, size, side-file effect on the owner's `traces.db`)
   happens only after D-9 is lifted, through this same procedure (gate C-6, R-4).
2. `--workspace` RUN_ACTIVE refusal covers the selected workspace only; a store shared by several workspaces can
   have other writers; SQLite's read transaction, not that check, supplies consistency.
3. Snapshot integrity: **digest verified at pin; metadata checked per query** (corrected in §6, F-4). A modification
   of a pinned snapshot that preserves size and mtime is caught by the next explicit verification, not per query.
4. The snapshot directory inherits the state directory's permissions model; files are 0400/0700, but a user with
   write access to the state directory can still delete snapshots (then `SNAPSHOT_UNAVAILABLE`).
5. The hot-journal fixture needs a page-cache spill to be hot; a zero-header journal reads fine (documented in A1).
6. The KUP `history.detail` returns `context`/`attribution`/`diagnostics`/`comparisons`/`output` as `not_recorded`
   (P-30); the telemetry topic is separate.
7. Explicit prune while a host displays a snapshot makes that host's next read `SNAPSHOT_UNAVAILABLE` (typed, by
   design); there is no cross-process "in use" lease.
8. Acquisition of a rollback-journal (non-WAL) store holds a shared lock for the copy duration and would block a
   writer until the deadline; Kriya stores are WAL, so this is theoretical here.

## 6. Corrective hand-off (2026-10-04, after `08_REVIEW_PHASE_C_HANDOFF.md`)

Owner instructions of 2026-10-04 (five items) reviewed against the code first: F-1, F-3 and F-4 are CONFIRMED
(TRACED: `kriya/workflow/run_events.py:28-41` serializes `kind … created_at`; `kriya/cli.py` set
`sys.dont_write_bytecode` only inside the command, after `kriya.cli`'s module imports; `kriya/kup/store.py` verified the
digest only at acquisition and on `--snapshots --verify`). Two refinements were applied and are stated here: the sandbox
test also passed `-B`, which had to go for the environment to match production; and per-id verification was added as
the operation `snapshot.verify` rather than hashing every retained snapshot through `snapshot.list --verify` on each pin
(up to 1.5 GiB per pin, and not bound to one id). Standing lines unchanged: no push, no live model, no real store, no
writes to the main repository; isolated fixture-only CLI subprocesses are used under the owner's permission of
2026-10-04 (temporary HOME/state/cwd, no protected store, no model, no network).

### 6.1 Commits

| commit | kind | content |
|---|---|---|
| `f4688ea` | **KUP:** | `snapshot.verify` (`traces --json --snapshot-verify <id>`): SHA-256 of exactly one published snapshot; `SNAPSHOT_CORRUPT` with the id on mismatch; test proves a same-size same-mtime tamper is invisible to the per-query check and caught here |
| `8065005` | **KUP:** | adapter event contract pinned: real `RunEvent.to_dict()` through `TraceLogger.log_run` -> acquisition -> `history.detail` comes back verbatim (keys, float `created_at`); fixture store events use the serializer's shape |
| `f876f49` | **KUP:** | write-boundary test under the production launch environment (`host_child_env`, fresh tree without `__pycache__`, PID-attributed denials, network denied), bytecode negative control, GUI/CLI store-resolution parity test; raw evidence in `handover/evidence/KUP/` |
| `9ab3038` | GUI-C | RunEvent schema = serializer shape (`kind`, `created_at` required); Timeline/Inspector/TrustStrip read `kind`/`created_at`/`details`; `ui/fixtures/serializer_events.py` + committed `fixtures/serializer/run_events.json`; tests at kup/shared/test-host; TS/Java/validators regenerated |
| `c3cab95` | GUI-C | `snapshot.verify` in the contract and hosts; the App verifies the exact id before pinning (acquire and choose) and never pins on failure or a misnamed answer; strip wording "digest verified at pin (…); metadata checked per query"; four App mutants killed |
| `75fd2a6` | GUI-C | `child_env.ts` (fixed PATH, fixed `PYTHONDONTWRITEBYTECODE=1`, operator HOME, absolute operator `KRIYA_STATE_DIR` only) wired into `main.ts`; spawn-level and structural tests |
| (this commit) | GUI-C docs | this section, strategy document §5, README |

### 6.2 Instruction-by-instruction

1. **Event contract.** Schema and every consumer use Kriya's serializer keys; the invented `event`/`at`/`payload` shape is
   refused by the validator (`kind` and `created_at` required - every stored event is produced by `RunEvent.to_dict()`;
   TRACED through `workflow.py:1297`, `run_trace.py`, `workflow_controller.py:3870-3891`, `milestones.py`,
   `workflow.py:417`). Fixtures for context, prompt composition and model metrics (plus a `model.transition` with a real
   `ModelRequestProfile`) are generated from `RunEvent.to_dict()` and read back through the adapter by
   `ui/fixtures/serializer_events.py`; `run-serializer-events` carries them verbatim. Tests fail if they disappear at
   any layer: kup (validation, exact key set), shared (timeline names, context list, model strip, evidence payload),
   test-host (rendered through the browser host), Kriya (`test_history_detail_returns_run_events_exactly_as_kriya_serializes_them`).
2. **Bytecode.** The real host passes the constant `PYTHONDONTWRITEBYTECODE=1`; the sandbox test runs the CLI with
   exactly the host policy (no `-B`, no `PYTHONPATH`, no test-only variables) from a fresh tree. MEASURED: 0 attributed
   write or network attempts across acquisition and six inspection commands; negative control without the variable:
   90 `__pycache__` write attempts inside the install tree, all denied (`handover/evidence/KUP/negative_control_bytecode_writes_2026-10-04.json`).
3. **Digest at pin.** `snapshot.verify` is bound to the exact id (Kriya refuses any other id typed; the UI refuses an
   answer naming another id); failure prevents pinning (tests: corrupt choose, corrupt acquire, misnamed answer;
   mutants M-A..M-D killed). Per-query metadata checks retained. Guarantee documented in the strip, README, strategy
   document and §5 item 3 as "digest verified at pin; metadata checked per query"; no protection against every later
   modification is claimed.
4. **Environment policy.** `child_env.ts` implements exactly the stated policy; refusals are typed `HOST_ERROR`
   envelopes. Fixture-specific construction stays in `main.ts::fakeEnv` (stand-in) and in the tests' temporary roots.
   Parity test (`tests/test_kup_host_environment.py`): GUI child vs operator shell resolve the same state directory and
   trace database for HOME-only, operator `KRIYA_STATE_DIR` and a `kriya.yaml` `paths.state` in the working directory.
5. **Isolated CLI tests** are now used as permitted; nothing touches a real store or a model.

### 6.3 Tests and checks (MEASURED, this checkout, `.newvenv`)

| suite | count | result |
|---|---|---|
| `tests/test_kup_cli.py` | 29 | pass |
| `tests/test_kup_acquisition.py` | 16 | pass |
| `tests/test_kup_write_boundary.py` | 2 (7 + 1 sandboxed CLI runs) | pass |
| `tests/test_kup_host_environment.py` | 4 | pass |
| `tests/test_traces_command.py` (adjacent) | all | pass |
| pylint (repo rules) / ruff on every touched file | - | 0 findings |
| `ui/`: `npm run check` | 118 tests (kup 10, shared 36, test-host 32, standalone 40) + generated drift + Java round trip (266 files) | pass |
| App mutants (verify/pin logic) | 4 | all killed |

**Not run:** the full Kriya suite at `-n 2` (on hold per the owner). The write-site audit is still tripped only by the
owner's pre-existing untracked `kriya/workflow/orig-attempt.py`, which was not moved or deleted.

### 6.4 Remaining limitations (additions to §5)

9. The child environment rule exists twice by necessity (TypeScript in the GUI checkout, Python in the Kriya tests);
   both are pinned to the same key set and constants by tests, but a change to one must be mirrored by hand.
10. (Resolved in §7, F-5.) The child's working directory is now the explicit, validated configuration directory, never
    Electron's own launch directory. No config-path variable was invented, per the owner.
11. The real store has still never been touched; the first real acquisition and verification follow after D-9 is lifted.

## 7. F-5: explicit, visible configuration directory (owner instruction and 08 addendum 3, 2026-10-04)

Reviewed against the code first: CONFIRMED that `main.ts` passed no `cwd`, so the real child inherited Electron's
working directory, and Kriya discovers `kriya.yaml` (and classifies it under SEC-009) from its working directory.

| commit | kind | content |
|---|---|---|
| `b531404` | **KUP:** (tests only; merge candidate 6) | `tests/test_kup_host_environment.py`: identical configuration/store resolution when the host sits in `/`, HOME or an unrelated directory and passes the configuration directory (with a `kriya.yaml` naming `paths.state`) as the child's cwd; equal to the operator's shell started there; a different configuration directory resolves a different store. Acquisition with `--workspace W` from configuration directories A and B writes under A's and B's stores and nothing under W; `runs status --workspace W` assesses W from either and loads no configuration |
| `59106b8` | GUI-C | host setting `configDirectory` (null = operator HOME), `resolveConfigDirectory` (absolute, no control characters, existing directory), passed as `cwd` to every kriya child (real and stand-in); invalid = typed `HOST_ERROR` on every call; `HostInfo` carries `configDirectory`/`configDirectorySource`/`configDirectoryProblem`; trust strip shows "Configuration directory" with its source and "Workspace (recovery assessment)"; schema and generated outputs updated |
| (this commit) | GUI-C docs | this section, README |

- **Explicit and visible:** the strip shows the directory and whether it comes from the setting or the HOME default,
  next to the resolved history-store path; an invalid value is shown with the host's reason.
- **Validated cwd, never inherited:** `main.ts` never reads `process.cwd()` (structural test); a real spawn from `/`
  and from the temporary directory lands in the explicit cwd (stand-in `echocwd`).
- **Distinct from the recovery workspace:** `workspacePath` only travels as an explicit argument; the Kriya-side test
  proves swapping the configuration directory changes the store and not the assessed workspace, and that the workspace
  receives no file.
- **No new configuration environment variable**; the mirrored environment rule is unchanged.
- **Tests (MEASURED):** `tests/test_kup_host_environment.py` 6 pass (4 + 2 new); `npm run check` 125 tests
  (kup 10, shared 38, test-host 32, standalone 45) + generated drift + Java round trip over 266 fixtures pass; pylint and
  ruff clean. Not run: the full suite at `-n 2` (still on hold).
- **Limitation:** M1 has no settings form; `configDirectory` is edited in the host's settings file like the other
  settings, and `HOME` is the default until then.

**Kriya-side merge candidates, in order:** `db96b12`, `f4bd13f`, `f4688ea`, `8065005`, `f876f49`, `b531404`.
