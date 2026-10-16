# Scenario 04 (Spring XML + Apache Ignite + Protocol): preparation and model-free qualification

Branch `demo/spring-ignite-protocol`, from the frozen tag `kriya-v1.2.0-demo-v2-validated` (`f731c26`; the tag and `demo/v2` are untouched). No product code is changed. **No live model was run.** Date of the work: 2026-10-16.
Evidence status words follow `handover/ENGINEERING_RULES.md`: MEASURED (observed by a command), TRACED (read in the code that produces it), INFERRED, CONFIRMED (a discriminating check), UNKNOWN.
Evidence directory: `handover/SPRING-IGNITE-PROTOCOL/evidence/qualification-0637589/` (a run of `demo-v2/scripts/qualify_spring_ignite.sh --with-containment` on the exact commit `0637589`, clean tracked tree) and `evidence/supplementary-measurements.md`.

## 0. What the historical record actually is (a correction to the brief's framing)

There is no single historical app that has Ignite, Spring XML, Protocol, ProtocolParser and a test. There are two separate historical successes, and this scenario is their composition:

| Historical success | Where | Ignite + Spring XML | Protocol / ProtocolParser | Test |
|---|---|---|---|---|
| "KNOW B", Kriya `ad5598d`, exit 0, 356 s, 3 units | `~/kriya-live-demo/demo-01-skills/evidence/v4/run-b-20261001T142913Z/` | YES (`IgniteSpringBean`, `getBean("igniteNode")`) | NO (cache of `String`) | NO (the goal forbade tests, to avoid a JUnit confound) |
| Staged build, Stage 1 (`df7e6b1`) and Stage 2 (~137 s, first attempt) | `~/WorkingDirectory/AI/ClaudeCode/kriya-staged-protocol-ignite-qpid/` | Stage 2: `Ignition.start("ignite-config.xml")` (plain `IgniteConfiguration` XML) | YES, `IgniteCache<Integer, Protocol>` | Stage 1 only: JUnit 5.8.2 (4/4 after one manual test correction) |

Stage 3 of the staged build adds Qpid/JMS and is out of scope. Nothing here was measured as a single historical run of the combined application: the combination is what this branch qualified, model-free.

## 1. Evidence table (every fact with its source)

Sources: `HISTORICAL_SUCCESS` (KNOW B), `STAGED_PROTOCOL_PROJECT`, `VERIFIED_SKILL`, `MODEL_FREE_FIXTURE` (`evidence/demo-defects-d6-d7/fixtures/knowb_candidate`), `MAVEN_LOCAL_CACHE`, `CURRENT_KRIYA_CONFIG`, plus `QUALIFICATION_RUN` (this work).

