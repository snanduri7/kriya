# Kriya v1 Critical Path

Analysis only — no implementation, no P9 execution authorized by this
document. This is the smallest dependency-respecting sequence of risk
closures necessary to reach the approved v1 envelope, not a plan to
close every architecture-quality concern.

## §1. Implementation-blocker view (Class A)

### `SEC-005` — Subprocess/package/network broker
Current evidence: E0. Current mechanism: none — `tools/process.py`/
`tools/web.py`/package-resolution logic exist but aren't broker-mediated.
Why implementation is unavoidable: no amount of live validation can prove
policy can deny a side effect before execution when no such gate exists.
Blocking dependencies: none among open risks (implies building `KRP-014`
scaffolding, not itself a tracked risk). Downstream unlocked: `POL-001`,
transitively `SEC-001`/`SEC-003`/`TOOL-002`/`TOOL-003`/`TOOL-001`.
Related KRP: `KRP-016` (+ `KRP-014`). Likely scope: normalize
command/network/package requests, wrap existing executors, migrate call
sites. Architecture impact: **ARCHITECTURE EXTENSION** (new broker layer;
not a redesign of existing safety logic, which is explicitly preserved
underneath it).

### `CONC-001` — Safe rejection of a conflicting mutation run
Current evidence: E0. Current mechanism: none (confirmed by direct grep).
Why unavoidable: DE-05's explicit requirement; no mechanism exists to
test. Blocking dependencies: none. Downstream unlocked: none directly,
but removes a real, currently-open unattended-safety gap. Related KRP:
`KRP-004`. Likely scope: canonical workspace identity, writer lease with
stale-owner recovery policy, read-vs-write lease semantics — the envelope
itself already names the required properties. Architecture impact:
**ARCHITECTURE EXTENSION** (new, self-contained service; does not touch
existing orchestration).

### `MODEL-001` — Model capability certification
Current evidence: E1. Current mechanism: static capability profiles only,
no active probing. Why unavoidable: nothing currently confirms the
configured model/quantization/template/server combination actually
behaves as declared. Blocking dependencies: none (independently
buildable; the roadmap's own `KRP-003` dependency is about a full event
store, not a hard prerequisite for a narrow fingerprint+probe script).
Downstream unlocked: `TOOL-004`, `REL-002` (both need it eventually).
Related KRP: `KRP-023`. Likely scope: fingerprint (digest/quantization/
template/server version), conformance probes (JSON mode, tool-call
syntax, streaming, deterministic tiny task), cache-by-fingerprint.
Architecture impact: **ARCHITECTURE EXTENSION** (new module, no existing
behavior changed).

### `OBS-001` — Enforce-mode telemetry gap
Current evidence: E4 (the gap itself directly observed, P8). Current
mechanism: `plan_repair_attempts` correctly wired; `generation_metrics`
is not populated in enforce mode's aggregated result. Why unavoidable:
already proven to force manual `traces.db` aggregation for every future
enforce-mode run's own performance reporting. Blocking dependencies:
none. Downstream unlocked: `OBS-002` needs it. Related KRP: `KRP-033`.
Likely scope: read/copy the same per-subtask metrics already collected
into the enforce-mode result, additive only. Architecture impact:
**MISSING WIRING** — the smallest-scoped item on this entire list, this
session's own Deliverable-5-style fix, not a new mechanism.

### `TOP-001` — Gradle build-system support
Current evidence: E0. Current mechanism: none — `_detect_stack()` has no
Gradle branch. Why unavoidable: DE-01's own explicit requirement, no
substitute. Blocking dependencies: none. Downstream unlocked: none
directly (recovery/retry machinery is already language-neutral and would
apply automatically once this lands). Related KRP: none dedicated in the
roadmap (a gap in the roadmap itself, not in this analysis). Likely
scope: `<modules>`-equivalent parsing for Gradle multi-project builds,
compile/test-gate integration mirroring the Maven path. Architecture
impact: **ARCHITECTURE EXTENSION** (a real, substantial second build-
system adapter — the largest single item on the Class A list, named
honestly as such, not minimized).

## §2. Validation-candidate view (Class B) — abbreviated to the ranked top items; full list in `KRIYA_V1_RISK_PRIORITIZATION.md`

