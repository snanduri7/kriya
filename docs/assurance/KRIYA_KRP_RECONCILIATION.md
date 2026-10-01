# KRP-001–033 → Risk Register Reconciliation (Pass 2 — full-spec)

Source: full text of `TASKS_DETAILED.md` (all 1923 lines, all 33 tasks —
read completely this pass, not the `MASTER_ROADMAP.md` one-liners Pass 1
used). All 33 KRPs now end with a mapped Risk ID or an explicit
`NO DISTINCT RISK` rationale — **33/33**.

Reading the full spec text did not overturn any Pass-1 domain mapping,
but it materially sharpened several dispositions and directly grounded
one reclassification the review specifically asked for (`TOOL-001`,
row 21).

| KRP | Deliverable | Risk ID(s) | Disposition | Rationale (updated where the full spec changed it) |
|---|---|---|---|---|
| KRP-001 | Characterization/regression harness | *(packaging concern, no single risk row)* | NEEDS_IMPLEMENTATION | Full spec (acceptance criteria: "clean checkout runs deterministic tests without a live LLM," "every safety invariant has a passing and failing scenario," "machine-readable JSON + human summary") confirms this is a packaging/reproducibility deliverable, not a new capability — R1 + PRV harness + P-series already produce the underlying evidence, just not in this shape or location |
| KRP-002 | Modular architecture contracts | `ORCH-001` | NEEDS_IMPLEMENTATION, OPTIONAL priority | Full spec is explicit this is contracts-only, "no existing runtime path changes" — pure architecture-debt work, confirming the `ORCH-001` reclassification below |
| KRP-003 | Canonical Run ID/event schema | `STATE-002`, `STATE-003` | NEEDS_IMPLEMENTATION | Full spec's acceptance criteria ("crash during append cannot produce a partially readable event," "legacy stores continue working unchanged") is additive infrastructure — supports both the consistency risk and the new replay risk |
| KRP-004 | Single-writer workspace lease | `CONC-001` | NEEDS_IMPLEMENTATION | Full spec directly warns against exactly the naive fix I originally proposed and was corrected on: "same-repo concurrency test, crash/stale lease test, symlinked workspace identity test" as required tests — confirms `CONC-001`'s own note that a pidfile/flock alone isn't assumed sufficient |
| KRP-005 | Extract RepositoryTransaction service | `REPO-001`, `REPO-002` | NEEDS_IMPLEMENTATION | Full spec: "no safety algorithm rewrite in this task," wraps existing `worktree.py`/`edit_safety.py`/`filesystem.py` — confirms Pass 1's framing that the underlying mechanisms are already `CLOSED`, only the service-extraction shape is open |
| KRP-006 | Universal verification contracts | `VER-003`, `VER-004`, `VER-005` | NEEDS_IMPLEMENTATION | Full spec's gate categories (STATIC/COMPILE/TARGETED_TEST/FULL_TEST/RUNTIME/SPEC/BROWNFIELD_API/OWNERSHIP/CUSTOM) span both language tracks |
| KRP-007 | Evidence-backed targeted test selection | `VER-003` | NEEDS_EVIDENCE | Full spec: "make retry-time targeted verification explainable," depends on `KRP-006` — the underlying targeted-retry logic exists in `attempt.py`, whether it's "evidence-backed" in the formal sense KRP-007 wants is unconfirmed, not absent |
| KRP-008 | Typed failure taxonomy | *(no dedicated risk row — adjacent to `RECV-001`)* | NEEDS_IMPLEMENTATION | Full spec's required taxonomy (POLICY/INFRASTRUCTURE/MODEL_PROTOCOL/CONTEXT/VERIFICATION/REPOSITORY_DRIFT/OPERATOR/TOOL/RESOURCE_BUDGET/INTERNAL_INVARIANT) is materially broader than `Failure`/`FailureLedger`/`EventAuthority`'s current real but narrower shape — confirmed by reading the full 10-class list, not just noting a taxonomy exists |
| KRP-009 | Extract RetryPolicy/RepairPlanner | `ORCH-002` | NEEDS_IMPLEMENTATION, OPTIONAL priority | |
| KRP-010 | Extract AttemptExecutor | `ORCH-002` | NEEDS_IMPLEMENTATION, OPTIONAL priority | Full spec depends on KRP-005/006/008/009 — none of which depend on orchestration unification themselves, confirming the independence finding below |
| KRP-011 | Authoritative typed workflow engine | `ORCH-001` | NEEDS_IMPLEMENTATION, OPTIONAL priority | |
| KRP-012 | Migrate WorkflowController into engine | `ORCH-001` | NEEDS_IMPLEMENTATION, OPTIONAL priority | **Confirmed this pass as the real dependency root for `TOOL-001`**: `KRP-021`'s own stated dependency is `KRP-020, KRP-012` |
| KRP-013 | Switch to one authoritative path | `ORCH-001` | NEEDS_IMPLEMENTATION, OPTIONAL priority | |
| KRP-014 | Action Broker contracts | `POL-001`, `SEC-001`, `TOOL-002` | NEEDS_IMPLEMENTATION | Full spec's dependencies are `KRP-002, KRP-003` only — **confirmed independent of orchestration unification (`KRP-011`/`012`/`013`)**, directly supporting the requirement-authority challenge on `ORCH-001`/`002` |
| KRP-015 | Migrate filesystem/Git through broker | `REPO-003` (mechanism CLOSED; brokering is new) | NEEDS_IMPLEMENTATION | Depends on `KRP-014, KRP-005` — same independence finding |
| KRP-016 | Migrate subprocess/package/network through broker | `SEC-005` | NEEDS_IMPLEMENTATION | Depends on `KRP-014, KRP-006` — same independence finding |
| KRP-017 | Execution policy audit → enforceable | `POL-001` | NEEDS_IMPLEMENTATION | Full spec: "fail startup/doctor check if enforce mode is requested while broker coverage is incomplete" — a real, specific acceptance bar beyond what Pass 1 credited |
| KRP-018 | SandboxBroker | `SEC-001` | NEEDS_IMPLEMENTATION | Depends on `KRP-017, KRP-014` — independent of orchestration unification |
| KRP-019 | Harden MCP transport | `SEC-003`, `SEC-004` | NEEDS_IMPLEMENTATION | |
| KRP-020 | Authoritative ToolBroker | `TOOL-002` | NEEDS_IMPLEMENTATION | **This task's own "Why this task exists" line is the direct source for `TOOL-001`'s reclassification**: "Structured enforce mode currently refuses TOOL-tagged subtasks because safe authoritative routing does not exist." |
| KRP-021 | Enable structured TOOL subtasks | `TOOL-001` | NEEDS_IMPLEMENTATION | Dependencies: `KRP-020, KRP-012` — this is the one place in the whole roadmap where the security/tool work *does* depend on orchestration unification, confirmed directly from the spec text |
| KRP-022 | Revision-aware ContextService | `CTX-001` | NEEDS_IMPLEMENTATION | |
| KRP-023 | Model runtime fingerprinting | `MODEL-001` | NEEDS_IMPLEMENTATION | |
| KRP-024 | Explicit run/subtask resource budgets | **`OBS-004` (new row, added this pass)** | NEEDS_IMPLEMENTATION | Full spec confirms this is a real, distinct, currently-unimplemented governing object ("wall-clock deadline, LLM calls/tokens, retries, tool calls, process execution time, test time, network bytes, output bytes, disk budget... one budget object") — Pass 1's "population gap" is closed |
| KRP-025 | Centralized secret redaction | `OBS-003` | NEEDS_EVIDENCE first | Full spec confirms no existing mechanism was assumed — "prevent credentials/tokens/secrets from leaking" is the objective, not yet confirmed absent or present with the rigor the register requires |
| KRP-026 | Unify state through event projections | `STATE-002` | NEEDS_EVIDENCE first (reframed this pass) | Full spec's own acceptance criteria are about crash-recovery *behavior* ("killed process after every tested transition can resume to a defined state") — that behavioral bar is what's actually required by DE-03, not this specific unification architecture; see `STATE-002`'s own reframing |
| KRP-027 | Deterministic run replay package | **`STATE-003` (new row, added this pass)** | NEEDS_IMPLEMENTATION | Full spec: "reproducibility ID," "explain replay reconstructs decisions without re-executing side effects," "bundle validation detects tampering" — distinct from `STATE-002`, confirmed by reading the full spec, not merely adjacent |
| KRP-028 | Adversarial/fault-injection/concurrency suites | `CORR`/`RECV`/`VER`-domain fault injection (CLOSED-equivalent, 10/10 PROTECTED) **+** `SEC-001`, `CONC-001` (NEEDS_IMPLEMENTATION) | **MIXED — decomposes across Risk IDs, not a single disposition** | **Correction from Pass 1, per explicit review**: KRP-028 is a roadmap task, not a risk row — the approved Risk Register disposition vocabulary (`CLOSED`/`NEEDS_EVIDENCE`/`NEEDS_IMPLEMENTATION`/`SUPERSEDED`/`DEFERRED`) is not extended with a `MIXED` value; `MIXED` is reconciliation-table language *only*, describing that this one roadmap task spans multiple already-distinct risk rows whose own dispositions differ. The register itself was never touched to accommodate this — no schema change was made |
| KRP-029 | Model/runtime regression matrix | `MODEL-001` | NEEDS_IMPLEMENTATION | |
| KRP-030 | `kriya doctor --production` | `REL-002` | NEEDS_IMPLEMENTATION | Full spec's required checks list (model fingerprint, sandbox capability, policy mode, broker coverage, MCP trust, context freshness, workspace lease, disk/resource budget) confirms `doctor` today covers only a fraction |
| KRP-031 | Plugin manifest/provenance registry | **`TOOL-004` (new row, added this pass)** | NEEDS_IMPLEMENTATION | Full spec confirms a distinct, real gap: manifest fields, registry rejecting incompatible versions, capability-approval-before-side-effect-authority — `kriya/plugins/` exists but none of this governance layer was found or traced |
| KRP-032 | Release engineering | `REL-001` | NEEDS_EVIDENCE first | Full spec's acceptance criteria ("no `__pycache__`/`.DS_Store`/`__MACOSX` in artifact," "SBOM ships with release") — the live repo's actual current packaging state (a real `pyproject.toml` exists, confirmed by this session's own `pip install -e .`) was not fully re-verified against this bar this pass either |
| KRP-033 | Operator run summary | `OBS-001`, `OBS-002` | NEEDS_IMPLEMENTATION | **Directly, independently validated as a real gap by this project's own P8 run** — required a manual three-row `traces.db` aggregation to produce P8's own performance section, observed firsthand this session, not inferred from the spec text |

## Coverage confirmation: 33/33

Every KRP now has a mapped Risk ID (or set of Risk IDs) or an explicit
rationale for why no single risk row applies (`KRP-001`, `KRP-028`). None
left unreconciled.

## What full-spec reading changed vs. the one-line summaries

Materially: nothing overturned a Pass-1 domain mapping. What changed was
precision — `KRP-020`'s own text became the direct textual grounding for
reclassifying `TOOL-001` away from "requirement-authority conflict";
`KRP-021`'s explicit `KRP-012` dependency is the actual, confirmed reason
`ORCH-001` has *any* transitive necessity at all (as opposed to Pass 1's
looser "the review recommends it" framing); `KRP-014`/`015`/`016`/`018`'s
dependencies confirmed the security/policy/broker work is structurally
independent of orchestration consolidation, which is the concrete basis
for reclassifying `ORCH-001`/`ORCH-002` from REQUIRED to OPTIONAL. Three
previously-unmapped tasks (`KRP-024`, `027`, `031`) now have real risk
rows instead of being flagged as gaps.
