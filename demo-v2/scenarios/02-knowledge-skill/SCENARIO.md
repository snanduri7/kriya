# Scenario 02 - Knowledge / skill acquisition

## ID
`02-knowledge-skill`

## TITLE
Terminal acquisition, GUI skill view, offline GUI run.

## PURPOSE
Show that Kriya can acquire missing technology knowledge through a governed, approved public lookup, keep it as a local
provenance-backed skill, make that skill visible in the GUI, and then use it for a concrete task with public lookup unavailable.

## STATUS
**PROVISIONAL. No technology is selected and nothing here is validated.** Historical evidence does not establish this scenario:
- Demo-02 (Artemis, `~/kriya-live-demo/demo-02-knowledge-acquisition/evidence/v4/LEARN-CLASSIFICATION.md`): acquisition mechanics
  succeeded (35 rules with provenance), but the offline-reuse generation FAILED because the acquired material described running a
  standalone broker, not the embedded API the goal needed. It is not a success narrative.
- Demo-01 (Ignite): a pre-authored skill made the run succeed (a skill-versus-no-skill comparison), which is not acquisition.
Neither is reused as proof. A technology must be selected and the ten-step sequence below measured end to end.

## PRECONDITIONS
- A technology chosen and qualified: a public, current documentation set whose lookup yields rules that cover what the chosen task needs.
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
