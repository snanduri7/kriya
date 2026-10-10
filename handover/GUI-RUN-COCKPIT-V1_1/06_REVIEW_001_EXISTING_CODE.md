# 06 — Review 001: the v1.0 specification against the existing code (preserved)

**Reviewer:** Claude (coding agent), 2026-10-10, in the owner's session, before this topic folder existed.
**Subject:** `inputs/KRIYA_ELECTRON_GUI_DESIGN_SPEC_v1_0_SUPERSEDED.md` (section numbers below refer to it).
**Repository state reviewed:** Kriya-main-demo, `main` @ 1a89bc5, tracked tree clean, certified executable 3844540 frozen.
**Status:** preserved faithfully from the session message (wording kept; formatting adapted to this file). Two later
corrections from the same session are appended in §4; they do not alter the findings.
**Evidence labels:** MEASURED = counted/observed by a command; TRACED = read in the exact code path; UNKNOWN = not traced.

---

## 1. Verdict

The specification is strong on principles and gets the big calls right: evidence before decoration, capability
gating, real vocabulary, no terminal, live and actions split out. I would approve its product identity and UX
principles as written. I would not freeze the information architecture yet, because the spec is built on one wrong
premise about the backend. It assumes the trace database behind KUP is the only durable evidence, so it classifies
candidate diffs, prompts, per-call context and the unit/attempt hierarchy as "not recorded". Kriya has a second,
richer store that already records most of that. That store changes the evidence matrix, the phase rail, the timeline
tree and the phasing plan.

## 2. Findings against the code

**F1. There are two evidence stores, and the spec only knows one (TRACED).** KUP reads `traces.snapshot.db` only.
Separately, the attempt evidence recorder under `kriya/core/attempt_evidence/` writes a hash-chained, sealed, per-run
store at `<state>/attempt-evidence/<run_id>/` with capture mode `full` by default, which the config docstring
describes as recording prompts, responses, diffs and gate output as local blobs. Its record kinds include run, unit,
phase and attempt open/close, model.request/response/result, prompt.sections, candidate.change with before and after
digests, blobs and a unified diff, gate.result, tool.execution, recovery.decision, fallback.decision, diagnosis,
retry.delta and authority.snapshot. Every envelope carries run_id, unit_id, unit_kind, phase, attempt_number, call_seq
and role. The CLI already exposes it read-only as `kriya evidence show | explain | verify | leak-check | candidate`.
Consequences for the spec:

- Section 15 Changes: Mode A is category B (recorded, not exposed), not C. Mode B is still required for runs whose
  store was pruned by retention (keep_runs and max_bytes), runs captured in `digest_only`, and runs older than the
  recorder.
- Section 21 Model I/O and Section 13 "model-facing context": category B. The `explain` module already answers nine
  fixed questions with RECORDED, NOT_APPLICABLE or NOT_RECORDED, which is exactly the availability model the spec wants.
- Section 17 Compare Attempts: the recorder has `retry.delta` records, so attempt comparison has a direct source.
- Section 20 Why: the recorder's `diagnosis` records carry an evidence class per statement (MEASURED,
  DERIVED_DETERMINISTIC, MODEL_CLAIMED, UNKNOWN). The Why panel should display that class on every line. This is the
  strongest differentiator Kriya has and the spec does not mention it.
- The store is not SQLite. It is JSONL plus gzip blobs, with a seal and a verify. An unsealed store means an active or
  crashed run. Reading it needs its own strict-read rules, the same way D-4 defined them for the trace database.

**F2. No producer records phase boundaries (TRACED).** The banner names PLANNING, ARCHITECTURE, DEVELOPMENT & QUALITY
GATES and REVIEW are log lines only. The trace run_events have no phase events. The recorder reserves
`phase.opened/closed`, but its own comment says no producer sets an explicit phase in M1; the `phase` on each record is
derived from the model role: planning, architect, review, requirement_verification, localization, milestone_planning,
or "attempt". KnowledgeGuard and Commit have no role, so no phase. Section 10's rail "KnowledgeGuard → Planning →
Architecture → Development & Quality Gates → Review → Commit" therefore cannot be rendered from durable evidence for
any historical run, and Section 36 puts the rail in GUI-1. Two honest options: a rail derived from role-tagged calls
and sentinel events, labelled as derived, with the recorder's phase strings as names, or a small backend change that
opens and closes explicit phases. The second is the right fix but it is a product change on frozen 3844540.

