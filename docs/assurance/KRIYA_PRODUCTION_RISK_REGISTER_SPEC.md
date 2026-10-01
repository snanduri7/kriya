# Kriya Production Risk Register — Specification v2

**Status: APPROVED (schema/taxonomy design only — population not yet authorized).**
Supersedes the informal schema sketch embedded at the top of
`KRIYA_PRODUCTION_RISK_REGISTER.md` (that file's existing KRP-022 seed
entry predates this spec and will need reconciliation into this shape
once population is authorized — noted, not done here).

This document is the authoritative schema for the Production Risk
Register. It governs `docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md`
(risk rows) and a companion evidence appendix (per-evidence-item detail,
§7). Every field below traces to a decision recorded in this document or
in `KRIYA_V1_DEPLOYMENT_ENVELOPE.md` — nothing here is inferred.

## 1. Risk ID scheme

Opaque, immutable, domain-prefixed: `<DOMAIN>-<3-digit sequence>` (e.g.
`VER-003`). **Identity carries no semantics beyond domain and sequence.**
Language, build system, framework, topology, or deployment mode are never
encoded into the ID — they are fields on the record (§3). A logical risk
that materially differs in implementation, applicability, evidence level,
remaining gap, or disposition by language (or any other dimension) becomes
a **separate immutable ID**, e.g. `VER-003` (Java validation correctness)
and `VER-004` (Python validation correctness) — never `VER-003-JAVA`/
`VER-003-PY`. IDs, once assigned, are never reused or renumbered, even if
the underlying PRV/KRP task is renamed or superseded.

**Risk Family** (optional field): a free-text grouping label linking
related immutable IDs, e.g. `Risk Family: VER-LANGUAGE-VALIDATION` on both
`VER-003` and `VER-004`. Purely organizational — carries no authority and
is never used as an identifier.

## 2. Domain taxonomy (provisionally approved, unchanged this round)

`ORCH` orchestration · `CORR` correctness · `RECV` recovery · `REPO`
repository mutation/transactions · `VER` verification · `SEC` security ·
`POL` policy · `TOOL` tools/MCP · `MODEL` local-model/runtime governance ·
`CTX` context/retrieval · `STATE` persistence/replay · `CONC` concurrency
· `OBS` observability · `REL` release/productization · `TOP` topology/
support boundary.

Not to be changed casually during population. If population reveals a
domain is empty, overloaded, or semantically wrong, that's reported as a
proposed taxonomy change on its own, separately, before any mass
renumbering — never silently adjusted mid-population.

## 3. Risk row schema

| Field | Notes |
|---|---|
| Risk ID | §1 |
| Risk Family | §1, optional |
| Risk Domain | §2 |
| Language Scope | `JAVA` \| `PYTHON` \| `LANGUAGE_NEUTRAL` |
| Risk Statement | Plain statement of the concern |
| Failure Consequence | What actually goes wrong if unaddressed |
| Requirement Authority | §4 |
| Requirement Authority Conflict | boolean + notes, §4 |
| Deployment Relevance | `REQUIRED` \| `OPTIONAL` \| `OUT_OF_SCOPE`, traced to a specific Deployment Envelope §, never asserted free-floating |
| Related PRV(s) | or `None identified` |
| Related P-series validation(s) | |
| Related KRP(s) | |
| Related MA8/MA9 mechanism | |
| Related R1 invariant(s) | |
| Related topology dimension(s) | |
| Related fault-injection scenario(s) | |
| Current Implementation Mechanism | file/module references |
| Effective Evidence Level | E0–E5, **derived**, never hand-entered — §6 |
| Required Evidence Level | E0–E5 the Deployment Envelope actually demands for this risk |
| Remaining Implementation Gap | |
| Remaining Evidence Gap | |
| Disposition | §8 |
| Candidate Next Action | |
| Candidate Production-Validation Scenario | |
| Explicit Operating Limitation (if DEFERRED) | |
| Superseding Mechanism (if SUPERSEDED) | must name the replacement + the evidence proving it satisfies the original risk |
| Evidence Items | pointer(s) into the evidence appendix, §7 — not embedded detail |
| Source References | |
| Rationale/Notes | |

