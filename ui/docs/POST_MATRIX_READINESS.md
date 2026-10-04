# Post-matrix readiness (2026-10-04, branch `codex/fix-demo1-attribution`, HEAD after batch 8 `b150cc6`)

The owner stated that the CAGC 80-run matrix is complete. This document records what that statement releases, what it
does not, what was verified read-only, what validation now ran, and the exact owner actions that remain. Nothing was
merged, acquired, started or pushed.

## 1. Restrictions: what expired, what is retained

| rule | source | status after the owner's statement |
|---|---|---|
| D-9 matrix protection: fixtures/fakes only; no real `kriya` CLI; no paths under `~/kriya-cagc-v2/` or `~/.kriya`; no live model runs - "until the owner states that the CAGC 80-run analysis is complete" | `handover/GUI-DESIGN-DISCUSSION/05_GATE.md` D-9; `04_REVIEW_2.md` C-7 | trigger condition met by the owner's statement. The four prohibitions no longer bind as matrix protection. Read-only inspection of the completion evidence under `~/kriya-cagc-v2/` was performed for §2 and nothing else there was touched. |
| "heavy parallel builds / full UI check / Java round trip deferred" (hand-off working rule while the matrix ran) | `08_REVIEW_PHASE_C_HANDOFF.md` §N-2, the batch instructions | expired: the full UI check ran (§3). |
| C-6: "D-9 matrix protection remains in force until the owner explicitly lifts it ... the first real acquisition/integration measurement happens only after explicit release and uses the approved procedure"; R-4: real-store copy "only the approved SQLite-aware procedure, after D-9 release and under C-6" | `handover/GUI-D4-READ-STRATEGY/03_GATE.md` C-6, R-4 | **retained.** Matrix completion is the precondition; the *release of real-store use* is a separate explicit owner statement. Until it is given, no real `kriya` call against a real store, nothing under `~/.kriya`, no model. §7 is preparation only. |
| Main-repository merge "coordinated with the main-repository session"; re-run KUP tests, goldens, sandbox test and full suite there | `08_REVIEW_PHASE_C_HANDOFF.md` N-2 items 3-4, Addendum 2 | **retained**: owner action. |
| Full Kriya suite at `-n 2` before the merge | `08_REVIEW_PHASE_C_HANDOFF.md` N-2 item 1; repository quota rule (pytest stays with the owner at a batch boundary) | **retained for the owner's terminal**: command in §4. |
| D-10: never push to GitHub; Kriya changes in separate `KUP:` commits | `05_GATE.md` D-10 | **retained, permanent.** |
| Owner's pre-existing untracked files are never moved or deleted | standing instruction | retained. `kriya/workflow/orig-attempt.py` is no longer present in the checkout (the owner moved it); 19 untracked owner files remain and were not touched. |

## 2. Completion evidence (verified read-only; nothing altered)