**F3. The timeline hierarchy cannot be built from KUP data (TRACED).** Section 19 wants phase → unit → attempt →
model call → gate → event. Trace run_events carry only `attempt`, never a unit id. Gate outcomes carry subtask_id,
plan_id and milestone_id only on failures, never on successful gates. The recorder envelope has the full hierarchy.
So the inspector tree in GUI-1 is limited to attempt → event unless the recorder is exposed. The spec's phasing has
GUI-1 delivering the tree and GUI-2 adding the read APIs it needs. That order is backwards.

**F4. Per-test evidence is narrower than the spec assumes (TRACED, persistence UNKNOWN).** Pytest has structured
per-test outcomes from its JUnit report. Surefire gives a Results summary with failures and errors only, never a pass
list, so a "486 more passed" table is impossible for any Java validator. Gradle has nothing structured. Whether the
parsed `test_outcomes` reach a durable record a reader can get is not yet traced. Section 16's rule is right; the
examples should drop the pass list.

**F5. Token rate is category C, not gated-available (TRACED).** The runtime maps Ollama's eval_count to
completion_tokens and keeps prompt_eval and load durations, but does not keep eval_duration. Tokens per second cannot
be computed from anything persisted. Per-role prompt and completion tokens, latency and estimated-token call counts
exist in model.role_metrics. Served context is observed, so a context percentage is possible when both numbers are
present.

**F6. "Active" in the Runs pane conflates two things (TRACED).** Row status values are success, failure, error,
in_progress, knowledge_gap, baseline_indeterminate and architect_file_list_rejected. "Active" is a workspace property,
RUN_ACTIVE from workspace.status, and gate D-6 forbids attaching it to a trace row. An `in_progress` row may be a
crashed run. Define the chip as "Unfinished (no terminal record)" and show RUN_ACTIVE only in the trust strip for the
selected workspace.

**F7. Vocabulary mismatches to fix in the spec.** The consistency kinds are snapshot_copy, live_observation and
not_applicable; "Single observation" is not one of them. The failure_category column also carries lowercase values
such as knowledge_gap beside the uppercase categories, so the UI shows literals. "Engineering Pack" appears only in
design.md and architecture decisions, not in any recorded field; the recorded concept is active_skills.
"Architecture" should be the recorder's "architect" unless the backend is changed first, per the spec's own
principle 4.

**F8. Trust strip fields and their categories.** Snapshot id, consistency kind, state-store path and configuration
directory are already in M1. Model identity digests per role are in events. Qualification status lives under the
qualification home, and SEC-009 durable approval lives in the authority approval records; both need a new read
operation. Add one more field the spec lacks: evidence-store seal and verification state, which `kriya evidence verify`
already computes.

**F9. Governance facts.** Gate 05 closed the M1 discussion and requires a new topic folder for any design change. D-9
matrix protection was lifted and real-store use was released on 2026-10-05 for the documented procedure. D-10 is
superseded by the main-only branch policy. Every backend addition this spec needs (evidence read operations, phase
producers, qualification and approval reads) is a product change on the frozen executable and needs the owner's
explicit authorization as new scope, not as a defect.

## 3. UX and architecture assessment

- **Surface count versus progressive disclosure.** Seven destinations, seven center tabs, three inspector levels,
  seven drawer tabs and a Why overlay is about twenty-five surfaces. The spec never states how "Evidence tab",
  "Inspector Evidence level" and "Raw Events drawer" differ. Write the rule down: center tabs are run-scoped
  documents, the inspector is node-scoped detail of the current selection, the drawer is streams and logs. Then cut
  the drawer to what has evidence: Build, Tests, Git, Raw Events now, Model I/O when the store is exposed, LSP removed
  (no LSP evidence exists).
