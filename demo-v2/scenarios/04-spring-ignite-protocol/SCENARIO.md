# Scenario 04 - Spring XML + Apache Ignite + Protocol (greenfield, specialised knowledge)

## ID
`04-spring-ignite-protocol`

## TITLE
Greenfield Java 17 / Maven / Spring XML / Apache Ignite 2.18.0 application with a Protocol and a ProtocolParser, a JUnit test, an embedded Ignite cache round trip and a clean exit - through the desktop app, with the verified `ignite-java17` skill as the knowledge.

## PURPOSE
Show Kriya building, from an empty project, an application that needs specialised, version-sensitive knowledge it already holds as a verified skill: an embedded Apache Ignite 2.18.0 node started from Spring XML on Java 17 (module flags, one-mechanism startup, resource closing), a `Protocol`/`ProtocolParser` pair, a real round-trip test, and a runtime run whose own output proves the Protocol went into an Ignite cache and came back equal. Every proof is a recorded Kriya gate or the program's own captured output, never model prose. The scenario restores, through the current GUI and the validated Demo V2 authority and verification path, the strongest historical specialised-knowledge demonstration (the frozen "KNOW B" Ignite run, `ad5598d`, 356 s), now with a Protocol and a test.

## STATUS
**MODEL-FREE QUALIFICATION PASS (read-back causally proven for the reference); owner accepted `knowledge.offline_mode: true` for this scenario only (2026-10-16); NOT YET REHEARSED WITH A LIVE MODEL.** The environment, the knowledge and the success proof are qualified without any model (`scripts/qualify_spring_ignite.sh`, evidence `handover/SPRING-IGNITE-PROTOCOL/evidence/`): the reference candidate builds and passes 5/5 JUnit tests offline (including two real embedded-Ignite round trips, one against a cache that shifts the stored value so only a genuine read-back passes), and runs through Kriya's own runtime-verification path under the desktop app's environment (Temurin 21 / Maven on JDK 26) and under Java 17: Spring XML loads, Ignite 2.18.0 starts, `ProtocolParser` runs, a `Protocol` is put into and read back from an Ignite cache, the exact expected `[RESULT]` line is printed, Ignite stops, exit 0, about 7 s. Nothing about what the live model produces, whether the Run Verifier picks the `exec:exec` command, or how many attempts it needs has been measured: **a live run of a stochastic model is one observation, not a reliability claim** (`BACKEND SOLVER READINESS: FAIL`, `SOLVER REPEATABILITY: NOT ESTABLISHED`). The owner's first rehearsal is the next step: `handover/SPRING-IGNITE-PROTOCOL/FIRST-LIVE-REHEARSAL.md`.

