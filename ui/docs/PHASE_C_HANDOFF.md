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

The two `KUP:` commits are the merge candidates for the main repository (after D-9 is lifted and the owner has
coordinated with the other session, per the Repository strategy); the GUI-C commits stay in this checkout.

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
3. Snapshot integrity on every query is size/mtime against the manifest; the SHA-256 is verified at acquisition
   and by `--snapshots --verify`, not per query (cost on large stores).
4. The snapshot directory inherits the state directory's permissions model; files are 0400/0700, but a user with
   write access to the state directory can still delete snapshots (then `SNAPSHOT_UNAVAILABLE`).
5. The hot-journal fixture needs a page-cache spill to be hot; a zero-header journal reads fine (documented in A1).
6. The KUP `history.detail` returns `context`/`attribution`/`diagnostics`/`comparisons`/`output` as `not_recorded`
   (P-30); the telemetry topic is separate.
7. Explicit prune while a host displays a snapshot makes that host's next read `SNAPSHOT_UNAVAILABLE` (typed, by
   design); there is no cross-process "in use" lease.
8. Acquisition of a rollback-journal (non-WAL) store holds a shared lock for the copy duration and would block a
   writer until the deadline; Kriya stores are WAL, so this is theoretical here.
