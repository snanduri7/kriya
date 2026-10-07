# GUI design discussion — process

**Topic:** a GUI for Kriya.
**Baseline:** branch `codex/fix-demo1-attribution` @ `61a867fc31a7b5ff5d7f21d1ea15e4eac43f2e03` (= GitHub `main`).
**Owner / arbiter:** Sriram.
**Proposer:** _(CHAT-GPT)_.
**Reviewer:** _(CLAUDE)_.

Read this file first. Then read the latest file in the sequence below.

## Sequence (fixed: two reviews, then a gate)

| # | File | Author | Purpose |
|---|---|---|---|
| 1 | `01_PROPOSAL.md` | Proposer | The design. |
| 2 | `02_REVIEW_1.md` | Reviewer | A full review of the proposal. |
| 2a | `02a_OWNER_REQUIREMENTS.md` | Owner (optional) | Owner input that changes scope. It is not a review. v2 must answer it. |
| 3 | `03_PROPOSAL_v2.md` | Proposer | The revised design. It starts with a response table: each finding from review 1 marked ACCEPTED, REJECTED (with a reason) or DEFERRED. |
| 4 | `04_REVIEW_2.md` | Reviewer | The final review (see the review 2 rules). |
| 5 | `05_GATE.md` | Owner | The decision. |
| 6 | `06_IMPLEMENTATION_INSTRUCTIONS_M1.md` | Owner (drafted by Claude) | Instructions for the implementing agent, written after the gate. |

### What `01_PROPOSAL.md` covers

1. The problem and who uses the GUI.
2. Scope, and what is out of scope.
3. The architecture, and how the GUI connects to Kriya. It must not add a new path to the model, an edit path or any change of permissions. Any action goes through the existing `kriya` commands and their checks.
4. Data sources: which existing traces and evidence it reads, and their formats.
5. Screens and user flows.
6. Technology choice, with the alternatives considered.
7. Security and safety: local only, what is read and what is written.
8. A test plan and acceptance criteria.
9. Milestones, where the first is a read-only run viewer.
10. Open questions for the owner.

## Rules

1. **Number every finding and claim.**
   - Reviewer findings are `R1-1`, `R1-2` … in review 1 and `R2-1` … in review 2.
   - Proposer claims are `P-1`, `P-2` ….
2. **Give every finding a severity:**
   - **BLOCKER:** the design is unsafe or wrong as written.
   - **MAJOR:** the design must change before building starts.
   - **MINOR:** an improvement, optional.
3. **Tag the evidence behind every claim:**
   - **MEASURED:** run or counted.
   - **TRACED:** read in the code at the baseline commit, with a `file:line`.
   - **INFERRED:** a judgement, which must name the check that would settle it.
4. **Review 2 rules, which keep this from looping:**
   - Review 2 checks only the review-1 findings and what changed in v2.
   - It may raise a new issue only if it is a BLOCKER and is either caused by a v2 change or backed by MEASURED or TRACED evidence.
   - Every finding gets one verdict: RESOLVED, ACCEPTED-AS-DEFERRED or UNRESOLVED.
5. **There is no third review.** After `04_REVIEW_2.md`, everything still disputed goes to the owner at the gate.
6. **During the discussion:**
   - No code changes, and no files outside this folder.
   - No live model runs.
   - **Never push to GitHub.**

## The gate (`05_GATE.md`, written by the owner)

The owner records one decision:
- **APPROVED:** build milestone 1 as described in v2.
- **APPROVED WITH CONDITIONS:** the conditions are listed and are binding.
- **REJECTED:** with a reason. A new attempt starts as a new topic folder.

The owner also rules on every UNRESOLVED item. After the gate, this folder is closed. Later changes start a new topic folder; this one is not reopened.