## PRECONDITIONS
- This checkout on branch `demo/spring-ignite-protocol` with a clean tracked tree, `.venv` built, the desktop app built from this checkout (`cd ui/standalone && npm run package:mac`).
- Ollama serving `qwen3-coder:30b` (and the fallback `qwen3.6:35b-a3b-q4_K_M`, `nomic-embed-text`) at `http://localhost:11434/v1`.
- Java 17+ and Maven on the desktop app's fixed PATH, and the OFFLINE Maven repository holding Ignite 2.18.0, ignite-spring 2.18.0, Spring 5.3.39 and a JUnit 5 stack: `scripts/qualify_spring_ignite.sh` checks the exact artifacts. **Measured:** the app's PATH resolves `java` to Temurin 21 and runs Maven itself on Homebrew JDK 26, not Java 17; the project targets Java 17 through its compiler settings, and Ignite 2.18.0 was qualified under both Java 17 and the app's environment.
- **Knowledge mode is part of the configuration, on purpose.** A desktop launch is always knowledge-policy strict and cannot acknowledge a KnowledgeGuard gap. The goal names Apache Ignite 2.18.0 (post-cutoff), so the default guard would stop the run before any model call (the online result is simulated with a seeded release date, `knowledge-guard.json`; no registry was queried). The scenario sets `knowledge.offline_mode: true` (no public release-date lookup; also what "no public network" means here). With the skill folder present the guard then reports no gap (it matches the folder NAME, not the skill's verified flag); without it the guard still does (measured). The setting is not shown in the configuration-approval dialog, so the owner accepts it explicitly (`FIRST-LIVE-REHEARSAL.md`, section 0). The guard's post-cutoff warning is not injected into the Developer prompt; the skill supplies the Ignite rules.
- Start the app with `scripts/launch_gui.sh --scenario 04` ONLY (any other launch reads the wrong state directory and refuses the run with `STATE_DIRECTORY_NOT_OBSERVABLE`).

## PRODUCT HEAD/TAG COMPATIBILITY
Written against the frozen Demo V2 product (`kriya-v1.2.0-demo-v2-validated`, `f731c26`) with NO product-code change: this branch adds scenario assets, a fixture, scripts, tests and handover documents only. Any later product change requires the qualification to be re-run.

## MODEL REQUIREMENT
Live local model (`qwen3-coder:30b`, fallback `qwen3.6:35b-a3b-q4_K_M`). Stochastic: a run may fail or need more attempts; a failed run is a result to keep and classify (`FIRST-LIVE-REHEARSAL.md`), never a reason to rerun until it passes.

## NETWORK REQUIREMENT
No external network or registry access is required. Embedded Ignite uses local listeners and discovery while the application runs (host mode: wildcard TCP 47100, 47500, 10800, 11211 and UDP multicast 47400; measured). Public lookup is off, no search endpoint, knowledge offline mode on; the model server is local. Maven runs through Kriya's host path WITHOUT `-o`, but the reference's dependency set is fully cached: measured to build, test and run with every remote repository unreachable. A model that chooses a dependency or plugin version that is not cached would make Maven try Maven Central (classify as `RUNTIME_ENV_GAP`).

## RESET
```bash
demo-v2/scripts/prepare_demo.sh --scenario 04                 # resets to an EMPTY project folder holding only kriya.yaml; copies the verified skill (digest checked); starts nothing; grants no approval
demo-v2/scripts/prepare_demo.sh --scenario 04 --check-build   # additionally runs the model-free qualification (offline Maven, JUnit, Spring XML, Ignite, Protocol, cache, exit)
demo-v2/scripts/verify_demo.sh  --scenario 04 --phase idle    # the start state: must end "0 failed"
```
Reset is idempotent. The reference candidate (`fixtures/spring-ignite-protocol/reference`) is NEVER copied into the workspace: the application source is generated at run time.

## RUN
```bash
demo-v2/scripts/launch_gui.sh --scenario 04
pbcopy < demo-v2/scenarios/04-spring-ignite-protocol/goal.md
```
In the app: **Open Folder** (the path is on your clipboard; Cmd+Shift+G in the dialog) -> **ACTION REQUIRED: Configuration approval required** -> paste the goal -> **Review & Approve** -> **Continue to Approval...** -> the native confirmation -> **Ready** -> **Start Run** -> Planning, Editing, Validating -> **ACTION REQUIRED: Kriya wants to change your project** (read the exact files) -> **Approve & Continue** -> build and tests -> the **runtime command review** -> its native confirmation -> the application runs -> **Run completed**. Full owner script with what to inspect at each step: `handover/SPRING-IGNITE-PROTOCOL/FIRST-LIVE-REHEARSAL.md`.

## EXPECTED EVENTS
Configuration approval (outside the project); one run: plan (a multi-unit decomposition is typical; historically three units), Developer output reviewed at the source review, Kriya's compile and test gates (the recorded test count is non-zero), the Run Verifier's runtime command (historically `mvn -e -q compile exec:exec -Dexec.mainClass=<main class>`), the runtime-command review and its separate native confirmation, execution, `COMMITTED`.

## EXPECTED EVIDENCE
See `expected/scenario-04.md`. In short: a change of about 6 new files shown as a diff before approval (`pom.xml`, a Spring XML, `Protocol.java`, `ProtocolParser.java`, a main class, a JUnit test); Build and Tests verified with `N / N` tests, N at least 1; an Application section with the recorded command, exit code 0 and the program's output, containing the line `[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true` (the sample values are fixed by the goal; the line is application-owned, not parsed from Maven noise). The output is long (Ignite's banner): the line sits well below the first 15 lines the result screen shows, behind "Show full output".

## SUCCESS CRITERIA
Outcome `success`; `pom.xml`, a Spring XML under `src/main/resources`, `Protocol.java`, `ProtocolParser.java`, a main class and at least one test exist; `verify_demo.sh --scenario 04 --phase post`: an independent offline `mvn test` passes with at least one executed test, and the application, run through `mvn -o compile exec:exec` under the desktop app's PATH, exits 0 and prints exactly the expected `[RESULT]` line. The structural contract of what MUST be true (not byte-identical output) is in `handover/SPRING-IGNITE-PROTOCOL/QUALIFICATION.md`, section 5.

## FAILURE DIAGNOSIS
Classify before anything is rerun, naming the FIRST wrong decision (`FIRST-LIVE-REHEARSAL.md`, section 11): `SOURCE_DOCUMENT_KNOWLEDGE_MISSING`, `PACK_SYNTHESIS_MISSING`, `CKM_TO_LLM_TRANSLATION_GAP`, `MODEL_TOOLING_EXECUTION_GAP`, `RUNTIME_ENV_GAP`, `KRIYA_CONTROL_PLANE_GAP`, or `MODEL_GENERATION_FAILURE` (Kriya behaved correctly, the model's candidate was wrong). A stop at the KnowledgeGuard stage with `knowledge_gap_acknowledgement` means the scenario configuration is not the rendered one (`KRIYA_CONTROL_PLANE_GAP` until shown otherwise). An offline-Maven failure naming a missing artifact is an environment finding. Do not rerun to a pass; keep the evidence.
