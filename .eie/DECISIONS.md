# Decisions - BACKEND-READINESS-V2 (2026-10-11)

- D1. The 4/12 baseline is dominated by run-to-run variance on two tasks plus six never-solved tasks; two of the four GENUINE are zero-model-call false-premise no-ops. The readiness gate is not reinterpreted.
- D2. No new canonical cross-role state object or failure ledger: no recorded case lost a constraint across roles; the existing ObligationLedger and MUST PRESERVE repair already carry them.
- D3. Overloads are folded at the Planner boundary only; the Developer retrieval path keeps per-overload loci (registry: LOCALIZATION-DEVELOPER-PATH-OVERLOAD-001).
- D4. The only new plan rejection is the delegating-shim edit with an unplanned delegate that contains a goal name; everything else is evidence, not a gate.
- D5. The S3_A ERROR->FAIL baseline flip gate is NOT changed: the PRE/POST cause is INFERRED, a gate edit needs its own reproducer, and the registry deferral requires measured volatility plus a safe rule. The specified parity check remains open.
- D6. Live A/B covers T4, S1_A, T3, T2 (one run each); the baselines are the existing c3 artifacts.
