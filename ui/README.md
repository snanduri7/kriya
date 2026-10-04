# Kriya UI (M1 - standalone run inspector)

Design authority: `handover/GUI-DESIGN-DISCUSSION/05_GATE.md` (with amendments A-1 Electron, A-2 plugin-ready), then
`03_PROPOSAL_v2.md`. Implementation instructions: `06_IMPLEMENTATION_INSTRUCTIONS_M1.md`. Nothing under `ui/` is
imported by `kriya/` (P-34).

| package | role |
|---|---|
| `kup/` | KUP v1 JSON Schemas (`schema/`), GENERATED TypeScript types, dependency-free precompiled validators and Java classes (`generated/`, `npm run generate -w @kriya-ui/kup`; `--check` detects drift), the host contract schema, and the Java usability check (`npm run java:check -w @kriya-ui/kup`: javac + Jackson round trip over the golden fixtures) |
| `shared/` | host-independent React/TypeScript panels, selection reducers, normalization and availability rules. Every host capability goes through `HostAdapter` (P-R1). No Electron or Node import, enforced by ESLint (`no-restricted-imports`) AND `scripts/check-shared-deps.mjs` |
| `test-host/` | plain-browser host with a fake `HostAdapter` over the generated fixtures; renders every panel in CI (P-R3) |
| `standalone/` | Electron shell (gate A-1 hardening); the only process spawner; fixture `kriya` stand-in while D-9 holds |
| `fixtures/` | deterministic synthetic KUP fixtures (`node fixtures/generate.mjs` -> `fixtures/generated/`, git-ignored). `fixtures/serializer/run_events.json` (committed) is produced by Kriya's OWN serializer through the KUP adapter (`npm run fixtures:serializer`, `.newvenv`; `fixtures:serializer:check` detects drift) and is the shape every synthetic event follows |
| `spikes/a1_zero_write/` | Phase A1 zero-write SQLite measurement (Python, `.newvenv`) |

```
cd ui && npm install            # pinned versions, package-lock.json committed
npm run check                   # fixtures + typecheck + lint + shared dependency check + every test
npm run build -w @kriya-ui/standalone && npm start -w @kriya-ui/standalone     # the Electron app on fixtures
KRIYA_UI_MEASURE=1 npm start -w @kriya-ui/standalone                          # P-35 measurement (forced-GC diagnostic) -> standalone/measurements/
KRIYA_UI_SOAK=1 npm start -w @kriya-ui/standalone                             # 2 h soak, >=1,000 selections, no forced GC, 5-min samples -> soak-*.json
npm run dev -w @kriya-ui/test-host                                             # browser test host at http://127.0.0.1:5181 (?scenario=STORE_BUSY etc.)
```

Matrix protection (D-9): the Electron shell talks only to `standalone/fake-kriya/fake_kriya.mjs` unless
`KRIYA_UI_ALLOW_REAL_KRIYA=1` is set and a kriya executable is configured. Do not set it before the owner lifts D-9.

Child environment of the real `kriya` (owner policy 2026-10-04; `standalone/src/main/child_env.ts`, exact copy in
`tests/_kup_fixtures.py::host_child_env`): fixed `PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin`, fixed
`PYTHONDONTWRITEBYTECODE=1`, the operator's `HOME`, and `KRIYA_STATE_DIR` only when the operator set it to an absolute
path. Nothing else is passed (no `PYTHONPATH`/`PYTHONHOME`, no `KRIYA_TRUST_FILE`, no credentials, no config-path
variable); configuration discovery stays Kriya's own (working directory, then install directory), so the GUI's child
inherits the Electron process's working directory for `kriya.yaml` discovery.

Snapshot integrity (gate C-2 + 08 review F-4): **digest verified at pin; metadata checked per query.** The UI requests
`snapshot.verify <id>` for exactly the snapshot it is about to display (after an acquisition, or on a user's choice) and
never pins on a failure; later queries check size/mtime only. `?scenario=verify_corrupt` on the browser test host and
`KRIYA_FAKE_BEHAVIOR=verify_corrupt` on the stand-in exercise the refusal.
