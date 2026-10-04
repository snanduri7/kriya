# Kriya UI (M1 - standalone run inspector)

Design authority: `handover/GUI-DESIGN-DISCUSSION/05_GATE.md` (with amendments A-1 Electron, A-2 plugin-ready), then
`03_PROPOSAL_v2.md`. Implementation instructions: `06_IMPLEMENTATION_INSTRUCTIONS_M1.md`. Nothing under `ui/` is
imported by `kriya/` (P-34).

| package | role |
|---|---|
| `kup/` | KUP v1 JSON Schema, generated TypeScript types, host contract schema (Phase B; placeholder in Phase A) |
| `shared/` | host-independent React/TypeScript panels, selection reducers, normalization and availability rules. Every host capability goes through `HostAdapter` (P-R1). No Electron or Node import, enforced by ESLint (`no-restricted-imports`) AND `scripts/check-shared-deps.mjs` |
| `test-host/` | plain-browser host with a fake `HostAdapter` over the generated fixtures; renders every panel in CI (P-R3) |
| `standalone/` | Electron shell (gate A-1 hardening); the only process spawner; fixture `kriya` stand-in while D-9 holds |
| `fixtures/` | deterministic synthetic KUP fixtures (`node fixtures/generate.mjs` -> `fixtures/generated/`, git-ignored) |
| `spikes/a1_zero_write/` | Phase A1 zero-write SQLite measurement (Python, `.newvenv`) |

```
cd ui && npm install            # pinned versions, package-lock.json committed
npm run check                   # fixtures + typecheck + lint + shared dependency check + every test
npm run build -w @kriya-ui/standalone && npm start -w @kriya-ui/standalone     # the Electron app on fixtures
KRIYA_UI_MEASURE=1 npm start -w @kriya-ui/standalone                          # P-35 measurement -> standalone/measurements/
npm run dev -w @kriya-ui/test-host                                             # browser test host at http://127.0.0.1:5181 (?scenario=STORE_BUSY etc.)
```

Matrix protection (D-9): the Electron shell talks only to `standalone/fake-kriya/fake_kriya.mjs` unless
`KRIYA_UI_ALLOW_REAL_KRIYA=1` is set and a kriya executable is configured. Do not set it before the owner lifts D-9.
