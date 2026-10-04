# Phase B (KUP v1 contract), Phase D (shared panels) and the Phase E soak - report

Date: 2026-10-04. Authority: `05_GATE.md` (A-1, A-2), `06_IMPLEMENTATION_INSTRUCTIONS_M1.md` §B, §D, §E; owner
instructions of 2026-10-04 (Phase C HELD: no adapter, no immutable read, no `kriya/` change; read-consistency values
and database-state reasons PROVISIONAL; soak added).

Standing lines: **No GitHub push. No live model run. No real `kriya` command run. Matrix protection (D-9) respected.**
Phase C untouched (`git diff --stat 61a867f -- kriya/` is empty).

## Phase B - KUP v1 contract (`ui/kup/`)

- **Schemas (JSON Schema draft-07, `ui/kup/schema/`):** `common` (Envelope, Section + Availability, KupError + the six
  error codes, Source, Consistency), `capabilities`, `history` (RunSummary, HistoryList, RunEvent, ContextItem,
  TokenAccounting, ContextRecord, AttributionRecord, Comparison, RunDetail, Prompt), `workspace` (WorkspaceStatus),
  `host-contract` (KupRequest = the P-25 grammar with the host's validation rules, OpenInIdeRequest/Result, HostInfo,
  SettingKey/Value, the six HostAdapter messages - gate A-2 P-R2). Every Kriya-record object is open
  (`additionalProperties: true`); every host-contract object is closed. **Provisional, per the owner:**
  `consistency.kind` is an open string (documented expected value `sqlite_transaction_snapshot`) and
  `KupError.database_state` / `Section.reason` are free text until the D-4 topic decides the store-state vocabulary.
- **Generation (`npm run generate -w @kriya-ui/kup`, deterministic, `--check` = CI drift check):**
  TypeScript types (`json-schema-to-typescript` 16.0.0; unknown fields typed as index signatures), precompiled Ajv
  validators as ESM with the one runtime helper inlined (`validators.mjs`: NO dependency, NO eval - they run under the
  Electron CSP `script-src 'self'`), and Java types (`quicktype-core` 26.0.0, 29 classes). MEASURED: quicktype's Java
  drops unknown properties, so the generator injects a Jackson `@JsonAnyGetter/@JsonAnySetter` map into every class
  from an open schema object and `@JsonIgnoreProperties(ignoreUnknown = false)` into the strict host-contract classes.
- **Java usability check (gate A-2 P-R4; `npm run java:check -w @kriya-ui/kup`):** plain `javac --release 17` (no
  Maven/Gradle, D-9) against three pinned Jackson 2.17.2 jars fetched from Maven Central and verified against SHA-256
  digests in `java/JACKSON.lock`; then `RoundTrip.java` reads every golden fixture, round-trips the envelope and the
  operation payload through the generated classes and compares by value. MEASURED: 253 files, 0 failures, unknown
  fields preserved (asserted explicitly on `run-unknown-fields`: `novel_top_level_section` and `severity_v9`). One
  documented relaxation: an optional property absent in the input may come back as an explicit `null` (a Java POJO
  cannot represent absence); nothing recorded is lost. JDK on this Mac: Temurin 17.0.10 (also 21). No Java host built.
- **TypeScript round trip and refusals (`ui/kup/test`, 7 tests):** every fixture envelope + payload validates;
  unknown fields survive parse/stringify and do not invalidate an open record; schema_version 2, a missing field, a
  non-object error, an unknown operation, an unknown availability and a RunDetail missing a section are refused; the
  host-contract validator accepts the five operations and refuses the same injection corpus as the Electron host.
- **Parity (`ui/standalone/test/contract_parity.test.ts`):** the Electron main process keeps its dependency-free
  hand-written validators; the test proves they agree with the generated schema validators on a 44-item corpus.

## Phase D - shared panels (`ui/shared/`)

- The panels now consume the generated contract: `shared/src/model/kup.ts` re-exports `@kriya-ui/kup` (added to the
  shared dependency allowlist, host-independent by construction) and keeps only the generic refinements
  (`Section<T>`, typed `RunDetail`). `checkEnvelope` does the schema-version check first (UNSUPPORTED_SCHEMA_VERSION),
  then the generated structural validator (INVALID_RESPONSE with the first Ajv messages) - runtime validation in every
  host (P-24).
- Honest unavailable states preserved: data renders only for `recorded` + non-null data; `not_recorded`, `unreadable`,
  `excluded`, `unsupported` and any unknown value show the state literally with the recorded reason/provenance;
  Output/Diff/Why/Prompt show "not recorded" wherever P-30 says the data is not persisted; nothing is reconstructed.
- **Mutation campaign (rule 8), five mutants, each killed by the existing tests:** (M1) `isRecorded` ignoring null
  data; (M2) unknown availability mapped to "recorded"; (M3) stale responses applied; (M4) schema-version check
  removed; (M5) failed refresh keeping `current = true`. Results are in the section "Mutation results" below.
- Tests after the switch: shared 26, test-host 20, standalone 28 (+2 parity), kup 7; `npm run check` (fixtures,
  generated-output drift, typecheck, lint, shared dependency check, every test, Java check) exits 0.

### Mutation results (MEASURED, 2026-10-04; shared suite = 26 tests)

