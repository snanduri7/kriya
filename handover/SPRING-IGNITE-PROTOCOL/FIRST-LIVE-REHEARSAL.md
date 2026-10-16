# First live rehearsal: Spring XML + Apache Ignite 2.18.0 + Protocol (scenario 04)

Everything here is for you to run with the real local model. Nothing has been run with a model yet. One run is one observation, not a reliability claim.
**Run it once.** If it fails, capture the evidence in section 13 and stop (section 14).

## 0. Decision already taken: `knowledge.offline_mode: true` is ACCEPTED for scenario 04 only (owner, 2026-10-16)
It is not a global or default Kriya behaviour and KnowledgeGuard's product semantics are unchanged. For the record, this scenario's configuration sets `knowledge.offline_mode: true`. Reason: a desktop launch is always knowledge-policy strict and cannot acknowledge KnowledgeGuard's post-cutoff warning for "Apache Ignite 2.18.0", so without the setting the run would stop before any model call. Effects you should accept knowingly: (a) the guard does not look up release dates, so its post-cutoff warning is NOT put into the model's prompt (the verified `ignite-java17` skill supplies the Ignite rules); (b) the guard is cleared because a skill folder whose name contains `ignite` exists; it does not read the skill's verified flag (the reset's digest check guarantees the folder is the verified skill); (c) the configuration-approval dialog does NOT list this setting. Details: `QUALIFICATION.md` section 3. A follow-up (registered as `KNOWLEDGEGUARD-SKILL-IDENTITY-001`, P3, non-blocking) is to make a future skill-sufficiency check rely on authoritative verified skill identity rather than folder-name presence.

## 1. Prerequisite status (all checked without a model)
| Item | Status |
|---|---|
| Branch `demo/spring-ignite-protocol` (from the frozen `kriya-v1.2.0-demo-v2-validated`), clean tracked tree, no product change | ready |
| Desktop app `ui/standalone/out/Kriya.app` (newer than the newest UI change, so `launch_gui.sh` accepts it) | present, built Oct 15 |
| Java/Maven, offline Maven repository (Ignite 2.18.0, ignite-spring, Spring 5.3.39, JUnit 5.8.2, surefire, compiler, exec plugin) | qualified: 3/3 JUnit, app runs, exit 0, about 7 s |
| Skill `ignite-java17` (verified, 44 rules), copied into the scenario by `prepare` with its digest checked | ready |
| Ollama with `qwen3-coder:30b` + fallback `qwen3.6:35b-a3b-q4_K_M` + `nomic-embed-text` | `verify --phase pre` checks it |
| Knowledge mode | the scenario runs KnowledgeGuard in offline mode (otherwise a desktop run of this goal stops before any model call); see `QUALIFICATION.md` section 3 |
| Network | no external network or registry access is required; embedded Ignite opens local listeners and discovery while it runs (section 10) |
| Known environment fact | under the app, `java` is Temurin 21 and Maven runs on JDK 26 (not 17); the project still targets Java 17; both were qualified |

## 2. Exact launch commands
```bash
cd ~/WorkingDirectory/AI/ClaudeCode/Kriya-main-demo
git switch demo/spring-ignite-protocol && source .venv/bin/activate
# Cmd+Q every Kriya window first
demo-v2/scripts/prepare_demo.sh --scenario 04                 # empty project folder + kriya.yaml, verified skill copied; starts nothing; grants no approval
demo-v2/scripts/verify_demo.sh  --scenario 04 --phase idle    # expect: "0 failed"
demo-v2/scripts/launch_gui.sh   --scenario 04                 # starts the app; copies the folder path to your clipboard
```
(Optional, no model, about 40 s: `demo-v2/scripts/prepare_demo.sh --scenario 04 --check-build` also re-runs the environment qualification.)

## 3. The folder to choose
`~/kriya-demo-v2-work/scenario-04/workspace` — in the app: **Open Folder**, press Cmd+Shift+G, paste (already on your clipboard), Enter. It holds only `kriya.yaml` (the configuration, unapproved); no source, no `pom.xml`, no git repository.

