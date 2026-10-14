# Scenario 01 - Normal GUI coding flow

## ID
`01-normal-coding`

## TITLE
Fix a real bug in an existing Spring Boot application, end to end in the desktop GUI.

## PURPOSE
Show the ordinary Kriya loop in the latest GUI: goal, plan, unit, attempt, source change, compile and test gates, evidence, outcome. Brownfield:
the repository already exists (22 classes, 53 passing tests) and Kriya must touch as little of it as possible.
The bug: `DefaultDriverService.delete(Long)` sets `deleted = true` on the entity but never persists it through the repository.

## STATUS
**SPECIFIED. UNREHEARSED on this product baseline.** What has been measured on `1064b9e` (no model involved): the fixture
materialises from its pinned commit with the expected target hash, the offline Maven build compiles, and the 53 baseline tests
pass in about 13 s. Everything that needs the GUI or a model (the RUN section) has not been run in Demo V2 and is not claimed.

## PRECONDITIONS
- Product checkout contains tag `kriya-v1.2.0-pre-modularization` with a clean tracked tree; `.venv` built (`kriya` on `.venv/bin`).
- The fixture source repository is available locally (`fixtures/brownfield-spring/fixture.env`, override with `DEMO_V2_BASELINE_SRC`).
- Java 17 and an offline-populated Maven repository for the fixture (`./mvnw -o` must work; proven by `prepare_demo.sh --check-build`).
- Ollama (or another OpenAI-compatible server) reachable at the URL in the rendered configuration, serving the models it names.
- The desktop app built from this checkout. **Open precondition:** how the desktop host selects the rendered configuration
  (host setting `configDirectory`, operator-configuration layering, SEC-009 authority) is NOT yet confirmed; the rehearsal must
  record which configuration the launched run actually used (visible in the GUI trust strip and Run Setup) before this scenario can be validated.

## PRODUCT HEAD/TAG COMPATIBILITY
Written against `kriya-v1.2.0-pre-modularization` (`1064b9e`, product code `9de5404`). `verify_demo.sh` requires that tag to be an ancestor of HEAD.
Any later product change requires a re-run of this scenario before it is called compatible.

## MODEL REQUIREMENT
Live local model. Developer `qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`, embeddings `nomic-embed-text` (see the configuration template).
Model output is stochastic; a run can fail. This scenario does not claim solver reliability (`BACKEND SOLVER READINESS: FAIL`,
`SOLVER REPEATABILITY: NOT ESTABLISHED`). A failed attempt is recorded as evidence, not retried until it passes.

## NETWORK REQUIREMENT
None at run time (`web_lookup_enabled: false`, empty search URL). Local model server on localhost only. Maven is used offline.

## RESET
```bash
demo-v2/scripts/prepare_demo.sh --scenario 01 [--check-build]   # materialise workspace at the pinned commit, render config
demo-v2/scripts/verify_demo.sh  --scenario 01 --phase pre       # must report 0 failed
```
Reset is `scripts/reset_demo.sh --scenario 01` (rebuilds the workspace from the fixture; clears Kriya state, memory and evidence for the scenario).

## RUN
1. Start the desktop app; Open Folder: `$DEMO_V2_WORK/scenario-01/workspace`.
2. Confirm in Run Setup that the model routing is the one in the rendered configuration and the models are reported available.
3. Enter the contents of `goal.md` as the goal and Start Run.
4. Watch the run (timeline, plan, units, attempts). When the change review appears, read the proposed diff and approve or reject it in the GUI.
5. After the run: open Evidence; then `demo-v2/scripts/verify_demo.sh --scenario 01 --phase post`.

## EXPECTED EVENTS
Repository analysis (Maven, Spring Boot); a plan whose unit targets `DefaultDriverService`; at least one attempt with a source change;
compile and test gates; a change review waiting for a human decision; a final outcome record. The exact number of attempts is NOT fixed.

## EXPECTED EVIDENCE
A recorded run in the scenario's Kriya state directory; per-attempt records in the evidence store (verifiable from the GUI Evidence view);
the applied diff in the workspace; Maven test output. See `expected/scenario-01.md`.

## SUCCESS CRITERIA
All of: the run's outcome in the GUI is success; `verify_demo.sh --phase post` reports 0 failed, i.e. exactly one file changed (the target),
`delete(Long)` now persists through the repository, the diff is narrow, the independent offline test run passes with 0 failures,
and Kriya recorded state for the run. The change must have been approved by a human in the GUI, not auto-approved.

## FAILURE DIAGNOSIS
- Pre-flight FAIL on config validation: the template drifted from `AppConfig`; fix the template, do not loosen the check.
- Models unavailable in Run Setup: start the model server; do not edit the configuration to point elsewhere to make it green.
- Run ends without success: classify before re-running (Kriya defect, model behaviour, provider/runtime, prompt/context, environment, harness, unknown).
  Keep the failed run's evidence. Do not re-run an unchanged failed candidate hoping for variance.
- Post FAIL "Kriya recorded state": the change was not produced by a Kriya run (for example it was applied by hand).
- Post FAIL on tests: the run left a broken workspace; this is a finding, not noise.