- **Three navigators over one hierarchy.** The phase rail, the timeline tree and the Attempts tab all navigate the
  same nodes. Make the timeline tree the only navigator, the rail a filter plus status summary, and Attempts a table
  view with compare. The shared package already has one selection reducer (run, attempt, event, tabs). Extend it
  rather than adding a router; Electron has no meaningful URL, and the test host can mirror the reducer to a hash.
- **Gate navigation on capabilities, not on a roadmap.** KUP capabilities already advertise feature flags
  (comparisons, attribution, diagnostics, output, all false today). Key each rail item and each tab to a flag. Hidden,
  not disabled: a rail with four greyed items reads as an unfinished product. v1 rail is Runs, Workspace, Settings.
- **Why panel item 2 is a causal claim.** "First incorrect or blocking state" is not a recorded fact. Rename it
  "First failed gate, in recorded order", which is. Keep causal attribution to the recorded tier, kind and confidence,
  plus the diagnosis evidence class from F1.
- **Milestones are missing from the Runs pane.** A decomposed goal produces N rows sharing milestone_group_id, plus
  enforce rows linked by enforce_run_id. Without grouping, the user sees unrelated runs. Group by milestone group
  first, then by day.
- **Privacy and size of Model I/O.** Full-capture blobs contain prompts with repository content, and the leak-check
  command exists precisely because hidden material can end up there. Exposing them needs a visibility policy, a
  per-blob size cap below the 8 MB envelope limit, and lazy loading per blob. The spec's performance section should
  name the blob operation as the only path.
- **Layout arithmetic.** At 1180 px the stated minimums (rail 56, secondary 240, main 620, inspector 300) sum to
  1216, so the inspector is collapsed by definition at the minimum width. Say so explicitly.
- **Design system.** The content security policy forbids remote content, so Inter or JetBrains Mono must be bundled.
  The mock's grey metadata on dark will not reach 4.5:1; set the metadata token at the AA threshold up front.
- **Freeze ordering.** Section 42 freezes the IA before Section 35's matrix is filled. The matrix is the
  precondition, since F1 to F3 move whole surfaces between categories.

Decisions requested of the owner at the time (all since taken, see `01_README_AND_GATE.md` §3): open a new topic
folder; authorize designing read operations over the attempt evidence store; explicit phase producers or a derived
rail; GUI-1 scope given F2/F3.

## 4. Addendum: later measurements from the same session (2026-10-10)

- **Recorder producers (MEASURED).** A per-kind count of writer call sites in production code (excluding the
  recorder's own model/explain/reader/export/leak modules) shows every declared record kind has at least one producer
  (candidate.change 5, prompt.sections 3, authority.snapshot 3, recorder.gap 3, model.request 2, model.response 2,
  gate.result 2, diagnosis 2, fallback.decision 2, retry.delta 2, regression.decision 2, developer.parse 2,
  run.closed 2, not_recorded 2; one each for run.opened, unit.opened, unit.closed, attempt.opened, attempt.closed,
  attempt.concluded, model.result, obligations.snapshot, recovery.decision, tool.execution and the four mirror kinds)
  **except `phase.opened` and `phase.closed`, which have zero.** F1 and F2 stand exactly as written.
- **Verification vocabulary (TRACED).** `reader.verify` returns one of VERIFIED, UNSEALED, NOT_FOUND, CHAIN_BROKEN,
  TRUNCATED_TAIL, BLOB_CORRUPT, SEAL_MISMATCH with record_count, sealed, broken_seq, seal and blob_problems.
- **Retention (TRACED).** Default keep_runs 200, max_bytes 5 GiB; a pruned store is renamed to a dot-prefixed name and
  removed; no tombstone is kept, so NOT_FOUND cannot distinguish "pruned" from "capture off" or "pre-recorder".
- **Canonical checkout (MEASURED).** Kriya-main-demo is `main` @ 1a89bc5, clean; the other checkout is detached at
  272ef16 with a modified CLAUDE.md. This topic uses Kriya-main-demo only.
