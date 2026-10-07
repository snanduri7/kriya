# First real-store acceptance: results (2026-10-05, steps 1-9 executed; STOPPED before step 10 as instructed)

Release: the owner approved the resolved inputs in `REAL_STORE_ACCEPTANCE_PLAN.md` and explicitly released real-store use for
the documented procedure (`POST_MATRIX_READINESS.md` §7), with the recovery workspace unset and a stop before step 10.
Full-suite prerequisite (owner's terminal, reported by the owner): 8,482 tests, 14 warnings, zero failures, two workers with
`--dist loadgroup`.

Executed from this session at the KUP protocol level with the exact child policy the Electron host applies
(`cwd=/Users/sriramnanduri`, `env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin HOME=/Users/sriramnanduri
PYTHONDONTWRITEBYTECODE=1`, argv exactly as `ui/standalone/src/main/kriya_argv.ts` builds it) and, for the panels, by rendering the
exported envelopes through the shared App in jsdom (`ui/test-host/test/real_store_acceptance.test.tsx`, run with
`KRIYA_REAL_EXPORT_DIR`). The Electron window itself was not driven; what only a human at the window can confirm is listed under
§3 as pending. Exports stay outside the repository (session scratchpad); no goal, prompt or output text is copied here.

Executable: `<checkout>/.newvenv/bin/kriya` (Python 3.14.6, package = this checkout). Kriya identity reported by `capabilities`:
version 0.1.0, commit UNKNOWN, build_provenance unavailable (the documented values for an editable checkout), implementation
`kriya-kup/1`, SQLite 3.53.4, 9 operations.

## 1. Measured results (criteria from §7 of the readiness document)

| step | criterion | measured | verdict |
|---|---|---|---|
| 1 Identity | store path is the intended real store; identity reported | `source.trace_database = /Users/sriramnanduri/.kriya/state/traces.db`, `state_directory = /Users/sriramnanduri/.kriya/state`; envelope and payload valid against the generated validators; `consistency.kind = not_applicable` | PASS |
| 2 Source write boundary | main database unchanged by the GUI path; writes only under the owned snapshot directory and approved sidecars | `traces.db` before/after every step: size 10,039,296, mtime 1790944489, inode 227107355, sha256 `60fb4539624371a1a68767973f049118cd91b876ccacf7227813f220252e4d94` identical at start, after acquisition 1, after browsing, after acquisition 2 and at close-out. Paths under `~/.kriya` newer than the start: `state/kup-snapshots/` (owned), `state/traces.db-wal` (0 bytes) and `state/traces.db-shm` (32,768 bytes) - the sidecars SQLite creates when a WAL-mode database is opened, listed by the D4 gate as approved sidecar writes - and the `state/` directory mtime. No file under `~/.kriya/logs`, no `.pyc` under `kriya/`, nothing elsewhere. | PASS |
| 3 Explicit acquisition | one `snapshot.acquire`; id, duration, size recorded; retention ≤ 3 | one invocation (`traces --json --snapshot`), exit 0, 0.83 s wall: `snapshot_id 20261005T020254001807Z-bb97d302`, `size 10039296`, `rows 348`, `duration_ms 26.4`, `backup_steps 10`, `pruned []`, `orphans_removed []`; published as `kup-snapshots/<id>/traces.snapshot.db` (0400) + `manifest.json` (0400) in a 0700 directory; `.acquire.lock` (0600) remains in the snapshot directory | PASS |
| 4 Snapshot integrity | digest verified at pin for exactly this id | `snapshot.verify`: `digest_verified true`, `sha256 027635a14de47e4071276d0828151bb110a4a5c7b706d8f6d58728aa616daa23`, `size 10039296`, `verified_at 2026-10-05T02:02:54.953219Z`, `source.snapshot_id` = the acquired id; independent `shasum -a 256` of the snapshot copy equals the reported digest; `--snapshots --verify` lists `digest_verified true` | PASS |
| 5 Pinned browsing | every history read carries the pinned id; prompt only on request; browsing never acquires | `history.list -n 50` page 1 (50 runs) and page 2 via the opaque cursor (50 runs, no run_id overlap), two `history.detail` records (run `1df27f7a`, status `failure`, 4 attempts, 54 events, 8 gates, 5 evidence records; run `280bd867`, status `success`, 21 events, 3 gates, 1 evidence record), one `history.prompt` (role planner, 691 characters, content not copied): all envelopes valid, `source.snapshot_id` and `consistency.snapshot_id` equal the pinned id; an unpublished id returns the typed `SNAPSHOT_UNAVAILABLE` carrying the id with `data: null`. In the rendered App the prompt is requested only on "Load prompt" and `snapshot.acquire` is called once. | PASS |
| 6 Freshness labels | labels from `consistency`; "no metadata change" labelled not a guarantee; change detected without acquisition | `snapshot.list`: `consistency = live_observation`; the acquired snapshot shows `source_metadata_at_acquisition` without sidecars and `source_metadata_now` with `wal_size 0`, `shm_size 32768`, so `source_metadata_changed: true` - a true statement (the acquisition's own read connection created the sidecars). Rendered strip wording ("source metadata change detected"; "digest verified at pin (...); metadata checked per query") verified in jsdom; the visual strip is pending (§3). | PASS (see observation O-2) |
| 7 Snapshot switch | verification first; invalidation; pruned id typed | second explicit acquisition `20261005T020824136023Z-a253a850` (0.88 s, `duration_ms 26.6`, same content digest as the first since the store did not change); both ids verify; `history.list` pinned to the older and to the newer id each answer with their own id in `source`/`consistency`; listing shows both (retention 2 of 3); the unpublished id is `SNAPSHOT_UNAVAILABLE`. The GUI select interaction itself is pending (§3). | PASS |
| 8 Representative panels | events by kind/created_at; gates by type/success/output; evidence by serializer fields; attribution "not recorded" with Kriya's reason; unknown fields listed, never dropped | jsdom render of both real runs: timeline count equals the recorded event count and no "(unnamed event)"; every gate renders `<type> · success/failure` with no "result not recorded", "type not recorded", "result ambiguous" or unknown field; every evidence record renders `#n kind · source · attempt n` with no unknown field; Why tab reads "Recorded causal attribution: not recorded - the baseline persists failure categories, not causal attribution" and the literal failure category; trust strip shows the real store path, "digest verified at pin (<verified_at>); metadata checked per query", the sha256 prefix, no "unverified", no "fixtures"; runs list shows the literal status values. 4 harness tests passed. | PASS |
| 9 Offline tools on the real export | reports written; no actionable diagnostic beyond documented ones; every pointer resolves | `kup-run-report` on both details + page 1: 0 diagnostics; `kup-run-compare` (failure run vs success run): 0 diagnostics, no ambiguous gate key; `kup-evidence-check` over capabilities, acquire, verify, both listings, both pages, both details and the unavailable envelope: 0 actionable, exit 0, 2 informational `EVC-UNK-001` for the `retrieved_chunks` detail section (observation O-3); snapshot scope `listed` | PASS |
| 10 Workspace status | - | NOT EXECUTED (stopped as instructed; the owner names the workspace) | pending owner |
| 11 Record | measured values recorded | this file | done |

No stop condition occurred: no write to the main database, no digest failure, no `SNAPSHOT_*` error on acquire, no panel inferring anything not recorded.

## 2. Observations (findings, not failed criteria)

- **O-1 Real status vocabulary is lowercase and wider than the fixtures.** Across the 100 listed runs: `failure` 71, `success` 23, `approval_required` 3, `needs_review` 1, `planner_output_incomplete` 1, `planner_output_schema_invalid` 1. The fixtures used `SUCCESS`/`FAILED`/`NEEDS_REVIEW`, and `RunsColumn.statusClass` colours only those spellings, so every real run is rendered in the neutral "unknown" colour while the literal text is shown. The contract rule (literal status, never mapped to success) holds; the colour hint is lost. Fixture-fidelity item for a later batch (`FIXTURE_FIDELITY.md`); not a correctness defect.
- **O-2 First acquisition reports `source_metadata_changed: true`.** The acquisition's read connection creates `traces.db-wal` and `traces.db-shm`, so the post-backup stat differs from the pre-backup stat by exactly the sidecar sizes. The label is truthful ("source metadata change detected") but will appear on the very first acquisition of a quiet store. Documented behaviour of `metadata_differs`; worth a note in the strip wording for the owner to consider.
- **O-3 `retrieved_chunks` is a real detail section.** `history_detail` emits `JSON_TEXT_COLUMNS` including `retrieved_chunks`; the KUP `RunDetail` schema and the UI's `DETAIL_SECTIONS` do not list it, so it is preserved as an unknown section (checker: informational `EVC-UNK-001`) and not rendered by any panel. Also the real `fields` block carries all eight stored TEXT columns (`active_skills`, `evidence_records`, `failure_report`, `gate_outcomes`, `generation_metrics`, `model_hops`, `retrieved_chunks`, `run_events`), whereas fixtures carried only `files_modified`. Fixture-fidelity item.
- **O-4 Sidecar lifetime.** The sidecars persisted after the KUP runs and disappeared after a non-KUP, text-mode `kriya traces` invocation (run once here to look for the legacy-store notice), then reappeared after the second acquisition. The main database never changed. The legacy pre-move store `<checkout>/logs/traces.db` produced no notice in text mode.
- **O-5 Run `280bd867` records `status success` with `attempts 0`.** Shown literally; not interpreted.
- **O-6 Harness correction during the run.** The first attempt at steps 5-7 did not execute (a zsh variable used as a command prefix, then `time` applied to a shell function; exit 127, nothing reached Kriya); both were rerun with the command spelled out. One empty leftover file from a mis-picked run id was deleted from the export directory.

## 3. Manual checks at the Electron window (OWNER-PERFORMED 2026-10-05: PASS per the owner's record; separate from the automated evidence above)

The owner performed the walkthrough in `MANUAL_WALKTHROUGH_SESSION_2026-10-05.md` (snapshot pinning and switching, Refresh without
acquisition, the panels for runs `1df27f7a` and `280bd867`, keyboard navigation, VoiceOver spot checks) and confirmed it as a pass.
This session did not observe the window; the owner's result column is the record. The items originally listed below were the
scope of that walkthrough:

Launch `KRIYA_UI_ALLOW_REAL_KRIYA=1 npm start -w @kriya-ui/standalone`, enter the executable in Settings (the settings file does not
exist yet), leave the configuration directory blank, and confirm visually:
- trust strip shows Host `electron` without "(fixtures - matrix protection D-9)", the configuration directory `/Users/sriramnanduri`
  ("default: operator HOME ..."), the store path and the Kriya identity (step 1);
- "Acquire new snapshot" status sequence and the pinned headline (step 3); the "Snapshot integrity" item (step 4);
- the displayed-snapshot select switching between the two published ids, with the runs list reloading under the chosen id (step 7);
- "Refresh displayed snapshot" sends no acquisition (host log) and the "Snapshot" item wording for a quiet store (step 6);
- panel appearance for the two runs above (step 8 was rendered in jsdom only);
- step 10 after the owner names the recovery workspace; then record the values in §4.

The accessibility walkthrough (`A11Y_MANUAL_WALKTHROUGH.md`) remains pending as well.

## 4. Owner recording

- 2026-10-05: manual walkthrough completed and confirmed as a pass by the owner (owner-performed check; see §3 and the session sheet).
- Step 10 (workspace assessment): NOT PERFORMED. No recovery workspace has been authorized; the step is neither passed nor failed and does
  not block the completed history-inspector checks (steps 1-9 automated, manual walkthrough owner-confirmed).
- Step 11: this record.