## 4. Requirement Authority

Determines *what is required*, independent of how well it's proven.
Authority sources: `DEPLOYMENT_ENVELOPE` · `ARCHITECTURE_INVARIANT` ·
`PRV_CONTRACT` · `KRP_REQUIREMENT` · `EXPLICIT_PRODUCT_DECISION` — each
with a citation (document + section/ID).

**Later evidence never changes requirement authority.** A production run
passing does not relax what was required; it only feeds evidence strength
(§5). If two requirement authorities genuinely conflict (e.g. an old PRV
contract implies something the approved Deployment Envelope doesn't
require), the row is flagged `Requirement Authority Conflict: true` with
notes describing the conflict — resolved by explicit human decision, never
by which one is newer.

## 5. Evidence hierarchy (E0–E5, unchanged)

`E0 NONE` · `E1 UNIT` (isolated component/helper) · `E2 INTEGRATION`
(multiple real components together, not the full authoritative path) ·
`E3 VERTICAL` (the actual authoritative production path exercised
deterministically) · `E4 PRODUCTION_RUN` (real repository + real local
model + actual Kriya workflow + externally verified acceptance evidence)
· `E5 REPEATED_PRODUCTION` (same capability/risk with production-run
evidence across **materially distinct** scenarios — never the same
scenario re-run, never "several P-runs happened to share a stack"; the
row's Rationale/Notes must name the actual diversity: different
repository, topology, implementation pattern, task type, framework, build
system, or model/runtime, whichever dimensions matter for that specific
risk).

This is an **evidence-item** property (§7), not directly assigned to a
risk row — the row's level is derived (§6).

## 6. Evidence Applicability, Validity, and the Effective Evidence Level

**Applicability.** Before any evidence item counts toward a risk's
effective level, confirm it actually covers the risk's own dimensions:
language, build system, framework, repository topology, workflow mode
(legacy/enforce), model/runtime, deployment environment, the specific
capability/path exercised, relevant security/policy configuration. Never
transferred across dimensions without stated justification — Java/Maven/
Spring E4 evidence does not establish Python E4; a Maven multi-module
production run does not establish Gradle multi-module evidence; a
supervised production run does not by itself establish unattended
hostile-code containment.

**Validity.** Each evidence item carries one of: `ACTIVE` · `OUTDATED`
(superseded by newer evidence for the same scope — deliberately *not*
named `SUPERSEDED`, to avoid colliding with the row-level Disposition
value of the same name; see §9) · `INVALIDATED` (a later applicable
failure/regression contradicts it for the affected configuration) ·
`STALE_REQUIRES_REVALIDATION` (evidence exists but its currency is in
doubt — e.g. the code path changed materially since the evidence was
produced). Contradictory evidence is never silently discarded — it stays
visible in the evidence appendix regardless of validity state.

**Effective Evidence Level (derived, row-level):**

> The highest-strength `ACTIVE` evidence item that is applicable to the
> exact risk scope and has not been invalidated by contradictory
> applicable evidence.

A later applicable failure can invalidate an earlier applicable success
for the affected configuration — the row's effective level drops
accordingly, and both the success and the invalidating failure remain
recorded in the evidence appendix, not erased.

## 7. Evidence appendix (traceability without duplication)

Per your own instruction: the main register stays a summary; full detail
lives in a linked evidence appendix (either a dedicated
`KRIYA_PRODUCTION_RISK_REGISTER_EVIDENCE.md`, or a per-domain section
within it — chosen at population time based on actual volume). Each
evidence item: `Evidence ID` · `Risk ID(s) it supports` · `Source`
(PRV/P-run/test file/design doc) · `Date/version/commit` where available
· `Evidence Type` (unit test / vertical test / PRV run / P-series run /
design citation) · `E-level` · `Applicability tags` (short, e.g. `java,
maven, spring, single-module, enforce-mode`) · `Result` (PASS/FAIL/BLOCKED
where applicable) · `Validity` (§6) · `Notes`.

## 8. Disposition vocabulary (approved, unchanged)

