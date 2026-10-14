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
**REHEARSED ONCE, PASS (2026-10-14, product `e82bb11` on `demo/v2`, tag baseline `kriya-v1.2.0-pre-modularization`).** One live run through the packaged desktop app with the real
`qwen3-coder:30b`: Open Folder 152 ms, Start Run acknowledged 102 ms, the run reached the human change review, the review was approved in the GUI (51 ms), Kriya resumed and
the run ended `success`, elapsed 2:06 (1 unit, 1 attempt, 6 model requests, 11 gate results, 80 evidence records, commit COMMITTED). `verify_demo.sh --phase post`: 0 failed (exactly one
file changed, one line added, independent offline tests 53/0, Kriya state recorded). Evidence: `handover/DEMO-V2/evidence/scenario-01-rehearsal-1/` (results.json, screenshots).
A second, independent run from a clean reset on the final Demo V2 HEAD (`20dde1d`, work area created fresh, operator approval re-granted, 205 s) also passed every check (`handover/DEMO-V2/evidence/scenario-01-rehearsal-2-clean-reset/`).
**Two runs of a stochastic model are not a reliability claim** (`BACKEND SOLVER READINESS: FAIL`, `SOLVER REPEATABILITY: NOT ESTABLISHED`). Also measured with no model: the fixture materialises from its pinned commit with the expected target hash, and the offline build passes 53 tests in about 13 s.

## PRECONDITIONS
- Product checkout contains tag `kriya-v1.2.0-pre-modularization` with a clean tracked tree; `.venv` built (`kriya` on `.venv/bin`).
- The fixture source repository is available locally (`fixtures/brownfield-spring/fixture.env`, override with `DEMO_V2_BASELINE_SRC`).
- Java 17 and an offline-populated Maven repository for the fixture (`./mvnw -o` must work; proven by `prepare_demo.sh --check-build`).
- Ollama (or another OpenAI-compatible server) reachable at the URL in the rendered configuration, serving the models it names.
- The desktop app built from this checkout (`cd ui/standalone && npm run package:mac`), including the Skills and Tools pages.
- **Configuration route (measured, 2026-10-14).** A GUI-launched run's working directory is the workspace, and Kriya discovers `kriya.yaml` there; the host passes no config path.
  `prepare_demo.sh` therefore places the rendered configuration at `<workspace>/kriya.yaml` (excluded from `git status` through `.git/info/exclude`). Kriya treats a
  workspace-supplied configuration as untrusted: **10 security-authority fields** (`autonomy.mode`, `embedding.base_url`, `llm.base_url`, `llm_chain`, `paths.memory`, `paths.skills`,
  `paths.state`, `search.base_url`, `workflow_controller.enabled`, `workflow_controller.mode`) are refused until an operator approves them. Approval is a deliberate human
  step (RESET step 2), stored outside the workspace under the operator's HOME (`~/.kriya/authority/<workspace id>.json`), and revoked by the next reset.
- **Confirmed by the rehearsal:** the desktop run used this configuration (the readiness details reported "Operator-only fields are present and covered by a durable approval · kriya.yaml in this folder"), and the run's records landed in the scenario's state directory.
- The GUI child's fixed environment has no `JAVA_HOME`, so the macOS `java` shim selects JDK 21 on this machine; measured with no model, the fixture builds and passes 53/53 under that environment.

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
demo-v2/scripts/prepare_demo.sh --scenario 01 [--check-build]   # 1. materialise the workspace at the pinned commit, revoke any earlier approval, render and place the configuration
# 2. OPERATOR STEP, in the app: Open Folder -> Review & Approve -> read the listed settings (names only) -> Approve Configuration. Or in a terminal:
#    ( cd "$DEMO_V2_WORK/scenario-01/workspace" && kriya authority approve )   (stored outside the workspace; the same approval either way)
demo-v2/scripts/verify_demo.sh  --scenario 01 --phase pre       # 3. must report 0 failed once the configuration is approved
```
`scripts/reset_demo.sh --scenario 01` rebuilds the workspace from the fixture, revokes the operator approval for it, and clears Kriya state, memory and evidence for the scenario.
`--check-build` additionally runs the fixture's own offline compile and tests (no model): measured on `1064b9e`, 53 passed in about 13 s.

## RUN
Automated rehearsal of steps 1-5 (LIVE, real model): `demo-v2/scripts/rehearse_gui.sh --scenario 01` (needs `cd ui/standalone && npm run package:mac:evidence`). By hand:

1. Start the desktop app; Open Folder: `$DEMO_V2_WORK/scenario-01/workspace`.
2. The Project page opens by itself. Its line under the goal box names the model and says Ready; open Project details > Model routing to confirm the routing is the one in the rendered configuration and the models are reported available.
3. Enter the contents of `goal.md` as the goal and press Start Run.
4. The page becomes the run: it names the phase (Planning, Editing, Validating) from Kriya's records. When ACTION REQUIRED appears, read what it shows (the file, `+1 -0`, the reviewer text and the validation; the diff TEXT is hidden by Kriya's default content-visibility policy, `evidence.attempt_recorder.content_visibility`, measured in both rehearsals) and press Approve & Continue (or Reject) in the GUI.
5. After the run the page says Run completed with the changed file and the recorded validation; View Evidence opens the evidence; then `demo-v2/scripts/verify_demo.sh --scenario 01 --phase post`.

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
- Models unavailable in Project details: start the model server; do not edit the configuration to point elsewhere to make it green.
- Run ends without success: classify before re-running (Kriya defect, model behaviour, provider/runtime, prompt/context, environment, harness, unknown).
  Keep the failed run's evidence. Do not re-run an unchanged failed candidate hoping for variance.
- Post FAIL "Kriya recorded state": the change was not produced by a Kriya run (for example it was applied by hand).
- Post FAIL on tests: the run left a broken workspace; this is a finding, not noise.