| Fact | Value | Source | Status |
|---|---|---|---|
| Java (project target) | 17 (`maven.compiler.source/target` 17) | HISTORICAL_SUCCESS `pom.xml`; STAGED `pom.xml` | MEASURED |
| Java actually installed | Temurin 17.0.10 (interactive default via `JAVA_HOME`) and 21.0.12 | QUALIFICATION_RUN `java_home -V` | MEASURED |
| Java under the desktop app's environment | `java` = Temurin 21.0.12.1 (`/usr/bin/java`); Maven itself runs on Homebrew OpenJDK 26.0.1 | QUALIFICATION_RUN (`toolchain.txt`; `child_env.ts` fixes `PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin`, no `JAVA_HOME`) | MEASURED, TRACED |
| Maven | 3.9.16 (historical runs: 3.9.x, exact patch not recorded) | QUALIFICATION_RUN; run-meta has no Maven version | MEASURED / UNKNOWN (historical patch) |
| Spring | 5.3.39 (transitive of `ignite-spring` 2.18.0: spring-context/beans/core/aop/jcl/expression) | QUALIFICATION_RUN classpath | MEASURED |
| Ignite | 2.18.0 (`ver. 2.18.0#20260423-sha1:d49adada`) | jar banner; HISTORICAL_SUCCESS | MEASURED |
| Ignite coordinates | `org.apache.ignite:ignite-core:2.18.0` and `org.apache.ignite:ignite-spring:2.18.0` (property `ignite.version`) | HISTORICAL_SUCCESS pom; VERIFIED_SKILL rules | MEASURED |
| Spring coordinates | none declared: Spring arrives transitively through `ignite-spring` | HISTORICAL_SUCCESS pom (only the two Ignite artifacts) | MEASURED |
| Test framework | JUnit Jupiter 5.8.2 (`org.junit.jupiter:junit-jupiter`, test scope) + maven-surefire-plugin 2.22.2 (+ compiler 3.8.1 historically; 3.11.0 in the Ignite builds) | STAGED_PROTOCOL_PROJECT (`df7e6b1:pom.xml`); MAVEN_LOCAL_CACHE | MEASURED |
| Source files (KNOW B) | `pom.xml`, `src/main/resources/ignite-config.xml`, `src/main/java/com/example/IgniteDemoApplication.java` | HISTORICAL_SUCCESS | MEASURED |
| Source files (staged) | `src/main/java/com/example/protocol/Protocol.java`, `.../ProtocolParser.java`, `src/main/java/com/example/Main.java`, `src/main/resources/ignite-config.xml`, `src/test/java/com/example/protocol/ProtocolTest.java` (Stage 1) | STAGED_PROTOCOL_PROJECT | MEASURED |
| Packages | `com.example` (main), `com.example.protocol` (Protocol, ProtocolParser) | STAGED | MEASURED |
| Spring XML location | `src/main/resources/ignite-config.xml` (loaded as a classpath resource) | HISTORICAL_SUCCESS / MODEL_FREE_FIXTURE | MEASURED |
| Ignite bean configuration | bean `ignite.cfg` = `IgniteConfiguration` (`igniteInstanceName=ignite-server-node`, `peerClassLoadingEnabled=true`); KNOW B adds bean `igniteNode` = `IgniteSpringBean` referencing it; staged Stage 2 omits `igniteNode` and starts the node with `Ignition.start("ignite-config.xml")` | HISTORICAL_SUCCESS; STAGED; VERIFIED_SKILL (Method A / Method B, exactly one) | MEASURED |
| Cache configuration | none in XML; `ignite.getOrCreateCache("<name>")` in code (KNOW B `my-cache`, staged `protocol-cache`) | HISTORICAL_SUCCESS; STAGED | MEASURED |
| Discovery / network configuration | **default, nothing configured**: no `localHost`, no `TcpDiscoverySpi`, no `-Djava.net.preferIPv4Stack`, no `IGNITE_*` options anywhere in the record | HISTORICAL_SUCCESS pom/xml; plan section 0 | MEASURED |
| `Protocol` class | fields `protocolVersion`, `softwareVersion`, `dataLength` (int), `time` (long), `body` (byte[]); public no-arg constructor; getters/setters; `equals`/`hashCode` compare `body` by CONTENT (`Arrays.equals`/`Arrays.hashCode`); `toString`; **not** `Serializable` | STAGED `Protocol.java` | MEASURED |
| `ProtocolParser` | `byte[] encode(Protocol)`, `Protocol decode(byte[])`; 9-byte big-endian header (protocolVersion 1, softwareVersion 1, dataLength 3, time 4) + raw body; `IllegalArgumentException` on malformed input; encoder derives `dataLength` from `body.length` | STAGED goal + `ProtocolParser.java` | MEASURED |
| What is stored in Ignite | the **decoded `Protocol` object itself**, key `Integer 1`, `IgniteCache<Integer, Protocol>` explicitly typed | STAGED goal Stage 2 + `Main.java` | MEASURED |
| What is read back | `cache.get(1)` into a separate variable, compared with `equals` (Stage 2: with the decoded one; Stage 3: with the original) | STAGED | MEASURED |
| Runtime main class | KNOW B `com.example.IgniteDemoApplication` (default `exec.mainClass` `com.example.App`); staged `com.example.Main` | HISTORICAL_SUCCESS; STAGED | MEASURED |
| Maven runtime command | `mvn -e -q compile exec:exec -Dexec.mainClass=com.example.IgniteDemoApplication` (chosen by Kriya's Run Verifier, `command_source: inferred`) | HISTORICAL_SUCCESS CLASSIFICATION.md | MEASURED |
| JVM flags | `<executable>java</executable>`, 24 `--add-opens` arguments, `-classpath <classpath/>`, final argument `${exec.mainClass}`; **`exec:exec`, never `exec:java`** | HISTORICAL_SUCCESS pom; VERIFIED_SKILL | MEASURED (classification in section 6) |
| Expected stdout (KNOW B) | `Ignite node started OK`, `Retrieved value from cache: Hello, Ignite!`, `[VERIFICATION] PASS` | HISTORICAL_SUCCESS | MEASURED |
| Expected stdout (staged) | `[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=<unix>, bodyLength=15, equals=true` then `[VERIFICATION] PASS` | STAGED goal.md | MEASURED |
| Expected exit code | 0, by itself (no external termination) | HISTORICAL_SUCCESS (`cli_exit_code 0`); QUALIFICATION_RUN | MEASURED |
| Test behaviour | Stage 1 `ProtocolTest`: 4 tests, round trip + invalid-length cases; one test's expectation was wrong and was corrected by hand (the retry loop kept "repairing" correct code) | STAGED goal.md / `df7e6b1` | MEASURED |
| Clean shutdown | closing the `ConfigurableApplicationContext` (Method B) or the `Ignite` (Method A) stops the node; without it the JVM never exits | VERIFIED_SKILL rules (confirmed live 2026-08-07); QUALIFICATION_RUN (`Ignite node stopped OK`, exit 0, no process left) | MEASURED |
| Offline artifacts | ignite-core/ignite-spring 2.18.0, spring 5.3.39, junit-jupiter 5.8.2, surefire 2.22.2, compiler 3.11.0, exec-maven-plugin 3.1.0 all present as files in `~/.m2` | MAVEN_LOCAL_CACHE | MEASURED |
| Current Kriya config | development profile; `autonomy.contained_execution_required=false` (host execution); desktop launch is always knowledge-policy strict | CURRENT_KRIYA_CONFIG (`config.py`, `cli.py::_launched_generate`) | TRACED |

## 2. The three preparation gaps

### 2A. Test dependencies: RESOLVED, offline
`org.junit.jupiter:junit-jupiter:5.8.2` (the exact coordinate of the staged Stage 1 evidence) with `maven-surefire-plugin:2.22.2` is fully cached: `mvn -o test` = 3/3 tests passed (MEASURED). A broader matrix with surefire UNSPECIFIED (Maven's default binding, 3.5.4): 5.8.2, 5.9.2, 5.10.0, 5.14.3 pass; **5.7.0 and 5.11.4 fail offline** (their `junit-platform-launcher` is not cached). Nothing was downloaded to change that. Consequence for the live run: the model's JUnit version is its own choice; the failing ones are `RUNTIME_ENV_GAP` only if Maven cannot reach Central (Kriya's host mode does not pass `-o`).
The reference test is non-vacuous: it round-trips a Protocol with field equality, pins the exact big-endian header bytes, and asserts `IllegalArgumentException` on a truncated message (mutation: `assertEquals(true, true)` in place of the round trip is caught by a test).

### 2B. Ignite containment / network: RESOLVED, no policy change
Measured through the closest Kriya path, `PolymorphicValidator.run_app_sequence` with the verification tree bound (exactly as the workflow calls it), not through a hand-run Maven:
- **Demo profile (host execution, the only profile any Demo V2 scenario uses):** no containment is active, so nothing blocks Ignite. Ignite 2.18.0 starts, `Topology snapshot [ver=1 ... servers=1]`, put/get works, `Ignite node stopped OK`, exit 0, about 7 s, under both the desktop app's environment and Java 17.
- **Binds (default configuration, MEASURED by sampling `lsof` on the JVM):** wildcard `TCP *:47100` (communication), `*:47500` (discovery), `*:10800` (thin client), `*:11211` (connector), `UDP *:47400` (multicast discovery) plus ephemeral UDP, and a TCP echo probe (port 7) to the machine's own LAN address. It is a wildcard-bind, multicast-capable default: a host-mode fact the owner should know; it is not made safer or less safe by this branch.
- **Under the OCI backend with network DENIED** (`egress: denied`, image `maven:3.9-eclipse-temurin-17`, Maven cache pre-seeded from `~/.m2`, no network): the same candidate starts Ignite and passes. Loopback is sufficient; no external interface, no peer and no discovery partner is needed (single node). No container was left behind.
- Not weakened, not bypassed: no Ignite-specific code, no network-policy change, no containment change. Classification: no `RUNTIME_ENV_GAP`, no `KRIYA_CONTROL_PLANE_GAP` for containment.
- Not measured: a containment-REQUIRED desktop run end to end (the Demo V2 scenarios are development-profile); only the runtime step was run contained.

### 2C. Protocol / cache semantics: RESOLVED from existing evidence; sources agree
- `Protocol` itself is the cache value (`IgniteCache<Integer, Protocol>`, key 1). It is not a parsed field and not the raw protocol string. STAGED Stage 2 goal and `Main.java`.
- It is not `Serializable`; Ignite's binary marshaller needs no special representation. Confirmed on the reference (no `Serializable`, public no-arg constructor): `ignite/work/db/marshaller/-165319811.classname0` appears and contains `com.example.protocol.Protocol` (`-165319811` = Java `hashCode` of the lower-cased class name). That file is Ignite's own proof a `Protocol` instance was marshalled for the cache, and the qualification asserts it.
- What proves `ProtocolParser` was used: the data reaches the cache only as the result of `decode(encode(original))` and the test pins the byte layout; in the program, the printed values come from the object read back from the cache. A program that printed the line without touching Ignite would produce the same text, which is why the runtime proof also needs Ignite's start/stop lines and (on the reference) the marshaller file; for a live candidate the source review is the owner's check.
- No conflict between sources was found (KNOW B's `String` cache and the staged `Protocol` cache are different tasks, not different rules). No new protocol design was invented: the field set, wire format, sample values and result-line format are the staged build's.

## 3. A finding the brief did not anticipate: the desktop cannot get past KnowledgeGuard on this goal by default

- TRACED: `kriya generate --launch-id` (the desktop path) always uses knowledge-policy `strict` with no acknowledgements (`cli.py::_launched_generate`, `kup/execution.py::KNOWLEDGE_POLICY`); a `knowledge_gap` result becomes `approval_required: knowledge_gap_acknowledgement`, a decision the desktop cannot make (`control/launch.py`). `OWNER_RUNBOOK.md` already says the same for scenario 02.
- MEASURED (`knowledge-guard.json`, `tests/test_demo_v2_spring_ignite.py`): for the scenario goal, `extract_library_versions` yields `org.apache.ignite:ignite-core 2.18.0`; the default (online) guard reports a gap **even with the verified skill present** (MEDIUM; HIGH without it) because 2.18.0 was released after the model's training cutoff. So a desktop run of a goal that names "Apache Ignite 2.18.0" would stop at the KnowledgeGuard stage before any model call.
- Resolution used (no product change): the scenario configuration sets `knowledge.offline_mode: true` (a `REPOSITORY_SAFE` field). MEASURED: with the skill present the guard reports no gap; offline mode WITHOUT the skill still reports one, so this is not a blanket bypass. The guard stays enabled (`check_enabled: true`).
- Honest cost: in offline mode the guard cannot look up release dates, so its post-cutoff WARNING is not injected into the Developer prompt (`workflow.py`, "KNOWLEDGE GUARD SAFETY CONSTRAINTS"). The verified `ignite-java17` skill supplies the Ignite facts instead. **Owner decision (not taken here):** accept this scenario-level setting, or later authorise a product feature by which a verified skill that covers the library resolves the guard in a desktop launch. Not authorised in this phase.
- Rejected alternative: leaving the version out of the goal text (the extractor needs "Apache Ignite <digits>"). That would engineer the goal around the classifier, which the brief forbids; the version stays in the goal.

## 4. Toolchain finding
Under the desktop app's environment the toolchain is Temurin 21 for `java` and Homebrew JDK 26 for Maven's own JVM, not Java 17 (`greenfield_prereqs.sh` only requires 17+). The project targets Java 17 through its compiler settings. Qualified under BOTH the app's environment and an explicit Java 17 (both pass; with the two required flags Ignite also starts on Java 26). The validated Demo 00 live pass went through the desktop app, hence this environment (INFERRED; the toolchain of that run was not recorded).

## 5. Project contract (what MUST be true; not byte-identical output)
- `pom.xml`: Java 17 source/target; `ignite-core` and `ignite-spring` at 2.18.0; a JUnit 5 test dependency (test scope) that resolves; `exec-maven-plugin` configured as **`exec:exec`** with `<executable>java</executable>`, the `--add-opens` arguments, `-classpath` + bare `<classpath/>`, and `${exec.mainClass}` as the final argument; never `exec:java`+`jvmArguments`.
- Spring XML under `src/main/resources`: an `IgniteConfiguration` bean; **exactly one** start mechanism (either `IgniteSpringBean` + `getBean("<id>")`, or a plain configuration + `Ignition.start("<xml>")`, never both).
- `Protocol`: the five fields; content-based `equals`/`hashCode` on `body`. `ProtocolParser`: `encode`/`decode` per the stated wire format; rejects malformed input.
- Main class: builds the sample (1, 2, 1700000000, "Hello, Protocol" UTF-8), encode/decode, puts the decoded Protocol into a typed cache, reads it back, compares with the original, prints the `[RESULT]` line from the cache's object, closes the context/node, exits.
- Test: a real round trip with field equality (and the format), at least one `@Test`, no vacuous assertion.
- Runtime: starts, exercises the parser, put/get, prints `[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true`, stops Ignite, exit 0; no `ignite/` work directory left in the project.
Checked by `verify_demo.sh --scenario 04 --phase post` (independent offline re-check) and, for the reference, by `tests/test_demo_v2_spring_ignite.py`.

## 6. JVM flags (Java 17 and 21; reference candidate; leave-one-out, `evidence/.../jvm-flag-leave-one-out.txt`)
| Flag | Class |
|---|---|
| `--add-opens=java.base/java.nio=ALL-UNNAMED` | **REQUIRED** (without it: `ExceptionInInitializerError ... DirectByteBuffer.address field is unavailable`) |
| `--add-opens=java.base/java.util=ALL-UNNAMED` | **REQUIRED** (without it: `Exception during start processors, node will be stopped`) |
| the other 22 `--add-opens` of the documented Ignite list | **OPTIONAL** for this workload (dropping any single one still passes); kept as the documented superset the verified skill carries; harmless |
| no flags at all | fails on 17 and 21 |
| `--illegal-access=permit` | **OBSOLETE** (ignored with a warning since JDK 17); it appears in one long rule line of the skill |
| `--add-exports=...` in the same rule line | **UNKNOWN** (not tested separately; not needed when the two opens are present) |
| `-Djava.security.manager=allow` (staged pom only) | **OPTIONAL/not needed** (accepted on 17/21; the skill does not ask for it) |
The skill is not changed (section 7): its superset is harmless and its "mandatory" wording is stronger than the measurement, not wrong.

## 7. Skill audit: `skills/ignite-java17` against the historical success
1. Explicitly covers Ignite 2.18.0: **yes** (`verified_context org.apache.ignite:ignite-core 2.18.0`, rules, examples).
2. Covers Java 17: **yes**.
3. Correct Spring XML guidance: **yes**, two mechanisms, "exactly one", with the observed failure (`Ignite instance with this name has already been started`). Both were run with a Protocol cache value: Method B (reference) and Method A (`supplementary-measurements.md` S3).
4. Needed JVM flags: **yes** (all 24 plus the two required; one obsolete `--illegal-access` token).
5. Embedded node start/stop: **yes** (close the context or the node in try-with-resources; hang explained).
6. Cache configuration: **yes for this use** (`getOrCreateCache("name")`, `org.apache.ignite.IgniteCache` import); no XML `CacheConfiguration` guidance, which the goal does not need.
7. Version-correct Maven coordinates: **yes** (`ignite-core`/`ignite-spring` 2.18.0, `exec-maven-plugin` 3.1.0, example `pom.xml`).
8. Unsupported claims: none found that break this workload. Imprecisions only: "mandatory" opens (two are required, the rest optional), the obsolete `--illegal-access`, and instructions.md calls Method A "Recommended" while rules.txt tells a model that already has `IgniteSpringBean` in its XML to use Method B (consistent: it is "exactly one").
9. Enough for a local model without Internet: for the Ignite part yes (it is what KNOW B used). Not covered, by design: JUnit/Maven test setup and the Protocol cache-value rule. The Stage 2 build passed on its first attempt without a Protocol rule and no failure is recorded for its absence, so **no concrete, evidence-backed gap exists: the skill is NOT changed.** Digest `947b0ae7...` recorded in `fixture.env`; reset refuses an edited skill.
Skill load through Kriya (MEASURED, `skill-load.json`): `SkillService.list` from the scenario configuration returns `ignite-java17` (verified, 44 rules, 1 example, `supported_versions *`, 0 unverified rules); the workflow's relevance predicate selects it for the goal.

## 8. The goal (adjusted from the brief's example to the historical Protocol semantics)
See `demo-v2/scenarios/04-spring-ignite-protocol/goal.md`. Differences from the brief's draft and from the plan's section 3, with reasons: the wire format, sample values and the `[RESULT]` format are the staged build's (so the expected line is deterministic and machine-checkable); the cache value is the Protocol itself; "`equals` against the original" replaces "decode/compare" so the proof is the program's own comparison; a JUnit test is required (the plan flagged it as the least reliable part); "runnable with Maven" is a natural requirement, not a command. It carries no Maven command, flag, skill name, bean name or classifier trigger (static test + negative control).

## 9. Failure classification for the live rehearsal (name the FIRST wrong decision, not the terminal symptom)
| First wrong decision | Class |
|---|---|
| The run stops at KnowledgeGuard with `knowledge_gap_acknowledgement` | `KRIYA_CONTROL_PLANE_GAP` (the rendered configuration is not the scenario's, or the guard changed); see section 3 |
| The skill is not loaded into the Developer prompt (Skills page empty, no "Loaded engineering skill 'ignite-java17'") | `KRIYA_CONTROL_PLANE_GAP` (or a setup error: `paths.skills`) |
| The skill carries the rule, the model ignores it (both start mechanisms, `exec:java`, un-closed context, wrong import) | `CKM_TO_LLM_TRANSLATION_GAP` |
| The needed rule is absent from the skill and the model errs because of it | `SOURCE_DOCUMENT_KNOWLEDGE_MISSING` |
| The rule exists upstream but was never synthesised into the skill | `PACK_SYNTHESIS_MISSING` |
| The Run Verifier picks `exec:java` or another command / a command that cannot work | `MODEL_TOOLING_EXECUTION_GAP` |
| The process never exits (resources not closed) | `MODEL_TOOLING_EXECUTION_GAP` |
| A dependency/plugin version that is not cached and Maven cannot reach Central | `RUNTIME_ENV_GAP` |
| Node cannot bind/discover under containment | `RUNTIME_ENV_GAP` (not expected: section 2B) |
| Review/approval/ordering/ledger/history defect | `KRIYA_CONTROL_PLANE_GAP` |
| Kriya behaved correctly and the candidate is simply wrong (wrong header arithmetic, wrong test) | `MODEL_GENERATION_FAILURE` |

## 10. Security statements
- Source review, runtime-command review and both native confirmations are untouched (scenario config carries `autonomy.mode: human-in-the-loop`, `run_verification_enabled: true`; the desktop gates are not configurable here). The configuration approval is the ordinary SEC-009 approval; the reference candidate is never copied into the project.
- No network or egress policy was changed; no Ignite-specific code exists in Kriya; no validator exception was added. The only non-default setting is `knowledge.offline_mode` (a repository-safe field), described in section 3.
- Host-mode Ignite listens on wildcard addresses (section 2B). Containment is not in the Demo V2 profile; under OCI with network denied it works.

## 11. Verification record (this branch; none of it is a live run)
- `scripts/qualify_spring_ignite.sh --with-containment` on `0637589`: 0 failed (toolchain, 7 offline artifacts, 2 identities, 3/3 JUnit, no-remote Maven, runtime under the app's environment and Java 17, no leftover process, OCI leg, no leftover container, KnowledgeGuard, skill).
- Qualification mutation controls (each mutant fails exactly the intended check): wrong printed value, exit code 3, missing `ignite-spring`, Ignite bypassed, a String put into the cache instead of the Protocol (caught only by the marshaller check, the output being identical).
- `verify_demo.sh --scenario 04`: idle/pre PASS (pre after a real `kriya authority approve` in a scratch workspace, then revoked); post PASS on the reference and FAIL on: no tests, wrong output, zero `@Test`. (A mutation run found and fixed a silent exit when `src/test` is missing.)
- `tests/test_demo_v2_spring_ignite.py` 29 tests + the two updated existing demo test files (68+ demo tests green); six assertion-level mutants killed. `ruff` and `pylint` clean; `bash -n` on all scripts; `shellcheck` is not installed.
- Not run: the 40-minute canonical suite (no product code changed), any live model, a desktop GUI session.

## 12. Remaining uncertainty
Whether the live model produces a correct project, picks `exec:exec`, converges, and whether its JUnit/surefire versions resolve; the exact owner-visible GUI behaviour for the new scenario (not clicked); the real elapsed time of a live run (historical: 356 s for three units without tests); the Maven patch version of the historical runs.