| item | observed (MEASURED) |
|---|---|
| `~/kriya-cagc-v2/matrix_v2.log` | 287 lines; 80 numbered run lines (run numbers 1..80, each once); final line `MATRIX_COMPLETE`; last run line at 22:17:44 (run 80, `python-error-invalidurl.r8`, arm A) |
| `~/kriya-cagc-v2/RESULTS/analysis/` | `report.md` (19,931 B), `runs.json` (328,313 B; `runs`: 80 entries, `incomplete`: 0), `summary.json` (arms A: 40 runs, B: 40 runs; keys arms, guidance, judges, per_task, runtime_digests); written 22:40 |
| `~/kriya-cagc-v2/REVIEW/` | `05a_80_RUN_EVIDENCE_SHA256.txt` (2,282 lines, frozen 2026-10-04T17:09:15Z), `05b_CAGC_V2_80_RUN_MANIFEST.csv` (80 rows + header), failure census, A_VS_B_DIFF, FINAL_ANALYSIS, PRE_RUN_MANIFEST, PROTOCOL_v2.md, ARM_A_COMMIT `61a867f` (this branch's baseline; present in this checkout), ARM_B_COMMIT `74d0fe0` (not in this checkout), PROTOCOL_COMMIT `b753356` (= `AUTHORIZED_TO_RUN`; not in this checkout) |
| designated marker | No hand-off document designates a file as the completion evidence; D-9 designates the owner's statement. The statement is corroborated by the `MATRIX_COMPLETE` marker, the 80/80 run count with zero incomplete entries and the frozen SHA-256 evidence list. |

Missing or incomplete: none observed. Not assessed: the content of the analysis (outcomes, judgements); that is the matrix's own
deliverable and outside this validation.

## 3. Validation that was deferred and has now run (sequential, one invocation, exit 0)

`cd ui && npm run check` = fixtures -> `check:generated` -> typecheck -> lint -> shared-deps -> every workspace's tests -> Java round trip.

| step | result |
|---|---|
| fixtures | 122 runs, 3 pages, big-events detail 6.93 MiB regenerated |
| generated-output drift (`check:generated`) | "generated output is current (42 files)" - no drift |
| typecheck, lint | clean (part of exit 0) |
| shared dependency check | OK (88 forbidden patterns, allowlist @kriya-ui/kup, react, react-dom) |
| tests | kup 66, shared 62, test-host 36, standalone 45 (209 total) |
| Java round trip | javac 17.0.10, 40 sources compiled with `--release 17`; `round-trip files=270 unknown_fields_preserved=101 failures=0` (pinned Jackson jars already on disk; nothing downloaded) |
| serializer fixtures | `fixtures:serializer:check` current for events, gates, evidence (run earlier in batch 7, isolated root, no bytecode) |

## 4. Full Kriya suite (owner's terminal; not run here)

The gate assigns this run to the pre-merge step and the repository rule keeps the final pytest run with the owner. The
write-site audit blocker named in `08_REVIEW` N-2 item 2 (`kriya/workflow/orig-attempt.py`) is no longer in the checkout.

```
cd /Users/sriramnanduri/WorkingDirectory/AI/AntiGravity/Kriya-By-Antigraviry/tmp/kriya-demo1-attribution-fix-2
ulimit -n 256; .newvenv/bin/pytest -q -n 2 --dist loadgroup
```

Targeted KUP subset (seconds, may be run first):

```
.newvenv/bin/pytest -q tests/test_kup_acquisition.py tests/test_kup_cli.py tests/test_kup_write_boundary.py tests/test_kup_host_environment.py tests/test_traces_command.py
```

Expected: the suite is green; the KUP tests were last green in this checkout at batch 2 of 2026-10-04 (29 + 16 + 2 + 6 plus the
traces command tests). A failure in the write-site audit would name a new untracked file under `kriya/`; do not move owner files to
satisfy it - report it.

## 5. Hand-off inventory: the `KUP:` merge candidates (ordered; each verified to touch no `ui/` path)

| # | commit | files | prerequisites |
|---|---|---|---|
| 1 | `db96b12` KUP: snapshot read adapter | `kriya/kup/{__init__,acquire,cli_ops,inspect,policy,store}.py`, `tests/_kup_fixtures.py`, `tests/test_kup_acquisition.py`, `tests/test_file_integrity_contract_001.py` (+1 row), `handover/FILE_INTEGRITY_CONTRACT_001.md` | baseline `61a867f` (= matrix arm A) |
| 2 | `f4bd13f` KUP: CLI grammar (`traces --json`, `runs status --json --kup-version 1`) | `kriya/cli.py`, `tests/test_kup_cli.py`, `tests/test_kup_write_boundary.py`, `tests/golden/kup/*.golden`, `handover/evidence/KUP/write_boundary_evidence_2026-10-04.json` | 1 |
| 3 | `f4688ea` KUP: `snapshot.verify` | `kriya/cli.py`, `kriya/kup/cli_ops.py`, `kriya/kup/policy.py`, `tests/test_kup_cli.py` | 2 |
| 4 | `8065005` KUP: event contract pinned to `RunEvent.to_dict` on the adapter path | `tests/_kup_fixtures.py`, `tests/test_kup_cli.py` | 3 |
| 5 | `f876f49` KUP: write boundary under the production launch environment, bytecode negative control, GUI/CLI parity | `tests/_kup_fixtures.py`, `tests/test_kup_host_environment.py`, `tests/test_kup_write_boundary.py`, `handover/evidence/KUP/*2026-10-04*.json` | 4 |
| 6 | `b531404` KUP: explicit child cwd makes resolution independent of the host's launch directory | `tests/test_kup_host_environment.py` | 5 |

`git show --stat` of each commit lists only `kriya/`, `tests/`, `handover/` paths: no UI change rides in any of them. The GUI-C
commits (`504c3ac` ... `b150cc6`) stay in this checkout. The child-environment policy is duplicated on purpose in
`ui/standalone/src/main/child_env.ts` and `tests/_kup_fixtures.py::host_child_env`; a change to one must change the other.

Remaining limitations (unchanged by matrix completion): every UI, report, compare and checker result so far is fixture evidence;
`AttributionRecord.evidence_ids` has no namespace (`FIXTURE_FIDELITY.md`); the a11y manual checklist is pending a human
(`A11Y_MANUAL_CHECKLIST.md`); Linux parity of the sandbox test was not run here; the stand-in `kriya` is still what the Electron
shell talks to until §7 is authorized.

## 6. Exact remaining owner actions

1. State explicitly whether real-store use is released (03_GATE C-6 wording), or keep it held. Nothing in §7 runs before that.
2. Run the full Kriya suite (§4) in your terminal and report pass/fail.
3. Coordinate the main-repository merge of the six `KUP:` commits with the main-repository session, in the order above; then re-run
   there: KUP tests, byte-identical goldens, the sandbox test, the full suite.
4. Perform the manual accessibility checks (`A11Y_MANUAL_CHECKLIST.md`) or delegate them; they are pending.
5. Never push.

## 7. First real-store acceptance procedure (PREPARATION ONLY; runs only after action 1 releases real-store use)

Preconditions, all checked before step 1: the owner's explicit release statement is on record; the full suite is green (§4); a
backup of the real `traces.db` is not required by the procedure (acquisition reads the source through SQLite's backup API inside one
read transaction and writes only owned snapshot staging/storage; C-5), but the owner may take one; the host's child environment is
the fixed policy (`child_env.ts`), the configuration directory setting names the directory whose `kriya.yaml` resolves the intended
state directory, and the recovery workspace setting names the real workspace to assess.

| step | action | acceptance (MEASURED on the day; record each value) |
|---|---|---|
| 1 Identity | In the Electron shell with the real `kriya` executable set, open Settings, confirm the trust strip shows the configuration directory as "set explicitly" and the resolved history-store path (`<state>/traces.db`). | The store path is the intended real store. `Kriya` shows the installed version and commit (capabilities), "unverified" never. |
| 2 Source write boundary | Before acquisition record `stat` (size, mtime_ns, inode) of `traces.db`, `traces.db-wal`, `traces.db-shm`. After every step below re-check. | No change to the main database file's inode or size caused by the GUI; the only writes are under `<state>/kup-snapshots/` (staging then published `<id>/traces.snapshot.db` 0400 + `manifest.json` 0400). The sandbox write-boundary test (`tests/test_kup_write_boundary.py`) is the standing evidence; this is the live observation. |
| 3 Explicit acquisition | Press "Acquire new snapshot" once. | Status shows "acquiring…" then the pinned headline; a new snapshot id appears in "Displayed snapshot"; `snapshot.acquire` ran exactly once (host log); duration and size recorded (`duration_ms`, `size`, `rows`); retention keeps at most the newest three published snapshots. |
| 4 Snapshot integrity | Observe the trust strip "Snapshot integrity" item after the pin. | "digest verified at pin (<verified_at>); metadata checked per query" with the sha256 prefix; the id equals the acquired id. Then tamper test on a COPY only if the owner wants it: no test modifies the published snapshot. |
| 5 Pinned browsing | Browse runs; open a run; switch inspector tabs; load the prompt explicitly. | Every history request carries the pinned snapshot id (host log); `history.prompt` is sent only on "Load prompt"; no `snapshot.acquire` is sent by browsing or by Refresh. |
| 6 Freshness labels | Press "Refresh displayed snapshot". | Headline and metadata text come from `consistency` (`snapshot_copy`, acquisition times); "no metadata change detected" is labelled "not a freshness guarantee"; if the live store changed, "source metadata change detected" appears and no acquisition happens. |
| 7 Snapshot switch | Choose the older snapshot from the select, then the newer one. | Each switch verifies the digest first; previous responses, cursors and the selection are invalidated (runs list reloads under the new id); a pruned id yields the typed `SNAPSHOT_UNAVAILABLE`, never a substitute. |
| 8 Representative panels | For one failed run and one successful run: Timeline (events named, attempt chips, filter), Context, Gates, Evidence, Why. | Events render by `kind`/`created_at`; gates by `type`/`success`/`output`; evidence records by the serializer's fields; attribution reads "not recorded - the baseline persists failure categories, not causal attribution"; any unknown field is listed, never dropped. |
| 9 Offline tools on the real export | Save the `history.detail` JSON of the two runs (via the fixture-free `kriya traces --json --snapshot-id <id> --run-id <run>` in the owner's terminal) and run `npm run report`, `npm run compare`, `npm run evidence-check` on them. | Reports written; the evidence checker reports no actionable diagnostic other than documented ones; every pointer resolves. This is the first real-record exercise of the tools; record every diagnostic verbatim. |
| 10 Workspace status | With the recovery workspace set to the real workspace, observe "Workspace (recovery assessment)". | `run_active`/status/exit code agree with `kriya runs status --workspace <path>` run in the owner's terminal. |
| 11 Record | Append the measured values (acquisition duration and size, stat before/after, snapshot ids, any diagnostic) to this file as §8. | Owner acceptance recorded, or the first defect becomes a Fix-Now item. |

Stop conditions: any write to the main database attributable to the GUI; a digest verification failure at pin; a `SNAPSHOT_*` error
on acquire; a panel inferring anything not recorded. Each stops the procedure and is reported with the raw host log.
