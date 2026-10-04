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
  started at 10:20 IST on fixtures (a snapshot copy, so nothing regenerated during the run can reach it) and its
  result is appended below when it completes.

## Owner checks still outstanding

1. Acceptance of the 393 MB idle memory (gate A-1 estimated 100-300 MB).
2. VoiceOver pass on the Electron app.
3. Visual confirmation that `code -g file:7` placed the cursor on line 7.
4. The D-4 ruling (WAL stores cannot be read read-only without sidecar writes) before Phase C can start.
