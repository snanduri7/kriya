# Scenario 04 - expected evidence (Spring XML + Apache Ignite + Protocol, greenfield)

Nothing in the live column has been measured with a live model yet; the model-free column is MEASURED by `scripts/qualify_spring_ignite.sh` (evidence: `handover/SPRING-IGNITE-PROTOCOL/evidence/`).

| Moment | What the screen should show | Where it comes from |
|---|---|---|
| Open Folder | Project page; **ACTION REQUIRED - Configuration approval required** (the folder holds only `kriya.yaml`) | `execution.preflight`: `CONFIG_AUTHORITY_REFUSED` until approved |
| Skills page | `ignite-java17`: verified, context `org.apache.ignite:ignite-core 2.18.0`, 44 rules, 1 example | `SkillService.list` (measured: `skill-load.json`) |
| Review & Approve -> native confirmation | names of protected settings; Cancel is the default | SEC-009 approval, same backend path as `kriya authority approve` |
| Start Run | no KnowledgeGuard stop (a stop here is a configuration defect, see SCENARIO.md) | measured: `knowledge-guard.json` |
| Source review | about 6 new files as exact diffs, nothing built or run yet | `change_approval.review` |
| Build / Tests | compile passes; recorded test count N >= 1 shown as `N / N tests passed` | gate records `compile`, `tests` |
| Runtime command review | the Run Verifier's command (historically `mvn -e -q compile exec:exec -Dexec.mainClass=<main class>`), reviewed separately | `launch_approval` runtime review |
| Application | the command, exit code 0, the program's output | `application_outcome` EXECUTED |
| Run completed | Build, Tests, Runtime verification passed; the formal requirement ledger may still say "not fully verified" by design | RunRecord, trace row |

**The application's own proof (model-free, measured on the reference):** the output ends with
```
[RESULT] protocolVersion=1, softwareVersion=2, dataLength=15, time=1700000000, bodyLength=15, equals=true
[VERIFICATION] PASS            (only if the generated program prints it; the goal does not require it)
```
and Ignite logs `Ignite node started OK ... Ignite node stopped OK`. On the reference the `[RESULT]` line is around output line 40 to 50: the result screen shows the first 15 lines, so it is under "Show full output".

**What the line proves and does not prove.** `equals=true` is the program's own comparison of the Protocol read back from the Ignite cache with the original. On its own it proves nothing about Ignite: it is the program's text. Together with the recorded exit code 0 and Ignite's own start and stop lines it shows Ignite ran. The runtime evidence that a Protocol instance really went INTO an Ignite cache (the put; the read-back is checked by reading the source) is Ignite's own marshaller registration (`ignite/work/db/marshaller/-165319811.classname0`, type id of `com.example.protocol.Protocol`); that file is runtime state, discarded by Kriya, so for a live candidate the owner reads the source at the review.

**Presenter: say** - "the folder held only a configuration", "Kriya showed me the exact files before anything ran", "the Ignite-specific rules (JVM flags, one start mechanism, closing the context) came from a verified skill", "the build, the tests and the run are Kriya's own recorded gates". **Do not say** the model is reliable, that the knowledge guard was satisfied (it ran in offline mode), or that the application ran unless the Application section is on screen.
