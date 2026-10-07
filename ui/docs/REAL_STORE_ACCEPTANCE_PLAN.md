# First real-store acceptance: concrete plan (prepared 2026-10-05; REAL-STORE USE STILL HELD)

Status: preparation only. Nothing below was executed against the real store: no database was opened, no snapshot acquired,
no `kriya` command run. Every path was resolved from the documented settings and from `stat`/`ls` only. The run itself
waits for (a) the owner's explicit release of real-store use (D4 gate C-6 / R-4) and (b) the owner's approval of the
resolved inputs and the exact command in §2.

## 1. Resolved inputs (MEASURED unless marked)

| input | resolution | how it was determined |
|---|---|---|
| Kriya executable | `/Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2/.newvenv/bin/kriya` | the only installed Kriya that contains the KUP adapter (`kriya/kup/`); console script resolves to this checkout's `kriya/` package under Python 3.14.6. The main repository has no `kriya/kup` yet (not merged), so its venv cannot serve the acceptance run. |
| Host launch flag | `KRIYA_UI_ALLOW_REAL_KRIYA=1` at Electron launch | `ui/standalone/src/main/main.ts:22,53,63`: the configured executable is used only when this flag is `1` AND the executable setting is set and exists; otherwise the fixture stand-in answers. No code change is needed to leave fixture mode. |
| Host settings file | `~/Library/Application Support/@kriya-ui/standalone/kriya-ui-settings.json` | `main.ts:148`; the file does not exist yet (only Electron caches are present), so every setting is at its default: executable unset, configuration directory null (= HOME), recovery workspace null, editor vscode. The executable must be entered through Settings before the run. |
| Configuration directory (child `cwd`) | `/Users/sriramnanduri` (operator HOME; setting unset) | `child_env.ts::resolveConfigDirectory`; `HOME=/Users/sriramnanduri` |
| `kriya.yaml` discovery | none found in HOME (`kriya.yaml`/`kriya.yml` absent) and none in the install directory (the checkout root) | `load_config` order: CWD, then install dir; neither has one -> packaged defaults only |
| State directory | `/Users/sriramnanduri/.kriya/state` | `KRIYA_STATE_DIR` unset in the operator environment; packaged `paths.state: null` -> `~/.kriya/state` (`kriya/core/state_paths.py`) |
| Source store | `/Users/sriramnanduri/.kriya/state/traces.db`, 10,039,296 bytes, mtime 2026-10-02 18:04 (stat only; not opened); no `-wal`/`-shm` sidecar listed at the time of the stat | `ls -la ~/.kriya/state` |
| Snapshot directory (to be created by the first acquisition) | `/Users/sriramnanduri/.kriya/state/kup-snapshots/` (`policy.SNAPSHOT_DIRNAME`); retention keeps the newest 3; per-snapshot bound 512 MiB; acquisition deadline 45 s inside the host's 60 s process timeout | does not exist yet |
| Legacy pre-move store | `<checkout>/logs/traces.db`, 90,578,944 bytes, 2026-09-23 | `kriya traces` reports a pre-move database at the historical default and `doctor --production` warns; it is NOT read by acquisition (which reads `trace_db_path(cfg)` only) and no migration is proposed. Expect the warning; do not act on it during acceptance. |
| Recovery workspace | unset (proposal: keep unset for procedure steps 1-9; set it only for step 10) | with no workspace, `snapshot.acquire` performs no run-active check; with one, acquisition is refused while a run is active there (`ACQUISITION_REFUSED_RUN_ACTIVE`). The owner names the workspace for step 10; nothing is proposed here (the matrix arm workspaces under `~/kriya-cagc-v2/{A,B}/ws` are frozen evidence and should not be the first choice). |
| Child environment | `PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin`, `HOME=/Users/sriramnanduri`, `PYTHONDONTWRITEBYTECODE=1`; nothing else | `child_env.ts` (mirrored by `tests/_kup_fixtures.py::host_child_env`) |

Expected size of the first snapshot: about 10 MB (the store's current size), far below the 512 MiB bound; expected duration:
seconds, far below the 45 s deadline.

## 2. The exact acquisition command (for approval; NOT run)

Through the GUI (the intended path), "Acquire new snapshot" makes the host spawn, with `cwd=/Users/sriramnanduri` and the
child environment above:

```
/Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2/.newvenv/bin/kriya traces --json --snapshot
```

(`--workspace <absolute path>` is appended only when the recovery workspace setting is set.)

Terminal equivalent, byte-for-byte the same environment policy, for the owner's own verification if wanted:

```
cd /Users/sriramnanduri && env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin HOME=/Users/sriramnanduri PYTHONDONTWRITEBYTECODE=1 \
  /Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2/.newvenv/bin/kriya traces --json --snapshot
```

Expected answer: one KUP envelope, `operation: snapshot.acquire`, `consistency.kind: snapshot_copy`, `data.snapshot_id` matching
`^\d{8}T\d{12}Z-[0-9a-f]{8}$`, `data.size` about 10,039,296, `data.rows` the run count, `data.pruned: []`. Files created:
`~/.kriya/state/kup-snapshots/<id>/traces.snapshot.db` (0400) and `manifest.json` (0400). The source `traces.db` keeps its inode
and size (the backup API reads inside one read transaction; the acquisition sandbox evidence is `handover/evidence/KUP/`).

Pre- and post-acquisition stat (procedure step 2), read-only:

```
stat -f '%N size=%z mtime=%m inode=%i' ~/.kriya/state/traces.db ~/.kriya/state/traces.db-wal ~/.kriya/state/traces.db-shm 2>&1
```

## 3. Launch sequence once released (preparation; follows `POST_MATRIX_READINESS.md` §7 step by step)

1. `cd .../tmp/kriya-demo1-attribution-fix-2/ui && KRIYA_UI_ALLOW_REAL_KRIYA=1 npm start -w @kriya-ui/standalone`
2. Settings -> Kriya executable = the path in §1 -> Save. Leave the configuration directory blank (HOME). Leave the recovery
   workspace blank until step 10 of the procedure. Close settings.
3. Trust strip must now read Host `electron` WITHOUT "(fixtures - matrix protection D-9)", Configuration directory
   `/Users/sriramnanduri` ("default: operator HOME ..."), and after the first `capabilities` answer, Kriya = the installed
   version/commit. If the strip still says fixtures, stop: the flag or the executable setting did not take.
4. Take the pre-acquisition stat (§2). Then follow §7 of `POST_MATRIX_READINESS.md` from step 3 ("Acquire new snapshot" once),
   recording every measured value, and stop on any failed criterion.

## 4. Readiness blockers and approvals

- **Blocker (owner statement):** real-store use is held until the owner explicitly releases it. Nothing in §2-§3 runs before that.
- **Approval requested:** the resolved inputs in §1 (executable, HOME as configuration directory, `~/.kriya/state/traces.db` as the
  source, unset workspace) and the exact command in §2.
- **Prerequisite:** the full Kriya suite in the owner's terminal (`POST_MATRIX_READINESS.md` §4) should be green first; the KUP
  tests in this checkout were last green at `b531404`.
- **No technical blocker found:** the executable exists and resolves to this checkout; the state directory and store exist; the
  snapshot directory will be created by acquisition; the host needs only the launch flag and the executable setting.
- **Expected non-blocking observations:** the legacy `logs/traces.db` warning; the host's 60 s timeout versus the 45 s acquisition
  deadline (ample for a 10 MB store).