| mutant | what was changed | tests failing |
|---|---|---|
| M1 | `isRecorded` returns true for `recorded` with null data | 1 |
| M2 | an unknown availability value labelled "recorded" | 2 |
| M3 | `applyResponse` applies a stale generation | 1 |
| M4 | the schema_version check removed from `checkEnvelope` | 2 |
| M5 | a failed refresh keeps `current = true` | 2 |

Every mutant was reverted afterwards (`git diff` of `availability.ts`/`requests.ts` empty). The parity test also caught
a real gap before commit: the host-contract schema allowed a control character in `workspace` that the Electron
validator refused; both now refuse every C0 control character in `workspace` and `path`.

## Phase E - memory checks

- The forced-GC diagnostic (`KRIYA_UI_MEASURE=1`) is retained unchanged.
- **Soak (`KRIYA_UI_SOAK=1`, `src/main/soak.ts`):** at least 1,000 selections over two hours, no forced GC, every
  Electron process sampled every five minutes (plus `ps` RSS), progress flushed at every sample, then the ceiling
  check (max total working set after a 10-minute warm-up <= 550 MB) and the sustained-growth check (least-squares
  slope in MB/hour over the post-warm-up samples). A 20-second smoke validated the mechanics. The two-hour run was
  started at 10:20 IST on fixtures (a snapshot copy, so nothing regenerated during the run can reach it).

### Soak result (MEASURED; `standalone/measurements/soak-2026-10-04T04-50-09-515Z.json`; owner-agreed 15-minute warm-up)

Electron 44.5.1, macOS 26.7 arm64, fixtures via the stand-in, window visible, no forced GC. 2 h 0 m 6 s wall time,
**1,177 selections** (one every 6.12 s, cycling the four heavy fixtures and all 50 page-1 runs), **0 errors**,
25 samples, `ps` RSS within 3 MB of `app.getAppMetrics()` at every sample. The run itself was taken with a 10-minute
warm-up option; the figures below are recomputed from the raw samples with the agreed 15-minute warm-up (no rerun
needed - the samples are the evidence; the regression test recomputes them the same way).

| measure | value |
|---|---|
| start (idle, before any selection) | 394 MB total working set |
| first post-warm-up sample (15 min 19 s) | 385 MB |
| post-warm-up samples (22) | min 354, **mean 371.86**, **max 401 MB** |
| **550 MB ceiling after warm-up** | **PASS** (401 MB) |
| sustained growth (least squares over the 22 post-warm-up samples) | **-7.14 MB per hour** (385 -> 364 MB), i.e. no growth |
| per process at the end | Browser 129, GPU 62, Utility 38, renderer 135 MB |

The two transient peaks (401 MB at 51 min, 392 MB at 117 min) are Browser-process excursions of ~30 MB that fall back
within one sample; the renderer stayed between 127 and 149 MB throughout. Classification (rule 21):
`EXPECTED_BOUNDED_CACHE`; no `KRIYA_PRODUCT_LEAK` signal in two hours. Caveats: fixtures only (real `kriya` output
volumes and a loaded local model are still to be measured after D-9 is lifted), and the development build ran other
work (npm tests, the Java check) on the same machine during the first 30 minutes.

## Owner-confirmed acceptances (2026-10-04) - distinct from the automated measurements above

These are decisions and human checks recorded as the owner stated them; they are not measurements by the agent.

| item | status | basis |
|---|---|---|
| idle footprint 393 MB | **ACCEPTED by the owner for M1** | measurement `measure-2026-10-04T04-29-17-837Z.json`; the 550 MB post-warm-up ceiling is RETAINED as the regression gate |
| VoiceOver pass on the Electron app | **ACCEPTED - owner-confirmed** (human check at the machine) | agent evidence was only the Chromium accessibility tree proxy (69 controls, 0 unnamed) |
| VS Code cursor placement (`code -g file:line`) | **ACCEPTED - owner-confirmed** (human check at the machine) | agent evidence was only `exit 0` on VS Code CLI 1.140.0 |
| soak warm-up | agreed at **15 minutes** | report and `memory_ceiling.test.ts` recompute from the raw samples with 900 s |

The ceiling is bound in code by `standalone/test/memory_ceiling.test.ts`: the soak defaults must keep 550 MB / 2 h /
1,000 selections / 5-minute samples / 15-minute warm-up / no forced GC, and the latest committed soak evidence,
recomputed from its raw samples, must be complete, under the ceiling and without sustained growth (slope < 10 MB/h;
a 60 MB/h ramp is the negative control). Any future soak that breaches the ceiling fails `npm run check`.
- **Chromium accessibility tree (MEASURED proxy, `measure-2026-10-04T07-03-56-510Z.json`):** read through the
  DevTools protocol (`Accessibility.getFullAXTree`), which is the tree macOS VoiceOver receives: 1,124 live nodes,
  69 interactive controls, **0 without an accessible name**; roles exposed: 4 regions, 3 listboxes, 47 options,
  2 tablists, 7 tabs, 11 buttons, a searchbox, a table with 5 column headers, 3 headings; the selected option's
  name reads "SUCCESS 2026-09-28 10:00:00 FIXTURE: 2,000-line recorded diff". This shows names and roles exist; it does
  not show how VoiceOver speaks them or whether the reading order is sensible.
- **D-4 ruling:** unchanged by the owner (no immutable reads, no SHM exceptions); Phase C stays on hold. A stable-
  snapshot read strategy for the owner's gate decision is in `ui/docs/D4_STABLE_SNAPSHOT_READ_STRATEGY.md`
  (specification only, not implemented).
