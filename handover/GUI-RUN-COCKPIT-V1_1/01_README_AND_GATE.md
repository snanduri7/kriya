# 01 — Kriya Run Cockpit v1.1: README and design gate

**Topic:** the Kriya Electron GUI beyond M1 — the Run Cockpit (design freeze, then implementation).
**Owner / arbiter:** Sriram. **Author of this package:** Claude (coding agent), 2026-10-10.
**Repository:** Kriya-main-demo, `main` @ 1a89bc5 (records-only closure commit on 6c917fa == origin/main); product
frozen at certified executable 3844540. This folder is additive; nothing under `kriya/`, `ui/`, `tests/` changed.
**Governance pattern:** `handover/GUI-DESIGN-DISCUSSION/README.md` (proposal → review → gate), applied with one
independent review (owner's one-review rule, 2026-10-10).

Read this file first, then `02`, then `03`, then `04`, then `05`. `06` and `07` are the reviews. `inputs/` holds the
frozen visual reference and the superseded v1.0 specification.

## 1. Authority order (highest first)

1. Owner decisions recorded in §3 and the signed gate in §5.
2. `02_GUI_RUN_COCKPIT_V1_1.md` — UX, information architecture, interaction and acceptance authority.
3. `04_UI_READ_API_CONTRACT.md` — data contract authority for everything beyond KUP v1.
4. `03_EVIDENCE_AVAILABILITY_MATRIX.md` — what may be rendered and from which source.
5. `05_IMPLEMENTATION_PLAN.md` — order, ownership, tests.
6. GUI M1 (`handover/GUI-DESIGN-DISCUSSION/05_GATE.md`, `03_PROPOSAL_v2.md`, `ui/README.md`) — remains the data
   authority for KUP v1 and the host boundary; this topic never weakens it.
7. `inputs/KriyaJavaHamcrestRepairDashboard.png` — visual character only; proves no feature.
8. `inputs/KRIYA_ELECTRON_GUI_DESIGN_SPEC_v1_0_SUPERSEDED.md` — superseded input, kept for traceability.

## 2. Sequence of files

| # | File | Author | Purpose |
|---|---|---|---|
| 01 | this file | Claude, signed by the owner | purpose, authority, frozen decisions, review sequence, gate |
| 02 | `02_GUI_RUN_COCKPIT_V1_1.md` | Claude | the authoritative design (one document) |
| 03 | `03_EVIDENCE_AVAILABILITY_MATRIX.md` | Claude | A/B/C/D/E classification of every datum, traced at 1a89bc5 |
| 04 | `04_UI_READ_API_CONTRACT.md` | Claude | KUP v1.1 composed read façade, strict-read rules, errors, future phase schema |
| 05 | `05_IMPLEMENTATION_PLAN.md` | Claude | phases, ownership, tests, first slice, prerequisites |
| 06 | `06_REVIEW_001_EXISTING_CODE.md` | Claude | the v1.0 specification reviewed against the code (preserved) |
| 07 | `07_REVIEW_002_FINAL_DESIGN.md` | independent reviewer + Claude | the single independent design review and its reconciled disposition table |
| inputs/ | PNG, v1.0 spec | owner | frozen visual reference; superseded input |

## 3. Frozen decisions (owner, 2026-10-10)

| # | Decision |
|---|---|
| F-1 | Product identity: Explainable Agent Execution Cockpit; Goal → Run → Phase/Unit → Attempt → Evidence → Outcome; not chat-first, not an IDE. |
| F-2 | Visual reference: the accepted PNG, for character only. No unsupported control or telemetry is copied. |
| F-3 | Compose, do not normalize: every returned object keeps source operation, schema version, consistency kind, provenance and verbatim values. |
| F-4 | All store reading stays in Kriya (Python). Electron main is a typed proxy and cache; the renderer consumes typed contracts. No Electron process parses SQLite, JSONL, gzip or evidence directories. |
| F-5 | Explicit phase events are not on the GUI critical path. Role-derived phase data is shown only when labelled DERIVED; no KnowledgeGuard or Commit boundaries are fabricated for historical runs. |
| F-6 | The M1 topic-folder pattern is followed; the v1.0 spec and the first review are preserved; the PNG is in `inputs/`. |
| F-7 | Matrix rows for capture mode, retention/pruning state and recorder gaps are mandatory (03 rows 25, 27, 28). |
| F-8 | Selection state includes selected phase filter and selected gate (02 §5). |
| F-9 | Initial navigation: Runs, Workspace, Settings. Knowledge, Models, Tools, Experiments stay hidden until their read operations exist. |
| F-10 | Three-surface rule and one navigator (02 §3.3, §3.4). Seven central tabs, no more. |
| F-11 | Bottom drawer is read-only: Build, Tests, Git, Raw Events; Model I/O after the read contract; no LSP/MCP without evidence; no terminal, no run control. |
| F-12 | Live mode and actions are separate future topics. Pause is absent until a backend mechanism exists. Token/sec is never displayed (not persisted). |
| F-13 | Cohort classifications never appear as runtime verdicts. |
| F-14 | Exactly one independent design review (07); objective errors fixed directly; owner-level questions recorded here, no Review 003. |
| F-15 | The agent prepares the gate evidence and recommendation; the owner signs PASS/FAIL. |
| F-16 | Commit policy: documentation only, after the owner approves the design commit; no production code in this topic. |

## 4. Review sequence and reconciliation

1. Review 001 (06): the v1.0 spec against the code — complete; every finding is incorporated in 02–05.
2. Review 002 (07): one fresh independent reviewer, scope limited to: matrix versus persisted evidence; read
   contracts' provenance, privacy and authority; any UI claim of unsupported facts; navigation simplicity; shell
   readiness for live/actions without redesign. The reviewer does not redesign aesthetics, reopen identity, propose
   alternate navigation without a demonstrated defect, or expand scope.
3. Reconciliation: each finding dispositioned in 07 as FIXED (with the file and section changed), REJECTED (with the
   evidence), or OWNER_DECISION (listed in §6 below). No further review round.

## 5. Owner gate checklist

Status column: filled by the agent with evidence pointers after reconciliation; the decision line is the owner's.

| # | Criterion | Evidence | Status |
|---|---|---|---|
| G-1 | M1 preserved (no change to `kriya/`, `ui/`, `tests/`; M1 contracts referenced, not altered) | `git status`: only the untracked topic folder and the root PNG; no file under `kriya/`, `ui/`, `tests/` touched; 02 §19 item 1 | MET |
| G-2 | Accepted PNG frozen as visual reference | `inputs/KriyaJavaHamcrestRepairDashboard.png`; 02 header | MET |
| G-3 | Attempt-evidence store incorporated | 03 S2 rows (10, 12, 15, 18, 25–31, 38, 40, 41, 44, 46, 47, 50, 56–60, 64); 02 §8–§11; 04 §4 | MET |
| G-4 | Evidence matrix complete (every datum any screen shows is classified; UNKNOWN items listed) | 03 §1 (92 rows), §2, §4 (4.1–4.5 resolved; 4.6, 4.7 open and scheduled in 05 §8); review 002 Q1 after fixes | MET |
| G-5 | No direct store parsing by Electron | 04 §1 rule 1; 02 §18; review 002 Q2 | MET |
| G-6 | Provenance preserved (compose, not normalize) | 04 §1 rule 2, §4.1 (verbatim seal/manifest), §4.10; review 002 Q2 | MET |
| G-7 | Timeline is the only hierarchical navigator | 02 §3.4, §5 (incl. callSeq, F-23), §16; review 002 Q4 | MET |
| G-8 | Context, Changes, WHY are evidence-backed with honest degradation | 02 §9.2, §9.4, §10; 03 §3; 04 §4.4 class table | MET |
| G-9 | Unavailable evidence explicit (four states + degraded) | 02 §12; 03 §3 | MET |
| G-10 | No fabricated phases, live telemetry or actions | 02 §2 item 5, §8; 03 rows 9, 67, 77–80 | MET |
| G-11 | No interactive terminal | 02 §11.3; 03 row 80 | MET |
| G-12 | No cohort vocabulary in normal runtime status | 02 §2 item 10; 03 row 91 | MET |
| G-13 | Initial navigation limited to Runs/Workspace/Settings | 02 §3.1 | MET |
| G-14 | Deep evidence reachable within two interactions | 02 §2 item 8, §19 item 13 | MET (design rule; measured in Phase 3 tests) |
| G-15 | Responsive behaviour defined | 02 §4.1 | MET |
| G-16 | Implementation dependencies ordered correctly (no inversion; no throw-away plumbing) | 05 §1; review 002 Q5 | MET |

**Agent recommendation (2026-10-10, after Review 002 reconciliation):** PASS, conditional on Q-1 … Q-4 below being
answered before the first code slice. Review 002: 0 blockers, 6 majors (all re-verified and fixed), 17 minors, 9 notes;
29 fixed, 2 owner decisions, 1 no change (`07`).
**Owner decision:** ☑ PASS ☐ FAIL — signed: Sriram (owner) date: 2026-10-10. Q-1 … Q-4 decided as recorded in §6. No Review 003.

## 6. Owner-level decisions (DECIDED 2026-10-10, final)

| # | Question | Options | Needed before |
|---|---|---|---|
| Q-1 **APPROVED** | Authorize the Phase 1A product change (new read-only KUP modules on top of 3844540, no workflow change) | yes / defer | first code slice |
| Q-2 **APPROVED: `off` by default; Kriya config owns it (SECURITY_AUTHORITY); host may only narrow** | Evidence-content visibility (Model I/O, prompt text, candidate content, goal): the authority is a Kriya config field `evidence.attempt_recorder.content_visibility`, SECURITY_AUTHORITY-classified, which the host can only narrow (review 002 F-2). Confirm the default. | `off` by default (proposed) / `on` | 1A-3 |
| Q-3 **APPROVED: DEFERRED P3 registry row `GUI-PHASE-OBSERVABILITY-001` (target_scope Phase 6 of 05; design 04 §7)** | Phase 6 (explicit phase events, `04 §7`) scheduling | DEFERRED P3 registry row now (proposed) / schedule after Phase 3 | registry update |
| Q-4 **DECIDED: retire M1 inspector panels only after cockpit parity in the test host and owner acceptance; keep them functioning meanwhile; no permanent hidden legacy route; the reducer is EXTENDED additively, M1 read contracts preserved** | (review 002 F-24) Keep the M1 panels reachable behind the new shell during migration, or retire them once the cockpit renders every M1 panel's data? | retire after parity in the test host (proposed) / keep behind a hidden route | Phase 2 start |

## 7. Commit and branch policy for this topic

Design documents only in the design commit (owner authorized it 2026-10-10; it also carries the Q-3 registry row). Implementation: local slice commits on the single branch `gui/run-cockpit-v1_1`; never pushed without explicit approval. The PNG at the
repository root stays untracked (owner policy on untracked files); the copy in `inputs/` is the tracked reference.
Branch: `main` (main-only policy); the implementation workstream, when authorized, gets exactly one branch.
