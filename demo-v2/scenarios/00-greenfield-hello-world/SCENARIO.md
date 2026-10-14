# Scenario 00 - Greenfield Hello World

## ID
`00-greenfield-hello-world`

## TITLE
Can I simply use Kriya? New Project, setup, goal, a working Java application, its tests and what it prints.

## PURPOSE
The product experience from nothing: create a project, let Kriya recommend its setup, approve it, state a goal, watch the run, review the exact change, and read what was built and
whether it works. The goal is deliberately small (`goal.md` is one sentence): this scenario is about the EXPERIENCE, not solver difficulty. The folder starts genuinely empty: no
source, no `kriya.yaml`, no previous run, no approval, no evidence.

## STATUS
**NOT YET REHEARSED.** The GUI journey (New Project, Project Setup, configuration approval with its native confirmation, goal, run states, the change review with the exact diff, the
result screens) is proven with a CONTROLLED workflow body in `handover/DEMO-V2/evidence/product-ux-completion/` and by tests; **no live model has run this goal**, so nothing about
what the model produces, whether the offline Maven repository suffices, or whether Kriya executes the application (see EXPECTED EVIDENCE, "Application") has been measured. The owner is
the next person to exercise it. A live run of a stochastic model is one observation, not a reliability claim (`BACKEND SOLVER READINESS: FAIL`, `SOLVER REPEATABILITY: NOT ESTABLISHED`).

## PRECONDITIONS
- Product checkout contains tag `kriya-v1.2.0-pre-modularization` with a clean tracked tree; `.venv` built; the desktop app built from this checkout (`cd ui/standalone && npm run package:mac`).
- Ollama (or another OpenAI-compatible server) serving `qwen3-coder:30b` (and the fallback `qwen3.6:35b-a3b-q4_K_M`) at `http://localhost:11434/v1`.
- **Java 17+ and Maven on the desktop app's fixed PATH** (the app's child process has no login shell): `scripts/greenfield_prereqs.sh` checks this. **Unknown until the run:** whether the local Maven repository holds the plugin
  and test-library versions the model puts in its `pom.xml`; an offline build can fail for that reason, which would be an environment finding, not a Kriya defect.
- Start the app with `scripts/launch_gui.sh --scenario 00` ONLY (an app opened any other way reads the wrong state directory and refuses the run with `STATE_DIRECTORY_NOT_OBSERVABLE`).

## PRODUCT HEAD/TAG COMPATIBILITY
Written against the product after the PRODUCT-UX-COMPLETION work (`handover/DEMO-V2/PRODUCT-UX-COMPLETION.md`); `verify_demo.sh` requires the baseline tag to be an ancestor of HEAD.
Any later product change requires a re-run before this is called compatible.

## MODEL REQUIREMENT
Live local model (`qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`). Stochastic: a run may fail or need more attempts; a failed run is a result to keep, not a reason to rerun until it passes.

## NETWORK REQUIREMENT
None for Kriya (live web lookup is off in the recommended profile; the model server is local). Maven runs offline against the local repository.

## RESET
```bash
demo-v2/scripts/prepare_demo.sh --scenario 00      # resets to a brand-new EMPTY folder; places NOTHING in it; starts nothing; grants no approval
demo-v2/scripts/prepare_demo.sh --scenario 00 --check-build   # additionally checks Java 17+ and Maven (no model)
demo-v2/scripts/verify_demo.sh  --scenario 00 --phase idle    # the start state: must end "0 failed"
```
Reset is idempotent: running it twice leaves the same state (an empty folder, no Kriya state, approval revoked).

## RUN
```bash
demo-v2/scripts/launch_gui.sh --scenario 00
pbcopy < demo-v2/scenarios/00-greenfield-hello-world/goal.md
```
In the app: **Open Folder** (the path is on your clipboard; Cmd+Shift+G in the dialog) -> the Project page shows **Set up this project (Recommended)** -> **Create Project Configuration** ->
**ACTION REQUIRED: Configuration approval required** -> **Review & Approve** -> **Continue to Approval...** -> the native confirmation -> **Ready** -> paste the goal -> **Start Run** ->
Planning, Editing, Validating -> **ACTION REQUIRED: Kriya wants to change your project** (the exact files, read them) -> **Approve & Continue** -> **Run completed**.
(The project already runs on Kriya's defaults before setup: you may paste the goal and start at once; setup applies the recommended local-model profile.)

## EXPECTED EVENTS
Project Setup creates `kriya.yaml` (protected settings, unapproved); the approval is recorded outside the folder; one run: plan, one unit, edit (`pom.xml`, an application class, a unit test),
Kriya's build and test gates, the human change review, final verification, `COMMITTED`.

## EXPECTED EVIDENCE
See `expected/scenario-00.md`. In short: the run history card titled with the goal you typed, a change of about three new files shown as a diff before you approved, Build/Tests/Requested behavior
verified, and - ONLY if Kriya's own Runtime Verification gate executed the application - an Application section with the recorded command and the program's output. If that gate did not run, there is
no Application section and no start command: Kriya has no authoritative "how to start it" and the UI does not guess one.

## SUCCESS CRITERIA
Outcome `success`; `pom.xml`, one class with `main()` and one unit test exist; `verify_demo.sh --scenario 00 --phase post`: independent offline `mvn test` passes and the compiled class prints `Hello from Kriya`.

## FAILURE DIAGNOSIS
A failed or stopped run shows what happened and how far it got on the page; technical reasons are under Technical reason / Details. An offline Maven failure (missing plugin or test library) is an environment
finding: `handover/DEMO-V2/FINDINGS.md`. Do not rerun to a pass; keep the evidence.