`CLOSED` — required mechanism and required evidence level both met.
`NEEDS_EVIDENCE` — mechanism appears adequate, proof insufficient (this is
the pool P9/P10/... scenarios should normally be drawn from).
`NEEDS_IMPLEMENTATION` — capability/safeguard absent or materially
insufficient; never schedule a live-model run to "demonstrate" this.
`SUPERSEDED` — an older requirement replaced by a later mechanism; must
name the superseding mechanism and the evidence proving it satisfies the
original risk (§3). Never means "no longer wanted."
`DEFERRED` — a valid concern intentionally outside the current Deployment
Envelope; must state the explicit operating limitation. No "partially
satisfied" value exists: incomplete mechanism → `NEEDS_IMPLEMENTATION`;
complete mechanism, insufficient proof → `NEEDS_EVIDENCE`.

## 9. Consistency review (performed this pass)

Checked all controlled vocabularies against each other for value
collisions:

- Deployment Relevance (`REQUIRED`/`OPTIONAL`/`OUT_OF_SCOPE`) — no overlap with any other vocabulary.
- Disposition (`CLOSED`/`NEEDS_EVIDENCE`/`NEEDS_IMPLEMENTATION`/`SUPERSEDED`/`DEFERRED`) — **`SUPERSEDED` collided with the originally-proposed Evidence Validity vocabulary** (both would have used the same word at different scopes — row-level requirement replacement vs. evidence-item currency). **Resolved**: Evidence Validity uses `OUTDATED` instead of `SUPERSEDED` for that state (§6). `SUPERSEDED` is now reserved exclusively for row-level Disposition. This is a decision I made directly rather than leaving open, since it was a low-stakes naming clash with an unambiguous fix — flagged here so it can be overridden if you'd rather keep the original word and rely on field-name context instead.
- Evidence Strength (`E0`–`E5`) — no overlap.
- Evidence Validity (`ACTIVE`/`OUTDATED`/`INVALIDATED`/`STALE_REQUIRES_REVALIDATION`) — no further overlap after the fix above.
- Requirement Authority (`DEPLOYMENT_ENVELOPE`/`ARCHITECTURE_INVARIANT`/`PRV_CONTRACT`/`KRP_REQUIREMENT`/`EXPLICIT_PRODUCT_DECISION`) — no overlap.
- Language Scope (`JAVA`/`PYTHON`/`LANGUAGE_NEUTRAL`) — no overlap.

No remaining collisions found across six vocabularies.

## 10. PRODUCTION_PRV → E4 (retained, conservative)

`PRODUCTION_PRV` in the legacy Topology Coverage vocabulary does **not**
automatically map to E4. Per-scenario, confirm: real repository, real
local model, real production Kriya workflow, the *specific* capability/
risk actually exercised, and sufficient deterministic or independent
acceptance evidence for the claimed result. Only then assign E4 to the
applicable risk row. Seven PRV scenarios (07–10, 14–16) have specs but no
recorded results — absence of a result is not evidence of anything, and
must never be silently read as either implementation or non-implementation.

## 11. Legacy-to-canonical mapping (carried forward from the prior round, unchanged)

`UNIT_ONLY→E1`, `INTEGRATION→E2`, `VERTICAL→E3` (all direct, Topology
Coverage). `PRODUCTION_PRV→provisional E4` (subject to §10's per-scenario
check). Invariant Catalog `Unit Evidence→E1`, `Vertical Evidence→
presumptive E3` (not row-audited). `PROTECTED`/Fault-Injection `PROTECTED`
+ Tier → **no automatic E-level mapping** (a judgment resting on a
combination of evidence, not itself a level). Fault Injection's
"detection alone" vs. "both" note stays a separate correctness-
completeness field, not folded into E0–E5. PRV narrative outcomes and
P-series `PASS` → not mapped in bulk; each needs individual per-scenario,
per-risk attribution during population.

## 12. Ambiguities status

All ambiguities raised in the prior round are resolved or explicitly
retained as population-time work, per §9's collision fix and §10/§11's
carried-forward rules. No further schema-blocking ambiguities identified
in this pass.