## 4. The goal to paste (exactly this; also `pbcopy < demo-v2/scenarios/04-spring-ignite-protocol/goal.md`)
> Create a Java 17 Maven application that uses Spring XML configuration to start an embedded Apache Ignite 2.18.0 node. Define a Protocol class with protocolVersion (int), softwareVersion (int), dataLength (int), time (long) and body (byte[]), and a ProtocolParser with encode(Protocol) returning byte[] and decode(byte[]) returning Protocol, using this wire format: a 9-byte big-endian header (protocolVersion 1 byte, softwareVersion 1 byte, dataLength 3 bytes, time 4 bytes) followed by the raw body bytes. In the application, create a sample Protocol (protocolVersion 1, softwareVersion 2, time 1700000000, body "Hello, Protocol" as UTF-8), encode it, decode it, store the decoded Protocol in an Ignite cache, read it back from the cache and check that it equals the original. Print one line in the format [RESULT] protocolVersion=X, softwareVersion=Y, dataLength=Z, time=T, bodyLength=B, equals=<the real comparison result>. Include a JUnit test that proves the ProtocolParser round trip, close all Spring and Ignite resources so the application exits normally, and make the application runnable with Maven.

## 5. Expected approval sequence (nothing is bypassed)
1. **ACTION REQUIRED: Configuration approval required** on the Project page -> paste the goal -> **Review & Approve** (protected setting NAMES only) -> **Continue to Approval…** -> **native confirmation** (Cancel is the default; approve only if you trust the settings) -> **Ready**.
2. **Start Run** -> the page shows Planning, Editing, Validating. A stop at this point with *knowledge gap acknowledgement* is a defect of the rendered configuration, not of the model (section 13).
3. **ACTION REQUIRED: Kriya wants to change your project** (source review: the exact files; nothing has been built or run) -> **Approve & Continue** (a confirmation follows).
4. Kriya builds and tests, then asks you to review the **runtime command** (a separate review) -> approve -> its **native confirmation** -> the application runs.
5. **Run completed**.

## 6. Expected files at the source review
About six new files (historically three units; with the test, more). At minimum: `pom.xml`, a Spring XML under `src/main/resources`, `Protocol.java`, `ProtocolParser.java`, a main class, a JUnit test under `src/test/java`. Not expected: any `ignite/` directory, any file outside the project, a modified `kriya.yaml`.

