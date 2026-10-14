# Scenario 02 - Knowledge / skill acquisition

## ID
`02-knowledge-skill`

## TITLE
Terminal acquisition, GUI skill view, offline GUI run.

## PURPOSE
Show that Kriya can acquire missing technology knowledge through a governed, approved public lookup, keep it as a local
provenance-backed skill, make that skill visible in the GUI, and then use it for a concrete task with public lookup unavailable.

## STATUS
**STOPPED AND RETURNED TO THE OWNER at the first new blocker (2026-10-14). Steps 1-7 are measured; steps 8 and 10 are not achieved or not measured; step 9 is measured at prompt level only.** Nothing here is validated as a demo.
After the planner blocker was classified (`PLANNER_MODEL_DEFECT`, with the generic contract and repair-feedback improvement that followed: `handover/DEMO-V2/FINDINGS.md` F2) one controlled live retry (same technology, goal intent, model route; no manual plan edit) got a valid plan on its first attempt and then ran the acquisition: knowledge-risk acknowledgement and governed lookups recorded, two skills created from the official pages with `live_lookup:<url>` provenance, shown populated in the Skills page.
The Developer prompt contained the acquired rules and the generated code used exactly that API. The run then failed in generation for a different reason: the model's `outline.py` missed nested functions and the model could not repair it through the edit protocol (F10). No Kriya defect is claimed; no further runs were made.
Per step: 1 PASS; 2 PASS on earlier no-knowledge evidence; 3 PASS; 4 PASS; 5 PASS; 6 PASS; 7 PASS (screenshots); 8 NOT MEASURED; 9 prompt-level only (acquisition run); 10 NOT ACHIEVED. Evidence: `handover/DEMO-V2/evidence/scenario-02-live-retry-1/`. Options for the owner are in `FINDINGS.md` F1/F10.
UPDATE 2026-10-14 (owner: bounded task isolation): the application task was replaced by an isolated one (`goal.md`: `function_start_offsets` over UTF-16 bytes, fixture `fixtures/knowledge-skill`; candidate 1 was rejected because its no-skill baseline passed). Measured pair on that task, same model and config, only the skills directory differing: no skill = 11 failed attempts on the missing library knowledge; acquired skill copied in, public lookup disabled = passed on attempt 1 with the acquired encoding rule in the Developer prompt (`handover/DEMO-V2/FINDINGS.md` F11). The ten steps are measured across the earlier acquisition run and this pair; the single from-empty end-to-end rehearsal is pending. Run one with `scripts/scenario02_run.sh`.
The groundwork (goal, configuration templates, `prompt_rules.json`, `scripts/answer_prompts.py`, fixture with `requirements.txt`) is kept; `prepare`/`reset` for this scenario are not implemented.

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
The accepted sequence. The demonstration is accepted only if ALL TEN are measured on one recorded run:
1. skill absent (GUI Skills page and `kriya skills list` agree);
2. acquisition invoked from the existing terminal workflow;
3. governed lookup and approval occur (recorded);
4. skill created;
5. provenance present (source URLs per rule);
6. GUI Skills page shows the skill with its provenance;
7. public lookup disabled or unavailable;
8. the same or a new concrete task executed from the GUI;
9. the local skill is actually selected and used by that run (recorded in the run, not inferred);
10. the application compiles, tests and runs successfully.

## EXPECTED EVENTS
Defined per the selected technology after rehearsal.

## EXPECTED EVIDENCE
Acquisition record and provenance file; GUI Skills page listing; run evidence showing the skill in the active set; build and test output.

## SUCCESS CRITERIA
All ten steps measured. If no candidate technology can pass reliably, this scenario is returned to the owner; Kriya platform scope is not expanded to rescue it.

## FAILURE DIAGNOSIS
Classify before re-running: acquisition relevance (the retrieved docs do not cover the task), acquisition mechanics, model behaviour,
skill selection, environment. A scenario whose offline run fails because the acquired knowledge was irrelevant is a failed candidate technology, not a Kriya defect.
