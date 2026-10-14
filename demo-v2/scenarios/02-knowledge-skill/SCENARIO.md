# Scenario 02 - Knowledge / skill acquisition

## ID
`02-knowledge-skill`

## TITLE
Terminal acquisition, GUI skill view, offline GUI run.

## PURPOSE
Show that Kriya can acquire missing technology knowledge through a governed, approved public lookup, keep it as a local
provenance-backed skill, make that skill visible in the GUI, and then use it for a concrete task with public lookup unavailable.

## STATUS
**STOPPED AND RETURNED TO THE OWNER (architectural stop rule 4), 2026-10-14. Nothing here is validated.** Four live attempts with py-tree-sitter 0.25 as the candidate technology established steps 1 and part of 2 only:
the skill is absent, the knowledge guard flags both libraries (HIGH, post-cutoff) once the workspace has a `requirements.txt` and the goal pins them as `name==version`, and the operator's acknowledgement is recorded in the governance log.
Every run that got that far then stopped in planning (`PLANNED_ARTIFACT_PREREQUISITE_UNDECLARED`, identical across all repair rounds), so no lookup, reference review or skill was ever produced; steps 3-10 are NOT measured.
Classification (2026-10-14, deterministic reproducer, no new model run): the planner blocker is a `PLANNER_MODEL_DEFECT`: the Planner put an installed package into a planned file's `requires_capabilities` against its own stated contract, in 6 of 6 recorded answers. Per the owner's rule no validator or prompt change was made and no technology was switched; the scenario waits for the owner.
Full evidence, the traced path and the options: `handover/DEMO-V2/FINDINGS.md` (F1, F2), test `tests/test_plan_external_package_capability_001.py`, registry row `PLAN-EXTERNAL-PACKAGE-CAPABILITY-001`.
Earlier evidence does not establish this scenario either: Artemis (demo-02) acquired documents that did not cover its task; Ignite (demo-01) used a pre-authored skill. The groundwork (goal, configuration templates, `prompt_rules.json`, `scripts/answer_prompts.py`) is kept and UNVALIDATED.

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
