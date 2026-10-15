# Spring XML + Apache Ignite + protocol class: Greenfield GUI demo plan

Analysis and design only (2026-10-15). No live model, no production change, no tag. **Nothing here is claimed ready until the owner runs it.** Every fact below is from a file or commit named in it; anything not found is marked MISSING or UNKNOWN.

## 0. Historical assets that exist (MEASURED, read-only search)
| Asset | Where | What it proves |
|---|---|---|
| Frozen "KNOW B" success run (Ignite + Spring XML, with the `ignite-java17` skill) | `~/kriya-live-demo/demo-01-skills/evidence/v4/run-b-20261001T142913Z/` (CLASSIFICATION.md, stderr.log, stdout.json, final-workspace/pom.xml) | Kriya `ad5598d`, exit 0, success, 356 s, three units (pom.xml, ignite-config.xml, IgniteDemoApplication.java), runtime command `mvn -e -q compile exec:exec -Dexec.mainClass=com.example.IgniteDemoApplication`, node started, `Retrieved value from cache: ...`, `[VERIFICATION] PASS` |
| The demo harness and goal | `~/kriya-live-demo/demo-01-skills/` (goal.md, RUNBOOK.md, expected-evidence.md, run-a/b scripts, `skills/ignite-java17`) | A/B with and without the skill, same model |
| Model-free complete candidate | `evidence/demo-defects-d6-d7/fixtures/knowb_candidate/` and `tests/fixtures/d6_knowb_candidate/` (pom.xml, `src/main/resources/ignite-config.xml`, `IgniteDemoApp.java`) | the exact pom (with the full `--add-opens` list), Spring XML (`IgniteConfiguration` + `IgniteSpringBean id="igniteNode"`) and main class; D6/D7 reproducers ran offline (`mvn -o`) |
| The Ignite skill | `skills/ignite-java17/` (skill.yaml, rules.txt 45 lines, instructions.md, rule_provenance.json, examples/pom.xml); verified, `verified_at 2026-09-09`, context `ignite-core 2.18.0` | the rules of section 6 |
| Staged protocol + Ignite + Qpid build | `~/WorkingDirectory/AI/ClaudeCode/kriya-staged-protocol-ignite-qpid/` (goal.md with per-stage outcomes, how-to-run.txt, kriya.yaml, skills/, src/) and `kriya-oneshot-protocol-ignite-qpid`, `kriya-protocol-parser-app`, `claude-code-qwen3-ignite-qpid-protocol`, `spring-ignite-demo` | Stage 1 protocol (one manual test correction), Stage 2 Ignite layer passed first attempt (~137 s), Stage 3 Qpid passed |
| Other skills | `skills/binary-wire-protocol`, `skills/qpid`, `skills/activemq-artemis` | wire-format and broker knowledge |
| Ignite + Spring jars for an OFFLINE run | `~/.m2/repository/org/apache/ignite/ignite-core` (2.14, 2.15, 2.17, **2.18.0**, 3.1.0), `ignite-spring` (2.14-2.18.0), Spring 5.3.39, exec-maven-plugin | the D6 reproducer already ran with `mvn -o` |
| Toolchain here | Temurin 17.0.10 (default) and 21.0.12 | Java 17 is the target |
Not found: any "CKM Ignite" work (CKM is KAD-046, "Compiled Knowledge Is Authoritative; derived products are not the source of truth", `Kriya-By-ClaudeCode/handover/KRIYA_ARCHITECTURE_DECISIONS.md:1240`); the Ignite work was the skill + KNOW demo. `PRV-10-public-knowledge-gap-ignite` exists but was never executed. No `-Djava.net.preferIPv4Stack` or `IGNITE_*` options appear anywhere.

