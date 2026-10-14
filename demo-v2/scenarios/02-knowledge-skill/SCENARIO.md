# Scenario 02 - Knowledge / skill acquisition

## ID
`02-knowledge-skill`

## TITLE
Terminal acquisition, GUI skill view, offline task run (terminal).

## PURPOSE
Show that Kriya can acquire missing technology knowledge through a governed, approved public lookup, keep it as a local
provenance-backed skill, make that skill visible in the GUI, and then use it for a concrete task with public lookup disabled.

## STATUS
**REHEARSED ONCE FROM EMPTY STATE, ISOLATED-TASK REHEARSAL PASS, WITH CAVEATS (2026-10-14, product `demo/v2`).** One pass of the owner's ten steps (`handover/DEMO-V2/FINDINGS.md` F11, F12; pre-registration `handover/DEMO-V2/SCENARIO-B-ISOLATION-PREREG.md`):
empty skills -> governed acquisition (terminal) -> populated Skills page -> lookup disabled -> offline run of the isolated task -> tests pass. The task is `function_start_offsets` over UTF-16 bytes (`goal.md`, fixture `fixtures/knowledge-skill`); the first candidate was rejected because its no-skill baseline passed completely, and the earlier complex task is `SCENARIO-B-COMPLEX-TASK-NOT-DEMO-SUITABLE`.
Measured: no skill, same model and config = 11 failed attempts on the missing library knowledge; fresh acquired skills copied in, lookup off = `Quality Gates: PASSED` (attempt 4, pytest 8/8) with the acquired construction facts in the Developer prompt. An earlier pair with a different acquisition (which included the encoding rule) passed on attempt 1.
Caveats, stated plainly: (1) acquired content varies per acquisition: the fresh skills lack the encoding rule that the earlier acquisition retrieved, and the passing offline run solved that part by decoding, not by the encoding parameter; (2) one stochastic sample per arm, an observed demonstration, not a reliability claim and not "the skill caused success"; (3) the offline run is a TERMINAL run (the desktop declines knowledge decisions by design); the GUI is used for the populated Skills page; (4) "public lookup disabled" means no knowledge lookup: KnowledgeGuard's PyPI release-date metadata requests and Kriya's dependency-acquisition step still appear in the log.
Evidence: `handover/DEMO-V2/evidence/scenario-02-final-rehearsal/`, `scenario-02-isolated-task/`, `scenario-02-live-retry-1/`. Scripts: `scripts/scenario02_run.sh` (noskill | skill | acquire), `scripts/capture_skills_page.sh`.

## PRECONDITIONS
- A technology chosen and qualified (NONE is, see STATUS): a public, current documentation set whose lookup yields rules that cover what the chosen task needs.
  Selection criterion: a rehearsal of steps 1-10 below passes; "the page is reachable" is not enough.
- Local SearXNG (or the configured search endpoint) reachable for the acquisition step only; model server reachable.
- The GUI does NOT approve lookups or knowledge-gap acknowledgements (a desktop launch declines them by design, `kriya/control/launch.py`).
  Acquisition is therefore a terminal step. Building a GUI approval path is out of scope for Demo V2.

## PRODUCT HEAD/TAG COMPATIBILITY
Not yet determined. Will be written against the tag it is rehearsed on.

## MODEL REQUIREMENT
Live local model for acquisition-time generation and for the offline run. Stochastic; may fail; failures are recorded, not retried to green.

## NETWORK REQUIREMENT
Acquisition step: outbound public lookup, approved interactively in the terminal. Offline-use step: public lookup disabled or unreachable.

## RESET
To be defined with the selected technology. Requirement: removes the acquired skill and its provenance, restores an empty workspace,
and leaves a record that the skill was absent before acquisition (step 1).

## RUN
The accepted sequence. Accepted only if ALL TEN are measured:
1. KNOWLEDGE ABSENT: the skills directory is empty (`scenario02_run.sh --mode acquire` records its digest, the empty-set hash).
2. SAME-TASK NO-SKILL RUN demonstrates a relevant knowledge failure (`--mode noskill`, same goal, model and config, lookup off).
3. POST-CUTOFF / GAP DETECTION: KnowledgeGuard flags the pinned libraries (recorded in the session log and the governance log).
4. GOVERNED ACQUISITION from the terminal: every prompt and answer recorded in `governance.jsonl` (allowlist driver).
5. ACQUISITION COMPLETES: skills created.
6. PROVENANCE-BACKED SKILL STORED (`rule_provenance.json`, `live_lookup:<url>`).
7. POPULATED SKILLS GUI shows them (`scripts/capture_skills_page.sh --run-id <acquire run>`).
8. PUBLIC LOOKUP DISABLED for the reuse run (`--mode skill`: `web_lookup_enabled: false`, no search endpoint, no search container, zero search prompts).
9. SAME TASK / SAME MODEL receives and uses the acquired knowledge: the recorded Developer request text contains the acquired rules (not inferred from the skills directory).
10. APPLICATION TESTS PASS (Kriya gates and an independent pytest run).

## EXPECTED EVENTS
Defined per the selected technology after rehearsal.

## EXPECTED EVIDENCE
Acquisition record and provenance file; GUI Skills page listing; run evidence showing the skill in the active set; build and test output.

## SUCCESS CRITERIA
All ten steps measured. If no candidate technology can pass reliably, this scenario is returned to the owner; Kriya platform scope is not expanded to rescue it.

## FAILURE DIAGNOSIS
Classify before re-running: acquisition relevance (the retrieved docs do not cover the task), acquisition mechanics, model behaviour,
skill selection, environment. A scenario whose offline run fails because the acquired knowledge was irrelevant is a failed candidate technology, not a Kriya defect.
