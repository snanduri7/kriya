# PRV-01–18 → Risk Register Reconciliation (Pass 2 — raw-evidence re-audit)

Pass 1 sourced outcomes from project-memory narrative and result-directory
*existence*. Pass 2 re-read the actual `RESULT.md`/`COMPARISON.md` files
for every PRV whose evidence was cited at E4, per the explicit instruction
not to trust "result exists" as a proxy for "result confirms this."
**Six of eleven previously-clean E4 citations changed as a result** —
this table records exactly what the raw files say, not what the scenario
name or a PASS label implied.

| PRV | Name | Raw result re-read this pass? | Actual finding | Risk ID(s) | Disposition contribution |
|---|---|---|---|---|---|
| PRV-01 | Small Contained Bug | No (folded, not independently cited at E4) | — | `CORR-017` | Folded — corroborates baseline correctness |
| PRV-02 | Security-Sensitive Bug | **Yes** | `RESULT.md` status: **NEEDS_REVIEW**. Quality Gates passed, `TokenValidator.java`/`TokenValidatorTest.java` produced, but the scenario's own manual check ("confirm valid/expired/malformed token semantics") is **unticked** | `POL-002` | **Downgraded from CLOSED/E4 to NEEDS_EVIDENCE/E2** — real activity occurred, the specific security-semantic correctness this scenario exists to prove was never confirmed |
| PRV-03 | Brownfield Enhancement | No | — | `CORR-017` | Folded |
| PRV-04 | Shared Entrypoint Extension | No | — | `CORR-013` | Superseded, unchanged |
| PRV-05 | Dependency Refactor | No | — | `CORR-017`, `CORR-008`–`010` | Folded |
| PRV-06 | Multi-Stage Single Application | **Yes** | `RESULT.md`/`COMPARISON.md` status: **FAIL for BOTH `legacy` and `hardened` variants**, reason `KRIYA_GREENFIELD_GIT_BOOTSTRAP_MISSING` — a harness/fixture-precondition failure, unrelated on its face to the process-boundary-compatibility mechanism this scenario is named for and that project memory describes being fixed | `RECV-003` | **The Pass-1 "E4, real live incident, real fix, confirmed" claim is withdrawn.** The fix and its own dedicated unit/vertical tests are real and independently named, supporting E3 on their own — but this specific captured result does not confirm the mechanism via a clean production run, and shows an apparently unrelated failure instead. Recorded as an evidence-validity note, not silently reconciled either direction — this pass does not investigate or fix the bootstrap error (analysis-only) |
| PRV-07 | Existing Multi-Module Composition | No (never run) | Spec only | `TOP-MVN-003`/`004` | No distinct risk |
| PRV-08 | Contract Evolution / Transitive Invalidation | No (never run) | Spec only | `CORR-016` | NEEDS_EVIDENCE, unchanged |
| PRV-09 | Artifact Drift | No (never run) | Spec only | `CTX-003` | NEEDS_EVIDENCE, unchanged |
| PRV-10 | Public Knowledge Gap — Ignite | No (never run) | Spec only | `MODEL-002` | NEEDS_EVIDENCE, unchanged |
| PRV-11 | Cross-Subtask Plan Recovery | **Yes** | `RESULT.md` status: PASS, but its own **explicit** text states: *"Plan Recovery Capability — NOT EXERCISED this run — no `plan_recovery_events` — this run proves nothing about plan recovery specifically."* | `RECV-002` | **Downgraded from CLOSED/E4 to NEEDS_EVIDENCE/E1** — the scenario passed for reasons unrelated to the MA9 mechanism it exists to test, by its own self-report |
| PRV-12 | Behavioral Runtime Verification | No (folded, not independently cited at E4) | — | `VER-002` | Folded — `VER-002`'s own E4 rests on the real P6 live defects (`e073d47`/`e790c1a`), not on PRV-12 |
| PRV-13 | Bounded Self-Correction | **Yes** | `RESULT.md` status: **NEEDS_REVIEW**, manual check ("confirm self-correction did not write build config") unticked | `RECV-004` | **Downgraded from CLOSED/E4 to NEEDS_EVIDENCE/E1** |
| PRV-14 | Workspace State Isolation | No (never run) | Spec only, fault/recovery invariant | `REPO-004` | NEEDS_EVIDENCE, unchanged |
| PRV-15 | Crash / Resume | No (never run) | Spec only, fault/recovery invariant | `STATE-001` | NEEDS_EVIDENCE, unchanged |
| PRV-16 | Large Repository Context / Scale | No (never run) | Spec only | `CTX-002` | NEEDS_EVIDENCE, unchanged |
| PRV-17 | Fresh Repository Stack Drift | **Yes** | `RESULT.md` status: NEEDS_REVIEW, but the underlying evidence is **materially stronger than Pass 1 credited**: a genuine Django greenfield generation (`manage.py`, `config/`, `customers/`, `pyproject.toml`, 11 files), `Quality Gates: PASSED`, `Kriya exit: 0`. The only open item is one scope-creep manual check ("confirm project remains Python/Django only"), not a correctness failure. This is also confirmed as **Python evidence**, not Java as Pass 1 left ambiguous | `MODEL-004`, `VER-005` | **`MODEL-004` downgraded from CLOSED to NEEDS_EVIDENCE** (heavily exercised is not the same as confirmed-closed) — but `VER-005` (new row) is **upgraded** relative to Pass 1's blanket Python pessimism, from an assumed E0/E1 to a source-grounded E2 |
| PRV-18 | Undeclared Write Scope | **Yes** | `RESULT.md` status: **NEEDS_REVIEW**, manual check ("for deterministic denial, use included scope probe as well as live goal") unticked | `CORR-002`/`REPO-003` | The Pass-1 "corroborating E4" citation is **withdrawn**. `CORR-002`/`REPO-003` remain CLOSED regardless, because their real evidentiary basis is `FI-01`/`FI-09`'s own directly-verified pytest tests (confirmed to exist by grep both passes), not PRV-18 |

## Net effect of the re-audit

**Six of the seven PRVs with recorded results that were cited as clean E4
evidence in Pass 1 turned out, on raw re-read, not to support that
claim as stated** (PRV-02, 06, 11, 13, 18 downgraded; PRV-17 recharacterized
— upgraded in one respect, downgraded in another). Only `PRV-11`'s status
field itself and `PRV-06`'s apparent unrelated failure were genuinely
surprising; the `NEEDS_REVIEW`-with-unticked-manual-check pattern across
PRV-02/13/18 was consistent enough across three separate scenarios that
it looks like a structural property of how this harness reports results
that were passed over too quickly in Pass 1, not three unrelated
coincidences.

## The seven never-executed PRVs — unchanged this pass

07, 08, 09, 10, 14, 15, 16 remain unrun. Per the register's own rule, a
specification is not evidence; none are run in this pass (analysis only,
explicitly instructed).

## Coverage confirmation: 18/18

All 18 PRVs accounted for, unchanged from Pass 1's coverage claim — this
pass corrected the *quality* of six citations, not the *completeness* of
the mapping.
