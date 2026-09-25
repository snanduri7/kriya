# Kriya v1 Risk Prioritization

Analysis only. Starts from the 37 open REQUIRED risks in
`KRIYA_PRODUCTION_RISK_REGISTER.md` (60 REQUIRED − 23 REQUIRED+CLOSED).
KRP sequencing was used as *evidence* about implementation dependencies,
not inherited as product priority — several classifications below
deliberately diverge from the roadmap's own stated task order, with the
reasoning given.

## Classification (Class A / B / C)

| Risk | Disposition | Class | Reasoning |
|---|---|---|---|
| `SEC-005` | NI | **A** | Entry point of the security/broker chain — no open risk blocks it, though it implies building shared Action Broker scaffolding (`KRP-014`) that is pure infrastructure, not itself a tracked risk |
| `SEC-004` | NE | **A** | Re-examined: a bounded MCP request timeout does **not** require the broker chain — independently, narrowly fixable |
| `CONC-001` | NI | **A** | No prerequisite among the other 36 — genuinely independent, bounded real design work |
| `OBS-001` | NI | **A** | Already fully diagnosed this session (P8's own run); MISSING WIRING, not architecture; independent of every other open risk |
| `OBS-003` | NE→A-narrow | **A** | A basic redaction filter over *current* logging/trace call sites doesn't require the broker; full coverage across broker/MCP evidence (once those exist) is separate, later work |
| `OBS-004` | NI | **A** | Same reasoning — a narrow wall-clock/retry-count budget object doesn't require the full broker; broker-mediated budget enforcement is later work |
| `CTX-001` | NI | **A** | Independent of the security chain entirely; own domain |
| `MODEL-001` | NI | **A** | Independent; foundational for *trusting* any other evidence (nothing else confirms the model/runtime combination is stable) |
| `TOP-001` | NI | **A** | Independent, large, real DE-01 requirement |
| `POL-001` | NI | **C**, blocked by `SEC-005` | `KRP-017` depends on `KRP-015`/`KRP-016` (broker migrations) |
| `SEC-001` | NI | **C**, blocked by `POL-001` | `KRP-018` depends on `KRP-017` |
| `SEC-002` | NE | **C**, blocked by `SEC-001`+`POL-001` | Cannot test fail-closed behavior of mechanisms that don't exist yet |
| `SEC-003` | NI | **C**, blocked by `SEC-001` | `KRP-019` depends on `KRP-018` |
| `TOOL-002` | NI | **C**, blocked by `POL-001`+`SEC-003` | `KRP-020` depends on `KRP-017`+`KRP-019` |
| `TOOL-003` | NI | **C**, blocked by `TOOL-002` | Same ToolBroker work |
| `TOOL-001` | NI | **C**, blocked by `TOOL-002` **and** `ORCH-001` | `KRP-021` depends on `KRP-020` **and** `KRP-012` — the one place in the whole roadmap where the security chain genuinely needs orchestration consolidation |
| `TOOL-004` | NI | **C**, blocked by `TOOL-002`+`MODEL-001` | `KRP-031` depends on `KRP-020`+`KRP-023` |
| `STATE-002` | NE | **C**, blocked by `STATE-001`'s own outcome | Not blocked by an unimplemented mechanism — blocked by not yet knowing whether one is needed (see §Critical Path) |
| `STATE-003` | NI | **C**, blocked by `STATE-002` | `KRP-027` depends on `KRP-026` |
| `OBS-002` | NI | **C**, blocked by `CTX-001`+`OBS-004`+`OBS-003`+`STATE-003` | `KRP-033`'s own stated dependencies |
| `REL-002` | NI | **C**, blocked by `POL-001`+`SEC-001`+`CTX-001`+`MODEL-001`+`OBS-004` | A certification command can't certify guarantees that don't exist yet |
| `ORCH-003` | NE | **B** | Cheap — confirm the current gap against source directly, no live model needed |
| `CORR-006` | NE | **B** | Real vertical (E3) evidence exists; needs a live run targeting semantic-contract regression under repair specifically |
| `CORR-016` | NE | **B** | PRV-08's scenario is frozen and ready, never executed |
| `RECV-002` | NE | **B** | Mechanism (MA9) real; PRV-11 didn't exercise it — needs a scenario that reliably triggers recovery, not just a re-run |
| `REPO-004` | NE | **B** | PRV-14's scenario is frozen and ready, never executed |
| `VER-004` | NE | **B** | Mechanism now confirmed real (this session's own correction) — closeable via a deterministic unit test, no live model required |
| `VER-005` | NE | **B** | Real, broad capability confirmed (Python sweep) — needs one clean, fully-confirmed production pass |
| `POL-002` | NE | **B** | PRV-02 already produced plausible output — needs the scenario's own manual semantic check completed, or a fresh run |
| `MODEL-002` | NE | **B** | Mechanism unit-tested; PRV-10's scenario is frozen and ready, never executed |
| `MODEL-004` | NE | **B** | Heavily exercised already (R7–R14); needs one more clean, fully-ticked confirmation |
| `CTX-002` | NE | **B** | A deterministic large-repo/budget test is plausible, no live model required |
| `CTX-003` | NE | **B** | A targeted deterministic test against real build metadata is plausible |
| `STATE-001` | NE | **B** | A deterministic crash/kill-mid-run test is plausible — this does **not** need to wait for anything |
| `REL-001` | NE | **B** | Largely a source-state confirmation (this session already found real evidence the repo has packaging metadata) — cheapest item in the whole list |
| `TOP-002` | NE | **B** | P7's own repository is already real, non-Spring evidence — likely closes on review, not a new run |
| `TOP-005` | NE | **B** | The P-series harness itself is real non-interactive evidence — likely closes on review/documentation, not new implementation |

**Counts:** Class A = 9, Class B = 16, Class C = 12. 9+16+12 = 37 ✓.

## Dependency chains (derived from current architecture + full KRP specs, not assumed)

**Security execution chain (derived order differs from the suggested one):**
`SEC-005` (subprocess/package/network broker, `KRP-016`) →
[shared broker scaffolding, `KRP-014`, not itself a risk] → `POL-001`
(enforce policy, `KRP-017`) → `SEC-001` (sandbox, `KRP-018`) → `SEC-003`
(MCP hardening, `KRP-019`) → `TOOL-002`/`TOOL-003` (ToolBroker, `KRP-020`)
→ `TOOL-001` (enable TOOL subtasks, `KRP-021` — needs `ORCH-001` too) →
`TOOL-004` (plugin governance, needs `MODEL-001` too). `SEC-002` and
`REL-002` sit downstream of this whole chain as certification/fail-closed
checks over mechanisms the chain itself must build first.

**Repository/concurrency chain — shorter than it looks:** repository
identity → workspace lease/ownership → conflicting-writer rejection →
crash/stale-ownership handling are **all the same risk**, `CONC-001` — the
Deployment Envelope's own §6 already specifies all four properties as one
piece of work. `TOP-005` (CI/unattended operation) is nearly independent
— the P-series harness already provides real non-interactive evidence.

**Python chain — mostly NOT a blocked implementation chain.** Per
`KRIYA_PYTHON_CAPABILITY_SWEEP.md`: discovery, metadata/dependency
handling, validation, testing, recovery, and retry/repair are all
`PRESENT`. The two genuine gaps (structural-evidence/preservation being
Java-syntax-shaped, and multi-package topology being untested) are noted
inline in `VER-005`, not separately tracked risks. This chain is
overwhelmingly Class B (validation), not Class A (implementation).

**Java/Gradle chain — collapses to one task.** `TOP-001` covers detection
through compile/test in one connected piece of work, mirroring how
`PolymorphicValidator`'s Maven path already works as one code path.
Recovery and production qualification would flow through the *existing*
language-neutral `CORR`/`RECV` mechanisms automatically once `TOP-001`
lands — no separate risk needed for those stages.

**State/recovery chain:** `STATE-001` should be validated *first*,
independently — see Critical Path for whether it actually blocks
`STATE-002` or the reverse.

## TOP 5 CLASS A

1. `SEC-005` — unlocks the entire downstream security/tool chain (12 Class C risks sit behind it, directly or transitively); highest dependency-centrality of any open risk.
2. `CONC-001` — explicit DE-05 requirement, zero prerequisite, real unattended-safety consequence (silent concurrent corruption) if left open.
3. `MODEL-001` — every other piece of evidence in this register implicitly assumes the model/runtime combination is stable; nothing currently confirms that.
4. `OBS-001` — smallest, already fully diagnosed (not speculative), directly improves the quality of every future validation's own evidence.
5. `TOP-001` — largest scope but a direct, explicit DE-01 requirement with no substitute.

## TOP 5 CLASS B

1. `RECV-002` — MA9 coordinated repair has **never** been genuinely exercised by any real run (PRV-11 explicitly disclaims it); the single largest "we believe this works but have zero confirming evidence" gap in the register.
2. `CORR-016` — PRV-08's scenario is frozen, ready, and tests a mechanism (transitive multi-consumer revalidation) that's never been proven either way.
3. `VER-005` — the only Class B item that expands evidence into an entirely new language family, directly testing DE-01.
4. `REPO-004` — PRV-14's scenario is frozen and ready; directly relevant to the same workspace-isolation concern `CONC-001` addresses on the implementation side.
5. `STATE-001` — a deterministic crash/kill-mid-run test, no live model needed, and its outcome determines whether `STATE-002`/`STATE-003` need real implementation work at all.

## Best P9 candidate and alternatives — see `KRIYA_V1_CRITICAL_PATH.md` §4 for the full reasoning.
