# R1 Topology Coverage (20 rows) → Risk Register Reconciliation

Source: `KRIYA_TOPOLOGY_COVERAGE.md` read completely this pass (all 20
entries, both summary tables, the cross-check section). No row
mechanically duplicated into a new risk — each maps to an existing risk,
or is recorded `NO DISTINCT RISK`, per the instruction. Zero new Risk IDs
were needed: every real gap this matrix documents was already represented
by an existing row (mostly `VER-001`/`VER-002`, or `TOP-001` for Gradle,
which is an exact pre-existing match for `TOP-REPO-002`).

| Topology ID | Existing Status | Validation Level | DE Relevance | Mapped Risk ID(s) | Evidence Applicability | Remaining Gap | Reason for NO DISTINCT RISK (where applicable) |
|---|---|---|---|---|---|---|---|
| TOP-MVN-001 | VALIDATED | INTEGRATION, PRODUCTION_PRV | REQUIRED (DE-01) | `VER-003` (NO DISTINCT RISK) | Java only | None | Foundational case `VER-003`'s own E4 evidence already covers; not a materially distinct risk from general Java build validation |
| TOP-MVN-002 | VALIDATED | INTEGRATION, PRODUCTION_PRV | REQUIRED (DE-01, DE-02) | `VER-001` | Java only | None at this scope | Direct match — `VER-001` *is* `INV-BUILD-001`, this row's own cited invariant |
| TOP-MVN-003 | VALIDATED | UNIT_ONLY, INTEGRATION | REQUIRED | `VER-001` (NO DISTINCT RISK) | Java only | None | Same mechanism/invariant as TOP-MVN-002, additional evidence not a new risk |
| TOP-MVN-004 | VALIDATED | INTEGRATION, PRODUCTION_PRV | REQUIRED (DE-02) | `CORR-015` | Java only | None | Direct match — `CORR-015` *is* `INV-PLAN-005` |
| TOP-MVN-005 | NOT_VALIDATED | — | REQUIRED (DE-02 multi-module Maven; profile-activated modules are a Maven-reactor variant, not excluded like monorepos are) | `VER-001` (NO DISTINCT RISK, gap noted) | None | `get_pom_reactor_modules()` never reads `<profiles>` — real gap | An unproven *sub-case* of the same build-validation risk `VER-001` already represents, not a materially different kind of risk |
| TOP-MVN-006 | NOT_VALIDATED | — | REQUIRED (DE-02) | `VER-001` (NO DISTINCT RISK, gap noted) | None | No recursion past one `<modules>` level | Same reasoning as TOP-MVN-005 |
| TOP-MVN-007 | NOT_VALIDATED | — | Ambiguous under DE-02 — the envelope's "multi-module" language is naturally read as one reactor with multiple modules, not multiple unrelated reactor roots sharing a workspace; **not confidently REQUIRED as written**, flagged rather than assumed | `VER-001` (NO DISTINCT RISK, gap noted) | None | One `workspace_path`, one `pom.xml` read | Same underlying compile-check mechanism; also borderline on DE-02 applicability, which argues against inventing a new ID for it |
| TOP-JAVA-001 | VALIDATED | INTEGRATION | REQUIRED | `CORR-001`, `CORR-008` | Java only | None | Direct match — both cited invariants already have rows |
| TOP-OWN-001 | VALIDATED | INTEGRATION, VERTICAL | REQUIRED | `CORR-001` | Java only | None | Direct match |
| TOP-OWN-002 | VALIDATED | INTEGRATION, VERTICAL, PRODUCTION_PRV | REQUIRED | `CORR-008`, `CORR-009` | Java only | None | Direct match |
| TOP-OWN-003 | VALIDATED | INTEGRATION, VERTICAL, PRODUCTION_PRV | REQUIRED | `CORR-002`, `CORR-003` | Java only | None | Direct match |
| TOP-OWN-004 | NOT_VALIDATED | — | REQUIRED (DE-02) | `VER-001` (NO DISTINCT RISK, gap noted) | None | Compiled-output check looks at `target/classes` only, not `target/test-classes` | Same compile-check mechanism as TOP-MVN-005/006 |
| TOP-RUNTIME-001 | VALIDATED (narrow) | INTEGRATION, PRODUCTION_PRV | REQUIRED | `VER-002` | Java only | Explicitly narrow per its own text — one Spring Boot–shaped, unauthenticated-HTTP service | Direct match |
| TOP-RUNTIME-002 | NOT_VALIDATED | — | Ambiguous under DE-06 — hostile-code containment doesn't itself require authenticated *readiness probing*; not confidently REQUIRED, flagged | `VER-002` (NO DISTINCT RISK, gap noted) | None | No credentialed request constructed anywhere in the test suite | Same runtime-verification mechanism/risk, unproven variant |
| TOP-RUNTIME-003 | NOT_VALIDATED | — | Same ambiguity as TOP-RUNTIME-002 | `VER-002` (NO DISTINCT RISK, gap noted) | None | "A real open question, not merely an untested instance" per the topology doc's own text — flagged as the most architecturally uncertain of the runtime gaps | Same mechanism; heightened uncertainty noted, not elevated to a new ID without a clearer DE mandate |
| TOP-RUNTIME-004 | NOT_VALIDATED | — | REQUIRED for the Gradle-adjacent case (a `gradle bootRun`-launched service is a real consequence of `TOP-001`'s own Gradle gap) | `VER-002`, `TOP-001` (NO DISTINCT RISK, cross-referenced) | None | PREPARE-phase evidence is jar/`mvn`-specific | Direct consequence of the Gradle gap already tracked at `TOP-001`; not a third ID |
| TOP-REPO-001 | VALIDATED (Java/Maven only) | PRODUCTION_PRV | REQUIRED | `CORR-017` | Java only | Explicit non-generalization already stated in the topology doc itself — full pipeline never driven against Python/Ruby/JS/Kotlin | Direct match — this row and `CORR-017` describe the same claim at different granularity |
| TOP-REPO-002 | NOT_VALIDATED | — | REQUIRED (DE-01) | **`TOP-001`** | None | Zero Gradle branch anywhere | **Exact pre-existing match** — `TOP-001` was already created for precisely this gap in the prior pass, before this topology doc was read row-by-row; confirms rather than duplicates it |
| TOP-REPO-003 | NOT_VALIDATED | — | Not confidently REQUIRED — DE-01 lists Java and Python as separate certified families, doesn't state a single repository must mix both; a real implementation gap, but not clearly DE-mandated | NO DISTINCT RISK — evidence/support-boundary only | None | One stack string computed per workspace, no per-module resolution | Genuine gap, deliberately not elevated to a Risk ID absent a clearer envelope statement that polyglot single-repo support is in scope |
| TOP-REPO-004 | NOT_VALIDATED | — | Unclear even in direction — the topology doc's own text says "recorded as genuinely unknown," not asserted as a gap | NO DISTINCT RISK — evidence/support-boundary only | None | Unknown | Too uncertain to warrant a dedicated row; revisit if a real incident ever surfaces |

## Coverage confirmation: 20/20

Every topology row mapped. Zero new Risk IDs created — `TOP-001`
(created in the prior pass, before this row-by-row read) turned out to be
an exact match for `TOP-REPO-002`, confirming rather than duplicating.
Two rows (`TOP-MVN-007`, `TOP-RUNTIME-002`/`003`, `TOP-REPO-003`) were
explicitly flagged as *ambiguous* under the approved envelope rather than
asserted REQUIRED or dismissed — a real production gap can exist without
being clearly demanded by the current Deployment Envelope's own wording,
and this reconciliation does not resolve that ambiguity on its own
authority.

## P1–P7 topology mapping (already present in the source document, re-used not re-derived)

The topology doc's own §"Totals" already states: P1→`TOP-MVN-001`/
`TOP-OWN-001`/`TOP-REPO-001`; P2→`TOP-MVN-001`/`TOP-OWN-002`/`TOP-OWN-003`/
`TOP-REPO-001`; P3→`TOP-MVN-001`/`TOP-OWN-002`/`TOP-REPO-001`;
**P4→`TOP-REPO-001`**; P5→`TOP-REPO-001`; P6→`TOP-RUNTIME-001`/
`TOP-REPO-001`; P7→`TOP-MVN-002`/`TOP-MVN-003`/`TOP-MVN-004`/`TOP-REPO-001`.
This directly resolves the P4 risk-mapping question — see
`KRIYA_P_SERIES_EVIDENCE_AUDIT.md` for the raw-evidence confirmation of
what P4 actually exercised.