## 1. Demo objective
A GUI-driven Greenfield run in which Kriya, using specialised knowledge it already holds (the `ignite-java17` skill), produces, shows for review, builds, tests and executes a Java 17 / Maven application that starts an embedded Apache Ignite node from Spring XML, stores and reads a protocol object through an Ignite cache, and exits by itself - with every proof coming from authoritative records (build, test count, the runtime command's captured output), not model prose.

## 2. First rehearsal scope (keep it comparable to the historical success)
Stage 1 only: the KNOW-B-equivalent (Ignite 2.18.0 via Spring XML, cache put/get, clean exit) with ONE small addition so the run is a real Greenfield test: a `Protocol` class used as the cache value. Nothing else (no Qpid, no JMS, no extra caches, no persistence, no multi-node).

## 3. Proposed owner goal (wording carries no hidden implementation tricks)
> Create a Maven project targeting Java 17. Define a `Protocol` class with fields protocolVersion (int), softwareVersion (int), dataLength (int), time (long) and body (byte[]), and a `ProtocolParser` that encodes a Protocol to bytes and decodes the bytes back (a 9-byte header: protocolVersion 1 byte, softwareVersion 1 byte, dataLength 3 bytes big-endian, time 4 bytes big-endian, then the raw body). Start an embedded Apache Ignite 2.18.0 node from a Spring XML configuration, put a sample Protocol into an Ignite cache, read it back, decode/compare it, and print the decoded field values with a `[RESULT]` marker. Cleanly close all Spring and Ignite resources so the application exits normally. Include a JUnit test for the parser round trip. Make the application runnable.

(The header layout is the one the staged Stage 1 goal used; the test requirement is new vs. KNOW B, whose goal forbade tests because of a JUnit-dependency confound - see risk 14.)

## 4. Technology baseline (only what the evidence supports)
Java 17 (source/target 17; Temurin 17.0.10 installed); Maven 3.9.x; maven-compiler-plugin 3.11.0; exec-maven-plugin 3.1.0 (`exec:exec`); Apache Ignite **2.18.0** (`ignite-core` + `ignite-spring`); Spring **5.3.39** (cited in `evidence/demo-defect-d5/README.md`); JUnit version: NOT fixed by existing evidence for this combination - the first rehearsal must observe what Kriya picks and whether it resolves offline (UNKNOWN).

## 5. Expected project files
`pom.xml`; `src/main/resources/<ignite-config>.xml` (or `applicationContext.xml`); `src/main/java/<pkg>/Protocol.java`, `ProtocolParser.java`, the application main class; `src/test/java/<pkg>/ProtocolParserTest.java`. No runtime state files: an `ignite/` work directory created by running is runtime state and must not appear in the candidate (D6 fixed the gate that flagged `ignite/README.txt`).

## 6. Knowledge dependencies (source of each fact)
| Fact | Source |
|---|---|
| Ignite 2.18.0; ignite-core + ignite-spring; Java 17 compiler settings | skill `ignite-java17` rules.txt |
| ONE mechanism only: plain `IgniteConfiguration` + `Ignition.start(cfg)` OR `IgniteSpringBean` + `getBean("igniteNode")`; never both; bean by id not `getBean(Ignite.class)`; `ConfigurableApplicationContext` in try-with-resources | rules.txt (confirmed live 2026-08-07) |
| `IgniteCache` is `org.apache.ignite.IgniteCache`; cache name is a String | rules.txt |
| `--add-opens`/`--add-exports` applied at JVM start via `exec:exec` (`<executable>java</executable>`, `-classpath <classpath/>`, final argument `${exec.mainClass}`); `exec:java` cannot | rules.txt; knowb_candidate/pom.xml lines ~60-95 |
| Required output via `System.out.println`, not a logger; close the context/node or the process hangs | rules.txt |
| Wire format of Protocol | the owner goal itself (stated in the goal, not from knowledge) |
| Binary-protocol encode/decode idioms | skill `binary-wire-protocol` (not exercised with Ignite together before) |
| `Protocol` as an Ignite cache VALUE (serialization: Ignite binary marshaller needs no `Serializable`, but a class with `byte[]` and no no-arg constructor behaviour) | MISSING: not in any skill; Stage 2 of the staged work passed with `IgniteCache<Integer, Protocol>` so it is evidenced to work, not documented as a rule |
| JUnit dependency/version resolution offline | MISSING for this stack (the KNOW B goal removed tests to avoid this confound) |
Gaps to record at rehearsal time: the skill must be present in the project's skills directory (default `./skills`); a Greenfield HelloWorld-style project has none, so the first rehearsal needs a prepared scenario (copy of `skills/ignite-java17`, offline config, as scenario 02 does with its snapshot). That scenario/prepare script does not exist yet (BACKLOG, not built overnight).

## 7. Expected planning shape
Do not hardcode an Ignite planner branch. A multi-unit decomposition is safer than one direct unit: (1) `pom.xml` (dependencies, compiler, `exec:exec` with the add-opens list), (2) Spring XML, (3) `Protocol` + `ProtocolParser` + test, (4) application main. KNOW B ran as three units with one attempt each; a single direct unit emits all files in one response, which concentrates multi-file consistency risk (the Greenfield live run that failed in this repo needed D1-D3 fixes for exactly that). Whether the packaged default (`workflow_controller.enabled: false`, one direct unit) or the enforce plan is used is a configuration choice for the rehearsal; Project Setup's recommended profile turns the enforced workflow on.

## 8. Source review flow (what the owner reviews before anything executes)
In Desktop human-in-the-loop, the multi-file source review appears first and states that nothing has been built, tested or run. Review: the Spring XML mechanism (exactly one of the two), `ignite-spring` present, the pom `exec:exec` block with the add-opens flags and `${exec.mainClass}`, the Protocol field types, the parser header arithmetic, a test that really round-trips, `close()` on the context. Reject here = no build, no test, no run, nothing applied.

## 9. Build/test flow (objective proof)
Maven build passes; the recorded test count is non-zero and equals the tests written (expected 1-3 round-trip cases, never "0 tests ran"); no compile-only success. Evidence: gate records `compile`, `tests`/`targeted_test` with `test_execution.cases`, on the result screen as `N / N tests passed`.

## 10. Runtime flow
Authoritative command (historical): `mvn -e -q compile exec:exec -Dexec.mainClass=<main class>`; it appears in the runtime-command review (separate native confirmation). Required JVM arguments (from the evidenced pom): the `--add-opens` list for `java.base` packages (lang, lang.invoke, lang.reflect, io, net, nio, math, util, util.concurrent(.atomic, .locks), time, text, sun.nio.ch, jdk.internal.misc/access, sun.util.calendar, `java.management/com.sun.jmx.mbeanserver`, ...); the exact list is `knowb_candidate/pom.xml` and `rules.txt`. Network: Ignite starts a node (local discovery); whether the runtime verification's containment (network DENIED for generated code) permits it is UNKNOWN for the current desktop path - KNOW B succeeded under the older demo runtime; rehearsal must observe it (classify as RUNTIME_ENV_GAP if blocked).

## 11. Expected output (bounded, not byte-exact)
The captured output contains, in order: Ignite startup log lines (noise, not asserted), the `[RESULT]` marker line with the decoded values equal to the sample values (protocolVersion, softwareVersion, dataLength, time, body length), an explicit success marker the goal asked for, and the process EXITS 0 without external termination (execution record: exit_code 0, not timed out). Ignite's own INFO logging goes to stdout/stderr and is large: the result screen shows the first 15 lines with "Show full output", so the `[RESULT]` line may sit under the fold (known presentation limit, `APPLICATION-OUTPUT-PROGRAM-STREAM-001`).

## 12. Protocol-class success criteria (used, not merely present)
(a) the parser test round-trips Protocol -> bytes -> Protocol with field equality; (b) the main prints `[RESULT]` values decoded from the bytes read BACK FROM THE IGNITE CACHE (not from the in-memory original); (c) the cache value type is `Protocol` (explicitly typed `IgniteCache<Integer, Protocol>`); (d) the printed `dataLength`/`body length` agree with the 9-byte header arithmetic. Deterministic check: the sample values are fixed in the goal; compare printed vs. expected in the recorded output (by the owner or a later deterministic closer - the requirement ledger will still say "not fully verified", by design).

## 13. Failure classification matrix
| Symptom | Class |
|---|---|
| Model invents Ignite API / wrong import / both start mechanisms | SOURCE_DOCUMENT_KNOWLEDGE_MISSING if the skill lacks the rule; else CKM_TO_LLM_TRANSLATION_GAP if the rule is present but ignored |
| Skill not loaded into the prompt / not in the project | KRIYA_CONTROL_PLANE_GAP (or setup error) |
| `DirectByteBuffer.address field is unavailable` | MODEL_TOOLING_EXECUTION_GAP (used `exec:java`) |
| No `Protocol`-in-cache rule available | PACK_SYNTHESIS_MISSING |
| Maven cannot resolve JUnit/Ignite offline | RUNTIME_ENV_GAP |
| Node cannot bind/discover under containment | RUNTIME_ENV_GAP |
| Process never exits | MODEL_TOOLING_EXECUTION_GAP (resources not closed) |
| Review/approval/ordering/ledger/History defects | KRIYA_CONTROL_PLANE_GAP |

## 14. Local-model risks
Multi-file consistency (package/class names across pom main class, XML bean class names, imports); Spring XML accuracy (bean ids vs `getBean("igniteNode")`); Maven dependency versions (JUnit with Ignite's transitive set, offline resolution); Ignite APIs (the recorded import and argument-type mistakes); repair convergence (the staged Stage 1 loop kept "fixing" correct code when a TEST was wrong - repair targeting a wrong test is a known failure); tool-call reliability on long structured responses. KNOW B's goal forbade tests; adding a JUnit test reintroduces the confound the historical goal removed - expected to be the least reliable part.

## 15. Ignite-specific risks (evidenced)
Java 17 module access (`DirectByteBuffer`/`java.nio` etc.; fix = `exec:exec` + opens); mixing `IgniteSpringBean` with `Ignition.start()` ("Ignite instance with this name has already been started"); context/node never closed => the JVM never exits (hangs the run until the timeout); `getBean(Ignite.class)` fails; the `ignite/` work directory is runtime state; Java 24+ Security Manager flag error (memory notes); `exec:java` runs in Maven's JVM and cannot take JVM args. Not evidenced anywhere: multicast/discovery/port failures.

## 16. Rehearsal ladder
1. Stage 1 (this plan): Protocol + Spring XML + Ignite put/get + clean exit + one parser test.
2. Stage 2: protocol verification strengthened (read-back equality asserted in code, `[VERIFICATION] PASS` printed only when equal - the KNOW B pattern), still one node.
3. Stage 3: only if 1-2 are stable: the richer specialised-knowledge demo (the staged Qpid/JMS layer, `skills/qpid`).
Before stage 1, validate the SETUP model-free: the knowb_candidate fixture builds and runs offline (`mvn -o`) under Java 17; the skill is in the scenario's skills directory; the offline config is in place.

## 17. What must NOT be hardcoded
No output hacks ("Hello"/`[RESULT]` literals in Kriya); no Ignite-specific branch in the planner, workflow or UI; no provider-specific shortcuts (generic code stays provider-neutral - INF-001); no fake or skipped runtime verification; no demo-only validator bypass; the `--add-opens` list belongs to the generated pom/skill, not to Kriya's runtime executor; the success proof must remain the recorded build/test/runtime evidence.

## 18. Owner demo script (future; not validated)
Open the prepared project (skill present, offline config) -> enter the goal in section 3 -> observe knowledge use (Skills page shows `ignite-java17`; Activity shows the units) -> source review (section 8) -> approve -> build/tests (count non-zero) -> runtime-command review (`mvn ... exec:exec ...`) -> native confirmation -> execution -> result screen (Build/Tests/Runtime verification passed, Application ran, output, formal ledger disclosure) -> inspect final evidence and the Recent runs entry (same state directory!).

## Readiness assessment
NOT READY for a first rehearsal: (1) no prepared scenario (skill + offline config + empty workspace) exists for this demo; (2) the JUnit-offline and containment-network behaviour are UNKNOWN; (3) the Protocol-as-cache-value knowledge is evidenced by a past run but not written as a rule. None of this needs Kriya production changes; (1) is a small demo-v2 scenario (prepare/reset/verify scripts like scenario 00/02).