## 7. What to inspect BEFORE approving the source
- **pom, FIRST: does it target Java 17?** (`maven.compiler.source`/`target`/`release` exactly **17**; the model is told the machine's JDK is 26, so check it did not copy 21 or 26: `java` runs Temurin 21 and would reject newer classes); `ignite-core` AND `ignite-spring` at 2.18.0; a JUnit 5 test dependency; `exec-maven-plugin` as **`exec:exec`** with `<executable>java</executable>`, many `--add-opens` (at least `java.base/java.nio` and `java.base/java.util`), `-classpath` + `<classpath/>`, and `${exec.mainClass}` as the last argument. Reject `exec:java` with `jvmArguments`.
- **Spring XML**: an `IgniteConfiguration` bean and EXACTLY ONE start mechanism: either an `IgniteSpringBean` (then the code must use `getBean("<id>")` and never `Ignition.start`) or a plain configuration with `Ignition.start("<xml>")`. Both together = a crash ("already been started").
- **Protocol / ProtocolParser**: the five fields; `equals` compares `body` by content; header arithmetic: dataLength in 3 bytes, time in 4 bytes, both big-endian; a malformed length is rejected.
- **Main**: an explicitly typed cache (`IgniteCache<Integer, Protocol>` or equivalent); `put`, then `get` into a separate variable; **the printed values must come from the object returned by `get`, not from the local object that was put** (the reference qualification proves this for itself with a cache-side witness; for the live candidate this source review is the check); the context/node is closed (try-with-resources) so the JVM can exit.
- **Test**: a real round trip with field equality; no `assertTrue(true)`; at least one `@Test`.
- **Java 17 is the one thing to say out loud at this review:** the application targets Java 17 even though this Mac's Maven host runs on a newer JDK and `java` is 21. A pom saying 21 or 26 is a finding. Qualification passed with the reference on Java 17 and in the desktop environment.
Rejecting here means no build, no test, no run, nothing applied.

## 8. Expected build / test result
Compile passes; the test gate records a **non-zero** test count shown as `N / N tests passed` (the reference has 5: 3 parser tests and 2 real embedded-Ignite round trips). "0 tests" or compile-only success is not a pass for this scenario.

## 9. Expected runtime command review
Historically `mvn -e -q compile exec:exec -Dexec.mainClass=<main class>` (the Run Verifier infers it from the pom). Check it names **`exec:exec`** and the right main class. A different command (for example `exec:java`) is a finding, not something to fix by hand.

## 10. Expected native confirmation
A separate native sheet for the runtime command (Cancel is the default). It is a different decision from the source approval.
**What running it means:** the application runs on your machine (no container). Ignite's defaults open listeners on all interfaces (TCP 47100, 47500, 10800, 11211, UDP 47400) and use multicast discovery, so another Ignite node on the same network could join it while it runs (a few seconds). Maven is not forced offline: before you start, `touch /tmp/kriya-04-start`; afterwards `find ~/.m2/repository -newer /tmp/kriya-04-start -type f | head` shows anything it downloaded (an expected result is none; any file is a deviation from "no public network" to record).

## 11. Expected runtime evidence and final screen
- Application section: the recorded command, **exit code 0**, and the program's output. Ignite prints a long banner, so the result line is far below the first 15 lines: use **Show full output**.
- The output contains `Ignite node started OK` ... `Ignite node stopped OK` and exactly: `[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true`. (`[VERIFICATION] PASS` appears only if the generated code prints it; the goal does not ask for it.)
- Final screen: **Run completed**; Build, Tests, Runtime verification passed; the formal requirement ledger may still say "not fully verified" (by design, the output line is evidence, not a closed requirement).
- After the run, with no model: `demo-v2/scripts/verify_demo.sh --scenario 04 --phase post`. It works on a throw-away COPY of the project (your evidence is untouched) and checks: no `ignite/` left in the project, offline `mvn test` with at least one test executed and none skipped, the application through Maven under the app's PATH exits 0, the exact expected line, Ignite's own start and stop lines, and a `Protocol` class in Ignite's marshaller registry. Expect "0 failed". It cannot tell whether the printed values were read back from the cache (that is your source review) and it hard-codes the `exec:exec` launch.
- Roughly: the historical Ignite run took 356 s for three units without tests; expect minutes, not seconds, and possibly more attempts.

## 12. If a failure occurs: classify the FIRST wrong decision, not the final symptom
Use the table in `QUALIFICATION.md` section 9: KnowledgeGuard stop (control plane), skill not loaded (control plane), skill rule present but ignored (`CKM_TO_LLM_TRANSLATION_GAP`), rule missing (`SOURCE_DOCUMENT_KNOWLEDGE_MISSING`), wrong runtime command or process never exits (`MODEL_TOOLING_EXECUTION_GAP`), uncached Maven artifact (`RUNTIME_ENV_GAP`), review/approval/ledger defect (`KRIYA_CONTROL_PLANE_GAP`), or Kriya correct and the candidate wrong (`MODEL_GENERATION_FAILURE`).

## 13. What to capture if it fails (copy these; change nothing)
- A screenshot of the final screen and of the screen where it stopped.
- The folder `~/kriya-demo-v2-work/scenario-04/` as it is: `workspace/` (whatever was generated), `state/` (Kriya's run records), `gui/app.stderr.log`, `gui/app.stdout.log`.
- The run id shown on the page, and for a stop at the start: the page's *Technical reason / Details* text.
- Optionally (no model; it runs the generated code, but only in a copy): `demo-v2/scripts/verify_demo.sh --scenario 04 --phase post > ~/kriya-demo-v2-work/scenario-04/evidence/post-check.txt 2>&1`.
- Which step you were at (sections 5 to 11) and what you clicked last.

## 14. Do NOT retry repeatedly
Run once. After a failure do not click Start Run again, do not edit the generated files, do not run `prepare` (it erases the evidence above) and do not change the configuration. Hand the captured material back for classification; a rerun happens only after a Kriya defect is fixed or you explicitly ask for a variance study.

## To return to a clean state afterwards
`Cmd+Q`, then `demo-v2/scripts/prepare_demo.sh --scenario 04` (erases the run and revokes the approval). Only after you have saved what you need.