| Risk | Current evidence | Required | Mechanism believed sufficient | Missing proof | Prerequisites | Recommended validation |
|---|---|---|---|---|---|---|
| `RECV-002` | E1 | E4 | Yes (MA9/`repair_contract.py`) | A real run that actually triggers coordinated repair (PRV-11 didn't) | None | New/redesigned production scenario — PRV-11's existing goal shape doesn't reliably trigger it |
| `CORR-016` | E0 | not below E3 | Plausibly (ObligationLedger already tracks regression during repair) | Whether it drives full transitive multi-consumer revalidation on a *deliberate* change | None | PRV-08 (scenario frozen, ready) — its own README requires the live goal plus a deterministic/manual check, not the live goal alone |
| `VER-005` | E2 | E4 | Yes (sweep confirms broad real capability) | One clean, fully-confirmed pass (PRV-17 is `NEEDS_REVIEW`, one manual check short) | None | Production P-series/PRV run against a Python target |
| `REPO-004` | E1 | not below E3 | Plausibly (worktree isolation, `REPO-001`) | Real isolation confirmation | None | PRV-14 (scenario frozen, ready) — same manual-check caveat as CORR-016 |
| `STATE-001` | E1 | not below E3 | Yes (`checkpoint.py`, unit-tested) | Real interruption, not just a clean resume | None | Deterministic fault-injection test (kill process mid-run in a test harness) — no live model needed |
| `VER-004` | E2 | not below E3 | Yes (confirmed this session) | A dedicated test isolating extraction→install | None | Deterministic unit test — no live model needed |
| `REL-001` | E0 | not below E2 | Unclear, likely yes (repo already has `pyproject.toml`) | Direct confirmation against `KRP-032`'s acceptance bar | None | Source-state review — cheapest item in the register |
| `TOP-002` | E1 | not below E3 | Yes (P7's own repo already non-Spring) | Direct confirmation, not a new run | None | Review of existing P7 evidence |
| `TOP-005` | E2 | not below E3 | Yes (P-series harness itself) | Direct confirmation/documentation, not new implementation | None | Review of existing harness invocation pattern |

## §3. `STATE-001`/`STATE-002` — does one truly block the other?

**No — `STATE-001` does not need `STATE-002` to close, and `STATE-002`'s
own status is *contingent on* `STATE-001`'s result, not blocked by an
unimplemented mechanism.** `STATE-001` can be validated independently
right now via a deterministic kill-mid-run test against the *existing*
checkpoint/resume path. If that test shows fragmented state stores
survive real interruption correctly, `STATE-002`'s own concern may
already be satisfied and its disposition should move toward `CLOSED`
rather than `NEEDS_IMPLEMENTATION` — the unification KRP-026 proposes
would then be pure architecture-debt (`OPTIONAL`), not a REQUIRED gap. If
it shows a real crash-consistency failure, `STATE-002` becomes a genuine
Class A item at that point, and `KRP-026`'s unification becomes one
candidate fix among possible narrower ones. **This ordering — validate
`STATE-001` before deciding anything about `STATE-002` — is itself part
of the critical path, not a side note.**

## §4. P9 selection

**BEST P9 CANDIDATE: `CORR-016` (PRV-08 — Contract Evolution / Transitive
Invalidation).**

Why: mechanism plausibly exists and no implementation blocker stands in
the way; the scenario is already frozen and ready to execute (unlike
`RECV-002`, which would need a new or redesigned scenario since PRV-11
didn't reliably trigger the mechanism it was built to test); it tests a
real, currently-unproven-either-direction mechanism (transitive
multi-consumer contract revalidation), not a repeat of P1–P8's shape;
deterministic acceptance is definable (does every real downstream
consumer get updated, not just the directly-changed file); and it is
directly tied to `MA8`'s own obligation-lifecycle correctness, a REQUIRED
DE-02 (multi-module) concern.

**Alternatives:**
1. `RECV-002` (MA9 coordinated repair) — higher intrinsic stakes (this
   mechanism has *never* been genuinely exercised by any real run) but
   requires new scenario design work first, since PRV-11's existing goal
   doesn't reliably trigger recovery — not "ready to go" the way PRV-08 is.
2. `VER-005` (Python end-to-end) — the strongest breadth pick (new
   language family, direct DE-01 test) if the next priority is expanding
   *what kind* of repository Kriya has been proven against, rather than
   deepening evidence on a still-Java mechanism.

## §5. Optional architecture refactor on the critical path

**`ORCH-001`/`ORCH-002` remain `OPTIONAL` overall.** The one narrow,
confirmed exception: `TOOL-001` (`KRP-021`) genuinely depends on `KRP-012`
(migrating the structured controller into the new engine), per that
task's own stated dependency list, read from the full spec text. This
does **not** promote the entire orchestration-consolidation project to
REQUIRED — only the specific, narrow slice of `KRP-011`/`KRP-012`'s work
that `KRP-021` actually needs is implied, and only once the rest of the
security/tool chain (`SEC-005` → `POL-001` → ... → `TOOL-002`) has
already closed, since `TOOL-001` sits at the very end of that chain
regardless.

**OPTIONAL ARCHITECTURE REFACTOR REQUIRED ON CRITICAL PATH: YES.**
Narrow dependency only: the portion of `KRP-011`/`KRP-012` needed for
`KRP-021`'s own TOOL-subtask dispatch to exist — not orchestration
consolidation generally, and not before it's actually reached in the
security chain (i.e., not now).

## §6. Critical path, in dependency order

For the security/tool/policy chain specifically (the longest chain in the
register):

`SEC-005` → `POL-001` → `SEC-001` → `SEC-003` → `TOOL-002`/`TOOL-003` →
(narrow `KRP-011`/`KRP-012` slice) → `TOOL-001` → `TOOL-004`

Independent of that chain, and not blocking or blocked by it:

`CONC-001`, `MODEL-001`, `OBS-001`, `TOP-001` (Class A, no
interdependency among themselves) — and, once `STATE-001` is validated,
either the closure of `STATE-002` (if it turns out `CLOSED`) or its
promotion to a real Class A item feeding `STATE-003`.

**This is not a recommendation to build the whole chain now.** Per §12 of
the governing instructions, the register does not authorize implementing
KRP-001 through KRP-033, and this document does not either — it states
what *would* have to happen, in what order, if and when the security/tool
chain is prioritized, separately from whichever single item is actually
chosen next.
