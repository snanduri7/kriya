# Scenario 03 - Controlled repair

## ID
`03-controlled-repair`

## TITLE
CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO: a wrong candidate is caught by Kriya's deterministic validation and repaired.

## PURPOSE
Show the engineering control system around an imperfect model: the model proposes a plausible but wrong candidate,
Kriya's deterministic validation rejects it with explainable evidence, a permitted next attempt supplies a correct candidate,
and compile, tests and contracts pass. Story: "Local models are imperfect; Kriya is the engineering control system around them."

## STATUS
**DESIGN. A feasibility probe has not been run; nothing here is built or validated.**

> **LABEL (mandatory in the GUI notes, presenter notes and any recording): this is a CONTROLLED / SCRIPTED scenario.**
> The model responses come from a deterministic stub that replays a fixed script. It is NOT live-model behaviour and
> must never be presented as evidence of how a real model behaves or fails.

## PRECONDITIONS
- Feasibility probe passed: a non-production configuration pointing the OpenAI-compatible endpoint at a localhost stub is accepted by
  the existing provider and configuration paths WITHOUT production-code special cases, model-qualification forgery or runtime-identity faking.
  If the probe shows the stub needs any of those, the scenario STOPS and goes back to the owner (architectural stop rule 5).
- A small separate fixture (not the historical qualification task bank), chosen so that the wrong candidate is rejected by a deterministic check (compile, a failing test or a contract).
- The stub answers every role Kriya calls (not only the Developer) in the response protocol the product requires.

## PRODUCT HEAD/TAG COMPATIBILITY
Not yet determined.

## MODEL REQUIREMENT
None. A scripted stub replaces the model. Optional later: one live-model variant, clearly separate and not presented as reproducible.

## NETWORK REQUIREMENT
None. The stub listens on localhost only.

## RESET
To be defined with the fixture: restores the fixture workspace and Kriya state, and restarts the stub at script position 0.

## RUN
1. Start the stub; confirm Run Setup shows the stub endpoint (so the audience can see the model is scripted).
2. Start the run from the GUI with the scenario goal.
3. Attempt 1 receives the scripted wrong candidate; observe the deterministic rejection and its expected/observed evidence in the GUI.
4. Attempt 2 receives the scripted correct candidate; observe compile, tests and contracts pass.
5. Approve the change in the GUI.

## EXPECTED EVENTS
Attempt 1 produced; deterministic rejection with a reason code; attempt 2 produced; gates pass; change review; outcome success.

## EXPECTED EVIDENCE
Per-attempt records showing the rejected candidate and the rule that rejected it, the repaired candidate, and gate results.

## SUCCESS CRITERIA
The rejection and the repair both occurred through Kriya's own validation and retry path (not by the stub deciding), and the final workspace passes the fixture's checks.

## FAILURE DIAGNOSIS
- Stub not hit: the configuration is not the one the run used.
- Rejection did not happen: the chosen wrong candidate is not actually caught by a deterministic check; choose a different defect, do not weaken the check.
- Any need to special-case the stub in product code: stop and report.
